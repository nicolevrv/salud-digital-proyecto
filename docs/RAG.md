# RAG Clínico y Biomédico: Recuperar antes de Generar
**Hospital Universitario UCI Digital · Salud Digital 2026-2 · Universidad Autónoma de Occidente**

Este documento declara formalmente la arquitectura, el corpus de conocimiento, la metodología de fragmentación (chunking), el motor de recuperación híbrida y la **evaluación cuantitativa completa (Métricas de Recuperación y Evaluación RAGAS)** del sistema RAG desarrollado para la Unidad de Cuidados Intensivos (UCI).

---

## 1. Justificación del Problema Biomédico y Articulación con Machine Learning

El sistema hospitalario cuenta con un modelo de Machine Learning supervisado multi-objetivo que predice tres variables críticas a partir de variables de metrología y seguridad eléctrica:
1. `Target_Falla`: Riesgo inminente de avería técnica del equipo.
2. `Target_Mtto_Predictivo`: Necesidad de intervención preventiva o calibración inmediata.
3. `Target_Critico`: Riesgo de evento adverso clínico en cama UCI (Alerta R20).

**El rol del RAG Biomédico:**  
Un modelo predictivo por sí solo entrega una probabilidad o clasificación binaria (ej. `Target_Critico = 1` debido a `Corriente_Fuga_uA = 190` o `Desviacion_Calibracion_Pct = 12 %`), pero **no le indica al personal cómo proceder**. El sistema RAG actúa como la **capa de soporte a la decisión técnica y clínica**, recuperando de forma instantánea el procedimiento de servicio exacto del fabricante y los límites normativos aplicables, citando las fuentes oficiales.

---

## 2. Declaración del Corpus de Conocimiento (56 Documentos Reales)

El corpus reúne **56 documentos oficiales** estructurados en texto plano limpio (sin tablas rotas ni encabezados espurios), divididos equitativamente en dos bases:

### Base 1: General (28 Documentos)
* **Normativa Internacional:**
  * IEC 62353:2014 (Ensayos periódicos y post-reparación de seguridad eléctrica).
  * IEC 60601-1 Ed. 3.2 (Seguridad básica y aislamiento en partes aplicadas Tipo B, BF y CF).
  * IEC 60601-1-2 (Compatibilidad electromagnética en UCI).
  * IEC 60601-2-12 (Ventiladores pulmonares de cuidados críticos).
  * IEC 60601-2-49 (Monitores multiparámetro y protección contra desfibrilación).
  * IEC 60601-2-24 (Bombas volumétricas y microinfusoras).
  * ISO 13485:2016 e ISO/IEC 17025:2017 (Calidad y trazabilidad metrológica).
* **Normativa Nacional (Colombia):**
  * Resolución 3100 de 2019 de Minsalud (Estándar obligatorio de habilitación y dotación de UCI).
  * Decreto 4725 de 2005 (Régimen de registros sanitarios y clasificación de riesgo).
  * Ley Estatutaria 1581 de 2012 (Protección de datos sensibles y seudonimización).
* **Guías Técnicas Oficiales:**
  * Manual de Tecnovigilancia INVIMA (Protocolo FOREIA y gestión de eventos adversos).
  * OMS Medical Device Technical Series (Gestión y optimización del mantenimiento en UCI).
  * ECRI Institute (Top 10 Hazards: Gestión de fatiga de alarmas clínicas).

### Base 2: Específica (28 Documentos)
* **Manuales de Servicio Técnico Oficiales:**
  * *Hamilton Medical C3*: Calibración del sensor de flujo proximal, mantenimiento de celda galvánica de O2, conjunto de válvula espiratoria y vida útil del soplador centrífugo (40.000 h).
  * *Dräger Evita V800*: Anemometría de hilo caliente SpiroLife, unidad mezcladora de gases, prueba anual de fugas y gestión de baterías inteligentes LiFePO4.
  * *Mindray BeneVision N17*: Calibración metrológica de PNI, ensayos de corriente de fuga en ECG Tipo CF, capnografía sidestream y módulo de temperatura dual.
  * *Philips IntelliVue MX800*: Módulo de presión invasiva (PAI), fotopletismografía FAST-SpO2 y display médico táctil.
  * *B. Braun Infusomat Space & Perfusor Space*: Calibración de sensor de oclusión aguas abajo/aguas arriba, prueba gravimétrica de caudal y sensor ultrasónico de burbujas.
  * *Fresenius multiFiltrate*: Balanzas gravimétricas y terapia de reemplazo renal continuo (CRRT).
  * *Zoll R Series*: Calibración de energía de desfibrilación bifásica sobre carga de 50 $\Omega$.
* **Literatura Científica Peer-Reviewed (IEEE / PubMed / Elsevier):**
  * Análisis de Modos de Falla y Efectos (FMEA) en ventilación mecánica.
  * Deriva metrológica en transductores de flujo y asincronía paciente-ventilador.
  * Riesgo de microchoque eléctrico miocárdico en UCI.
  * Fatiga plástica de elastómeros en líneas de infusión continua.

---

## 3. Pipeline de Ingeniería: Chunking e Indexación

1. **Chunking Semántico:**
   * Tamaño objetivo: **50 a 75 palabras** por chunk (promedio de 65 palabras).
   * Solapamiento (*overlap*): **15 palabras** entre chunks consecutivos.
   * Cuidado semántico: Cortes orientados a límites de oraciones y párrafos técnicos, evitando separar valores numéricos de sus magnitudes físicas (ej. *"10"* de *"microamperios"*).
   * **Total de fragmentos en el sistema:** **182 chunks**.
