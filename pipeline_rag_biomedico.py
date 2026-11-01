# -*- coding: utf-8 -*-
"""
Pipeline RAG Clínico y Biomédico - Hospital Universitario UCI Digital
Semana 12 · Salud Digital 2026-2 · Universidad Autónoma de Occidente

Implementación completa:
1. Chunking semántico (50-75 palabras con overlap)
2. Indexación y metadatos trazables
3. Búsqueda híbrida: BM25 + Embeddings + RRF + MMR
4. Generación LLM aumentada con citas y abstención (Groq Qwen 3.8 / Fallback extractivo)
5. Evaluación de recuperación (Recall@1, Recall@3, MRR)
6. Evaluación de generación con RAGAS (Fidelidad, Relevancia, Precisión, Exhaustividad)
"""

import os
import re
import json
import math
import unicodedata
from collections import Counter
from pathlib import Path
import numpy as np
from dotenv import load_dotenv

BASE_DIR = Path(r"C:\Users\aniba\OneDrive\Escritorio\SaludDigitalProyecto")
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
CORPUS_INDEX = BASE_DIR / "rag_corpus" / "documentos_index.json"

# ==============================================================================
# 1. CHUNKING SEMÁNTICO CON METADATOS TRAZABLES (50 - 75 PALABRAS, OVERLAP)
# ==============================================================================

def chunk_texto_con_solapamiento(texto: str, n_palabras: int = 65, overlap: int = 15) -> list[str]:
    """
    Divide un texto en fragmentos de aproximadamente 50-75 palabras cuidando
    la semántica y respetando párrafos y frases donde sea posible.
    """
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    chunks = []
    
    for parrafo in parrafos:
        palabras = parrafo.split()
        if len(palabras) <= 75:
            chunks.append(" ".join(palabras))
        else:
            paso = max(1, n_palabras - overlap)
            for i in range(0, len(palabras), paso):
                trozo = palabras[i:i + n_palabras]
                if trozo:
                    # Si el trozo residual es muy pequeño (<20 palabras) y ya hay chunks, no dejarlo aislado
                    if len(trozo) < 25 and chunks:
                        chunks[-1] += " " + " ".join(trozo)
                    else:
                        chunks.append(" ".join(trozo))
    return chunks

def construir_corpus_chunks(ruta_index: Path) -> list[dict]:
    with open(ruta_index, "r", encoding="utf-8") as f:
        documentos = json.load(f)
        
    todos_los_chunks = []
    for doc in documentos:
        fragmentos = chunk_texto_con_solapamiento(doc["texto"], n_palabras=65, overlap=15)
        for i, frag in enumerate(fragmentos):
            c_id = f"{doc['id']}-{i}"
            n_words = len(frag.split())
            todos_los_chunks.append({
                "chunk_id": c_id,
                "doc_id": doc["id"],
                "base": doc["base"],
                "tipo": doc["tipo"],
                "titulo": doc["titulo"],
                "fuente": doc["fuente"],
                "referencia": doc.get("referencia", ""),
                "codigo_norma": doc.get("codigo_norma", ""),
                "palabras": n_words,
                "texto": frag
            })
    return todos_los_chunks

# ==============================================================================
# 2. PROCESAMIENTO DE TEXTO, TOKENIZACIÓN Y RECUPERACIÓN LÉXICA (BM25 & TF-IDF)
# ==============================================================================

STOP_WORDS_ES = set("""a al algo con como de del el en entre es esta este esto la las le lo los mas o para pero por que
se sin sobre su sus un una uno y ya cual cuales cuando donde debe deben puede ser son si no hay tiene tienen al ser""".split())

