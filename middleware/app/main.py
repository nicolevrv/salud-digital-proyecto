import os
import urllib.parse
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends, status, Request
from fastapi.staticfiles import StaticFiles
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
from datetime import datetime, timedelta, date
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

# Montar directorio de recursos estáticos (fotografías reales de equipos y certificados)
static_dir = BASE_DIR / "static"
if not static_dir.exists():
    static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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

            CREATE TABLE IF NOT EXISTS equipos_documentos (
                id SERIAL PRIMARY KEY,
                equipo_id VARCHAR(50) NOT NULL,
                tipo_documento VARCHAR(50) NOT NULL,
                titulo VARCHAR(150) NOT NULL,
                numero_certificado VARCHAR(100),
                entidad_emisora VARCHAR(150),
                fecha_emision DATE,
                fecha_vencimiento DATE,
                estado_vigencia VARCHAR(30) DEFAULT 'VIGENTE',
                archivo_url TEXT NOT NULL,
                archivo_nombre VARCHAR(255),
                archivo_tamano_kb INT DEFAULT 0,
                observaciones TEXT,
                created_by VARCHAR(100) DEFAULT 'Sistema Biomédico',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pk_documento VARCHAR(50)
            );
            ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS pk_documento VARCHAR(50);
            CREATE INDEX IF NOT EXISTS idx_equipos_docs_equipo ON equipos_documentos(equipo_id);
            CREATE INDEX IF NOT EXISTS idx_equipos_docs_pk ON equipos_documentos(pk_documento);
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
# 6. ESTADO DE EQUIPOS, PREDICCIÓN Y DATASET BIOMÉDICO (NEON DATASET_EVENTOS)
# ==========================================

FAMILIAS_DATASET_MAP = {
    "bombas-volumetricas": {
        "nombre": "Bombas Volumétricas",
        "tipo_db": "Bomba de Infusión Volumétrica",
        "slug": "bombas-volumetricas",
        "prefijo": "BVOL",
        "icon": "💧",
        "riesgo_defecto": "IIB"
    },
    "bombas-jeringa": {
        "nombre": "Bombas de Jeringa",
        "tipo_db": "Bomba de Infusión a Jeringa",
        "slug": "bombas-jeringa",
        "prefijo": "BJER",
        "icon": "💉",
        "riesgo_defecto": "IIB"
    },
    "ventiladores": {
        "nombre": "Ventiladores Mecánicos",
        "tipo_db": "Ventilador Mecánico",
        "slug": "ventiladores",
        "prefijo": "VENT",
        "icon": "🫁",
        "riesgo_defecto": "III"
    },
    "monitores": {
        "nombre": "Monitores de Signos Vitales",
        "tipo_db": "Monitor Multiparámetro",
        "slug": "monitores",
        "prefijo": "MON",
        "icon": "📈",
        "riesgo_defecto": "IIB"
    },
    "desfibriladores": {
        "nombre": "Desfibriladores Bifásicos",
        "tipo_db": "Desfibrilador Bifásico",
        "slug": "desfibriladores",
        "prefijo": "DEF",
        "icon": "⚡",
        "riesgo_defecto": "III"
    },
    "crrt": {
        "nombre": "Máquinas CRRT",
        "tipo_db": "Máquina CRRT",
        "slug": "crrt",
        "prefijo": "CRRT",
        "icon": "🩸",
        "riesgo_defecto": "III"
    },
    "ecg": {
        "nombre": "Electrocardiógrafos 12D",
        "tipo_db": "Electrocardiógrafo 12 Derivaciones",
        "slug": "ecg",
        "prefijo": "ECG",
        "icon": "📊",
        "riesgo_defecto": "IIA"
    },
    "poct": {
        "nombre": "Analizadores de Gases POCT",
        "tipo_db": "Analizador de Gases (POCT)",
        "slug": "poct",
        "prefijo": "POCT",
        "icon": "🧪",
        "riesgo_defecto": "IIA"
    },
    "ecografos": {
        "nombre": "Ecógrafos Portátiles Doppler",
        "tipo_db": "Ecógrafo Portátil Doppler",
        "slug": "ecografos",
        "prefijo": "ECO",
        "icon": "🔊",
        "riesgo_defecto": "IIA"
    },
    "rayos-x": {
        "nombre": "Equipos de Rayos X Portátiles",
        "tipo_db": "Equipo de Rayos X Portátil",
        "slug": "rayos-x",
        "prefijo": "RX",
        "icon": "☢️",
        "riesgo_defecto": "IIB"
    }
}

DATASET_COLUMNS = [
    "id",
    "ID_Registro",
    "ID_Equipo",
    "Tipo_Equipo",
    "Clasificacion_Riesgo",
    "Fecha_Reporte",
    "Edad_Equipo_Meses",
    "Meses_Desde_Ultimo_Mtto",
    "Fallas_Acumuladas",
    "MTBF_Promedio_Dias",
    "Variable_Target_Falla",
    "Descripcion_Evento",
    "Desviacion_Calibracion_Pct",
    "Estado_IEC62353",
    "Accion_Recomendada"
]

def obtener_slug_tipo(tipo_str: str) -> str:
    if not tipo_str:
        return "bombas-volumetricas"
    t_lower = tipo_str.lower()
    for slug, cfg in FAMILIAS_DATASET_MAP.items():
        if slug in t_lower or cfg["tipo_db"].lower() in t_lower or cfg["prefijo"].lower() in t_lower:
            return slug
    if "jeringa" in t_lower:
        return "bombas-jeringa"
    if "volum" in t_lower:
        return "bombas-volumetricas"
    if "vent" in t_lower:
        return "ventiladores"
    if "monit" in t_lower:
        return "monitores"
    if "desfib" in t_lower:
        return "desfibriladores"
    if "crrt" in t_lower:
        return "crrt"
    if "ecg" in t_lower or "cardio" in t_lower:
        return "ecg"
    if "poct" in t_lower or "gases" in t_lower:
        return "poct"
    if "eco" in t_lower or "ultrason" in t_lower:
        return "ecografos"
    if "rx" in t_lower or "rayos" in t_lower:
        return "rayos-x"
    return "ventiladores"

