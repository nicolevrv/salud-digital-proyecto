# agente/skills.py · Índice y carga bajo demanda de skills (divulgación progresiva)
from pathlib import Path

CARPETA = Path(__file__).parent / "skills"

def _leer(archivo: Path) -> tuple[dict, str]:
    texto = archivo.read_text(encoding="utf-8")
    _, cabecera, cuerpo = texto.split("---", 2)
    meta = dict(linea.split(":", 1) for linea in cabecera.strip().splitlines())
    return {k.strip(): v.strip() for k, v in meta.items()}, cuerpo.strip()

def catalogo() -> dict:
    """nombre -> ruta del SKILL.md"""
    return {_leer(a)[0]["name"]: a for a in sorted(CARPETA.glob("*/SKILL.md"))}

def indice() -> str:
    """Una línea por skill: esto es lo único que va en el prompt de sistema."""
    return "\n".join(f"- {n}: {_leer(a)[0]['description']}" for n, a in catalogo().items())

def cargar(nombre: str) -> str:
    """Carga el cuerpo del procedimiento validando contra el catálogo (evita path traversal)."""
    rutas = catalogo()
    if nombre not in rutas:
        return f"SKILL_NO_EXISTE: usa una de {list(rutas)}"
    return _leer(rutas[nombre])[1]
