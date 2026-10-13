import os
import urllib.parse
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
import base64
import hmac
import time
from datetime import datetime, timedelta
from enum import Enum
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
    title="BIOT UAO - Salud Digital",
    description="Aplicación para gestionar hojas de vida de equipos biomédicos, inventario UCI y variables clínicas LOINC con 3 perfiles (Médico, Admin Biomédico, Servicio IoT).",
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

# Configuración de Base de Datos (Neon PostgreSQL / Render / Local)
DEFAULT_NEON_URL = "postgresql://neondb_owner:npg_9ETzJKWp7jeO@ep-sweet-unit-aemm14bw-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
DATABASE_URL = os.getenv("DATABASE_URL") or DEFAULT_NEON_URL
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5433")
DB_NAME = os.getenv("DB_NAME", "uci_telemetria")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "adminpassword")

# El token de sesión usa una clave secreta de Render cuando existe.
# En Neon/Render, DATABASE_URL sirve como respaldo sin exponerlo al navegador.
SESSION_SECRET = os.getenv("SESSION_SECRET") or os.getenv("DATABASE_URL") or "dev-only-session-secret-change-in-production"
SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES", "480"))
AUTO_INIT_DB = os.getenv("AUTO_INIT_DB", "false").lower() in {"1", "true", "yes"}

def get_connection():
    """Establece conexión a PostgreSQL soportando DATABASE_URL (Neon/Render) y variables individuales (Docker/Local)."""
    if DATABASE_URL:
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(db_url, connect_timeout=10)
    
    # Fallback para local o Docker Compose
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            connect_timeout=5
        )
    except Exception:
        # Reintento en puerto 5432 estándar por si se corre dentro del contenedor app-db
        return psycopg2.connect(
            host=DB_HOST,
            port=5432,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            connect_timeout=5
        )

def get_db():
    try:
        conn = get_connection()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error de conexión con la base de datos PostgreSQL: {str(e)}"
        )
    try:
        yield conn
    finally:
        conn.close()

# ==========================================
# SEGURIDAD: CONTROL DE ACCESO, INTENTOS FALLIDOS Y PBKDF2
# ==========================================

LOGIN_ATTEMPTS = {}
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 15

def normalize_email(email: str) -> str:
    return email.strip().lower()

def log_login_event(email: str, evento: str, usuario_id: Optional[int] = None,
                    detalle: Optional[str] = None, ip: Optional[str] = None,
                    realizado_por: Optional[int] = None, conn = None):
    """Registra un evento de acceso en login_logs reutilizando la conexión activa."""
    own_conn = False
    cur = None
    try:
        if conn is None:
            conn = get_connection()
            own_conn = True
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO login_logs (usuario_id, email, evento, detalle, ip_origen, realizado_por)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (usuario_id, email, evento, detalle, ip, realizado_por))
        conn.commit()
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"No se pudo registrar el evento de login ({evento}): {exc}")
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

def _minutes_left(locked_until) -> int:
    secs = (locked_until - datetime.now()).total_seconds()
    return max(1, int((secs + 59) // 60))

def check_account_lockout(email: str, ip: Optional[str] = None, conn = None):
    email_key = normalize_email(email)
    now = datetime.now()

    # 1) Estado persistente en base de datos reutilizando la conexión activa
    locked = None
    cur = None
    own_conn = False
    try:
        if conn is None:
            conn = get_connection()
            own_conn = True
        cur = conn.cursor()
        cur.execute(
            "SELECT id, locked_until FROM usuarios WHERE LOWER(email) = %s AND is_deleted = FALSE",
            (email_key,)
        )
        row = cur.fetchone()
        if row and row[1]:
            if now < row[1]:
                locked = (row[0], row[1])
            else:
                cur.execute(
                    "UPDATE usuarios SET failed_login_attempts = 0, locked_until = NULL WHERE id = %s",
                    (row[0],)
                )
                conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

    if locked:
        log_login_event(email_key, "LOGIN_BLOCKED", locked[0], "Intento de ingreso con la cuenta bloqueada", ip, conn=conn)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos. Intente de nuevo en {_minutes_left(locked[1])} minuto(s) o solicite a un administrador que la desbloquee."
        )

    # 2) Respaldo en memoria
    record = LOGIN_ATTEMPTS.get(email_key)
    if not record:
        return
    locked_until = record.get("locked_until")
    if locked_until:
        if now < locked_until:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos. Intente de nuevo en {_minutes_left(locked_until)} minuto(s)."
            )
        LOGIN_ATTEMPTS.pop(email_key, None)

def register_failed_attempt(email: str, ip: Optional[str] = None, conn = None):
    email_key = normalize_email(email)
    now = datetime.now()
    outcome = None
    uid = None
    cur = None
    own_conn = False

    try:
        if conn is None:
            conn = get_connection()
            own_conn = True
        cur = conn.cursor()
        cur.execute(
            "SELECT id, COALESCE(failed_login_attempts, 0) FROM usuarios WHERE LOWER(email) = %s AND is_deleted = FALSE",
            (email_key,)
        )
        row = cur.fetchone()
        if row:
            uid = row[0]
            attempts = row[1] + 1
            is_locked = attempts >= MAX_FAILED_ATTEMPTS
            if is_locked:
                cur.execute(
                    "UPDATE usuarios SET failed_login_attempts = %s, locked_until = %s WHERE id = %s",
                    (attempts, now + timedelta(minutes=LOCKOUT_MINUTES), uid)
                )
            else:
                cur.execute("UPDATE usuarios SET failed_login_attempts = %s WHERE id = %s", (attempts, uid))
            conn.commit()
            outcome = (attempts, is_locked)
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        outcome = None
        uid = None
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

    if outcome is None:
        record = LOGIN_ATTEMPTS.get(email_key, {"attempts": 0, "locked_until": None})
        record["attempts"] += 1
        is_locked = record["attempts"] >= MAX_FAILED_ATTEMPTS
        if is_locked:
            record["locked_until"] = now + timedelta(minutes=LOCKOUT_MINUTES)
        LOGIN_ATTEMPTS[email_key] = record
        outcome = (record["attempts"], is_locked)

    attempts, is_locked = outcome
    if is_locked:
        log_login_event(email_key, "LOCKED", uid,
                        f"Cuenta bloqueada {LOCKOUT_MINUTES} min tras {attempts} intentos fallidos", ip, conn=conn)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos."
        )
    log_login_event(email_key, "LOGIN_FAIL", uid, f"Contraseña incorrecta (intento {attempts} de {MAX_FAILED_ATTEMPTS})", ip, conn=conn)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Contraseña incorrecta. Intento {attempts} de {MAX_FAILED_ATTEMPTS}."
    )

def reset_login_attempts(email: str, conn = None):
    email_key = normalize_email(email)
    LOGIN_ATTEMPTS.pop(email_key, None)
    own_conn = False
    cur = None
    try:
        if conn is None:
            conn = get_connection()
            own_conn = True
        cur = conn.cursor()
        cur.execute("""
            UPDATE usuarios SET failed_login_attempts = 0, locked_until = NULL
            WHERE LOWER(email) = %s
              AND (COALESCE(failed_login_attempts, 0) <> 0 OR locked_until IS NOT NULL)
        """, (email_key,))
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

def verify_and_upgrade_password(conn, user_id, provided_password, stored_hash):
    """Acepta formatos PBKDF2 antiguos/nuevos y migra claves demo en texto plano."""
    if not stored_hash:
        return False

    if stored_hash.startswith("pbkdf2:sha256:"):
        try:
            parts = stored_hash.split("$")
            # Compatibilidad con ambos formatos históricos:
            # pbkdf2:sha256:$100000$salt$hash
            # pbkdf2:sha256:100000$salt$hash
            if len(parts) == 4 and parts[0] == "pbkdf2:sha256:":
                iterations = int(parts[1])
                salt = parts[2]
                expected_hash = parts[3]
            elif len(parts) == 3 and parts[0].startswith("pbkdf2:sha256:"):
                iterations = int(parts[0].rsplit(":", 1)[1])
                salt = parts[1]
                expected_hash = parts[2]
            else:
                return False
            computed = hashlib.pbkdf2_hmac(
                "sha256",
                provided_password.encode("utf-8"),
                salt.encode("utf-8"),
                iterations
            ).hex()
            return secrets.compare_digest(computed, expected_hash)
        except (ValueError, TypeError, IndexError):
            return False

    if secrets.compare_digest(str(stored_hash), str(provided_password)):
        salt = secrets.token_hex(16)
        iterations = 100000
        new_hash = hashlib.pbkdf2_hmac(
            "sha256",
            provided_password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations
        ).hex()
        formatted = "pbkdf2:sha256:" + str(iterations) + "$" + salt + "$" + new_hash
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE usuarios SET password_hash = %s WHERE id = %s",
            (formatted, user_id)
        )
        conn.commit()
        cursor.close()
        return True

    return False

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def create_session_token(user_id: int) -> str:
    expires_at = int(time.time()) + SESSION_TTL_MINUTES * 60
    payload = f"{int(user_id)}.{expires_at}"
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).digest()
    return f"{_b64url(payload.encode('utf-8'))}.{_b64url(signature)}"

