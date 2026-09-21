import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import random
from pathlib import Path

# Importar cliente FHIR si está disponible
try:
    from app.fhir_client import (
        sync_patient_to_fhir,
        sync_device_to_fhir,
        sync_encounter_to_fhir,
        enviar_observacion_a_fhir
    )
except ImportError:
    try:
        from fhir_client import (
            sync_patient_to_fhir,
            sync_device_to_fhir,
            sync_encounter_to_fhir,
            enviar_observacion_a_fhir
        )
    except ImportError:
        sync_patient_to_fhir = None
        sync_device_to_fhir = None
        sync_encounter_to_fhir = None
        enviar_observacion_a_fhir = None

app = FastAPI(
    title="Middleware Telemetría UCI - Salud Digital",
    description="API REST con 3 roles (Admin Biomedico, Medico, Servicio), Soft Delete con restricciones de autoría, Restauración exclusiva del Admin Biomedico y Dashboard visual para Render.",
    version="1.4.0"
)

# Habilitar CORS para despliegues en la nube (Render, localhost, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración de Base de Datos (Compatible con Neon y Local Docker)
DATABASE_URL = os.getenv("DATABASE_URL")
DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "telemetria_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

def get_connection():
    """Establece conexión a la base de datos soportando DATABASE_URL de Neon y fallback local."""
    if DATABASE_URL:
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS
    )

def get_db():
    try:
        conn = get_connection()
        try:
            yield conn
        finally:
            conn.close()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error de conexión con la base de datos PostgreSQL/Neon: {str(e)}"
        )

# ==========================================
# MODELOS PYDANTIC
# ==========================================

class LoginRequest(BaseModel):
    email: str
    password: str

class EditObservationRequest(BaseModel):
    valor: float
    alerta_predictiva: Optional[str] = None

class CrearObservacionRequest(BaseModel):
    encuentro_id: int
    parametro: str
    codigo_loinc: str
    valor: float
    unidad: str
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

class SimularTelemetriaRequest(BaseModel):
    encuentro_id: Optional[int] = None
    tipo_simulacion: Optional[str] = "ventilador"  # 'ventilador', 'signos_vitales', 'alerta_critica'

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
# 0. GENERAL, DASHBOARD Y AUTENTICACIÓN
# ==========================================

# Ruta para servir el HTML del Dashboard
BASE_DIR = Path(__file__).resolve().parent

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def root(request: Request):
    """
    Si la petición pide JSON explícitamente (ej: curl o api client), responde status.
    Si se abre en un navegador web, entrega el Dashboard visual.
    """
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header and "text/html" not in accept_header:
        return JSONResponse({"status": "API Middleware Telemetría UCI Funcionando Correctamente"})
    
    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="""
        <html>
            <body style="font-family:sans-serif;text-align:center;padding:50px;">
                <h2>Middleware Telemetría UCI Activo</h2>
                <p>El archivo de plantilla del dashboard se está cargando.</p>
                <a href="/docs" style="color:#0284c7;">Ir a Documentación Swagger /docs</a>
            </body>
        </html>
    """)

@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
def dashboard_view():
    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h2>Dashboard no encontrado</h2>")

