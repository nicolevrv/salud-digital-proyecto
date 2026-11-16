# 🏥 Agente Clínico, Metrología UCI y Harness (Semana 13)
**Salud Digital 2026-2 · Universidad Autónoma de Occidente**  
**Docente:** Dr. Carlos Andrés Ferro Sánchez  
**Equipo:** Salud Digital & Equipos Biomédicos UCI

---

## 📌 ¿Qué incluye este paquete?

1. **Las 3 Tools obligatorias de la tarea:**
   * `calcular_imc`: Cálculo exacto de IMC y clasificación según la OMS.
   * `calcular_grasa_corporal`: Estimación de %GC con la fórmula clínica de Deurenberg et al.
   * `formatear_salida`: Conversión y exportación automática a **Tabla Markdown**, **XML interoperable** o documento descargable en **Word (.docx)**.
2. **Conexión con el RAG:** Consulta de guías clínicas y normas de metrología UCI (IEC 62353, Res. 3100 de 2019, ventiladores y bombas).
3. **El Harness Completo:**
   * Prompt dinámico por capas (`agente/prompts/sistema.md`).
   * Control de tokens y prevención de bucles infinitos (`ModelCallLimitMiddleware` con tope de 8 pasos y salida a 600 tokens).
   * Ventana de contexto recortada a 12 mensajes (`VentanaContexto`).
   * Aprobación humana (*Human-in-the-loop*) para remisiones médicas.
   * Auditoría de trazabilidad (`Auditoria`) con seudónimos seguros (R12 / R13).
4. **Soporte Híbrido:** Funciona tanto con **Ollama local** (sin internet ni costos) como con **Groq API** (instantáneo en la nube).

---

## 🚀 Guía Rápida para ponerlo a funcionar en tu PC

### Paso 1: Instalar las dependencias de Python
Abre una terminal en esta carpeta (con tu entorno virtual activado si tienes uno) y ejecuta:
```bash
pip install -r requirements_agente.txt
```

---

### Paso 2: Configurar el archivo `.env`
Copia el archivo `.env.example` y renómbralo a `.env`:
* **Si vas a usar Ollama local (como pidió el profesor):**
  Asegúrate de tener Ollama instalado y corriendo (`ollama run qwen3:4b` o `ollama run llama3.2:3b`), y en el `.env` dejas:
  ```env
  OLLAMA_URL=http://localhost:11434
  MODELO_OLLAMA=llama3.2:3b
  PREFERIR_PROVEEDOR=auto
  LLAVE_SEUDONIMO=llave-secreta-salud-digital-uao-2026
  ```

* **Si vas a usar Groq (ultra rápido en 1 segundo):**
  Crea tu clave gratis en [console.groq.com/keys](https://console.groq.com/keys) y en tu `.env` colocas:
  ```env
  GROQ_API_KEY=gsk_tu_clave_aqui
  PREFERIR_PROVEEDOR=groq
  MODELO_GROQ=openai/gpt-oss-120b
  LLAVE_SEUDONIMO=llave-secreta-salud-digital-uao-2026
  ```

---

### Paso 3: Probar que todo funciona

#### Opción A: Chat Interactivo en vivo
Ejecuta en tu terminal:
```bash
python chat_agente.py
```
Puedes hacerle preguntas como:
* *"Calcula el IMC de 70 kg y 1.65 m y dame el resultado en una tabla"*
* *"Calcula la grasa corporal de un hombre de 45 años con 85 kg y 1.76 m"*
* *"Descarga el reporte en Word"* *(guardará el archivo `.docx` en la carpeta `reportes_exportados/`)*
* *"¿Cuál es el límite de fuga a tierra según la norma IEC 62353?"*

#### Opción B: Verificación Unitaria de las 3 Tools
Ejecuta:
```bash
python probar_agente_semana13.py
```
Verifica en pantalla la ejecución determinista de las 3 herramientas y el registro de auditoría.

---

## 📁 Estructura del Proyecto
```
├── agente/
│   ├── herramientas.py       # Las 3 tools (IMC, Grasa, Formato Word/XML/Tabla) y RAG
│   ├── harness.py            # Orquestador con control de tokens, auditoría y loops
│   ├── modelo.py             # Switch inteligente entre Ollama y Groq
│   ├── rag_conector.py       # Conector con la base documental y normas UCI
│   ├── prompt.py / sistema.md # Prompt por capas
│   ├── simulado.py           # Modo de práctica sin internet
│   └── skills/               # Procedimientos de remisión y metrología
├── reportes_exportados/      # Carpeta donde se guardan los archivos Word y XML reales
├── chat_agente.py            # Chat interactivo para interactuar con el agente
├── probar_agente_semana13.py # Pruebas unitarias de las 3 tools
├── requirements_agente.txt   # Librerías necesarias
└── .env.example              # Plantilla de configuración
```