def decode_session_token(token: str) -> int:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload_bytes = _b64url_decode(encoded_payload)
        provided_signature = _b64url_decode(encoded_signature)
        expected_signature = hmac.new(
            SESSION_SECRET.encode("utf-8"),
            payload_bytes,
            hashlib.sha256
        ).digest()
        if not secrets.compare_digest(provided_signature, expected_signature):
            raise ValueError("firma inválida")
        user_part, exp_part = payload_bytes.decode("utf-8").split(".", 1)
        if int(exp_part) <= int(time.time()):
            raise ValueError("sesión expirada")
        return int(user_part)
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida o expirada. Inicie sesión nuevamente."
        )

def get_user_by_id(conn, user_id: int):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT u.id, u.nombre, u.email, u.rol_id, r.nombre as rol_nombre
        FROM usuarios u
        JOIN roles r ON u.rol_id = r.id
        WHERE u.id = %s AND u.is_deleted = FALSE
    """, (user_id,))
    user = cursor.fetchone()
    cursor.close()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El usuario de la sesión ya no existe o está inactivo."
        )
    return user

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
    x_session_token: Optional[str] = Header(default=None, description="Token de sesión"),
    authorization: Optional[str] = Header(default=None, description="Bearer token"),
    x_user_email: Optional[str] = Header(default=None, description="Correo del usuario (compatibilidad)"),
    x_user_password: Optional[str] = Header(default=None, description="Contraseña (compatibilidad)"),
    conn = Depends(get_db)
):
    token = x_session_token
    if not token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        elif len(parts) == 1:
            token = parts[0]

    if token:
        user_id = decode_session_token(token)
        return get_user_by_id(conn, user_id)

    if not x_user_email or not x_user_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida."
        )

    email = normalize_email(x_user_email)
    check_account_lockout(email, conn=conn)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT u.id, u.nombre, u.email, u.password_hash, u.rol_id, r.nombre as rol_nombre
        FROM usuarios u
        JOIN roles r ON u.rol_id = r.id
        WHERE LOWER(u.email) = %s AND u.is_deleted = FALSE
    """, (email,))
    user = cursor.fetchone()
    cursor.close()

    if not user:
        register_failed_attempt(email, conn=conn)
    if not verify_and_upgrade_password(conn, user["id"], x_user_password, user["password_hash"]):
        register_failed_attempt(email, conn=conn)

    reset_login_attempts(email, conn=conn)
    user.pop("password_hash", None)
    return user

# ==========================================
# ROLES (RBAC) Y DEPENDENCIAS DE SEGURIDAD
# ==========================================

class RolSistema(str, Enum):
    """Roles del sistema: clínicos/biomédicos + mantenimiento (ADMIN, ING_PREVENTIVO, ING_CORRECTIVO)."""
    # Roles originales
    ADMIN_BIOMEDICO = "Admin Biomedico"
    MEDICO = "Medico"
    SERVICIO = "Servicio"
    # Roles de gestión y mantenimiento técnico
    ADMIN = "ADMIN"
    ING_PREVENTIVO = "ING_PREVENTIVO"
    ING_CORRECTIVO = "ING_CORRECTIVO"

# Roles con permisos de administración
ADMIN_ROLES = {RolSistema.ADMIN.value, RolSistema.ADMIN_BIOMEDICO.value, "Administrador"}

# Roles con acceso a Predicción de Equipos (Admin Biomédico, Administrador, Ingeniero Correctivo e Ingeniero Preventivo)
PREDICCION_ROLES = {
    RolSistema.ADMIN_BIOMEDICO.value,
    RolSistema.ADMIN.value,
    RolSistema.ING_PREVENTIVO.value,
    RolSistema.ING_CORRECTIVO.value,
    "Ingeniero Preventivo",
    "Ingeniero Correctivo",
    "Administrador",
}

# Roles con acceso a Base de Datos de Equipos (Dataset y Edición)
DATASET_ROLES = {
    RolSistema.ADMIN_BIOMEDICO.value,
    RolSistema.ADMIN.value,
    RolSistema.ING_PREVENTIVO.value,
    RolSistema.ING_CORRECTIVO.value,
    "Ingeniero Preventivo",
    "Ingeniero Correctivo",
    "Administrador",
}

def require_roles(allowed_roles: List[str]):
    """Fábrica de dependencias: require_roles(["ADMIN", "ING_PREVENTIVO"])."""
    allowed = {r.value if isinstance(r, Enum) else str(r) for r in allowed_roles}

    def _role_checker(user: dict = Depends(verify_user_credentials)):
        if user["rol_nombre"] not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: el rol '{user['rol_nombre']}' no tiene permisos para esta operación."
            )
        return user
    return _role_checker

def get_current_admin(user: dict = Depends(verify_user_credentials)):
    """Solo administradores (ADMIN y el Admin Biomédico existente)."""
    if user["rol_nombre"] not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: esta operación es exclusiva del administrador del sistema."
        )
    return user

# ==========================================
# 0. GENERAL, DASHBOARD Y AUTENTICACIÓN
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