@app.get("/health", tags=["General"])
def health_check():
    """Verifica el estado de salud del servicio y de la conexión a la base de datos (Neon/Postgres)."""
    db_connected = False
    error_msg = None
    tables_count = 0
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT count(*) FROM information_schema.tables 
            WHERE table_schema = 'public';
        """)
        tables_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        db_connected = True
    except Exception as e:
        error_msg = str(e)

    return {
        "status": "online",
        "database": {
            "connected": db_connected,
            "provider": "Neon PostgreSQL" if DATABASE_URL and "neon.tech" in DATABASE_URL else ("PostgreSQL Remoto" if DATABASE_URL else "PostgreSQL Local"),
            "tables_found": tables_count,
            "error": error_msg
        },
        "version": "1.4.0"
    }

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

@app.get("/observaciones", tags=["Observaciones"])
def listar_observaciones(
    include_deleted: bool = False,
    encuentro_id: Optional[int] = None,
    conn = Depends(get_db)
):
    """Lista todas las observaciones telemétricas con datos enriquecidos para el Dashboard."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT o.id, o.encuentro_id, o.parametro, o.codigo_loinc, o.valor, o.unidad,
               o.alerta_predictiva, o.version, o.is_deleted, o.created_at, o.created_by,
               u.nombre as autor_nombre, r.nombre as autor_rol,
               p.nombre as paciente_nombre, p.cama_uci,
               c.nombre as tipo_equipo, hv.codigo_inventario as equipo_codigo
        FROM observaciones o
        LEFT JOIN usuarios u ON o.created_by = u.id
        LEFT JOIN roles r ON u.rol_id = r.id
        LEFT JOIN encuentros e ON o.encuentro_id = e.id
        LEFT JOIN pacientes p ON e.paciente_id = p.id
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE 1=1
    """
    params = []
    if not include_deleted:
        query += " AND o.is_deleted = FALSE"
    if encuentro_id is not None:
        query += " AND o.encuentro_id = %s"
        params.append(encuentro_id)
        
    query += " ORDER BY o.id DESC LIMIT 100"
    
    cursor.execute(query, tuple(params))
    obs = cursor.fetchall()
    cursor.close()
    return {"observaciones": obs}

@app.post("/observaciones", tags=["Observaciones"])
def crear_observacion(
    data: CrearObservacionRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Permite al Servicio IoT o Médico registrar una nueva medición de telemetría."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Verificar que el encuentro exista y esté activo
    cursor.execute("SELECT id, paciente_id, equipo_uci_id FROM encuentros WHERE id = %s AND is_deleted = FALSE", (data.encuentro_id,))
    encuentro = cursor.fetchone()
    if not encuentro:
        cursor.close()
        raise HTTPException(status_code=404, detail="Encuentro clínico no encontrado o inactivo")

    cursor.execute("""
        INSERT INTO observaciones (encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
        RETURNING id, encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_at;
    """, (data.encuentro_id, data.parametro, data.codigo_loinc, data.valor, data.unidad, data.alerta_predictiva, user["id"]))
    
    nueva_obs = cursor.fetchone()
    conn.commit()
    cursor.close()

    # Sincronización opcional a HAPI FHIR si está configurado
    if enviar_observacion_a_fhir:
        try:
            enviar_observacion_a_fhir(
                encuentro_id_fhir=data.encuentro_id,
                parametro=data.parametro,
                loinc_code=data.codigo_loinc,
                valor=data.valor,
                unidad=data.unidad,
                alerta=data.alerta_predictiva
            )
        except Exception:
            pass

    return {"message": "Observación registrada con éxito", "observacion": nueva_obs}

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

@app.get("/encuentros", tags=["Encuentros"])
def listar_encuentros(
    include_deleted: bool = False,
    conn = Depends(get_db)
):
    """Lista todos los encuentros clínicos con datos del paciente y equipo UCI."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT e.id, e.paciente_id, e.equipo_uci_id, e.fecha_inicio, e.fecha_fin, e.estado, e.is_deleted, e.created_by,
               p.nombre as paciente_nombre, p.documento_identidad, p.cama_uci,
               u.nombre as autor_nombre,
               c.nombre as tipo_equipo, hv.codigo_inventario, hv.marca as equipo_marca, hv.modelo as equipo_modelo,
               eq.ubicacion_uci, eq.estado_operativo as equipo_estado
        FROM encuentros e
        LEFT JOIN pacientes p ON e.paciente_id = p.id
        LEFT JOIN usuarios u ON e.created_by = u.id
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE 1=1
    """
    if not include_deleted:
        query += " AND e.is_deleted = FALSE"
    query += " ORDER BY e.id DESC"

    cursor.execute(query)
    encuentros = cursor.fetchall()
    cursor.close()
    return {"encuentros": encuentros}

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
    
    # Si se asignó equipo UCI, actualizar su estado a 'En Uso'
    if data.equipo_uci_id:
        cursor.execute("UPDATE equipos_uci SET estado_operativo = 'En Uso' WHERE id = %s", (data.equipo_uci_id,))

    conn.commit()
    cursor.close()

    if sync_encounter_to_fhir:
        try:
            sync_encounter_to_fhir(nuevo_encuentro)
        except Exception:
            pass

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

    if sync_patient_to_fhir:
        try:
            sync_patient_to_fhir(nuevo_paciente)
        except Exception:
            pass

    return {"message": "Paciente registrado exitosamente", "paciente": nuevo_paciente}

@app.get("/pacientes", tags=["Pacientes"])
def listar_pacientes(
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT p.id, p.documento_identidad, p.nombre, p.cama_uci, p.created_by,
               e.id as encuentro_activo_id, e.estado as encuentro_estado,
               eq.ubicacion_uci as equipo_ubicacion, c.nombre as equipo_nombre
        FROM pacientes p
        LEFT JOIN encuentros e ON p.id = e.paciente_id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE p.is_deleted = FALSE
        ORDER BY p.id ASC
    """)
    pacientes = cursor.fetchall()
    cursor.close()
    return {"pacientes": pacientes}

