# -*- coding: utf-8 -*-
"""
Suite Exhaustiva de Pruebas Pre-Vuelo para Render & Local
Salud Digital 2026-2 · Universidad Autónoma de Occidente
Verifica:
1. Estructura y sintaxis de index.html (Scripts limpios, sin contaminación).
2. Integridad de listeners y funciones críticas de Login.
3. Autenticación exitosa de los 6 roles institucionales.
4. Respuesta y payload del endpoint /health.
5. Ejecución del Asistente Clínico (cálculo de IMC y %GC).
6. Generación y descarga física de archivos (.docx y .xml).
7. Restricción RBAC de seguridad (Contable UCI -> 403 Forbidden).
8. Consulta al motor RAG documental.
9. Verificación de salud y conectividad en vivo contra el despliegue de Render.
"""
import sys
import os
import re
from pathlib import Path
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

sys.path.insert(0, str(BASE_DIR / "middleware"))
sys.path.insert(0, str(BASE_DIR))

import httpx
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def banner(titulo):
    print("\n" + "=" * 75)
    print(f" {titulo}")
    print("=" * 75)

def probar_html_y_scripts():
    banner("1. VALIDACIÓN ESTRUCTURAL DE INDEX.HTML Y JAVASCRIPT")
    index_file = BASE_DIR / "middleware" / "app" / "templates" / "index.html"
    assert index_file.exists(), "index.html no existe"
    html = index_file.read_text(encoding="utf-8")

    # A. Verificar los 4 bloques script
    scripts = re.findall(r'<script\b([^>]*)>([\s\S]*?)</script>', html, re.I)
    print(f"-> Bloques <script> encontrados: {len(scripts)}")
    assert len(scripts) == 4, f"Se esperaban 4 bloques de script y hay {len(scripts)}"

    # Script 1 y 2 deben ser externos limpios (longitud 0)
    assert len(scripts[0][1].strip()) == 0, f"Script 1 contaminado: {len(scripts[0][1])}"
    assert 'src="https://cdn.tailwindcss.com"' in scripts[0][0], "Script 1 debe ser tailwindcdn"
    print("  [OK] Script 1: Tailwind CDN limpio sin inyecciones.")

    assert len(scripts[1][1].strip()) == 0, f"Script 2 contaminado: {len(scripts[1][1])}"
    assert 'src="https://cdn.jsdelivr.net/npm/chart.js"' in scripts[1][0], "Script 2 debe ser chart.js"
    print("  [OK] Script 2: Chart.js CDN limpio sin inyecciones.")

    # Script 3 debe ser tailwind.config
    assert "tailwind.config" in scripts[2][1], "Script 3 debe contener tailwind.config"
    print(f"  [OK] Script 3: tailwind.config intacto ({len(scripts[2][1].strip())} bytes).")

    # Script 4 debe ser el script de la app
    code = scripts[3][1]
    assert len(code) > 200000, f"Script 4 demasiado corto: {len(code)}"
    print(f"  [OK] Script 4: Aplicación hospitalaria ({len(code)} bytes).")

    # B. Verificar balance de delimitadores en script 4
    # (Removiendo strings para un chequeo limpio)
    # Contar llaves {}
    curly_open = code.count('{')
    curly_close = code.count('}')
    print(f"  -> Llaves de bloque: {curly_open} abiertas vs {curly_close} cerradas")
    assert curly_open == curly_close, f"Desbalance de llaves: {curly_open} != {curly_close}"
    print("  [OK] Balance sintáctico de llaves {} perfecto.")

    # C. Verificar que las funciones críticas existan en el script
    funciones_criticas = [
        "function ejecutarLogin",
        "function ejecutarLoginPortal",
        "function cargarPerfilDemo",
        "function checkHealthStatus",
        "function actualizarInterfazPerfil",
        "function cambiarTab",
        "function ejecutarConsultaAgente",
        "function decidirAccionAgente",
        "function seleccionarAccionRapidaAgente",
        "function cargarReportesRecientesAgente",
        "function ejecutarConsultaRAG"
    ]
    for fn in funciones_criticas:
        assert fn in code, f"Falta la función {fn} en el script"
        print(f"  [OK] Función '{fn}' declarada.")

    # D. Verificar que NO exista ninguna tarjeta ni botón de especialista
    assert "especialista@hospital.com" not in html, "Aún hay referencias a especialista@hospital.com en el HTML"
    print("  [OK] Perfil especialista completamente erradicado de la interfaz.")

