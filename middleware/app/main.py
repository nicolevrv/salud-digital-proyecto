import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import random
import hashlib
import secrets
from datetime import datetime, timedelta
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
    title="Salud Predictiva - Salud Digital",
    description="Aplicación para gestionar hojas de vida de equipos biomédicos, inventario UCI y variables clínicas LOINC con 4 perfiles (Paciente, Médico, Admin Biomédico, Servicio IoT).",
    version="2.0.0"
)

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración de Base de Datos
DATABASE_URL = os.getenv("DATABASE_URL")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5433")
DB_NAME = os.getenv("DB_NAME", "uci_telemetria")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "adminpassword")

def get_connection():
    """Establece conexión a PostgreSQL soportando DATABASE_URL (Neon/Render) y variables individuales (Docker/Local)."""
    if DATABASE_URL:
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(db_url)
    
    # Fallback para local o Docker Compose
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
    except Exception:
        # Reintento en puerto 5432 estándar por si se corre dentro del contenedor app-db
        return psycopg2.connect(
            host=DB_HOST,
            port=5432,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
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
            detail=f"Error de conexión con la base de datos PostgreSQL: {str(e)}"
        )

# ==========================================
# SEGURIDAD: CONTROL DE ACCESO, INTENTOS FALLIDOS Y PBKDF2
# ==========================================

LOGIN_ATTEMPTS = {}
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 15

def check_account_lockout(email: str):
    email_key = email.lower().strip()
    record = LOGIN_ATTEMPTS.get(email_key)
    if not record:
        return
    if record.get("locked_until"):
        now = datetime.now()
        if now < record["locked_until"]:
            mins_left = max(1, int((record["locked_until"] - now).total_seconds() / 60))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos de contraseña. Intente de nuevo en {mins_left} minuto(s)."
            )
        else:
            LOGIN_ATTEMPTS.pop(email_key, None)

def register_failed_attempt(email: str):
    email_key = email.lower().strip()
    now = datetime.now()
    record = LOGIN_ATTEMPTS.get(email_key, {"attempts": 0, "locked_until": None})
    record["attempts"] += 1
    if record["attempts"] >= MAX_FAILED_ATTEMPTS:
        record["locked_until"] = now + timedelta(minutes=LOCKOUT_MINUTES)
        LOGIN_ATTEMPTS[email_key] = record
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos de contraseña."
        )
    else:
        LOGIN_ATTEMPTS[email_key] = record
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Contraseña incorrecta. Intento {record['attempts']} de {MAX_FAILED_ATTEMPTS}."
        )

def reset_login_attempts(email: str):
    LOGIN_ATTEMPTS.pop(email.lower().strip(), None)

def verify_and_upgrade_password(conn, user_id, provided_password, stored_hash):
    """
    Verifica la contraseña contra hash PBKDF2 o contra texto plano de demo.
    Si coincide en texto plano, la convierte a PBKDF2 en la base de datos tras el primer login.
    """
    if stored_hash.startswith("pbkdf2:sha256:"):
        try:
            parts = stored_hash.split("$")
            iterations = int(parts[1])
            salt = parts[2]
            expected_hash = parts[3]
            computed = hashlib.pbkdf2_hmac("sha256", provided_password.encode(), salt.encode(), iterations).hex()
            return secrets.compare_digest(computed, expected_hash)
        except Exception:
            return False
    elif stored_hash == provided_password:
        # Conversión automática a PBKDF2 tras el primer inicio de sesión correcto
        salt = secrets.token_hex(16)
        iterations = 100000
        new_hash = hashlib.pbkdf2_hmac("sha256", provided_password.encode(), salt.encode(), iterations).hex()
        formatted = f"pbkdf2:sha256:${iterations}${salt}${new_hash}"
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s", (formatted, user_id))
            conn.commit()
            cursor.close()
        except Exception as e:
            print(f"Error convirtiendo clave a PBKDF2: {e}")
        return True
    return False

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

class CrearEquipoUCIRequest(BaseModel):
    equipo_catalogo_id: int
    codigo_inventario: str
    marca: str
    modelo: str
    numero_serie: str
    registro_invima: str
    servicio_asignado: Optional[str] = "UCI Adultos"
    ubicacion_uci: str
    estado_operativo: Optional[str] = "Disponible"
    bateria_backup_porcentaje: Optional[int] = 100

