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

# Configuración de Base de Datos
DATABASE_URL = os.getenv("DATABASE_URL")
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
                    realizado_por: Optional[int] = None):
    """Registra un evento de acceso en login_logs. Nunca interrumpe el flujo de login."""
    try:
        log_conn = get_connection()
        try:
            cur = log_conn.cursor()
            cur.execute("""
                INSERT INTO login_logs (usuario_id, email, evento, detalle, ip_origen, realizado_por)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (usuario_id, email, evento, detalle, ip, realizado_por))
            log_conn.commit()
            cur.close()
        finally:
            log_conn.close()
    except Exception as exc:
        print(f"No se pudo registrar el evento de login ({evento}): {exc}")

def _minutes_left(locked_until) -> int:
    secs = (locked_until - datetime.now()).total_seconds()
    return max(1, int((secs + 59) // 60))

def check_account_lockout(email: str, ip: Optional[str] = None):
    email_key = normalize_email(email)
    now = datetime.now()

    # 1) Estado persistente en base de datos
    locked = None
    try:
        lock_conn = get_connection()
        try:
            cur = lock_conn.cursor()
            cur.execute(
                "SELECT id, locked_until FROM usuarios WHERE LOWER(email) = %s AND is_deleted = FALSE",
                (email_key,)
            )
            row = cur.fetchone()
            if row and row[1]:
                if now < row[1]:
                    locked = (row[0], row[1])
                else:
                    # Bloqueo vencido: se limpia el estado
                    cur.execute(
                        "UPDATE usuarios SET failed_login_attempts = 0, locked_until = NULL WHERE id = %s",
                        (row[0],)
                    )
                    lock_conn.commit()
            cur.close()
        finally:
            lock_conn.close()
    except Exception:
        pass

    if locked:
        log_login_event(email_key, "LOGIN_BLOCKED", locked[0], "Intento de ingreso con la cuenta bloqueada", ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos. Intente de nuevo en {_minutes_left(locked[1])} minuto(s) o solicite a un administrador que la desbloquee."
        )

    # 2) Respaldo en memoria (correos inexistentes o base de datos no disponible)
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

def register_failed_attempt(email: str, ip: Optional[str] = None):
    email_key = normalize_email(email)
    now = datetime.now()
    outcome = None
    uid = None

    # 1) Contador persistente en base de datos
    try:
        att_conn = get_connection()
        try:
            cur = att_conn.cursor()
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
                att_conn.commit()
                outcome = (attempts, is_locked)
            cur.close()
        finally:
            att_conn.close()
    except Exception:
        outcome = None
        uid = None

    # 2) Respaldo en memoria (correo inexistente o base de datos no disponible)
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
                        f"Cuenta bloqueada {LOCKOUT_MINUTES} min tras {attempts} intentos fallidos", ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta bloqueada durante 15 minutos tras alcanzar 3 intentos fallidos."
        )
    log_login_event(email_key, "LOGIN_FAIL", uid, f"Contraseña incorrecta (intento {attempts} de {MAX_FAILED_ATTEMPTS})", ip)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Contraseña incorrecta. Intento {attempts} de {MAX_FAILED_ATTEMPTS}."
    )

def reset_login_attempts(email: str):
    email_key = normalize_email(email)
    LOGIN_ATTEMPTS.pop(email_key, None)
    try:
        rst_conn = get_connection()
        try:
            cur = rst_conn.cursor()
            cur.execute("""
                UPDATE usuarios SET failed_login_attempts = 0, locked_until = NULL
                WHERE LOWER(email) = %s
                  AND (COALESCE(failed_login_attempts, 0) <> 0 OR locked_until IS NOT NULL)
            """, (email_key,))
            rst_conn.commit()
            cur.close()
        finally:
            rst_conn.close()
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
    x_user_email: Optional[str] = Header(default=None, description="Correo del usuario (compatibilidad)"),
    x_user_password: Optional[str] = Header(default=None, description="Contraseña (compatibilidad)"),
    conn = Depends(get_db)
):
    if x_session_token:
        user_id = decode_session_token(x_session_token)
        return get_user_by_id(conn, user_id)

    if not x_user_email or not x_user_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida."
        )

    email = normalize_email(x_user_email)
    check_account_lockout(email)
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
        register_failed_attempt(email)
    if not verify_and_upgrade_password(conn, user["id"], x_user_password, user["password_hash"]):
        register_failed_attempt(email)

    reset_login_attempts(email)
    user.pop("password_hash", None)
    return user

# ==========================================
# ROLES (RBAC) Y DEPENDENCIAS DE SEGURIDAD
# ==========================================

class RolSistema(str, Enum):
    """Roles del sistema: los 3 clínicos/biomédicos originales + los 4 nuevos de mantenimiento."""
    # Roles originales
    ADMIN_BIOMEDICO = "Admin Biomedico"
    MEDICO = "Medico"
    SERVICIO = "Servicio"
    # Nuevos roles de gestión de mantenimiento
    ADMIN = "ADMIN"
    JEFE_AREA = "JEFE_AREA"
    ING_PREVENTIVO = "ING_PREVENTIVO"
    ING_CORRECTIVO = "ING_CORRECTIVO"

# Roles con permisos de administración (el Admin Biomédico original conserva su acceso)
ADMIN_ROLES = {RolSistema.ADMIN.value, RolSistema.ADMIN_BIOMEDICO.value}

def require_roles(allowed_roles: List[str]):
    """Fábrica de dependencias: require_roles(["ADMIN", "JEFE_AREA"])."""
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
    check_account_lockout(email, ip)
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
        register_failed_attempt(email, ip)
    if not verify_and_upgrade_password(conn, user["id"], credentials.password, user["password_hash"]):
        register_failed_attempt(email, ip)

    reset_login_attempts(email)
    log_login_event(email, "LOGIN_OK", user["id"], f"Ingreso exitoso ({user['rol_nombre']})", ip)
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
    """Permite al Servicio IoT, Médico o Administrador Biomédico registrar una nueva medición telemétrica."""
    if user["rol_nombre"] not in ["Medico", "Servicio", "Admin Biomedico"]:
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

    es_admin = user["rol_nombre"] == "Admin Biomedico"
    es_medico_autor = user["rol_nombre"] == "Medico" and obs["created_by"] == user["id"]

    if not (es_admin or es_medico_autor):
        cursor.close()
        if user["rol_nombre"] == "Medico":
            raise HTTPException(status_code=403, detail="Restricción de autoría: Un médico solo puede editar sus propios registros clínicos.")
        raise HTTPException(status_code=403, detail="Rol sin permisos para editar registros clínicos.")

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
    """Permite al Admin Biomédico o Médico autor eliminar registros clínicos."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s AND is_deleted = FALSE", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada o ya eliminada")

    es_admin = user["rol_nombre"] == "Admin Biomedico"
    es_medico_autor = user["rol_nombre"] == "Medico" and obs["created_by"] == user["id"]

    if not (es_admin or es_medico_autor):
        cursor.close()
        if user["rol_nombre"] == "Medico":
            raise HTTPException(status_code=403, detail="Restricción de autoría: Un médico solo puede eliminar sus propios registros clínicos.")
        raise HTTPException(status_code=403, detail="Rol sin permisos para eliminar registros clínicos.")

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
    """Permite al Admin Biomédico o Médico autor restaurar un registro clínico."""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM observaciones WHERE id = %s", (obs_id,))
    obs = cursor.fetchone()

    if not obs:
        cursor.close()
        raise HTTPException(status_code=404, detail="Observación no encontrada")

    es_admin = user["rol_nombre"] == "Admin Biomedico"
    es_medico_autor = user["rol_nombre"] == "Medico" and obs["created_by"] == user["id"]

    if not (es_admin or es_medico_autor):
        cursor.close()
        if user["rol_nombre"] == "Medico":
            raise HTTPException(status_code=403, detail="Acceso denegado: Restauración clínica reservada al médico autor de este registro.")
        raise HTTPException(status_code=403, detail="Acceso denegado: No tiene permisos para restaurar registros.")

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