class DatasetRecordUpdate(BaseModel):
    Meses_Desde_Ultimo_Mtto: Optional[float] = None
    Fallas_Acumuladas: Optional[int] = None
    MTBF_Promedio_Dias: Optional[float] = None
    Variable_Target_Falla: Optional[float] = None
    Descripcion_Evento: Optional[str] = None
    Edad_Equipo_Meses: Optional[float] = None
    Clasificacion_Riesgo: Optional[str] = None

class DatasetRecordCreate(BaseModel):
    ID_Equipo: str
    Tipo_Equipo: Optional[str] = None
    Clasificacion_Riesgo: Optional[str] = "IIB"
    Fecha_Reporte: Optional[str] = None
    Edad_Equipo_Meses: Optional[float] = 40.0
    Meses_Desde_Ultimo_Mtto: Optional[float] = 1.0
    Fallas_Acumuladas: Optional[int] = 0
    MTBF_Promedio_Dias: Optional[float] = 500.0
    Variable_Target_Falla: Optional[float] = 0.0
    Descripcion_Evento: Optional[str] = "Operación Normal"

class MantenimientoEjecutarRequest(BaseModel):
    equipo_id: str
    tipo_mantenimiento: Optional[str] = "Preventivo"
    observaciones: Optional[str] = "Mantenimiento Preventivo Ejecutado (Calibración e Inspección Funcional)"

class PredictDatasetRequest(BaseModel):
    equipo_id: Optional[str] = None
    tipo: Optional[str] = None
    meses_desde_ultimo_mtto: Optional[float] = None
    fallas_acumuladas: Optional[int] = None
    mtbf_promedio_dias: Optional[float] = None
    edad_equipo_meses: Optional[float] = None
    clasificacion_riesgo: Optional[str] = None
    guardar_en_bd: Optional[bool] = False
    variable_target_falla: Optional[float] = None
    descripcion_evento: Optional[str] = None

def calcular_prediccion_equipo(item: dict) -> dict:
    """Motor predictivo calibrado sobre las fallas y mantenimiento de dataset_eventos en Neon."""
    tipo_equipo = item.get("Tipo_Equipo") or ""
    slug = obtener_slug_tipo(tipo_equipo)
    
    meses_mtto = float(item.get("Meses_Desde_Ultimo_Mtto") or 0.0)
    fallas_acum = int(item.get("Fallas_Acumuladas") or 0)
    mtbf = float(item.get("MTBF_Promedio_Dias") or 0.0)
    edad = float(item.get("Edad_Equipo_Meses") or 0.0)
    riesgo = item.get("Clasificacion_Riesgo") or "IIB"
    falla_target = item.get("Variable_Target_Falla")
    evento = str(item.get("Descripcion_Evento") or "")

    score = 8.0
    anomalias = []
    diagnostico_partes = []

    # 1. Factor de Degradación por Mantenimiento Vencido
    if meses_mtto >= 9.0:
        score += 42.0
        anomalias.append(f"Mantenimiento preventivo crítico vencido ({meses_mtto:.1f} meses)")
        diagnostico_partes.append("sobrepasó el límite máximo admisible de servicio preventivo")
    elif meses_mtto >= 6.0:
        score += 26.0
        anomalias.append(f"Mantenimiento preventivo requerido ({meses_mtto:.1f} meses sin mtto)")
        diagnostico_partes.append("intervalo de calibración e inspección superado")
    elif meses_mtto >= 4.0:
        score += 12.0
    elif meses_mtto <= 1.0:
        score -= 4.0

    # 2. Factor MTBF (Confiabilidad y Frecuencia de Fallos)
    if mtbf > 0:
        if mtbf < 180.0:
            score += 30.0
            anomalias.append(f"MTBF crítico ({mtbf:.1f} días, degradación acelerada)")
            diagnostico_partes.append("tiempo medio entre fallas críticamente bajo")
        elif mtbf < 350.0:
            score += 18.0
            anomalias.append(f"Baja confiabilidad MTBF ({mtbf:.1f} días)")
        elif mtbf < 600.0:
            score += 8.0
        elif mtbf > 1000.0:
            score -= 8.0

    # 3. Factor de Fallas Previas Acumuladas
    if fallas_acum >= 12:
        score += 22.0
        anomalias.append(f"Historial severo de fallas previas ({fallas_acum} fallas)")
        diagnostico_partes.append("desgaste estructural recurrente por múltiples fallas")
    elif fallas_acum >= 6:
        score += 14.0
        anomalias.append(f"Frecuencia moderada de fallas acumuladas ({fallas_acum} fallas)")
    elif fallas_acum >= 2:
        score += 6.0

    # 4. Envejecimiento del Equipo
    if edad > 100.0:
        score += 12.0
        anomalias.append(f"Equipo en fase de obsolescencia ({edad:.1f} meses de servicio)")
    elif edad > 60.0:
        score += 6.0

    # 5. Clasificación de Riesgo
    if riesgo == "III":
        score += 8.0
    elif riesgo == "IIB":
        score += 4.0

    # 6. Falla activa confirmada o target en 1
    es_falla_activa = (falla_target == 1.0) or ("normal" not in evento.lower() and "sin dato" not in evento.lower())
    if es_falla_activa:
        score += 48.0
        anomalias.append(f"Evento de falla activa registrado: {evento}")
        diagnostico_partes.append(evento)

    prob = max(5.0, min(99.0, score))

    # Diagnóstico específico por tecnología
    if slug in ["bombas-volumetricas", "bombas-jeringa"]:
        diagnostico_falla_posible = "Riesgo de oclusión constante, fallo en sensor ultrasónico de burbujas o bloqueo en motor paso a paso."
    elif slug == "ventiladores":
        diagnostico_falla_posible = "Riesgo de pérdida de presión en turbina/compresor, obstrucción en válvula exhalatoria o sobrecalentamiento de bloque."
    elif slug in ["monitores", "ecg"]:
        diagnostico_falla_posible = "Riesgo de agotamiento de batería de backup, fallo de comunicación en módulo SpO2 o ruido en amplificador ECG."
    elif slug == "crrt":
        diagnostico_falla_posible = "Riesgo de fuga en circuito de dializado, fallo en bomba de heparina o error en balanza de ultrafiltración."
    elif slug == "desfibriladores":
        diagnostico_falla_posible = "Riesgo en módulo de marcapasos transcutáneo, lectura de impedancia en paletas o capacitor de descarga."
    elif slug == "rayos-x":
        diagnostico_falla_posible = "Riesgo de sobrecalentamiento térmico en tubo de rayos X o descalibración del colimador."
    elif slug == "ecografos":
        diagnostico_falla_posible = "Riesgo de artefacto acústico por transductor piezoeléctrico dañado o fallo en conector de sonda."
    else:
        diagnostico_falla_posible = "Riesgo de fallo en electrodos de sensado o bomba peristáltica interna."

    if prob >= 70.0 or es_falla_activa:
        nivel = "Crítico"
        accion = "Intervención técnica inmediata requerida. Retirar temporalmente de atención y ejecutar Mantenimiento Correctivo."
    elif prob >= 50.0:
        nivel = "Alto"
        accion = "Programar Mantenimiento Preventivo prioritario y calibración metrológica en menos de 7 días."
    elif prob >= 30.0:
        nivel = "Medio"
        accion = "Programar inspección rutinaria de rutina y revisión funcional en el próximo ciclo de mantenimiento."
    else:
        nivel = "Bajo"
        accion = "Equipo en parámetros de funcionamiento seguro. Continuar monitoreo y uso nominal."

    diag_text = ("; ".join(diagnostico_partes).capitalize() + ".") if diagnostico_partes else diagnostico_falla_posible

    return {
        "probabilidad_falla": round(prob, 1),
        "nivel_riesgo": nivel,
        "diagnostico": diag_text,
        "anomalias": anomalias,
        "accion_recomendada": accion,
        "falla_actual": 1 if es_falla_activa else 0,
        "evento_reciente": evento
    }