class EditEquipoUCIRequest(BaseModel):
    ubicacion_uci: str
    estado_operativo: str
    bateria_backup_porcentaje: Optional[int] = 100

class SimularTelemetriaRequest(BaseModel):
    encuentro_id: Optional[int] = None
    tipo_simulacion: Optional[str] = "ventilador"

# ==========================================
# DEPENDENCIA DE AUTENTICACIÓN
# ==========================================

def verify_user_credentials(
    x_user_email: str = Header(..., description="Correo del usuario"),
    x_user_password: str = Header(..., description="Contraseña del usuario"),
    conn = Depends(get_db)
):
    check_account_lockout(x_user_email)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT u.id, u.nombre, u.email, u.password_hash, u.rol_id, r.nombre as rol_nombre 
        FROM usuarios u 
        JOIN roles r ON u.rol_id = r.id 
        WHERE u.email = %s AND u.is_deleted = FALSE
    """, (x_user_email,))
    user = cursor.fetchone()
    cursor.close()

    if not user:
        register_failed_attempt(x_user_email)

    if not verify_and_upgrade_password(conn, user["id"], x_user_password, user["password_hash"]):
        register_failed_attempt(x_user_email)

    reset_login_attempts(x_user_email)
    user.pop("password_hash", None)
    return user

# ==========================================
# 0. GENERAL, DASHBOARD Y AUTENTICACIÓN
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def root(request: Request):
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header and "text/html" not in accept_header:
        return JSONResponse({"status": "Salud Predictiva - API Salud Digital Funcionando Correctamente"})
    
    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h2>Salud Predictiva - Dashboard no encontrado</h2>")

@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
def dashboard_view():
    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h2>Dashboard no encontrado</h2>")

@app.get("/health", tags=["General"])
def health_check():
    """Estado de la API y conexión PostgreSQL."""
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
        "app": "Salud Predictiva - Salud Digital",
        "database": {
            "connected": db_connected,
            "provider": "Neon PostgreSQL" if DATABASE_URL and "neon.tech" in DATABASE_URL else ("PostgreSQL Remoto" if DATABASE_URL else "PostgreSQL Local / Docker"),
            "tables_found": tables_count,
            "error": error_msg
        },
        "version": "2.0.0"
    }

@app.post("/login", tags=["Autenticación"])
def login(credentials: LoginRequest, conn = Depends(get_db)):
    check_account_lockout(credentials.email)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.id, u.nombre, u.email, u.password_hash, u.rol_id, r.nombre as rol_nombre 
            FROM usuarios u 
            JOIN roles r ON u.rol_id = r.id 
            WHERE u.email = %s AND u.is_deleted = FALSE
        """, (credentials.email,))
        user = cursor.fetchone()
        cursor.close()
    except Exception as e:
        cursor.close()
        err_str = str(e).lower()
        if "does not exist" in err_str or "no existe" in err_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La base de datos aún no contiene las tablas necesarias. Ejecuta docker compose o aplica el esquema inicial."
            )
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")

    if not user:
        register_failed_attempt(credentials.email)

    if not verify_and_upgrade_password(conn, user["id"], credentials.password, user["password_hash"]):
        register_failed_attempt(credentials.email)

    reset_login_attempts(credentials.email)

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
# 1. SECCIÓN DE OBSERVACIONES TELEMÉTRICAS
# ==========================================

