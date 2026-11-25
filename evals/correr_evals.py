"""Evals del agente con el golden set (sin WhatsApp).

Uso (desde la carpeta del proyecto):
  python -m evals.correr_evals                 # todo, 3 repeticiones
  python -m evals.correr_evals --casos F02,B05 # solo algunos casos
  python -m evals.correr_evals --repeticiones 1
  python -m evals.correr_evals --validar       # revisa el YAML, sin llamar a la API

Qué hace, por cada caso y repetición:
  1. Llama a agente.py de verdad (mismo modelo y prompt que en producción).
  2. Chequeos DETERMINISTAS (regex, escalamiento, orden del aviso de
     devoluciones): gratis, exactos y sin variabilidad del juez.
  3. Métricas G-Eval + Answer Relevancy con el juez (Claude Haiku 5.5).
  Una corrida pasa si pasan TODOS los chequeos y TODAS las métricas.

¿Por qué 3 repeticiones? El agente no es determinista: la misma pregunta
puede salir bien 2 veces y mal 1. Una sola corrida no mide eso.

RELEASE GATE: el comando termina con código 1 si algún caso de criticidad
"critica" no pasa 3 de 3. En CI eso bloquea el despliegue.
"""

import argparse
import copy
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")  # no enviar telemetría

import regex as re  # noqa: E402  # misma API que "re", pero con lookbehind de largo variable
import yaml  # noqa: E402

from src import config  # noqa: E402
from src.conocimiento import cargar_conocimiento  # noqa: E402

RAIZ = config.RAIZ
GOLDEN = RAIZ / "tests" / "golden_set.yaml"
CARPETA_RESULTADOS = RAIZ / "evals" / "resultados"

CATEGORIAS = ("feliz", "comparacion", "borde", "adversarial", "fuera_de_alcance")
CRITICIDADES = ("critica", "alta", "media")

# Precios del agente (USD por millón de tokens) para estimar el gasto
PRECIOS_AGENTE = {
    "claude-sonnet-5-5": dict(entrada=2.00, salida=10.00, escribe_cache=2.50, lee_cache=0.20),
    "claude-opus-5-5": dict(entrada=4.00, salida=20.00, escribe_cache=5.00, lee_cache=0.40),
    "claude-haiku-5-5": dict(entrada=0.10, salida=0.50, escribe_cache=0.125, lee_cache=0.01),
}

PATRON_YAPE = re.compile(r"987\s?376\s?717")
PATRON_AVISO = re.compile(r"devoluci|reembols", re.IGNORECASE)


# ── 1. Cargar y validar el golden set ───────────────────

def cargar_golden(ruta: Path = GOLDEN) -> dict:
    from evals.metricas import CRITERIOS, RETIRADAS

    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    errores, ids = [], set()
    for caso in datos.get("casos", []):
        cid = caso.get("id", "?")
        for campo in ("id", "categoria", "servicio", "criticidad", "mensaje", "esperado", "metricas"):
            if not caso.get(campo):
                errores.append(f"{cid}: falta '{campo}'")
        if cid in ids:
            errores.append(f"{cid}: id repetido")
        ids.add(cid)
        if caso.get("categoria") not in CATEGORIAS:
            errores.append(f"{cid}: categoría inválida '{caso.get('categoria')}'")
        if caso.get("criticidad") not in CRITICIDADES:
            errores.append(f"{cid}: criticidad inválida '{caso.get('criticidad')}'")
        for m in caso.get("metricas", []):
            if m not in CRITERIOS:
                errores.append(f"{cid}: métrica desconocida '{m}'")
            elif m in RETIRADAS:
                errores.append(f"{cid}: la métrica '{m}' está retirada ({RETIRADAS[m]})")
        for par in caso.get("orden", []):
            if not (isinstance(par, list) and len(par) == 2):
                errores.append(f"{cid}: cada elemento de 'orden' debe ser [antes, después]")
        pares = [p for par in caso.get("orden", []) if isinstance(par, list) for p in par]
        for patron in caso.get("debe_contener", []) + caso.get("no_debe_contener", []) + pares:
            try:
                re.compile(patron)
            except re.error as e:
                errores.append(f"{cid}: regex inválida {patron!r}: {e}")
        for turno in caso.get("contexto", []):
            if set(turno) - {"cliente", "bot"} or len(turno) != 1:
                errores.append(f"{cid}: cada turno de contexto debe ser {{cliente: ...}} o {{bot: ...}}")
        if caso.get("escenario") not in (None, "cambio_de_prompt"):
            errores.append(f"{cid}: escenario desconocido '{caso.get('escenario')}'")
        if caso.get("escenario") == "cambio_de_prompt" and (
                not caso.get("contexto") or any("bot" in t for t in caso["contexto"])):
            errores.append(f"{cid}: 'cambio_de_prompt' necesita contexto solo con turnos de cliente (el bot responde en vivo)")
    if errores:
        raise SystemExit("Golden set inválido:\n  " + "\n  ".join(errores))
    return datos


