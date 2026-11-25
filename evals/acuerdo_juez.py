"""Acuerdo del juez LLM con una revisión humana (meta-evaluación del juez).

Uso:  python -m evals.acuerdo_juez tests/revision_humana_corrida1.yaml

El archivo lista juicios del juez con la etiqueta humana (falla/pasa). Como
el universo son juicios REPROBADOS por el juez, el % de acuerdo equivale a
la precisión de sus rechazos: de cada 100 veces que el juez dice "falla",
¿cuántas tiene razón?
"""

import sys
from collections import defaultdict
from pathlib import Path

import yaml


def main() -> None:
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8")
    ruta = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/revision_humana_corrida1.yaml")
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    revs = datos["revisiones"]

    def acuerdo(items):
        return sum(r["humano"] == "falla" for r in items), len(items)

    ok, n = acuerdo(revs)
    print(f"Juez {datos['juez']} · corrida {datos['corrida']} · {n} rechazos revisados\n")
    print(f"ACUERDO GLOBAL (precisión de los rechazos): {ok}/{n} = {ok / n:.1%}")
    sin_dudosos = [r for r in revs if not r.get("dudoso")]
    ok2, n2 = acuerdo(sin_dudosos)
    print(f"Sin los {n - n2} dudosos:                     {ok2}/{n2} = {ok2 / n2:.1%}\n")

    por_metrica = defaultdict(list)
    for r in revs:
        por_metrica[r["metrica"]].append(r)
    print(f"{'Métrica':<34} {'acuerdo':>9}   falsos positivos del juez")
    for m, items in sorted(por_metrica.items(), key=lambda kv: acuerdo(kv[1])[0] / len(kv[1])):
        a, t = acuerdo(items)
        print(f"{m:<34} {a:>3}/{t:<3} {a / t:6.0%}   {t - a}")


if __name__ == "__main__":
    main()
