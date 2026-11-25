"""Decide qué hacer con cada mensaje que llega por WhatsApp.

Es el equivalente a consola.py de la fase 2, pero para WhatsApp:

  mensaje del ADMIN (tu número)      → comando (/reactivar, /pausados...)
  mensaje de un CLIENTE, chat activo → el agente responde
  mensaje de un CLIENTE, chat pausado→ se guarda y se te reenvía
  ECO de la app (coexistencia)       → respondiste tú: se pausa el bot

agente.py no se modifica: por cada mensaje hacemos una copia ligera del
agente (comparte la conexión con Claude y el conocimiento) y le ponemos
el historial de ese cliente, cargado desde SQLite.
"""

import copy
import logging
import threading
import time

from src import base_datos as bd
from src import config
from src import whatsapp as wa
from src.agente import MENSAJE_ERROR_CLIENTE, Agente

log = logging.getLogger("asunvic.atencion")

AYUDA_ADMIN = """🤖 *Comandos del bot*
/pausados — chats que esperan a una persona
/ver 519… — últimos mensajes de ese chat
/responder 519… texto — escribirle al cliente desde el número del bot
/reactivar 519… — el bot vuelve a atender ese chat
/pausar 519… motivo — pausar un chat a mano
/borrar 519… si — borrar TODOS los datos de ese número
/cliente — probar el bot como si fueras un cliente
/admin — volver al modo administrador
/ayuda — esta lista"""

# ── Estado compartido ───────────────────────────────────

_agente_base: Agente | None = None
_candado_agente = threading.Lock()

# Un candado por teléfono: si un cliente manda dos mensajes seguidos, el
# segundo espera a que termine el primero (así no se mezclan los historiales).
_candados: dict[str, threading.Lock] = {}
_candado_candados = threading.Lock()

# Si está activo, tus mensajes (los que no empiezan con /) van al agente
# como si fueras un cliente. Se guarda en memoria: al reiniciar vuelve a admin.
_admin_en_modo_cliente = False

# Última vez que se aplicaron los plazos de borrado (una vez al día)
_ultima_limpieza = 0.0
_candado_limpieza = threading.Lock()


def _limpiar_si_toca() -> None:
    global _ultima_limpieza
    with _candado_limpieza:
        if time.time() - _ultima_limpieza < 24 * 3600:
            return
        _ultima_limpieza = time.time()
    bd.limpiar_antiguos()
    log.info("Limpieza diaria de datos antiguos hecha")


def _agente_para(historial: list) -> Agente:
    global _agente_base
    with _candado_agente:
        if _agente_base is None:
            _agente_base = Agente()
    agente = copy.copy(_agente_base)   # copia ligera: comparte cliente y conocimiento
    agente.historial = historial       # ...pero con el historial de ESTE cliente
    return agente


def _candado(telefono: str) -> threading.Lock:
    with _candado_candados:
        return _candados.setdefault(telefono, threading.Lock())


# ── Envíos ──────────────────────────────────────────────

def _enviar(telefono: str, texto: str) -> bool:
    """Envía y registra los IDs (para no confundirlos luego con tus respuestas)."""
    ids = wa.enviar_texto(telefono, texto)
    for id_mensaje in ids:
        bd.registrar_enviado(id_mensaje, telefono)
    return bool(ids)


def _avisar_admin(texto: str) -> None:
    if not _enviar(config.NUMERO_ADMIN, texto):
        log.error("No se pudo avisar al admin. Aviso perdido: %s", texto)


# ── Punto de entrada ────────────────────────────────────

def procesar_payload(payload: dict) -> None:
    """Lo llama servidor.py en segundo plano por cada aviso de Meta."""
    try:
        _limpiar_si_toca()
        mensajes, ecos = wa.leer_eventos(payload)
        for eco in ecos:
            _procesar_eco(eco)
        for mensaje in mensajes:
            _procesar_mensaje(mensaje)
    except Exception:
        # Un error con un mensaje no debe tumbar el servidor
        log.exception("Error inesperado procesando un aviso de Meta")


def _procesar_mensaje(m: wa.MensajeCliente) -> None:
    if not bd.marcar_procesado(m.id):
        log.info("Mensaje %s de %s descartado: repetido (Meta lo reenvió)", m.id, m.telefono)
        return
    wa.marcar_leido(m.id)
    log.info("Mensaje de %s (%s): %s", m.telefono, m.tipo, m.texto[:100])

    es_admin = m.telefono == config.NUMERO_ADMIN
    if es_admin and (m.texto.startswith("/") or not _admin_en_modo_cliente):
        _comando_admin(m.texto.strip())
        return

    with _candado(m.telefono):
        if bd.esta_pausado(m.telefono):
            _mensaje_en_chat_pausado(m, es_admin)
        else:
            _responder_con_agente(m)


def _quien(m: wa.MensajeCliente) -> str:
    """Cómo se muestra el cliente en tus avisos: id (Nombre, @usuario)."""
    detalles = [d for d in (m.nombre, f"@{m.usuario}" if m.usuario else "") if d]
    return m.telefono + (f" ({', '.join(detalles)})" if detalles else "")