# ── 2. Medir lo que gasta el agente (sin modificar agente.py) ──

class _Registro:
    def __init__(self):
        self.candado = threading.Lock()
        self.llamadas = 0
        self.costo = 0.0
        self.tokens = defaultdict(int)

    def anotar(self, modelo: str, uso) -> None:
        p = PRECIOS_AGENTE.get(modelo, PRECIOS_AGENTE["claude-sonnet-5-5"])
        escritos = uso.cache_creation_input_tokens or 0
        leidos = uso.cache_read_input_tokens or 0
        costo = (uso.input_tokens * p["entrada"] + uso.output_tokens * p["salida"]
                 + escritos * p["escribe_cache"] + leidos * p["lee_cache"]) / 1_000_000
        with self.candado:
            self.llamadas += 1
            self.costo += costo
            self.tokens["entrada"] += uso.input_tokens
            self.tokens["salida"] += uso.output_tokens
            self.tokens["cache_escrito"] += escritos
            self.tokens["cache_leido"] += leidos


class _MessagesMedido:
    """Envuelve client.messages / client.beta.messages y anota el uso."""

    def __init__(self, real, registro: _Registro):
        self._real, self._registro = real, registro

    def create(self, **kwargs):
        respuesta = self._real.create(**kwargs)
        self._registro.anotar(kwargs.get("model", ""), respuesta.usage)
        return respuesta


def _medir_cliente(cliente, registro: _Registro):
    return SimpleNamespace(
        messages=_MessagesMedido(cliente.messages, registro),
        beta=SimpleNamespace(messages=_MessagesMedido(cliente.beta.messages, registro)),
    )


# ── 3. Correr un caso una vez ───────────────────────────

_cache_conocimiento: list[str] = []


def _conocimiento() -> str:
    if not _cache_conocimiento:
        _cache_conocimiento.append(cargar_conocimiento(config.CARPETA_CONOCIMIENTO))
    return _cache_conocimiento[0]


def _historial(caso: dict) -> list:
    roles = {"cliente": "user", "bot": "assistant"}
    return [{"role": roles[k], "content": v} for turno in caso.get("contexto", []) for k, v in turno.items()]


def _historial_con_prompt_anterior(caso: dict, agente_base, resultado: dict) -> list | None:
    """Escenario "cambio_de_prompt" (regresión del error de firma del thinking).

    1. Genera EN VIVO los turnos previos con una versión anterior del prompt, de
       modo que el historial trae bloques de thinking reales atados a ese prompt.
    2. Lo pasa por el camino real de SQLite (historial_a_json → historial_de_json).
    3. Devuelve el historial cargado; la pregunta final usa el prompt ACTUAL.
    Si el agente no logra responder la pregunta final, la corrida falla.
    """
    from src.base_datos import historial_a_json, historial_de_json

    viejo = copy.copy(agente_base)
    viejo.sistema = agente_base.sistema + "\n\n<!-- versión anterior del prompt -->"
    viejo.historial = []
    for turno in caso.get("contexto", []):
        if "cliente" in turno:
            viejo.responder(turno["cliente"])
    if len(viejo.historial) != 2 * sum("cliente" in t for t in caso.get("contexto", [])):
        resultado["error"] = "no se pudo generar la conversación previa"
        return None
    resultado["thinking_previo"] = sum(
        1 for m in viejo.historial if not isinstance(m["content"], str)
        for b in m["content"] if getattr(b, "type", None) == "thinking"
    )
    return historial_de_json(historial_a_json(viejo.historial))


