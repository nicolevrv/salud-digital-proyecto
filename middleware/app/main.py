import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends, status
from pydantic import BaseModel
from typing import Optional

app = FastAPI(
    title="Middleware Telemetría UCI - Salud Digital",
    description="API REST con 3 roles (Admin Biomedico, Medico, Servicio), Soft Delete con restricciones de autoría y Restauración exclusiva del Admin Biomedico.",
    version="1.3.0"
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

# ==========================================
# MODELOS PYDANTIC
# ==========================================

class LoginRequest(BaseModel):
    email: str
    password: str

class EditObservationRequest(BaseModel):
    valor: float
    alerta_predictiva: Optional[str] = None

class CrearPacienteRequest(BaseModel):
    documento_identidad: str
    nombre: str
    cama_uci: str

class EditEncuentroRequest(BaseModel):
    equipo_uci_id: Optional[int] = None
    estado: Optional[str] = "in-progress"

class CrearEncuentroRequest(BaseModel):
    paciente_id: int
    equipo_uci_id: Optional[int] = None

class EditEquipoUCIRequest(BaseModel):
    ubicacion_uci: str
    estado_operativo: str
    bateria_backup_porcentaje: Optional[int] = 100

# ==========================================
# DEPENDENCIA DE AUTENTICACIÓN
# ==========================================

def verify_user_credentials(
    x_user_email: str = Header(..., description="Correo del usuario"),
    x_user_password: str = Header(..., description="Contraseña del usuario"),
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT u.id, u.nombre, u.email, u.rol_id, r.nombre as rol_nombre 
        FROM usuarios u 
        JOIN roles r ON u.rol_id = r.id 
        WHERE u.email = %s AND u.password_hash = %s AND u.is_deleted = FALSE
    """, (x_user_email, x_user_password))
    user = cursor.fetchone()
    cursor.close()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Credenciales inválidas: Correo o contraseña incorrectos"
        )
    return user

# ==========================================
# 0. GENERAL Y AUTENTICACIÓN
# ==========================================

@app.get("/", tags=["General"])
def root():
    return {"status": "API Middleware Telemetría UCI Funcionando Correctamente"}

@app.post("/login", tags=["Autenticación"])
def login(credentials: LoginRequest, conn = Depends(get_db)):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT u.id, u.nombre, u.email, u.rol_id, r.nombre as rol_nombre 
        FROM usuarios u 
        JOIN roles r ON u.rol_id = r.id 
        WHERE u.email = %s AND u.password_hash = %s AND u.is_deleted = FALSE
    """, (credentials.email, credentials.password))
    user = cursor.fetchone()
    cursor.close()

    if not user:
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")

    return {
        "message": "Autenticación exitosa",
        "usuario": {
            "id": user["id"],
            "nombre": user["nombre"],
            "email": user["email"],
            "rol": user["rol_nombre"]
        }
    }

# ==========================================
# 1. SECCIÓN DE OBSERVACIONES
# ==========================================

@app.put("/observaciones/{obs_id}/soft-edit", tags=["Observaciones"])
def soft_edit_observation(
    obs_id: int, 
    data: EditObservationRequest, 
    user: dict = Depends(verify_user_credentials), 
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()
    
    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o eliminada")

    if user["rol_nombre"] == "Medico" and obs["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Un médico solo puede editar sus propios registros clínicos")
    elif user["rol_nombre"] not in ["Admin Biomedico", "Medico"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para editar registros")

    new_version = obs["version"] + 1
    cursor.execute("""
        UPDATE observaciones 
        SET valor = %s, alerta_predictiva = %s, version = %s 
        WHERE id = %s
    """, (data.valor, data.alerta_predictiva, new_version, obs_id))

    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_EDIT', 'observaciones', %s)
    """, (user["id"], obs_id))

    conn.commit()
    cursor.close()
    return {"message": "Observación editada correctamente", "nueva_version": new_version}

@app.delete("/observaciones/{obs_id}/soft-delete", tags=["Observaciones"])
def soft_delete_observation(
    obs_id: int, 
    user: dict = Depends(verify_user_credentials), 
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o ya eliminada")

    if user["rol_nombre"] == "Medico" and obs["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Un médico solo puede eliminar sus propios registros clínicos")
    elif user["rol_nombre"] not in ["Admin Biomedico", "Medico"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para eliminar registros")

    cursor.execute("UPDATE observaciones SET is_deleted = TRUE WHERE id = %s", (obs_id,))
    
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_DELETE', 'observaciones', %s)
    """, (user["id"], obs_id))

    conn.commit()
    cursor.close()
    return {"message": f"Observación ID {obs_id} marcada como eliminada (Soft Delete)"}

@app.post("/observaciones/{obs_id}/restore", tags=["Observaciones"])
def restore_observation(
    obs_id: int, 
    user: dict = Depends(verify_user_credentials), 
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Acceso denegado: Reservado exclusivamente al Admin Biomedico")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("UPDATE observaciones SET is_deleted = FALSE WHERE id = %s", (obs_id,))
    
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'RESTORE', 'observaciones', %s)
    """, (user["id"], obs_id))

    conn.commit()
    cursor.close()
    return {"message": f"Observación ID {obs_id} restaurada exitosamente por el Admin Biomedico"}

# ==========================================
# 2. SECCIÓN DE ENCUENTROS CLÍNICOS
# ==========================================

@app.post("/encuentros", tags=["Encuentros"])
def crear_encuentro(
    data: CrearEncuentroRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] not in ["Medico", "Admin Biomedico"]:
        raise HTTPException(status_code=403, detail="No tiene permisos para iniciar encuentros clínicos")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by)
        VALUES (%s, %s, 'in-progress', %s)
        RETURNING id, paciente_id, equipo_uci_id, estado, fecha_inicio;
    """, (data.paciente_id, data.equipo_uci_id, user["id"]))
    
    nuevo_encuentro = cursor.fetchone()
    conn.commit()
    cursor.close()
    return {"message": "Encuentro clínico iniciado exitosamente", "encuentro": nuevo_encuentro}

@app.put("/encuentros/{encuentro_id}/soft-edit", tags=["Encuentros"])
def soft_edit_encuentro(
    encuentro_id: int,
    data: EditEncuentroRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM encuentros WHERE id = %s AND is_deleted = FALSE", (encuentro_id,))
    enc = cursor.fetchone()

    if not enc:
        cursor.close()
        raise HTTPException(status_code=404, detail="Encuentro no encontrado o eliminado")

    if user["rol_nombre"] == "Medico" and enc["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Un médico solo puede editar los encuentros que inició")
    elif user["rol_nombre"] not in ["Admin Biomedico", "Medico"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para editar encuentros")

    cursor.execute("""
        UPDATE encuentros 
        SET equipo_uci_id = COALESCE(%s, equipo_uci_id), estado = COALESCE(%s, estado)
        WHERE id = %s
    """, (data.equipo_uci_id, data.estado, encuentro_id))

    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_EDIT', 'encuentros', %s)
    """, (user["id"], encuentro_id))

    conn.commit()
    cursor.close()
    return {"message": f"Encuentro ID {encuentro_id} actualizado correctamente"}

@app.delete("/encuentros/{encuentro_id}/soft-delete", tags=["Encuentros"])
def soft_delete_encuentro(
    encuentro_id: int,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM encuentros WHERE id = %s AND is_deleted = FALSE", (encuentro_id,))
    enc = cursor.fetchone()

    if not enc:
        cursor.close()
        raise HTTPException(status_code=404, detail="Encuentro no encontrado o ya eliminado")

    if user["rol_nombre"] == "Medico" and enc["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Un médico solo puede eliminar sus propios encuentros")
    elif user["rol_nombre"] not in ["Admin Biomedico", "Medico"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para eliminar encuentros")

    cursor.execute("UPDATE encuentros SET is_deleted = TRUE WHERE id = %s", (encuentro_id,))
    
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_DELETE', 'encuentros', %s)
    """, (user["id"], encuentro_id))

    conn.commit()
    cursor.close()
    return {"message": f"Encuentro ID {encuentro_id} marcado como eliminado"}

@app.post("/encuentros/{encuentro_id}/restore", tags=["Encuentros"])
def restore_encuentro(
    encuentro_id: int,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Acceso denegado: La restauración de encuentros está reservada exclusivamente al Admin Biomedico")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("UPDATE encuentros SET is_deleted = FALSE WHERE id = %s", (encuentro_id,))
    
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'RESTORE', 'encuentros', %s)
    """, (user["id"], encuentro_id))

    conn.commit()
    cursor.close()
    return {"message": f"Encuentro ID {encuentro_id} restaurado exitosamente por el Admin Biomedico"}

# ==========================================
# 3. SECCIÓN DE PACIENTES
# ==========================================

@app.post("/pacientes", tags=["Pacientes"])
def crear_paciente(
    data: CrearPacienteRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] not in ["Medico", "Admin Biomedico"]:
        raise HTTPException(status_code=403, detail="No tiene permisos para registrar pacientes")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        INSERT INTO pacientes (documento_identidad, nombre, cama_uci, created_by)
        VALUES (%s, %s, %s, %s)
        RETURNING id, documento_identidad, nombre, cama_uci;
    """, (data.documento_identidad, data.nombre, data.cama_uci, user["id"]))
    
    nuevo_paciente = cursor.fetchone()
    conn.commit()
    cursor.close()
    return {"message": "Paciente registrado exitosamente", "paciente": nuevo_paciente}

@app.get("/pacientes", tags=["Pacientes"])
def listar_pacientes(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id, documento_identidad, nombre, cama_uci FROM pacientes WHERE is_deleted = FALSE")
    pacientes = cursor.fetchall()
    cursor.close()
    return {"pacientes": pacientes}

# ==========================================
# 4. SECCIÓN DE EQUIPOS UCI Y HOJAS DE VIDA
# ==========================================

@app.get("/equipos-uci", tags=["Equipos UCI & Hojas de Vida"])
def listar_equipos_uci(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT e.id as equipo_uci_id, e.ubicacion_uci, e.estado_operativo, e.bateria_backup_porcentaje,
               hv.codigo_inventario, hv.marca, hv.modelo, hv.numero_serie, hv.registro_invima,
               c.nombre as tipo_equipo, c.clasificacion_riesgo
        FROM equipos_uci e
        JOIN hoja_vida_equipos hv ON e.hoja_vida_id = hv.id
        JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE e.is_deleted = FALSE
    """)
    equipos = cursor.fetchall()
    cursor.close()
    return {"equipos_uci": equipos}

@app.put("/equipos-uci/{equipo_id}/soft-edit", tags=["Equipos UCI & Hojas de Vida"])
def soft_edit_equipo_uci(
    equipo_id: int,
    data: EditEquipoUCIRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Exclusivo del Admin Biomedico: Gestión técnica y metrológica de equipos")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        UPDATE equipos_uci 
        SET ubicacion_uci = %s, estado_operativo = %s, bateria_backup_porcentaje = %s
        WHERE id = %s AND is_deleted = FALSE
    """, (data.ubicacion_uci, data.estado_operativo, data.bateria_backup_porcentaje, equipo_id))

    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_EDIT', 'equipos_uci', %s)
    """, (user["id"], equipo_id))

    conn.commit()
    cursor.close()
    return {"message": f"Equipo UCI ID {equipo_id} actualizado exitosamente por la Gestión Biomédica"}