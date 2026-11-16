# agente/prompt.py · Arma el prompt de sistema con el contexto de cada solicitud
from datetime import date
from pathlib import Path

PLANTILLA = (Path(__file__).parent / "prompts" / "sistema.md").read_text(encoding="utf-8")

class _Seguro(dict):
    def __missing__(self, clave):
        return "{" + clave + "}"

def armar_prompt(contexto, indice_skills: str, preferencias: list[str]) -> str:
    """Rellena la plantilla del sistema con los metadatos protegidos de la sesión."""
    return PLANTILLA.format_map(_Seguro(
        usuario=contexto.usuario,
        rol=contexto.rol,
        paciente=contexto.paciente_alias or "ninguno",
        fecha=date.today().isoformat(),
        skills=indice_skills or "(ninguna)",
        preferencias="\n".join(f"- {p}" for p in preferencias) or "(sin preferencias guardadas)",
    ))