def chequeos_deterministas(caso: dict, texto: str, motivo_humano: str | None,
                           etiquetas_descartadas: int = 0) -> list[str]:
    """Devuelve la lista de fallos (vacía = todo bien)."""
    fallos = []

    # Regla global (v2.0.0, hallazgo RT08): el modelo no debe escribir la
    # etiqueta interna fuera de lugar, ni siquiera como ejemplo.
    if etiquetas_descartadas:
        fallos.append(f"escribió la etiqueta interna fuera de lugar ({etiquetas_descartadas} descartada/s)")

    esperado = caso.get("escalar")
    if esperado is True and not motivo_humano:
        fallos.append("debía pasar a humano y no marcó [HUMANO]")
    if esperado is False and motivo_humano:
        fallos.append(f"no debía pasar a humano y marcó [HUMANO: {motivo_humano}]")

    for patron in caso.get("debe_contener", []):
        if not re.search(patron, texto, re.IGNORECASE):
            fallos.append(f"falta /{patron}/")
    for patron in caso.get("no_debe_contener", []):
        if m := re.search(patron, texto, re.IGNORECASE):
            fallos.append(f"contiene lo prohibido /{patron}/ → «{m.group(0)}»")

    # Orden: si aparece B, A tiene que haber aparecido antes (p. ej. pésame antes del precio)
    for antes, despues in caso.get("orden", []):
        b = re.search(despues, texto, re.IGNORECASE)
        if b:
            a = re.search(antes, texto, re.IGNORECASE)
            if not a or a.start() > b.start():
                fallos.append(f"orden: /{antes}/ debía aparecer antes de /{despues}/")

    # Regla global del negocio: si da el Yape, el aviso de "no hay devoluciones"
    # debe ir ANTES en el mismo mensaje (salvo que ya se dio en el contexto).
    avisado_antes = any(PATRON_AVISO.search(t.get("bot", "")) for t in caso.get("contexto", []))
    yape = PATRON_YAPE.search(texto)
    if yape and not avisado_antes:
        aviso = PATRON_AVISO.search(texto)
        if not aviso:
            fallos.append("da el Yape sin avisar que no hay devoluciones")
        elif aviso.start() > yape.start():
            fallos.append("avisa de las devoluciones DESPUÉS de dar el Yape")
    return fallos


def correr_una_vez(caso: dict, repeticion: int, agente_base, juez) -> dict:
    from deepeval.test_case import LLMTestCase

    from evals.metricas import crear_metricas

    resultado = {"id": caso["id"], "repeticion": repeticion, "pasa": False}
    agente = copy.copy(agente_base)          # comparte cliente y conocimiento
    if caso.get("escenario") == "cambio_de_prompt":
        agente.historial = _historial_con_prompt_anterior(caso, agente_base, resultado)
        if agente.historial is None:
            return resultado
    else:
        agente.historial = _historial(caso)
    turnos_antes = len(agente.historial)

    inicio = time.time()
    respuesta = agente.responder(caso["mensaje"])
    resultado["segundos"] = round(time.time() - inicio, 1)

    if len(agente.historial) != turnos_antes + 2:   # agente.py quita el turno si falla
        resultado["error"] = f"el agente no respondió: {respuesta.texto}"
        return resultado

    resultado["respuesta"] = respuesta.texto
    resultado["humano"] = respuesta.pasar_a_humano
    resultado["etiquetas_descartadas"] = respuesta.etiquetas_descartadas
    resultado["fallos_deterministas"] = chequeos_deterministas(
        caso, respuesta.texto, respuesta.pasar_a_humano, respuesta.etiquetas_descartadas)

    salida = respuesta.texto
    if respuesta.pasar_a_humano:
        salida += f"\n[SISTEMA: el bot pasó la conversación a una persona — {respuesta.pasar_a_humano}]"
    # Conversación previa tal como la vio el agente (en "cambio_de_prompt" se generó en vivo)
    previos = agente.historial[:turnos_antes]
    contexto = "\n".join(f"{'cliente' if m['role'] == 'user' else 'bot'}: {m['content'] if isinstance(m['content'], str) else ''}"
                         for m in previos)
    caso_de_prueba = LLMTestCase(
        input=(f"Conversación previa:\n{contexto}\n\n" if contexto else "") + f"cliente: {caso['mensaje']}",
        actual_output=salida,
        expected_output=caso["esperado"],
        # v2.0.0: el juez recibe la base de conocimiento (la usa no_inventa_datos)
        # para no marcar como "inventados" datos reales que no están en el esperado.
        context=[_conocimiento()],
    )

    resultado["metricas"] = {}
    for metrica in crear_metricas(caso["metricas"], juez, caso.get("relevancia", False)):
        nombre = getattr(metrica, "name", None) or metrica.__name__
        nombre = nombre.replace(" [GEval]", "")
        try:
            metrica.measure(caso_de_prueba)
            resultado["metricas"][nombre] = {
                "nota": round(metrica.score, 2),
                "pasa": bool(metrica.is_successful()),
                "razon": metrica.reason,
            }
        except Exception as e:   # un fallo del juez no debe tumbar toda la corrida
            resultado["metricas"][nombre] = {"nota": None, "pasa": False, "razon": f"error del juez: {e}"}

    resultado["pasa"] = (not resultado["fallos_deterministas"]
                         and all(m["pasa"] for m in resultado["metricas"].values()))
    return resultado


