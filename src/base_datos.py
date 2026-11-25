"""Base de datos SQLite: la memoria del bot entre mensajes.

SQLite es una base de datos que vive en un solo archivo (asunvic.db).
No hay que instalar nada: viene incluida en Python.

Tablas:
- conversaciones           historial y estado (pausado o no) de cada teléfono
- conversaciones_archivo   historiales viejos que se reiniciaron por inactividad
- mensajes_procesados      IDs de mensajes ya atendidos (Meta a veces reenvía)
- mensajes_enviados        IDs de lo que envió el BOT; sirve en coexistencia
                           para distinguir lo que respondiste tú desde la app
"""

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from src import config

log = logging.getLogger("asunvic.base_datos")

# Si un dueño responde a alguien que nunca escribió al bot, el historial
# empezaría con un mensaje del "assistant", y la API exige empezar con "user".
_INICIO_SIN_CLIENTE = "[Inicio de la conversación]"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conectar() -> sqlite3.Connection:
    # Una conexión nueva por operación: es lo más simple y seguro cuando
    # varios mensajes se procesan a la vez en hilos distintos.
    con = sqlite3.connect(config.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def iniciar() -> None:
    """Crea el archivo y las tablas si no existen. Se llama al arrancar."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_conectar()) as con, con:
        # WAL permite leer mientras otro hilo escribe
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversaciones (
                telefono      TEXT PRIMARY KEY,
                historial     TEXT NOT NULL DEFAULT '[]',
                pausado       INTEGER NOT NULL DEFAULT 0,
                motivo_pausa  TEXT,
                pausado_en    TEXT,
                actualizado   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversaciones_archivo (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono      TEXT NOT NULL,
                historial     TEXT NOT NULL,
                archivado_en  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mensajes_procesados (
                id_mensaje    TEXT PRIMARY KEY,
                fecha         TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mensajes_enviados (
                id_mensaje    TEXT PRIMARY KEY,
                telefono      TEXT NOT NULL,
                fecha         TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS migraciones (
                nombre        TEXT PRIMARY KEY,
                aplicada_en   TEXT NOT NULL,
                detalle       TEXT
            );
            """
        )
    migrar_quitar_thinking()
    limpiar_antiguos()


def migrar_quitar_thinking() -> dict | None:
    """MIGRACIÓN ÚNICA: quita los bloques de thinking de TODOS los historiales
    ya guardados (activos y archivados), que se guardaron con el código viejo.

    Corre sola al arrancar (iniciar) y queda anotada en la tabla
    `migraciones`, así no se repite. Devuelve un resumen, o None si ya se
    había aplicado.
    """
    nombre = "2026-11-25_quitar_thinking"
    with closing(_conectar()) as con, con:
        if con.execute("SELECT 1 FROM migraciones WHERE nombre = ?", (nombre,)).fetchone():
            return None
        resumen = {"historiales": 0, "modificados": 0, "bloques_quitados": 0}
        for tabla, clave in (("conversaciones", "telefono"), ("conversaciones_archivo", "id")):
            for fila in con.execute(f"SELECT {clave} AS clave, historial FROM {tabla}").fetchall():
                resumen["historiales"] += 1
                original = json.loads(fila["historial"])
                quitados = sum(
                    1 for m in original if isinstance(m["content"], list)
                    for b in m["content"] if b.get("type") != "text"
                )
                limpio = historial_a_json(original)
                if quitados or limpio != fila["historial"]:
                    con.execute(f"UPDATE {tabla} SET historial = ? WHERE {clave} = ?", (limpio, fila["clave"]))
                    resumen["modificados"] += 1
                    resumen["bloques_quitados"] += quitados
        con.execute(
            "INSERT INTO migraciones (nombre, aplicada_en, detalle) VALUES (?, ?, ?)",
            (nombre, _ahora(), json.dumps(resumen)),
        )
    log.info("Migración %s aplicada: %s", nombre, resumen)
    return resumen


