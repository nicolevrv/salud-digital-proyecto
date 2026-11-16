# -*- coding: utf-8 -*-
"""
Script de Demostración y Verificación de la Asignación Semana 13
Salud Digital 2026-2 · Universidad Autónoma de Occidente
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

from agente.herramientas import (
    ContextoClinico, seudonimo,
    calcular_imc, calcular_grasa_corporal, formatear_salida,
    consultar_paciente, buscar_guias, proponer_remision
)
from agente.harness import construir_agente, AUDITORIA
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

def probar_herramientas_unitarias():
    print("=" * 70)
    print("1. VERIFICACIÓN DE LAS HERRAMIENTAS ASIGNADAS")
    print("=" * 70)

    # Tool 1: IMC
    print("\n--- [TOOL 1] calcular_imc ---")
    res_imc = calcular_imc.invoke({"peso_kg": 78.5, "talla_m": 1.58})
    print(res_imc)

    # Tool 2: Grasa Corporal (Deurenberg)
    print("\n--- [TOOL 2] calcular_grasa_corporal (Fórmula de Deurenberg) ---")
    res_gc = calcular_grasa_corporal.invoke({"peso_kg": 78.5, "talla_m": 1.58, "edad": 58, "sexo": "F"})
    print(res_gc)

    # Tool 3: Formatear salida (Tabla y XML)
    print("\n--- [TOOL 3] formatear_salida (Formato Tabla) ---")
    res_tabla = formatear_salida.invoke({"datos": "IMC 31.4 (Obesidad I), Grasa 40.5% (Elevada)", "formato": "tabla"})
    print(res_tabla)

    print("\n--- [TOOL 3] formatear_salida (Formato XML interoperable) ---")
    res_xml = formatear_salida.invoke({"datos": "IMC 31.4 (Obesidad I), Grasa 40.5% (Elevada)", "formato": "xml"})
    print(res_xml)

def probar_flujo_agente():
    print("\n" + "=" * 70)
    print("2. PRUEBA DEL FLUJO COMPLETO DEL AGENTE CLÍNICO Y HARNESS")
    print("=" * 70)

    memoria_hilos = InMemorySaver()
    memoria_larga = InMemoryStore()
    agente = construir_agente(checkpointer=memoria_hilos, store=memoria_larga, latencia_simulada=0.1)

    ctx_paciente_101 = ContextoClinico(
        usuario="dra.rios@uao.edu.co",
        rol="medico",
        paciente_id=101,
        paciente_alias=seudonimo(101)
    )

    consulta = (
        "Calcula el IMC y porcentaje de grasa corporal de un paciente de 78.5 kg, 1.58 m, "
        "58 años, mujer, y formatea el resultado en una tabla."
    )

    hilo = {"configurable": {"thread_id": "dra_rios:caso-1"}}
    salida = agente.invoke({"messages": [{"role": "user", "content": consulta}]}, hilo, context=ctx_paciente_101)

    print(f"\nPregunta del usuario:\n«{consulta}»\n")
    print("Respuesta generada por el agente:\n")
    print(salida["messages"][-1].content)

    print("\n--- Registros de Auditoría Generados (R13) ---")
    for a in AUDITORIA[-5:]:
        print(f"[{a['fecha']}] Herramienta: {a['herramienta']:24s} | Paciente: {a['paciente']} | Args: {a['args']}")

if __name__ == "__main__":
    probar_herramientas_unitarias()
    probar_flujo_agente()