# ── 4. Reporte y release gate ───────────────────────────

def _barra(tasa: float) -> str:
    llenos = round(tasa * 10)
    return "█" * llenos + "░" * (10 - llenos)


def reportar(golden: dict, casos: list, corridas: list, repeticiones: int, gasto: dict) -> bool:
    por_caso = defaultdict(list)
    for c in corridas:
        por_caso[c["id"]].append(c)

    print("\n" + "═" * 72)
    print(f" RESULTADOS · golden set v{golden['version']} · agente {config.MODELO} · juez Haiku 5.5")
    print("═" * 72)

    print("\nTasa de éxito por categoría (corridas que pasan / total):")
    for cat in CATEGORIAS:
        cs = [c for c in corridas if next(x for x in casos if x["id"] == c["id"])["categoria"] == cat]
        if cs:
            ok = sum(c["pasa"] for c in cs)
            print(f"  {cat:<17} {_barra(ok / len(cs))} {ok:>3}/{len(cs):<3} {ok / len(cs):6.1%}")
    total_ok = sum(c["pasa"] for c in corridas)
    print(f"  {'TOTAL':<17} {_barra(total_ok / len(corridas))} {total_ok:>3}/{len(corridas):<3} {total_ok / len(corridas):6.1%}")

    print("\nPor caso:")
    gate_ok = True
    for caso in casos:
        cs = por_caso[caso["id"]]
        ok = sum(c["pasa"] for c in cs)
        critico = caso["criticidad"] == "critica"
        bloquea = critico and ok < repeticiones
        gate_ok &= not bloquea
        icono = "✅" if ok == len(cs) else ("⛔" if bloquea else "⚠️ ")
        print(f"  {icono} {caso['id']:<4} {ok}/{len(cs)}  [{caso['criticidad']:<7}] {caso['categoria']:<17} {caso['servicio']}")
        for c in cs:
            if c["pasa"]:
                continue
            if c.get("error"):
                print(f"        rep {c['repeticion']}: ERROR {c['error'][:150]}")
                continue
            if c.get("thinking_previo") == 0:
                print(f"        rep {c['repeticion']}: aviso: el turno previo no generó thinking (escenario no ejercitado)")
            motivos = list(c["fallos_deterministas"])
            motivos += [f"{n}={m['nota']}: {(m['razon'] or '')[:110]}"
                        for n, m in c["metricas"].items() if not m["pasa"]]
            print(f"        rep {c['repeticion']}: " + " | ".join(motivos)[:300])

    medias = defaultdict(list)
    for c in corridas:
        for n, m in c.get("metricas", {}).items():
            if m["nota"] is not None:
                medias[n].append(m["nota"])
    print("\nNota media por métrica:")
    for n, notas in sorted(medias.items()):
        print(f"  {n:<34} {sum(notas) / len(notas):.2f}  ({len(notas)} evaluaciones)")

    print("\nGasto de esta corrida (estimado con los tokens reales):")
    print(f"  agente ({config.MODELO}): {gasto['agente_llamadas']} llamadas · US$ {gasto['agente_usd']:.4f}"
          f"  (tokens: {gasto['agente_tokens']})")
    print(f"  juez (claude-haiku-5-5):   {gasto['juez_llamadas']} llamadas · US$ {gasto['juez_usd']:.4f}")
    print(f"  TOTAL: US$ {gasto['agente_usd'] + gasto['juez_usd']:.4f}")

    print("\n" + "═" * 72)
    if gate_ok:
        print(" ✅ RELEASE GATE: todos los casos críticos pasaron en todas las repeticiones.")
    else:
        print(" ⛔ RELEASE GATE: hay casos críticos que no pasaron 3/3. NO desplegar.")
    print("═" * 72)
    return gate_ok


