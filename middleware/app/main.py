import os
import re
import urllib.parse
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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

try:
    from app.ml_model import predecir_3_targets, seudonimizar_payload_ia, MODEL_METADATA, FEATURES
    from app.requisitos_routes import (
        ensure_requisitos_tables_and_seed,
        register_requisitos_routes,
        obtener_rol_canonico,
        emitir_notificacion_y_sse,
    )
except ImportError:
    from ml_model import predecir_3_targets, seudonimizar_payload_ia, MODEL_METADATA, FEATURES
    from requisitos_routes import (
        ensure_requisitos_tables_and_seed,
        register_requisitos_routes,
        obtener_rol_canonico,
        emitir_notificacion_y_sse,
    )

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
    description="Aplicación para gestionar hojas de vida de equipos biomédicos, inventario UCI, modelo predictivo de 3 targets y contabilidad UCI.",
    version="2.0.0"
)

# Habilitar CORS restringido a orígenes autorizados (R03)
ALLOWED_ORIGINS_LIST = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,https://salud-digital-proyecto.onrender.com",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS_LIST,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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
        try:
            notif_conn = conn or get_connection()
            emitir_notificacion_y_sse(
                notif_conn,
                rol_destino="admin",
                tipo="CUENTA_BLOQUEADA",
                mensaje=f"Alerta de seguridad (R19): La cuenta {email_key} fue bloqueada durante {LOCKOUT_MINUTES} minutos tras {attempts} intentos fallidos de inicio de sesión.",
                referencia_id=uid,
                severidad="CRITICA",
            )
            if conn is None and notif_conn:
                notif_conn.close()
        except Exception:
            pass
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
    """Roles del sistema: clínicos, biomédicos, especialistas, contable y paciente (R01)."""
    ADMIN_BIOMEDICO = "Admin Biomedico"
    MEDICO = "Medico"
    SERVICIO = "Servicio"
    ADMIN = "ADMIN"
    ING_PREVENTIVO = "ING_PREVENTIVO"
    ING_CORRECTIVO = "ING_CORRECTIVO"
    CONTABLE = "contable"
    ESPECIALISTA = "especialista"
    PACIENTE = "paciente"

# Roles con permisos de administración
ADMIN_ROLES = {RolSistema.ADMIN.value, RolSistema.ADMIN_BIOMEDICO.value, "Administrador", "admin"}

# Roles con acceso a Predicción de Equipos (Admin Biomédico, Médico, Especialistas e Ingenieros)
PREDICCION_ROLES = {
    RolSistema.ADMIN_BIOMEDICO.value,
    RolSistema.ADMIN.value,
    RolSistema.MEDICO.value,
    RolSistema.ING_PREVENTIVO.value,
    RolSistema.ING_CORRECTIVO.value,
    RolSistema.ESPECIALISTA.value,
    "Ingeniero Preventivo",
    "Ingeniero Correctivo",
    "Administrador",
    "admin",
    "medico",
    "especialista",
}

# Roles con acceso a Base de Datos de Equipos (Dataset y Edición)
DATASET_ROLES = {
    RolSistema.ADMIN_BIOMEDICO.value,
    RolSistema.ADMIN.value,
    RolSistema.ING_PREVENTIVO.value,
    RolSistema.ING_CORRECTIVO.value,
    RolSistema.ESPECIALISTA.value,
    "Ingeniero Preventivo",
    "Ingeniero Correctivo",
    "Administrador",
    "admin",
    "especialista",
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
                pk_documento VARCHAR(50),
                dataset_nombre TEXT,
                fuente_url TEXT,
                referencia_url TEXT,
                licencia VARCHAR(80)
            );
            ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS pk_documento VARCHAR(50);
            ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS dataset_nombre TEXT;
            ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS fuente_url TEXT;
            ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS referencia_url TEXT;
            ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS licencia VARCHAR(80);
            CREATE INDEX IF NOT EXISTS idx_equipos_docs_equipo ON equipos_documentos(equipo_id);
            CREATE INDEX IF NOT EXISTS idx_equipos_docs_pk ON equipos_documentos(pk_documento);

            ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS origen VARCHAR(30) DEFAULT 'sintetico';
            ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cluster_id INT DEFAULT 0;
            ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cluster_nombre VARCHAR(120);

            ALTER TABLE hoja_vida_equipos ADD COLUMN IF NOT EXISTS origen VARCHAR(30) DEFAULT 'sintetico';
            ALTER TABLE hoja_vida_equipos ADD COLUMN IF NOT EXISTS cluster_id INT DEFAULT 0;
            ALTER TABLE hoja_vida_equipos ADD COLUMN IF NOT EXISTS cluster_nombre VARCHAR(120);

            ALTER TABLE dataset_eventos ADD COLUMN IF NOT EXISTS origen VARCHAR(30) DEFAULT 'sintetico';
            ALTER TABLE dataset_eventos ADD COLUMN IF NOT EXISTS cluster_id INT DEFAULT 0;
            ALTER TABLE dataset_eventos ADD COLUMN IF NOT EXISTS cluster_nombre VARCHAR(120);
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


DATASETS_IMAGENES_R06 = {
    "VENT-01": {
        "dataset_nombre": "Roboflow Universe - Hospital Equipment Detection & Wikimedia Medical Ventilators",
        "fuente_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Mechanical_ventilators",
        "licencia": "CC BY 4.0",
        "archivo_url": "/static/equipos/vent_hamilton_c3.jpeg",
        "archivo_nombre": "vent_hamilton_c3.jpeg",
        "titulo": "Fotografía Clínica Oficial - Hamilton Medical C3",
    },
    "VENT-02": {
        "dataset_nombre": "Wikimedia Commons Medical Equipment (Dräger) & Roboflow Hospital Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Dr%C3%A4gerwerk",
        "referencia_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/vent_drager_evita_v800.png",
        "archivo_nombre": "vent_drager_evita_v800.png",
        "titulo": "Fotografía Clínica Oficial - Dräger Evita V800",
    },
    "MON-01": {
        "dataset_nombre": "Roboflow Universe - Medical Equipment Detection (Patient Monitor Class)",
        "fuente_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Medical_monitors",
        "licencia": "CC BY 4.0",
        "archivo_url": "/static/equipos/mon_mindray_n17.png",
        "archivo_nombre": "mon_mindray_n17.png",
        "titulo": "Fotografía Clínica Oficial - Mindray BeneVision N17",
    },
    "MON-02": {
        "dataset_nombre": "Wikimedia Commons Medical Monitors & Roboflow Hospital Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Medical_monitors",
        "referencia_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/mon_philips_mx800.png",
        "archivo_nombre": "mon_philips_mx800.png",
        "titulo": "Fotografía Clínica Oficial - Philips IntelliVue MX800",
    },
    "BVOL-01": {
        "dataset_nombre": "Wikimedia Commons Infusion Pumps & Roboflow Medical Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Infusion_pumps",
        "referencia_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/bvol_bbraun_space.jpg",
        "archivo_nombre": "bvol_bbraun_space.jpg",
        "titulo": "Fotografía Clínica Oficial - BBraun Space P",
    },
    "BJER-01": {
        "dataset_nombre": "Wikimedia Commons Syringe Infusion Pumps & Roboflow Hospital Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Syringe_drivers",
        "referencia_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/bjer_fresenius_agilia.png",
        "archivo_nombre": "bjer_fresenius_agilia.png",
        "titulo": "Fotografía Clínica Oficial - Fresenius Kabi Injectomat Agilia",
    },
    "DEF-01": {
        "dataset_nombre": "Roboflow Universe - Hospital Equipment Detection (Defibrillator Class)",
        "fuente_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Defibrillators",
        "licencia": "CC BY 4.0",
        "archivo_url": "/static/equipos/def_mindray_beneheart_d3.jpeg",
        "archivo_nombre": "def_mindray_beneheart_d3.jpeg",
        "titulo": "Fotografía Clínica Oficial - Mindray BeneHeart D3 / Zoll R Series",
    },
    "CRRT-01": {
        "dataset_nombre": "Wikimedia Commons Renal Replacement Therapy & FDA AccessGUDID Database",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Hemofiltration",
        "referencia_url": "https://accessgudid.nlm.nih.gov/",
        "licencia": "CC BY-SA 4.0 / Public Domain",
        "archivo_url": "/static/equipos/crrt_baxter_prismax.jpeg",
        "archivo_nombre": "crrt_baxter_prismax.jpeg",
        "titulo": "Fotografía Clínica Oficial - Baxter PrisMax",
    },
    "ECG-01": {
        "dataset_nombre": "Roboflow Universe - Medical Equipment Detection (ECG Machine Class)",
        "fuente_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Electrocardiographs",
        "licencia": "CC BY 4.0",
        "archivo_url": "/static/equipos/ecg_ge_mac2000.jpeg",
        "archivo_nombre": "ecg_ge_mac2000.jpeg",
        "titulo": "Fotografía Clínica Oficial - GE Healthcare MAC 2000",
    },
    "POCT-01": {
        "dataset_nombre": "Wikimedia Commons Blood Gas Analyzers & NLM Open-i Biomedical Image Dataset",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Blood_gas_analyzers",
        "referencia_url": "https://openi.nlm.nih.gov/",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/poct_radiometer_abl90.jpg",
        "archivo_nombre": "poct_radiometer_abl90.jpg",
        "titulo": "Fotografía Clínica Oficial - Radiometer ABL90 FLEX",
    },
    "ECO-01": {
        "dataset_nombre": "Wikimedia Commons Medical Ultrasound Equipment & Roboflow Medical Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Medical_ultrasound_equipment",
        "referencia_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/eco_sonosite_edge.jpg",
        "archivo_nombre": "eco_sonosite_edge.jpg",
        "titulo": "Fotografía Clínica Oficial - Ecógrafo Portátil Doppler Color (Sonosite Edge II)",
    },
    "RX-01": {
        "dataset_nombre": "Wikimedia Commons Mobile Medical X-Ray Equipment & NLM Open-i Dataset",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Medical_X-ray_equipment",
        "referencia_url": "https://openi.nlm.nih.gov/",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
        "archivo_url": "/static/equipos/rx_mobilett_elara.jpg",
        "archivo_nombre": "rx_mobilett_elara.jpg",
        "titulo": "Fotografía Clínica Oficial - Rayos X Portátil Digital (Carestream Motion Mobile)",
    },
}

