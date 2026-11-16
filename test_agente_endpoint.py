# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

sys.path.insert(0, str(BASE_DIR / "middleware"))
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_agente_endpoints():
    print("=" * 70)
    print("VERIFICACIÓN INTEGRAL DE ENDPOINTS DEL AGENTE CLÍNICO")
    print("=" * 70)

    # 1. Login como Médico UCI
    print("\n1. Autenticando como Médico UCI...")
    resp_login = client.post("/login", json={"email": "medico@hospital.com", "password": "med123"})
    assert resp_login.status_code == 200, f"Error login medico: {resp_login.text}"
    token_medico = resp_login.json()["token"]
    headers_medico = {"Authorization": f"Bearer {token_medico}"}
    print("-> Token obtenido con éxito para Médico UCI")

    # 2. Prueba de cálculo directo de IMC (como lo solicitó el usuario en la captura 2)
    # "calcula el IMC de un paciente con 67 kg y 1.95 m"
    print("\n2. Probando cálculo determinista de IMC (67 kg y 1.95 m)...")
    resp_imc = client.post("/agente/interactuar", json={
        "mensaje": "calcula el IMC de un paciente con 67 kg y 1.95 m",
        "paciente_id": None
    }, headers=headers_medico)
    assert resp_imc.status_code == 200, f"Error: {resp_imc.text}"
    data_imc = resp_imc.json()
    print("Herramientas usadas:", data_imc["herramientas_usadas"])
    print("Respuesta:\n", data_imc["respuesta"])
    assert "calcular_imc" in data_imc["herramientas_usadas"]
    assert "17.62" in data_imc["respuesta"] or "17.6" in data_imc["respuesta"] or "Bajo peso" in data_imc["respuesta"]

    # 3. Prueba de cálculo de grasa corporal Deurenberg
    print("\n3. Probando estimación de grasa corporal Deurenberg...")
    resp_gc = client.post("/agente/interactuar", json={
        "mensaje": "calcula la grasa corporal para 78.5 kg, 1.58 m, 58 años, mujer",
        "paciente_id": 101
    }, headers=headers_medico)
    assert resp_gc.status_code == 200
    data_gc = resp_gc.json()
    print("Herramientas usadas:", data_gc["herramientas_usadas"])
    print("Respuesta:\n", data_gc["respuesta"])
    assert "calcular_grasa_corporal" in data_gc["herramientas_usadas"]

    # 4. Prueba de generación y descarga física de archivo Word (.docx)
    print("\n4. Probando generación y descarga de archivo Word (.docx)...")
    resp_word = client.post("/agente/interactuar", json={
        "mensaje": "genera el reporte en formato word para descargar",
        "paciente_id": 101
    }, headers=headers_medico)
    assert resp_word.status_code == 200
    data_word = resp_word.json()
    print("Archivos generados:", data_word["archivos"])
    assert len(data_word["archivos"]) > 0
    archivo_word = data_word["archivos"][0]["nombre"]
    print(f"Descargando archivo {archivo_word}...")
    resp_descarga = client.get(f"/agente/descargar/{archivo_word}")
    assert resp_descarga.status_code == 200
    assert len(resp_descarga.content) > 1000
    print(f"-> Archivo Word descargado exitosamente ({len(resp_descarga.content)} bytes)")

    # 5. Prueba de generación y descarga de XML
    print("\n5. Probando generación y descarga de archivo XML...")
    resp_xml = client.post("/agente/interactuar", json={
        "mensaje": "exporta los datos en formato xml interoperable para descargar",
        "paciente_id": 101
    }, headers=headers_medico)
    assert resp_xml.status_code == 200
    data_xml = resp_xml.json()
    assert len(data_xml["archivos"]) > 0
    archivo_xml = data_xml["archivos"][0]["nombre"]
    resp_descarga_xml = client.get(f"/agente/descargar/{archivo_xml}")
    assert resp_descarga_xml.status_code == 200
    assert b"ReporteClinicoUCI" in resp_descarga_xml.content
    print(f"-> Archivo XML interoperable verificado exitosamente ({len(resp_descarga_xml.content)} bytes)")

    # 6. Prueba RBAC: El rol contable debe recibir 403 Forbidden
    print("\n6. Probando restricción RBAC para rol Contable...")
    resp_login_contable = client.post("/login", json={"email": "contable@hospital.com", "password": "contable123"})
    assert resp_login_contable.status_code == 200, f"Error login contable: {resp_login_contable.text}"
    token_contable = resp_login_contable.json()["token"]
    headers_contable = {"Authorization": f"Bearer {token_contable}"}
    resp_403 = client.post("/agente/interactuar", json={
        "mensaje": "calcula el IMC de 70 kg y 1.70 m"
    }, headers=headers_contable)
    assert resp_403.status_code == 403, f"Esperaba 403 y recibí {resp_403.status_code}"
    print("-> RBAC verificado: 403 Forbidden para Contable UCI")

    print("\n" + "=" * 70)
    print("¡TODAS LAS PRUEBAS DEL AGENTE CLÍNICO PASARON AL 100%!")
    print("=" * 70)

if __name__ == "__main__":
    test_agente_endpoints()