def limpiar_antiguos() -> None:
    """Aplica los plazos de la política de privacidad (/privacidad):

    1. Conversaciones sin mensajes en DIAS_REINICIO_HISTORIAL días (y no
       pausadas) → se archivan.
    2. Archivadas hace más de DIAS_CONSERVAR_ARCHIVO días → se borran.
    3. IDs técnicos de mensajes de más de 7 días → se borran (Meta no
       reenvía avisos tan tarde).

    Se llama al arrancar y una vez al día desde atencion.py.
    """
    ahora = datetime.now(timezone.utc)
    inactivas = (ahora - timedelta(days=config.DIAS_REINICIO_HISTORIAL)).isoformat()
    archivo_viejo = (ahora - timedelta(days=config.DIAS_CONSERVAR_ARCHIVO)).isoformat()
    ids_viejos = (ahora - timedelta(days=7)).isoformat()

    with closing(_conectar()) as con, con:
        con.execute(
            """
            INSERT INTO conversaciones_archivo (telefono, historial, archivado_en)
            SELECT telefono, historial, ? FROM conversaciones
            WHERE pausado = 0 AND actualizado < ? AND historial != '[]'
            """,
            (ahora.isoformat(), inactivas),
        )
        con.execute(
            "DELETE FROM conversaciones WHERE pausado = 0 AND actualizado < ?", (inactivas,)
        )
        con.execute("DELETE FROM conversaciones_archivo WHERE archivado_en < ?", (archivo_viejo,))
        con.execute("DELETE FROM mensajes_procesados WHERE fecha < ?", (ids_viejos,))
        con.execute("DELETE FROM mensajes_enviados WHERE fecha < ?", (ids_viejos,))


def borrar_datos(telefono: str) -> int:
    """Borra TODO lo guardado de un número (solicitud de eliminación).

    Devuelve cuántos registros se borraron.
    """
    with closing(_conectar()) as con, con:
        total = 0
        for tabla in ("conversaciones", "conversaciones_archivo", "mensajes_enviados"):
            total += con.execute(f"DELETE FROM {tabla} WHERE telefono = ?", (telefono,)).rowcount
        return total


# ── Historial ───────────────────────────────────────────
#
# SOLO SE GUARDA EL TEXTO de cada turno, nunca los bloques de "thinking".
#
# Por qué: cada bloque de thinking viene firmado y queda atado al prompt de
# sistema con el que se generó. Si después cambia sistema.md o /conocimiento
# y se reenvía ese bloque, la API responde 400: "Invalid signature in thinking
# block… The system prompt differs from the one this block was created with".
# Pasó en producción con los chats de los testers (24/25-nov-2026).
#
# Quitar los bloques es la solución documentada ("Remove the block"). El costo
# es que el modelo no ve su razonamiento de turnos ANTERIORES (sí ve todo lo que
# dijo); con esfuerzo "low" en un chat de atención el efecto es mínimo.
# También se quitan otros bloques internos (redacted_thinking, fallback…).
#
# Ojo: dentro de una misma llamada, agente.py sí mantiene los bloques en
# memoria. Solo se descartan al guardar en la base de datos.

TEXTO_VACIO = "…"   # la API no acepta un turno sin contenido


def _contenido_a_texto(contenido) -> str:
    """Convierte el contenido de un turno (texto o lista de bloques del SDK o
    dict) en texto plano, descartando thinking y cualquier bloque no textual."""
    if isinstance(contenido, str):
        return contenido
    partes = []
    for bloque in contenido:
        datos = bloque if isinstance(bloque, dict) else bloque.to_dict(mode="json", exclude_none=True)
        if datos.get("type") == "text" and datos.get("text"):
            partes.append(datos["text"])
    texto = "".join(partes).strip()
    return texto or TEXTO_VACIO


def historial_a_json(historial: list) -> str:
    """Historial (en memoria) → JSON para SQLite. Solo texto, sin thinking."""
    return json.dumps(
        [{"role": m["role"], "content": _contenido_a_texto(m["content"])} for m in historial],
        ensure_ascii=False,
    )


def historial_de_json(datos: str) -> list:
    """JSON de SQLite → historial para agente.historial.

    También limpia al CARGAR: así un historial guardado con una versión vieja
    del código (con thinking) nunca llega a la API, aunque la migración no
    haya corrido todavía.
    """
    return [{"role": m["role"], "content": _contenido_a_texto(m["content"])} for m in json.loads(datos)]


