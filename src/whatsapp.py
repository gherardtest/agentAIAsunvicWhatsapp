"""Todo lo que habla con la WhatsApp Cloud API de Meta.

1. verificar_firma()   comprueba que una petición viene de verdad de Meta
2. leer_eventos()      convierte el JSON que manda Meta en objetos simples
3. enviar_texto()      manda un mensaje a un número
4. marcar_leido()      pone los dos checks azules al mensaje del cliente

Este archivo no decide nada (eso lo hará atencion.py): solo traduce
entre nuestro código y el formato de Meta.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx2

from src import config

log = logging.getLogger("asunvic.whatsapp")

# WhatsApp corta los mensajes de texto a 4096 caracteres
LIMITE_TEXTO = 4096

# Código de error de Meta cuando han pasado más de 24 h desde el último
# mensaje de esa persona: solo se le puede escribir con una plantilla.
ERROR_FUERA_DE_VENTANA = 131047

_cliente_http = httpx2.Client(timeout=15.0)


# ── 1. Seguridad ────────────────────────────────────────

def verificar_firma(cuerpo: bytes, cabecera_firma: str | None) -> bool:
    """Meta firma cada petición con tu App Secret (cabecera X-Hub-Signature-256).

    Calculamos la misma firma con el cuerpo recibido; si coinciden, la
    petición es auténtica. Sin esto, cualquiera que conozca la URL del
    webhook podría mandar mensajes falsos y gastar tus créditos de Claude.
    """
    if not cabecera_firma or not cabecera_firma.startswith("sha256="):
        return False
    esperada = hmac.new(
        config.WHATSAPP_APP_SECRET.encode(), cuerpo, hashlib.sha256
    ).hexdigest()
    # compare_digest compara en tiempo constante (evita ataques de "timing")
    return hmac.compare_digest(esperada, cabecera_firma.removeprefix("sha256="))


# ── 2. Leer lo que manda Meta ───────────────────────────

def es_bsuid(identificador: str) -> bool:
    """¿Es un ID de usuario de Meta (BSUID) en vez de un teléfono?

    Desde 2026, si un usuario tiene nombre de usuario (@username) en WhatsApp,
    Meta puede NO enviar su número: solo un "business-scoped user ID" con la
    forma PAÍS.números, por ejemplo "PE.1854414865870128".
    """
    return "." in identificador and identificador.split(".", 1)[0].isalpha()


@dataclass
class MensajeCliente:
    """Un mensaje que un cliente (o tú, desde tu número personal) envió al bot."""
    id: str
    telefono: str          # "51987654321" (sin +), o el BSUID "PE.185…" si Meta no da el número
    nombre: str            # nombre de perfil de WhatsApp (puede venir vacío)
    tipo: str              # text, image, audio...
    texto: str             # el texto, o una descripción si no era texto
    usuario: str = ""      # @username de WhatsApp, si tiene


@dataclass
class EcoDeLaApp:
    """Un mensaje que la EMPRESA envió desde la app de WhatsApp Business.

    Solo existe en modo coexistencia (número real, más adelante). Sirve para
    detectar que respondiste tú desde el celular y pausar el bot en ese chat.
    """
    id: str
    telefono: str          # el CLIENTE al que le escribiste (teléfono o BSUID)
    tipo: str
    texto: str


def _describir(mensaje: dict) -> str | None:
    """Convierte cualquier tipo de mensaje en un texto para el agente.

    Devuelve None para lo que conviene ignorar (por ejemplo, reacciones).
    """
    tipo = mensaje.get("type")
    if tipo == "text":
        return mensaje["text"]["body"]
    if tipo == "image":
        leyenda = mensaje.get("image", {}).get("caption", "")
        return "[El cliente envió una imagen]" + (f" con el texto: {leyenda}" if leyenda else "")
    if tipo == "document":
        nombre = mensaje.get("document", {}).get("filename", "")
        return f"[El cliente envió un documento {nombre}]".replace("  ", " ")
    if tipo == "audio":
        return "[El cliente envió un audio. No puedes escucharlo: pídele con amabilidad que lo escriba.]"
    if tipo == "video":
        return "[El cliente envió un video. No puedes verlo: pídele con amabilidad que escriba su consulta.]"
    if tipo == "sticker":
        return "[El cliente envió un sticker]"
    if tipo == "location":
        ubicacion = mensaje.get("location", {})
        partes = [ubicacion.get("name", ""), ubicacion.get("address", "")]
        detalle = ", ".join(p for p in partes if p)
        return f"[El cliente envió una ubicación{': ' + detalle if detalle else ''}]"
    if tipo == "button":
        return mensaje.get("button", {}).get("text", "")
    if tipo == "interactive":
        interactivo = mensaje.get("interactive", {})
        respuesta = interactivo.get("button_reply") or interactivo.get("list_reply") or {}
        return respuesta.get("title", "")
    if tipo == "reaction":
        return None  # un 👍 a un mensaje no necesita respuesta
    return f"[El cliente envió un mensaje de tipo '{tipo}' que no se puede leer]"


def leer_eventos(payload: dict) -> tuple[list[MensajeCliente], list[EcoDeLaApp]]:
    """Extrae los mensajes del JSON del webhook.

    Meta agrupa todo en entry[] → changes[] → value. En un mismo aviso
    pueden venir mensajes, cambios de estado (enviado/entregado/leído) o
    ecos de la app. Los cambios de estado se ignoran.
    """
    mensajes: list[MensajeCliente] = []
    ecos: list[EcoDeLaApp] = []

    if not payload.get("entry"):
        log.info("Payload descartado: no trae 'entry' (objeto=%s)", payload.get("object"))

    for entrada in payload.get("entry", []):
        for cambio in entrada.get("changes", []):
            campo = cambio.get("field")
            valor = cambio.get("value", {})

            if campo == "messages":
                # Perfil de cada remitente, buscable por teléfono o por BSUID
                perfiles = {}
                for c in valor.get("contacts", []):
                    for clave in (c.get("wa_id"), c.get("user_id")):
                        if clave:
                            perfiles[clave] = c.get("profile", {})

                estados = [s.get("status", "?") for s in valor.get("statuses", [])]
                if estados:
                    log.info("Payload descartado: aviso de estado (%s), no es un mensaje", ", ".join(estados))

                for m in valor.get("messages", []):
                    # Preferimos el teléfono; si Meta no lo comparte (usuarios
                    # con @username), usamos su BSUID para no perder el mensaje.
                    remitente = m.get("from") or m.get("from_user_id")
                    if not remitente:
                        log.info("Mensaje %s descartado: no trae 'from' ni 'from_user_id'", m.get("id"))
                        continue
                    texto = _describir(m)
                    if texto is None:
                        log.info("Mensaje %s de %s descartado: tipo '%s' no necesita respuesta",
                                 m.get("id"), remitente, m.get("type"))
                        continue
                    perfil = perfiles.get(remitente) or perfiles.get(m.get("from_user_id"), {})
                    mensajes.append(MensajeCliente(
                        id=m["id"],
                        telefono=remitente,
                        nombre=perfil.get("name", ""),
                        tipo=m.get("type", ""),
                        texto=texto,
                        usuario=perfil.get("username", ""),
                    ))

            elif campo == "smb_message_echoes":
                for m in valor.get("message_echoes", []):
                    telefono = m.get("to") or m.get("to_user_id")
                    if not telefono:
                        log.warning("Eco %s descartado: no trae 'to' ni 'to_user_id', "
                                    "no se sabe qué chat pausar", m.get("id"))
                        continue
                    if m.get("type") in ("edit", "revoke"):
                        log.info("Eco %s descartado: es una edición o un mensaje eliminado", m.get("id"))
                        continue
                    tipo = m.get("type", "")
                    texto = m["text"]["body"] if tipo == "text" else f"[El equipo envió un mensaje de tipo '{tipo}' desde la app]"
                    ecos.append(EcoDeLaApp(id=m["id"], telefono=telefono, tipo=tipo, texto=texto))

            else:
                log.info("Payload descartado: campo '%s' no se usa en este bot", campo)

    return mensajes, ecos


# ── 3. Enviar mensajes ──────────────────────────────────

def _url(ruta: str) -> str:
    return f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/{ruta}"


def _cabeceras() -> dict:
    return {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}


def _partir(texto: str) -> list[str]:
    """Divide un texto largo en trozos de máximo 4096 caracteres,
    cortando en saltos de línea cuando se puede."""
    trozos = []
    while len(texto) > LIMITE_TEXTO:
        corte = texto.rfind("\n", 0, LIMITE_TEXTO)
        if corte <= 0:
            corte = LIMITE_TEXTO
        trozos.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip()
    trozos.append(texto)
    return [t for t in trozos if t]


@dataclass
class ResultadoEnvio:
    ids: list[str]                 # IDs de los mensajes enviados (vacía si falló)
    error: str | None = None       # explicación del fallo, para tus avisos

    @property
    def ok(self) -> bool:
        return bool(self.ids) and self.error is None


# Explicaciones de los errores de Meta más comunes en este bot
_EXPLICACIONES = {
    ERROR_FUERA_DE_VENTANA: "pasaron más de 24 h desde su último mensaje; solo se le puede escribir con una plantilla",
    131030: ("el destinatario no está en la lista de números autorizados del número de prueba. "
             "Si es un usuario con @usuario (ID PE.…), puede que Meta no lo asocie a la lista aunque "
             "su número esté agregado"),
    131062: "a un usuario identificado solo por su ID (BSUID) no se le puede enviar este tipo de mensaje",
}


def enviar_texto(telefono: str, texto: str) -> list[str]:
    """Envía un texto a un teléfono o a un BSUID. Devuelve los IDs de los
    mensajes enviados (lista vacía si falló; el error queda en el log)."""
    return enviar_texto_detallado(telefono, texto).ids


def enviar_texto_detallado(telefono: str, texto: str) -> ResultadoEnvio:
    """Como enviar_texto, pero además explica el error si el envío falla."""
    # A un teléfono se le escribe con "to"; a un BSUID, con "recipient" (sin "to")
    destino = {"recipient": telefono} if es_bsuid(telefono) else {"to": telefono}
    ids = []
    for trozo in _partir(texto):
        cuerpo = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            **destino,
            "type": "text",
            "text": {"preview_url": False, "body": trozo},
        }
        try:
            respuesta = _cliente_http.post(_url("messages"), json=cuerpo, headers=_cabeceras())
        except httpx2.HTTPError as e:
            log.error("No se pudo conectar con WhatsApp para enviar a %s: %s", telefono, e)
            return ResultadoEnvio(ids, f"sin conexión con WhatsApp ({e})")

        if respuesta.status_code != 200:
            return ResultadoEnvio(ids, _registrar_error(telefono, respuesta))

        ids.extend(m["id"] for m in respuesta.json().get("messages", []))
    return ResultadoEnvio(ids)


def _registrar_error(telefono: str, respuesta) -> str:
    """Escribe el error en el log y devuelve una explicación corta."""
    try:
        error = respuesta.json().get("error", {})
    except ValueError:
        error = {}
    codigo = error.get("code")
    explicacion = _EXPLICACIONES.get(codigo, error.get("message", respuesta.text[:300]))
    log.error(
        "WhatsApp rechazó el envío a %s (HTTP %s, código %s): %s",
        telefono, respuesta.status_code, codigo, explicacion,
    )
    if codigo == ERROR_FUERA_DE_VENTANA:
        log.error("Si %s es tu número de admin, escribe 'hola' al bot para abrir la ventana.", telefono)
    return f"código {codigo}: {explicacion}"


# ── 4. Marcar como leído ────────────────────────────────

def marcar_leido(id_mensaje: str) -> None:
    """Pone los checks azules. Si falla no pasa nada grave: solo se registra."""
    cuerpo = {"messaging_product": "whatsapp", "status": "read", "message_id": id_mensaje}
    try:
        respuesta = _cliente_http.post(_url("messages"), json=cuerpo, headers=_cabeceras())
        if respuesta.status_code != 200:
            log.warning("No se pudo marcar como leído %s: HTTP %s", id_mensaje, respuesta.status_code)
    except httpx2.HTTPError as e:
        log.warning("No se pudo marcar como leído %s: %s", id_mensaje, e)
