"""Lee los archivos de /conocimiento y los une en un solo texto.

Cada archivo va envuelto en etiquetas <archivo nombre="..."> para que el
modelo sepa de dónde viene cada dato (coro, instrumentos, etc.).
Si agregas un archivo nuevo (por ejemplo agrupacion.md), se usa solo.
"""

from pathlib import Path


def cargar_conocimiento(carpeta: Path) -> str:
    archivos = sorted(carpeta.glob("*.md"))  # sorted: siempre el mismo orden
    if not archivos:
        raise SystemExit(f"No hay archivos .md en {carpeta}")

    partes = []
    for archivo in archivos:
        contenido = archivo.read_text(encoding="utf-8").strip()
        partes.append(f'<archivo nombre="{archivo.name}">\n{contenido}\n</archivo>')
    return "\n\n".join(partes)


def nombres_de_archivos(carpeta: Path) -> list[str]:
    return [a.name for a in sorted(carpeta.glob("*.md"))]
