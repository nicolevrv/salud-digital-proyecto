# -*- coding: utf-8 -*-
"""
rag/motor.py · Motor RAG del Hospital Universitario UCI Digital (Semana 12)
Soporte biomédico y clínico: BM25, embeddings, filtrado y generación con Groq / Fallback extractivo.
"""
import os
import re
import json
import math
import unicodedata
from collections import Counter
from pathlib import Path
import numpy as np

RUTA_FRAGMENTOS = Path(__file__).parent / "fragmentos.json"

STOP_WORDS_ES = set("""a al algo con como de del el en entre es esta este esto la las le lo los mas o para pero por que
se sin sobre su sus un una uno y ya cual cuales cuando donde debe deben puede ser son si no hay tiene tienen al ser""".split())

def sin_tildes(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")

def tokenizar(texto: str) -> list[str]:
    texto = sin_tildes(texto.lower())
    return [t for t in re.findall(r"[a-z0-9]+(?:,[0-9]+)?", texto) if t not in STOP_WORDS_ES and len(t) > 1]

def cargar_fragmentos() -> list[dict]:
    if RUTA_FRAGMENTOS.exists():
        with open(RUTA_FRAGMENTOS, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

class BM25:
    def __init__(self, textos: list[str], k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [tokenizar(t) for t in textos]
        self.N = len(self.docs)
        self.avgdl = sum(len(d) for d in self.docs) / max(1, self.N)
        self.tf = [Counter(d) for d in self.docs]
        df = Counter(t for d in self.docs for t in set(d))
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def puntajes(self, pregunta: str) -> list[float]:
        q = tokenizar(pregunta)
        salida = []
        for tf, d in zip(self.tf, self.docs):
            norm = self.k1 * (1 - self.b + self.b * len(d) / self.avgdl)
            salida.append(sum(self.idf.get(t, 0) * tf.get(t, 0) * (self.k1 + 1) / (tf.get(t, 0) + norm) for t in q if tf.get(t)))
        return salida

FRAGMENTOS = cargar_fragmentos()
INDICE_BM25 = BM25([f["texto"] for f in FRAGMENTOS]) if FRAGMENTOS else None
UMBRAL_BM25 = float(os.getenv("RAG_UMBRAL_BM25", "1.2"))

PROMPT_SISTEMA = (
    "Eres un asistente de apoyo a la decisión clínica y de ingeniería biomédica para el Hospital Universitario UCI Digital. "
    "Responde SOLO con el CONTEXTO provisto (normas IEC/INVIMA y manuales técnicos de UCI), "
    "cita cada afirmación técnica con [n] y, si el contexto no alcanza, di exactamente: "
    "\"No hay evidencia suficiente en las fuentes del sistema.\" "
    "No inventes valores de calibración ni recomendaciones. Máximo 5 oraciones."
)
SIN_EVIDENCIA = "No hay evidencia suficiente en las fuentes del sistema."

def permitido(f: dict, base_filtro: str | None = None) -> bool:
    if base_filtro:
        return f.get("base") == base_filtro
    return True

def buscar(pregunta: str, base_filtro: str | None = None, k: int = 3) -> list[dict]:
    if not INDICE_BM25 or not FRAGMENTOS:
        return []
    puntajes = INDICE_BM25.puntajes(pregunta)
    orden = sorted(range(len(FRAGMENTOS)), key=lambda i: -puntajes[i])
    elegidos = [i for i in orden if permitido(FRAGMENTOS[i], base_filtro) and puntajes[i] >= UMBRAL_BM25][:k]
    return [dict(FRAGMENTOS[i], puntaje=round(puntajes[i], 3)) for i in elegidos]

def construir_mensajes(pregunta: str, fragmentos: list[dict]) -> list[dict]:
    contexto = "\n\n".join(
        f"[{n}] ({f['titulo']} - {f.get('codigo_norma', '')})\n{f['texto']}"
        for n, f in enumerate(fragmentos, start=1)
    )
    return [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": f"CONTEXTO:\n{contexto}\n\nPREGUNTA TÉCNICA/CLÍNICA: {pregunta}"}
    ]

def generar(mensajes: list[dict], fragmentos: list[dict]) -> tuple[str, str]:
    llave = os.getenv("GROQ_API_KEY")
    modelo = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    if llave:
        try:
            import httpx
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {llave}"},
                json={"model": modelo, "messages": mensajes, "temperature": 0.0, "max_tokens": 400},
                timeout=45
            )
            r.raise_for_status()
            texto = r.json()["choices"][0]["message"]["content"]
            return re.sub(r"<think>.*?</think>", "", texto, flags=re.S).strip(), "groq_llm"
        except Exception:
            pass
    # Respaldo extractivo
    resp_ext = " ".join(f"{f['texto'].split('.')[0].strip()}. [{n}]" for n, f in enumerate(fragmentos, start=1))
    return resp_ext, "extractivo"

def responder(pregunta: str, base_filtro: str | None = None, k: int = 3) -> dict:
    fragmentos = buscar(pregunta, base_filtro, k=k)
    if not fragmentos:
        return {
            "respuesta": SIN_EVIDENCIA,
            "modo": "abstencion",
            "fuentes": [],
            "mensajes": []
        }
    mensajes = construir_mensajes(pregunta, fragmentos)
    texto, modo = generar(mensajes, fragmentos)
    fuentes = [
        {
            "n": n,
            "chunk_id": f["chunk_id"],
            "doc_id": f["doc_id"],
            "titulo": f["titulo"],
            "norma": f.get("codigo_norma", "")
        }
        for n, f in enumerate(fragmentos, start=1)
    ]
    return {
        "respuesta": texto,
        "modo": modo,
        "fuentes": fuentes,
        "mensajes": mensajes
    }