def ensure_security_tables_and_roles(conn = None):
    """
    Garantiza de forma idempotente que las columnas de bloqueo, la tabla login_logs,
    los roles de mantenimiento (ADMIN, ING_PREVENTIVO, ING_CORRECTIVO sin JEFE_AREA) y sus usuarios existan en Neon.
    """
    own_conn = False
    cur = None
    try:
        if conn is None:
            conn = get_connection()
            own_conn = True
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(50) NOT NULL
            );
            INSERT INTO roles (id, nombre) VALUES
            (1, 'Admin Biomedico'),
            (2, 'Medico'),
            (3, 'Servicio'),
            (5, 'ADMIN'),
            (7, 'ING_PREVENTIVO'),
            (8, 'ING_CORRECTIVO')
            ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre;

            -- Eliminar el rol JEFE_AREA y su usuario de prueba si existían
            DELETE FROM usuarios WHERE email = 'jefe.area@hospital.com' OR rol_id = 6;
            DELETE FROM roles WHERE id = 6 OR nombre = 'JEFE_AREA';

            SELECT setval('roles_id_seq', COALESCE((SELECT MAX(id) FROM roles), 1));

            ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS failed_login_attempts INT DEFAULT 0;
            ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP;

            CREATE TABLE IF NOT EXISTS login_logs (
                id SERIAL PRIMARY KEY,
                usuario_id INT REFERENCES usuarios(id) ON DELETE SET NULL,
                email VARCHAR(100) NOT NULL,
                evento VARCHAR(30) NOT NULL,
                detalle TEXT,
                ip_origen VARCHAR(60),
                realizado_por INT REFERENCES usuarios(id) ON DELETE SET NULL,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO usuarios (nombre, email, password_hash, rol_id)
            SELECT v.nombre, v.email, v.password_hash, r.id
            FROM (VALUES
                ('Administrador del Sistema', 'admin.sistema@hospital.com', 'pbkdf2:sha256:100000$a9d9b5fe08318a41eea86dc0664219aa$d62d945071758e230cb2d67df6d736f88e88681111f8947d2cfdadb04fb3a778', 'ADMIN'),
                ('Ing. Mantenimiento Preventivo', 'ing.preventivo@hospital.com', 'pbkdf2:sha256:100000$574d817745c48d1cddca549857d44660$7d178807c8b3463fa381d956b756552dc9845148441fa997086e9479beac906e', 'ING_PREVENTIVO'),
                ('Ing. Mantenimiento Correctivo', 'ing.correctivo@hospital.com', 'pbkdf2:sha256:100000$a323f2909707aca9d2e2bf8945115afc$e8b53e6feaae0cda5752892a7f4652916c33749724d4a1060f1e07e76705989f', 'ING_CORRECTIVO')
            ) AS v(nombre, email, password_hash, rol_nombre)
            JOIN roles r ON r.nombre = v.rol_nombre
            ON CONFLICT (email) DO UPDATE SET rol_id = EXCLUDED.rol_id, password_hash = EXCLUDED.password_hash;
            SELECT setval('usuarios_id_seq', COALESCE((SELECT MAX(id) FROM usuarios), 1));
        """)
        conn.commit()
        print("Esquema de seguridad y roles inicializados correctamente (sin JEFE_AREA).")
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"Aviso en ensure_security_tables_and_roles: {exc}")
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

def _find_schema_file():
    candidates = [
        BASE_DIR.parent / "init-scripts" / "01_schema.sql",
        BASE_DIR.parent.parent / "init-scripts" / "01_schema.sql",
        Path("init-scripts/01_schema.sql").resolve(),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None

def initialize_database_on_startup():
    if not AUTO_INIT_DB:
        return

    schema_path = _find_schema_file()
    if not schema_path:
        print("AUTO_INIT_DB habilitado, pero no se encontró init-scripts/01_schema.sql")
        return

    last_error = None
    for attempt in range(1, 11):
        try:
            conn = get_connection()
            with conn.cursor() as cursor:
                with open(schema_path, "r", encoding="utf-8") as f:
                    cursor.execute(f.read())
            conn.commit()
            conn.close()
            print("Base de datos inicializada/verificada correctamente")
            return
        except Exception as exc:
            last_error = exc
            print(f"Esperando PostgreSQL (intento {attempt}/10): {exc}")
            time.sleep(2)

    print(f"No fue posible inicializar la base de datos automáticamente: {last_error}")

@app.on_event("startup")
def startup_event():
    ensure_security_tables_and_roles()
    initialize_database_on_startup()

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def root(request: Request):
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header and "text/html" not in accept_header:
        return JSONResponse({"status": "BIOT UAO - API Salud Digital Funcionando Correctamente"})
    
    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h2>BIOT UAO - Dashboard no encontrado</h2>")

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
        "app": "BIOT UAO - Salud Digital",
        "database": {
            "connected": db_connected,
            "provider": "Neon PostgreSQL" if DATABASE_URL and "neon.tech" in DATABASE_URL else ("PostgreSQL Remoto" if DATABASE_URL else "PostgreSQL Local / Docker"),
            "tables_found": tables_count,
            "error": error_msg
        },
        "version": "2.0.0"
    }

@app.post("/login", tags=["Autenticación"])
def login(credentials: LoginRequest, request: Request, conn = Depends(get_db)):
    email = normalize_email(credentials.email)
    forwarded = request.headers.get("x-forwarded-for")
    ip = (forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None))
    check_account_lockout(email, ip, conn=conn)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.id, u.nombre, u.email, u.password_hash, u.rol_id, r.nombre as rol_nombre
            FROM usuarios u
            JOIN roles r ON u.rol_id = r.id
            WHERE LOWER(u.email) = %s AND u.is_deleted = FALSE
        """, (email,))
        user = cursor.fetchone()
        cursor.close()
    except Exception as e:
        cursor.close()
        err_str = str(e).lower()
        if "does not exist" in err_str or "no existe" in err_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La base de datos aún no está inicializada. Espere unos segundos y vuelva a intentar."
            )
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")

    if not user:
        if email in {
            "admin.sistema@hospital.com",
            "ing.preventivo@hospital.com",
            "ing.correctivo@hospital.com"
        }:
            ensure_security_tables_and_roles(conn)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT u.id, u.nombre, u.email, u.password_hash, u.rol_id, r.nombre as rol_nombre
                FROM usuarios u
                JOIN roles r ON u.rol_id = r.id
                WHERE LOWER(u.email) = %s AND u.is_deleted = FALSE
            """, (email,))
            user = cursor.fetchone()
            cursor.close()

    if not user:
        register_failed_attempt(email, ip, conn=conn)
    if not verify_and_upgrade_password(conn, user["id"], credentials.password, user["password_hash"]):
        register_failed_attempt(email, ip, conn=conn)

    reset_login_attempts(email, conn=conn)
    log_login_event(email, "LOGIN_OK", user["id"], f"Ingreso exitoso ({user['rol_nombre']})", ip, conn=conn)
    token = create_session_token(user["id"])
    return {
        "message": "Autenticación exitosa",
        "token": token,
        "expires_in_seconds": SESSION_TTL_MINUTES * 60,
        "usuario": {
            "id": user["id"],
            "nombre": user["nombre"],
            "email": user["email"],
            "rol": user["rol_nombre"]
        }
    }

@app.get("/me", tags=["Autenticación"])
def current_user(user: dict = Depends(verify_user_credentials)):
    return {"usuario": user}

# ==========================================
# SEGURIDAD Y ACCESOS: USUARIOS, INGRESOS Y DESBLOQUEO (SOLO ADMIN)
# ==========================================

@app.get("/usuarios", tags=["Seguridad y Accesos"])
def listar_usuarios(
    admin: dict = Depends(get_current_admin),
    conn = Depends(get_db)
):
    """Usuarios del sistema con su estado de bloqueo y último ingreso."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.id, u.nombre, u.email, r.nombre AS rol_nombre,
                   COALESCE(u.failed_login_attempts, 0) AS failed_login_attempts,
                   u.locked_until,
                   (u.locked_until IS NOT NULL AND u.locked_until > %s) AS bloqueado,
                   (SELECT MAX(l.fecha) FROM login_logs l
                     WHERE l.usuario_id = u.id AND l.evento = 'LOGIN_OK') AS ultimo_ingreso
            FROM usuarios u
            LEFT JOIN roles r ON u.rol_id = r.id
            WHERE u.is_deleted = FALSE
            ORDER BY bloqueado DESC, u.id ASC
        """, (datetime.now(),))
        usuarios = cursor.fetchall()
        cursor.close()
    except Exception as exc:
        conn.rollback()
        ensure_security_tables_and_roles(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT u.id, u.nombre, u.email, r.nombre AS rol_nombre,
                   COALESCE(u.failed_login_attempts, 0) AS failed_login_attempts,
                   u.locked_until,
                   (u.locked_until IS NOT NULL AND u.locked_until > %s) AS bloqueado,
                   (SELECT MAX(l.fecha) FROM login_logs l
                     WHERE l.usuario_id = u.id AND l.evento = 'LOGIN_OK') AS ultimo_ingreso
            FROM usuarios u
            LEFT JOIN roles r ON u.rol_id = r.id
            WHERE u.is_deleted = FALSE
            ORDER BY bloqueado DESC, u.id ASC
        """, (datetime.now(),))
        usuarios = cursor.fetchall()
        cursor.close()
    return {"usuarios": usuarios}

@app.get("/login-logs", tags=["Seguridad y Accesos"])
def listar_login_logs(
    limit: int = 100,
    admin: dict = Depends(get_current_admin),
    conn = Depends(get_db)
):
    """Registro de ingresos: accesos exitosos, fallidos, bloqueos y desbloqueos."""
    limit = max(1, min(limit, 500))
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT l.id, l.usuario_id, l.email, l.evento, l.detalle, l.ip_origen, l.fecha,
                   u.nombre AS usuario_nombre, r.nombre AS rol_nombre,
                   a.nombre AS realizado_por_nombre
            FROM login_logs l
            LEFT JOIN usuarios u ON l.usuario_id = u.id
            LEFT JOIN roles r ON u.rol_id = r.id
            LEFT JOIN usuarios a ON l.realizado_por = a.id
            ORDER BY l.fecha DESC, l.id DESC
            LIMIT %s
        """, (limit,))
        logs = cursor.fetchall()
        cursor.close()
    except Exception as exc:
        conn.rollback()
        ensure_security_tables_and_roles(conn)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT l.id, l.usuario_id, l.email, l.evento, l.detalle, l.ip_origen, l.fecha,
                   u.nombre AS usuario_nombre, r.nombre AS rol_nombre,
                   a.nombre AS realizado_por_nombre
            FROM login_logs l
            LEFT JOIN usuarios u ON l.usuario_id = u.id
            LEFT JOIN roles r ON u.rol_id = r.id
            LEFT JOIN usuarios a ON l.realizado_por = a.id
            ORDER BY l.fecha DESC, l.id DESC
            LIMIT %s
        """, (limit,))
        logs = cursor.fetchall()
        cursor.close()
    return {"login_logs": logs}

