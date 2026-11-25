"""Regresión: "Invalid signature in thinking block… The system prompt differs
from the one this block was created with" (producción, 24/25-nov-2026).

Causa: se guardaban en SQLite los bloques de thinking, que quedan atados al
prompt de sistema con que se generaron. Al cambiar sistema.md o /conocimiento,
reenviar esos bloques daba 400 y el chat dejaba de responder.
Arreglo: base_datos.py guarda y carga SOLO texto, y una migración limpió los
historiales viejos.

Sin API:   python -m pytest tests/test_historial_thinking.py -v
Con API:   $env:ASUNVIC_PRUEBA_REAL = "1"; python -m pytest tests/test_historial_thinking.py -v
           (~US$ 0.05: genera thinking real y responde tras cambiar el prompt)
"""

import copy
import json
import os
import sqlite3

import anthropic
import pytest

from src import base_datos as bd
from src import config

BLOQUE_THINKING = {"type": "thinking", "thinking": "", "signature": "firma-atada-al-prompt-viejo"}


@pytest.fixture
def db_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "prueba.db")
    bd.iniciar()
    return config.DB_PATH


def _historial_con_thinking():
    msg = anthropic.types.Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [BLOQUE_THINKING, {"type": "text", "text": "La chonta cuesta S/ 200."}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })
    return [{"role": "user", "content": "¿Cuánto cuesta la chonta?"},
            {"role": "assistant", "content": msg.content}]       # bloques del SDK, como en producción


def _tipos_guardados(db, tabla="conversaciones"):
    con = sqlite3.connect(db)
    tipos = set()
    for (h,) in con.execute(f"SELECT historial FROM {tabla}"):
        for m in json.loads(h):
            if isinstance(m["content"], list):
                tipos |= {b.get("type") for b in m["content"]}
    return tipos


# ── Sin API ─────────────────────────────────────────────

def test_guardar_no_guarda_thinking(db_temporal):
    bd.guardar_historial("51911111111", _historial_con_thinking())
    assert "thinking" not in _tipos_guardados(db_temporal)
    historial = bd.cargar_historial("51911111111")
    assert historial[1] == {"role": "assistant", "content": "La chonta cuesta S/ 200."}


def test_cargar_limpia_historiales_viejos_con_thinking(db_temporal):
    """Aunque la migración no hubiera corrido, lo que se carga va sin thinking."""
    viejo = json.dumps([{"role": "user", "content": "Hola"},
                        {"role": "assistant", "content": [BLOQUE_THINKING, {"type": "text", "text": "¡Hola!"}]}])
    con = sqlite3.connect(db_temporal)
    con.execute("INSERT INTO conversaciones (telefono, historial, actualizado) VALUES (?, ?, ?)",
                ("51922222222", viejo, bd._ahora()))
    con.commit()
    assert bd.cargar_historial("51922222222")[1]["content"] == "¡Hola!"


def test_turno_solo_con_thinking_no_queda_vacio(db_temporal):
    bd.guardar_historial("51933333333", [{"role": "user", "content": "Hola"},
                                         {"role": "assistant", "content": [BLOQUE_THINKING]}])
    assert bd.cargar_historial("51933333333")[1]["content"] == bd.TEXTO_VACIO   # la API rechaza contenido vacío


def test_migracion_limpia_todo_y_no_se_repite(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "vieja.db")
    # Base "de producción" creada con el código viejo: con thinking guardado
    con = sqlite3.connect(config.DB_PATH)
    con.executescript("""
        CREATE TABLE conversaciones (telefono TEXT PRIMARY KEY, historial TEXT NOT NULL DEFAULT '[]',
            pausado INTEGER NOT NULL DEFAULT 0, motivo_pausa TEXT, pausado_en TEXT, actualizado TEXT NOT NULL);
        CREATE TABLE conversaciones_archivo (id INTEGER PRIMARY KEY AUTOINCREMENT, telefono TEXT NOT NULL,
            historial TEXT NOT NULL, archivado_en TEXT NOT NULL);
    """)
    con_thinking = json.dumps([{"role": "user", "content": "Hola"},
                               {"role": "assistant", "content": [BLOQUE_THINKING, {"type": "text", "text": "Hola"}]}])
    con.execute("INSERT INTO conversaciones VALUES ('519', ?, 0, NULL, NULL, ?)", (con_thinking, bd._ahora()))
    con.execute("INSERT INTO conversaciones_archivo (telefono, historial, archivado_en) VALUES ('518', ?, ?)",
                (con_thinking, bd._ahora()))
    con.commit()
    con.close()

    bd.iniciar()   # aplica la migración
    assert "thinking" not in _tipos_guardados(config.DB_PATH, "conversaciones")
    assert "thinking" not in _tipos_guardados(config.DB_PATH, "conversaciones_archivo")
    assert bd.migrar_quitar_thinking() is None, "la migración no debe volver a correr"


# ── Con API: el escenario real de producción ────────────

@pytest.mark.skipif(os.getenv("ASUNVIC_PRUEBA_REAL") != "1", reason="usa la API (define ASUNVIC_PRUEBA_REAL=1)")
def test_regresion_historial_guardado_y_prompt_cambiado(db_temporal):
    from src.agente import Agente

    # 1. Conversación con el prompt "de ayer"
    agente = Agente()
    agente.sistema += "\n\n<!-- versión anterior del prompt -->"
    agente.responder("Hola, quiero 3 quenas de chonta y 2 de bambú flameado negro, envío a Puno. "
                     "¿Cuánto es el total y cuánto el adelanto?")
    thinking = sum(1 for m in agente.historial if not isinstance(m["content"], str)
                   for b in m["content"] if b.type == "thinking")
    if thinking == 0:
        pytest.skip("el modelo no generó thinking en este turno: el escenario no se reprodujo")
    bd.guardar_historial("51944444444", agente.historial)

    # 2. CONTROL: el código viejo (reenviar los bloques tal cual) con el prompt
    #    nuevo debe reproducir el error de producción
    viejo = copy.copy(agente)
    viejo.historial = list(agente.historial)
    viejo.recargar_conocimiento()   # prompt actual: distinto del de la conversación
    r_viejo = viejo.responder("¿Y el saldo cuándo lo pago?")
    reproducido = len(viejo.historial) == 2 and "signature" in r_viejo.texto.lower()

    # 3. ARREGLO: se carga de SQLite (sin thinking) y responde con el prompt nuevo
    nuevo = copy.copy(agente)
    nuevo.recargar_conocimiento()
    nuevo.historial = bd.cargar_historial("51944444444")
    respuesta = nuevo.responder("¿Y el saldo cuándo lo pago?")

    assert len(nuevo.historial) == 4, f"el agente no respondió: {respuesta.texto}"
    assert not respuesta.texto.startswith("(Error"), respuesta.texto
    if not reproducido:
        pytest.xfail("el arreglo funciona, pero el control no reprodujo el 400 (¿cuenta sin verificación de historial?)")
