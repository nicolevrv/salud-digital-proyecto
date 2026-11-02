# -*- coding: utf-8 -*-
"""
rag/motor.py · Motor RAG del Hospital Universitario UCI Digital (Semana 12)
Soporte biomédico y clínico: Búsqueda Híbrida (BM25 + TF-IDF Coseno + RRF)
Persistencia híbrida (PostgreSQL Neon en tabla 'rag_chunks' + fallback JSON local)
Regla de abstención estricta para preguntas fuera de dominio (ej. 'cuanto es mil menos 1').
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

STOP_WORDS_ES = set("""
a al algo con como de del el en entre es esta este esto la las le lo los mas o para pero por que
se sin sobre su sus un una uno y ya cual cuales cuando donde debe deben puede ser son si no hay tiene tienen al ser
cuanto cuanta cuantos cuantas mil menos mas cero uno dos tres cuatro cinco seis siete ocho nueve diez
suma resta multiplica divide igual resultado dime sabes puedes decir explicar responder hola buenos dias tardes noches
quien quienes que porque
""".split())

def sin_tildes(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")

def tokenizar(texto: str) -> list[str]:
    texto = sin_tildes(texto.lower())
    return [
        t for t in re.findall(r"[a-z0-9]+(?:,[0-9]+)?", texto)
        if t not in STOP_WORDS_ES and len(t) > 1 and not t.isdigit()
    ]

def tokenizar_palabras(texto: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]+", texto.lower()))

def cargar_fragmentos() -> list[dict]:
    # 1. Carga inmediata desde fragmentos.json local (sub-milisegundo para alta concurrencia)
    if RUTA_FRAGMENTOS.exists():
        try:
            with open(RUTA_FRAGMENTOS, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data:
                    return data
        except Exception:
            pass

    # 2. Respaldo directo en la nube Neon PostgreSQL si no estuviese el archivo local
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=4)
            cur = conn.cursor()
            cur.execute("SELECT chunk_id, doc_id, base, tipo, titulo, codigo_norma, fuente, palabras, texto FROM rag_chunks;")
            rows = cur.fetchall()
            if rows:
                chunks = []
                for r in rows:
                    chunks.append({
                        "chunk_id": r[0],
                        "doc_id": r[1],
                        "base": r[2],
                        "tipo": r[3],
                        "titulo": r[4],
                        "codigo_norma": r[5],
                        "fuente": r[6],
                        "palabras": r[7],
                        "texto": r[8]
                    })
                cur.close()
                conn.close()
                return chunks
        except Exception:
            pass

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

class BuscadorTFIDF:
    def __init__(self, textos: list[str]):
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vectorizer = TfidfVectorizer(tokenizer=tokenizar, lowercase=False, token_pattern=None)
            self.matriz = self.vectorizer.fit_transform(textos)
        except Exception:
            self.vectorizer = None
            self.matriz = None

    def puntajes(self, pregunta: str) -> np.ndarray:
        if self.vectorizer is None or self.matriz is None:
            return np.zeros(len(FRAGMENTOS))
        q = self.vectorizer.transform([pregunta])
        return (self.matriz @ q.T).toarray().ravel()

FRAGMENTOS = cargar_fragmentos()
TEXTOS_CORPUS = [f["texto"] for f in FRAGMENTOS] if FRAGMENTOS else []
INDICE_BM25 = BM25(TEXTOS_CORPUS) if FRAGMENTOS else None
INDICE_TFIDF = BuscadorTFIDF(TEXTOS_CORPUS) if FRAGMENTOS else None

# Umbrales rigurosos para evitar responder preguntas fuera de dominio
UMBRAL_BM25 = 1.8
UMBRAL_TFIDF = 0.16

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

def rrf(listas_rankings: list[list[int]], k: int = 60) -> list[int]:
    total = Counter()
    for lista in listas_rankings:
        for pos, idx in enumerate(lista, start=1):
            total[idx] += 1.0 / (k + pos)
    return [idx for idx, _ in total.most_common()]

def buscar(pregunta: str, base_filtro: str | None = None, k: int = 3) -> list[dict]:
    if not INDICE_BM25 or not FRAGMENTOS:
        return []
        
    q_tokens = tokenizar(pregunta)
    # Regla de abstención 1: Si no hay tokens sustantivos tras limpiar stopwords y números (ej. 'cuanto es mil menos 1')
    if not q_tokens or len(q_tokens) < 1:
        return []

    s_bm25 = np.array(INDICE_BM25.puntajes(pregunta))
    s_tfidf = INDICE_TFIDF.puntajes(pregunta) if INDICE_TFIDF else np.zeros(len(FRAGMENTOS))
    
    max_b = float(s_bm25.max()) if len(s_bm25) else 0.0
    max_t = float(s_tfidf.max()) if len(s_tfidf) else 0.0
    
    # Regla de abstención 2: Si la coincidencia semántica/léxica está por debajo del umbral de dominio biomédico
    if max_b < UMBRAL_BM25 and max_t < UMBRAL_TFIDF:
        return []
        
    # Fusión RRF de BM25 y TF-IDF Coseno
    rank_bm25 = list(np.argsort(-s_bm25))
    rank_tfidf = list(np.argsort(-s_tfidf))
    orden_fused = rrf([rank_bm25, rank_tfidf], k=60)
    
    elegidos = [i for i in orden_fused if permitido(FRAGMENTOS[i], base_filtro)][:k]
    return [dict(FRAGMENTOS[i], puntaje=round(float(s_bm25[i]), 3)) for i in elegidos]

def es_encabezado_puro(idx: int, sent: str) -> bool:
    if idx == 0:
        tiene_unidades = bool(re.search(r'ohmios|microamperios|µA|mmHg|bar|psi|mL|hPa|voltios|%|minutos|horas|días|meses|kg|joules|julios', sent, re.I))
        tiene_accion = bool(re.search(r'\b(ingresa|conecta|presuriza|mide|calibra|verifica|excede|supera|ajusta|detiene|establece|exige|sufren|requiere|corresponde|cuenta)\b', sent, re.I))
        if not tiene_unidades and not tiene_accion:
            return True
        if len(sent.split()) <= 12 and not tiene_unidades:
            return True
    return False

def sintetizar_respuesta_extractiva(pregunta: str, fragmentos: list[dict], max_oraciones: int = 4) -> str:
    q_tokens = tokenizar_palabras(pregunta) - STOP_WORDS_ES
    todas_oraciones = []
    
    for n, f in enumerate(fragmentos, start=1):
        texto = f["texto"]
        raw_sents = [s.strip() for s in re.split(r"\.\s+", texto) if s.strip()]
        for idx, s in enumerate(raw_sents):
            s_clean = s.strip()
            if len(s_clean.split()) < 4 or es_encabezado_puro(idx, s_clean):
                continue
            s_tokens = tokenizar_palabras(s_clean) - STOP_WORDS_ES
            coincidencias = len(q_tokens.intersection(s_tokens))
            
            tiene_cifras = bool(re.search(r"\d+", s_clean))
            tiene_unidades = bool(re.search(r"ohmios|microamperios|µA|mmHg|bar|psi|mL|hPa|voltios|%|minutos|horas|días|meses|kg|joules|julios", s_clean, re.I))
            tiene_accion = bool(re.search(r"conecta|ingresa|presuriza|mide|calibra|verifica|excede|supera|ajusta|utiliza|establece|requiere|permite|detiene", s_clean, re.I))
            tiene_criterio = bool(re.search(r"límite|máximo|mínimo|tolerancia|norma|procedimiento|error|resistencia|corriente|fuga|exactitud|servicio", s_clean, re.I))
            
            score = coincidencias * 3.5
            if tiene_cifras: score += 3.0
            if tiene_unidades: score += 4.0
            if tiene_accion: score += 2.5
            if tiene_criterio: score += 2.0
            
            todas_oraciones.append({
                "score": score,
                "n": n,
                "idx": idx,
                "sent": s_clean
            })
            
    todas_oraciones.sort(key=lambda x: -x["score"])
    seleccionadas = todas_oraciones[:max_oraciones]
    seleccionadas.sort(key=lambda x: (x["n"], x["idx"]))
    
    resultado = []
    vistos = set()
    for item in seleccionadas:
        if item["sent"] not in vistos:
            vistos.add(item["sent"])
            resultado.append(f"{item['sent'].rstrip('.')} [{item['n']}].")
            
    if not resultado:
        return " ".join(f"{f['texto'][:240].rsplit(' ', 1)[0]}... [{n}]" for n, f in enumerate(fragmentos, start=1))
        
    return " ".join(resultado)

def construir_mensajes(pregunta: str, fragmentos: list[dict]) -> list[dict]:
    contexto = "\n\n".join(
        f"[{n}] ({f['titulo']} - {f.get('codigo_norma', '')})\n{f['texto']}"
        for n, f in enumerate(fragmentos, start=1)
    )
    return [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": f"CONTEXTO:\n{contexto}\n\nPREGUNTA TÉCNICA/CLÍNICA: {pregunta}"}
    ]

def generar(pregunta: str, mensajes: list[dict], fragmentos: list[dict]) -> tuple[str, str]:
    llave = os.getenv("GROQ_API_KEY")
    modelo = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    if llave:
        try:
            import httpx
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {llave}"},
                json={"model": modelo, "messages": mensajes, "temperature": 0.0, "max_tokens": 400},
                timeout=15
            )
            r.raise_for_status()
            texto = r.json()["choices"][0]["message"]["content"]
            return re.sub(r"<think>.*?</think>", "", texto, flags=re.S).strip(), "groq_llm"
        except Exception:
            pass
            
    resp_ext = sintetizar_respuesta_extractiva(pregunta, fragmentos, max_oraciones=4)
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
    texto, modo = generar(pregunta, mensajes, fragmentos)
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