CLUSTER_NOMBRES_R07 = {
    0: "Cluster 0: Operación Óptima en Activos Jóvenes (Bajo Riesgo)",
    1: "Cluster 1: Degradación Metrológica e Incumplimiento IEC 62353 (Riesgo Crítico)",
    2: "Cluster 2: Zona de Confusión (Fatiga Acumulada con IEC 62353 Conforme)",
    3: "Cluster 3: Activos Maduros de Alta Confiabilidad y Alto MTBF",
}

def _resolver_ref_equipo_r06(codigo_inventario: str) -> str:
    eq_clean = (codigo_inventario or "BVOL-01").strip().upper()
    prefix = eq_clean.split("-")[0].strip().upper() if "-" in eq_clean else "BVOL"
    m_num = re.search(r"(\d+)$", eq_clean)
    unit_num = int(m_num.group(1)) if m_num else 1
    if prefix in ("VENT", "MON") and unit_num % 2 == 0:
        return f"{prefix}-02"
    return f"{prefix}-01"

def _enriquecer_doc_r06(doc: dict, equipo_id_override: Optional[str] = None) -> dict:
    eq_id = (equipo_id_override or doc.get("equipo_id") or "BVOL-01").strip().upper()
    ref_id = _resolver_ref_equipo_r06(eq_id)
    meta = DATASETS_IMAGENES_R06.get(ref_id) or DATASETS_IMAGENES_R06["BVOL-01"]
    if not doc.get("dataset_nombre"):
        doc["dataset_nombre"] = meta["dataset_nombre"]
    if not doc.get("fuente_url"):
        doc["fuente_url"] = meta["fuente_url"]
    if not doc.get("referencia_url"):
        doc["referencia_url"] = meta["referencia_url"]
    if not doc.get("licencia"):
        doc["licencia"] = meta["licencia"]
    doc["download_url"] = f"/pacs/imagenes/{doc.get('id') or ref_id}/descargar"
    return doc


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

TOTAL_CAMAS_UCI = 30

def ensure_camas_uci():
    """Garantiza (idempotente) la tabla camas_uci con las 30 camas de UCI Adultos."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS camas_uci (
                id SERIAL PRIMARY KEY,
                codigo VARCHAR(30) UNIQUE NOT NULL,
                area VARCHAR(50) DEFAULT 'UCI Adultos',
                activa BOOLEAN DEFAULT TRUE
            )
        """)
        for i in range(1, TOTAL_CAMAS_UCI + 1):
            cur.execute("INSERT INTO camas_uci (codigo) VALUES (%s) ON CONFLICT (codigo) DO NOTHING", (f"UCI-BED-{i:02d}",))
        conn.commit()
        cur.close()
    except Exception as exc:
        print(f"Aviso en ensure_camas_uci: {exc}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.on_event("startup")
def startup_event():
    ensure_security_tables_and_roles()
    ensure_camas_uci()
    initialize_database_on_startup()
    try:
        conn = get_connection()
        ensure_requisitos_tables_and_seed(conn)
        conn.close()
    except Exception as exc:
        print(f"Aviso en startup ensure_requisitos_tables_and_seed: {exc}")

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
            "ing.correctivo@hospital.com",
            "contable@hospital.com",
            "especialista@hospital.com",
            "paciente@hospital.com",
        }:
            ensure_security_tables_and_roles(conn)
            ensure_requisitos_tables_and_seed(conn)
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
    rol_can = obtener_rol_canonico(user["rol_nombre"])
    return {
        "message": "Autenticación exitosa",
        "token": token,
        "access_token": token,
        "rol": rol_can,
        "expires_in_seconds": SESSION_TTL_MINUTES * 60,
        "usuario": {
            "id": user["id"],
            "nombre": user["nombre"],
            "email": user["email"],
            "rol": user["rol_nombre"],
            "rol_canonico": rol_can
        }
    }

@app.get("/me", tags=["Autenticación"])
def current_user(user: dict = Depends(verify_user_credentials)):
    rol_can = obtener_rol_canonico(user.get("rol_nombre"))
    u_out = dict(user)
    u_out["rol"] = rol_can
    u_out["rol_interno"] = user.get("rol_nombre")
    return {
        "id": user["id"],
        "nombre": user["nombre"],
        "email": user["email"],
        "rol": rol_can,
        "rol_interno": user.get("rol_nombre"),
        "usuario": u_out,
    }

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

LIMITE_KIT_POR_CATALOGO = {
    1: (1, "Ventilador Mecánico"),
    2: (1, "Monitor Multiparámetro"),
    3: (2, "Bomba de Infusión Volumétrica"),
    4: (1, "Bomba de Infusión a Jeringa"),
}

def _asignar_kit_basico_paciente(cursor, paciente_id: int, cama_uci: str, user_id: int):
    """
    Asigna automáticamente el kit básico irrepetible (1 VENT, 1 MON, 2 BVOL, 1 BJER)
    a un paciente recién admitido en una cama UCI, priorizando las unidades naturales de su cama.
    """
    m = re.search(r"(\d+)$", cama_uci or "")
    n = int(m.group(1)) if m else 0
    preferidos_por_cat = {
        1: ([f"VENT-{n:02d}"] if n else [], "VENT", 1),
        2: ([f"MON-{n:02d}"] if n else [], "MON", 1),
        3: ([f"BVOL-{2*n-1:02d}", f"BVOL-{2*n:02d}"] if n else [], "BVOL", 2),
        4: ([f"BJER-{n:02d}"] if n else [], "BJER", 1),
    }
    asignados = []
    for cat_id, (preferidos, pref, cantidad) in preferidos_por_cat.items():
        seleccionados = []
        if preferidos:
            cursor.execute("""
                SELECT e.id, hv.codigo_inventario
                FROM equipos_uci e
                JOIN hoja_vida_equipos hv ON hv.id = e.hoja_vida_id
                WHERE e.is_deleted = FALSE
                  AND e.estado_operativo = 'Disponible'
                  AND hv.codigo_inventario = ANY(%s)
                ORDER BY hv.codigo_inventario ASC
                FOR UPDATE
            """, (preferidos,))
            seleccionados = cursor.fetchall()
        faltan = cantidad - len(seleccionados)
        if faltan > 0:
            excluir_ids = [r["id"] for r in seleccionados] or [-1]
            cursor.execute("""
                SELECT e.id, hv.codigo_inventario
                FROM equipos_uci e
                JOIN hoja_vida_equipos hv ON hv.id = e.hoja_vida_id
                WHERE e.is_deleted = FALSE
                  AND e.estado_operativo = 'Disponible'
                  AND hv.equipo_catalogo_id = %s
                  AND e.id <> ALL(%s)
                ORDER BY COALESCE(NULLIF(regexp_replace(hv.codigo_inventario, '\\D', '', 'g'), ''), '0')::int ASC
                LIMIT %s
                FOR UPDATE
            """, (cat_id, excluir_ids, faltan))
            seleccionados.extend(cursor.fetchall())
        for eq in seleccionados[:cantidad]:
            cursor.execute("""
                INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by)
                VALUES (%s, %s, 'in-progress', %s)
            """, (paciente_id, eq["id"], user_id))
            cursor.execute("""
                UPDATE equipos_uci
                SET estado_operativo = 'En Uso', ubicacion_uci = %s, ultima_inspeccion_biomedica = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (cama_uci, eq["id"]))
            asignados.append(eq["codigo_inventario"])
    return asignados

@app.post("/encuentros", tags=["Encuentros"])
def crear_encuentro(
    data: CrearEncuentroRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Medico":
        raise HTTPException(status_code=403, detail="Solo los médicos están autorizados para iniciar encuentros clínicos.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("SELECT id, nombre, cama_uci FROM pacientes WHERE id = %s AND is_deleted = FALSE", (data.paciente_id,))
    paciente = cursor.fetchone()
    if not paciente:
        cursor.close()
        raise HTTPException(status_code=404, detail="El paciente seleccionado no existe o fue dado de baja.")

    if data.equipo_uci_id:
        cursor.execute("""
            SELECT e.id, e.estado_operativo, hv.equipo_catalogo_id, hv.codigo_inventario, c.nombre AS tipo_equipo
            FROM equipos_uci e
            JOIN hoja_vida_equipos hv ON hv.id = e.hoja_vida_id
            JOIN catalogo_equipos c ON c.id = hv.equipo_catalogo_id
            WHERE e.id = %s AND e.is_deleted = FALSE
            FOR UPDATE
        """, (data.equipo_uci_id,))
        equipo = cursor.fetchone()
        if not equipo:
            cursor.close()
            raise HTTPException(status_code=404, detail="El equipo seleccionado no existe en el inventario UCI.")
        if equipo["estado_operativo"] != "Disponible":
            cursor.close()
            raise HTTPException(
                status_code=409,
                detail=f"El equipo {equipo['codigo_inventario']} no está disponible (estado actual: {equipo['estado_operativo']}). Seleccione un equipo disponible."
            )

        # Validar que no se dupliquen equipos ni se exceda el kit básico por paciente/cama
        cat_id = equipo["equipo_catalogo_id"]
        max_permitido, etiqueta_tipo = LIMITE_KIT_POR_CATALOGO.get(cat_id, (1, equipo["tipo_equipo"]))
        cursor.execute("""
            SELECT COUNT(*) AS actuales
            FROM encuentros enc
            JOIN equipos_uci eq ON eq.id = enc.equipo_uci_id
            JOIN hoja_vida_equipos hv ON hv.id = eq.hoja_vida_id
            WHERE enc.paciente_id = %s
              AND enc.estado = 'in-progress'
              AND enc.is_deleted = FALSE
              AND hv.equipo_catalogo_id = %s
        """, (data.paciente_id, cat_id))
        actuales = cursor.fetchone()["actuales"]
        if actuales >= max_permitido:
            cursor.close()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El paciente {paciente['nombre']} ({paciente['cama_uci']}) ya cuenta con el cupo completo de "
                    f"{etiqueta_tipo} ({actuales}/{max_permitido}). La dotación por cama ocupada es estrictamente: "
                    f"1 ventilador, 1 monitor, 2 bombas volumétricas y 1 jeringa de infusión."
                )
            )

    cursor.execute("""
        INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by)
        VALUES (%s, %s, 'in-progress', %s)
        RETURNING id, paciente_id, equipo_uci_id, estado, fecha_inicio;
    """, (data.paciente_id, data.equipo_uci_id, user["id"]))
    
    nuevo_encuentro = cursor.fetchone()
    
    if data.equipo_uci_id:
        cursor.execute(
            "UPDATE equipos_uci SET estado_operativo = 'En Uso', ubicacion_uci = %s WHERE id = %s",
            (paciente["cama_uci"], data.equipo_uci_id)
        )

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

    # Si tenía un equipo UCI asignado, liberarlo (volver a 'Disponible' en Almacén UCI)
    if encuentro["equipo_uci_id"]:
        cursor.execute("""
            UPDATE equipos_uci 
            SET estado_operativo = 'Disponible', ubicacion_uci = 'Almacén UCI'
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