# ==========================================
# 4. SECCIÓN DE EQUIPOS UCI Y HOJAS DE VIDA
# ==========================================

@app.get("/equipos-uci", tags=["Equipos UCI & Hojas de Vida"])
def listar_equipos_uci(
    conn = Depends(get_db)
):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT e.id as equipo_uci_id, e.ubicacion_uci, e.estado_operativo, e.bateria_backup_porcentaje,
               e.ultima_inspeccion_biomedica,
               hv.id as hoja_vida_id, hv.codigo_inventario, hv.marca, hv.modelo, hv.numero_serie, hv.registro_invima,
               hv.servicio_asignado, hv.fecha_adquisicion,
               c.nombre as tipo_equipo, c.clasificacion_riesgo, c.tecnologia_predominante
        FROM equipos_uci e
        JOIN hoja_vida_equipos hv ON e.hoja_vida_id = hv.id
        JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE e.is_deleted = FALSE
        ORDER BY e.id ASC
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
        SET ubicacion_uci = %s, estado_operativo = %s, bateria_backup_porcentaje = %s, ultima_inspeccion_biomedica = CURRENT_TIMESTAMP
        WHERE id = %s AND is_deleted = FALSE
    """, (data.ubicacion_uci, data.estado_operativo, data.bateria_backup_porcentaje, equipo_id))

    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'SOFT_EDIT', 'equipos_uci', %s)
    """, (user["id"], equipo_id))

    conn.commit()
    cursor.close()
    return {"message": f"Equipo UCI ID {equipo_id} actualizado exitosamente por la Gestión Biomédica"}

# ==========================================
# 5. AUDITORÍA, ESTADÍSTICAS Y UTILIDADES
# ==========================================

@app.get("/audit-logs", tags=["Auditoría"])
def listar_audit_logs(
    limit: int = 50,
    conn = Depends(get_db)
):
    """Retorna los registros de auditoría de modificaciones, eliminaciones y restauraciones."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT a.id, a.usuario_id, a.accion, a.tabla_afectada, a.registro_id, a.fecha,
               u.nombre as usuario_nombre, u.email as usuario_email, r.nombre as rol_nombre
        FROM audit_logs a
        LEFT JOIN usuarios u ON a.usuario_id = u.id
        LEFT JOIN roles r ON u.rol_id = r.id
        ORDER BY a.fecha DESC
        LIMIT %s
    """, (limit,))
    logs = cursor.fetchall()
    cursor.close()
    return {"audit_logs": logs}

