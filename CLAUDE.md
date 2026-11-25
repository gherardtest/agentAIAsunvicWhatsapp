# Asunvic Bot

Agente de IA para el WhatsApp Business de Asunvic (Lima, Perú).
Un solo número atiende tres servicios:
- Coro Asunvic: coro litúrgico católico para misas (tono formal y respetuoso) → conocimiento/coro.md
- Agrupación Musical Asunvic: música andina latinoamericana para eventos (tono festivo y seguro) → conocimiento/agrupacion.md (precios PENDIENTES)
- Casa Musical Asunvic: venta de quenas (tono amable y práctico) → conocimiento/instrumentos.md

## Stack
- Python 3.12, FastAPI, SDK de Anthropic (Claude)
- WhatsApp Cloud API de Meta (webhooks)
- Desarrollo en Windows; ngrok para exponer el webhook local

## Reglas
- Responder siempre en español, mensajes cortos (es WhatsApp)
- Nunca inventar precios, fechas disponibles ni kilómetros: usar solo /conocimiento
- Avisar que no hay cancelaciones ni devoluciones ANTES de que el cliente pague
- Nunca dar un pago por confirmado: las capturas de pago las verifica un humano
- Claves en .env, nunca en el código
- Estoy aprendiendo: explica brevemente lo que haces y por qué

## Ventas (coro y agrupación)
- El agente vende con los argumentos de venta de coro.md y agrupacion.md y cierra con una pregunta que avance la reserva
- Comparaciones (otros coros, organista, DJ, otros estilos) y preguntas de orientación (qué paquete conviene, si sirve para tal evento): las responde el agente, SIN escalar
- Nunca hablar mal de la competencia ni afirmar lo que no se puede comprobar ("somos los mejores")
- Misas de difuntos: empatía primero (pésame antes de cualquier dato) y nada de presión de venta

## Cuándo pasar a humano (coro y agrupación)
Solo para: cotizar algo sin precio en /conocimiento (movilidad, paquete personalizado, precio de la agrupación), confirmar fechas, pagos, descuentos o quejas.
Esta lista manda sobre las secciones "Pasar a humano cuando" de los archivos de /conocimiento.
Quenas: según instrumentos.md.

## QA
- Golden set: tests/golden_set.yaml (subir `version` al cambiar casos). Si cambia una política o un precio en /conocimiento, actualizar los casos afectados.
- Evals: `python -m evals.correr_evals` (release gate: casos críticos 3/3)
- Red teaming: redteam/promptfooconfig.yaml