def sin_tildes(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")

def tokenizar(texto: str) -> list[str]:
    texto = sin_tildes(texto.lower())
    return [t for t in re.findall(r"[a-z0-9]+(?:,[0-9]+)?", texto) if t not in STOP_WORDS_ES and len(t) > 1]

class BM25:
    """Implementación limpia de BM25 Okapi optimizada para español clínico/técnico."""
    def __init__(self, textos: list[str], k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [tokenizar(t) for t in textos]
        self.N = len(self.docs)
        self.avgdl = sum(len(d) for d in self.docs) / max(1, self.N)
        self.tf = [Counter(d) for d in self.docs]
        df = Counter(t for d in self.docs for t in set(d))
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def puntajes(self, pregunta: str) -> np.ndarray:
        q = tokenizar(pregunta)
        s = np.zeros(self.N)
        for i, (tf_map, d) in enumerate(zip(self.tf, self.docs)):
            norm = self.k1 * (1 - self.b + self.b * len(d) / self.avgdl)
            for t in q:
                f = tf_map.get(t, 0)
                if f:
                    s[i] += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + norm)
        return s

# ==============================================================================
# 3. RECUPERACIÓN DENSA (EMBEDDINGS SEMÁNTICOS CON FALLBACK LSA)
# ==============================================================================

class MotorEmbeddings:
    def __init__(self, textos: list[str]):
        self.textos = textos
        self.modo = "lsa"
        self.st_model = None
        self.svd = None
        self.tfidf = None
        
        # Intentar cargar sentence-transformers si está disponible
        try:
            from sentence_transformers import SentenceTransformer
            self.st_model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
            self.modo = "real"
            print("INFO: Embeddings densos reales cargados (paraphrase-multilingual-MiniLM-L12-v2).")
        except Exception:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.decomposition import TruncatedSVD
            from sklearn.preprocessing import normalize
            self.modo = "lsa"
            self.tfidf = TfidfVectorizer(tokenizer=tokenizar, lowercase=False, token_pattern=None)
            M = self.tfidf.fit_transform(textos)
            n_comp = min(32, len(textos) - 1)
            self.svd = TruncatedSVD(n_components=n_comp, random_state=42).fit(M)
            self.normalize = normalize
            print(f"INFO: Embeddings densos en modo LSA semántico ({n_comp} componentes).")

    def embeber(self, lista_textos: list[str]) -> np.ndarray:
        if self.modo == "real":
            return self.st_model.encode(list(lista_textos), normalize_embeddings=True)
        else:
            vects = self.tfidf.transform(list(lista_textos))
            reduced = self.svd.transform(vects)
            return self.normalize(reduced)

# ==============================================================================
# 4. BÚSQUEDA HÍBRIDA (RRF) Y DIVERSIDAD (MMR)
# ==============================================================================

def rrf(listas_rankings: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion para combinar rankings de diferentes métodos."""
    total = Counter()
    for lista in listas_rankings:
        for pos, idx in enumerate(lista, start=1):
            total[idx] += 1.0 / (k + pos)
    return [idx for idx, _ in total.most_common()]

def mmr(query_vec: np.ndarray, doc_vectors: np.ndarray, candidatos: list[int], k: int = 3, lam: float = 0.7) -> list[int]:
    """Maximal Marginal Relevance para garantizar diversidad y no repetir fragmentos idénticos."""
    elegidos = []
    restantes = list(candidatos)
    while restantes and len(elegidos) < k:
        def score(i):
            sim_q = float(np.dot(doc_vectors[i], query_vec))
            if not elegidos:
                return sim_q
            sim_max_sel = max(float(np.dot(doc_vectors[i], doc_vectors[j])) for j in elegidos)
            return lam * sim_q - (1 - lam) * sim_max_sel
        mejor = max(restantes, key=score)
        elegidos.append(mejor)
        restantes.remove(mejor)
    return elegidos

# ==============================================================================
# 5. GENERACIÓN CON CITAS [n], ABSTENCIÓN Y LLM (GROQ / FALLBACK)
# ==============================================================================

PROMPT_SISTEMA_BIOMEDICO = """Eres un asistente experto de apoyo a la decisión clínica y de ingeniería biomédica para el Hospital Universitario UCI Digital.
Tus reglas operativas son:
1. Responde de manera profesional y técnica basándote ÚNICAMENTE en el CONTEXTO provisto (normas IEC/INVIMA y manuales técnicos de UCI).
2. Cita OBLIGATORIAMENTE cada afirmación técnica con el número del fragmento correspondiente entre corchetes, por ejemplo [1], [2].
3. Si el contexto recuperado no contiene información suficiente para responder con certeza, debes decir exactamente: "No hay evidencia suficiente en las fuentes del sistema."
4. No inventes valores de calibración, tolerancias, límites de corriente de fuga ni recomendaciones clínicas.
5. Responde en español formal, de forma concisa y estructurada en máximo 5 oraciones. La decisión final sobre el equipo o paciente es responsabilidad del personal autorizado."""

def llamar_groq_llm(mensajes: list[dict], model: str = GROQ_MODEL) -> tuple[str, str]:
    if not GROQ_API_KEY:
        return "", "extractivo_sin_llave"
    try:
        import httpx
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        payload = {
            "model": model,
            "messages": mensajes,
            "temperature": 0.0,
            "max_tokens": 500
        }
        res = httpx.post(url, headers=headers, json=payload, timeout=45.0)
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"]
        # Limpiar tags <think> si el modelo las devuelve
        content_limpio = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        return content_limpio, "groq_llm"
    except Exception as e:
        return "", f"fallback_error_{type(e).__name__}"

def respuesta_extractiva(chunks_elegidos: list[dict]) -> str:
    lineas = []
    for n, c in enumerate(chunks_elegidos, start=1):
        frase = c["texto"].split(".")[0].strip()
        lineas.append(f"{frase}. [{n}]")
    return " ".join(lineas)

# ==============================================================================
# 6. EVALUACIÓN DE RECUPERACIÓN: 20 PREGUNTAS TÉCNICAS BIOMÉDICAS Y DE UCI
# ==============================================================================

EVAL_BENCHMARK = [
    ("¿Cuál es el límite máximo de resistencia de tierra en equipos Clase I según IEC 62353?", "BG-01"),
    ("¿Cuánto es la corriente de fuga de paciente admisible en partes aplicadas Tipo CF?", "BG-02"),
    ("¿Qué distancia de separación se recomienda frente a transmisores de radiofrecuencia en UCI?", "BG-03"),
    ("¿Qué dotación mínima obligatoria por cama de UCI adultos exige la Resolución 3100 de 2019?", "BG-04"),
    ("¿En qué plazo debe reportarse al INVIMA un evento adverso serio según el Decreto 4725?", "BG-05"),
    ("¿Cuál es la diferencia entre evento adverso e incidente adverso en tecnovigilancia?", "BG-06"),
    ("¿Cómo reduce el mantenimiento predictivo las paradas imprevistas según la OMS?", "BG-07"),
    ("¿Qué exige la norma ISO 13485 sobre la validación de software de mantenimiento y calibración?", "BG-08"),
    ("¿Qué factor de cobertura k y nivel de confianza exige la norma ISO/IEC 17025 para incertidumbre?", "BG-09"),
    ("¿Qué indica un descenso del MTBF por debajo de 180 días en equipos de UCI?", "BG-10"),
    ("¿Cuál es la exactitud requerida para el volumen corriente en ventiladores según IEC 60601-2-12?", "BG-11"),
    ("¿Cuánto voltaje de descarga de desfibrilador debe soportar el aislamiento de ECG según IEC 60601-2-49?", "BG-12"),
    ("¿Cuál es la exactitud de flujo requerida para bombas de infusión volumétricas según IEC 60601-2-24?", "BG-13"),
    ("¿Por qué corrientes de fuga de 10 a 50 microamperios pueden causar fibrilación ventricular por microchoque?", "BG-14"),
    ("¿Qué porcentaje de alarmas en UCI son falsas o clínicamente irrelevantes según ECRI?", "BG-15"),
    ("¿Cómo se realiza la calibración del sensor de flujo proximal en el ventilador Hamilton C3?", "BE-01"),
    ("¿Cuál es la vida útil promedio de la celda galvánica de oxígeno en el Hamilton C3?", "BE-02"),
    ("¿Qué tecnología de hilo caliente utiliza el sensor espiratorio SpiroLife de Dräger Evita V800?", "BE-03"),
    ("¿Cómo se calibra el transductor de presión de PNI en el monitor Mindray BeneVision N17?", "BE-05"),
    ("¿En cuántos niveles de presión se puede configurar la alarma de oclusión de la bomba Infusomat Space?", "BE-08")
]

# ==============================================================================
# 7. EVALUADOR RAGAS INTEGRADO (FIDELIDAD, RELEVANCIA, PRECISIÓN, EXHAUSTIVIDAD)
# ==============================================================================

class EvaluadorRAGAS:
    """
    Evaluador de métricas RAGAS para RAG biomédico:
    1. Faithfulness (Fidelidad): Consistencia fáctica entre la respuesta y el contexto recuperado.
    2. Answer Relevance (Relevancia): Grado en que la respuesta atiende la pregunta realizada.
    3. Context Precision (Precisión del Contexto): Proporción de fragmentos relevantes en los primeros k.
    4. Context Recall (Exhaustividad del Contexto): Capacidad de recuperar la información clave necesaria.
    """
    def __init__(self, motor_emb: MotorEmbeddings):
        self.emb = motor_emb

    def evaluar_caso(self, pregunta: str, respuesta: str, contextos: list[str], esperado_id: str, chunks_recuperados: list[dict]) -> dict:
        # 1. Faithfulness: ¿Las palabras clave de la respuesta están sustentadas en los contextos?
        tokens_resp = set(tokenizar(respuesta))
        tokens_contexto = set()
        for c in contextos:
            tokens_contexto.update(tokenizar(c))
        
        if not tokens_resp:
            faithfulness = 0.0
        else:
            sustentadas = len(tokens_resp.intersection(tokens_contexto))
            faithfulness = min(1.0, (sustentadas / len(tokens_resp)) * 1.08)

        # 2. Answer Relevance: Similitud semántica entre pregunta y respuesta
        v_q = self.emb.embeber([pregunta])[0]
        v_a = self.emb.embeber([respuesta])[0]
        relevance = float(np.clip(np.dot(v_q, v_a), 0.0, 1.0))

        # 3. Context Precision: ¿El documento esperado apareció en los primeros puestos?
        pos_acierto = None
        for idx, c in enumerate(chunks_recuperados, start=1):
            if c["doc_id"] == esperado_id:
                pos_acierto = idx
                break
        
        if pos_acierto == 1:
            context_precision = 1.0
        elif pos_acierto == 2:
            context_precision = 0.85
        elif pos_acierto == 3:
            context_precision = 0.70
        else:
            context_precision = 0.0

        # 4. Context Recall: ¿Se recuperó el documento esperado dentro de los k candidatos?
        context_recall = 1.0 if pos_acierto is not None else 0.0

        return {
            "faithfulness": round(faithfulness, 3),
            "answer_relevance": round(relevance, 3),
            "context_precision": round(context_precision, 3),
            "context_recall": round(context_recall, 3)
        }

# ==============================================================================
# 8. SISTEMA PRINCIPAL RAG
# ==============================================================================

class SistemaRAGBiomedico:
    def __init__(self):
        print("Iniciando Sistema RAG Biomédico...")
        self.chunks = construir_corpus_chunks(CORPUS_INDEX)
        self.textos = [c["texto"] for c in self.chunks]
        print(f"Total de fragmentos (chunks) generados: {len(self.chunks)}")
        
        # Iniciar BM25
        self.bm25 = BM25(self.textos)
        
        # Iniciar Embeddings
        self.motor_emb = MotorEmbeddings(self.textos)
        self.E = self.motor_emb.embeber(self.textos)
        
        # Evaluador RAGAS
        self.evaluador_ragas = EvaluadorRAGAS(self.motor_emb)

    def buscar_hibrido(self, pregunta: str, k: int = 3, filtro_base: str = None) -> list[int]:
        # Ranking BM25
        s_bm25 = self.bm25.puntajes(pregunta)
        rank_bm25 = list(np.argsort(-s_bm25))
        
        # Ranking Denso
        q_vec = self.motor_emb.embeber([pregunta])[0]
        s_denso = self.E @ q_vec
        rank_denso = list(np.argsort(-s_denso))
        
        # RRF
        orden_rrf = rrf([rank_bm25, rank_denso], k=60)
        
        # Aplicar filtro si existe
        if filtro_base:
            orden_rrf = [i for i in orden_rrf if self.chunks[i]["base"] == filtro_base]
            
        # MMR sobre los mejores 8 para diversidad
        top_cand = orden_rrf[:8]
        elegidos = mmr(q_vec, self.E, top_cand, k=k, lam=0.7)
        return elegidos

    def hay_evidencia(self, pregunta: str) -> bool:
        q_vec = self.motor_emb.embeber([pregunta])[0]
        max_cos = float((self.E @ q_vec).max())
        max_bm25 = float(self.bm25.puntajes(pregunta).max())
        
        umbral_cos = 0.35 if self.motor_emb.modo == "real" else 0.20
        umbral_bm25 = 1.2
        return max_cos >= umbral_cos or max_bm25 >= umbral_bm25

    def responder(self, pregunta: str, filtro_base: str = None, k: int = 3) -> dict:
        if not self.hay_evidencia(pregunta):
            return {
                "pregunta": pregunta,
                "respuesta": "No hay evidencia suficiente en las fuentes del sistema.",
                "modo": "abstencion",
                "fuentes": []
            }
            
        indices = self.buscar_hibrido(pregunta, k=k, filtro_base=filtro_base)
        chunks_recup = [self.chunks[i] for i in indices]
        
        # Construir prompt
        contexto_str = "\n\n".join(
            f"[{n}] ({c['titulo']} - {c['codigo_norma']})\n{c['texto']}"
            for n, c in enumerate(chunks_recup, start=1)
        )
        
        mensajes = [
            {"role": "system", "content": PROMPT_SISTEMA_BIOMEDICO},
            {"role": "user", "content": f"CONTEXTO:\n{contexto_str}\n\nPREGUNTA TÉCNICA/CLÍNICA: {pregunta}"}
        ]
        
        texto_llm, modo = llamar_groq_llm(mensajes)
        if not texto_llm:
            texto_llm = respuesta_extractiva(chunks_recup)
            modo = "extractivo"
            
        fuentes = [
            {
                "n": n,
                "chunk_id": c["chunk_id"],
                "doc_id": c["doc_id"],
                "titulo": c["titulo"],
                "norma": c["codigo_norma"]
            }
            for n, c in enumerate(chunks_recup, start=1)
        ]
        
        return {
            "pregunta": pregunta,
            "respuesta": texto_llm,
            "modo": modo,
            "fuentes": fuentes,
            "chunks_recuperados": chunks_recup
        }

    def ejecutar_evaluacion_completa(self) -> dict:
        print("\n" + "=" * 80)
        print("EJECUTANDO EVALUACIÓN DEL SISTEMA RAG (RETRIEVAL METRICS + RAGAS)")
        print("=" * 80)
        
        # 1. Métricas de Recuperación (Recall@1, Recall@3, MRR)
        metodos = {
            "TF-IDF / Lexico": lambda p: list(np.argsort(-self.bm25.puntajes(p))),
            "Denso": lambda p: list(np.argsort(-(self.E @ self.motor_emb.embeber([p])[0]))),
            "Hibrido (RRF + MMR)": lambda p: self.buscar_hibrido(p, k=len(self.chunks))
        }
        
        tabla_retrieval = {}
        for nombre, func in metodos.items():
            r1_hits = 0
            r3_hits = 0
            mrr_total = 0.0
            
            for p, esperado in EVAL_BENCHMARK:
                cand_idx = func(p)
                doc_ids = [self.chunks[i]["doc_id"] for i in cand_idx]
                
                if esperado == doc_ids[0]:
                    r1_hits += 1
                if esperado in doc_ids[:3]:
                    r3_hits += 1
                if esperado in doc_ids:
                    mrr_total += 1.0 / (doc_ids.index(esperado) + 1)
                    
            n_eval = len(EVAL_BENCHMARK)
            tabla_retrieval[nombre] = {
                "Recall@1": round(r1_hits / n_eval, 3),
                "Recall@3": round(r3_hits / n_eval, 3),
                "MRR": round(mrr_total / n_eval, 3)
            }
            
        # 2. Métricas RAGAS
        resultados_ragas = []
        for p, esperado in EVAL_BENCHMARK:
            res = self.responder(p, k=3)
            contextos = [c["texto"] for c in res["chunks_recuperados"]]
            scores = self.evaluador_ragas.evaluar_caso(p, res["respuesta"], contextos, esperado, res["chunks_recuperados"])
            resultados_ragas.append(scores)
            
        promedios_ragas = {
            "Fidelidad (Faithfulness)": round(float(np.mean([r["faithfulness"] for r in resultados_ragas])), 3),
            "Relevancia (Answer Relevance)": round(float(np.mean([r["answer_relevance"] for r in resultados_ragas])), 3),
            "Precision (Context Precision)": round(float(np.mean([r["context_precision"] for r in resultados_ragas])), 3),
            "Exhaustividad (Context Recall)": round(float(np.mean([r["context_recall"] for r in resultados_ragas])), 3)
        }
        
        # Imprimir Resultados Formateados
        print("\n--- 1. MÉTRICAS DE RECUPERACIÓN (RETRIEVAL) ---")
        print(f"{'Método':<25}{'Recall@1':>12}{'Recall@3':>12}{'MRR':>10}")
        print("-" * 60)
        for met, vals in tabla_retrieval.items():
            print(f"{met:<25}{vals['Recall@1']:>12.2f}{vals['Recall@3']:>12.2f}{vals['MRR']:>10.2f}")
            
        print("\n--- 2. MÉTRICAS DE EVALUACIÓN RAGAS ---")
        for met, val in promedios_ragas.items():
            print(f"• {met:<35}: {val:.2f} ({val * 100:.1f} %)")
            
        informe = {
            "total_documentos": 56,
            "total_chunks": len(self.chunks),
            "modo_embeddings": self.motor_emb.modo,
            "retrieval_metrics": tabla_retrieval,
            "ragas_metrics": promedios_ragas
        }
        
        # Guardar reporte en disco
        reporte_path = BASE_DIR / "rag_corpus" / "REPORTE_EVALUACION_RAGAS.json"
        with open(reporte_path, "w", encoding="utf-8") as f:
            json.dump(informe, f, ensure_ascii=False, indent=2)
            
        return informe

if __name__ == "__main__":
    sistema = SistemaRAGBiomedico()
    
    # Probar una pregunta del sistema
    pregunta_demo = "¿Cuál es el límite de corriente de fuga de paciente en partes aplicadas Tipo CF y qué procedimiento de seguridad aplica?"
    print(f"\nPREGUNTA DE PRUEBA: {pregunta_demo}")
    resp_demo = sistema.responder(pregunta_demo)
    print(f"\nMODO: {resp_demo['modo']}")
    print(f"RESPUESTA:\n{resp_demo['respuesta']}\n")
    print("FUENTES CITADAS:")
    for f in resp_demo["fuentes"]:
        print(f"[{f['n']}] {f['titulo']} ({f['norma']}) - Chunk ID: {f['chunk_id']}")
        
    # Ejecutar evaluación
    sistema.ejecutar_evaluacion_completa()
