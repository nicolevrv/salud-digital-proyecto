import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
from .fhir_client import sync_patient_to_fhir, enviar_observacion_a_fhir

app = FastAPI(
    title="Middleware Telemetría UCI - Salud Digital",
    description="API REST con control de acceso por roles, Soft Delete, Soft Edit y Sincronización FHIR",
    version="1.0.0"
)

DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "telemetria_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS
    )
    try:
        yield conn
    finally:
        conn.close()

# Modelos Pydantic
class EditObservationRequest(BaseModel):
    valor: float
    alerta_predictiva: Optional[str] = None

# Autenticación basada en Headers
def verify_user_role(x_user_id: int = Header(...), conn = Depends(get_db)):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT u.id, u.nombre, u.rol_id, r.nombre as rol_nombre 
        FROM usuarios u 
        JOIN roles r ON u.rol_id = r.id 
        WHERE u.id = %s
    """, (x_user_id,))
    user = cursor.fetchone()
    cursor.close()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no autorizado")
    return user

@app.get("/")
def root():
    return {"status": "API Middleware Funcionando Correctamente"}

# Endpoint 1: Soft Edit Observación
@app.put("/observaciones/{obs_id}/soft-edit")
def soft_edit_observation(
    obs_id: int, 
    data: EditObservationRequest, 
    user: dict = Depends(verify_user_role), 
    conn = Depends(get_db)
):
    if user["rol_nombre"] not in ["Admin", "Biomedico"]:
        raise HTTPException(status_code=403, detail="Rol sin permisos para editar registros")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()
    
    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o eliminada")

    # Actualizar incremento de versión
    new_version = obs["version"] + 1
    cursor.execute("""
        UPDATE observaciones 
        SET valor = %s, alerta_predictiva = %s, version = %s 
        WHERE id = %s
    """, (data.valor, data.alerta_predictiva, new_version, obs_id))

    # Auditoría
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_EDIT', 'observaciones', %s)
    """, (user["id"], obs_id))

    conn.commit()
    cursor.close()
    return {"message": "Observación editada correctamente", "nueva_version": new_version}

# Endpoint 2: Soft Delete Paciente
@app.delete("/pacientes/{paciente_id}/soft-delete")
def soft_delete_patient(
    paciente_id: int, 
    user: dict = Depends(verify_user_role), 
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Admin":
        raise HTTPException(status_code=403, detail="Solo Administradores pueden eliminar registros")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("UPDATE pacientes SET is_deleted = TRUE WHERE id = %s", (paciente_id,))
    
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_DELETE', 'pacientes', %s)
    """, (user["id"], paciente_id))

    conn.commit()
    cursor.close()
    return {"message": f"Paciente ID {paciente_id} marcado como eliminado (Soft Delete)"}

# Endpoint 3: Restaurar (Undelete)
@app.post("/pacientes/{paciente_id}/restore")
def restore_patient(
    paciente_id: int, 
    user: dict = Depends(verify_user_role), 
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Admin":
        raise HTTPException(status_code=403, detail="Solo Administradores pueden restaurar registros")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("UPDATE pacientes SET is_deleted = FALSE WHERE id = %s", (paciente_id,))
    
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'RESTORE', 'pacientes', %s)
    """, (user["id"], paciente_id))

    conn.commit()
    cursor.close()
    return {"message": f"Paciente ID {paciente_id} restaurado exitosamente"}