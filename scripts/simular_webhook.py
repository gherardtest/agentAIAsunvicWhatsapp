"""Manda un mensaje FALSO al servidor local, como si viniera de Meta.

Sirve para probar el servidor sin WhatsApp. El agente sí responde (usa
Claude de verdad), pero el envío de la respuesta a ese número fallará si
no es un número real autorizado: es normal, mira la terminal del servidor.

Uso (con el servidor arrancado):
  python scripts/simular_webhook.py 51911111111 "Hola, quiero una quena"
  python scripts/simular_webhook.py --verificar
"""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx2  # noqa: E402

from src import config  # noqa: E402

URL = "http://127.0.0.1:8000/webhook"


def verificar() -> None:
    r = httpx2.get(URL, params={
        "hub.mode": "subscribe",
        "hub.verify_token": config.WHATSAPP_VERIFY_TOKEN,
        "hub.challenge": "12345",
    })
    print(f"Verificación: HTTP {r.status_code} → {r.text!r} (debe ser '12345')")


def enviar(telefono: str, texto: str) -> None:
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{
        "field": "messages",
        "value": {
            "contacts": [{"wa_id": telefono, "profile": {"name": "Prueba"}}],
            "messages": [{
                "from": telefono,
                "id": f"wamid.simulado.{time.time_ns()}",
                "type": "text",
                "text": {"body": texto},
            }],
        },
    }]}]}
    cuerpo = json.dumps(payload).encode()
    firma = hmac.new(config.WHATSAPP_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    r = httpx2.post(URL, content=cuerpo, headers={
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={firma}",
    })
    print(f"HTTP {r.status_code}: {r.text}  → mira la terminal del servidor")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--verificar":
        verificar()
    elif len(sys.argv) == 3:
        enviar(sys.argv[1], sys.argv[2])
    else:
        print(__doc__)
