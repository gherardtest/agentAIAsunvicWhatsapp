"""Servidor web (FastAPI) que recibe los avisos de WhatsApp.

Rutas:
  GET  /salud              para comprobar que el servidor está vivo
  GET  /privacidad         política de privacidad (Meta la pide)
  GET  /eliminacion-datos  cómo pedir que borremos tus datos (Meta la pide)
  GET  /webhook  Meta comprueba que el webhook es tuyo (una sola vez)
  POST /webhook  Meta avisa de cada mensaje nuevo

Arrancar en tu PC:   uvicorn src.servidor:app --port 8000 --reload
"""

import json
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from src import atencion, paginas
from src import base_datos as bd
from src import config
from src import whatsapp as wa

# Tildes y emojis en los logs de la terminal de Windows
for flujo in (sys.stdout, sys.stderr):
    flujo.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx2").setLevel(logging.WARNING)  # menos ruido
log = logging.getLogger("asunvic.servidor")


@asynccontextmanager
async def al_arrancar(app: FastAPI):
    """Se ejecuta una vez al iniciar: si falta algo, mejor saberlo ya."""
    config.verificar_api_key()
    faltantes = config.variables_whatsapp_faltantes()
    if faltantes:
        raise SystemExit(f"Faltan variables en .env: {', '.join(faltantes)}")
    bd.iniciar()
    log.info("Servidor listo. Modelo: %s · Base de datos: %s", config.MODELO, config.DB_PATH)
    yield


app = FastAPI(title="Asunvic Bot", lifespan=al_arrancar)


@app.get("/salud")
def salud():
    return {"estado": "ok"}


# Páginas públicas que Meta pide para publicar la app
@app.get("/privacidad", response_class=HTMLResponse)
def pagina_privacidad():
    return paginas.privacidad()


@app.get("/eliminacion-datos", response_class=HTMLResponse)
def pagina_eliminacion_datos():
    return paginas.eliminacion_datos()


@app.get("/webhook")
def verificar_webhook(
    modo: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    desafio: str = Query("", alias="hub.challenge"),
):
    """Meta llama aquí cuando guardas la URL del webhook en su panel.

    Si el token coincide con WHATSAPP_VERIFY_TOKEN, devolvemos el número
    "desafío" tal cual: así Meta sabe que el servidor es tuyo.
    """
    if modo == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        log.info("Webhook verificado por Meta")
        return PlainTextResponse(desafio)
    log.warning("Intento de verificación con token incorrecto")
    return Response(status_code=403)


@app.post("/webhook")
async def recibir_webhook(request: Request, tareas: BackgroundTasks):
    cuerpo = await request.body()

    if not wa.verificar_firma(cuerpo, request.headers.get("X-Hub-Signature-256")):
        log.warning("Payload descartado: firma inválida (no viene de Meta o el App Secret no coincide)")
        return Response(status_code=401)

    try:
        payload = json.loads(cuerpo)
    except ValueError:
        log.info("Payload descartado: el cuerpo no es JSON válido")
        return Response(status_code=400)

    # Respondemos 200 a Meta YA y procesamos después. Claude tarda unos
    # segundos; si Meta no recibe respuesta rápido, reenvía el aviso.
    tareas.add_task(atencion.procesar_payload, payload)
    return {"ok": True}