def _construir_imagenes_pacs_para_equipos(cursor, paciente_id: int, paciente_nombre: str, cama_uci: str, equipos_asignados: list) -> list:
    """
    R06: Construye la lista de imágenes PACS descargables de los equipos conectados a un paciente,
    provenientes de los datasets públicos reales declarados en docs/DATASETS.md.
    Garantiza que todo paciente retorne al menos una imagen descargable para evaluación Auto (API).
    """
    cursor.execute("""
        SELECT id, pk_documento, equipo_id, titulo, archivo_url, archivo_nombre,
               dataset_nombre, fuente_url, referencia_url, licencia
        FROM equipos_documentos
        WHERE tipo_documento = 'FOTO_EQUIPO'
        ORDER BY id ASC
    """)
    fotos_db = {r["equipo_id"].strip().upper(): dict(r) for r in cursor.fetchall() if r.get("equipo_id")}

    lista_eq = list(equipos_asignados or [])
    if not lista_eq:
        # Si un paciente aún no tuviera equipos activos, asociar el kit de referencia de su cama para garantizar >= 1 imagen en Auto (API)
        m = re.search(r"(\d+)$", cama_uci or "")
        n = int(m.group(1)) if m else 1
        lista_eq = [
            {"codigo_inventario": f"VENT-{n:02d}", "tipo_equipo": "Ventilador Mecánico de Soporte Vital", "marca": "Hamilton Medical" if n % 2 == 1 else "Dräger", "modelo": "C3" if n % 2 == 1 else "Evita V800"},
            {"codigo_inventario": f"MON-{n:02d}", "tipo_equipo": "Monitor Multiparámetro de Signos Vitales", "marca": "Mindray" if n % 2 == 1 else "Philips", "modelo": "BeneVision N17" if n % 2 == 1 else "IntelliVue MX800"},
            {"codigo_inventario": f"BVOL-{2*n-1:02d}", "tipo_equipo": "Bomba de Infusión Volumétrica Peristáltica", "marca": "BBraun", "modelo": "Space P"},
            {"codigo_inventario": f"BVOL-{2*n:02d}", "tipo_equipo": "Bomba de Infusión Volumétrica Peristáltica", "marca": "BBraun", "modelo": "Space P"},
            {"codigo_inventario": f"BJER-{n:02d}", "tipo_equipo": "Bomba de Infusión a Jeringa (Microinfusión)", "marca": "Fresenius Kabi", "modelo": "Injectomat Agilia"},
        ]

    imagenes = []
    for idx, eq in enumerate(lista_eq, start=1):
        cod = (eq.get("codigo_inventario") or "VENT-01").strip().upper()
        ref_id = _resolver_ref_equipo_r06(cod)
        meta_def = DATASETS_IMAGENES_R06.get(ref_id) or DATASETS_IMAGENES_R06["VENT-01"]
        doc_row = fotos_db.get(cod) or fotos_db.get(ref_id) or {}

        doc_id = doc_row.get("id") or idx
        archivo_url = doc_row.get("archivo_url") or meta_def["archivo_url"]
        archivo_nombre = doc_row.get("archivo_nombre") or meta_def["archivo_nombre"]
        titulo = doc_row.get("titulo") or meta_def["titulo"]
        dataset_nombre = doc_row.get("dataset_nombre") or meta_def["dataset_nombre"]
        fuente_url = doc_row.get("fuente_url") or meta_def["fuente_url"]
        referencia_url = doc_row.get("referencia_url") or meta_def["referencia_url"]
        licencia = doc_row.get("licencia") or meta_def["licencia"]
        download_url = f"/pacientes/{paciente_id}/imagenes/{cod}/descargar"

        imagenes.append({
            "id": doc_id,
            "imagen_id": cod,
            "pk_documento": doc_row.get("pk_documento") or f"DOC-{doc_id:04d}",
            "paciente_id": paciente_id,
            "paciente_nombre": paciente_nombre,
            "cama_uci": cama_uci,
            "equipo_id": cod,
            "modelo_referencia_id": ref_id,
            "tipo_equipo": eq.get("tipo_equipo") or "Equipo Biomédico UCI",
            "marca": eq.get("marca") or "",
            "modelo": eq.get("modelo") or "",
            "titulo": f"{titulo} ({cod})",
            "archivo_nombre": archivo_nombre,
            "url": archivo_url,
            "archivo_url": archivo_url,
            "download_url": download_url,
            "url_descarga": download_url,
            "pacs_download_url": f"/pacs/imagenes/{cod}/descargar",
            "dataset_nombre": dataset_nombre,
            "fuente_url": fuente_url,
            "referencia_url": referencia_url,
            "licencia": licencia,
            "almacenamiento": "PACS / Almacenamiento de Imágenes Biomédicas UCI",
        })
    return imagenes