def _mensaje_en_chat_pausado(m: wa.MensajeCliente, es_admin: bool) -> None:
    bd.agregar_mensaje(m.telefono, "user", m.texto)
    log.info("Mensaje de %s guardado sin responder: el chat está pausado", m.telefono)
    if not es_admin:
        _avisar_admin(f"💬 {_quien(m)} [pausado]:\n{m.texto}")


def _responder_con_agente(m: wa.MensajeCliente) -> None:
    agente = _agente_para(bd.cargar_historial(m.telefono))
    turnos_antes = len(agente.historial)

    try:
        respuesta = agente.responder(m.texto)
    except SystemExit as e:
        # agente.py cierra el programa si la API key o el modelo están mal.
        # En la consola tiene sentido; en un servidor no: lo atrapamos aquí.
        log.critical("El agente no puede funcionar: %s", e)
        bd.agregar_mensaje(m.telefono, "user", m.texto)
        _fallo_del_agente(m, f"error de configuración: {e}")
        return

    # Si todo fue bien, el agente añadió 2 turnos (cliente + respuesta).
    # Si hubo un error, los quitó, y respuesta.texto es un aviso para la
    # consola que NO debe llegar al cliente.
    exito = len(agente.historial) == turnos_antes + 2
    if not exito:
        bd.agregar_mensaje(m.telefono, "user", m.texto)
        if respuesta.pasar_a_humano:          # el modelo no pudo responder
            _fallo_del_agente(m, respuesta.pasar_a_humano)
        else:                                 # error de conexión o límite de uso
            log.error("Fallo técnico respondiendo a %s: %s", m.telefono, respuesta.texto)
            _fallo_del_agente(m, f"fallo técnico: {respuesta.texto}")
        return

    if respuesta.texto:
        envio = wa.enviar_texto_detallado(m.telefono, respuesta.texto)
        for id_mensaje in envio.ids:
            bd.registrar_enviado(id_mensaje, m.telefono)
        if not envio.ok:
            # La respuesta NO llegó: no la guardamos como dicha (el agente
            # creería que ya contestó), pausamos el chat y te avisamos.
            bd.guardar_historial(m.telefono, agente.historial[:-1])
            bd.pausar(m.telefono, f"no se pudo entregar la respuesta ({envio.error})")
            log.error("Respuesta a %s NO entregada: %s", m.telefono, envio.error)
            _avisar_admin(
                f"❌ *No se pudo entregar la respuesta* a {_quien(m)}"
                f"\n*Error:* {envio.error}"
                f"\n*Su mensaje:* {m.texto[:300]}"
                f"\n\nChat pausado. Cuando lo resuelvas: /reactivar {m.telefono}"
            )
            return

    bd.guardar_historial(m.telefono, agente.historial)
    log.info("Respuesta a %s: %s", m.telefono, respuesta.texto[:100])
    if respuesta.etiquetas_descartadas:
        # El modelo escribió [HUMANO] fuera de lugar: posible intento de sacarle
        # sus instrucciones internas. No pausamos el chat, pero queda registrado.
        log.warning("Posible fuga de la etiqueta interna en el chat de %s (%s descartadas). Mensaje: %s",
                    m.telefono, respuesta.etiquetas_descartadas, m.texto[:200])
        _avisar_admin(f"🛡️ El bot escribió su etiqueta interna en el chat de {_quien(m)} "
                      f"(posible intento de manipulación). No se pausó.\n/ver {m.telefono}")

    if respuesta.pasar_a_humano:
        bd.pausar(m.telefono, respuesta.pasar_a_humano)
        _avisar_admin(
            f"🔔 *Pasar a humano:* {_quien(m)}"
            f"\n*Motivo:* {respuesta.pasar_a_humano}"
            f"\n*Último mensaje:* {m.texto[:300]}"
            f"\n\n/ver {m.telefono}\n/reactivar {m.telefono}"
        )


def _fallo_del_agente(m: wa.MensajeCliente, motivo: str) -> None:
    """El bot no pudo responder: el cliente recibe un mensaje amable y te avisamos."""
    _enviar(m.telefono, MENSAJE_ERROR_CLIENTE)
    bd.agregar_mensaje(m.telefono, "assistant", MENSAJE_ERROR_CLIENTE)
    bd.pausar(m.telefono, motivo)
    _avisar_admin(
        f"⚠️ *El bot no pudo responder* a {_quien(m)}\n*Motivo:* {motivo}"
        f"\n*Mensaje:* {m.texto[:300]}\n\n/reactivar {m.telefono}"
    )


# ── Coexistencia: respondiste tú desde la app ───────────

