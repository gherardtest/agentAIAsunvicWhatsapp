"""Configuración del proyecto: lee el archivo .env y define rutas.

Todo lo que puede cambiar (clave, modelo, esfuerzo) vive en .env,
así no hay que tocar el código para cambiarlo.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Carpeta raíz del proyecto (la que contiene src/, conocimiento/, prompts/)
RAIZ = Path(__file__).resolve().parent.parent

# Carga las variables del archivo .env como si fueran variables de entorno
load_dotenv(RAIZ / ".env")

CARPETA_CONOCIMIENTO = RAIZ / "conocimiento"
ARCHIVO_PROMPT_SISTEMA = RAIZ / "prompts" / "sistema.md"

MODELO = os.getenv("MODELO", "claude-sonnet-5-5")
ESFUERZO = os.getenv("ESFUERZO", "low")

# Respuestas de WhatsApp son cortas; este límite incluye lo que el modelo "piensa".
MAX_TOKENS = 4000


# ── WhatsApp Cloud API (fase 3) ─────────────────────────
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v26.0")

# Tu número personal: sus mensajes son comandos, no conversaciones de cliente
NUMERO_ADMIN = os.getenv("NUMERO_ADMIN", "").lstrip("+").replace(" ", "")

# ── Base de datos ───────────────────────────────────────
# Una ruta relativa (asunvic.db) se guarda en la carpeta del proyecto;
# en Railway se usa una absoluta dentro del Volume (/data/asunvic.db).
_ruta_db = Path(os.getenv("DB_PATH", "asunvic.db"))
DB_PATH = _ruta_db if _ruta_db.is_absolute() else RAIZ / _ruta_db
DIAS_REINICIO_HISTORIAL = int(os.getenv("DIAS_REINICIO_HISTORIAL", "7"))
# Días que se guarda una conversación archivada antes de borrarla para siempre
DIAS_CONSERVAR_ARCHIVO = int(os.getenv("DIAS_CONSERVAR_ARCHIVO", "90"))

# Nombre que aparece en las páginas de privacidad
NOMBRE_NEGOCIO = "Asunvic"


def variables_whatsapp_faltantes() -> list[str]:
    """Devuelve los nombres de las variables de WhatsApp que faltan en .env."""
    requeridas = {
        "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
        "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
        "WHATSAPP_VERIFY_TOKEN": WHATSAPP_VERIFY_TOKEN,
        "WHATSAPP_APP_SECRET": WHATSAPP_APP_SECRET,
        "NUMERO_ADMIN": NUMERO_ADMIN,
    }
    return [
        nombre for nombre, valor in requeridas.items()
        if not valor or valor.startswith(("pon-", "inventa-", "000", "51XXX"))
    ]


def verificar_api_key() -> None:
    """Detiene el programa con un mensaje claro si falta la clave."""
    clave = os.getenv("ANTHROPIC_API_KEY", "")
    if not clave or "pon-tu-clave" in clave:
        raise SystemExit(
            "Falta ANTHROPIC_API_KEY.\n"
            "Copia .env.example como .env y pega tu clave real dentro."
        )