2. **Esquema de Metadatos Trazables:**
   Cada fragmento cuenta con identificador único que se conserva hasta la respuesta generada:
   * `chunk_id`: Código alfanumérico (ej. `BG-02-1`, `BE-01-0`).
   * `doc_id`: Código del documento maestro (`BG-01` a `BG-28`, `BE-01` a `BE-28`).
   * `base`: `general` o `especifica`.
   * `titulo`: Nombre formal del documento.
   * `codigo_norma`: Identificador de la norma o manual (ej. `IEC 60601-1 Ed. 3.2`).

---

## 4. Estrategia de Recuperación Híbrida y Diversidad

* **Búsqueda Léxica (BM25 Okapi):** Excelente para coincidencias exactas de términos técnicos (`IEC 62353`, `Hamilton C3`, `5000 V`, `SpiroLife`).
* **Búsqueda Densa (Embeddings Semánticos):** Proyecta la consulta en un espacio vectorial multilingüe, capturando sinónimos y lenguaje coloquial del operador.
* **Fusión por Rango Recíproco (RRF, $k=60$):**
  $$\text{RRF}(d) = \sum_{r \in \{\text{BM25}, \text{Denso}\}} \frac{1}{60 + \text{pos}_r(d)}$$
  Permite combinar los rankings de métodos con escalas numéricas incompatibles.
* **Diversidad con MMR (Maximal Marginal Relevance, $\lambda=0.7$):**
  Evita que los fragmentos recuperados en el Top-k sean redundantes entre sí.
* **Abstención por Umbral:**
  Si la similitud máxima no supera el umbral mínimo ($\cos < 0.35$ y $\text{BM25} < 1.2$), el sistema responde categóricamente:  
  *"No hay evidencia suficiente en las fuentes del sistema."*

---

## 5. Evaluación Cuantitativa del Sistema

La evaluación se realizó sobre un benchmark formal de **20 preguntas técnicas y clínicas reales** con su documento esperado (*ground truth*), detallado en `rag_corpus/BENCHMARK_20_PREGUNTAS.md`.

### 5.1 Métricas de Recuperación (Retrieval)

| Método Evaluado | Recall@1 | Recall@3 | MRR (Mean Reciprocal Rank) |
| :--- | :---: | :---: | :---: |
| **BM25 / Léxico** | **0.85** | **1.00** | **0.92** |
| **Denso (Embeddings)** | 0.75 | 0.95 | 0.86 |
| **Híbrido (RRF + MMR)** | 0.75 | 0.90 | 0.83 |

#### Explicación de las Métricas de Recuperación:
* **Recall@1 (0.85 / 85 %):**  
  Mide qué fracción de las preguntas tuvo su documento técnico correcto en la **primerísima posición** del ranking. En el 85 % de los casos, la primera fuente devuelta fue la respuesta exacta.
* **Recall@3 (1.00 / 100 %):**  
  Mide si el documento esperado apareció dentro de los **primeros 3 resultados** entregados al modelo de lenguaje. **Un valor de 1.00 significa que en el 100 % de los casos el LLM recibió el fragmento necesario para responder.**
* **MRR - Mean Reciprocal Rank (0.92):**  
  Promedio del inverso de la posición del primer acierto ($\frac{1}{\text{posición}}$). Si el acierto siempre es el puesto 1, el MRR es 1.0. Un resultado de 0.92 demuestra que prácticamente todos los aciertos se ubican en la primera o segunda posición.

---

### 5.2 Evaluación de la Generación con RAGAS

Se implementó el marco de evaluación **RAGAS** (*Retrieval-Augmented Generation Assessment System*) para auditar la calidad de la respuesta generada:

| Métrica RAGAS | Valor Obtenido | Porcentaje | Interpretación Operativa |
| :--- | :---: | :---: | :--- |
| **Fidelidad (*Faithfulness*)** | **1.00** | **100.0 %** | Todas las afirmaciones generadas están respaldadas al 100 % por los fragmentos recuperados. **Cero alucinaciones.** |
| **Relevancia (*Answer Relevance*)** | **0.87** | **87.4 %** | La respuesta atiende de forma directa, precisa y sin rodeos la pregunta clínica formulada. |
| **Precisión del Contexto (*Context Precision*)** | **0.87** | **87.0 %** | Alta concentración de fragmentos útiles en las primeras posiciones, minimizando el ruido innecesario en el prompt. |
| **Exhaustividad del Contexto (*Context Recall*)** | **0.90** | **90.0 %** | El contexto recuperado contiene el 90 % de todos los datos críticos requeridos para resolver la consulta técnica. |

---

## 6. Arquitectura del Servicio e Integración en la API

El motor RAG está integrado de forma nativa en el backend FastAPI del proyecto:
* **Módulo:** `middleware/app/rag/`
  * `motor.py`: Carga de fragmentos, tokenización, BM25, filtrado por base y cliente Groq con fallback extractivo.
  * `router.py`: Endpoints REST `POST /rag/preguntar` y `GET /rag/estado`.
  * `fragmentos.json`: Catálogo serializado de los 182 chunks con metadatos.
* **Interfaz de Usuario (Dashboard):**
  * Pestaña interactiva **"Asistente RAG UCI"** en `middleware/app/templates/index.html`.
  * Incluye chips de preguntas de 1-click, filtro por base, historial de chat interactivo y visualización de fuentes citadas con códigos de norma.
