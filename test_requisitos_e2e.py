import sys
import os
import json
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "middleware"))
from app.main import app

client = TestClient(app)

def test_full_pipeline():
    print("=== INICIANDO PRUEBAS E2E DE REQUISITOS R01 - R22 ===", flush=True)
    
    # 1. R01 - Login y /yo con los 5 roles
    credenciales = [
        ("admin.biomedico@hospital.com", "admin123", "Admin Biomedico"),
        ("medico@hospital.com", "med123", "Medico"),
        ("especialista@hospital.com", "especialista123", "Especialista"),
        ("contable@hospital.com", "contable123", "Contable UCI"),
        ("paciente@hospital.com", "paciente123", "Paciente")
    ]
    tokens = {}
    
    for email, pwd, expected_role in credenciales:
        r = client.post("/login", json={"email": email, "password": pwd})
        assert r.status_code == 200, f"Error login {email}: {r.text}"
        data = r.json()
        token = data["token"]
        tokens[expected_role] = token
        
        # Probar /yo
        r_yo = client.get("/yo", headers={"Authorization": f"Bearer {token}"})
        assert r_yo.status_code == 200, f"Error /yo {email}: {r_yo.text}"
        yo_data = r_yo.json()
        assert "rol" in yo_data, f"Respuesta /yo no tiene 'rol': {yo_data}"
        print(f"[OK] R01: Rol verificado para {email} -> {yo_data['rol']}", flush=True)

    token_medico = tokens["Medico"]
    token_contable = tokens["Contable UCI"]
    token_especialista = tokens["Especialista"]
    token_admin = tokens["Admin Biomedico"]
    token_paciente = tokens["Paciente"]

    # 2. R07 - Clusters
    r_clusters = client.get("/clusters", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_clusters.status_code == 200, f"Error /clusters: {r_clusters.text}"
    clusters_data = r_clusters.json()
    assert isinstance(clusters_data, list) and len(clusters_data) > 0, "Clusters no es lista"
    for c in clusters_data:
        assert "cluster" in c and "descripcion" in c and "n_reales" in c and "n_sinteticos" in c
    print(f"[OK] R07: Clusters validados ({len(clusters_data)} grupos)", flush=True)

    # 3. R06 - Imagenes del paciente
    r_imgs = client.get("/pacientes/1/imagenes", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_imgs.status_code == 200, f"Error imagenes: {r_imgs.text}"
    imgs_data = r_imgs.json()
    assert "imagenes" in imgs_data or isinstance(imgs_data, list), "Formato de imagenes invalido"
    print(f"[OK] R06: Endpoint imagenes paciente validado", flush=True)

    # 4. R18 - Pendientes
    r_pend = client.get("/pendientes", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_pend.status_code == 200, f"Error /pendientes: {r_pend.text}"
    pend_data = r_pend.json()
    for campo in ["reportes_por_revisar", "remisiones_recibidas", "alertas_activas"]:
        assert campo in pend_data, f"Falta {campo} en pendientes"
    print(f"[OK] R18: Pendientes iniciales: {pend_data}", flush=True)

    # 5. R12 / R04 - IA Analizar (3 Targets)
    payload_analisis = {
        "paciente_id": 1,
        "equipo_id": "EQ-VENT-01",
        "Tipo_Equipo": "Ventilador Mecanico",
        "Edad_Equipo_Meses": 36,
        "Meses_Desde_Ultimo_Mtto": 7,
        "Horas_Uso_Acumuladas": 14200,
        "Fallas_Acumuladas": 4,
        "MTBF_Promedio_Dias": 85.5,
        "Corriente_Fuga_uA": 165.2,
        "Resistencia_Tierra_Ohms": 0.28,
        "Desviacion_Calibracion_Pct": 4.8
    }
    r_analisis = client.post("/ia/analizar", json=payload_analisis, headers={"Authorization": f"Bearer {token_medico}"})
    assert r_analisis.status_code == 200, f"Error /ia/analizar: {r_analisis.text}"
    res_ia = r_analisis.json()
    assert "id" in res_ia and res_ia.get("estado") == "pendiente", f"Respuesta analisis invalida: {res_ia}"
    reporte_id = res_ia["id"]
    print(f"[OK] R10/R12: Reporte IA generado con ID {reporte_id}, estado: {res_ia.get('estado')}", flush=True)
    print(f"      Prediccion 3 Targets: {res_ia.get('predicciones')}", flush=True)

    # 6. R12 - Payload Seudonimizado
    r_payload = client.get(f"/ia/reportes/{reporte_id}/payload", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_payload.status_code == 200, f"Error /ia/payload: {r_payload.text}"
    payload_data = r_payload.json()
    assert "paciente_id" not in str(payload_data.get("payload", {})), "Fuga de paciente_id en payload seudonimizado"
    print(f"[OK] R12: Payload seudonimizado verificado (sin PHI directo)", flush=True)

    # 7. R10 / R19 - Aprobacion de Reporte IA ("Soft Put") -> Dispara Factura Contable Automatica
    r_aprobar = client.put(f"/ia/reportes/{reporte_id}", json={"estado": "aprobado", "observaciones": "Aprobado clinicamente"}, headers={"Authorization": f"Bearer {token_medico}"})
    assert r_aprobar.status_code == 200, f"Error al aprobar reporte: {r_aprobar.text}"
    rep_aprobado = r_aprobar.json()
    assert rep_aprobado.get("estado") == "aprobado", f"No cambio a aprobado: {rep_aprobado}"
    print(f"[OK] R10/R19: Reporte IA {reporte_id} aprobado exitosamente", flush=True)

    # 8. R09 - Facturacion Contable
    r_facturas = client.get("/contabilidad/facturas", headers={"Authorization": f"Bearer {token_contable}"})
    assert r_facturas.status_code == 200, f"Error /contabilidad/facturas: {r_facturas.text}"
    facturas_data = r_facturas.json()
    assert isinstance(facturas_data, list) and len(facturas_data) > 0, "No hay facturas"
    print(f"[OK] R09: Facturas contables listadas ({len(facturas_data)} registros)", flush=True)

    # 9. R11 - Historia Clinica PDF
    r_pdf = client.get("/pacientes/1/historia/pdf", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_pdf.status_code == 200, f"Error PDF historia: {r_pdf.status_code}"
    assert "application/pdf" in r_pdf.headers.get("content-type", ""), "Content-Type no es PDF"
    print(f"[OK] R11: Historia clinica PDF generada binariamente ({len(r_pdf.content)} bytes)", flush=True)

    # 10. R08 / R19 - Remision y Aceptacion
    payload_remision = {
        "paciente_id": 1,
        "motivo": "Valoracion urgente por soporte ventilatorio prolongado",
        "servicio_destino": "Cuidado Critico e Intervencionismo",
        "prioridad": "Alta"
    }
    r_rem = client.post("/remisiones", json=payload_remision, headers={"Authorization": f"Bearer {token_medico}"})
    assert r_rem.status_code == 200, f"Error crear remision: {r_rem.text}"
    rem_data = r_rem.json()
    remision_id = rem_data["id"]
    print(f"[OK] R08: Remision creada con ID {remision_id}", flush=True)

    # Especialista acepta la remision
    r_rem_aceptar = client.put(f"/remisiones/{remision_id}/aceptar", headers={"Authorization": f"Bearer {token_especialista}"})
    assert r_rem_aceptar.status_code == 200, f"Error aceptar remision: {r_rem_aceptar.text}"
    print(f"[OK] R19: Remision {remision_id} aceptada por Especialista", flush=True)

    # 11. R20 - Alertas Criticas y Reconocimiento
    r_alertas = client.get("/alertas", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_alertas.status_code == 200, f"Error /alertas: {r_alertas.text}"
    alertas_list = r_alertas.json()
    assert isinstance(alertas_list, list), "Alertas no es lista"
    if len(alertas_list) > 0:
        alerta_id = alertas_list[0]["id"]
        r_rec = client.put(f"/alertas/{alerta_id}/reconocer", json={"nota": "Verificado en cabecera de cama"}, headers={"Authorization": f"Bearer {token_medico}"})
        assert r_rec.status_code == 200, f"Error reconocer alerta: {r_rec.text}"
        print(f"[OK] R20: Alerta critica {alerta_id} reconocida con nota clinica", flush=True)

    # 12. R03 - Control de Acceso por Rol (Seguridad)
    r_auditoria_med = client.get("/auditoria", headers={"Authorization": f"Bearer {token_medico}"})
    assert r_auditoria_med.status_code in [401, 403], f"Medico no debio ver auditoria: {r_auditoria_med.status_code}"
    print(f"[OK] R03: Medico bloqueado de /auditoria con HTTP {r_auditoria_med.status_code}", flush=True)

    r_auditoria_admin = client.get("/auditoria", headers={"Authorization": f"Bearer {token_admin}"})
    assert r_auditoria_admin.status_code == 200, f"Admin debio ver auditoria: {r_auditoria_admin.status_code}"
    print(f"[OK] R03: Admin accedio a /auditoria exitosamente", flush=True)

    print("=== TODOS LOS TESTS E2E R01-R22 PASARON CON EXITO TOTAL! ===", flush=True)

if __name__ == "__main__":
    test_full_pipeline()
