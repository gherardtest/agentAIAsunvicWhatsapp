"""El "juez": el modelo que califica las respuestas del agente.

Usamos Claude Haiku 5.5, el modelo más barato de Anthropic ($0.10 / $0.50
por millón de tokens de entrada / salida, unas 20 veces menos que Sonnet).
Calificar contra criterios escritos es mucho más fácil que atender a un
cliente, así que un modelo pequeño basta.

No usamos el AnthropicModel que trae DeepEval: envía parámetros de muestreo
(temperature) que Haiku 5.5 rechaza, y no nos dejaría bajar el esfuerzo.
Implementamos la interfaz DeepEvalBaseLLM con el SDK oficial.

También medimos el gasto: cada llamada devuelve (resultado, costo_usd) y
DeepEval lo suma en metric.evaluation_cost.
"""

import asyncio
import threading

import anthropic
from deepeval.models import DeepEvalBaseLLM

MODELO_JUEZ = "claude-haiku-5-5"

# Precios por millón de tokens (USD), prompts de hasta 100K tokens
PRECIO_ENTRADA = 0.10
PRECIO_SALIDA = 0.50

INSTRUCCION_JSON = "\n\nResponde SOLO con un objeto JSON válido, sin texto adicional."


class JuezClaude(DeepEvalBaseLLM):
    def __init__(self, modelo: str = MODELO_JUEZ, esfuerzo: str = "low"):
        self.modelo = modelo
        self.esfuerzo = esfuerzo    # low: califica rápido y barato
        self.llamadas = 0
        self.costo_total = 0.0
        self._candado = threading.Lock()
        super().__init__(modelo)

    def load_model(self):
        # max_retries: si hay un 429 (límite de uso), el SDK espera y reintenta
        return anthropic.Anthropic(max_retries=5)

    def get_model_name(self) -> str:
        return self.modelo

    def _costo(self, uso) -> float:
        costo = (uso.input_tokens * PRECIO_ENTRADA + uso.output_tokens * PRECIO_SALIDA) / 1_000_000
        with self._candado:
            self.llamadas += 1
            self.costo_total += costo
        return costo

    def generate(self, prompt: str, schema=None):
        """DeepEval pasa `schema` (un modelo Pydantic) cuando espera JSON.

        Con schema usamos salida estructurada (messages.parse): la API
        garantiza JSON válido con esa forma, sin tener que "limpiar" texto.
        """
        comunes = dict(
            model=self.modelo,
            max_tokens=4000,
            output_config={"effort": self.esfuerzo},
            messages=[{"role": "user", "content": prompt}],
        )
        if schema is not None:
            try:
                r = self.model.messages.parse(output_format=schema, **comunes)
                self._revisar(r)
                return r.parsed_output, self._costo(r.usage)
            except anthropic.BadRequestError:
                # Algún esquema de DeepEval podría no ser compatible con salida
                # estructurada: entonces pedimos JSON en texto y DeepEval lo lee.
                comunes["messages"] = [{"role": "user", "content": prompt + INSTRUCCION_JSON}]

        r = self.model.messages.create(**comunes)
        self._revisar(r)
        texto = "".join(b.text for b in r.content if b.type == "text")
        return texto, self._costo(r.usage)

    async def a_generate(self, prompt: str, schema=None):
        return await asyncio.to_thread(self.generate, prompt, schema)

    @staticmethod
    def _revisar(respuesta) -> None:
        if respuesta.stop_reason == "refusal":
            raise RuntimeError("El juez rechazó calificar este caso (stop_reason=refusal)")
        if respuesta.stop_reason == "max_tokens":
            raise RuntimeError("El juez se quedó sin tokens (max_tokens)")