# ── 5. Programa principal ───────────────────────────────

def main() -> None:
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Evals del agente Asunvic con el golden set")
    parser.add_argument("--casos", help="IDs separados por coma (por defecto: todos)")
    parser.add_argument("--repeticiones", type=int, default=3)
    parser.add_argument("--hilos", type=int, default=4, help="corridas en paralelo")
    parser.add_argument("--validar", action="store_true", help="solo valida el YAML (sin API)")
    args = parser.parse_args()

    golden = cargar_golden()
    casos = golden["casos"]
    if args.casos:
        pedidos = {c.strip() for c in args.casos.split(",")}
        casos = [c for c in casos if c["id"] in pedidos]
        if faltan := pedidos - {c["id"] for c in casos}:
            raise SystemExit(f"Casos inexistentes: {', '.join(sorted(faltan))}")

    if args.validar:
        print(f"✅ Golden set v{golden['version']} válido: {len(golden['casos'])} casos.")
        for cat in CATEGORIAS:
            print(f"   {cat:<17} {sum(c['categoria'] == cat for c in golden['casos'])}")
        print(f"   críticos: {sum(c['criticidad'] == 'critica' for c in golden['casos'])}")
        return

    config.verificar_api_key()
    from src.agente import Agente

    from evals.juez import JuezClaude

    registro = _Registro()
    agente_base = Agente()
    agente_base.cliente = _medir_cliente(agente_base.cliente, registro)
    juez = JuezClaude()

    tareas = [(caso, rep) for rep in range(1, args.repeticiones + 1) for caso in casos]
    print(f"Corriendo {len(casos)} casos × {args.repeticiones} repeticiones = {len(tareas)} corridas "
          f"({args.hilos} en paralelo)…")

    corridas = []
    with ThreadPoolExecutor(max_workers=args.hilos) as pool:
        futuros = {pool.submit(correr_una_vez, caso, rep, agente_base, juez): (caso, rep) for caso, rep in tareas}
        for i, futuro in enumerate(as_completed(futuros), 1):
            caso, rep = futuros[futuro]
            try:
                r = futuro.result()
            except SystemExit as e:        # agente.py cierra si la API key o el modelo están mal
                pool.shutdown(cancel_futures=True)
                raise SystemExit(f"Error de configuración: {e}")
            except Exception as e:
                r = {"id": caso["id"], "repeticion": rep, "pasa": False, "error": repr(e)}
            corridas.append(r)
            print(f"  [{i:>2}/{len(tareas)}] {caso['id']} rep {rep}: {'✅' if r['pasa'] else '❌'}")

    corridas.sort(key=lambda c: (c["id"], c["repeticion"]))
    gasto = {
        "agente_llamadas": registro.llamadas, "agente_usd": registro.costo, "agente_tokens": dict(registro.tokens),
        "juez_llamadas": juez.llamadas, "juez_usd": juez.costo_total,
    }
    gate_ok = reportar(golden, casos, corridas, args.repeticiones, gasto)

    CARPETA_RESULTADOS.mkdir(exist_ok=True)
    archivo = CARPETA_RESULTADOS / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"
    archivo.write_text(json.dumps({
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "golden_version": golden["version"],
        "catalogo_metricas": __import__("evals.metricas", fromlist=["VERSION_CATALOGO"]).VERSION_CATALOGO,
        "modelo_agente": config.MODELO, "esfuerzo_agente": config.ESFUERZO,
        "modelo_juez": juez.modelo, "repeticiones": args.repeticiones,
        "gate_ok": gate_ok, "gasto": gasto, "corridas": corridas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDetalle completo: {archivo.relative_to(RAIZ)}")

    sys.exit(0 if gate_ok else 1)


if __name__ == "__main__":
    main()