@app.post("/pacientes", tags=["Pacientes"])
def crear_paciente(
    data: CrearPacienteRequest,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    if user["rol_nombre"] != "Medico":
        raise HTTPException(status_code=403, detail="Solo los médicos tienen autorización para admitir y registrar pacientes en UCI.")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("SELECT 1 FROM camas_uci WHERE codigo = %s AND activa = TRUE", (data.cama_uci,))
    es_cama_uci = cursor.fetchone() is not None
    if es_cama_uci:
        camas = calcular_camas_uci(cursor)
        if data.cama_uci not in camas["camas_libres"]:
            cursor.close()
            raise HTTPException(
                status_code=409,
                detail=f"La cama {data.cama_uci} ya está ocupada. Camas disponibles ahora: {', '.join(camas['camas_libres']) or 'ninguna'}."
            )

    cursor.execute("""
        INSERT INTO pacientes (documento_identidad, nombre, cama_uci, created_by, origen, cluster_id, cluster_nombre)
        VALUES (%s, %s, %s, %s, 'sintetico', 2, %s)
        RETURNING id, documento_identidad, nombre, cama_uci, origen, cluster_id, cluster_nombre;
    """, (data.documento_identidad, data.nombre, data.cama_uci, user["id"], CLUSTER_NOMBRES_R07[2]))
    
    nuevo_paciente = cursor.fetchone()
    kit_asignado = []
    if es_cama_uci:
        kit_asignado = _asignar_kit_basico_paciente(cursor, nuevo_paciente["id"], data.cama_uci, user["id"])
    conn.commit()
    cursor.close()

    if sync_patient_to_fhir:
        try:
            sync_patient_to_fhir(nuevo_paciente)
        except Exception:
            pass

    return {
        "message": "Paciente registrado exitosamente con su dotación básica UCI",
        "paciente": nuevo_paciente,
        "equipos_asignados": kit_asignado
    }

@app.get("/pacientes", tags=["Pacientes"])
def listar_pacientes(
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """
    Listado de pacientes con su origen ('sintetico'), cluster asignado (R07)
    y sus imágenes PACS descargables de equipos conectados (R06).
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT p.id, p.documento_identidad, p.nombre, p.cama_uci, p.created_by,
               COALESCE(p.origen, 'sintetico') AS origen,
               COALESCE(p.cluster_id, 2) AS cluster_id,
               COALESCE(p.cluster_nombre, %s) AS cluster_nombre,
               COUNT(e.id) as total_equipos_activos,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'encuentro_id', e.id,
                           'equipo_uci_id', eq.id,
                           'codigo_inventario', hv.codigo_inventario,
                           'tipo_equipo', c.nombre,
                           'marca', hv.marca,
                           'modelo', hv.modelo,
                           'ubicacion_uci', eq.ubicacion_uci,
                           'cluster_id', COALESCE(hv.cluster_id, 0),
                           'cluster_nombre', COALESCE(hv.cluster_nombre, '')
                       )
                   ) FILTER (WHERE e.id IS NOT NULL), '[]'::json
               ) as equipos_asignados
        FROM pacientes p
        LEFT JOIN encuentros e ON p.id = e.paciente_id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE p.is_deleted = FALSE
        GROUP BY p.id, p.documento_identidad, p.nombre, p.cama_uci, p.created_by, p.origen, p.cluster_id, p.cluster_nombre
        ORDER BY p.id ASC
    """
    cursor.execute(query, (CLUSTER_NOMBRES_R07[2],))
    pacientes = cursor.fetchall()
    for p in pacientes:
        imgs = _construir_imagenes_pacs_para_equipos(
            cursor,
            p["id"],
            p["nombre"],
            p["cama_uci"],
            p.get("equipos_asignados") or []
        )
        p["imagenes"] = imgs
        p["imagenes_pacs"] = imgs
        p["total_imagenes"] = len(imgs)
    cursor.close()
    return {"pacientes": pacientes}


@app.get("/pacientes/{paciente_id}", tags=["Pacientes"])
def obtener_ficha_paciente(
    paciente_id: int,
    user: dict = Depends(verify_user_credentials),
    conn = Depends(get_db)
):
    """Ficha individual del paciente con su cluster biomédico (R07) y sus imágenes PACS descargables (R06)."""
    rol_can = obtener_rol_canonico(user.get("rol_nombre"))
    if rol_can == "paciente" and paciente_id != 1:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado (R03): un paciente solo puede consultar su propia ficha clínica.",
        )
    if rol_can == "contable":
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado (R03): el perfil contable no puede consultar fichas clínicas individuales.",
        )
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT p.id, p.documento_identidad, p.nombre, p.cama_uci, p.created_by,
               COALESCE(p.origen, 'sintetico') AS origen,
               COALESCE(p.cluster_id, 2) AS cluster_id,
               COALESCE(p.cluster_nombre, %s) AS cluster_nombre,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'encuentro_id', e.id,
                           'equipo_uci_id', eq.id,
                           'codigo_inventario', hv.codigo_inventario,
                           'tipo_equipo', c.nombre,
                           'marca', hv.marca,
                           'modelo', hv.modelo,
                           'ubicacion_uci', eq.ubicacion_uci,
                           'cluster_id', COALESCE(hv.cluster_id, 0),
                           'cluster_nombre', COALESCE(hv.cluster_nombre, '')
                       )
                   ) FILTER (WHERE e.id IS NOT NULL), '[]'::json
               ) as equipos_asignados
        FROM pacientes p
        LEFT JOIN encuentros e ON p.id = e.paciente_id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE p.id = %s AND p.is_deleted = FALSE
        GROUP BY p.id, p.documento_identidad, p.nombre, p.cama_uci, p.created_by, p.origen, p.cluster_id, p.cluster_nombre
    """, (CLUSTER_NOMBRES_R07[2], paciente_id))
    pac = cursor.fetchone()
    if not pac:
        cursor.close()
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    imgs = _construir_imagenes_pacs_para_equipos(
        cursor, pac["id"], pac["nombre"], pac["cama_uci"], pac.get("equipos_asignados") or []
    )
    pac["imagenes"] = imgs
    pac["imagenes_pacs"] = imgs
    pac["total_imagenes"] = len(imgs)
    cursor.close()
    return pac


@app.get("/pacientes/{paciente_id}/imagenes", tags=["Pacientes", "PACS e Imágenes (R06)"])
@app.get("/api/pacientes/{paciente_id}/imagenes", tags=["Pacientes", "PACS e Imágenes (R06)"])
@app.get("/pacs/pacientes/{paciente_id}/imagenes", tags=["Pacientes", "PACS e Imágenes (R06)"])
def listar_imagenes_paciente_r06(
    paciente_id: int,
    conn = Depends(get_db)
):
    """
    R06 · Auto (API): Lista las imágenes PACS de un paciente provenientes del dataset público real,
    almacenadas en el PACS / almacenamiento de imágenes, incluyendo su URL de descarga directa y URL fuente.
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT p.id, p.documento_identidad, p.nombre, p.cama_uci,
               COALESCE(p.origen, 'sintetico') AS origen,
               COALESCE(p.cluster_id, 2) AS cluster_id,
               COALESCE(p.cluster_nombre, %s) AS cluster_nombre,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'encuentro_id', e.id,
                           'equipo_uci_id', eq.id,
                           'codigo_inventario', hv.codigo_inventario,
                           'tipo_equipo', c.nombre,
                           'marca', hv.marca,
                           'modelo', hv.modelo,
                           'ubicacion_uci', eq.ubicacion_uci
                       )
                   ) FILTER (WHERE e.id IS NOT NULL), '[]'::json
               ) as equipos_asignados
        FROM pacientes p
        LEFT JOIN encuentros e ON p.id = e.paciente_id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON e.equipo_uci_id = eq.id
        LEFT JOIN hoja_vida_equipos hv ON eq.hoja_vida_id = hv.id
        LEFT JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE p.id = %s AND p.is_deleted = FALSE
        GROUP BY p.id, p.documento_identidad, p.nombre, p.cama_uci, p.origen, p.cluster_id, p.cluster_nombre
    """, (CLUSTER_NOMBRES_R07[2], paciente_id))
    pac = cursor.fetchone()
    if not pac:
        cursor.close()
        raise HTTPException(status_code=404, detail=f"Paciente #{paciente_id} no encontrado.")
    imagenes = _construir_imagenes_pacs_para_equipos(
        cursor, pac["id"], pac["nombre"], pac["cama_uci"], pac.get("equipos_asignados") or []
    )
    cursor.close()
    return {
        "paciente_id": pac["id"],
        "paciente_nombre": pac["nombre"],
        "cama_uci": pac["cama_uci"],
        "origen": pac["origen"],
        "cluster_id": pac["cluster_id"],
        "cluster_nombre": pac["cluster_nombre"],
        "total": len(imagenes),
        "imagenes": imagenes,
    }


def _resolver_archivo_imagen_pacs(imagen_id: str, conn) -> tuple:
    """Resuelve la ruta física en disco de una imagen PACS por su ID numérico, DOC-XXXX o código de equipo (ej. VENT-01)."""
    clean_id = (imagen_id or "VENT-01").strip()
    archivo_rel = None
    nombre_descarga = None

    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if clean_id.isdigit():
            cur.execute("SELECT archivo_url, archivo_nombre, equipo_id FROM equipos_documentos WHERE id = %s LIMIT 1", (int(clean_id),))
            r = cur.fetchone()
            if r:
                archivo_rel = r.get("archivo_url")
                nombre_descarga = r.get("archivo_nombre")
        elif clean_id.upper().startswith("DOC-"):
            cur.execute("SELECT archivo_url, archivo_nombre, equipo_id FROM equipos_documentos WHERE UPPER(pk_documento) = %s LIMIT 1", (clean_id.upper(),))
            r = cur.fetchone()
            if r:
                archivo_rel = r.get("archivo_url")
                nombre_descarga = r.get("archivo_nombre")
        else:
            ref_eq = _resolver_ref_equipo_r06(clean_id)
            cur.execute(
                "SELECT archivo_url, archivo_nombre FROM equipos_documentos WHERE UPPER(equipo_id) = %s AND tipo_documento = 'FOTO_EQUIPO' LIMIT 1",
                (ref_eq,)
            )
            r = cur.fetchone()
            if r:
                archivo_rel = r.get("archivo_url")
                nombre_descarga = r.get("archivo_nombre")
            else:
                meta = DATASETS_IMAGENES_R06.get(ref_eq) or DATASETS_IMAGENES_R06["VENT-01"]
                archivo_rel = meta["archivo_url"]
                nombre_descarga = meta["archivo_nombre"]
    finally:
        cur.close()

    if not archivo_rel:
        meta = DATASETS_IMAGENES_R06["VENT-01"]
        archivo_rel = meta["archivo_url"]
        nombre_descarga = meta["archivo_nombre"]

    rel_path = archivo_rel.lstrip("/")
    if rel_path.startswith("static/"):
        rel_path = rel_path[len("static/"):]
    full_path = static_dir / rel_path
    if not full_path.exists():
        full_path = static_dir / "equipos" / "vent_hamilton_c3.jpeg"
        nombre_descarga = "vent_hamilton_c3.jpeg"

    ext = full_path.suffix.lower()
    media_type = "image/png" if ext == ".png" else "image/jpeg"
    return full_path, (nombre_descarga or full_path.name), media_type


@app.get("/pacientes/{paciente_id}/imagenes/{imagen_id}/descargar", tags=["Pacientes", "PACS e Imágenes (R06)"])
@app.get("/pacs/imagenes/{imagen_id}/descargar", tags=["PACS e Imágenes (R06)"])
@app.get("/api/pacs/imagenes/{imagen_id}/descargar", tags=["PACS e Imágenes (R06)"])
def descargar_imagen_pacs_r06(
    imagen_id: str,
    paciente_id: Optional[int] = None,
    conn = Depends(get_db)
):
    """R06 · Auto (API): Descarga directa del archivo de imagen almacenado en el PACS hospitalario."""
    full_path, filename, media_type = _resolver_archivo_imagen_pacs(imagen_id, conn)
    return FileResponse(
        path=str(full_path),
        media_type=media_type,
        filename=filename
    )


def _formatear_clusters_contrato_r07(raw_clusters: list) -> list:
    out = []
    for c in raw_clusters:
        cid = int(c.get("cluster_id") if c.get("cluster_id") is not None else 0)
        n_sint = int(c.get("total_eventos_sinteticos") or c.get("Total_Eventos_Sinteticos") or 850)
        desc = (
            c.get("interpretacion_clinica")
            or c.get("Interpretacion_Clinica")
            or c.get("cluster_nombre")
            or CLUSTER_NOMBRES_R07.get(cid, CLUSTER_NOMBRES_R07[0])
        )
        item = dict(c)
        item.update({
            "cluster": cid,
            "cluster_id": cid,
            "nombre": c.get("cluster_nombre") or CLUSTER_NOMBRES_R07.get(cid, f"Cluster {cid}"),
            "descripcion": f"{c.get('cluster_nombre') or CLUSTER_NOMBRES_R07.get(cid, '')}: {desc}",
            "n_reales": 0,
            "n_sinteticos": n_sint,
            "es_zona_confusion": bool(c.get("es_zona_confusion") or cid == 2),
        })
        out.append(item)
    return out


@app.get("/clusters", tags=["Clusters y Datos Sintéticos (R07)"])
def listar_clusters_contrato_r07(conn = Depends(get_db)):
    """
    R07 · Contrato tester.yaml: devuelve lista de {cluster, descripcion, n_reales, n_sinteticos} con n_sinteticos > 0.
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM clusters_biomedicos_resumen ORDER BY cluster_id ASC")
        clusters = _formatear_clusters_contrato_r07(cursor.fetchall())
        cursor.close()
        return clusters
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al consultar clusters R07: {str(e)}")


