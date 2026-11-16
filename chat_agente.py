# -*- coding: utf-8 -*-
"""
Chat Interactivo del Agente Clínico y Metrología UCI
Salud Digital 2026-2 · Universidad Autónoma de Occidente
Ejecutar en la terminal de VS Code: python chat_agente.py
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agente.herramientas import ContextoClinico, seudonimo, formatear_salida, DIR_EXPORTADOS
from agente.harness import construir_agente
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

def iniciar_chat():
    print("=" * 70)
    print("🏥 AGENTE CLÍNICO Y METROLOGÍA UCI - HOSPITAL UNIVERSITARIO UAO")
    print("=" * 70)
    print("Cargando arnés con Ollama local (qwen3:4b)...")

    memoria_hilos = InMemorySaver()
    memoria_larga = InMemoryStore()
    agente = construir_agente(checkpointer=memoria_hilos, store=memoria_larga, latencia_simulada=0.1)

    # Contexto clínico por defecto: Médico/Biomédico con Paciente 101 abierto
    ctx = ContextoClinico(
        usuario="estudiante.uao@uao.edu.co",
        rol="medico",
        paciente_id=101,
        paciente_alias=seudonimo(101)
    )

    hilo = {"configurable": {"thread_id": "sesion_interactiva_vscode"}}

    print("\n✅ Agente listo. Paciente activo:", ctx.paciente_alias)
    print("Escribe tus consultas clínicas o de metrología. Escribe 'salir' para terminar.\n")
    print("Ejemplos que puedes probar:")
    print("1. Calcula el IMC de alguien con 82 kg y 1.72 m")
    print("2. Calcula la grasa corporal de un paciente hombre de 45 años con 85 kg y 1.76 m")
    print("3. ¿Cuáles son los datos de este paciente y formatea el reporte en tabla?")
    print("4. ¿Cuál es el límite de seguridad eléctrica según la norma IEC 62353?")
    print("-" * 70)

    ultima_respuesta = ""

    while True:
        try:
            pregunta = input("\n🧑‍⚕️ Tú: ").strip()
            if not pregunta:
                continue
            if pregunta.lower() in ["salir", "exit", "quit", "bye"]:
                print("\nCerrando sesión del agente. ¡Hasta luego!")
                break

            # Atajo directo: si el usuario escribe solo "word", "descargar" o "xml"
            if pregunta.lower() in ["word", "descargar word", "descarga word", "descargar", "exportar word"] and ultima_respuesta:
                print("\n📄 Exportando automáticamente la última respuesta a Word (.docx)...")
                res_export = formatear_salida.invoke({"datos": ultima_respuesta, "formato": "word"})
                print(f"\n{res_export}")
                print("\n" + "-" * 70)
                continue
            elif pregunta.lower() in ["xml", "descargar xml", "exportar xml"] and ultima_respuesta:
                print("\n📄 Exportando automáticamente la última respuesta a XML (.xml)...")
                res_export = formatear_salida.invoke({"datos": ultima_respuesta, "formato": "xml"})
                print(f"\n{res_export}")
                print("\n" + "-" * 70)
                continue

            print("\n🤖 Agente pensando y evaluando herramientas...")
            salida = agente.invoke(
                {"messages": [{"role": "user", "content": pregunta}]},
                hilo,
                context=ctx
            )
            raw_content = salida["messages"][-1].content
            
            # Limpiar etiquetas <think>...</think> o monólogo interno
            import re
            respuesta_limpia = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.S).strip()
            
            # Si el modelo dejó el texto de pensamiento sin tags, extraer la parte en español
            if not respuesta_limpia or "Okay, let me" in respuesta_limpia:
                partes = raw_content.split("</think>")
                if len(partes) > 1:
                    respuesta_limpia = partes[-1].strip()
                else:
                    lineas = [l for l in raw_content.split("\n") if not l.strip().startswith(("Okay", "Let me", "Wait,", "First,", "The response"))]
                    respuesta_limpia = "\n".join(lineas).strip() if lineas else raw_content

            contenido_final = respuesta_limpia if respuesta_limpia else raw_content
            ultima_respuesta = contenido_final

            print("\n📋 Respuesta del Agente:\n")
            print(contenido_final)

            # GARANTÍA DE DESCARGA: Si el usuario pidió Word/descarga/XML o el modelo mencionó exportar a Word,
            # pero no se generó el archivo en el texto, ejecutamos la tool físicamente de inmediato
            quiere_word = any(w in pregunta.lower() for w in ["word", "descarg", "guard", "docx"]) or "exportarse a word" in contenido_final.lower()
            quiere_xml = any(x in pregunta.lower() for x in ["xml", "exportar xml"])

            ya_descargo = "[EXITO]" in contenido_final

            if quiere_word and not ya_descargo:
                print("\n" + "=" * 50)
                print("💾 Generando y guardando archivo Word (.docx) en disco...")
                res_doc = formatear_salida.invoke({"datos": contenido_final, "formato": "word"})
                print(res_doc)
                print("=" * 50)
            elif quiere_xml and not ya_descargo:
                print("\n" + "=" * 50)
                print("💾 Generando y guardando archivo XML (.xml) en disco...")
                res_xml = formatear_salida.invoke({"datos": contenido_final, "formato": "xml"})
                print(res_xml)
                print("=" * 50)

            print("\n" + "-" * 70)

        except KeyboardInterrupt:
            print("\nSesión finalizada.")
            break
        except Exception as e:
            print(f"\n❌ Error al procesar: {e}")

if __name__ == "__main__":
    iniciar_chat()