def probar_endpoint_health():
    banner("2. PRUEBA DE SALUD (/health) Y BASE DE DATOS")
    r = client.get("/health")
    assert r.status_code == 200, f"Error en /health: {r.status_code}"
    data = r.json()
    print("-> Respuesta de /health:", data)
    assert data["status"] == "online"
    assert data["database"]["connected"] is True, f"Base de datos desconectada: {data['database']}"
    assert data["database"]["tables_found"] >= 20, f"Tablas insuficientes: {data['database']['tables_found']}"
    print(f"  [OK] Estado online con {data['database']['provider']} ({data['database']['tables_found']} tablas).")

def probar_autenticacion_roles():
    banner("3. PRUEBA DE LOGIN PARA TODOS LOS ROLES INSTITUCIONALES")
    usuarios_demo = [
        ("Admin Biomédico", "admin.biomedico@hospital.com", "admin123"),
        ("Médico UCI", "medico@hospital.com", "med123"),
        ("Ingeniero Preventivo", "ing.preventivo@hospital.com", "prev123"),
        ("Ingeniero Correctivo", "ing.correctivo@hospital.com", "corr123"),
        ("Contable UCI", "contable@hospital.com", "contable123"),
        ("Servicio Telemetría", "servicio.iot@hospital.com", "service123"),
    ]

    tokens = {}
    for rol_nombre, email, password in usuarios_demo:
        resp = client.post("/login", json={"email": email, "password": password})
        assert resp.status_code == 200, f"Fallo login de {rol_nombre} ({email}): {resp.text}"
        payload = resp.json()
        assert "token" in payload, f"Sin token para {email}"
        assert payload["usuario"]["email"] == email
        tokens[email] = payload["token"]
        print(f"  [OK] {rol_nombre:22s} ({email:28s}) -> Token generado correctamente.")

    # Prueba de login fallido (debe rechazar con 401)
    resp_fallido = client.post("/login", json={"email": "medico@hospital.com", "password": "clave_equivocada"})
    assert resp_fallido.status_code == 401, "Se esperaba 401 ante clave equivocada"
    print("  [OK] Intento fallido con contraseña incorrecta rechazado con HTTP 401.")

    return tokens

def probar_agente_clinico_y_descargas(token_medico, token_contable):
    banner("4. PRUEBA DEL ASISTENTE CLÍNICO Y GENERACIÓN DE REPORTES")
    headers_medico = {"Authorization": f"Bearer {token_medico}"}
    headers_contable = {"Authorization": f"Bearer {token_contable}"}

    # A. Consulta de IMC con el prompt exacto del usuario
    pregunta_imc = "calcula el IMC de un paciente con 67 kg y 1.95 m"
    print(f"-> Enviando consulta: «{pregunta_imc}»")
    resp_imc = client.post("/agente/interactuar", json={"mensaje": pregunta_imc}, headers=headers_medico)
    assert resp_imc.status_code == 200, f"Error: {resp_imc.text}"
    data_imc = resp_imc.json()
    print("  Herramientas ejecutadas:", data_imc["herramientas_usadas"])
    print("  Respuesta resumida:\n ", data_imc["respuesta"].replace("\n", "\n  "))
    assert "calcular_imc" in data_imc["herramientas_usadas"]
    assert "17.62" in data_imc["respuesta"]
    assert "Bajo peso" in data_imc["respuesta"]
    print("  [OK] Cálculo determinista de IMC verificado (17.62 kg/m² - Bajo peso).")

    # B. Consulta de Grasa Corporal Deurenberg
    pregunta_gc = "calcula la grasa corporal para 78.5 kg, 1.58 m, 58 años, mujer"
    resp_gc = client.post("/agente/interactuar", json={"mensaje": pregunta_gc}, headers=headers_medico)
    assert resp_gc.status_code == 200
    data_gc = resp_gc.json()
    assert "calcular_grasa_corporal" in data_gc["herramientas_usadas"]
    assert "45.7" in data_gc["respuesta"]
    print("  [OK] Cálculo determinista de Deurenberg verificado (45.7% %GC).")

    # C. Generación de reporte Word descargable
    resp_doc = client.post("/agente/interactuar", json={
        "mensaje": "genera el reporte en formato word para descargar"
    }, headers=headers_medico)
    assert resp_doc.status_code == 200
    data_doc = resp_doc.json()
    assert len(data_doc["archivos"]) > 0, "No se generó el archivo Word"
    arch_nombre = data_doc["archivos"][0]["nombre"]
    print(f"  Archivo Word generado: {arch_nombre}")

    # Descarga física del archivo Word
    resp_descarga = client.get(f"/agente/descargar/{arch_nombre}")
    assert resp_descarga.status_code == 200
    assert len(resp_descarga.content) > 10000, "Archivo Word descargado incompleto"
    print(f"  [OK] Descarga de archivo Word validada ({len(resp_descarga.content)} bytes recibidos).")

    # D. Generación de reporte XML interoperable
    resp_xml = client.post("/agente/interactuar", json={
        "mensaje": "exporta los datos en formato xml para descargar"
    }, headers=headers_medico)
    assert resp_xml.status_code == 200
    data_xml = resp_xml.json()
    assert len(data_xml["archivos"]) > 0
    arch_xml_nombre = data_xml["archivos"][0]["nombre"]
    resp_descarga_xml = client.get(f"/agente/descargar/{arch_xml_nombre}")
    assert resp_descarga_xml.status_code == 200
    assert b"<ReporteClinicoUCI" in resp_descarga_xml.content
    print(f"  [OK] Descarga de archivo XML interoperable validada ({len(resp_descarga_xml.content)} bytes).")

    # E. Restricción RBAC: Contable no puede usar el agente clínico
    resp_403 = client.post("/agente/interactuar", json={"mensaje": "calcula el IMC"}, headers=headers_contable)
    assert resp_403.status_code == 403
    print("  [OK] Control de acceso RBAC: Rol Contable bloqueado del Agente Clínico con HTTP 403.")

