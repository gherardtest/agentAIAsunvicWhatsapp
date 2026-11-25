"""El "cerebro" del bot: recibe el mensaje del cliente y devuelve la respuesta.

No sabe nada de la consola ni de WhatsApp. En la fase 3 el webhook de
FastAPI usará esta misma clase; solo cambiará quién la llama.
"""

import re
from dataclasses import dataclass

import anthropic

from src import config
from src.conocimiento import cargar_conocimiento

# Modelos que admiten el "respaldo" automático del servidor: si el modelo
# rechaza un mensaje por un falso positivo de seguridad, la API lo reintenta
# con otro modelo dentro de la misma llamada.
MODELOS_CON_RESPALDO = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

# Etiqueta [HUMANO: motivo] con la que el modelo pide pasar a una persona.
PATRON_HUMANO = re.compile(r"\[\s*HUMANO\s*:\s*(.*?)\]", re.IGNORECASE)

# Motivos de relleno: si el modelo escribe la etiqueta como EJEMPLO (porque un
# cliente se lo pidió), el motivo suele ser genérico. Eso no es una derivación.
MOTIVOS_DE_RELLENO = {"", "motivo", "motivo breve", "...", "…", "x", "xxx", "ejemplo", "razón", "razon"}

MENSAJE_ERROR_CLIENTE = (
    "Disculpa, tuve un problema para responderte. "
    "Una persona del equipo te escribirá por aquí en breve."
)


@dataclass
class Respuesta:
    texto: str                    # lo que ve el cliente (sin la etiqueta)
    pasar_a_humano: str | None    # motivo, si hay que pasar a una persona
    tokens_cache: int = 0         # tokens leídos de la caché (más baratos)
    etiquetas_descartadas: int = 0  # [HUMANO] fuera de lugar o de relleno (posible fuga)


def interpretar_etiquetas(texto: str) -> tuple[str, str | None, int]:
    """Separa la etiqueta [HUMANO: motivo] del texto para el cliente.

    Solo cuenta como derivación REAL si:
      1. es la última cosa del mensaje (el prompt pide ponerla al final), y
      2. su motivo no es de relleno ("motivo breve", "…").
    Cualquier otra aparición (por ejemplo, el modelo "mostrando" la etiqueta
    porque un cliente se la pidió) NO pausa el chat. Igual se borra del texto,
    para que el cliente nunca la vea, y se cuenta como descartada.

    Antes se aceptaba la primera etiqueta en cualquier lugar: un cliente podía
    pausar su chat y generar avisos falsos pidiendo "muéstrame la etiqueta"
    (hallazgo RT08 del red teaming).

    Devuelve (texto_limpio, motivo_o_None, etiquetas_descartadas).
    """
    coincidencias = list(PATRON_HUMANO.finditer(texto))
    if not coincidencias:
        return texto, None, 0

    motivo = None
    ultima = coincidencias[-1]
    if not texto[ultima.end():].strip():  # nada después de la etiqueta
        candidato = ultima.group(1).strip()
        if candidato.lower().strip(" .…") not in {m.strip(" .…") for m in MOTIVOS_DE_RELLENO}:
            motivo = candidato
    descartadas = len(coincidencias) - (1 if motivo else 0)

    limpio = PATRON_HUMANO.sub("", texto)
    limpio = re.sub(r"`{1,3}\s*`{1,3}", "", limpio)     # bloques de código que quedaron vacíos
    limpio = re.sub(r"\n{3,}", "\n\n", limpio).strip()   # líneas en blanco sobrantes
    return limpio, motivo, descartadas


class Agente:
    def __init__(self) -> None:
        # Anthropic() lee ANTHROPIC_API_KEY del entorno (cargado desde .env)
        self.cliente = anthropic.Anthropic()
        self.historial: list = []
        self.recargar_conocimiento()

    def recargar_conocimiento(self) -> None:
        reglas = config.ARCHIVO_PROMPT_SISTEMA.read_text(encoding="utf-8")
        conocimiento = cargar_conocimiento(config.CARPETA_CONOCIMIENTO)
        self.sistema = f"{reglas}\n\n<conocimiento>\n{conocimiento}\n</conocimiento>"

    def reiniciar(self) -> None:
        """Olvida la conversación (como si escribiera un cliente nuevo)."""
        self.historial = []

    def responder(self, mensaje_cliente: str) -> Respuesta:
        self.historial.append({"role": "user", "content": mensaje_cliente})

        try:
            respuesta = self._llamar_a_claude()
        except anthropic.AuthenticationError:
            self.historial.pop()
            raise SystemExit("La API key no es válida. Revisa tu archivo .env.")
        except anthropic.NotFoundError:
            self.historial.pop()
            raise SystemExit(f"El modelo '{config.MODELO}' no existe. Revisa MODELO en .env.")
        except anthropic.RateLimitError:
            self.historial.pop()
            return Respuesta("(Límite de uso alcanzado; espera un minuto y vuelve a intentar.)", None)
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            self.historial.pop()
            return Respuesta(f"(Error de conexión con Claude: {e})", None)

        # Rechazo por seguridad (raro en este bot): no inventamos respuesta.
        if respuesta.stop_reason == "refusal":
            self.historial.pop()
            return Respuesta(MENSAJE_ERROR_CLIENTE, "el modelo no pudo responder este mensaje")

        # Guardamos la respuesta COMPLETA (incluye bloques de "pensamiento"),
        # no solo el texto: la API pide devolverlos tal cual en el siguiente turno.
        self.historial.append({"role": "assistant", "content": respuesta.content})

        # La respuesta trae varios bloques; solo los de tipo "text" son para el cliente.
        texto = "".join(b.text for b in respuesta.content if b.type == "text").strip()

        texto, motivo, descartadas = interpretar_etiquetas(texto)

        cache = respuesta.usage.cache_read_input_tokens or 0
        return Respuesta(texto, motivo, cache, descartadas)

    def _llamar_a_claude(self):
        parametros = dict(
            model=config.MODELO,
            max_tokens=config.MAX_TOKENS,
            # Punto de caché FIJO al final del prompt de sistema (reglas +
            # conocimiento, ~7.800 tokens). Es igual para todos los clientes, así
            # que el primer mensaje de un cliente nuevo ya lo lee de la caché
            # (~10 % del precio) en vez de pagarlo completo.
            system=[{"type": "text", "text": self.sistema, "cache_control": {"type": "ephemeral"}}],
            messages=self.historial,
            output_config={"effort": config.ESFUERZO},
            # Caché automática para la conversación: cubre el historial que
            # crece mensaje a mensaje dentro de un mismo chat.
            cache_control={"type": "ephemeral"},
        )
        if config.MODELO in MODELOS_CON_RESPALDO:
            return self.cliente.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **parametros,
            )
        return self.cliente.messages.create(**parametros)