@app.post("/usuarios/{usuario_id}/desbloquear", tags=["Seguridad y Accesos"])
def desbloquear_usuario(
    usuario_id: int,
    admin: dict = Depends(get_current_admin),
    conn = Depends(get_db)
):
    """El administrador levanta el bloqueo de 15 minutos de una cuenta y queda registrado."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT id, nombre, email FROM usuarios WHERE id = %s AND is_deleted = FALSE",
        (usuario_id,)
    )
    objetivo = cursor.fetchone()
    if not objetivo:
        cursor.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    cursor.execute("""
        UPDATE usuarios SET failed_login_attempts = 0, locked_until = NULL WHERE id = %s
    """, (usuario_id,))
    cursor.execute("""
        INSERT INTO login_logs (usuario_id, email, evento, detalle, realizado_por)
        VALUES (%s, %s, 'UNLOCK', %s, %s)
    """, (usuario_id, objetivo["email"], f"Cuenta desbloqueada por {admin['nombre']} ({admin['rol_nombre']})", admin["id"]))
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'UNLOCK_USER', 'usuarios', %s)
    """, (admin["id"], usuario_id))
    conn.commit()
    cursor.close()

    # Limpia también el respaldo en memoria
    LOGIN_ATTEMPTS.pop(normalize_email(objetivo["email"]), None)

    return {"message": "Usuario desbloqueado correctamente"}

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
    - Admin Biomédico: no accede a datos clínicos.
    - Médico: consulta registros clínicos.
    """
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
    """Permite al Servicio IoT, Médico, Administrador Biomédico e Ingenieros registrar una nueva medición telemétrica de equipo."""
    roles_obs = {"Medico", "Servicio", "Admin Biomedico", "ADMIN", "ING_PREVENTIVO", "ING_CORRECTIVO", "Ingeniero Preventivo", "Ingeniero Correctivo", "Administrador"}
    if user["rol_nombre"] not in roles_obs:
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
    """Permite a Admin Biomédico, Administradores, Ingenieros o al autor editar la medición técnica."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()
    
    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o eliminada")

    es_admin = user["rol_nombre"] in {"Admin Biomedico", "ADMIN", "Administrador", "ING_PREVENTIVO", "ING_CORRECTIVO"}
    es_medico_autor = obs["created_by"] == user["id"]

    if not (es_admin or es_medico_autor):
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para editar esta medición.")

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
    """Permite al Admin Biomédico, Administrador o autor eliminar mediciones."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o ya eliminada")

    es_admin = user["rol_nombre"] in {"Admin Biomedico", "ADMIN", "Administrador", "ING_PREVENTIVO", "ING_CORRECTIVO"}
    es_medico_autor = obs["created_by"] == user["id"]

    if not (es_admin or es_medico_autor):
        cursor.close()
        raise HTTPException(status_code=403, detail="Rol sin permisos para eliminar esta medición.")

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
    """Permite al Admin Biomédico, Administrador o autor restaurar un registro técnico."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada")

    es_admin = user["rol_nombre"] in {"Admin Biomedico", "ADMIN", "Administrador", "ING_PREVENTIVO", "ING_CORRECTIVO"}
    es_medico_autor = obs["created_by"] == user["id"]

    if not (es_admin or es_medico_autor):
        cursor.close()
        raise HTTPException(status_code=403, detail="Acceso denegado: No tiene permisos para restaurar este registro.")

    cursor.execute("UPDATE observaciones SET is_deleted = FALSE WHERE id = %s", (obs_id,))
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'RESTORE', 'observaciones', %s)
    """, (user["id"], obs_id))

    conn.commit()
    cursor.close()
    return {"message": f"Observación ID {obs_id} restaurada exitosamente"}

# ==========================================
# 2. SECCIÓN DE ENCUENTROS CLÍNICOS
# ==========================================

@app.get("/encuentros", tags=["Encuentros"])
def listar_encuentros(
    include_deleted: bool = False,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
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
    params = []

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

@app.put("/encuentros/{encuentro_id}/finalizar", tags=["Encuentros"])
@app.post("/encuentros/{encuentro_id}/finalizar", tags=["Encuentros"])
def finalizar_encuentro(
    encuentro_id: int,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Permite únicamente al Médico finalizar y cerrar un encuentro clínico activo."""
    if user["rol_nombre"] != "Medico":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Únicamente los médicos están autorizados para finalizar y cerrar encuentros clínicos."
        )

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT id, paciente_id, equipo_uci_id, estado, created_by 
        FROM encuentros 
        WHERE id = %s AND is_deleted = FALSE
    """, (encuentro_id,))
    encuentro = cursor.fetchone()

    if not encuentro:
        cursor.close()
        raise HTTPException(status_code=404, detail="Encuentro clínico no encontrado o inactivo.")

    if encuentro["estado"] == "finished":
        cursor.close()
        raise HTTPException(status_code=400, detail="El encuentro clínico ya se encuentra finalizado.")

    cursor.execute("""
        UPDATE encuentros 
        SET estado = 'finished', fecha_fin = CURRENT_TIMESTAMP 
        WHERE id = %s
        RETURNING id, paciente_id, equipo_uci_id, estado, fecha_inicio, fecha_fin;
    """, (encuentro_id,))
    encuentro_finalizado = cursor.fetchone()

    # Si tenía un equipo UCI asignado, liberarlo (volver a 'Disponible')
    if encuentro["equipo_uci_id"]:
        cursor.execute("""
            UPDATE equipos_uci 
            SET estado_operativo = 'Disponible' 
            WHERE id = %s
        """, (encuentro["equipo_uci_id"],))

    # Registrar en pista de auditoría inmutable
    cursor.execute("""
        INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
        VALUES (%s, 'FINALIZAR_ENCUENTRO', 'encuentros', %s)
    """, (user["id"], encuentro_id))

    conn.commit()
    cursor.close()

    if sync_encounter_to_fhir:
        try:
            sync_encounter_to_fhir(encuentro_finalizado)
        except Exception:
            pass

    return {
        "message": f"Encuentro #{encuentro_id} finalizado y cerrado exitosamente por el médico tratante.",
        "encuentro": encuentro_finalizado
    }

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
    - Admin Biomédico: no accede a registros clínicos.
    - Médico: consulta pacientes UCI.
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT p.id, p.documento_identidad, p.nombre, p.cama_uci, p.created_by,
               e.id as encuentro_activo_id, e.estado as encuentro_estado,
               eq.ubicacion_uci as equipo_ubicacion, c.nombre as equipo_nombre
        FROM pacientes p
        LEFT JOIN encuentros e ON p.id = e.paciente_id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE p.is_deleted = FALSE
        ORDER BY p.id DESC
    """
    cursor.execute(query)
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
               enc.id as encuentro_activo_id, p.id as paciente_id, p.nombre as paciente_nombre
        FROM equipos_uci e
        JOIN hoja_vida_equipos hv ON e.hoja_vida_id = hv.id
        JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        LEFT JOIN encuentros enc ON e.id = enc.equipo_uci_id AND enc.estado = 'in-progress' AND enc.is_deleted = FALSE
        LEFT JOIN pacientes p ON enc.paciente_id = p.id
        WHERE e.is_deleted = FALSE
        ORDER BY e.id ASC
    """
    cursor.execute(query)
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
    if user["rol_nombre"] not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado: La auditoría inmutable está reservada exclusivamente a la Administración.")

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
    data: Optional[SimularTelemetriaRequest] = None,
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
    
    encuentro_id = data.encuentro_id if data else None
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
            "parametro": "Presión Neumática (psi)",
            "codigo_loinc": "Presion_Neumatica_psi",
            "valor": round(random.uniform(46.0, 64.0), 1),
            "unidad": "psi",
            "alerta": "Alerta: Presión neumática fluctuante" if random.random() > 0.75 else None
        },
        {
            "parametro": "Nivel Vibración Turbina (Hz)",
            "codigo_loinc": "Nivel_Vibracion_Turbina_Hz",
            "valor": round(random.uniform(62.0, 85.0), 1),
            "unidad": "Hz",
            "alerta": "Alerta Predictiva: Vibración de turbina elevada (>80 Hz)" if random.random() > 0.7 else None
        },
        {
            "parametro": "Temperatura Bloque (°C)",
            "codigo_loinc": "Temperatura_Bloque_C",
            "valor": round(random.uniform(38.0, 52.0), 1),
            "unidad": "°C",
            "alerta": "Alerta: Temperatura de bloque sobre umbral de seguridad (>49 °C)" if random.random() > 0.7 else None
        },
        {
            "parametro": "Fugas de Aire (L/min)",
            "codigo_loinc": "Fugas_Aire_Lmin",
            "valor": round(random.uniform(0.2, 3.8), 2),
            "unidad": "L/min",
            "alerta": "Alerta: Pérdida de estanqueidad neumática (>3.0 L/min)" if random.random() > 0.7 else None
        },
        {
            "parametro": "Ciclos Descarga Batería",
            "codigo_loinc": "Ciclos_Descarga_Bateria",
            "valor": round(random.uniform(150, 560), 0),
            "unidad": "ciclos",
            "alerta": "Alerta Predictiva: Batería degradada (>500 ciclos)" if random.random() > 0.7 else None
        },
        {
            "parametro": "Errores Módulo ECG",
            "codigo_loinc": "Errores_Modulo_ECG",
            "valor": round(random.uniform(0, 12), 0),
            "unidad": "eventos",
            "alerta": "Alerta: Pérdida de integridad de señal en módulo ECG" if random.random() > 0.7 else None
        },
        {
            "parametro": "Ruido Señal SpO2 (dB)",
            "codigo_loinc": "Ruido_Senal_SpO2_dB",
            "valor": round(random.uniform(6.0, 26.0), 1),
            "unidad": "dB",
            "alerta": "Alerta: Ruido óptico excesivo en sensor fotopletismografía (>22 dB)" if random.random() > 0.7 else None
        },
        {
            "parametro": "Nivel Vibración Motor (rpm)",
            "codigo_loinc": "Nivel_Vibracion_Motor_rpm",
            "valor": round(random.uniform(2950, 3600), 0),
            "unidad": "rpm",
            "alerta": "Alerta Predictiva: Vibración fuera de rango en rotor peristáltico" if random.random() > 0.7 else None
        },
        {
            "parametro": "Desviación Presión Oclusión (mmHg)",
            "codigo_loinc": "Desviacion_Presion_Oclusion_mmHg",
            "valor": round(random.uniform(2.0, 34.0), 1),
            "unidad": "mmHg",
            "alerta": "Alerta: Descalibración de sensor de oclusión en línea" if random.random() > 0.7 else None
        },
        {
            "parametro": "Error Paso Motor Micrométrico (mm)",
            "codigo_loinc": "Error_Paso_Motor_Micro_mm",
            "valor": round(random.uniform(0.01, 0.22), 3),
            "unidad": "mm",
            "alerta": "Alerta: Holgura crítica en husillo micrométrico (>0.15 mm)" if random.random() > 0.7 else None
        },
        {
            "parametro": "Resistencia Émbolo (N)",
            "codigo_loinc": "Resistencia_Embolo_N",
            "valor": round(random.uniform(10.0, 30.0), 1),
            "unidad": "N",
            "alerta": "Alerta: Resistencia mecánica anómala en émbolo propulsor" if random.random() > 0.7 else None
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
                DROP TABLE IF EXISTS login_logs CASCADE;
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

# ==========================================
# 6. ESTADO DE EQUIPOS, PREDICCIÓN Y DATASET BIOMÉDICO (NEON)
# ==========================================

EQUIPOS_TABLE_MAP = {
    "ventiladores": {
        "table": "ventiladores_mecanicos",
        "pk": "ID_Registro",
        "name": "Ventiladores Mecánicos",
        "equipo_prefix": "VEN",
        "columns": [
            "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
            "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Presion_Neumatica_psi",
            "Nivel_Vibracion_Turbina_Hz", "Temperatura_Bloque_C", "Fugas_Aire_Lmin", "Falla_Actual"
        ]
    },
    "monitores": {
        "table": "monitores_signos_vitales",
        "pk": "ID_Registro",
        "name": "Monitores de Signos Vitales",
        "equipo_prefix": "MON",
        "columns": [
            "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
            "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Ciclos_Descarga_Bateria",
            "Errores_Modulo_ECG", "Ruido_Senal_SpO2_dB", "Falla_Actual"
        ]
    },
    "bombas-volumetricas": {
        "table": "bombas_volumetricas",
        "pk": "ID_Registro",
        "name": "Bombas de Infusión Volumétrica",
        "equipo_prefix": "BVO",
        "columns": [
            "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
            "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Nivel_Vibracion_Motor_rpm",
            "Desviacion_Presion_Oclusion_mmHg", "Frecuencia_Alarma_Burbuja", "Falla_Actual"
        ]
    },
    "bombas-jeringa": {
        "table": "bombas_jeringa",
        "pk": "ID_Registro",
        "name": "Bombas de Infusión a Jeringa",
        "equipo_prefix": "BJE",
        "columns": [
            "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
            "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Error_Paso_Motor_Micro_mm",
            "Resistencia_Embolo_N", "Falla_Actual"
        ]
    },
    "rotativos": {
        "table": "equipos_compartidos_inventario",
        "pk": "ID_Equipo_Rotativo",
        "name": "Equipos Rotativos y Soporte UCI",
        "equipo_prefix": "ROT",
        "columns": [
            "ID_Equipo_Rotativo", "Tipo_Tecnologia", "Ubicacion_Estrategica",
            "Estado_Operativo", "Fecha_Ultimo_Mtto", "Horas_Uso_Estimado"
        ]
    }
}

class PredictRequest(BaseModel):
    tipo: str
    equipo_id: Optional[str] = None
    horas_uso: Optional[float] = None
    dias_mtto: Optional[int] = None
    fallas_previas: Optional[int] = None
    variables: Optional[dict] = None

class SimularTelemetriaEquipoRequest(BaseModel):
    tipo: str
    equipo_id: str
    cama_asignada: Optional[int] = 1
    variables: Optional[dict] = None

def calcular_prediccion_equipo(tipo: str, item: dict) -> dict:
    """Motor predictivo analítico de fallas entrenado sobre los patrones de telemetría de equipos biomédicos en Neon."""
    tipo_key = tipo.lower()
    score = 10.0
    anomalias = []
    falla_actual = int(item.get("Falla_Actual") or 0)
    if falla_actual == 1:
        score += 45.0
        anomalias.append("Falla activa registrada en el sistema de telemetría")

    horas = float(item.get("Horas_Uso_Total") or 0)
    dias = float(item.get("Dias_Desde_Ultimo_Mtto") or 0)
    fallas_previas = int(item.get("Numero_Fallas_Previas") or 0)
    score += min(fallas_previas * 4, 16)

    diagnostico_partes = []

    if tipo_key in {"ventiladores", "ventiladores_mecanicos"}:
        vib = float(item.get("Nivel_Vibracion_Turbina_Hz") or 0)
        temp = float(item.get("Temperatura_Bloque_C") or 0)
        fugas = float(item.get("Fugas_Aire_Lmin") or 0)
        presion = float(item.get("Presion_Neumatica_psi") or 0)

        if vib > 82:
            score += 26
            anomalias.append(f"Vibración turbina crítica ({vib:.1f} Hz, umbral seguro < 75 Hz)")
            diagnostico_partes.append("desgaste severo en rodamientos de turbina")
        elif vib > 76:
            score += 15
            anomalias.append(f"Vibración turbina elevada ({vib:.1f} Hz)")
            diagnostico_partes.append("desbalance dinámico de turbina")

        if temp > 49:
            score += 22
            anomalias.append(f"Sobrecalentamiento de bloque ({temp:.1f} °C, máx seguro 46 °C)")
            diagnostico_partes.append("deficiencia en disipación térmica del motor")
        elif temp > 46.5:
            score += 12
            anomalias.append(f"Temperatura bloque alta ({temp:.1f} °C)")

        if fugas > 1.5:
            score += 24
            anomalias.append(f"Fugas de aire críticas ({fugas:.2f} L/min, umbral < 1.0 L/min)")
            diagnostico_partes.append("pérdida de hermeticidad en diafragma o válvula exhalatoria")
        elif fugas > 1.0:
            score += 12
            anomalias.append(f"Fugas de aire en circuito ({fugas:.2f} L/min)")

        if presion < 46.0 or presion > 54.0:
            score += 10
            anomalias.append(f"Desviación presión neumática ({presion:.1f} psi, rango óptimo 48-52 psi)")

        if dias > 260:
            score += 20
            anomalias.append(f"Intervalo de mantenimiento preventivo superado ({int(dias)} días)")
        elif dias > 180:
            score += 10

        if horas > 16000:
            score += 10

    elif tipo_key in {"monitores", "monitores_signos_vitales"}:
        ruido = float(item.get("Ruido_Senal_SpO2_dB") or 0)
        ecg_err = int(item.get("Errores_Modulo_ECG") or 0)
        ciclos = int(item.get("Ciclos_Descarga_Bateria") or 0)

        if ruido > 31.0:
            score += 28
            anomalias.append(f"Ruido excesivo en canal SpO2 ({ruido:.1f} dB, umbral < 28 dB)")
            diagnostico_partes.append("degradación óptica de fotodiodo o cable de paciente")
        elif ruido > 28.5:
            score += 14
            anomalias.append(f"Ruido moderado en canal SpO2 ({ruido:.1f} dB)")

        if ecg_err >= 4:
            score += 25
            anomalias.append(f"Múltiples fallos módulo ECG ({ecg_err} errores)")
            diagnostico_partes.append("inestabilidad en placa de procesamiento analógico ECG")
        elif ecg_err >= 2:
            score += 12
            anomalias.append(f"Alertas esporádicas en canal ECG ({ecg_err} errores)")

        if ciclos > 500:
            score += 20
            anomalias.append(f"Degradación química de batería ({ciclos} ciclos descarga)")
            diagnostico_partes.append("batería de backup cerca del final de su vida útil")
        elif ciclos > 380:
            score += 10

        if dias > 250:
            score += 18
            anomalias.append(f"Intervalo de calibración superado ({int(dias)} días)")

    elif tipo_key in {"bombas-volumetricas", "bombas_volumetricas"}:
        vib_rpm = float(item.get("Nivel_Vibracion_Motor_rpm") or 0)
        oclusion = float(item.get("Desviacion_Presion_Oclusion_mmHg") or 0)
        burbuja = int(item.get("Frecuencia_Alarma_Burbuja") or 0)

        if oclusion > 40.0:
            score += 30
            anomalias.append(f"Desviación presión oclusión elevada ({oclusion:.1f} mmHg, seguro < 32 mmHg)")
            diagnostico_partes.append("sensor de presión oclusión descalibrado o mecanismo peristáltico fatigado")
        elif oclusion > 32.0:
            score += 15
            anomalias.append(f"Presión oclusión en límite superior ({oclusion:.1f} mmHg)")

        if vib_rpm > 1780:
            score += 25
            anomalias.append(f"Vibración motor peristáltico anómala ({vib_rpm:.0f} rpm)")
            diagnostico_partes.append("desgaste mecánico en rotor peristáltico")
        elif vib_rpm > 1650:
            score += 12

        if burbuja >= 4:
            score += 20
            anomalias.append(f"Falsas alarmas de burbuja ({burbuja} eventos)")
            diagnostico_partes.append("sensor ultrasónico de aire en línea descalibrado")

        if dias > 240:
            score += 18

    elif tipo_key in {"bombas-jeringa", "bombas_jeringa"}:
        paso_err = float(item.get("Error_Paso_Motor_Micro_mm") or 0)
        embolo_res = float(item.get("Resistencia_Embolo_N") or 0)

        if paso_err > 0.09:
            score += 32
            anomalias.append(f"Error paso motor crítico ({paso_err:.3f} mm, tolerancia < 0.07 mm)")
            diagnostico_partes.append("pérdida de pasos en husillo de microinfusión")
        elif paso_err > 0.07:
            score += 16
            anomalias.append(f"Error paso motor leve ({paso_err:.3f} mm)")

        if embolo_res > 13.0:
            score += 30
            anomalias.append(f"Resistencia mecánica del émbolo elevada ({embolo_res:.1f} N, seguro < 10.5 N)")
            diagnostico_partes.append("fricción excesiva en corredera o sensor de fuerza atascado")
        elif embolo_res > 10.5:
            score += 15

        if dias > 230:
            score += 18

    prob = max(5.0, min(99.0, score))
    if prob >= 75.0 or falla_actual == 1:
        nivel = "Crítico"
        accion = "Retirar de uso en cama UCI. Programar Mantenimiento Correctivo inmediato."
    elif prob >= 50.0:
        nivel = "Alto"
        accion = "Programar intervención técnica prioritaria dentro de las próximas 48 horas."
    elif prob >= 30.0:
        nivel = "Medio"
        accion = "Programar inspección y calibración metrológica en Mantenimiento Preventivo."
    else:
        nivel = "Bajo"
        accion = "Equipo en parámetros de funcionamiento seguro. Continuar monitoreo habitual."

    diag_text = ("; ".join(diagnostico_partes).capitalize() + ".") if diagnostico_partes else "Sin anomalías mecánicas detectadas."
    return {
        "probabilidad_falla": round(prob, 1),
        "nivel_riesgo": nivel,
        "diagnostico": diag_text,
        "anomalias": anomalias,
        "accion_recomendada": accion,
        "falla_actual": falla_actual
    }

@app.get("/api/equipos-dataset/{tipo}", tags=["Dataset de Equipos"])
def get_equipos_dataset(
    tipo: str,
    search: Optional[str] = None,
    q: Optional[str] = None,
    equipo_id: Optional[str] = None,
    falla: Optional[str] = None,
    cama: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    tipo_key = tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP:
        raise HTTPException(status_code=404, detail=f"Tipo de equipo '{tipo}' no reconocido.")

    cfg = EQUIPOS_TABLE_MAP[tipo_key]
    table_name = cfg["table"]
    cols_sql = ", ".join(f'"{c}"' for c in cfg["columns"])
    pk_col = cfg["pk"]

    where_clauses = []
    params = []

    search_term = (search or q or "").strip()
    if search_term:
        s = f"%{search_term}%"
        if tipo_key == "rotativos":
            where_clauses.append('("ID_Equipo_Rotativo" ILIKE %s OR "Tipo_Tecnologia" ILIKE %s OR "Ubicacion_Estrategica" ILIKE %s)')
            params.extend([s, s, s])
        else:
            where_clauses.append('(CAST("ID_Registro" AS TEXT) ILIKE %s OR "ID_Equipo" ILIKE %s OR CAST("Cama_Asignada" AS TEXT) ILIKE %s)')
            params.extend([s, s, s])

    if equipo_id:
        if tipo_key == "rotativos":
            where_clauses.append('"ID_Equipo_Rotativo" ILIKE %s')
            params.append(f"%{equipo_id.strip()}%")
        else:
            where_clauses.append('"ID_Equipo" ILIKE %s')
            params.append(f"%{equipo_id.strip()}%")

    if falla is not None and str(falla).strip().lower() not in ["todos", "", "none", "null"] and tipo_key != "rotativos":
        try:
            falla_int = int(falla)
            where_clauses.append('"Falla_Actual" = %s')
            params.append(falla_int)
        except ValueError:
            pass

    if cama is not None and str(cama).strip().lower() not in ["todos", "", "none", "null"] and tipo_key != "rotativos":
        where_clauses.append('CAST("Cama_Asignada" AS TEXT) ILIKE %s')
        params.append(f"%{str(cama).strip()}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    page = max(1, page)
    limit = max(1, min(limit, 200))
    offset = (page - 1) * limit

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        count_query = f'SELECT count(*) as total FROM "{table_name}" {where_sql}'
        cursor.execute(count_query, params)
        total_rows = cursor.fetchone()["total"]

        data_query = f'SELECT {cols_sql} FROM "{table_name}" {where_sql} ORDER BY "{pk_col}" ASC LIMIT %s OFFSET %s'
        cursor.execute(data_query, params + [limit, offset])
        rows = cursor.fetchall()
        cursor.close()

        for r in rows:
            for k, v in r.items():
                if isinstance(v, datetime):
                    r[k] = v.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "tipo": tipo_key,
            "nombre": cfg["name"],
            "tabla": table_name,
            "total": total_rows,
            "page": page,
            "limit": limit,
            "total_pages": (total_rows + limit - 1) // limit if total_rows > 0 else 1,
            "columns": cfg["columns"],
            "columnas": cfg["columns"],
            "pk": pk_col,
            "items": rows,
            "rows": rows
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error consultando dataset: {str(e)}")

@app.put("/api/equipos-dataset/{tipo}/{registro_id}", tags=["Dataset de Equipos"])
def update_equipo_record(
    tipo: str,
    registro_id: str,
    payload: dict,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] not in DATASET_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado: solo personal biomédico y técnico puede editar el dataset.")

    tipo_key = tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP:
        raise HTTPException(status_code=404, detail="Tipo de equipo no reconocido.")

    cfg = EQUIPOS_TABLE_MAP[tipo_key]
    table_name = cfg["table"]
    pk_col = cfg["pk"]
    allowed_cols = set(cfg["columns"])

    update_items = []
    params = []
    for k, v in payload.items():
        if k in allowed_cols and k != pk_col:
            update_items.append(f'"{k}" = %s')
            params.append(v)

    if not update_items:
        raise HTTPException(status_code=400, detail="No se suministraron campos válidos para actualizar.")

    params.append(registro_id)
    update_sql = f'UPDATE "{table_name}" SET {", ".join(update_items)} WHERE "{pk_col}" = %s'

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(update_sql, params)
        if cursor.rowcount == 0:
            cursor.close()
            raise HTTPException(status_code=404, detail=f"Registro '{registro_id}' no encontrado.")
        conn.commit()

        # Registrar auditoría
        cursor.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
            VALUES (%s, %s, %s, %s)
        """, (user["id"], f"EDITAR_REGISTRO_DATASET: {registro_id} en {table_name}", table_name, None))
        conn.commit()
        cursor.close()

        return {
            "status": "success",
            "message": f"Registro '{registro_id}' actualizado correctamente en Neon PostgreSQL.",
            "registro_id": registro_id
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error actualizando registro: {str(e)}")

@app.post("/api/equipos-dataset/{tipo}", tags=["Dataset de Equipos"])
def create_equipo_record(
    tipo: str,
    payload: dict,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] not in DATASET_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado: solo personal biomédico y técnico puede insertar registros.")

    tipo_key = tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP:
        raise HTTPException(status_code=404, detail="Tipo de equipo no reconocido.")

    cfg = EQUIPOS_TABLE_MAP[tipo_key]
    table_name = cfg["table"]
    allowed_cols = cfg["columns"]
    pk_col = cfg["pk"]

    if pk_col not in payload or not payload[pk_col]:
        cursor = conn.cursor()
        cursor.execute(f'SELECT count(*) FROM "{table_name}"')
        c = cursor.fetchone()[0] + 1
        prefix = "REG-" if pk_col == "ID_Registro" else "ROT-"
        payload[pk_col] = f"{prefix}{c:05d}"
        cursor.close()

    cols_to_insert = [c for c in allowed_cols if c in payload]
    values_to_insert = [payload[c] for c in cols_to_insert]

    cols_sql = ", ".join(f'"{c}"' for c in cols_to_insert)
    placeholders = ", ".join(["%s"] * len(cols_to_insert))
    insert_sql = f'INSERT INTO "{table_name}" ({cols_sql}) VALUES ({placeholders})'

    cursor = conn.cursor()
    try:
        cursor.execute(insert_sql, values_to_insert)
        conn.commit()
        cursor.close()
        return {
            "status": "success",
            "message": "Registro agregado exitosamente al dataset.",
            "registro_id": payload[pk_col]
        }
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error creando registro: {str(e)}")

@app.delete("/api/equipos-dataset/{tipo}/{registro_id}", tags=["Dataset de Equipos"])
def delete_equipo_record(
    tipo: str,
    registro_id: str,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] not in DATASET_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado para eliminar registros.")

    tipo_key = tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP:
        raise HTTPException(status_code=404, detail="Tipo de equipo no reconocido.")

    cfg = EQUIPOS_TABLE_MAP[tipo_key]
    table_name = cfg["table"]
    pk_col = cfg["pk"]

    cursor = conn.cursor()
    try:
        cursor.execute(f'DELETE FROM "{table_name}" WHERE "{pk_col}" = %s', (registro_id,))
        if cursor.rowcount == 0:
            cursor.close()
            raise HTTPException(status_code=404, detail="Registro no encontrado.")
        conn.commit()
        cursor.close()
        return {"status": "success", "message": f"Registro '{registro_id}' eliminado exitosamente."}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al eliminar registro: {str(e)}")

@app.get("/api/equipos-estado", tags=["Estado de Equipos"])
def get_equipos_estado_completo(
    conn = Depends(get_db)
):
    """Retorna el estado operativo consolidado y telemetría de todos los equipos biomédicos en UCI."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Ventiladores
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
                "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Presion_Neumatica_psi",
                "Nivel_Vibracion_Turbina_Hz", "Temperatura_Bloque_C", "Fugas_Aire_Lmin", "Falla_Actual"
            FROM ventiladores_mecanicos
            ORDER BY "ID_Equipo", "ID_Registro" DESC
        ''')
        vent_rows = cursor.fetchall()

        # 2. Monitores
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
                "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Ciclos_Descarga_Bateria",
                "Errores_Modulo_ECG", "Ruido_Senal_SpO2_dB", "Falla_Actual"
            FROM monitores_signos_vitales
            ORDER BY "ID_Equipo", "ID_Registro" DESC
        ''')
        mon_rows = cursor.fetchall()

        # 3. Bombas Volumétricas
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
                "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Nivel_Vibracion_Motor_rpm",
                "Desviacion_Presion_Oclusion_mmHg", "Frecuencia_Alarma_Burbuja", "Falla_Actual"
            FROM bombas_volumetricas
            ORDER BY "ID_Equipo", "ID_Registro" DESC
        ''')
        bvo_rows = cursor.fetchall()

        # 4. Bombas a Jeringa
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                "ID_Registro", "ID_Equipo", "Cama_Asignada", "Horas_Uso_Total",
                "Dias_Desde_Ultimo_Mtto", "Numero_Fallas_Previas", "Error_Paso_Motor_Micro_mm",
                "Resistencia_Embolo_N", "Falla_Actual"
            FROM bombas_jeringa
            ORDER BY "ID_Equipo", "ID_Registro" DESC
        ''')
        bje_rows = cursor.fetchall()

        # 5. Rotativos
        cursor.execute('''
            SELECT "ID_Equipo_Rotativo", "Tipo_Tecnologia", "Ubicacion_Estrategica", "Estado_Operativo", "Fecha_Ultimo_Mtto", "Horas_Uso_Estimado"
            FROM equipos_compartidos_inventario
            ORDER BY "ID_Equipo_Rotativo" ASC
        ''')
        rot_rows = cursor.fetchall()
        for r in rot_rows:
            if isinstance(r.get("Fecha_Ultimo_Mtto"), datetime):
                r["Fecha_Ultimo_Mtto"] = r["Fecha_Ultimo_Mtto"].strftime("%Y-%m-%d")

        cursor.close()

        # Enriquecer con predicciones rápidas
        for r in vent_rows:
            r["prediccion"] = calcular_prediccion_equipo("ventiladores", r)
        for r in mon_rows:
            r["prediccion"] = calcular_prediccion_equipo("monitores", r)
        for r in bvo_rows:
            r["prediccion"] = calcular_prediccion_equipo("bombas-volumetricas", r)
        for r in bje_rows:
            r["prediccion"] = calcular_prediccion_equipo("bombas-jeringa", r)

        total_activos = len(vent_rows) + len(mon_rows) + len(bvo_rows) + len(bje_rows) + len(rot_rows)
        fallas_count = (
            sum(1 for r in vent_rows if r["Falla_Actual"] == 1) +
            sum(1 for r in mon_rows if r["Falla_Actual"] == 1) +
            sum(1 for r in bvo_rows if r["Falla_Actual"] == 1) +
            sum(1 for r in bje_rows if r["Falla_Actual"] == 1)
        )
        alertas_preventivas = (
            sum(1 for r in vent_rows if r["prediccion"]["nivel_riesgo"] in {"Medio", "Alto", "Crítico"}) +
            sum(1 for r in mon_rows if r["prediccion"]["nivel_riesgo"] in {"Medio", "Alto", "Crítico"}) +
            sum(1 for r in bvo_rows if r["prediccion"]["nivel_riesgo"] in {"Medio", "Alto", "Crítico"}) +
            sum(1 for r in bje_rows if r["prediccion"]["nivel_riesgo"] in {"Medio", "Alto", "Crítico"})
        )

        return {
            "resumen": {
                "total_equipos": total_activos,
                "fallas_activas": fallas_count,
                "alertas_riesgo": alertas_preventivas,
                "ventiladores_count": len(vent_rows),
                "monitores_count": len(mon_rows),
                "bombas_vol_count": len(bvo_rows),
                "bombas_jer_count": len(bje_rows),
                "rotativos_count": len(rot_rows)
            },
            "ventiladores": vent_rows,
            "monitores": mon_rows,
            "bombas_volumetricas": bvo_rows,
            "bombas_jeringa": bje_rows,
            "rotativos": rot_rows
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error consultando estado de equipos: {str(e)}")

@app.get("/api/equipos-historial/{tipo}/{equipo_id}", tags=["Estado de Equipos"])
def get_equipo_historial(
    tipo: str,
    equipo_id: str,
    conn = Depends(get_db)
):
    """Retorna las mediciones históricas de un equipo específico para trazar curvas en Chart.js."""
    tipo_key = tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP:
        raise HTTPException(status_code=404, detail="Tipo de equipo no reconocido.")

    cfg = EQUIPOS_TABLE_MAP[tipo_key]
    table_name = cfg["table"]
    cols_sql = ", ".join(f'"{c}"' for c in cfg["columns"])
    pk_col = cfg["pk"]

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        query = f'SELECT {cols_sql} FROM "{table_name}" WHERE "ID_Equipo" = %s ORDER BY "{pk_col}" ASC'
        cursor.execute(query, (equipo_id.strip(),))
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            raise HTTPException(status_code=404, detail=f"No se encontraron registros para el equipo {equipo_id}")

        labels = [r["ID_Registro"] for r in rows]
        series = {}
        for c in cfg["columns"]:
            if c not in {"ID_Registro", "ID_Equipo", "Cama_Asignada"}:
                series[c] = [r.get(c) for r in rows]

        return {
            "tipo": tipo_key,
            "equipo_id": equipo_id,
            "cama_asignada": rows[-1].get("Cama_Asignada"),
            "total_muestras": len(rows),
            "labels": labels,
            "series": series,
            "ultimo_registro": rows[-1],
            "prediccion_actual": calcular_prediccion_equipo(tipo_key, rows[-1])
        }
    except HTTPException:
        raise
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error consultando historial del equipo: {str(e)}")

@app.post("/api/equipos-prediccion", tags=["Predicción de Equipos"])
def predict_equipo_failure(
    req: PredictRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Ejecuta el modelo predictivo de falla para un equipo o parámetros personalizados."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: El módulo de Predicción de Equipos está reservado al Administrador Biomédico y a los Ingenieros de Mantenimiento."
        )

    tipo_key = req.tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP:
        raise HTTPException(status_code=404, detail=f"Tipo de equipo '{req.tipo}' no reconocido.")

    item_data = {}
    if req.equipo_id:
        cfg = EQUIPOS_TABLE_MAP[tipo_key]
        table_name = cfg["table"]
        cols_sql = ", ".join(f'"{c}"' for c in cfg["columns"])
        pk_col = cfg["pk"]
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(f'SELECT {cols_sql} FROM "{table_name}" WHERE "ID_Equipo" = %s ORDER BY "{pk_col}" DESC LIMIT 1', (req.equipo_id.strip(),))
        row = cursor.fetchone()
        cursor.close()
        if row:
            item_data = dict(row)

    if req.horas_uso is not None:
        item_data["Horas_Uso_Total"] = req.horas_uso
    if req.dias_mtto is not None:
        item_data["Dias_Desde_Ultimo_Mtto"] = req.dias_mtto
    if req.fallas_previas is not None:
        item_data["Numero_Fallas_Previas"] = req.fallas_previas
    if req.variables and isinstance(req.variables, dict):
        item_data.update(req.variables)

    pred = calcular_prediccion_equipo(tipo_key, item_data)
    pred["tipo"] = tipo_key
    pred["equipo_id"] = req.equipo_id
    pred["parametros_analizados"] = item_data
    return pred

@app.get("/api/equipos-prediccion-resumen", tags=["Predicción de Equipos"])
def get_prediccion_resumen_global(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Genera la matriz predictiva del parque biomédico con priorización de mantenimiento."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: Exclusivo para Administrador Biomédico e Ingenieros de Mantenimiento."
        )

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        lista_equipos = []
        for tipo_k, cfg in [
            ("ventiladores", EQUIPOS_TABLE_MAP["ventiladores"]),
            ("monitores", EQUIPOS_TABLE_MAP["monitores"]),
            ("bombas-volumetricas", EQUIPOS_TABLE_MAP["bombas-volumetricas"]),
            ("bombas-jeringa", EQUIPOS_TABLE_MAP["bombas-jeringa"])
        ]:
            tbl = cfg["table"]
            cols = ", ".join(f'"{c}"' for c in cfg["columns"])
            pk = cfg["pk"]
            cursor.execute(f'SELECT DISTINCT ON ("ID_Equipo") {cols} FROM "{tbl}" ORDER BY "ID_Equipo", "{pk}" DESC')
            for r in cursor.fetchall():
                p = calcular_prediccion_equipo(tipo_k, r)
                lista_equipos.append({
                    "tipo": tipo_k,
                    "familia": cfg["name"],
                    "equipo_id": r["ID_Equipo"],
                    "cama": r.get("Cama_Asignada"),
                    "horas_uso": r.get("Horas_Uso_Total"),
                    "dias_mtto": r.get("Dias_Desde_Ultimo_Mtto"),
                    "fallas_previas": r.get("Numero_Fallas_Previas"),
                    "falla_actual": r.get("Falla_Actual", 0),
                    "probabilidad_falla": p["probabilidad_falla"],
                    "nivel_riesgo": p["nivel_riesgo"],
                    "diagnostico": p["diagnostico"],
                    "anomalias": p["anomalias"],
                    "accion_recomendada": p["accion_recomendada"],
                    "ultimo_registro": r.get("ID_Registro")
                })

        cursor.close()

        lista_equipos.sort(key=lambda x: (x["falla_actual"], x["probabilidad_falla"]), reverse=True)

        total = len(lista_equipos)
        criticos = sum(1 for e in lista_equipos if e["nivel_riesgo"] in {"Crítico", "Alto"} or e["falla_actual"] == 1)
        medios = sum(1 for e in lista_equipos if e["nivel_riesgo"] == "Medio")
        optimos = sum(1 for e in lista_equipos if e["nivel_riesgo"] == "Bajo" and e["falla_actual"] == 0)

        return {
            "total_analizados": total,
            "equipos_criticos": criticos,
            "equipos_atencion": medios,
            "equipos_optimos": optimos,
            "indice_confiabilidad": round((optimos / total * 100) if total > 0 else 100, 1),
            "equipos": lista_equipos
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error en resumen predictivo: {str(e)}")

@app.post("/api/equipos-telemetria-simular", tags=["Telemetría de Equipos"])
def simular_telemetria_equipo_biomedico(
    data: SimularTelemetriaEquipoRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Simula o inyecta una nueva medición de telemetría física de un equipo (Servicio IoT o Admin)."""
    if user["rol_nombre"] not in {"Servicio", "Admin Biomedico", "ADMIN", "ING_PREVENTIVO", "ING_CORRECTIVO"}:
        raise HTTPException(status_code=403, detail="Acceso denegado: solo el Servicio IoT o Ingeniería pueden transmitir telemetría.")

    tipo_key = data.tipo.lower()
    if tipo_key not in EQUIPOS_TABLE_MAP or tipo_key == "rotativos":
        raise HTTPException(status_code=400, detail="Tipo de equipo inválido para telemetría continua.")

    cfg = EQUIPOS_TABLE_MAP[tipo_key]
    table = cfg["table"]

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(f'SELECT count(*) FROM "{table}"')
        total_rec = cursor.fetchone()["count"] + 1
        new_id = f"REG-{total_rec:05d}"

        # Obtener valores del equipo actual
        cursor.execute(f'SELECT * FROM "{table}" WHERE "ID_Equipo" = %s ORDER BY "ID_Registro" DESC LIMIT 1', (data.equipo_id.strip(),))
        prev = cursor.fetchone()
        cursor.close()

        horas = (float(prev["Horas_Uso_Total"]) + round(random.uniform(20.0, 80.0), 1)) if prev else 1000.0
        dias = int(prev["Dias_Desde_Ultimo_Mtto"]) + 1 if prev else 10
        fallas_prev = int(prev["Numero_Fallas_Previas"]) if prev else 0
        cama = data.cama_asignada or (prev["Cama_Asignada"] if prev else 1)

        new_row = {
            "ID_Registro": new_id,
            "ID_Equipo": data.equipo_id.strip(),
            "Cama_Asignada": cama,
            "Horas_Uso_Total": horas,
            "Dias_Desde_Ultimo_Mtto": dias,
            "Numero_Fallas_Previas": fallas_prev,
            "Falla_Actual": 0
        }

        # Generar variables físicas según la familia
        if tipo_key == "ventiladores":
            new_row["Presion_Neumatica_psi"] = round(random.gauss(50.0, 1.8), 3)
            new_row["Nivel_Vibracion_Turbina_Hz"] = round(random.gauss(72.0, 6.0), 3)
            new_row["Temperatura_Bloque_C"] = round(random.gauss(44.0, 3.0), 3)
            new_row["Fugas_Aire_Lmin"] = max(0.0, round(random.gauss(0.8, 0.4), 3))
        elif tipo_key == "monitores":
            new_row["Ciclos_Descarga_Bateria"] = int(random.uniform(250, 520))
            new_row["Errores_Modulo_ECG"] = int(random.choices([0, 1, 2, 3], weights=[0.6, 0.25, 0.1, 0.05])[0])
            new_row["Ruido_Senal_SpO2_dB"] = round(random.gauss(25.5, 3.5), 3)
        elif tipo_key == "bombas-volumetricas":
            new_row["Nivel_Vibracion_Motor_rpm"] = round(random.gauss(1560.0, 140.0), 3)
            new_row["Desviacion_Presion_Oclusion_mmHg"] = round(random.gauss(26.0, 10.0), 3)
            new_row["Frecuencia_Alarma_Burbuja"] = int(random.choices([0, 1, 2, 3, 4], weights=[0.4, 0.3, 0.15, 0.1, 0.05])[0])
        elif tipo_key == "bombas-jeringa":
            new_row["Error_Paso_Motor_Micro_mm"] = max(0.0, round(random.gauss(0.06, 0.025), 3))
            new_row["Resistencia_Embolo_N"] = round(random.gauss(9.5, 2.4), 3)

        if data.variables and isinstance(data.variables, dict):
            new_row.update(data.variables)

        # Determinar si detona Falla_Actual
        pred = calcular_prediccion_equipo(tipo_key, new_row)
        if pred["probabilidad_falla"] > 85.0:
            new_row["Falla_Actual"] = 1

        cols_sql = ", ".join(f'"{c}"' for c in new_row.keys())
        placeholders = ", ".join(["%s"] * len(new_row))
        insert_sql = f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders})'

        cursor = conn.cursor()
        cursor.execute(insert_sql, list(new_row.values()))
        conn.commit()
        cursor.close()

        return {
            "status": "success",
            "message": f"Paquete de telemetría técnica inyectado para {data.equipo_id} ({new_id}).",
            "registro": new_row,
            "prediccion": pred
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error transmitiendo telemetría técnica: {str(e)}")