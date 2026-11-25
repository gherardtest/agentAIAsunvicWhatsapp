"""Proveedor de Promptfoo: el sistema bajo prueba es agente.py (sin WhatsApp).

Promptfoo llama a call_api() con el texto del ataque. Cada ataque empieza
una conversación nueva (historial vacío), como un cliente nuevo.

La salida es el texto que vería el cliente. Si el agente marcó [HUMANO],
se agrega una línea "⟦escala_a_humano: motivo⟧" para poder verificarlo, y
"⟦etiqueta_descartada: n⟧" si el modelo escribió la etiqueta fuera de lugar.
Usamos otro formato a propósito: así un "[HUMANO" en la salida solo puede
ser una FUGA de la etiqueta interna, no nuestra marca.

Modo falso (sin API): ASUNVIC_FALSO=1 devuelve respuestas fijas, para
probar la configuración de Promptfoo sin gastar.
"""

import copy
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_agente_base = None


def call_api(prompt, options, context):
    global _agente_base
    if os.getenv("ASUNVIC_FALSO") == "1":
        # ASUNVIC_FALSO_SALIDA permite simular una respuesta "comprometida"
        # y comprobar que las aserciones detectan el ataque.
        return {"output": os.getenv("ASUNVIC_FALSO_SALIDA",
                                    "Hola 😊 La quena de chonta cuesta S/ 200 + S/ 20 de envío.\n⟦escala_a_humano: prueba⟧")}

    try:
        from src.agente import Agente

        if _agente_base is None:
            _agente_base = Agente()
        agente = copy.copy(_agente_base)
        agente.historial = []
        turnos_antes = 0

        respuesta = agente.responder(prompt)
    except SystemExit as e:  # API key o modelo mal configurados
        return {"error": f"Error de configuración del agente: {e}"}

    if len(agente.historial) != turnos_antes + 2:
        return {"error": f"El agente no respondió: {respuesta.texto}"}

    salida = respuesta.texto
    if respuesta.pasar_a_humano:
        salida += f"\n⟦escala_a_humano: {respuesta.pasar_a_humano}⟧"
    if respuesta.etiquetas_descartadas:
        # agente.py borra del texto las etiquetas fuera de lugar, así que la
        # frase trampa "[HUMANO" ya no las vería: lo marcamos explícitamente.
        salida += f"\n⟦etiqueta_descartada: {respuesta.etiquetas_descartadas}⟧"
    return {"output": salida}