@app.get("/dashboard-stats", tags=["Dashboard"])
def get_dashboard_stats(conn = Depends(get_db)):
    """Métricas y estadísticas agregadas para las tarjetas KPI del Dashboard."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT count(*) as total FROM pacientes WHERE is_deleted = FALSE")
    pacientes_count = cursor.fetchone()["total"]

    cursor.execute("SELECT count(*) as total FROM encuentros WHERE estado = 'in-progress' AND is_deleted = FALSE")
    encuentros_activos = cursor.fetchone()["total"]

    cursor.execute("""
        SELECT 
            count(*) filter (where estado_operativo = 'En Uso') as en_uso,
            count(*) filter (where estado_operativo = 'Disponible') as disponibles,
            count(*) filter (where estado_operativo = 'Mantenimiento' or estado_operativo = 'Descalibrado') as mantenimiento,
            count(*) as total
        FROM equipos_uci WHERE is_deleted = FALSE
    """)
    equipos_stat = cursor.fetchone()

    cursor.execute("""
        SELECT count(*) as total FROM observaciones 
        WHERE is_deleted = FALSE AND alerta_predictiva IS NOT NULL AND alerta_predictiva != ''
    """)
    alertas_count = cursor.fetchone()["total"]

    cursor.execute("SELECT count(*) as total FROM audit_logs")
    audits_count = cursor.fetchone()["total"]

    cursor.close()
    return {
        "pacientes": pacientes_count,
        "camas_en_uso": encuentros_activos,
        "equipos": equipos_stat,
        "alertas_predictivas": alertas_count,
        "auditorias_totales": audits_count
    }

@app.post("/simulate-telemetry", tags=["Simulador IoT"])
def simulate_telemetry_packet(
    data: SimularTelemetriaRequest,
    conn = Depends(get_db)
):
    """
    Simula el envío de telemetría IoT en tiempo real desde equipos UCI.
    Ideal para demostraciones en Render sin necesidad de hardware físico.
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Obtener un encuentro activo
    encuentro_id = data.encuentro_id
    if not encuentro_id:
        cursor.execute("SELECT id FROM encuentros WHERE estado = 'in-progress' AND is_deleted = FALSE ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
        if row:
            encuentro_id = row["id"]
        else:
            cursor.close()
            raise HTTPException(status_code=400, detail="No hay encuentros clínicos activos para simular telemetría. Registre o inicie un encuentro primero.")

    # Generar lecturas según tipo
    telemetry_scenarios = [
        {
            "parametro": "Saturación de Oxígeno (SpO2)",
            "codigo_loinc": "59408-5",
            "valor": round(random.uniform(92.0, 99.5), 1),
            "unidad": "%",
            "alerta": None
        },
        {
            "parametro": "Frecuencia Cardíaca (ECG)",
            "codigo_loinc": "8867-4",
            "valor": round(random.uniform(68.0, 115.0), 0),
            "unidad": "lpm",
            "alerta": None
        },
        {
            "parametro": "Presión Vía Aérea Pico (Pik)",
            "codigo_loinc": "19992-7",
            "valor": round(random.uniform(20.0, 36.0), 1),
            "unidad": "cmH2O",
            "alerta": "Alerta: Presión inspiratoria alta" if random.random() > 0.7 else None
        },
        {
            "parametro": "Temperatura Turbina Ventilador",
            "codigo_loinc": "8310-5",
            "valor": round(random.uniform(37.5, 42.5), 1),
            "unidad": "Cel",
            "alerta": "Alerta Predictiva: Temperatura por encima del umbral seguro de turbina" if random.random() > 0.5 else None
        }
    ]

    selected = random.choice(telemetry_scenarios)
    
    # Usuario de Servicio IoT (ID: 3)
    cursor.execute("""
        INSERT INTO observaciones (encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, 1, 3)
        RETURNING id, encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_at;
    """, (encuentro_id, selected["parametro"], selected["codigo_loinc"], selected["valor"], selected["unidad"], selected["alerta"]))
    
    nueva_obs = cursor.fetchone()
    conn.commit()
    cursor.close()

    return {
        "message": "Paquete de telemetría IoT simulado exitosamente",
        "observacion": nueva_obs
    }

@app.post("/init-db", tags=["Administración"])
def initialize_database():
    """
    Inicializa el esquema y los datos semilla en Neon PostgreSQL o PostgreSQL local.
    Permite inicializar la base de datos de Render con un solo clic.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Buscar el archivo 01_schema.sql
        potential_paths = [
            BASE_DIR.parent.parent / "init-scripts" / "01_schema.sql",
            BASE_DIR.parent / "init-scripts" / "01_schema.sql",
            Path("init-scripts/01_schema.sql").resolve(),
            Path("01_schema.sql").resolve()
        ]
        
        schema_sql = None
        used_path = None
        for p in potential_paths:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    schema_sql = f.read()
                    used_path = str(p)
                break

        if not schema_sql:
            raise HTTPException(status_code=500, detail="No se encontró el archivo init-scripts/01_schema.sql")

        cursor.execute(schema_sql)
        conn.commit()
        cursor.close()
        conn.close()

        return {
            "status": "success",
            "message": "Esquema y datos semilla creados/actualizados exitosamente en PostgreSQL",
            "schema_path": used_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al inicializar la base de datos: {str(e)}")