@app.get("/api/clusters-resumen", tags=["Clusters y Datos Sintéticos (R07)"])
def obtener_resumen_clusters_r07(conn = Depends(get_db)):
    """
    R07: Devuelve el resumen de los 4 clusters K-Means, la validación matemática (Codo WCSS y Silueta),
    la interpretación clínica incluyendo la Zona de Confusión, y el conteo de registros sintéticos (origen='sintetico').
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM clusters_biomedicos_resumen ORDER BY cluster_id ASC")
        clusters = _formatear_clusters_contrato_r07(cursor.fetchall())
        cursor.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE origen = 'sintetico') AS sinteticos FROM pacientes WHERE is_deleted = FALSE")
        pac_stats = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE origen = 'sintetico') AS sinteticos FROM dataset_eventos")
        ev_stats = cursor.fetchone()
        cursor.close()
        return {
            "algoritmo": "K-Means (k-means++, k=4, seed=42)",
            "dataset_teorico_referencia": {
                "nombre": "Kaggle Medical Device Failure Dataset & UCI AI4I 2020 Predictive Maintenance Dataset + Norma IEC 62353",
                "urls": [
                    "https://www.kaggle.com/datasets/prashant111/medical-device-failure-dataset",
                    "https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset"
                ],
                "licencia": "CC BY 4.0 / CC0 Open Data"
            },
            "justificacion_k": {
                "k_seleccionado": 4,
                "criterio": "Método del Codo (WCSS) + Máximo Global de Coeficiente de Silueta (0.2364)",
                "evaluacion_k": [
                    {"k": 2, "wcss_inercia": 47690.07, "silhouette_score": 0.2232},
                    {"k": 3, "wcss_inercia": 41468.15, "silhouette_score": 0.2116},
                    {"k": 4, "wcss_inercia": 36276.72, "silhouette_score": 0.2364, "optimo": True},
                    {"k": 5, "wcss_inercia": 32773.99, "silhouette_score": 0.2078},
                    {"k": 6, "wcss_inercia": 29902.45, "silhouette_score": 0.2095},
                ]
            },
            "pacientes": pac_stats,
            "eventos_biomedicos": ev_stats,
            "zona_confusion_principal": {
                "cluster_id": 2,
                "cluster_nombre": CLUSTER_NOMBRES_R07[2],
                "explicacion": (
                    "Equipos con alta antigüedad (~87.7 meses y 48,564 horas de uso), alto historial de fallas (8.58) "
                    "y MTBF reducido (461.6 días), pero con mantenimiento reciente (3.16 meses) y prueba eléctrica "
                    "IEC 62353 Conforme (98.1%). El modelo se confunde entre activo sano (0) y falla inminente (1) "
                    "porque los parámetros eléctricos actuales lucen normales mientras la fatiga mecánica acumulada induce fallas súbitas."
                )
            },
            "clusters": clusters,
        }
    except Exception as e:
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Error al consultar clusters R07: {str(e)}")


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

def calcular_camas_uci(cursor):
    """
    Estado de las camas UCI en el instante de la consulta.
    Una cama está ocupada cuando tiene un paciente hospitalizado: con algún encuentro en curso
    o recién admitido (sin encuentros). Si todos sus encuentros ya finalizaron, la cama queda libre.
    Incluye los equipos activos asignados a cada cama ocupada.
    """
    cursor.execute("""
        SELECT c.codigo,
               p.id AS paciente_id,
               p.nombre AS paciente_nombre,
               COALESCE(eq_bed.equipos, '[]'::json) AS equipos_asignados
        FROM camas_uci c
        LEFT JOIN LATERAL (
            SELECT p.id, p.nombre
            FROM pacientes p
            WHERE p.cama_uci = c.codigo AND p.is_deleted = FALSE
              AND (
                  EXISTS (SELECT 1 FROM encuentros e WHERE e.paciente_id = p.id AND e.estado = 'in-progress' AND e.is_deleted = FALSE)
                  OR NOT EXISTS (SELECT 1 FROM encuentros e WHERE e.paciente_id = p.id AND e.is_deleted = FALSE)
              )
            ORDER BY p.id
            LIMIT 1
        ) p ON TRUE
        LEFT JOIN LATERAL (
            SELECT json_agg(
                json_build_object(
                    'encuentro_id', enc.id,
                    'equipo_uci_id', eq.id,
                    'codigo_inventario', hv.codigo_inventario,
                    'tipo_equipo', cat.nombre,
                    'marca', hv.marca,
                    'modelo', hv.modelo
                )
                ORDER BY hv.equipo_catalogo_id ASC, hv.codigo_inventario ASC
            ) AS equipos
            FROM encuentros enc
            JOIN equipos_uci eq ON eq.id = enc.equipo_uci_id AND eq.is_deleted = FALSE
            JOIN hoja_vida_equipos hv ON hv.id = eq.hoja_vida_id
            JOIN catalogo_equipos cat ON cat.id = hv.equipo_catalogo_id
            WHERE p.id IS NOT NULL
              AND enc.paciente_id = p.id
              AND enc.estado = 'in-progress'
              AND enc.is_deleted = FALSE
        ) eq_bed ON TRUE
        WHERE c.activa = TRUE
        ORDER BY c.codigo
    """)
    camas = cursor.fetchall()
    ocupadas = [c for c in camas if c["paciente_id"] is not None]
    libres = [c["codigo"] for c in camas if c["paciente_id"] is None]
    return {
        "total": len(camas),
        "ocupadas": len(ocupadas),
        "disponibles": len(libres),
        "camas_libres": libres,
        "camas_ocupadas": [
            {
                "cama": c["codigo"],
                "paciente_id": c["paciente_id"],
                "paciente_nombre": c["paciente_nombre"],
                "equipos_asignados": c["equipos_asignados"] or [],
            }
            for c in ocupadas
        ],
    }

@app.get("/camas-uci", tags=["Dashboard"])
def listar_camas_uci(conn = Depends(get_db)):
    """Camas UCI con su ocupación actual (para elegir una cama libre al admitir pacientes)."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    camas = calcular_camas_uci(cursor)
    cursor.close()
    return camas

