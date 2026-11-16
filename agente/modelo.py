# agente/modelo.py · Punto único para elegir el modelo (Ollama local, Groq o Simulado)
import os, httpx
from agente.simulado import ModeloSimulado

def ollama_esta_activo(url: str = "http://localhost:11434") -> bool:
    """Verifica si el servidor local de Ollama está escuchando."""
    try:
        r = httpx.get(url, timeout=1.0)
        return r.status_code == 200
    except Exception:
        return False

def crear_modelo(latencia_simulada: float = 0.5):
    """
    Selecciona el modelo según disponibilidad y preferencia:
    1. Si PREFERIR_PROVEEDOR == 'groq' y hay GROQ_API_KEY, usa Groq ultrarrápido.
    2. Si Ollama está activo, usa Ollama local optimizado para CPU.
    3. Si no, usa Groq (si hay key) o el ModeloSimulado.
    """
    preferencia = os.getenv("PREFERIR_PROVEEDOR", "auto").strip().lower()
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    modelo_ollama = os.getenv("MODELO_OLLAMA", "qwen3:4b")
    groq_api_key = os.getenv("GROQ_API_KEY")

    # Si el usuario prefiere Groq para velocidad instantánea
    if preferencia == "groq" and groq_api_key:
        try:
            from langchain_groq import ChatGroq
            return ChatGroq(
                model=os.getenv("MODELO_GROQ", "openai/gpt-oss-120b"),
                temperature=0,
                max_tokens=600,
                max_retries=2,
                timeout=30,
            )
        except Exception:
            pass

    # Intentar Ollama local si está activo (optimizado a num_ctx=2048 para que sea veloz en CPU)
    if ollama_esta_activo(ollama_url) and preferencia != "simulado":
        try:
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=modelo_ollama,
                base_url=ollama_url,
                temperature=0,
                num_predict=400,
                num_ctx=2048,        # reducido de 8192 para velocidad en CPU
                reasoning=False,
            )
        except Exception as e:
            print(f"[agente.modelo] Error al inicializar ChatOllama: {e}. Probando Groq...")

    # Si Ollama no está o falló, usar Groq si hay clave
    if groq_api_key:
        try:
            from langchain_groq import ChatGroq
            return ChatGroq(
                model=os.getenv("MODELO_GROQ", "openai/gpt-oss-120b"),
                temperature=0,
                max_tokens=600,
                max_retries=2,
                timeout=30,
            )
        except Exception:
            pass

    # Fallback al modelo simulado
    return ModeloSimulado(latencia=latencia_simulada)