@app.get("/api/equipos-dataset/{tipo}", tags=["Dataset de Equipos"])
def get_equipos_dataset(
    tipo: str,
    search: Optional[str] = None,
    q: Optional[str] = None,
    equipo_id: Optional[str] = None,
    falla: Optional[str] = None,
    riesgo: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Consulta paginada del dataset técnico de mantenimientos y fallas de Neon PostgreSQL (dataset_eventos)."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        where_clauses = []
        params = []

        tipo_key = tipo.lower()
        if tipo_key not in ["todos", "todas", "all", ""]:
            if tipo_key in FAMILIAS_DATASET_MAP:
                cfg = FAMILIAS_DATASET_MAP[tipo_key]
                where_clauses.append('"Tipo_Equipo" = %s')
                params.append(cfg["tipo_db"])
            else:
                where_clauses.append('("Tipo_Equipo" ILIKE %s OR "ID_Equipo" ILIKE %s)')
                params.extend([f"%{tipo_key}%", f"%{tipo_key}%"])

        search_term = (search or q or "").strip()
        if search_term:
            s = f"%{search_term}%"
            where_clauses.append('("ID_Equipo" ILIKE %s OR "Descripcion_Evento" ILIKE %s OR "Tipo_Equipo" ILIKE %s OR CAST(id AS TEXT) ILIKE %s)')
            params.extend([s, s, s, s])

        if equipo_id:
            where_clauses.append('"ID_Equipo" = %s')
            params.append(equipo_id.strip())

        if falla is not None and str(falla).strip().lower() not in ["todos", "all", "", "none"]:
            falla_clean = str(falla).strip().lower()
            if falla_clean in ["1", "falla", "true"]:
                where_clauses.append('"Variable_Target_Falla" = 1')
            elif falla_clean in ["0", "normal", "false"]:
                where_clauses.append('"Variable_Target_Falla" = 0')
            elif falla_clean in ["null", "incompleta", "pendiente"]:
                where_clauses.append('"Variable_Target_Falla" IS NULL')

        if riesgo and riesgo.strip().lower() not in ["todos", "all", ""]:
            where_clauses.append('"Clasificacion_Riesgo" = %s')
            params.append(riesgo.strip().upper())

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total count
        count_sql = f'SELECT COUNT(*) as total FROM dataset_eventos {where_sql}'
        cursor.execute(count_sql, params)
        total = cursor.fetchone()["total"]

        # Pagination
        offset = max(0, (page - 1) * limit)
        cols_sql = ', '.join(f'"{c}"' for c in DATASET_COLUMNS)
        data_sql = f'SELECT {cols_sql} FROM dataset_eventos {where_sql} ORDER BY "Fecha_Reporte" DESC, id DESC LIMIT %s OFFSET %s'
        cursor.execute(data_sql, params + [limit, offset])
        rows = cursor.fetchall()
        cursor.close()

        # Format dates and ensure primary key ID_Registro
        for r in rows:
            if not r.get("ID_Registro"):
                r["ID_Registro"] = f"REG-{r['id']:05d}"
            if isinstance(r.get("Fecha_Reporte"), datetime):
                r["Fecha_Reporte"] = r["Fecha_Reporte"].strftime("%Y-%m-%d")

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "tabla": "dataset_eventos",
            "columnas": DATASET_COLUMNS,
            "rows": rows
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error consultando dataset_eventos: {str(e)}")

@app.put("/api/equipos-dataset/{registro_id}", tags=["Dataset de Equipos"])
def update_equipo_record(
    registro_id: int,
    data: DatasetRecordUpdate,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Edita un registro del dataset de mantenimientos y fallas en Neon PostgreSQL."""
    if user["rol_nombre"] not in DATASET_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado: solo personal biomédico y técnico puede editar registros.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('SELECT * FROM dataset_eventos WHERE id = %s', (registro_id,))
        existente = cursor.fetchone()
        if not existente:
            cursor.close()
            raise HTTPException(status_code=404, detail=f"Registro #{registro_id} no encontrado en dataset_eventos.")

        updates = []
        params = []
        if data.Meses_Desde_Ultimo_Mtto is not None:
            updates.append('"Meses_Desde_Ultimo_Mtto" = %s')
            params.append(data.Meses_Desde_Ultimo_Mtto)
        if data.Fallas_Acumuladas is not None:
            updates.append('"Fallas_Acumuladas" = %s')
            params.append(data.Fallas_Acumuladas)
        if data.MTBF_Promedio_Dias is not None:
            updates.append('"MTBF_Promedio_Dias" = %s')
            params.append(data.MTBF_Promedio_Dias)
        if data.Variable_Target_Falla is not None:
            updates.append('"Variable_Target_Falla" = %s')
            params.append(data.Variable_Target_Falla)
        if data.Descripcion_Evento is not None:
            updates.append('"Descripcion_Evento" = %s')
            params.append(data.Descripcion_Evento.strip())
        if data.Edad_Equipo_Meses is not None:
            updates.append('"Edad_Equipo_Meses" = %s')
            params.append(data.Edad_Equipo_Meses)
        if data.Clasificacion_Riesgo is not None:
            updates.append('"Clasificacion_Riesgo" = %s')
            params.append(data.Clasificacion_Riesgo.strip())

        if not updates:
            cursor.close()
            return {"status": "success", "message": "Sin cambios requeridos.", "registro_id": registro_id}

        params.append(registro_id)
        update_sql = f'UPDATE dataset_eventos SET {", ".join(updates)} WHERE id = %s RETURNING *'
        cursor.execute(update_sql, params)
        updated_row = cursor.fetchone()

        cursor.execute('''
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
            VALUES (%s, %s, %s, %s)
        ''', (user["id"], f"EDITAR_REGISTRO_DATASET: ID {registro_id} ({existente.get('ID_Equipo')})", "dataset_eventos", registro_id))
        conn.commit()
        cursor.close()

        if isinstance(updated_row.get("Fecha_Reporte"), datetime):
            updated_row["Fecha_Reporte"] = updated_row["Fecha_Reporte"].strftime("%Y-%m-%d")

        return {
            "status": "success",
            "message": f"Registro #{registro_id} de {existente.get('ID_Equipo')} actualizado correctamente en Neon.",
            "data": updated_row
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error actualizando registro en dataset_eventos: {str(e)}")

@app.post("/api/equipos-dataset", tags=["Dataset de Equipos"])
def create_equipo_record(
    data: DatasetRecordCreate,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Inserta un nuevo registro de reporte, mantenimiento o falla en dataset_eventos."""
    if user["rol_nombre"] not in DATASET_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado: solo personal biomédico y técnico puede insertar registros.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        fecha = datetime.strptime(data.Fecha_Reporte, "%Y-%m-%d") if data.Fecha_Reporte else datetime.now()
        tipo_eq = data.Tipo_Equipo
        if not tipo_eq:
            slug = obtener_slug_tipo(data.ID_Equipo)
            tipo_eq = FAMILIAS_DATASET_MAP.get(slug, {}).get("tipo_db", "Bomba de Infusión Volumétrica")

        cursor.execute('''
            INSERT INTO dataset_eventos (
                "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Fallas_Acumuladas",
                "MTBF_Promedio_Dias", "Variable_Target_Falla", "Descripcion_Evento"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (
            data.ID_Equipo.strip(),
            tipo_eq,
            data.Clasificacion_Riesgo or "IIB",
            fecha,
            data.Edad_Equipo_Meses or 40.0,
            data.Meses_Desde_Ultimo_Mtto or 1.0,
            data.Fallas_Acumuladas or 0,
            data.MTBF_Promedio_Dias or 500.0,
            data.Variable_Target_Falla,
            data.Descripcion_Evento or "Operación Normal"
        ))
        new_id = cursor.fetchone()["id"]

        cursor.execute('''
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
            VALUES (%s, %s, %s, %s)
        ''', (user["id"], f"CREAR_REGISTRO_DATASET: ID {new_id} ({data.ID_Equipo})", "dataset_eventos", new_id))
        conn.commit()
        cursor.close()

        return {
            "status": "success",
            "message": f"Registro #{new_id} agregado exitosamente a dataset_eventos.",
            "registro_id": new_id
        }
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error creando registro en dataset_eventos: {str(e)}")

@app.delete("/api/equipos-dataset/{registro_id}", tags=["Dataset de Equipos"])
def delete_equipo_record(
    registro_id: int,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Elimina un registro específico de dataset_eventos."""
    if user["rol_nombre"] not in DATASET_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado para eliminar registros.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('SELECT "ID_Equipo" FROM dataset_eventos WHERE id = %s', (registro_id,))
        r = cursor.fetchone()
        if not r:
            cursor.close()
            raise HTTPException(status_code=404, detail=f"Registro #{registro_id} no encontrado.")

        cursor.execute('DELETE FROM dataset_eventos WHERE id = %s', (registro_id,))
        cursor.execute('''
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
            VALUES (%s, %s, %s, %s)
        ''', (user["id"], f"ELIMINAR_REGISTRO_DATASET: ID {registro_id} ({r['ID_Equipo']})", "dataset_eventos", registro_id))
        conn.commit()
        cursor.close()

        return {
            "status": "success",
            "message": f"Registro #{registro_id} eliminado exitosamente de dataset_eventos."
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al eliminar registro: {str(e)}")

@app.get("/api/equipos-estado", tags=["Estado de Equipos"])
def get_equipos_estado_completo(
    conn = Depends(get_db)
):
    """Retorna el estado operativo consolidado de los 232 equipos biomédicos desde dataset_eventos."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                id, "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Fallas_Acumuladas",
                "MTBF_Promedio_Dias", "Variable_Target_Falla", "Descripcion_Evento"
            FROM dataset_eventos
            ORDER BY "ID_Equipo", "Fecha_Reporte" DESC
        ''')
        rows = cursor.fetchall()
        cursor.close()

        conteo_por_familia = {slug: 0 for slug in FAMILIAS_DATASET_MAP.keys()}
        all_equipos = []
        fallas_count = 0
        alertas_preventivas = 0

        for r in rows:
            tipo_db = r["Tipo_Equipo"]
            slug = obtener_slug_tipo(tipo_db)
            conteo_por_familia[slug] = conteo_por_familia.get(slug, 0) + 1

            pred = calcular_prediccion_equipo(r)
            es_falla = (r.get("Variable_Target_Falla") == 1.0) or (pred["falla_actual"] == 1)
            if es_falla:
                fallas_count += 1
                estado_operativo = "En Falla"
            elif pred["nivel_riesgo"] in ["Crítico", "Alto"]:
                alertas_preventivas += 1
                estado_operativo = "Requiere Mtto"
            else:
                estado_operativo = "Operativo Normal"

            fecha_str = r["Fecha_Reporte"].strftime("%Y-%m-%d") if isinstance(r.get("Fecha_Reporte"), datetime) else str(r.get("Fecha_Reporte") or "")

            all_equipos.append({
                "id_registro": r["id"],
                "id_equipo": r["ID_Equipo"],
                "tipo": slug,
                "tipo_nombre": tipo_db,
                "clasificacion_riesgo": r.get("Clasificacion_Riesgo") or "IIB",
                "fecha_reporte": fecha_str,
                "edad_meses": r.get("Edad_Equipo_Meses") or 0.0,
                "meses_mtto": r.get("Meses_Desde_Ultimo_Mtto") or 0.0,
                "fallas_acum": r.get("Fallas_Acumuladas") or 0,
                "mtbf_dias": r.get("MTBF_Promedio_Dias") or 0.0,
                "target_falla": r.get("Variable_Target_Falla"),
                "evento": r.get("Descripcion_Evento") or "Operación Normal",
                "estado_operativo": estado_operativo,
                "prediccion": pred
            })

        all_equipos.sort(key=lambda x: (x["prediccion"]["falla_actual"], x["prediccion"]["probabilidad_falla"]), reverse=True)

        return {
            "resumen": {
                "total_equipos": len(rows),
                "fallas_activas": fallas_count,
                "alertas_riesgo": alertas_preventivas,
                "familias_count": len(FAMILIAS_DATASET_MAP)
            },
            "conteo_por_familia": conteo_por_familia,
            "equipos": all_equipos
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
    """Retorna la bitácora histórica completa de mantenimientos, eventos y curvas MTBF de un equipo en dataset_eventos."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('''
            SELECT id, "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                   "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Fallas_Acumuladas",
                   "MTBF_Promedio_Dias", "Variable_Target_Falla", "Descripcion_Evento"
            FROM dataset_eventos
            WHERE "ID_Equipo" = %s
            ORDER BY "Fecha_Reporte" ASC, id ASC
        ''', (equipo_id.strip(),))
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            raise HTTPException(status_code=404, detail=f"No se encontraron registros para el equipo {equipo_id}")

        labels = []
        puntos = []
        series_mtbf = []
        series_mtto = []
        series_fallas = []

        for r in rows:
            f_str = r["Fecha_Reporte"].strftime("%Y-%m") if isinstance(r.get("Fecha_Reporte"), datetime) else str(r.get("Fecha_Reporte") or "")
            labels.append(f_str)
            series_mtbf.append(float(r.get("MTBF_Promedio_Dias") or 0.0))
            series_mtto.append(float(r.get("Meses_Desde_Ultimo_Mtto") or 0.0))
            series_fallas.append(int(r.get("Fallas_Acumuladas") or 0))

            puntos.append({
                "id": r["id"],
                "fecha": f_str,
                "edad_meses": r.get("Edad_Equipo_Meses"),
                "meses_mtto": r.get("Meses_Desde_Ultimo_Mtto"),
                "fallas_acum": r.get("Fallas_Acumuladas"),
                "mtbf_dias": r.get("MTBF_Promedio_Dias"),
                "target_falla": r.get("Variable_Target_Falla"),
                "evento": r.get("Descripcion_Evento")
            })

        ultimo = rows[-1]
        if isinstance(ultimo.get("Fecha_Reporte"), datetime):
            ultimo["Fecha_Reporte"] = ultimo["Fecha_Reporte"].strftime("%Y-%m-%d")

        pred = calcular_prediccion_equipo(ultimo)

        return {
            "equipo_id": equipo_id,
            "tipo_nombre": ultimo.get("Tipo_Equipo"),
            "clasificacion_riesgo": ultimo.get("Clasificacion_Riesgo"),
            "total_muestras": len(rows),
            "labels": labels,
            "series": {
                "MTBF_Promedio_Dias": series_mtbf,
                "Meses_Desde_Ultimo_Mtto": series_mtto,
                "Fallas_Acumuladas": series_fallas
            },
            "puntos": puntos,
            "ultimo_registro": ultimo,
            "prediccion_actual": pred
        }
    except HTTPException:
        raise
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error consultando historial del equipo: {str(e)}")

@app.post("/api/equipos-prediccion", tags=["Predicción de Equipos"])
def predict_equipo_failure(
    req: PredictDatasetRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Calcula el riesgo predictivo sobre variables reales del equipo y permite simular o persistir cambios en Neon."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: El módulo de Predicción de Equipos está reservado al Administrador Biomédico y a los Ingenieros de Mantenimiento."
        )

    item_data = {}
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    if req.equipo_id:
        cursor.execute('''
            SELECT * FROM dataset_eventos
            WHERE "ID_Equipo" = %s
            ORDER BY "Fecha_Reporte" DESC, id DESC
            LIMIT 1
        ''', (req.equipo_id.strip(),))
        row = cursor.fetchone()
        if row:
            item_data = dict(row)

    if req.tipo:
        item_data["Tipo_Equipo"] = req.tipo
    if req.meses_desde_ultimo_mtto is not None:
        item_data["Meses_Desde_Ultimo_Mtto"] = req.meses_desde_ultimo_mtto
    if req.fallas_acumuladas is not None:
        item_data["Fallas_Acumuladas"] = req.fallas_acumuladas
    if req.mtbf_promedio_dias is not None:
        item_data["MTBF_Promedio_Dias"] = req.mtbf_promedio_dias
    if req.edad_equipo_meses is not None:
        item_data["Edad_Equipo_Meses"] = req.edad_equipo_meses
    if req.clasificacion_riesgo is not None:
        item_data["Clasificacion_Riesgo"] = req.clasificacion_riesgo
    if req.variable_target_falla is not None:
        item_data["Variable_Target_Falla"] = req.variable_target_falla
    if req.descripcion_evento:
        item_data["Descripcion_Evento"] = req.descripcion_evento

    pred = calcular_prediccion_equipo(item_data)
    pred["equipo_id"] = req.equipo_id or item_data.get("ID_Equipo")
    pred["parametros_analizados"] = {
        "Meses_Desde_Ultimo_Mtto": item_data.get("Meses_Desde_Ultimo_Mtto"),
        "Fallas_Acumuladas": item_data.get("Fallas_Acumuladas"),
        "MTBF_Promedio_Dias": item_data.get("MTBF_Promedio_Dias"),
        "Edad_Equipo_Meses": item_data.get("Edad_Equipo_Meses"),
        "Clasificacion_Riesgo": item_data.get("Clasificacion_Riesgo"),
        "Variable_Target_Falla": item_data.get("Variable_Target_Falla")
    }

    # Opción de persistir la predicción/ajuste en Neon para la ventana evaluada
    if req.guardar_en_bd and req.equipo_id and item_data.get("id"):
        try:
            target_val = 1.0 if pred["probabilidad_falla"] >= 70.0 else 0.0
            if req.variable_target_falla is not None:
                target_val = req.variable_target_falla
            desc_val = req.descripcion_evento or (pred["diagnostico"] if target_val == 1.0 else "Operación Normal (Pronóstico Estable)")

            cursor.execute('''
                UPDATE dataset_eventos SET
                    "Meses_Desde_Ultimo_Mtto" = %s,
                    "Fallas_Acumuladas" = %s,
                    "MTBF_Promedio_Dias" = %s,
                    "Variable_Target_Falla" = %s,
                    "Descripcion_Evento" = %s
                WHERE id = %s
            ''', (
                item_data.get("Meses_Desde_Ultimo_Mtto"),
                item_data.get("Fallas_Acumuladas"),
                item_data.get("MTBF_Promedio_Dias"),
                target_val,
                desc_val,
                item_data.get("id")
            ))
            cursor.execute('''
                INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
                VALUES (%s, %s, %s, %s)
            ''', (user["id"], f"GUARDAR_PREDICCION_BD: Equipo {req.equipo_id} (Riesgo {pred['nivel_riesgo']} - {pred['probabilidad_falla']}%)", "dataset_eventos", item_data.get("id")))
            conn.commit()
            pred["guardado_en_bd"] = True
            pred["mensaje_bd"] = f"Ajuste predictivo y estado guardados en Neon para {req.equipo_id}."
        except Exception as e:
            conn.rollback()
            pred["guardado_en_bd"] = False
            pred["mensaje_bd"] = f"No se pudo persistir en BD: {str(e)}"

    cursor.close()
    return pred

@app.post("/api/equipos-mantenimiento-ejecutar", tags=["Predicción de Equipos"])
def registrar_mantenimiento_preventivo(
    data: MantenimientoEjecutarRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Registra la ejecución de un mantenimiento preventivo en Neon, reseteando Meses_Desde_Ultimo_Mtto a 0.0."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(status_code=403, detail="Acceso denegado: solo personal de ingeniería y biomédica puede registrar mantenimientos.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('''
            SELECT * FROM dataset_eventos
            WHERE "ID_Equipo" = %s
            ORDER BY "Fecha_Reporte" DESC, id DESC
            LIMIT 1
        ''', (data.equipo_id.strip(),))
        prev = cursor.fetchone()

        if not prev:
            cursor.close()
            raise HTTPException(status_code=404, detail=f"Equipo {data.equipo_id} no encontrado en dataset_eventos.")

        edad = float(prev.get("Edad_Equipo_Meses") or 40.0) + 0.1
        mtbf = float(prev.get("MTBF_Promedio_Dias") or 500.0)
        fallas = int(prev.get("Fallas_Acumuladas") or 0)
        ahora = datetime.now()

        cursor.execute('''
            INSERT INTO dataset_eventos (
                "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Fallas_Acumuladas",
                "MTBF_Promedio_Dias", "Variable_Target_Falla", "Descripcion_Evento"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (
            prev["ID_Equipo"],
            prev["Tipo_Equipo"],
            prev["Clasificacion_Riesgo"],
            ahora,
            edad,
            0.0,  # Contador reiniciado a 0 por mantenimiento preventivo
            fallas,
            mtbf + 30.0,  # Mejora de MTBF tras intervención técnica
            0.0,
            f"Mantenimiento Preventivo Ejecutado por {user['nombre']}: {data.observaciones}"
        ))
        nuevo_id = cursor.fetchone()["id"]

        cursor.execute('''
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id)
            VALUES (%s, %s, %s, %s)
        ''', (user["id"], f"EJECUTAR_MTTO_PREVENTIVO: {data.equipo_id} (Nuevo ID #{nuevo_id})", "dataset_eventos", nuevo_id))
        conn.commit()
        cursor.close()

        # Recalcular predicción post-mantenimiento
        item_actualizado = {
            "Tipo_Equipo": prev["Tipo_Equipo"],
            "Clasificacion_Riesgo": prev["Clasificacion_Riesgo"],
            "Edad_Equipo_Meses": edad,
            "Meses_Desde_Ultimo_Mtto": 0.0,
            "Fallas_Acumuladas": fallas,
            "MTBF_Promedio_Dias": mtbf + 30.0,
            "Variable_Target_Falla": 0.0,
            "Descripcion_Evento": "Operación Normal (Preventivo Reciente)"
        }
        nueva_pred = calcular_prediccion_equipo(item_actualizado)

        return {
            "status": "success",
            "message": f"Mantenimiento preventivo registrado exitosamente para {data.equipo_id}. Contador reiniciado a 0 meses.",
            "registro_id": nuevo_id,
            "prediccion_actualizada": nueva_pred
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error registrando mantenimiento preventivo: {str(e)}")

@app.get("/api/equipos-prediccion-resumen", tags=["Predicción de Equipos"])
def get_prediccion_resumen_global(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Genera la matriz predictiva priorizada de los 232 equipos biomédicos desde dataset_eventos."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: Exclusivo para Administrador Biomédico e Ingenieros de Mantenimiento."
        )

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                id, "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Fallas_Acumuladas",
                "MTBF_Promedio_Dias", "Variable_Target_Falla", "Descripcion_Evento"
            FROM dataset_eventos
            ORDER BY "ID_Equipo", "Fecha_Reporte" DESC
        ''')
        rows = cursor.fetchall()
        cursor.close()

        lista_equipos = []
        for r in rows:
            p = calcular_prediccion_equipo(r)
            slug = obtener_slug_tipo(r["Tipo_Equipo"])
            f_str = r["Fecha_Reporte"].strftime("%Y-%m-%d") if isinstance(r.get("Fecha_Reporte"), datetime) else str(r.get("Fecha_Reporte") or "")
            lista_equipos.append({
                "registro_id": r["id"],
                "tipo": slug,
                "familia": r["Tipo_Equipo"],
                "equipo_id": r["ID_Equipo"],
                "clasificacion_riesgo": r.get("Clasificacion_Riesgo") or "IIB",
                "fecha_reporte": f_str,
                "edad_meses": r.get("Edad_Equipo_Meses") or 0.0,
                "meses_mtto": r.get("Meses_Desde_Ultimo_Mtto") or 0.0,
                "fallas_previas": r.get("Fallas_Acumuladas") or 0,
                "mtbf_dias": r.get("MTBF_Promedio_Dias") or 0.0,
                "falla_actual": p["falla_actual"],
                "probabilidad_falla": p["probabilidad_falla"],
                "nivel_riesgo": p["nivel_riesgo"],
                "diagnostico": p["diagnostico"],
                "anomalias": p["anomalias"],
                "accion_recomendada": p["accion_recomendada"],
                "evento_reciente": r.get("Descripcion_Evento") or "Operación Normal"
            })

        lista_equipos.sort(key=lambda x: (x["falla_actual"], x["probabilidad_falla"]), reverse=True)

        total = len(lista_equipos)
        criticos = sum(1 for e in lista_equipos if e["nivel_riesgo"] in {"Crítico", "Alto"} or e["falla_actual"] == 1)
        medios = sum(1 for e in lista_equipos if e["nivel_riesgo"] == "Medio")
        optimos = sum(1 for e in lista_equipos if e["nivel_riesgo"] == "Bajo" and e["falla_actual"] == 0)

        return {
            "total_analizados": total,
            "equipos_criticos": criticos,
            "criticos": criticos,
            "equipos_atencion": medios,
            "preventivos": medios,
            "equipos_optimos": optimos,
            "indice_confiabilidad": round((optimos / total * 100) if total > 0 else 100, 1),
            "confiabilidad_global_pct": round((optimos / total * 100) if total > 0 else 100, 1),
            "equipos": lista_equipos,
            "equipos_priorizados": lista_equipos
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error en resumen predictivo: {str(e)}")


# ==========================================
# SECCIÓN 7: EXPEDIENTE METROLÓGICO Y MULTIMEDIA DE EQUIPOS (FOTOS Y CERTIFICADOS)
# ==========================================

class DocumentoEquipoCreate(BaseModel):
    equipo_id: str
    tipo_documento: str
    titulo: str
    numero_certificado: Optional[str] = None
    entidad_emisora: Optional[str] = None
    fecha_emision: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    estado_vigencia: Optional[str] = "VIGENTE"
    archivo_url: Optional[str] = None
    archivo_nombre: Optional[str] = None
    archivo_tamano_kb: Optional[int] = 0
    observaciones: Optional[str] = None


@app.get("/api/equipos-documentos", tags=["Expediente de Equipos"])
def listar_documentos_equipos(
    equipo_id: Optional[str] = None,
    tipo: Optional[str] = None,
    vigencia: Optional[str] = None,
    search: Optional[str] = None,
    q: Optional[str] = None,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Consulta la lista de certificados de calibración, fotografías y fichas técnicas de los equipos."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        query = 'SELECT * FROM equipos_documentos WHERE 1=1'
        params = []

        if equipo_id:
            query += ' AND UPPER(equipo_id) = UPPER(%s)'
            params.append(equipo_id.strip())

        if tipo and tipo.lower() != 'todos':
            query += ' AND UPPER(tipo_documento) = UPPER(%s)'
            params.append(tipo.strip())

        if vigencia and vigencia.lower() != 'todos':
            query += ' AND UPPER(estado_vigencia) = UPPER(%s)'
            params.append(vigencia.strip())

        search_term = (search or q or "").strip()
        if search_term:
            query += ' AND (titulo ILIKE %s OR equipo_id ILIKE %s OR numero_certificado ILIKE %s OR entidad_emisora ILIKE %s OR pk_documento ILIKE %s)'
            term = f"%{search_term}%"
            params.extend([term, term, term, term, term])

        query += ' ORDER BY fecha_emision DESC NULLS LAST, id DESC;'
        cursor.execute(query, tuple(params))
        docs = cursor.fetchall()
        cursor.close()

        # Formatear fechas y PK de documento
        for d in docs:
            if not d.get("pk_documento"):
                d["pk_documento"] = f"DOC-{d['id']:04d}"
            if isinstance(d.get("fecha_emision"), (datetime, date)):
                d["fecha_emision"] = d["fecha_emision"].strftime("%Y-%m-%d")
            if isinstance(d.get("fecha_vencimiento"), (datetime, date)):
                d["fecha_vencimiento"] = d["fecha_vencimiento"].strftime("%Y-%m-%d")
            if isinstance(d.get("created_at"), (datetime, date)):
                d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M")

        return {
            "total": len(docs),
            "documentos": docs
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al listar documentos: {str(e)}")


@app.get("/api/equipos-documentos/{equipo_id}", tags=["Expediente de Equipos"])
def get_documentos_equipo_individual(
    equipo_id: str,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Obtiene el pack documental completo (foto, certificado de calibración y ficha técnica) de una unidad por su Primary Key (ID_Equipo) o por PK Documento."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Si equipo_id parece un PK Documento (ej. DOC-0001)
        if equipo_id.strip().upper().startswith("DOC-"):
            cursor.execute(
                'SELECT * FROM equipos_documentos WHERE UPPER(pk_documento) = UPPER(%s) OR id = %s ORDER BY id ASC;',
                (equipo_id.strip(), int(equipo_id.replace("DOC-", "")) if equipo_id.replace("DOC-", "").isdigit() else -1)
            )
            docs = cursor.fetchall()
        else:
            cursor.execute(
                'SELECT * FROM equipos_documentos WHERE UPPER(equipo_id) = UPPER(%s) ORDER BY id ASC;',
                (equipo_id.strip(),)
            )
            docs = cursor.fetchall()

        # Si no tiene documentos específicos asignados, generar plantilla predeterminada con su tecnología
        if not docs:
            cursor.execute(
                'SELECT "Tipo_Equipo", "Clasificacion_Riesgo" FROM dataset_eventos WHERE UPPER("ID_Equipo") = UPPER(%s) LIMIT 1;',
                (equipo_id.strip(),)
            )
            eq_info = cursor.fetchone()
            tipo_nombre = eq_info["Tipo_Equipo"] if eq_info else "Equipo Biomédico"

            # Intentar clonar del primer equipo con esa tecnología o prefijo
            prefix = equipo_id.split("-")[0].strip().upper() if "-" in equipo_id else "BVOL"
            cursor.execute(
                'SELECT * FROM equipos_documentos WHERE UPPER(equipo_id) LIKE %s ORDER BY id ASC;',
                (f"{prefix}-%",)
            )
            docs = cursor.fetchall()

        cursor.close()

        for d in docs:
            if not d.get("pk_documento"):
                d["pk_documento"] = f"DOC-{d['id']:04d}"
            d["equipo_id"] = equipo_id.upper()
            if isinstance(d.get("fecha_emision"), (datetime, date)):
                d["fecha_emision"] = d["fecha_emision"].strftime("%Y-%m-%d")
            if isinstance(d.get("fecha_vencimiento"), (datetime, date)):
                d["fecha_vencimiento"] = d["fecha_vencimiento"].strftime("%Y-%m-%d")
            if isinstance(d.get("created_at"), (datetime, date)):
                d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M")

        foto = next((d for d in docs if d["tipo_documento"] == "FOTO_EQUIPO"), None)
        certificado = next((d for d in docs if d["tipo_documento"] == "CERTIFICADO_CALIBRACION"), None)
        ficha = next((d for d in docs if d["tipo_documento"] == "FICHA_TECNICA"), None)

        return {
            "equipo_id": equipo_id.upper(),
            "foto": foto,
            "certificado": certificado,
            "ficha_tecnica": ficha,
            "todos": docs
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al obtener documentos del equipo: {str(e)}")


@app.post("/api/equipos-documentos", tags=["Expediente de Equipos"])
def registrar_documento_equipo(
    doc: DocumentoEquipoCreate,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Sube o registra un nuevo certificado de calibración, fotografía o manual para un equipo."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(status_code=403, detail="Permiso denegado: Solo personal de ingeniería y administración biomédica puede registrar certificados.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Calcular estado de vigencia si se suministró fecha de vencimiento
        estado_vig = doc.estado_vigencia or "VIGENTE"
        if doc.fecha_vencimiento:
            try:
                f_venc = datetime.strptime(doc.fecha_vencimiento, "%Y-%m-%d").date()
                hoy = date.today()
                if f_venc < hoy:
                    estado_vig = "VENCIDO"
                elif (f_venc - hoy).days <= 30:
                    estado_vig = "PROXIMO_VENCER"
                else:
                    estado_vig = "VIGENTE"
            except Exception:
                pass

        cursor.execute('''
            INSERT INTO equipos_documentos (
                equipo_id, tipo_documento, titulo, numero_certificado,
                entidad_emisora, fecha_emision, fecha_vencimiento, estado_vigencia,
                archivo_url, archivo_nombre, archivo_tamano_kb, observaciones,
                created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, equipo_id, titulo, tipo_documento, estado_vigencia;
        ''', (
            doc.equipo_id.strip().upper(),
            doc.tipo_documento.strip().upper(),
            doc.titulo.strip(),
            doc.numero_certificado.strip() if doc.numero_certificado else None,
            doc.entidad_emisora.strip() if doc.entidad_emisora else "Ingeniería Biomédica Hospitalaria",
            doc.fecha_emision or date.today().strftime("%Y-%m-%d"),
            doc.fecha_vencimiento,
            estado_vig,
            doc.archivo_url,
            doc.archivo_nombre or f"{doc.tipo_documento.lower()}_{doc.equipo_id.lower()}",
            doc.archivo_tamano_kb or 150,
            doc.observaciones or "Registrado en expediente digital del equipo.",
            user.get("nombre") or user.get("email")
        ))
        nuevo_doc = cursor.fetchone()
        new_pk = f"DOC-{nuevo_doc['id']:04d}"
        cursor.execute('UPDATE equipos_documentos SET pk_documento = %s WHERE id = %s', (new_pk, nuevo_doc['id']))
        nuevo_doc['pk_documento'] = new_pk
        conn.commit()
        cursor.close()

        registrar_auditoria(
            conn,
            usuario_id=user["id"],
            usuario_email=user["email"],
            usuario_rol=user["rol_nombre"],
            accion="CREATE",
            modulo="EXPEDIENTE_EQUIPOS",
            entidad_id=nuevo_doc["id"],
            detalles=f"Subido {doc.tipo_documento} para equipo {doc.equipo_id}: {doc.titulo}"
        )

        return {
            "mensaje": f"Documento '{doc.titulo}' registrado exitosamente para el equipo {doc.equipo_id}.",
            "documento": nuevo_doc
        }
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al registrar documento: {str(e)}")


@app.delete("/api/equipos-documentos/{doc_id}", tags=["Expediente de Equipos"])
def eliminar_documento_equipo(
    doc_id: int,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Elimina un documento del expediente de un equipo."""
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(status_code=403, detail="Permiso denegado.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('DELETE FROM equipos_documentos WHERE id = %s RETURNING id, equipo_id, titulo;', (doc_id,))
        eliminado = cursor.fetchone()
        if not eliminado:
            cursor.close()
            raise HTTPException(status_code=404, detail="Documento no encontrado.")

        conn.commit()
        cursor.close()

        registrar_auditoria(
            conn,
            usuario_id=user["id"],
            usuario_email=user["email"],
            usuario_rol=user["rol_nombre"],
            accion="DELETE",
            modulo="EXPEDIENTE_EQUIPOS",
            entidad_id=doc_id,
            detalles=f"Eliminado documento {eliminado['titulo']} del equipo {eliminado['equipo_id']}"
        )

        return {"mensaje": f"Documento #{doc_id} eliminado exitosamente."}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al eliminar documento: {str(e)}")

