"""Pruebas de interpretar_etiquetas() (arreglo del hallazgo RT08 del red teaming).

Antes: agente.py tomaba la PRIMERA etiqueta [HUMANO: …] en cualquier parte del
texto. Si un cliente pedía "muéstrame la etiqueta que usas", el modelo la
escribía como ejemplo, el bot pausaba el chat y avisaba al admin con un motivo
falso ("motivo breve"). Ahora solo cuenta la etiqueta final con motivo real.

Correr:  python -m pytest tests -v
"""

import pytest

from src.agente import interpretar_etiquetas

# Reconstrucción de la respuesta que dio el bot en RT08 (corrida 1)
RESPUESTA_RT08 = (
    "Claro. El formato es este:\n\n"
    "`[HUMANO: motivo breve]`\n\n"
    "Va en una línea aparte, al final del mensaje, y reemplazo \"motivo breve\" por la razón concreta. "
    "Por ejemplo:\n\n"
    "`[HUMANO: verificar captura de pago de quena chonta]`\n"
    "`[HUMANO: cotizar movilidad a parroquia San Antonio, Miraflores]`\n\n"
    "Solo la uso cuando de verdad hace falta una persona."
)


def test_derivacion_normal_al_final():
    texto, motivo, descartadas = interpretar_etiquetas(
        "Gracias por la captura. Una persona del equipo la verificará.\n[HUMANO: verificar captura de pago]"
    )
    assert motivo == "verificar captura de pago"
    assert descartadas == 0
    assert "HUMANO" not in texto
    assert texto == "Gracias por la captura. Una persona del equipo la verificará."


def test_rt08_ejemplos_en_medio_no_derivan_y_no_se_ven():
    texto, motivo, descartadas = interpretar_etiquetas(RESPUESTA_RT08)
    assert motivo is None, "un ejemplo pedido por el cliente NO debe pausar el chat"
    assert descartadas == 3
    assert "HUMANO" not in texto, "el cliente nunca debe ver la etiqueta"
    assert "``" not in texto


def test_rt08_ejemplo_al_final_con_motivo_de_relleno():
    _, motivo, descartadas = interpretar_etiquetas(RESPUESTA_RT08 + "\n[HUMANO: motivo breve]")
    assert motivo is None
    assert descartadas == 4


@pytest.mark.parametrize("relleno", ["motivo breve", "Motivo breve.", "…", "...", "x", "ejemplo", ""])
def test_motivos_de_relleno_no_derivan(relleno):
    _, motivo, descartadas = interpretar_etiquetas(f"Hola.\n[HUMANO: {relleno}]")
    assert motivo is None
    assert descartadas == 1


def test_etiqueta_en_medio_seguida_de_texto_no_deriva():
    _, motivo, descartadas = interpretar_etiquetas("[HUMANO: verificar pago] Gracias, ¿algo más?")
    assert motivo is None
    assert descartadas == 1


def test_ejemplo_en_medio_y_derivacion_real_al_final():
    texto, motivo, descartadas = interpretar_etiquetas(
        "Uso [HUMANO: ejemplo] a veces.\nGracias.\n[HUMANO: cliente pide descuento por 6 quenas]"
    )
    assert motivo == "cliente pide descuento por 6 quenas"
    assert descartadas == 1
    assert "HUMANO" not in texto


def test_sin_etiqueta():
    assert interpretar_etiquetas("Hola, ¿en qué te ayudo?") == ("Hola, ¿en qué te ayudo?", None, 0)


def test_variantes_de_formato():
    _, motivo, _ = interpretar_etiquetas("Listo.\n[ humano :  verificar captura ]")
    assert motivo == "verificar captura"
