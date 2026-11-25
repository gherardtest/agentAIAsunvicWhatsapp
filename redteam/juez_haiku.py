"""Proveedor de Promptfoo para CALIFICAR (llm-rubric) con Claude Haiku 5.5.

Promptfoo le pasa al juez un prompt que pide un JSON con
{"reason": ..., "pass": ..., "score": ...}. Usamos salida estructurada para
que el JSON siempre sea válido.

No usamos el proveedor "anthropic:" que trae Promptfoo porque puede enviar
temperature, y Haiku 5.5 rechaza parámetros de muestreo distintos de los
predeterminados.
"""

import json
import os

PRECIO_ENTRADA, PRECIO_SALIDA = 0.10, 0.50   # USD por millón de tokens
_cliente = None


def _a_mensajes(prompt: str):
    """El prompt de llm-rubric suele llegar como una lista de mensajes en JSON."""
    try:
        datos = json.loads(prompt)
    except (TypeError, ValueError):
        return None, [{"role": "user", "content": prompt}]
    if not isinstance(datos, list):
        return None, [{"role": "user", "content": prompt}]
    sistema = "\n\n".join(m["content"] for m in datos if m.get("role") == "system")
    mensajes = [{"role": m["role"], "content": m["content"]} for m in datos if m.get("role") in ("user", "assistant")]
    return sistema or None, mensajes or [{"role": "user", "content": prompt}]


def call_api(prompt, options, context):
    if os.getenv("ASUNVIC_FALSO") == "1":
        return {"output": json.dumps({"reason": "modo falso", "pass": True, "score": 1.0})}

    global _cliente
    import anthropic
    from pydantic import BaseModel

    class Veredicto(BaseModel):
        reason: str
        score: float
        # "pass" es palabra reservada en Python: se usa un alias
        aprobado: bool

    if _cliente is None:
        _cliente = anthropic.Anthropic(max_retries=5)

    sistema, mensajes = _a_mensajes(prompt)
    instruccion = ("\n\nDevuelve: reason (en español, breve), score (0 a 1) y aprobado "
                   "(true si la respuesta CUMPLE la rúbrica).")
    mensajes[-1] = {**mensajes[-1], "content": mensajes[-1]["content"] + instruccion}
    extra = {"system": sistema} if sistema else {}
    r = _cliente.messages.parse(
        model="claude-haiku-5-5",
        max_tokens=2000,
        output_config={"effort": "low"},
        output_format=Veredicto,
        messages=mensajes,
        **extra,
    )
    if r.stop_reason == "refusal":
        return {"error": "El juez rechazó calificar (refusal)"}
    v = r.parsed_output
    costo = (r.usage.input_tokens * PRECIO_ENTRADA + r.usage.output_tokens * PRECIO_SALIDA) / 1_000_000
    return {
        "output": json.dumps({"reason": v.reason, "pass": v.aprobado, "score": v.score}, ensure_ascii=False),
        "tokenUsage": {"prompt": r.usage.input_tokens, "completion": r.usage.output_tokens,
                       "total": r.usage.input_tokens + r.usage.output_tokens},
        "cost": costo,
    }
