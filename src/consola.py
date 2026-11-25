"""FASE 2: chat de prueba en la terminal.

Tú escribes como si fueras un cliente de WhatsApp y el agente responde.
Ejecuta desde la carpeta del proyecto:   python -m src.consola
"""

import sys

from src import config
from src.agente import Agente
from src.conocimiento import nombres_de_archivos

AYUDA = """Comandos:
  /captura    simula que el cliente envía una captura de pago
  /reiniciar  empieza una conversación nueva (otro cliente)
  /recargar   vuelve a leer /conocimiento y prompts/sistema.md
  /salir      cierra el programa"""

# WhatsApp no deja que el bot "vea" la imagen en esta fase; la simulamos con texto.
MENSAJE_CAPTURA = "[El cliente envió una imagen: captura de pago de Yape]"


def main() -> None:
    # Evita errores con tildes y emojis en algunas terminales de Windows
    sys.stdout.reconfigure(encoding="utf-8")
    config.verificar_api_key()
    agente = Agente()

    print("=" * 60)
    print("  Asunvic Bot — chat de prueba")
    print(f"  Modelo: {config.MODELO} (esfuerzo: {config.ESFUERZO})")
    print(f"  Conocimiento: {', '.join(nombres_de_archivos(config.CARPETA_CONOCIMIENTO))}")
    print("=" * 60)
    print(AYUDA)
    print()

    while True:
        try:
            mensaje = input("Cliente > ").strip()
        except (EOFError, KeyboardInterrupt):  # Ctrl+C o Ctrl+Z
            print("\nHasta luego.")
            break

        if not mensaje:
            continue
        if mensaje == "/salir":
            print("Hasta luego.")
            break
        if mensaje == "/reiniciar":
            agente.reiniciar()
            print("— Conversación reiniciada (cliente nuevo) —\n")
            continue
        if mensaje == "/recargar":
            agente.recargar_conocimiento()
            print("— Conocimiento y reglas recargados —\n")
            continue
        if mensaje == "/captura":
            mensaje = MENSAJE_CAPTURA
            print(f"          {mensaje}")
        elif mensaje.startswith("/"):
            print(AYUDA + "\n")
            continue

        respuesta = agente.responder(mensaje)

        print(f"\nAsunvic > {respuesta.texto}")
        if respuesta.pasar_a_humano:
            print(f"   ⚠️  Se pasaría a humano: {respuesta.pasar_a_humano}")
        print()


if __name__ == "__main__":
    main()
