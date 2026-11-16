# agente/rag_conector.py · Une el RAG de Metrología UCI y Guías Clínicas con el agente
import os, sys, re, json
from pathlib import Path
from rank_bm25 import BM25Okapi

BASE_DIR = Path(__file__).resolve().parent.parent

def _tokens(texto: str) -> list[str]:
    return re.findall(r"\w+", texto.lower())

# Fragmentos híbridos: Guías Clínicas de Diabetes + Protocolos de Metrología UCI (Proyecto Biomédico)
FRAGMENTOS_BASE = [
    # Clínicos (Semana 13)
    {
        "chunk_id": "GC-01", "fuente": "Guía de Práctica Clínica · Diagnóstico y Metas Glucémicas",
        "texto": "Diagnóstico de diabetes: HbA1c igual o mayor a 6,5 %, glucosa en ayunas igual o mayor a 126 mg/dL. La meta general de HbA1c en adultos es menor a 7,0 %, individualizada según comorbilidades."
    },
    {
        "chunk_id": "GC-02", "fuente": "Guía de Práctica Clínica · Retinopatía Diabética",
        "texto": "Tamizaje de retinopatía diabética con retinografía o fondo de ojo al diagnóstico de diabetes tipo 2 y luego cada año. Remitir a oftalmología toda retinopatía no proliferativa moderada (grado 2) o peor, y con urgencia la proliferativa."
    },
    {
        "chunk_id": "GC-03", "fuente": "Guía de Práctica Clínica · Hipoglucemia y Emergencias",
        "texto": "Hipoglucemia: glucosa menor a 70 mg/dL. Aplicar regla 15-15: 15 g de carbohidrato simple y control a los 15 min. Si el paciente está inconsciente, administrar glucagón y traslado prioritario."
    },
    {
        "chunk_id": "GC-04", "fuente": "Guía OMS · Riesgo Cardiovascular y Composición Antropométrica",
        "texto": "Un IMC igual o mayor a 30 kg/m² indica obesidad con riesgo cardiometabólico incrementado. La estimación de grasa corporal superior a 25% en hombres y 32% en mujeres exige plan nutricional y control de presión arterial < 130/80 mmHg."
    },
    # Metrología y Equipos Biomédicos de UCI (Salud Digital Proyecto)
    {
        "chunk_id": "BM-01", "fuente": "Norma IEC 62353 · Ensayos Periódicos de Seguridad Eléctrica UCI",
        "texto": "La norma IEC 62353 exige evaluar la resistencia del conductor de protección de tierra (límite máximo 0,2 ohmios o 0,3 con cable separable) y la corriente de fuga a tierra (límite máximo 500 µA en equipos de soporte vital UCI)."
    },
    {
        "chunk_id": "BM-02", "fuente": "Protocolo Metrológico · Ventiladores Mecánicos UCI (Hamilton C3 / Dräger Evita)",
        "texto": "En ventiladores de cuidados intensivos, el error de volumen corriente (Vt) no debe exceder el ±10% o ±10 mL, y el error de presión inspiratoria máxima debe ser menor a ±2 hPa. Toda desviación superior requiere parada metrológica inmediata."
    },
    {
        "chunk_id": "BM-03", "fuente": "Resolución 3100 de 2019 de Minsalud Colombia · Estándar de Dotación UCI",
        "texto": "Los prestadores de salud deben garantizar el programa de mantenimiento preventivo y calibración metrológica vigente para bombas de infusión, desfibriladores y ventiladores en unidades de cuidados intensivos."
    },
    {
        "chunk_id": "BM-04", "fuente": "Protocolo Metrológico · Bombas de Infusión UCI (B. Braun Infusomat)",
        "texto": "La tolerancia admisible de exactitud en flujo para bombas de infusión volumétricas en UCI pediátrica y adultos es de ±5%. Errores mayores provocan dosificación errónea de medicamentos vasoactivos."
    }
]

# Cargar documentos reales adicionales de rag_corpus si existen
CORPUS_FILE = BASE_DIR / "rag_corpus" / "documentos_index.json"
FRAGMENTOS = list(FRAGMENTOS_BASE)

if CORPUS_FILE.exists():
    try:
        with open(CORPUS_FILE, "r", encoding="utf-8") as f:
            docs = json.load(f)
            for d in docs[:40]:  # hasta 40 fragmentos del corpus real
                FRAGMENTOS.append({
                    "chunk_id": d.get("id", f"DOC-{len(FRAGMENTOS)+1}"),
                    "fuente": d.get("titulo", "Manual Técnico UCI"),
                    "texto": d.get("contenido", "")[:350]
                })
    except Exception:
        pass

# Motor BM25 precalculado
_bm25 = BM25Okapi([_tokens(f["texto"]) for f in FRAGMENTOS])

def consultar_rag(pregunta: str, alias: str | None = None, k: int = 3) -> dict:
    """Busca en el corpus de guías clínicas y metrología UCI con ranking BM25."""
    q_tokens = _tokens(pregunta)
    if not q_tokens:
        return {"respuesta": "Pregunta vacía", "modo": "abstencion", "fuentes": []}

    scores = _bm25.get_scores(q_tokens)
    orden = sorted(range(len(FRAGMENTOS)), key=lambda i: -scores[i])[:k]
    fuentes = [FRAGMENTOS[i] for i in orden if scores[i] > 0]

    if not fuentes:
        return {
            "respuesta": "No encontré evidencia en las guías clínicas ni en los protocolos de metrología UCI para esa consulta.",
            "modo": "abstencion",
            "fuentes": []
        }

    return {
        "respuesta": fuentes[0]["texto"],
        "modo": "bm25_biomedico",
        "fuentes": fuentes
    }

def formatear_para_agente(resultado: dict) -> str:
    """Formatea la salida del RAG con fuentes numeradas [1], [2]... requeridas por el harness."""
    if not resultado.get("fuentes"):
        return "SIN_EVIDENCIA: " + resultado.get("respuesta", "")
    lineas = [f"Respuesta del RAG: {resultado.get('respuesta', '')}", "Fuentes:"]
    for n, f in enumerate(resultado["fuentes"], start=1):
        ident = f.get("chunk_id") or f"fuente{n}"
        origen = f.get("fuente") or ""
        texto = f.get("texto") or ""
        lineas.append(f"[{n}] ({ident}) {origen}: {texto[:300]}")
    return "\n".join(lineas)

ORIGEN = f"hibrido_uci_clinico ({len(FRAGMENTOS)} documentos)"
