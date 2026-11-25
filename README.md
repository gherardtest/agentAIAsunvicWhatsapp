# Asunvic Bot

Agente de IA para el WhatsApp Business de Asunvic (coro, quenas y agrupación musical).
Ver `CLAUDE.md` para las reglas del proyecto.

## Fase 2: chat de prueba en la consola

### Instalación (una sola vez)

En PowerShell, desde esta carpeta:

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Abre `.env` y pega tu API key en `ANTHROPIC_API_KEY`.

### Uso

```
.venv\Scripts\activate
python -m src.consola
```

Escribe como si fueras un cliente. Comandos: `/captura`, `/reiniciar`, `/recargar`, `/salir`.

## Fase 3: WhatsApp (servidor)

Completa las variables de WhatsApp en `.env` (ver `.env.example`) y arranca:

```
.venv\Scripts\python -m uvicorn src.servidor:app --port 8000 --reload
```

En otra ventana, exponlo a internet con `ngrok http 8000` y pon en Meta la URL
`https://<tu-dominio-ngrok>/webhook` con tu `WHATSAPP_VERIFY_TOKEN`.

Probar sin WhatsApp (con el servidor arrancado):

```
.venv\Scripts\python scripts\simular_webhook.py --verificar
.venv\Scripts\python scripts\simular_webhook.py 51911111111 "Hola, quiero una quena"
```

Comandos desde tu número (`NUMERO_ADMIN`): `/pausados`, `/ver`, `/responder`,
`/reactivar`, `/pausar`, `/cliente`, `/admin`, `/ayuda`.

| Archivo             | Qué hace                                              |
|---------------------|-------------------------------------------------------|
| `src/servidor.py`   | rutas web: `/webhook` (Meta) y `/salud`               |
| `src/atencion.py`   | decide qué hacer con cada mensaje                     |
| `src/whatsapp.py`   | enviar y leer mensajes de Meta, verificar la firma    |
| `src/base_datos.py` | historial y chats pausados en SQLite                  |

## QA: evals y red teaming

Dependencias de desarrollo (no se instalan en producción):

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

**Evals con el golden set** (`tests/golden_set.yaml`, DeepEval, juez Claude Haiku 5.5):

```
.venv\Scripts\python -m evals.correr_evals --validar          # revisa el YAML, gratis
.venv\Scripts\python -m evals.correr_evals --casos F02 --repeticiones 1   # prueba barata
.venv\Scripts\python -m evals.correr_evals                    # todo × 3 (release gate)
```

Termina con código 1 si algún caso `critica` no pasa 3/3. El detalle queda en
`evals/resultados/`.

**Red teaming** (Promptfoo, 12 ataques con criterio de éxito definido):

```
$env:PROMPTFOO_PYTHON = ".venv\Scripts\python.exe"
npx -y promptfoo@0.120.19 eval -c redteam/promptfooconfig.yaml --no-cache
npx -y promptfoo@0.120.19 view
```

Sin gastar (respuestas falsas, para probar la configuración): `$env:ASUNVIC_FALSO = "1"`.

## Dónde cambiar cada cosa

| Quiero cambiar…                   | Edita                    |
|-----------------------------------|--------------------------|
| Precios, paquetes, datos de pago  | `conocimiento/*.md`      |
| Tono, reglas, cuándo pasar a humano | `prompts/sistema.md`   |
| Modelo o API key                  | `.env`                   |
