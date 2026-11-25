"""Pruebas del caché de prompts de agente.py.

1. test_peticion_*: sin API. Revisan que la petición a Claude lleve el punto
   de caché fijo en el prompt de sistema y la caché automática.
2. test_cache_real_entre_clientes: CON API (cuesta ~US$ 0.03). Dos clientes
   NUEVOS con mensajes distintos: el segundo debe leer el prompt de sistema
   de la caché. Solo corre si ASUNVIC_PRUEBA_REAL=1.

Correr:  python -m pytest tests -v
         $env:ASUNVIC_PRUEBA_REAL = "1"; python -m pytest tests -v
"""

import copy
import os
from types import SimpleNamespace

import pytest

from src import config
from src.agente import Agente


class _ClienteFalso:
    """Guarda la petición en vez de enviarla."""

    def __init__(self):
        self.peticiones = []
        crear = self._crear
        self.messages = SimpleNamespace(create=crear)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=crear))

    def _crear(self, **kwargs):
        self.peticiones.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Hola")],
            usage=SimpleNamespace(cache_read_input_tokens=0),
        )


@pytest.fixture
def agente_falso(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY") or "sk-ant-falsa")
    agente = Agente()
    agente.cliente = _ClienteFalso()
    return agente


@pytest.mark.parametrize("modelo", ["claude-sonnet-5-5", "claude-haiku-5-5"])
def test_peticion_lleva_cache_fijo_en_el_sistema(agente_falso, monkeypatch, modelo):
    # Sonnet va por la ruta con respaldo (beta); Haiku por la normal: probamos ambas
    monkeypatch.setattr(config, "MODELO", modelo)
    agente_falso.responder("Hola")
    peticion = agente_falso.cliente.peticiones[-1]

    sistema = peticion["system"]
    assert isinstance(sistema, list), "system debe ser una lista de bloques para poder marcar la caché"
    assert sistema[-1]["cache_control"] == {"type": "ephemeral"}
    assert sistema[-1]["text"] == agente_falso.sistema, "el texto del prompt no debe cambiar"


def test_peticion_mantiene_cache_automatica(agente_falso):
    agente_falso.responder("Hola")
    assert agente_falso.cliente.peticiones[-1]["cache_control"] == {"type": "ephemeral"}


def test_prompt_de_sistema_es_identico_entre_clientes(agente_falso):
    """Si el prompt cambiara entre clientes (fecha, nombre...), la caché nunca acertaría."""
    otro = copy.copy(agente_falso)
    otro.historial = []
    agente_falso.responder("Hola")
    otro.responder("¿Venden quenas?")
    a, b = agente_falso.cliente.peticiones[-2:]
    assert a["system"] == b["system"]


@pytest.mark.skipif(os.getenv("ASUNVIC_PRUEBA_REAL") != "1", reason="usa la API (define ASUNVIC_PRUEBA_REAL=1)")
def test_cache_real_entre_clientes():
    base = Agente()
    primero = copy.copy(base)
    primero.historial = []
    primero.responder("Hola, ¿qué paquetes tiene el coro?")      # escribe (o ya lee) la caché

    segundo = copy.copy(base)
    segundo.historial = []
    respuesta = segundo.responder("Buenas, ¿venden quenas de madera?")   # cliente NUEVO, otro mensaje

    # El prompt de sistema mide ~7.800 tokens: casi todo debe venir de la caché
    assert respuesta.tokens_cache >= 5000, (
        f"el 2.º cliente leyó solo {respuesta.tokens_cache} tokens de la caché"
    )