def cargar_historial(telefono: str) -> list:
    """Devuelve el historial del cliente, listo para agente.historial.

    Si lleva más de DIAS_REINICIO_HISTORIAL días sin escribir (y no está
    pausado), se archiva y empieza de cero.
    """
    with closing(_conectar()) as con, con:
        fila = con.execute(
            "SELECT historial, pausado, actualizado FROM conversaciones WHERE telefono = ?",
            (telefono,),
        ).fetchone()
        if fila is None:
            return []

        historial = historial_de_json(fila["historial"])
        ultima = datetime.fromisoformat(fila["actualizado"])
        vencido = datetime.now(timezone.utc) - ultima > timedelta(days=config.DIAS_REINICIO_HISTORIAL)

        if vencido and not fila["pausado"] and historial:
            con.execute(
                "INSERT INTO conversaciones_archivo (telefono, historial, archivado_en) VALUES (?, ?, ?)",
                (telefono, historial_a_json(historial), _ahora()),
            )
            con.execute(
                "UPDATE conversaciones SET historial = '[]', actualizado = ? WHERE telefono = ?",
                (_ahora(), telefono),
            )
            return []
        return historial


def guardar_historial(telefono: str, historial: list) -> None:
    datos = historial_a_json(historial)
    with closing(_conectar()) as con, con:
        con.execute(
            """
            INSERT INTO conversaciones (telefono, historial, actualizado) VALUES (?, ?, ?)
            ON CONFLICT(telefono) DO UPDATE SET historial = excluded.historial,
                                                actualizado = excluded.actualizado
            """,
            (telefono, datos, _ahora()),
        )


def agregar_mensaje(telefono: str, rol: str, texto: str) -> None:
    """Añade un mensaje al final del historial sin pasar por el agente.

    rol="user":      lo que escribe el cliente mientras el chat está pausado.
    rol="assistant": lo que respondes tú (por /responder o desde la app).
    Así, al reactivar, el agente sabe todo lo que pasó.
    """
    historial = cargar_historial(telefono)
    if not historial and rol == "assistant":
        historial.append({"role": "user", "content": _INICIO_SIN_CLIENTE})
    historial.append({"role": rol, "content": texto})
    guardar_historial(telefono, historial)


# ── Pausa (pasar a humano) ──────────────────────────────

def pausar(telefono: str, motivo: str) -> None:
    with closing(_conectar()) as con, con:
        con.execute(
            """
            INSERT INTO conversaciones (telefono, pausado, motivo_pausa, pausado_en, actualizado)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(telefono) DO UPDATE SET pausado = 1,
                                                motivo_pausa = excluded.motivo_pausa,
                                                pausado_en = excluded.pausado_en
            """,
            (telefono, motivo, _ahora(), _ahora()),
        )


def reactivar(telefono: str) -> bool:
    """Devuelve True si el chat estaba pausado y se reactivó."""
    with closing(_conectar()) as con, con:
        cursor = con.execute(
            "UPDATE conversaciones SET pausado = 0, motivo_pausa = NULL, pausado_en = NULL "
            "WHERE telefono = ? AND pausado = 1",
            (telefono,),
        )
        return cursor.rowcount > 0


def esta_pausado(telefono: str) -> bool:
    with closing(_conectar()) as con:
        fila = con.execute(
            "SELECT pausado FROM conversaciones WHERE telefono = ?", (telefono,)
        ).fetchone()
        return bool(fila and fila["pausado"])


def listar_pausados() -> list[dict]:
    with closing(_conectar()) as con:
        filas = con.execute(
            "SELECT telefono, motivo_pausa, pausado_en FROM conversaciones "
            "WHERE pausado = 1 ORDER BY pausado_en"
        ).fetchall()
        return [dict(f) for f in filas]


# ── Control de mensajes ─────────────────────────────────

def marcar_procesado(id_mensaje: str) -> bool:
    """Registra el ID. Devuelve True si es nuevo, False si ya se había visto.

    Se hace en un solo paso (INSERT OR IGNORE) para que, si Meta manda el
    mismo mensaje dos veces casi a la vez, solo uno de los dos pase.
    """
    with closing(_conectar()) as con, con:
        cursor = con.execute(
            "INSERT OR IGNORE INTO mensajes_procesados (id_mensaje, fecha) VALUES (?, ?)",
            (id_mensaje, _ahora()),
        )
        return cursor.rowcount == 1


def registrar_enviado(id_mensaje: str, telefono: str) -> None:
    with closing(_conectar()) as con, con:
        con.execute(
            "INSERT OR IGNORE INTO mensajes_enviados (id_mensaje, telefono, fecha) VALUES (?, ?, ?)",
            (id_mensaje, telefono, _ahora()),
        )


def fue_enviado_por_bot(id_mensaje: str) -> bool:
    with closing(_conectar()) as con:
        fila = con.execute(
            "SELECT 1 FROM mensajes_enviados WHERE id_mensaje = ?", (id_mensaje,)
        ).fetchone()
        return fila is not None