@app.get("/observaciones", tags=["Observaciones"])
def listar_observaciones(
    include_deleted: bool = False,
    encuentro_id: Optional[int] = None,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """
    Lista observaciones telemétricas según perfil:
    - Paciente: consulta únicamente sus propias mediciones.
    - Admin Biomédico: no accede a datos clínicos.
    - Médico: consulta registros clínicos.
    """
    if user["rol_nombre"] == "Admin Biomedico":
        raise HTTPException(status_code=403, detail="El Administrador Biomédico no tiene acceso a datos clínicos de telemetría de pacientes.")
    if user["rol_nombre"] == "Servicio":
        raise HTTPException(status_code=403, detail="El Servicio IoT no consulta observaciones clínicas.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT o.id, o.encuentro_id, o.parametro, o.codigo_loinc, o.valor, o.unidad,
               o.alerta_predictiva, o.version, o.is_deleted, o.created_at, o.created_by,
               u.nombre as autor_nombre, r.nombre as autor_rol,
               p.nombre as paciente_nombre, p.cama_uci, p.usuario_id as paciente_usuario_id,
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
    
    # Aislamiento para Paciente
    if user["rol_nombre"] == "Paciente":
        query += " AND (p.usuario_id = %s OR p.nombre ILIKE %s)"
        params.extend([user["id"], f"%{user['nombre']}%"])

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
    """Permite al Servicio IoT o Médico registrar una nueva medición telemétrica."""
    if user["rol_nombre"] not in ["Medico", "Servicio"]:
        raise HTTPException(status_code=403, detail="No tiene permisos para registrar observaciones telemétricas.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
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
    """Los médicos solo pueden modificar sus propios registros clínicos."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()
    
    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o eliminada")

    if user["rol_nombre"] != "Medico":
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para editar registros clínicos.")

    if obs["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Restricción de autoría: Un médico solo puede editar sus propios registros clínicos.")

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
    """Los médicos solo pueden eliminar sus propios registros clínicos."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o ya eliminada")

    if user["rol_nombre"] != "Medico":
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para eliminar registros clínicos.")

    if obs["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Restricción de autoría: Un médico solo puede eliminar sus propios registros clínicos.")

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
    """Restauración clínica: Reservada al médico autor."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada")

    if user["rol_nombre"] != "Medico":
        cursor.close()
        raise HTTPException(status_code=403, detail="Acceso denegado: La restauración clínica está reservada exclusivamente a médicos.")

    if obs["created_by"] != user["id"]:
        cursor.close()
        raise HTTPException(status_code=403, detail="Acceso denegado: Restauración clínica reservada al médico autor de este registro.")

    cursor.execute("UPDATE observaciones SET is_deleted = FALSE WHERE id = %s", (obs_id,))
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'RESTORE', 'observaciones', %s)
    """, (user["id"], obs_id))

    conn.commit()
    cursor.close()
    return {"message": f"Observación ID {obs_id} restaurada exitosamente por su médico autor"}

# ==========================================
# 2. SECCIÓN DE ENCUENTROS CLÍNICOS
# ==========================================

@app.get("/encuentros", tags=["Encuentros"])
def listar_encuentros(
    include_deleted: bool = False,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Lista encuentros clínicos según perfil."""
    if user["rol_nombre"] == "Admin Biomedico":
        raise HTTPException(status_code=403, detail="El Administrador Biomédico no tiene acceso a datos clínicos de encuentros.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT e.id, e.paciente_id, e.equipo_uci_id, e.fecha_inicio, e.fecha_fin, e.estado, e.is_deleted, e.created_by,
               p.nombre as paciente_nombre, p.documento_identidad, p.cama_uci, p.usuario_id as paciente_usuario_id,
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
    params = []
    if user["rol_nombre"] == "Paciente":
        query += " AND (p.usuario_id = %s OR p.nombre ILIKE %s)"
        params.extend([user["id"], f"%{user['nombre']}%"])

    if not include_deleted:
        query += " AND e.is_deleted = FALSE"
    query += " ORDER BY e.id DESC"

    cursor.execute(query, tuple(params))
    encuentros = cursor.fetchall()
    cursor.close()
    return {"encuentros": encuentros}

@app.post("/encuentros", tags=["Encuentros"])
def crear_encuentro(
    data: CrearEncuentroRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Medico":
        raise HTTPException(status_code=403, detail="Solo los médicos están autorizados para iniciar encuentros clínicos.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by)
        VALUES (%s, %s, 'in-progress', %s)
        RETURNING id, paciente_id, equipo_uci_id, estado, fecha_inicio;
    """, (data.paciente_id, data.equipo_uci_id, user["id"]))
    
    nuevo_encuentro = cursor.fetchone()
    
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

# ==========================================
# 3. SECCIÓN DE PACIENTES
# ==========================================

@app.post("/pacientes", tags=["Pacientes"])
def crear_paciente(
    data: CrearPacienteRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Medico":
        raise HTTPException(status_code=403, detail="Solo los médicos tienen autorización para admitir y registrar pacientes en UCI.")

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
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """
    Listado de pacientes:
    - Paciente: consulta únicamente su propio registro.
    - Admin Biomédico: no accede a registros clínicos.
    - Médico: consulta pacientes UCI.
    """
    if user["rol_nombre"] == "Admin Biomedico":
        raise HTTPException(status_code=403, detail="El Administrador Biomédico no tiene acceso a datos clínicos de pacientes.")
    if user["rol_nombre"] == "Servicio":
        raise HTTPException(status_code=403, detail="El servicio IoT no consulta pacientes.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT p.id, p.documento_identidad, p.nombre, p.cama_uci, p.usuario_id, p.created_by,
               e.id as encuentro_activo_id, e.estado as encuentro_estado,
               eq.ubicacion_uci as equipo_ubicacion, c.nombre as equipo_nombre
        FROM pacientes p
        LEFT JOIN encuentros e ON p.id = e.paciente_id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE p.is_deleted = FALSE
    """
    params = []
    if user["rol_nombre"] == "Paciente":
        query += " AND (p.usuario_id = %s OR p.nombre ILIKE %s)"
        params.extend([user["id"], f"%{user['nombre']}%"])
        
    query += " ORDER BY p.id ASC"

    cursor.execute(query, tuple(params))
    pacientes = cursor.fetchall()
    cursor.close()
    return {"pacientes": pacientes}

# ==========================================
# 4. GESTIÓN BIOMÉDICA & HOJAS DE VIDA
# ==========================================

@app.get("/catalogo-equipos", tags=["Equipos UCI & Hojas de Vida"])
def listar_catalogo_equipos(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Tipos de equipo disponibles para el administrador biomédico."""
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Acceso denegado: El catálogo de tecnología médica está reservado al Administrador Biomédico.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT id, nombre, tipo_servicio, clasificacion_riesgo, tecnologia_predominante 
        FROM catalogo_equipos 
        WHERE is_deleted = FALSE 
        ORDER BY id ASC
    """)
    catalogo = cursor.fetchall()
    cursor.close()
    return {"catalogo": catalogo}

@app.post("/equipos-uci", tags=["Equipos UCI & Hojas de Vida"])
def crear_equipo_uci(
    data: CrearEquipoUCIRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Creación de hoja de vida y registro UCI por administración biomédica."""
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Exclusivo del Admin Biomedico: Creación y registro metrológico de equipos.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Insertar Hoja de Vida
    cursor.execute("""
        INSERT INTO hoja_vida_equipos (equipo_catalogo_id, codigo_inventario, marca, modelo, numero_serie, registro_invima, es_equipo_uci, servicio_asignado, fecha_adquisicion, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, CURRENT_DATE, %s)
        RETURNING id, codigo_inventario, marca, modelo, numero_serie, registro_invima;
    """, (data.equipo_catalogo_id, data.codigo_inventario, data.marca, data.modelo, data.numero_serie, data.registro_invima, data.servicio_asignado, user["id"]))
    hv = cursor.fetchone()

    # 2. Insertar Equipo UCI
    cursor.execute("""
        INSERT INTO equipos_uci (hoja_vida_id, ubicacion_uci, estado_operativo, bateria_backup_porcentaje, created_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, ubicacion_uci, estado_operativo, bateria_backup_porcentaje;
    """, (hv["id"], data.ubicacion_uci, data.estado_operativo, data.bateria_backup_porcentaje, user["id"]))
    eq = cursor.fetchone()

    # Log de auditoría
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'CREATE_EQUIPO', 'equipos_uci', %s)
    """, (user["id"], eq["id"]))

    conn.commit()
    cursor.close()
    return {
        "message": "Equipo biomédico y hoja de vida creados exitosamente",
        "equipo_uci": eq,
        "hoja_vida": hv
    }

@app.get("/equipos-uci", tags=["Equipos UCI & Hojas de Vida"])
def listar_equipos_uci(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Inventario metrológico de equipos y hojas de vida."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    query = """
        SELECT e.id as equipo_uci_id, e.ubicacion_uci, e.estado_operativo, e.bateria_backup_porcentaje,
               e.ultima_inspeccion_biomedica,
               hv.id as hoja_vida_id, hv.codigo_inventario, hv.marca, hv.modelo, hv.numero_serie, hv.registro_invima,
               hv.servicio_asignado, hv.fecha_adquisicion,
               c.nombre as tipo_equipo, c.clasificacion_riesgo, c.tecnologia_predominante,
               enc.id as encuentro_activo_id, p.id as paciente_id, p.nombre as paciente_nombre, p.usuario_id as paciente_usuario_id
        FROM equipos_uci e
        JOIN hoja_vida_equipos hv ON e.hoja_vida_id = hv.id
        JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        LEFT JOIN encuentros enc ON e.id = enc.equipo_uci_id AND enc.estado = 'in-progress' AND enc.is_deleted = FALSE
        LEFT JOIN pacientes p ON enc.paciente_id = p.id
        WHERE e.is_deleted = FALSE
    """
    params = []
    
    # Paciente solo consulta su equipo asignado
    if user["rol_nombre"] == "Paciente":
        query += " AND (p.usuario_id = %s OR p.nombre ILIKE %s)"
        params.extend([user["id"], f"%{user['nombre']}%"])
        
    query += " ORDER BY e.id ASC"

    cursor.execute(query, tuple(params))
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
    """Actualización de estado y batería de backup (Admin Biomédico)."""
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Exclusivo del Admin Biomedico: Gestión técnica y metrológica de equipos.")

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
# 5. AUDITORÍA, ESTADÍSTICAS Y SIMULADOR IOT
# ==========================================

@app.get("/audit-logs", tags=["Auditoría"])
def listar_audit_logs(
    limit: int = 50,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Auditoría administrativa."""
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(status_code=403, detail="Acceso denegado: La auditoría inmutable está reservada exclusivamente a la Administración Biomédica.")

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
    """Estadísticas agregadas para las tarjetas KPI del Dashboard."""
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
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Simulación de telemetría disponible ÚNICAMENTE para la cuenta de Servicio IoT."""
    if user["rol_nombre"] != "Servicio":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: La simulación de telemetría está disponible únicamente para la cuenta de Servicio IoT."
        )

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    encuentro_id = data.encuentro_id
    if not encuentro_id:
        cursor.execute("SELECT id FROM encuentros WHERE estado = 'in-progress' AND is_deleted = FALSE ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
        if row:
            encuentro_id = row["id"]
        else:
            cursor.close()
            raise HTTPException(status_code=400, detail="No hay encuentros clínicos activos para simular telemetría.")

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
    
    cursor.execute("""
        INSERT INTO observaciones (encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
        RETURNING id, encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_at;
    """, (encuentro_id, selected["parametro"], selected["codigo_loinc"], selected["valor"], selected["unidad"], selected["alerta"], user["id"]))
    
    nueva_obs = cursor.fetchone()
    conn.commit()
    cursor.close()

    return {
        "message": "Paquete de telemetría IoT simulado exitosamente por la cuenta de Servicio IoT",
        "observacion": nueva_obs
    }

@app.post("/init-db", tags=["Administración"])
def initialize_database(
    reset: bool = False,
    user: dict = Depends(verify_user_credentials)
):
    """
    Actualización autenticada del esquema por administración biomédica.
    Requiere autenticación con rol 'Admin Biomedico'.
    """
    if user["rol_nombre"] != "Admin Biomedico":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: El endpoint /init-db requiere autenticación administrativa de Administrador Biomédico."
        )

    try:
        conn = get_connection()
        cursor = conn.cursor()

        if reset:
            cursor.execute("""
                DROP TABLE IF EXISTS audit_logs CASCADE;
                DROP TABLE IF EXISTS observaciones CASCADE;
                DROP TABLE IF EXISTS encuentros CASCADE;
                DROP TABLE IF EXISTS pacientes CASCADE;
                DROP TABLE IF EXISTS equipos_uci CASCADE;
                DROP TABLE IF EXISTS hoja_vida_equipos CASCADE;
                DROP TABLE IF EXISTS catalogo_equipos CASCADE;
                DROP TABLE IF EXISTS usuarios CASCADE;
                DROP TABLE IF EXISTS roles CASCADE;
            """)
            conn.commit()
        else:
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'encuentros') THEN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'encuentros' AND column_name = 'equipo_uci_id') THEN
                            ALTER TABLE encuentros ADD COLUMN equipo_uci_id INT;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'encuentros' AND column_name = 'fecha_fin') THEN
                            ALTER TABLE encuentros ADD COLUMN fecha_fin TIMESTAMP;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'encuentros' AND column_name = 'is_deleted') THEN
                            ALTER TABLE encuentros ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE;
                        END IF;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pacientes') THEN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'pacientes' AND column_name = 'usuario_id') THEN
                            ALTER TABLE pacientes ADD COLUMN usuario_id INT;
                        END IF;
                    END IF;
                END $$;
            """)
            conn.commit()

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
            "message": "Esquema y datos semilla creados/actualizados exitosamente en PostgreSQL" + (" (Reinicio limpio)" if reset else ""),
            "schema_path": used_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al inicializar la base de datos: {str(e)}")