def _procesar_eco(eco: wa.EcoDeLaApp) -> None:
    if bd.fue_enviado_por_bot(eco.id):
        log.info("Eco %s descartado: lo envió el bot por la API, no tú", eco.id)
        return
    if not bd.marcar_procesado(eco.id):
        log.info("Eco %s descartado: repetido", eco.id)
        return
    with _candado(eco.telefono):
        bd.agregar_mensaje(eco.telefono, "assistant", eco.texto)
        if not bd.esta_pausado(eco.telefono):
            bd.pausar(eco.telefono, "respondiste desde la app")
            _avisar_admin(
                f"⏸️ Bot pausado en {eco.telefono} porque respondiste desde la app."
                f"\nCuando termines: /reactivar {eco.telefono}"
            )


# ── Comandos del admin ──────────────────────────────────

def _normalizar_telefono(texto: str) -> str:
    """Acepta '+51 987 654 321', '987654321', '51987654321'...
    y también un BSUID tal cual ('PE.1854414865870128')."""
    if wa.es_bsuid(texto):
        return texto
    digitos = "".join(c for c in texto if c.isdigit())
    if len(digitos) == 9 and digitos.startswith("9"):  # celular peruano sin código
        digitos = "51" + digitos
    return digitos


def _texto_de(contenido) -> str:
    if isinstance(contenido, str):
        return contenido
    return "".join(b.get("text", "") for b in contenido if b.get("type") == "text")


def _comando_admin(texto: str) -> None:
    global _admin_en_modo_cliente
    partes = texto.split(maxsplit=2)
    comando = partes[0].lower() if partes else ""
    telefono = _normalizar_telefono(partes[1]) if len(partes) > 1 else ""

    if comando == "/pausados":
        pausados = bd.listar_pausados()
        if not pausados:
            _avisar_admin("✅ No hay chats pausados.")
            return
        lineas = [f"• {p['telefono']} — {p['motivo_pausa'] or 'sin motivo'}" for p in pausados]
        _avisar_admin("⏸️ *Chats pausados:*\n" + "\n".join(lineas))

    elif comando == "/reactivar" and telefono:
        if bd.reactivar(telefono):
            _avisar_admin(f"▶️ El bot vuelve a atender a {telefono}.")
        else:
            _avisar_admin(f"Ese chat ({telefono}) no estaba pausado.")

    elif comando == "/pausar" and telefono:
        motivo = partes[2] if len(partes) > 2 else "pausado a mano"
        bd.pausar(telefono, motivo)
        _avisar_admin(f"⏸️ Chat {telefono} pausado. Para reactivar: /reactivar {telefono}")

    elif comando == "/responder" and telefono and len(partes) > 2:
        mensaje = partes[2]
        with _candado(telefono):
            if _enviar(telefono, mensaje):
                bd.agregar_mensaje(telefono, "assistant", mensaje)
                bd.pausar(telefono, "estás respondiendo tú")
                _avisar_admin(f"📤 Enviado a {telefono}. El chat sigue pausado hasta /reactivar {telefono}")
            else:
                _avisar_admin(
                    f"❌ No se pudo enviar a {telefono}. Si el cliente no escribe hace más "
                    "de 24 h, WhatsApp solo permite plantillas."
                )

    elif comando == "/ver" and telefono:
        historial = bd.cargar_historial(telefono)
        lineas = []
        for turno in historial[-8:]:
            texto_turno = _texto_de(turno["content"]).strip()
            if texto_turno:
                quien = "👤" if turno["role"] == "user" else "🤖"
                lineas.append(f"{quien} {texto_turno[:300]}")
        estado = "⏸️ pausado" if bd.esta_pausado(telefono) else "▶️ activo"
        _avisar_admin(f"*Chat {telefono}* ({estado})\n\n" + ("\n\n".join(lineas) or "(sin mensajes)"))

    elif comando == "/borrar" and telefono:
        # Pide confirmación: borrar no se puede deshacer
        confirmado = len(partes) > 2 and partes[2].strip().lower() in ("si", "sí")
        if not confirmado:
            _avisar_admin(
                f"🗑️ Esto borrará el historial, el archivo y el estado del chat {telefono}. "
                f"No se puede deshacer.\nPara confirmar: /borrar {telefono} si"
            )
            return
        with _candado(telefono):
            total = bd.borrar_datos(telefono)
        log.info("Datos de %s borrados a pedido (%s registros)", telefono, total)
        _avisar_admin(
            f"✅ Datos de {telefono} borrados ({total} registros).\n"
            "Si quieres confirmárselo al cliente, usa /responder. Ojo: ese mensaje "
            "iniciará un chat nuevo en la base de datos."
        )

    elif comando == "/cliente":
        _admin_en_modo_cliente = True
        _avisar_admin("🧪 Modo cliente: lo que escribas (sin /) lo responde el bot. /admin para salir.")

    elif comando == "/admin":
        _admin_en_modo_cliente = False
        _avisar_admin("🛠️ Modo administrador.\n\n" + AYUDA_ADMIN)

    else:
        # Cualquier otro mensaje (por ejemplo tu "hola" diario) muestra la ayuda.
        _avisar_admin("👋 Ventana de 24 h abierta.\n\n" + AYUDA_ADMIN)