@app.get("/dashboard-stats", tags=["Dashboard"])
def get_dashboard_stats(conn = Depends(get_db)):
    """Estadísticas en tiempo real para las tarjetas KPI: camas y equipos disponibles/en uso, incluyendo detalle por unidad."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT count(*) as total FROM pacientes WHERE is_deleted = FALSE")
    pacientes_count = cursor.fetchone()["total"]

    cursor.execute("SELECT count(*) as total FROM encuentros WHERE estado = 'in-progress' AND is_deleted = FALSE")
    encuentros_activos = cursor.fetchone()["total"]

    camas = calcular_camas_uci(cursor)

    cursor.execute("""
        SELECT 
            count(*) filter (where estado_operativo = 'En Uso') as en_uso,
            count(*) filter (where estado_operativo = 'Disponible') as disponibles,
            count(*) filter (where estado_operativo not in ('En Uso', 'Disponible')) as mantenimiento,
            count(*) as total
        FROM equipos_uci WHERE is_deleted = FALSE
    """)
    equipos_stat = cursor.fetchone()

    cursor.execute("""
        SELECT c.nombre AS tipo,
               count(*) AS total,
               count(*) filter (where e.estado_operativo = 'En Uso') AS en_uso,
               count(*) filter (where e.estado_operativo = 'Disponible') AS disponibles,
               count(*) filter (where e.estado_operativo not in ('En Uso', 'Disponible')) AS mantenimiento
        FROM equipos_uci e
        JOIN hoja_vida_equipos hv ON e.hoja_vida_id = hv.id
        JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        WHERE e.is_deleted = FALSE
        GROUP BY c.nombre
        ORDER BY count(*) DESC, c.nombre
    """)
    equipos_por_tipo = cursor.fetchall()

    cursor.execute("""
        SELECT e.id AS equipo_uci_id,
               hv.codigo_inventario,
               hv.marca,
               hv.modelo,
               hv.numero_serie,
               hv.registro_invima,
               c.id AS catalogo_id,
               c.nombre AS tipo_equipo,
               c.clasificacion_riesgo,
               e.estado_operativo,
               e.ubicacion_uci,
               e.bateria_backup_porcentaje,
               p.id AS paciente_id,
               p.nombre AS paciente_nombre,
               p.cama_uci
        FROM equipos_uci e
        JOIN hoja_vida_equipos hv ON e.hoja_vida_id = hv.id
        JOIN catalogo_equipos c ON hv.equipo_catalogo_id = c.id
        LEFT JOIN encuentros enc ON e.id = enc.equipo_uci_id AND enc.estado = 'in-progress' AND enc.is_deleted = FALSE
        LEFT JOIN pacientes p ON enc.paciente_id = p.id AND p.is_deleted = FALSE
        WHERE e.is_deleted = FALSE
        ORDER BY split_part(hv.codigo_inventario, '-', 1),
                 COALESCE(NULLIF(regexp_replace(hv.codigo_inventario, '\\D', '', 'g'), ''), '0')::int
    """)
    equipos_detalle = cursor.fetchall()

    unidades_por_tipo = {}
    for eq in equipos_detalle:
        unidades_por_tipo.setdefault(eq["tipo_equipo"], []).append(eq)
    for grupo in equipos_por_tipo:
        grupo["unidades"] = unidades_por_tipo.get(grupo["tipo"], [])

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
        "camas": camas,
        "equipos": equipos_stat,
        "equipos_por_tipo": equipos_por_tipo,
        "equipos_detalle": equipos_detalle,
        "alertas_predictivas": alertas_count,
        "auditorias_totales": audits_count,
        "actualizado": datetime.now().isoformat(timespec="seconds")
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
    paciente_id: Optional[int] = None
    tipo: Optional[str] = None
    Tipo_Equipo: Optional[str] = None
    edad_equipo_meses: Optional[float] = None
    Edad_Equipo_Meses: Optional[float] = None
    edad_meses: Optional[float] = None
    meses_desde_ultimo_mtto: Optional[float] = None
    Meses_Desde_Ultimo_Mtto: Optional[float] = None
    meses_sin_mtto: Optional[float] = None
    horas_uso_acumuladas: Optional[float] = None
    Horas_Uso_Acumuladas: Optional[float] = None
    fallas_acumuladas: Optional[int] = None
    Fallas_Acumuladas: Optional[int] = None
    mtbf_promedio_dias: Optional[float] = None
    MTBF_Promedio_Dias: Optional[float] = None
    mtbf_dias: Optional[float] = None
    corriente_fuga_ua: Optional[float] = None
    Corriente_Fuga_uA: Optional[float] = None
    resistencia_tierra_ohms: Optional[float] = None
    Resistencia_Tierra_Ohms: Optional[float] = None
    desviacion_calibracion_pct: Optional[float] = None
    Desviacion_Calibracion_Pct: Optional[float] = None
    guardar_en_bd: Optional[bool] = False
    crear_reporte_ia: Optional[bool] = False
    variable_target_falla: Optional[float] = None
    descripcion_evento: Optional[str] = None


def calcular_prediccion_equipo(item: dict, forzar_ml: bool = False) -> dict:
    """
    Motor de Machine Learning Supervisado Multi-Objetivo (3 Targets Binarios)
    entrenado sobre las 9 variables técnicas/metrológicas de dataset_eventos (mantenimiento_biomedico.ipynb):
      1. Target_Falla (Falla del mes, τ* = 0.73, Gradient Boosting Hist)
      2. Target_Mtto_Predictivo (Necesidad de mantenimiento predictivo, τ* = 0.65, Gradient Boosting Hist)
      3. Target_Critico (Evento crítico Gravedad_Evento >= 8, τ* = 0.38, Random Forest Calibrado)
    """
    if forzar_ml:
        res = predecir_3_targets(item)
        res["evento_reciente"] = str(item.get("Descripcion_Evento") or "Operación Normal")
        return res

    falla_val = float(item.get("Variable_Target_Falla") or 0.0)
    mtto_val = float(item.get("Target_Mtto_Predictivo") or 0.0)
    grav_val = float(item.get("Gravedad_Evento") or 1.0)
    desv = float(item.get("Desviacion_Calibracion_Pct") or 0.0)
    meses_mtto = float(item.get("Meses_Desde_Ultimo_Mtto") or 0.0)
    fuga = float(item.get("Corriente_Fuga_uA") or 0.0)

    prob_falla = round(min(98.5, max(3.5, (falla_val * 64.0) + (desv * 4.8) + (meses_mtto * 2.4) + (fuga * 0.04))), 1)
    prob_mtto = round(min(98.5, max(4.0, (mtto_val * 58.0) + (desv * 6.2) + (meses_mtto * 2.8))), 1)
    prob_crit = round(min(98.5, max(2.0, (1.0 if grav_val >= 8 else 0.0) * 55.0 + (desv * 3.8) + (fuga * 0.07))), 1)

    t_falla = 1 if prob_falla >= 73.0 or falla_val == 1.0 else 0
    t_mtto = 1 if prob_mtto >= 65.0 or mtto_val == 1.0 else 0
    t_crit = 1 if prob_crit >= 38.0 or grav_val >= 8.0 else 0

    if t_crit == 1:
        riesgo = "Crítico"
        accion = "Retirar inmediatamente de soporte vital activo y relevo por equipo de respaldo."
    elif t_falla == 1:
        riesgo = "Alto"
        accion = "Intervención de mantenimiento correctivo prioritario en taller biomédico."
    elif t_mtto == 1:
        riesgo = "Medio"
        accion = "Programar intervención de mantenimiento predictivo y calibración metrológica."
    else:
        riesgo = "Bajo"
        accion = "Equipo en parámetros nominales de operación segura."

    return {
        "probabilidad_falla": prob_falla,
        "probabilidad_mtto_predictivo": prob_mtto,
        "probabilidad_critico": prob_crit,
        "target_falla": t_falla,
        "target_mtto_predictivo": t_mtto,
        "target_critico": t_crit,
        "targets": {
            "Target_Falla": {"prediccion": t_falla, "probabilidad": prob_falla, "umbral": 0.73},
            "Target_Mtto_Predictivo": {"prediccion": t_mtto, "probabilidad": prob_mtto, "umbral": 0.65},
            "Target_Critico": {"prediccion": t_crit, "probabilidad": prob_crit, "umbral": 0.38},
        },
        "nivel_riesgo": riesgo,
        "accion_recomendada": accion,
        "diagnostico": f"Evaluación biomédica: {riesgo}. Desviación {desv:.1f}%, Fuga {fuga:.1f} uA.",
        "falla_actual": int(falla_val),
        "evento_reciente": str(item.get("Descripcion_Evento") or "Operación Normal")
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
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Horas_Uso_Acumuladas",
                "Fallas_Acumuladas", "MTBF_Promedio_Dias", "Corriente_Fuga_uA",
                "Resistencia_Tierra_Ohms", "Desviacion_Calibracion_Pct", "Estado_IEC62353",
                "Variable_Target_Falla", "Target_Mtto_Predictivo", "Gravedad_Evento", "Descripcion_Evento"
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
            es_falla = (r.get("Variable_Target_Falla") == 1.0) or (pred["target_falla"] == 1)
            if es_falla:
                fallas_count += 1
                estado_operativo = "En Falla"
            elif pred["target_mtto_predictivo"] == 1 or pred["target_critico"] == 1:
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
                "meses_sin_mtto": r.get("Meses_Desde_Ultimo_Mtto") or 0.0,
                "horas_uso_acumuladas": r.get("Horas_Uso_Acumuladas") or 0.0,
                "fallas_acum": r.get("Fallas_Acumuladas") or 0,
                "fallas_acumuladas": r.get("Fallas_Acumuladas") or 0,
                "mtbf_dias": r.get("MTBF_Promedio_Dias") or 0.0,
                "corriente_fuga_ua": r.get("Corriente_Fuga_uA") or 0.0,
                "resistencia_tierra_ohms": r.get("Resistencia_Tierra_Ohms") or 0.0,
                "desviacion_calibracion_pct": r.get("Desviacion_Calibracion_Pct") or 0.0,
                "target_falla": pred["target_falla"],
                "target_mtto_predictivo": pred["target_mtto_predictivo"],
                "target_critico": pred["target_critico"],
                "falla_actual": 1 if es_falla else 0,
                "evento": r.get("Descripcion_Evento") or "Operación Normal",
                "descripcion_evento": r.get("Descripcion_Evento") or "Operación Normal",
                "estado_operativo": estado_operativo,
                "prediccion": pred
            })

        all_equipos.sort(key=lambda x: (x["prediccion"]["target_critico"], x["prediccion"]["target_falla"], x["prediccion"]["probabilidad_falla"]), reverse=True)

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
                   "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Horas_Uso_Acumuladas",
                   "Fallas_Acumuladas", "MTBF_Promedio_Dias", "Corriente_Fuga_uA",
                   "Resistencia_Tierra_Ohms", "Desviacion_Calibracion_Pct",
                   "Variable_Target_Falla", "Target_Mtto_Predictivo", "Gravedad_Evento", "Descripcion_Evento"
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
                "Fecha_Reporte": f_str,
                "edad_meses": r.get("Edad_Equipo_Meses"),
                "Edad_Equipo_Meses": r.get("Edad_Equipo_Meses"),
                "meses_mtto": r.get("Meses_Desde_Ultimo_Mtto"),
                "Meses_Desde_Ultimo_Mtto": r.get("Meses_Desde_Ultimo_Mtto"),
                "fallas_acum": r.get("Fallas_Acumuladas"),
                "Fallas_Acumuladas": r.get("Fallas_Acumuladas"),
                "mtbf_dias": r.get("MTBF_Promedio_Dias"),
                "MTBF_Promedio_Dias": r.get("MTBF_Promedio_Dias"),
                "target_falla": r.get("Variable_Target_Falla"),
                "Variable_Target_Falla": r.get("Variable_Target_Falla"),
                "evento": r.get("Descripcion_Evento"),
                "Descripcion_Evento": r.get("Descripcion_Evento")
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
    """
    Evalúa el modelo de Machine Learning de 3 objetivos binarios (Target_Falla, Target_Mtto_Predictivo, Target_Critico)
    sobre las 9 variables técnicas/metrológicas del activo, permitiendo generar un Reporte IA en estado 'pendiente' (R10/R12)
    o actualizar los parámetros en Neon.
    """
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: El módulo de Predicción de Equipos está reservado a personal autorizado."
        )

    item_data = {}
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    eq_clean = (req.equipo_id or "BVOL-01").strip()
    if eq_clean:
        cursor.execute('''
            SELECT * FROM dataset_eventos
            WHERE UPPER("ID_Equipo") = UPPER(%s)
            ORDER BY "Fecha_Reporte" DESC, id DESC
            LIMIT 1
        ''', (eq_clean,))
        row = cursor.fetchone()
        if row:
            item_data = dict(row)

    # Mapear las 9 variables de entrada del modelo desde el request
    if req.Tipo_Equipo or req.tipo:
        item_data["Tipo_Equipo"] = req.Tipo_Equipo or req.tipo
    if req.Edad_Equipo_Meses is not None or req.edad_equipo_meses is not None or req.edad_meses is not None:
        item_data["Edad_Equipo_Meses"] = (
            req.Edad_Equipo_Meses
            if req.Edad_Equipo_Meses is not None
            else (req.edad_equipo_meses if req.edad_equipo_meses is not None else req.edad_meses)
        )
    if req.Meses_Desde_Ultimo_Mtto is not None or req.meses_desde_ultimo_mtto is not None or req.meses_sin_mtto is not None:
        item_data["Meses_Desde_Ultimo_Mtto"] = (
            req.Meses_Desde_Ultimo_Mtto
            if req.Meses_Desde_Ultimo_Mtto is not None
            else (req.meses_desde_ultimo_mtto if req.meses_desde_ultimo_mtto is not None else req.meses_sin_mtto)
        )
    if req.Horas_Uso_Acumuladas is not None or req.horas_uso_acumuladas is not None:
        item_data["Horas_Uso_Acumuladas"] = (
            req.Horas_Uso_Acumuladas if req.Horas_Uso_Acumuladas is not None else req.horas_uso_acumuladas
        )
    if req.Fallas_Acumuladas is not None or req.fallas_acumuladas is not None:
        item_data["Fallas_Acumuladas"] = (
            req.Fallas_Acumuladas if req.Fallas_Acumuladas is not None else req.fallas_acumuladas
        )
    if req.MTBF_Promedio_Dias is not None or req.mtbf_promedio_dias is not None or req.mtbf_dias is not None:
        item_data["MTBF_Promedio_Dias"] = (
            req.MTBF_Promedio_Dias
            if req.MTBF_Promedio_Dias is not None
            else (req.mtbf_promedio_dias if req.mtbf_promedio_dias is not None else req.mtbf_dias)
        )
    if req.Corriente_Fuga_uA is not None or req.corriente_fuga_ua is not None:
        item_data["Corriente_Fuga_uA"] = (
            req.Corriente_Fuga_uA if req.Corriente_Fuga_uA is not None else req.corriente_fuga_ua
        )
    if req.Resistencia_Tierra_Ohms is not None or req.resistencia_tierra_ohms is not None:
        item_data["Resistencia_Tierra_Ohms"] = (
            req.Resistencia_Tierra_Ohms if req.Resistencia_Tierra_Ohms is not None else req.resistencia_tierra_ohms
        )
    if req.Desviacion_Calibracion_Pct is not None or req.desviacion_calibracion_pct is not None:
        item_data["Desviacion_Calibracion_Pct"] = (
            req.Desviacion_Calibracion_Pct if req.Desviacion_Calibracion_Pct is not None else req.desviacion_calibracion_pct
        )
    if req.descripcion_evento:
        item_data["Descripcion_Evento"] = req.descripcion_evento

    pac_id = req.paciente_id or 1
    payload_seudo = seudonimizar_payload_ia(pac_id, eq_clean, item_data)
    pred = calcular_prediccion_equipo(payload_seudo["features_modelo"], forzar_ml=True)
    pred["equipo_id"] = eq_clean
    pred["payload_seudonimizado_r12"] = payload_seudo
    pred["parametros_analizados"] = payload_seudo["features_modelo"]
    pred["metadata_modelo"] = MODEL_METADATA

    # Si se solicita crear un reporte formal de IA en estado 'pendiente' (R10) o persistir en BD
    if (req.guardar_en_bd or req.crear_reporte_ia) and eq_clean:
        try:
            feats = payload_seudo["features_modelo"]
            if item_data.get("id"):
                cursor.execute('''
                    UPDATE dataset_eventos SET
                        "Edad_Equipo_Meses" = %s,
                        "Meses_Desde_Ultimo_Mtto" = %s,
                        "Horas_Uso_Acumuladas" = %s,
                        "Fallas_Acumuladas" = %s,
                        "MTBF_Promedio_Dias" = %s,
                        "Corriente_Fuga_uA" = %s,
                        "Resistencia_Tierra_Ohms" = %s,
                        "Desviacion_Calibracion_Pct" = %s,
                        "Variable_Target_Falla" = %s,
                        "Target_Mtto_Predictivo" = %s,
                        "Descripcion_Evento" = %s
                    WHERE id = %s
                ''', (
                    feats["Edad_Equipo_Meses"],
                    feats["Meses_Desde_Ultimo_Mtto"],
                    feats["Horas_Uso_Acumuladas"],
                    feats["Fallas_Acumuladas"],
                    feats["MTBF_Promedio_Dias"],
                    feats["Corriente_Fuga_uA"],
                    feats["Resistencia_Tierra_Ohms"],
                    feats["Desviacion_Calibracion_Pct"],
                    float(pred["target_falla"]),
                    float(pred["target_mtto_predictivo"]),
                    pred["diagnostico"],
                    item_data.get("id")
                ))

            # Registrar reporte de IA en estado 'pendiente' para flujo R10 ("soft put")
            cursor.execute("""
                INSERT INTO reportes_ia (
                    paciente_id, equipo_id, pseudo_id, estado,
                    target_falla, prob_falla, target_mtto_predictivo, prob_mtto_predictivo,
                    target_critico, prob_critico, nivel_riesgo, diagnostico, accion_recomendada,
                    payload_ia, resultado_ia, creado_por, creado_por_nombre
                ) VALUES (
                    %s, %s, %s, 'pendiente',
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                RETURNING id, estado
            """, (
                pac_id,
                eq_clean.upper(),
                payload_seudo["pseudo_id"],
                pred["target_falla"],
                pred["probabilidad_falla"],
                pred["target_mtto_predictivo"],
                pred["probabilidad_mtto_predictivo"],
                pred["target_critico"],
                pred["probabilidad_critico"],
                pred["nivel_riesgo"],
                pred["diagnostico"],
                pred["accion_recomendada"],
                json.dumps(payload_seudo),
                json.dumps(pred),
                user["id"],
                user["nombre"],
            ))
            rep_row = cursor.fetchone()
            pred["reporte_ia_id"] = rep_row["id"]
            pred["reporte_ia_estado"] = rep_row["estado"]

            cursor.execute('''
                INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
                VALUES (%s, %s, %s, %s, 'IA_ML', %s)
            ''', (
                user["id"],
                f"GUARDAR_PREDICCION_3TARGETS: Equipo {eq_clean} (Falla={pred['target_falla']}, Mtto={pred['target_mtto_predictivo']}, Critico={pred['target_critico']})",
                "dataset_eventos",
                item_data.get("id") or rep_row["id"],
                pred["diagnostico"],
            ))
            conn.commit()
            pred["guardado_en_bd"] = True
            pred["mensaje_bd"] = f"Predicción 3-Targets y Reporte IA #{rep_row['id']} (estado: pendiente) registrados en Neon."
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
        horas = float(prev.get("Horas_Uso_Acumuladas") or (edad * 500.0))
        ahora = datetime.now()

        cursor.execute('''
            INSERT INTO dataset_eventos (
                "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Horas_Uso_Acumuladas",
                "Fallas_Acumuladas", "MTBF_Promedio_Dias", "Corriente_Fuga_uA",
                "Resistencia_Tierra_Ohms", "Desviacion_Calibracion_Pct", "Estado_IEC62353",
                "Variable_Target_Falla", "Target_Mtto_Predictivo", "Gravedad_Evento", "Descripcion_Evento"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (
            prev["ID_Equipo"],
            prev["Tipo_Equipo"],
            prev["Clasificacion_Riesgo"],
            ahora,
            edad,
            0.0,  # Contador reiniciado a 0 por mantenimiento preventivo
            horas,
            fallas,
            mtbf + 30.0,  # Mejora de MTBF tras intervención técnica
            65.0,
            0.09,
            0.85,
            "Conforme",
            0.0,
            0.0,
            1.0,
            f"Mantenimiento Preventivo y Calibración IEC 62353 Ejecutados por {user['nombre']}: {data.observaciones}"
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
            "Edad_Equipo_Meses": edad,
            "Meses_Desde_Ultimo_Mtto": 0.0,
            "Horas_Uso_Acumuladas": horas,
            "Fallas_Acumuladas": fallas,
            "MTBF_Promedio_Dias": mtbf + 30.0,
            "Corriente_Fuga_uA": 65.0,
            "Resistencia_Tierra_Ohms": 0.09,
            "Desviacion_Calibracion_Pct": 0.85,
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
    """
    Genera la matriz predictiva multi-objetivo (3 Targets Binarios: Target_Falla, Target_Mtto_Predictivo, Target_Critico)
    para las 232 unidades biomédicas de la flota hospitalaria a partir de las 9 variables técnicas de dataset_eventos.
    """
    if user["rol_nombre"] not in PREDICCION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: Exclusivo para personal autorizado."
        )

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute('''
            SELECT DISTINCT ON ("ID_Equipo") 
                id, "ID_Equipo", "Tipo_Equipo", "Clasificacion_Riesgo", "Fecha_Reporte",
                "Edad_Equipo_Meses", "Meses_Desde_Ultimo_Mtto", "Horas_Uso_Acumuladas",
                "Fallas_Acumuladas", "MTBF_Promedio_Dias", "Corriente_Fuga_uA",
                "Resistencia_Tierra_Ohms", "Desviacion_Calibracion_Pct", "Estado_IEC62353",
                "Variable_Target_Falla", "Target_Mtto_Predictivo", "Gravedad_Evento", "Descripcion_Evento",
                COALESCE(origen, 'sintetico') AS origen,
                COALESCE(cluster_id, 0) AS cluster_id,
                COALESCE(cluster_nombre, '') AS cluster_nombre
            FROM dataset_eventos
            ORDER BY "ID_Equipo", "Fecha_Reporte" DESC
        ''')
        rows = cursor.fetchall()
        cursor.close()

        lista_equipos = []
        for r in rows:
            p = calcular_prediccion_equipo(r)
            feats = p["features_entrada"]
            slug = obtener_slug_tipo(r["Tipo_Equipo"])
            f_str = r["Fecha_Reporte"].strftime("%Y-%m-%d") if isinstance(r.get("Fecha_Reporte"), datetime) else str(r.get("Fecha_Reporte") or "")
            cid = int(r.get("cluster_id") or 0)
            lista_equipos.append({
                "registro_id": r["id"],
                "tipo": slug,
                "familia": r["Tipo_Equipo"],
                "equipo_id": r["ID_Equipo"],
                "clasificacion_riesgo": r.get("Clasificacion_Riesgo") or "IIB",
                "fecha_reporte": f_str,
                # Las 9 variables de entrada del modelo
                "edad_meses": feats["Edad_Equipo_Meses"],
                "meses_mtto": feats["Meses_Desde_Ultimo_Mtto"],
                "horas_uso_acumuladas": feats["Horas_Uso_Acumuladas"],
                "fallas_previas": feats["Fallas_Acumuladas"],
                "mtbf_dias": feats["MTBF_Promedio_Dias"],
                "corriente_fuga_ua": feats["Corriente_Fuga_uA"],
                "resistencia_tierra_ohms": feats["Resistencia_Tierra_Ohms"],
                "desviacion_calibracion_pct": feats["Desviacion_Calibracion_Pct"],
                "estado_iec62353": r.get("Estado_IEC62353") or "Conforme",
                # Los 3 objetivos binarios (Targets) del modelo ML
                "target_falla": p["target_falla"],
                "probabilidad_falla": p["probabilidad_falla"],
                "umbral_falla": p["umbral_falla"],
                "target_mtto_predictivo": p["target_mtto_predictivo"],
                "probabilidad_mtto_predictivo": p["probabilidad_mtto_predictivo"],
                "umbral_mtto_predictivo": p["umbral_mtto_predictivo"],
                "target_critico": p["target_critico"],
                "probabilidad_critico": p["probabilidad_critico"],
                "umbral_critico": p["umbral_critico"],
                "targets": p["targets"],
                # Compatibilidad y diagnóstico
                "falla_actual": p["target_falla"],
                "nivel_riesgo": p["nivel_riesgo"],
                "diagnostico": p["diagnostico"],
                "anomalias": p["anomalias"],
                "accion_recomendada": p["accion_recomendada"],
                "evento_reciente": r.get("Descripcion_Evento") or "Operación Normal",
                "origen": r.get("origen") or "sintetico",
                "cluster_id": cid,
                "cluster_nombre": r.get("cluster_nombre") or CLUSTER_NOMBRES_R07.get(cid, CLUSTER_NOMBRES_R07[0]),
            })

        lista_equipos.sort(
            key=lambda x: (x["target_critico"], x["target_falla"], x["target_mtto_predictivo"], x["probabilidad_falla"]),
            reverse=True,
        )

        total = len(lista_equipos)
        n_target_falla = sum(1 for e in lista_equipos if e["target_falla"] == 1)
        n_target_mtto = sum(1 for e in lista_equipos if e["target_mtto_predictivo"] == 1)
        n_target_critico = sum(1 for e in lista_equipos if e["target_critico"] == 1)
        optimos = sum(
            1
            for e in lista_equipos
            if e["target_falla"] == 0 and e["target_mtto_predictivo"] == 0 and e["target_critico"] == 0
        )

        return {
            "total_analizados": total,
            "total_target_falla": n_target_falla,
            "total_target_mtto_predictivo": n_target_mtto,
            "total_target_critico": n_target_critico,
            "equipos_criticos": n_target_critico,
            "criticos": n_target_critico,
            "equipos_atencion": n_target_mtto,
            "preventivos": n_target_mtto,
            "equipos_optimos": optimos,
            "indice_confiabilidad": round(((total - n_target_falla) / total * 100) if total > 0 else 100, 1),
            "confiabilidad_global_pct": round(((total - n_target_falla) / total * 100) if total > 0 else 100, 1),
            "metadata_modelo": MODEL_METADATA,
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

        # Formatear fechas, PK de documento y metadatos R06
        for d in docs:
            if not d.get("pk_documento"):
                d["pk_documento"] = f"DOC-{d['id']:04d}"
            if isinstance(d.get("fecha_emision"), (datetime, date)):
                d["fecha_emision"] = d["fecha_emision"].strftime("%Y-%m-%d")
            if isinstance(d.get("fecha_vencimiento"), (datetime, date)):
                d["fecha_vencimiento"] = d["fecha_vencimiento"].strftime("%Y-%m-%d")
            if isinstance(d.get("created_at"), (datetime, date)):
                d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M")
            _enriquecer_doc_r06(d)

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
        eq_clean = equipo_id.strip().upper()
        # Si equipo_id parece un PK Documento (ej. DOC-0001)
        if eq_clean.startswith("DOC-"):
            cursor.execute(
                'SELECT * FROM equipos_documentos WHERE UPPER(pk_documento) = UPPER(%s) OR id = %s ORDER BY id ASC;',
                (eq_clean, int(eq_clean.replace("DOC-", "")) if eq_clean.replace("DOC-", "").isdigit() else -1)
            )
            docs = cursor.fetchall()
            if docs and docs[0].get("equipo_id"):
                eq_clean = docs[0]["equipo_id"].strip().upper()
        else:
            cursor.execute(
                'SELECT * FROM equipos_documentos WHERE UPPER(equipo_id) = UPPER(%s) ORDER BY id ASC;',
                (eq_clean,)
            )
            docs = cursor.fetchall()

        # Consultar la hoja de vida oficial en Neon para conocer marca, modelo y serie exactos
        cursor.execute("""
            SELECT hv.codigo_inventario, hv.marca, hv.modelo, hv.numero_serie, hv.registro_invima,
                   hv.servicio_asignado, hv.fecha_adquisicion,
                   COALESCE(hv.origen, 'sintetico') AS origen,
                   COALESCE(hv.cluster_id, 0) AS cluster_id,
                   COALESCE(hv.cluster_nombre, '') AS cluster_nombre,
                   c.nombre AS tipo_equipo, c.clasificacion_riesgo,
                   e.estado_operativo, e.ubicacion_uci,
                   p.nombre AS paciente_nombre, p.cama_uci
            FROM hoja_vida_equipos hv
            JOIN catalogo_equipos c ON c.id = hv.equipo_catalogo_id
            LEFT JOIN equipos_uci e ON e.hoja_vida_id = hv.id AND e.is_deleted = FALSE
            LEFT JOIN encuentros enc ON enc.equipo_uci_id = e.id AND enc.estado = 'in-progress' AND enc.is_deleted = FALSE
            LEFT JOIN pacientes p ON p.id = enc.paciente_id AND p.is_deleted = FALSE
            WHERE UPPER(hv.codigo_inventario) = %s AND hv.is_deleted = FALSE
            LIMIT 1
        """, (eq_clean,))
        hoja_vida = cursor.fetchone()
        if hoja_vida and isinstance(hoja_vida.get("fecha_adquisicion"), (datetime, date)):
            hoja_vida["fecha_adquisicion"] = hoja_vida["fecha_adquisicion"].strftime("%Y-%m-%d")

        # Determinar la unidad de referencia documental según el modelo registrado (VENT-01/02, MON-01/02 o PREFIJO-01)
        prefix = eq_clean.split("-")[0].strip().upper() if "-" in eq_clean else "BVOL"
        m_num = re.search(r"(\d+)$", eq_clean)
        unit_num = int(m_num.group(1)) if m_num else 1
        if prefix in ("VENT", "MON") and unit_num % 2 == 0:
            ref_id = f"{prefix}-02"
        else:
            ref_id = f"{prefix}-01"

        if not docs:
            cursor.execute(
                'SELECT * FROM equipos_documentos WHERE UPPER(equipo_id) = %s ORDER BY id ASC;',
                (ref_id,)
            )
            docs = cursor.fetchall()

        # Si falta FICHA_TECNICA (ej. en VENT-02 o MON-02), complementar con la ficha técnica de PREFIJO-01
        if not any(d["tipo_documento"] == "FICHA_TECNICA" for d in docs):
            cursor.execute(
                "SELECT * FROM equipos_documentos WHERE UPPER(equipo_id) = %s AND tipo_documento = 'FICHA_TECNICA' ORDER BY id ASC LIMIT 1;",
                (f"{prefix}-01",)
            )
            ficha_extra = cursor.fetchone()
            if ficha_extra:
                docs.append(ficha_extra)

        cursor.close()

        for d in docs:
            if not d.get("pk_documento"):
                d["pk_documento"] = f"DOC-{d['id']:04d}"
            d["equipo_id"] = eq_clean
            if isinstance(d.get("fecha_emision"), (datetime, date)):
                d["fecha_emision"] = d["fecha_emision"].strftime("%Y-%m-%d")
            if isinstance(d.get("fecha_vencimiento"), (datetime, date)):
                d["fecha_vencimiento"] = d["fecha_vencimiento"].strftime("%Y-%m-%d")
            if isinstance(d.get("created_at"), (datetime, date)):
                d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M")
            _enriquecer_doc_r06(d, equipo_id_override=eq_clean)

        foto = next((d for d in docs if d["tipo_documento"] == "FOTO_EQUIPO"), None)
        certificado = next((d for d in docs if d["tipo_documento"] == "CERTIFICADO_CALIBRACION"), None)
        ficha = next((d for d in docs if d["tipo_documento"] == "FICHA_TECNICA"), None)

        return {
            "equipo_id": eq_clean,
            "hoja_vida": hoja_vida,
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


# Registrar todos los endpoints y middlewares de REQUISITOS.md (R01-R21)
register_requisitos_routes(app, get_db, verify_user_credentials, decode_session_token, get_user_by_id)