def probar_rag_normativo(token_medico):
    banner("5. PRUEBA DEL REPOSITORIO NORMATIVO RAG (SEPARADO DEL AGENTE)")
    headers_medico = {"Authorization": f"Bearer {token_medico}"}

    pregunta_norma = "¿Cuál es el límite máximo de corriente de fuga de paciente en partes aplicadas Tipo CF según la norma IEC 60601-1?"
    resp_rag = client.post("/rag/preguntar", json={"pregunta": pregunta_norma, "k": 3}, headers=headers_medico)
    assert resp_rag.status_code == 200
    data_rag = resp_rag.json()
    print("  Respuesta RAG:\n ", data_rag["respuesta"][:180], "...")
    assert len(data_rag["fuentes"]) > 0
    print(f"  [OK] RAG respondió citando {len(data_rag['fuentes'])} fuentes técnicas oficiales.")

def probar_render_en_vivo():
    banner("6. VERIFICACIÓN EN VIVO DEL SERVICIO DESPLEGADO EN RENDER")
    url_render = "https://salud-digital-proyecto.onrender.com"
    print(f"-> Conectando a {url_render}...")
    try:
        r_health = httpx.get(f"{url_render}/health", timeout=12.0)
        print("  Status /health en Render:", r_health.status_code)
        assert r_health.status_code == 200
        data_health = r_health.json()
        print("  Info de BD en Render:", data_health.get("database"))
        assert data_health["database"]["connected"] is True
        print("  [OK] Render conectado en vivo a Neon PostgreSQL.")

        # Probar login en Render
        r_login = httpx.post(
            f"{url_render}/login",
            json={"email": "admin.biomedico@hospital.com", "password": "admin123"},
            timeout=12.0
        )
        print("  Status /login en Render:", r_login.status_code)
        assert r_login.status_code == 200
        token_render = r_login.json()["token"]
        assert token_render
        print("  [OK] Login en producción autentica y devuelve token JWT con éxito.")

        # Probar el nuevo endpoint del Agente en Render
        r_agente = httpx.post(
            f"{url_render}/agente/interactuar",
            json={"mensaje": "calcula el IMC de 67 kg y 1.95 m"},
            headers={"Authorization": f"Bearer {token_render}"},
            timeout=15.0
        )
        print("  Status /agente/interactuar en Render:", r_agente.status_code)
        assert r_agente.status_code == 200
        data_agente = r_agente.json()
        assert "17.62" in data_agente["respuesta"]
        print("  [OK] Agente Clínico en producción ejecutó @tool calcular_imc determinista.")

    except Exception as e:
        print("  Aviso en conexión Render:", type(e), e)
        raise

if __name__ == "__main__":
    probar_html_y_scripts()
    probar_endpoint_health()
    tokens = probar_autenticacion_roles()
    probar_agente_clinico_y_descargas(tokens["medico@hospital.com"], tokens["contable@hospital.com"])
    probar_rag_normativo(tokens["medico@hospital.com"])
    probar_render_en_vivo()

    banner("¡ÉXITO TOTAL! TODAS LAS PRUEBAS PRE-VUELO PASARON AL 100%")
