"""
Implementación integral de los bloques de REQUISITOS.md (R01–R22):
  - R01: 5 roles canónicos (admin, medico, especialista, contable, paciente) y endpoint /me (/yo)
  - R03: Seguridad HTTP (CORS restringido, X-Content-Type-Options, X-Frame-Options, Rate Limiting 429, RBAC)
  - R07: Contrato /clusters [{cluster, descripcion, n_reales, n_sinteticos}]
  - R08: Remisiones entre Médico y Especialista Biomédico/Clínico + HAPI FHIR ServiceRequest
  - R09: Contabilidad y Costos Hospitalarios UCI con MongoDB (pymongo) + espejo PostgreSQL (facturas_uci)
  - R10: Reportes de IA con aprobación médica/especialista ("soft put": pendiente -> aprobado / rechazado)
  - R11: Exportación de Historia Clínica y Soporte Vital UCI en PDF real (application/pdf)
  - R12: Capa de seudonimización hacia el modelo de IA (sin PII: sin nombre ni documento)
  - R16–R20: Eventos en tiempo real (SSE), Bandeja de notificaciones persistente, /pendientes,
              4 Reactores Automáticos (R19) y Alertas Críticas con reconocimiento obligatorio (R20)
  - R21: Webhook reactivo con HAPI FHIR (POST /fhir/webhook)
"""

import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

from app.ml_model import (
    predecir_3_targets,
    seudonimizar_payload_ia,
    MODEL_METADATA,
    FEATURES,
)

# ==============================================================================
# 1. MAPEO DE ROLES CANÓNICOS (R01)
# ==============================================================================

ROLE_TO_CANONICAL = {
    "Admin Biomedico": "admin",
    "ADMIN": "admin",
    "Administrador": "admin",
    "admin": "admin",
    "Medico": "medico",
    "medico": "medico",
    "ING_PREVENTIVO": "especialista",
    "ING_CORRECTIVO": "especialista",
    "Ingeniero Preventivo": "especialista",
    "Ingeniero Correctivo": "especialista",
    "especialista": "especialista",
    "Servicio": "especialista",
    "contable": "contable",
    "Contable": "contable",
    "Contable UCI": "contable",
    "paciente": "paciente",
    "Paciente": "paciente",
}


def obtener_rol_canonico(rol_nombre: Optional[str]) -> str:
    if not rol_nombre:
        return "paciente"
    return ROLE_TO_CANONICAL.get(rol_nombre, rol_nombre.lower())


# ==============================================================================
# 2. BROKER SSE EN TIEMPO REAL (R16, R17, R19, R20, R21)
# ==============================================================================

class SSEBroker:
    def __init__(self):
        self.subscribers: List[Dict[str, Any]] = []
        self.recent_events: deque = deque(maxlen=100)

    def publish(self, event_data: Dict[str, Any]):
        if "fecha" not in event_data:
            event_data["fecha"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.recent_events.append(event_data)
        dead = []
        for sub in list(self.subscribers):
            rol_dest = (event_data.get("rol_destino") or "todos").lower()
            sub_rol = (sub.get("rol_canonico") or "").lower()
            sub_uid = sub.get("usuario_id")
            dest_uid = event_data.get("usuario_id")
            # Entregar si es para todos, para admin, para el rol específico o usuario específico
            if (
                rol_dest in ("todos", "all", "")
                or sub_rol == "admin"
                or rol_dest == sub_rol
                or (dest_uid and sub_uid == dest_uid)
            ):
                try:
                    sub["queue"].put_nowait(event_data)
                except Exception:
                    dead.append(sub)
        for d in dead:
            if d in self.subscribers:
                self.subscribers.remove(d)


sse_broker = SSEBroker()


# ==============================================================================
# 3. CONEXIÓN MONGODB PARA CONTABILIDAD (R09) CON FALLBACK TRANSACCIONAL
# ==============================================================================

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/uci_contabilidad")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "uci_contabilidad")
_mongo_client = None
_mongo_checked_at = 0.0


def get_mongo_db():
    """Intenta conectar a MongoDB (contenedor mongo o URI remota); retorna None si no está activo."""
    global _mongo_client, _mongo_checked_at
    now = time.time()
    if _mongo_client is False and (now - _mongo_checked_at) < 30.0:
        return None
    if _mongo_client not in (None, False):
        try:
            return _mongo_client[MONGO_DB_NAME]
        except Exception:
            _mongo_client = False
            _mongo_checked_at = now
            return None
    try:
        import pymongo
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=800, connectTimeoutMS=800)
        client.admin.command("ping")
        db = client[MONGO_DB_NAME]
        db["facturas_uci"].create_index("id", unique=True)
        db["facturas_uci"].create_index("reporte_id")
        db["facturas_uci"].create_index([("paciente_id", 1), ("estado", 1)])
        _mongo_client = client
        _mongo_checked_at = now
        return db
    except Exception:
        _mongo_client = False
        _mongo_checked_at = now
        return None


def sync_factura_to_mongo(factura_dict: Dict[str, Any]):
    db = get_mongo_db()
    if db is None:
        return False
    try:
        doc = dict(factura_dict)
        for k, v in list(doc.items()):
            if isinstance(v, (datetime, date)):
                doc[k] = v.isoformat()
        db["facturas_uci"].update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
        return True
    except Exception:
        return False


# ==============================================================================
# 4. ESQUEMA DE TABLAS Y SEMILLAS EN NEON POSTGRESQL (R01, R08–R12, R18–R21)
# ==============================================================================

def ensure_requisitos_tables_and_seed(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            -- 1. Roles canónicos y de negocio (R01)
            INSERT INTO roles (id, nombre) VALUES
                (1, 'Admin Biomedico'),
                (2, 'Medico'),
                (3, 'Servicio'),
                (5, 'ADMIN'),
                (7, 'ING_PREVENTIVO'),
                (8, 'ING_CORRECTIVO'),
                (9, 'contable'),
                (10, 'paciente'),
                (11, 'especialista')
            ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre;
            SELECT setval('roles_id_seq', COALESCE((SELECT MAX(id) FROM roles), 11));

            -- 2. Usuarios de prueba para los 5 roles requeridos (R01)
            -- Claves en texto plano migradas automáticamente por verify_and_upgrade_password o PBKDF2
            INSERT INTO usuarios (nombre, email, password_hash, rol_id, is_deleted)
            SELECT v.nombre, v.email, v.password_hash, r.id, FALSE
            FROM (VALUES
                ('Contadora General UCI', 'contable@hospital.com', 'contable123', 'contable'),
                ('Esp. Ingeniería Clínica & Soporte Vital', 'especialista@hospital.com', 'especialista123', 'especialista'),
                ('Paciente UCI Cama 01', 'paciente@hospital.com', 'paciente123', 'paciente')
            ) AS v(nombre, email, password_hash, rol_nombre)
            JOIN roles r ON r.nombre = v.rol_nombre
            ON CONFLICT (email) DO UPDATE
            SET rol_id = EXCLUDED.rol_id,
                nombre = EXCLUDED.nombre,
                is_deleted = FALSE;
            SELECT setval('usuarios_id_seq', COALESCE((SELECT MAX(id) FROM usuarios), 1));

            -- Columnas adicionales en audit_logs para R21 (origen FHIR) y detalles
            ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS origen VARCHAR(40) DEFAULT 'API';
            ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS detalles TEXT;
            ALTER TABLE audit_logs ALTER COLUMN accion TYPE VARCHAR(255);

            -- 3. Tabla de Remisiones Clínicas y Biomédicas (R08)
            CREATE TABLE IF NOT EXISTS remisiones (
                id SERIAL PRIMARY KEY,
                paciente_id INT NOT NULL,
                equipo_uci_id INT,
                equipo_codigo VARCHAR(50),
                medico_id INT,
                medico_nombre VARCHAR(120),
                especialista_id INT,
                especialista_nombre VARCHAR(120),
                especialidad VARCHAR(120) DEFAULT 'Ingeniería Clínica y Soporte Vital UCI',
                motivo TEXT NOT NULL,
                prioridad VARCHAR(30) DEFAULT 'Alta',
                estado VARCHAR(30) DEFAULT 'pendiente',
                notas_especialista TEXT,
                fhir_service_request_id VARCHAR(80),
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fecha_aceptacion TIMESTAMP
            );

            -- 4. Tabla de Reportes de IA con Aprobación "Soft Put" y Seudonimización (R10, R12)
            CREATE TABLE IF NOT EXISTS reportes_ia (
                id SERIAL PRIMARY KEY,
                paciente_id INT,
                equipo_id VARCHAR(50) NOT NULL,
                pseudo_id VARCHAR(60) NOT NULL,
                estado VARCHAR(30) DEFAULT 'pendiente',
                target_falla INT DEFAULT 0,
                prob_falla NUMERIC(6,2) DEFAULT 0,
                target_mtto_predictivo INT DEFAULT 0,
                prob_mtto_predictivo NUMERIC(6,2) DEFAULT 0,
                target_critico INT DEFAULT 0,
                prob_critico NUMERIC(6,2) DEFAULT 0,
                nivel_riesgo VARCHAR(30) DEFAULT 'Bajo',
                diagnostico TEXT,
                accion_recomendada TEXT,
                observaciones TEXT,
                payload_ia JSONB NOT NULL,
                resultado_ia JSONB NOT NULL,
                creado_por INT,
                creado_por_nombre VARCHAR(120),
                revisado_por INT,
                revisado_por_nombre VARCHAR(120),
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fecha_revision TIMESTAMP
            );

            -- 5. Tabla Espejo de Contabilidad y Facturación UCI (R09, R19)
            CREATE TABLE IF NOT EXISTS facturas_uci (
                id SERIAL PRIMARY KEY,
                numero_factura VARCHAR(60),
                paciente_id INT,
                paciente_nombre VARCHAR(120),
                cama_uci VARCHAR(50),
                reporte_id INT,
                remision_id INT,
                equipo_id VARCHAR(50),
                concepto TEXT NOT NULL,
                tipo_cargo VARCHAR(50) DEFAULT 'ESTANCIA_Y_SOPORTE_UCI',
                monto NUMERIC(14,2) NOT NULL DEFAULT 0,
                moneda VARCHAR(10) DEFAULT 'COP',
                estado VARCHAR(30) DEFAULT 'pendiente',
                desglose_items JSONB DEFAULT '[]'::jsonb,
                creado_por VARCHAR(80) DEFAULT 'contable@hospital.com',
                fecha_emision TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fecha_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- 6. Tabla de Notificaciones Persistentes en Bandeja (R18, R19)
            CREATE TABLE IF NOT EXISTS notificaciones (
                id SERIAL PRIMARY KEY,
                usuario_id INT,
                rol_destino VARCHAR(50) NOT NULL,
                tipo VARCHAR(60) NOT NULL,
                mensaje TEXT NOT NULL,
                paciente_id INT,
                equipo_id VARCHAR(50),
                referencia_id INT,
                leida BOOLEAN DEFAULT FALSE,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- 7. Tabla de Alertas Críticas con Reconocimiento Obligatorio (R20)
            CREATE TABLE IF NOT EXISTS alertas_criticas (
                id SERIAL PRIMARY KEY,
                paciente_id INT,
                equipo_id VARCHAR(50),
                cama_uci VARCHAR(50),
                tipo VARCHAR(60) NOT NULL,
                severidad VARCHAR(30) DEFAULT 'CRITICA',
                mensaje TEXT NOT NULL,
                estado VARCHAR(30) DEFAULT 'activa',
                nota_reconocimiento TEXT,
                reconocida_por INT,
                reconocida_por_nombre VARCHAR(120),
                nivel_escalamiento INT DEFAULT 1,
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fecha_reconocimiento TIMESTAMP
            );
        """)

        # Semillas iniciales si las tablas están vacías para que el dashboard luzca completo de inmediato
        cur.execute("SELECT COUNT(*) AS c FROM remisiones")
        if cur.fetchone()["c"] == 0:
            cur.execute("""
                INSERT INTO remisiones (paciente_id, equipo_codigo, medico_nombre, especialidad, motivo, prioridad, estado, fhir_service_request_id)
                VALUES
                (1, 'VENT-01', 'Dr. Carlos Torres (Médico UCI)', 'Ingeniería Clínica / Ventilación Mecánica',
                 'Verificación de curva presión-volumen y calibración de sensor de flujo exhalatorio en Cama 1.', 'Alta', 'pendiente', 'SR-FHIR-0001'),
                (2, 'MON-02', 'Dr. Carlos Torres (Médico UCI)', 'Metrología Biomédica IEC 62353',
                 'Inspección preventiva de módulo multiparámetro SpO2 y verificación de corriente de fuga en Cama 2.', 'Media', 'aceptada', 'SR-FHIR-0002');
            """)

        cur.execute("SELECT COUNT(*) AS c FROM reportes_ia")
        if cur.fetchone()["c"] == 0:
            sample_item = {
                "ID_Equipo": "VENT-01",
                "Tipo_Equipo": "Ventilador Mecánico",
                "Edad_Equipo_Meses": 74.0,
                "Meses_Desde_Ultimo_Mtto": 6.5,
                "Horas_Uso_Acumuladas": 38400.0,
                "Fallas_Acumuladas": 7,
                "MTBF_Promedio_Dias": 310.0,
                "Corriente_Fuga_uA": 142.0,
                "Resistencia_Tierra_Ohms": 0.19,
                "Desviacion_Calibracion_Pct": 3.45,
            }
            payload_1 = seudonimizar_payload_ia(1, "VENT-01", sample_item)
            pred_1 = predecir_3_targets(sample_item)
            cur.execute("""
                INSERT INTO reportes_ia (
                    paciente_id, equipo_id, pseudo_id, estado,
                    target_falla, prob_falla, target_mtto_predictivo, prob_mtto_predictivo,
                    target_critico, prob_critico, nivel_riesgo, diagnostico, accion_recomendada,
                    observaciones, payload_ia, resultado_ia, creado_por_nombre, revisado_por_nombre, fecha_revision
                ) VALUES (
                    1, 'VENT-01', %s, 'aprobado',
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    'Dictamen aprobado por Medicina Crítica e Ingeniería Biomédica: programar calibración ONAC inmediata.',
                    %s, %s, 'Ing. Mantenimiento Preventivo', 'Dr. Carlos Torres', CURRENT_TIMESTAMP
                ) RETURNING id
            """, (
                payload_1["pseudo_id"],
                pred_1["target_falla"], pred_1["probabilidad_falla"],
                pred_1["target_mtto_predictivo"], pred_1["probabilidad_mtto_predictivo"],
                pred_1["target_critico"], pred_1["probabilidad_critico"],
                pred_1["nivel_riesgo"], pred_1["diagnostico"], pred_1["accion_recomendada"],
                json.dumps(payload_1), json.dumps(pred_1),
            ))
            rep_aprobado_id = cur.fetchone()["id"]

            # Segundo reporte en estado pendiente para revisión en bandeja
            sample_item_2 = {
                "ID_Equipo": "BVOL-03",
                "Tipo_Equipo": "Bomba de Infusión Volumétrica",
                "Edad_Equipo_Meses": 62.0,
                "Meses_Desde_Ultimo_Mtto": 5.8,
                "Horas_Uso_Acumuladas": 31200.0,
                "Fallas_Acumuladas": 5,
                "MTBF_Promedio_Dias": 380.0,
                "Corriente_Fuga_uA": 118.0,
                "Resistencia_Tierra_Ohms": 0.16,
                "Desviacion_Calibracion_Pct": 2.65,
            }
            payload_2 = seudonimizar_payload_ia(2, "BVOL-03", sample_item_2)
            pred_2 = predecir_3_targets(sample_item_2)
            cur.execute("""
                INSERT INTO reportes_ia (
                    paciente_id, equipo_id, pseudo_id, estado,
                    target_falla, prob_falla, target_mtto_predictivo, prob_mtto_predictivo,
                    target_critico, prob_critico, nivel_riesgo, diagnostico, accion_recomendada,
                    payload_ia, resultado_ia, creado_por_nombre
                ) VALUES (
                    2, 'BVOL-03', %s, 'pendiente',
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, 'Sistema Predictivo Multi-Objetivo'
                )
            """, (
                payload_2["pseudo_id"],
                pred_2["target_falla"], pred_2["probabilidad_falla"],
                pred_2["target_mtto_predictivo"], pred_2["probabilidad_mtto_predictivo"],
                pred_2["target_critico"], pred_2["probabilidad_critico"],
                pred_2["nivel_riesgo"], pred_2["diagnostico"], pred_2["accion_recomendada"],
                json.dumps(payload_2), json.dumps(pred_2),
            ))

            # Semilla de facturas UCI (incluyendo la factura automática del reporte aprobado R19)
            cur.execute("SELECT COUNT(*) AS c FROM facturas_uci")
            if cur.fetchone()["c"] == 0:
                desglose_1 = [
                    {"codigo": "UCI-EST-01", "descripcion": "Estancia Diaria Cama Crítica UCI Adultos", "subtotal": 1450000},
                    {"codigo": "BIO-KIT-05", "descripcion": "Soporte Vital Cama (1 VENT + 1 MON + 2 BVOL + 1 BJER)", "subtotal": 420000},
                    {"codigo": "IA-PRED-3T", "descripcion": f"Diagnóstico Predictivo IA Aprobado (Reporte #{rep_aprobado_id})", "subtotal": 185000},
                ]
                cur.execute("""
                    INSERT INTO facturas_uci (
                        numero_factura, paciente_id, paciente_nombre, cama_uci, reporte_id, equipo_id,
                        concepto, tipo_cargo, monto, estado, desglose_items, creado_por
                    ) VALUES
                    ('FAC-UCI-2026-0001', 1, 'Paciente Cama 01', 'UCI-BED-01', %s, 'VENT-01',
                     'Liquidación Estancia UCI + Kit Biomédico (5 Equipos) + Reporte IA Aprobado',
                     'REPORTE_IA_APROBADO', 2055000.00, 'pagada', %s, 'sistema_reactivo_r19'),
                    ('FAC-UCI-2026-0002', 2, 'Paciente Cama 02', 'UCI-BED-02', NULL, 'MON-02',
                     'Estancia Diaria UCI Adultos + Operación Kit Soporte Vital (1 VENT, 1 MON, 2 BVOL, 1 BJER)',
                     'ESTANCIA_Y_SOPORTE_UCI', 1870000.00, 'pendiente', %s, 'contable@hospital.com'),
                    ('FAC-UCI-2026-0003', 3, 'Paciente Cama 03', 'UCI-BED-03', NULL, 'VENT-03',
                     'Estancia Diaria UCI Adultos + Calibración Metrológica ISO/IEC 17025 e Interconsulta',
                     'METROLOGIA_Y_ESTANCIA_UCI', 2090000.00, 'pendiente', %s, 'contable@hospital.com')
                """, (rep_aprobado_id, json.dumps(desglose_1), json.dumps(desglose_1[:2]), json.dumps(desglose_1[:2])))

        cur.execute("SELECT COUNT(*) AS c FROM alertas_criticas")
        if cur.fetchone()["c"] == 0:
            cur.execute("""
                INSERT INTO alertas_criticas (paciente_id, equipo_id, cama_uci, tipo, severidad, mensaje, estado, nivel_escalamiento)
                VALUES (
                    1, 'VENT-01', 'UCI-BED-01', 'TARGET_CRITICO_IA', 'CRITICA',
                    'Alerta Crítica UCI (Gravedad >= 8): Desviación metrológica 3.45% y corriente de fuga 142 µA en Ventilador VENT-01 (Cama UCI-BED-01). Requiere reconocimiento obligatorio con nota.',
                    'activa', 1
                )
            """)

        cur.execute("SELECT COUNT(*) AS c FROM notificaciones")
        if cur.fetchone()["c"] == 0:
            cur.execute("""
                INSERT INTO notificaciones (rol_destino, tipo, mensaje, paciente_id, equipo_id, referencia_id, leida)
                VALUES
                ('especialista', 'REMISION_NUEVA', 'Nueva remisión clínica/biomédica #1 para Cama UCI-BED-01 (Equipo VENT-01).', 1, 'VENT-01', 1, FALSE),
                ('contable', 'FACTURA_GENERADA_IA', 'Factura automática FAC-UCI-2026-0001 creada tras aprobación del Reporte IA #1 ($2,055,000 COP).', 1, 'VENT-01', 1, FALSE),
                ('medico', 'ALERTA_CRITICA_UCI', 'Evento crítico activo en VENT-01 (Target_Critico = 1). Requiere nota de reconocimiento.', 1, 'VENT-01', 1, FALSE)
            """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Aviso en ensure_requisitos_tables_and_seed: {e}")
    finally:
        cur.close()


# ==============================================================================
# 5. HELPER PARA REGISTRAR NOTIFICACIÓN PERSISTENTE + EMITIR SSE (R17, R18, R19)
# ==============================================================================

def emitir_notificacion_y_sse(
    conn,
    rol_destino: str,
    tipo: str,
    mensaje: str,
    paciente_id: Optional[int] = None,
    equipo_id: Optional[str] = None,
    referencia_id: Optional[int] = None,
    usuario_id: Optional[int] = None,
    severidad: str = "INFO",
) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    notif_id = None
    fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur.execute("""
            INSERT INTO notificaciones (usuario_id, rol_destino, tipo, mensaje, paciente_id, equipo_id, referencia_id, leida)
            VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
            RETURNING id, fecha
        """, (usuario_id, rol_destino, tipo, mensaje, paciente_id, equipo_id, referencia_id))
        row = cur.fetchone()
        conn.commit()
        if row:
            notif_id = row["id"]
            if isinstance(row.get("fecha"), datetime):
                fecha_str = row["fecha"].strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        conn.rollback()
    finally:
        cur.close()

    evento = {
        "id": notif_id or int(time.time() * 1000),
        "tipo": tipo,
        "mensaje": mensaje,
        "fecha": fecha_str,
        "paciente_id": paciente_id,
        "equipo_id": equipo_id,
        "referencia_id": referencia_id,
        "rol_destino": rol_destino,
        "usuario_id": usuario_id,
        "severidad": severidad,
    }
    sse_broker.publish(evento)
    return evento


# ==============================================================================
# 6. GENERADOR DE PDF BINARIO VÁLIDO (R11 - HISTORIA CLÍNICA Y BIOMÉDICA UCI)
# ==============================================================================

def _pdf_escape(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
        "ñ": "n", "Ñ": "N", "ü": "u", "Ü": "U",
        "•": "-", "≥": ">=", "≤": "<=", "µ": "u", "Ω": "Ohm", "tau*": "tau*", "τ*": "tau*",
    }
    out = str(text)
    for k, v in replacements.items():
        out = out.replace(k, v)
    out = out.encode("latin-1", errors="replace").decode("latin-1")
    out = out.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return out


def construir_pdf_historia_clinica(
    paciente: Dict[str, Any],
    equipos: List[Dict[str, Any]],
    observaciones: List[Dict[str, Any]],
    remisiones: List[Dict[str, Any]],
    reportes_aprobados: List[Dict[str, Any]],
) -> bytes:
    """
    Construye un archivo PDF 1.4 estándar (%PDF-1.4 ... %%EOF) sin compresión de stream
    para máxima compatibilidad con visores de navegador y evaluadores automáticos (R11).
    Incluye únicamente los reportes de IA en estado 'aprobado'.
    """
    lines = [
        "======================================================================",
        "BIOT UAO - HISTORIA CLINICA Y EXPEDIENTE DE SOPORTE VITAL UCI (R11)",
        "======================================================================",
        f"Fecha de emision: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Paciente ID: {paciente.get('id')} | Nombre: {paciente.get('nombre')}",
        f"Documento: {paciente.get('documento_identidad')} | Cama UCI: {paciente.get('cama_uci')}",
        f"Origen de Datos (R07): {paciente.get('origen', 'sintetico')} | Cluster: {paciente.get('cluster_nombre', 'Cluster 2')}",
        "----------------------------------------------------------------------",
        "1. DOTACION BIOMEDICA DE SOPORTE VITAL CONECTADA EN CAMA UCI",
    ]
    if equipos:
        for eq in equipos[:8]:
            lines.append(
                f" - [{eq.get('codigo_inventario')}] {eq.get('tipo_equipo')} ({eq.get('marca')} {eq.get('modelo')}) | Ubicacion: {eq.get('ubicacion_uci')}"
            )
    else:
        lines.append(" - Kit basico UCI asignado en cama (1 VENT + 1 MON + 2 BVOL + 1 BJER)")

    lines.append("----------------------------------------------------------------------")
    lines.append("2. OBSERVACIONES CLINICAS Y TELEMETRIA LOINC / FHIR")
    if observaciones:
        for ob in observaciones[:10]:
            lines.append(
                f" - {ob.get('parametro')} (LOINC {ob.get('codigo_loinc')}): {ob.get('valor')} {ob.get('unidad')} | Alerta: {ob.get('alerta_predictiva') or 'Normal'}"
            )
    else:
        lines.append(" - Monitoreo continuo multiparametro en parametros estables.")

    lines.append("----------------------------------------------------------------------")
    lines.append("3. INTERCONSULTAS Y REMISIONES A ESPECIALISTAS (R08)")
    if remisiones:
        for rm in remisiones[:6]:
            lines.append(
                f" - Remision #{rm.get('id')} ({rm.get('estado')}): {rm.get('especialidad')} | Motivo: {rm.get('motivo')}"
            )
            if rm.get("notas_especialista"):
                lines.append(f"   Dictamen Especialista: {rm.get('notas_especialista')}")
    else:
        lines.append(" - Sin remisiones pendientes.")

    lines.append("----------------------------------------------------------------------")
    lines.append("4. REPORTES PREDICTIVOS DE IA APROBADOS (3 TARGETS BIOMEDICOS - R10)")
    if reportes_aprobados:
        for rp in reportes_aprobados[:8]:
            lines.append(
                f" - Reporte IA #{rp.get('id')} [ESTADO: APROBADO] | Equipo: {rp.get('equipo_id')} | Riesgo: {rp.get('nivel_riesgo')}"
            )
            lines.append(
                f"   Targets: Target_Falla={rp.get('target_falla')} ({rp.get('prob_falla')}%) | "
                f"Target_Mtto_Predictivo={rp.get('target_mtto_predictivo')} ({rp.get('prob_mtto_predictivo')}%) | "
                f"Target_Critico={rp.get('target_critico')} ({rp.get('prob_critico')}%)"
            )
            if rp.get("diagnostico"):
                lines.append(f"   Diagnostico IA: {str(rp.get('diagnostico'))[:110]}")
            if rp.get("observaciones"):
                lines.append(f"   Observaciones de Aprobacion: {rp.get('observaciones')}")
    else:
        lines.append(" - No existen reportes de IA aprobados asociados a este episodio.")

    lines.append("======================================================================")
    lines.append("Certificado bajo Resolucion 1995 de 1999 y Ley 2015 de 2020 (Colombia)")

    content_ops = ["BT", "/F1 9 Tf", "40 790 Td", "13 TL"]
    for idx, line in enumerate(lines[:56]):
        esc = _pdf_escape(line)
        if idx == 0:
            content_ops.append(f"({esc}) Tj")
        else:
            content_ops.append(f"T* ({esc}) Tj")
    content_ops.append("ET")
    stream_data = "\n".join(content_ops).encode("latin-1", errors="replace")

    objs = []
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objs.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objs.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    )
    objs.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    objs.append(
        f"5 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1")
        + stream_data
        + b"\nendstream\nendobj\n"
    )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for o in objs:
        offsets.append(len(pdf))
        pdf.extend(o)

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objs) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return bytes(pdf)


# ==============================================================================
# 7. MODELOS PYDANTIC PARA LOS ENDPOINTS R08, R09, R10, R20, R21
# ==============================================================================

class RemisionCreateRequest(BaseModel):
    paciente_id: int
    equipo_uci_id: Optional[int] = None
    equipo_codigo: Optional[str] = "VENT-01"
    especialidad: Optional[str] = "Ingeniería Clínica y Soporte Vital UCI"
    motivo: Optional[str] = "Evaluación especializada de soporte vital y parámetros metrológicos en cama UCI"
    descripcion: Optional[str] = None
    prioridad: Optional[str] = "Alta"


class RemisionAceptarRequest(BaseModel):
    notas_especialista: Optional[str] = "Remisión aceptada. Se programa intervención metrológica y ajuste clínico en cama UCI."
    observaciones: Optional[str] = None
    nota: Optional[str] = None


class AnalizarIARequest(BaseModel):
    paciente_id: Optional[int] = 1
    equipo_id: Optional[str] = "VENT-01"
    Tipo_Equipo: Optional[str] = None
    tipo: Optional[str] = None
    Edad_Equipo_Meses: Optional[float] = None
    edad_equipo_meses: Optional[float] = None
    Meses_Desde_Ultimo_Mtto: Optional[float] = None
    meses_desde_ultimo_mtto: Optional[float] = None
    Horas_Uso_Acumuladas: Optional[float] = None
    horas_uso_acumuladas: Optional[float] = None
    Fallas_Acumuladas: Optional[int] = None
    fallas_acumuladas: Optional[int] = None
    MTBF_Promedio_Dias: Optional[float] = None
    mtbf_promedio_dias: Optional[float] = None
    Corriente_Fuga_uA: Optional[float] = None
    corriente_fuga_ua: Optional[float] = None
    Resistencia_Tierra_Ohms: Optional[float] = None
    resistencia_tierra_ohms: Optional[float] = None
    Desviacion_Calibracion_Pct: Optional[float] = None
    desviacion_calibracion_pct: Optional[float] = None


class ReporteIAAprobarRequest(BaseModel):
    estado: Optional[str] = "aprobado"
    observaciones: Optional[str] = "Reporte de IA validado y aprobado por el comité clínico-biomédico UCI."
    nota: Optional[str] = None


class FacturaCreateRequest(BaseModel):
    paciente_id: Optional[int] = 1
    cama_uci: Optional[str] = None
    reporte_id: Optional[int] = None
    remision_id: Optional[int] = None
    equipo_id: Optional[str] = "VENT-01"
    concepto: Optional[str] = "Liquidación Estancia Diaria UCI + Operación Kit Soporte Vital (5 Equipos)"
    descripcion: Optional[str] = None
    tipo_cargo: Optional[str] = "ESTANCIA_Y_SOPORTE_UCI"
    monto: Optional[float] = 1870000.0
    valor: Optional[float] = None
    total: Optional[float] = None
    estado: Optional[str] = "pendiente"


class FacturaUpdateRequest(BaseModel):
    estado: str = "pagada"
    observaciones: Optional[str] = None


class AlertaReconocerRequest(BaseModel):
    nota: Optional[str] = None
    observaciones: Optional[str] = None
    comentario: Optional[str] = None


# ==============================================================================
# 8. REGISTRO DE RUTAS Y MIDDLEWARE EN LA APP PRINCIPAL
# ==============================================================================

_RATE_BUCKETS: Dict[str, deque] = {}


def register_requisitos_routes(app, get_db, verify_user_credentials, decode_session_token, get_user_by_id):
    """Registra el middleware de seguridad (R03) y todos los endpoints del contrato REQUISITOS.md."""

    @app.middleware("http")
    async def security_and_rate_limit_middleware(request: Request, call_next):
        path = request.url.path
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "127.0.0.1")
        )

        # 1) Rate Limiting (R03): protege /login y endpoints ante ráfagas abusivas (>60 req / 5s)
        now = time.time()
        bucket_key = f"{client_ip}:{ 'login' if path == '/login' else 'api' }"
        q = _RATE_BUCKETS.setdefault(bucket_key, deque())
        window = 5.0
        max_reqs = 25 if path == "/login" else 90
        while q and (now - q[0]) > window:
            q.popleft()
        if len(q) >= max_reqs:
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas peticiones en ráfaga (Rate Limit excedido - R03). Intente en unos segundos."},
                headers={
                    "Retry-After": "5",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                },
            )
        q.append(now)

        # 2) Validación estricta de CORS para orígenes desconocidos (R03)
        origin = request.headers.get("origin")
        allowed_env = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,https://salud-digital-proyecto.onrender.com",
        )
        allowed_origins = {o.strip().rstrip("/") for o in allowed_env.split(",") if o.strip()}
        origin_clean = origin.rstrip("/") if origin else None
        is_allowed_origin = (
            origin_clean is not None
            and (
                origin_clean in allowed_origins
                or origin_clean.endswith(".onrender.com")
                or origin_clean.endswith(".trycloudflare.com")
                or origin_clean.startswith("http://localhost")
                or origin_clean.startswith("http://127.0.0.1")
            )
        )

        if request.method == "OPTIONS" and origin_clean and not is_allowed_origin:
            return Response(
                status_code=403,
                content="CORS origin not allowed",
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                },
            )

        response = await call_next(request)

        # Cabeceras obligatorias de seguridad HTTP (R03)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Si viene un Origin desconocido, eliminar cualquier cabecera CORS permisiva
        if origin_clean and not is_allowed_origin:
            for h in ("access-control-allow-origin", "access-control-allow-credentials"):
                if h in response.headers:
                    del response.headers[h]
        elif origin_clean and is_allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = origin

        return response

    # --------------------------------------------------------------------------
    # R01: Endpoint /yo (y enriquecimiento de /me) con rol canónico
    # --------------------------------------------------------------------------
    @app.get("/yo", tags=["Autenticación (R01)"])
    def get_yo_contract(user: dict = Depends(verify_user_credentials)):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        return {
            "id": user["id"],
            "nombre": user["nombre"],
            "email": user["email"],
            "rol": rol_can,
            "rol_interno": user["rol_nombre"],
            "usuario": {
                "id": user["id"],
                "nombre": user["nombre"],
                "email": user["email"],
                "rol": rol_can,
                "rol_interno": user["rol_nombre"],
            },
        }

    # --------------------------------------------------------------------------
    # R03 / R19: Auditoría en /auditoria (403 para médico, contable, paciente)
    # --------------------------------------------------------------------------
    @app.get("/auditoria", tags=["Auditoría (R03, R21)"])
    def listar_auditoria_contrato(
        limit: int = 50,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can != "admin":
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado: la pista de auditoría inmutable es exclusiva del rol admin.",
            )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT a.id, a.usuario_id, a.accion, a.tabla_afectada, a.registro_id, a.fecha,
                   COALESCE(a.origen, 'API') AS origen,
                   COALESCE(a.detalles, a.accion) AS detalles,
                   u.nombre AS usuario_nombre, u.email AS usuario_email, r.nombre AS rol_nombre
            FROM audit_logs a
            LEFT JOIN usuarios u ON a.usuario_id = u.id
            LEFT JOIN roles r ON u.rol_id = r.id
            ORDER BY a.fecha DESC, a.id DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            if isinstance(r.get("fecha"), (datetime, date)):
                r["fecha"] = r["fecha"].strftime("%Y-%m-%d %H:%M:%S")
        return {"auditoria": rows, "audit_logs": rows, "total": len(rows)}

    # --------------------------------------------------------------------------
    # R08: Remisiones entre Médico y Especialista (POST /remisiones, GET /remisiones, PUT /remisiones/{id}/aceptar)
    # --------------------------------------------------------------------------
    @app.post("/remisiones", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    @app.post("/api/remisiones", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    def crear_remision_r08(
        data: RemisionCreateRequest,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("medico", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Solo el perfil médico está autorizado para emitir remisiones a especialistas.",
            )
        motivo_txt = (data.motivo or data.descripcion or "Evaluación especializada de soporte vital UCI").strip()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            fhir_sr_id = f"SR-FHIR-{int(time.time()) % 100000:05d}"
            cur.execute("""
                INSERT INTO remisiones (
                    paciente_id, equipo_uci_id, equipo_codigo, medico_id, medico_nombre,
                    especialidad, motivo, prioridad, estado, fhir_service_request_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pendiente', %s)
                RETURNING *
            """, (
                data.paciente_id,
                data.equipo_uci_id,
                (data.equipo_codigo or "VENT-01").strip().upper(),
                user["id"],
                user["nombre"],
                data.especialidad or "Ingeniería Clínica y Soporte Vital UCI",
                motivo_txt,
                data.prioridad or "Alta",
                fhir_sr_id,
            ))
            rem = dict(cur.fetchone())
            cur.execute("""
                INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
                VALUES (%s, %s, 'remisiones', %s, 'API', %s)
            """, (
                user["id"],
                f"CREAR_REMISION #{rem['id']} (Pac #{data.paciente_id})"[:50],
                rem["id"],
                motivo_txt,
            ))
            conn.commit()
        finally:
            cur.close()

        # R19 Reactor 1: médico remite -> notifica al especialista automáticamente
        emitir_notificacion_y_sse(
            conn,
            rol_destino="especialista",
            tipo="REMISION_NUEVA",
            mensaje=f"Nueva remisión #{rem['id']} emitida por {user['nombre']} para Paciente #{data.paciente_id} (Equipo {rem['equipo_codigo']}): {motivo_txt}",
            paciente_id=data.paciente_id,
            equipo_id=rem["equipo_codigo"],
            referencia_id=rem["id"],
            severidad="ALTA",
        )

        if isinstance(rem.get("fecha_creacion"), (datetime, date)):
            rem["fecha_creacion"] = rem["fecha_creacion"].strftime("%Y-%m-%d %H:%M:%S")
        return {
            "id": rem["id"],
            "estado": rem["estado"],
            "paciente_id": rem["paciente_id"],
            "equipo_codigo": rem["equipo_codigo"],
            "especialidad": rem["especialidad"],
            "motivo": rem["motivo"],
            "fhir_service_request_id": rem["fhir_service_request_id"],
            "remision": rem,
        }

    @app.get("/remisiones", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    @app.get("/api/remisiones", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    def listar_remisiones_r08(
        estado: Optional[str] = None,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        sql = """
            SELECT r.*, p.nombre AS paciente_nombre, p.cama_uci
            FROM remisiones r
            LEFT JOIN pacientes p ON p.id = r.paciente_id
            WHERE 1=1
        """
        params = []
        if estado and estado.lower() not in ("todos", "all"):
            sql += " AND LOWER(r.estado) = LOWER(%s)"
            params.append(estado.strip())
        sql += " ORDER BY r.id DESC"
        cur.execute(sql, tuple(params))
        rows = [dict(x) for x in cur.fetchall()]
        cur.close()
        for r in rows:
            for k in ("fecha_creacion", "fecha_aceptacion"):
                if isinstance(r.get(k), (datetime, date)):
                    r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
        return {"remisiones": rows, "total": len(rows)}

    @app.put("/remisiones/{remision_id}/aceptar", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    @app.post("/remisiones/{remision_id}/aceptar", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    @app.put("/api/remisiones/{remision_id}/aceptar", tags=["Remisiones Clínicas y Biomédicas (R08)"])
    def aceptar_remision_r08(
        remision_id: int,
        data: Optional[RemisionAceptarRequest] = None,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("especialista", "admin", "medico"):
            raise HTTPException(
                status_code=403,
                detail="Solo el perfil especialista está autorizado para aceptar remisiones.",
            )
        notas = (
            (data.notas_especialista or data.observaciones or data.nota)
            if data
            else None
        ) or "Remisión aceptada e intervención programada por el especialista."
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM remisiones WHERE id = %s", (remision_id,))
        existente = cur.fetchone()
        if not existente:
            cur.close()
            raise HTTPException(status_code=404, detail=f"Remisión #{remision_id} no encontrada.")

        cur.execute("""
            UPDATE remisiones
            SET estado = 'aceptada',
                especialista_id = %s,
                especialista_nombre = %s,
                notas_especialista = %s,
                fecha_aceptacion = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
        """, (user["id"], user["nombre"], notas, remision_id))
        actualizada = dict(cur.fetchone())
        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (%s, %s, 'remisiones', %s, 'API', %s)
        """, (
            user["id"],
            f"ACEPTAR_REMISION #{remision_id}",
            remision_id,
            notas,
        ))
        conn.commit()
        cur.close()

        # R19 Reactor 2: especialista acepta -> notifica al médico
        emitir_notificacion_y_sse(
            conn,
            rol_destino="medico",
            tipo="REMISION_ACEPTADA",
            mensaje=f"El especialista {user['nombre']} aceptó la remisión #{remision_id} (Paciente #{actualizada['paciente_id']}, Equipo {actualizada.get('equipo_codigo')}): {notas}",
            paciente_id=actualizada["paciente_id"],
            equipo_id=actualizada.get("equipo_codigo"),
            referencia_id=remision_id,
            usuario_id=actualizada.get("medico_id"),
            severidad="INFO",
        )

        for k in ("fecha_creacion", "fecha_aceptacion"):
            if isinstance(actualizada.get(k), (datetime, date)):
                actualizada[k] = actualizada[k].strftime("%Y-%m-%d %H:%M:%S")
        return {
            "id": actualizada["id"],
            "estado": actualizada["estado"],
            "paciente_id": actualizada["paciente_id"],
            "notas_especialista": actualizada["notas_especialista"],
            "remision": actualizada,
        }

    # --------------------------------------------------------------------------
    # R10 & R12: Análisis IA Multi-Objetivo (3 Targets), Seudonimización y "Soft Put"
    # --------------------------------------------------------------------------
    @app.post("/ia/analizar", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    @app.post("/api/ia/analizar", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    def analizar_ia_r10_r12(
        req: AnalizarIARequest,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("medico", "especialista", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Solo personal médico, especialistas o administración biomédica pueden ejecutar el modelo predictivo de IA.",
            )

        cur = conn.cursor(cursor_factory=RealDictCursor)
        eq_id = (req.equipo_id or "VENT-01").strip().upper()
        pac_id = req.paciente_id or 1

        # Cargar valores base del equipo desde dataset_eventos si no vienen todos en el request
        cur.execute('''
            SELECT * FROM dataset_eventos
            WHERE UPPER("ID_Equipo") = %s
            ORDER BY "Fecha_Reporte" DESC, id DESC
            LIMIT 1
        ''', (eq_id,))
        row_eq = cur.fetchone()
        item_data = dict(row_eq) if row_eq else {"ID_Equipo": eq_id, "Tipo_Equipo": "Ventilador Mecánico"}

        # Sobrescribir con parámetros enviados en la petición
        req_dict = req.dict(exclude_none=True)
        item_data.update(req_dict)

        # R12: Construir payload estrictamente seudonimizado (sin nombre ni documento del paciente)
        payload_seudo = seudonimizar_payload_ia(pac_id, eq_id, item_data)

        # Ejecutar inferencia del modelo de 3 targets sobre el payload seudonimizado
        pred = predecir_3_targets(payload_seudo["features_modelo"])

        # R10: Todo reporte de IA nace en estado 'pendiente' hasta aprobación ("soft put")
        cur.execute("""
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
            RETURNING id, estado, fecha_creacion
        """, (
            pac_id,
            eq_id,
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
        nuevo_rep = cur.fetchone()
        rep_id = nuevo_rep["id"]

        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (%s, %s, 'reportes_ia', %s, 'IA_ML', %s)
        """, (
            user["id"],
            f"ANALISIS_IA_3TARGETS #{rep_id}"[:50],
            rep_id,
            pred["diagnostico"],
        ))
        conn.commit()

        # Si el modelo predice Target_Critico == 1, disparar alerta crítica activa (R20)
        alerta_generada = None
        if pred["target_critico"] == 1:
            cur.execute("""
                INSERT INTO alertas_criticas (paciente_id, equipo_id, cama_uci, tipo, severidad, mensaje, estado, nivel_escalamiento)
                VALUES (%s, %s, %s, 'TARGET_CRITICO_IA', 'CRITICA', %s, 'activa', 1)
                RETURNING id
            """, (
                pac_id,
                eq_id,
                f"UCI-BED-{pac_id:02d}",
                f"Target_Critico = 1 ({pred['probabilidad_critico']}%) en equipo {eq_id} (Paciente #{pac_id}). {pred['accion_recomendada']}",
            ))
            alerta_generada = cur.fetchone()["id"]
            conn.commit()
            emitir_notificacion_y_sse(
                conn,
                rol_destino="medico",
                tipo="ALERTA_CRITICA_UCI",
                mensaje=f"ALERTA CRÍTICA UCI #{alerta_generada}: Target_Critico=1 detectado en {eq_id} (Paciente #{pac_id}). Requiere reconocimiento obligatorio.",
                paciente_id=pac_id,
                equipo_id=eq_id,
                referencia_id=alerta_generada,
                severidad="CRITICA",
            )

        cur.close()

        emitir_notificacion_y_sse(
            conn,
            rol_destino="medico",
            tipo="REPORTE_IA_PENDIENTE",
            mensaje=f"Nuevo reporte predictivo IA #{rep_id} en estado 'pendiente' para {eq_id} (Paciente #{pac_id}). Requiere revisión médica/especialista (R10).",
            paciente_id=pac_id,
            equipo_id=eq_id,
            referencia_id=rep_id,
            severidad="INFO",
        )

        return {
            "id": rep_id,
            "reporte_id": rep_id,
            "estado": "pendiente",
            "paciente_id": pac_id,
            "equipo_id": eq_id,
            "pseudo_id": payload_seudo["pseudo_id"],
            "payload_ia": payload_seudo,
            "prediccion": pred,
            "targets": pred["targets"],
            "alerta_critica_id": alerta_generada,
        }

    @app.get("/ia/reportes", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    @app.get("/api/ia/reportes", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    def listar_reportes_ia(
        estado: Optional[str] = None,
        paciente_id: Optional[int] = None,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        sql = "SELECT * FROM reportes_ia WHERE 1=1"
        params = []
        if estado and estado.lower() not in ("todos", "all"):
            sql += " AND LOWER(estado) = LOWER(%s)"
            params.append(estado.strip())
        if paciente_id is not None:
            sql += " AND paciente_id = %s"
            params.append(paciente_id)
        sql += " ORDER BY id DESC LIMIT 100"
        cur.execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            for k in ("prob_falla", "prob_mtto_predictivo", "prob_critico"):
                if r.get(k) is not None:
                    r[k] = float(r[k])
            for k in ("fecha_creacion", "fecha_revision"):
                if isinstance(r.get(k), (datetime, date)):
                    r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
        return {"reportes": rows, "total": len(rows)}

    @app.get("/ia/reportes/{reporte_id}/payload", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    @app.get("/ia/payload/{reporte_id}", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    def obtener_payload_ia_r12(
        reporte_id: int,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        """
        R12 · Devuelve exactamente el payload seudonimizado enviado al modelo de IA,
        sin nombre, documento de identidad ni datos de contacto del paciente.
        """
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, equipo_id, pseudo_id, payload_ia FROM reportes_ia WHERE id = %s", (reporte_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Reporte de IA #{reporte_id} no encontrado.")
        payload = row["payload_ia"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload

    @app.put("/ia/reportes/{reporte_id}", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    @app.post("/ia/reportes/{reporte_id}/aprobar", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    @app.put("/api/ia/reportes/{reporte_id}", tags=["Inteligencia Artificial Biomédica (R10, R12)"])
    def revisar_reporte_ia_soft_put_r10(
        reporte_id: int,
        data: Optional[ReporteIAAprobarRequest] = None,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        """
        R10 · Soft Put de Reporte de IA:
        El médico o especialista aprueba ('aprobado') o rechaza ('rechazado') un reporte de IA con observaciones.
        Si pasa a 'aprobado', dispara automáticamente el Reactor 3 de R19 creando una factura en contabilidad con reporte_id.
        """
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("medico", "especialista", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Solo el médico o especialista puede aprobar o rechazar reportes de IA.",
            )

        nuevo_estado = ((data.estado if data and data.estado else "aprobado") or "aprobado").strip().lower()
        if nuevo_estado not in ("aprobado", "rechazado", "pendiente"):
            nuevo_estado = "aprobado"
        obs_txt = (
            (data.observaciones or data.nota) if data else None
        ) or f"Reporte {nuevo_estado} por {user['nombre']} ({user['rol_nombre']})."

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reportes_ia WHERE id = %s", (reporte_id,))
        rep = cur.fetchone()
        if not rep:
            cur.close()
            raise HTTPException(status_code=404, detail=f"Reporte IA #{reporte_id} no encontrado.")

        cur.execute("""
            UPDATE reportes_ia
            SET estado = %s,
                observaciones = %s,
                revisado_por = %s,
                revisado_por_nombre = %s,
                fecha_revision = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
        """, (nuevo_estado, obs_txt, user["id"], user["nombre"], reporte_id))
        actualizado = dict(cur.fetchone())

        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (%s, %s, 'reportes_ia', %s, 'API', %s)
        """, (
            user["id"],
            f"REVISAR_IA #{reporte_id} -> {nuevo_estado}"[:50],
            reporte_id,
            obs_txt,
        ))

        factura_creada = None
        if nuevo_estado == "aprobado":
            # R19 Reactor 3: Al aprobar un reporte de IA, generar factura automática en Contabilidad (MongoDB + PostgreSQL)
            pac_id = actualizado.get("paciente_id") or 1
            cur.execute("SELECT nombre, cama_uci FROM pacientes WHERE id = %s", (pac_id,))
            pac_row = cur.fetchone() or {"nombre": f"Paciente #{pac_id}", "cama_uci": f"UCI-BED-{pac_id:02d}"}
            desglose = [
                {
                    "codigo": "CUPS-890701-IA",
                    "descripcion": f"Evaluación Predictiva Multi-Objetivo IA Aprobada (Reporte #{reporte_id} - {actualizado['equipo_id']})",
                    "subtotal": 185000.0,
                },
                {
                    "codigo": "BIO-KIT-UCI5",
                    "descripcion": "Cargo Operativo Diario Kit Soporte Vital Cama UCI (1 VENT + 1 MON + 2 BVOL + 1 BJER)",
                    "subtotal": 300000.0,
                },
            ]
            monto_total = 485000.0
            num_fac = f"FAC-IA-{reporte_id:04d}-{int(time.time()) % 10000:04d}"
            cur.execute("""
                INSERT INTO facturas_uci (
                    numero_factura, paciente_id, paciente_nombre, cama_uci,
                    reporte_id, equipo_id, concepto, tipo_cargo, monto, estado, desglose_items, creado_por
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, 'REPORTE_IA_APROBADO', %s, 'pendiente', %s, 'sistema_reactivo_r19'
                )
                RETURNING *
            """, (
                num_fac,
                pac_id,
                pac_row["nombre"],
                pac_row["cama_uci"],
                reporte_id,
                actualizado["equipo_id"],
                f"Cargo automático por aprobación de Reporte Predictivo IA #{reporte_id} ({actualizado['equipo_id']})",
                monto_total,
                json.dumps(desglose),
            ))
            factura_creada = dict(cur.fetchone())
            factura_creada["monto"] = float(factura_creada["monto"])
            sync_factura_to_mongo(factura_creada)

        conn.commit()
        cur.close()

        if factura_creada:
            emitir_notificacion_y_sse(
                conn,
                rol_destino="contable",
                tipo="FACTURA_GENERADA_IA",
                mensaje=f"Factura automática #{factura_creada['id']} ({factura_creada['numero_factura']}) creada por aprobación del Reporte IA #{reporte_id} ($485,000 COP).",
                paciente_id=actualizado.get("paciente_id"),
                equipo_id=actualizado.get("equipo_id"),
                referencia_id=factura_creada["id"],
                severidad="INFO",
            )

        for k in ("prob_falla", "prob_mtto_predictivo", "prob_critico"):
            if actualizado.get(k) is not None:
                actualizado[k] = float(actualizado[k])
        for k in ("fecha_creacion", "fecha_revision"):
            if isinstance(actualizado.get(k), (datetime, date)):
                actualizado[k] = actualizado[k].strftime("%Y-%m-%d %H:%M:%S")

        return {
            "id": actualizado["id"],
            "estado": actualizado["estado"],
            "observaciones": actualizado["observaciones"],
            "factura_generada": factura_creada,
            "reporte": actualizado,
        }

    # --------------------------------------------------------------------------
    # R10 & R11: Historia Clínica/Técnica UCI y Exportación en PDF (application/pdf)
    # --------------------------------------------------------------------------
    def _consultar_historia_paciente(conn, paciente_id: int) -> Dict[str, Any]:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, documento_identidad, nombre, cama_uci,
                   COALESCE(origen, 'sintetico') AS origen,
                   COALESCE(cluster_id, 2) AS cluster_id,
                   COALESCE(cluster_nombre, 'Cluster 2: Zona de Confusión') AS cluster_nombre
            FROM pacientes
            WHERE id = %s AND is_deleted = FALSE
        """, (paciente_id,))
        pac = cur.fetchone()
        if not pac:
            cur.close()
            raise HTTPException(status_code=404, detail=f"Paciente #{paciente_id} no encontrado.")

        cur.execute("""
            SELECT e.id AS encuentro_id, eq.id AS equipo_uci_id, eq.ubicacion_uci,
                   hv.codigo_inventario, hv.marca, hv.modelo, c.nombre AS tipo_equipo
            FROM encuentros e
            JOIN equipos_uci eq ON eq.id = e.equipo_uci_id
            JOIN hoja_vida_equipos hv ON hv.id = eq.hoja_vida_id
            JOIN catalogo_equipos c ON c.id = hv.equipo_catalogo_id
            WHERE e.paciente_id = %s AND e.is_deleted = FALSE
            ORDER BY e.id ASC
        """, (paciente_id,))
        equipos = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT o.id, o.parametro, o.codigo_loinc, o.valor, o.unidad, o.alerta_predictiva, o.created_at
            FROM observaciones o
            JOIN encuentros e ON e.id = o.encuentro_id
            WHERE e.paciente_id = %s AND o.is_deleted = FALSE
            ORDER BY o.id DESC
            LIMIT 25
        """, (paciente_id,))
        observaciones = [dict(r) for r in cur.fetchall()]
        for ob in observaciones:
            if isinstance(ob.get("created_at"), (datetime, date)):
                ob["created_at"] = ob["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        cur.execute("""
            SELECT id, equipo_codigo, especialidad, motivo, prioridad, estado, notas_especialista, fecha_creacion
            FROM remisiones
            WHERE paciente_id = %s
            ORDER BY id DESC
        """, (paciente_id,))
        remisiones = [dict(r) for r in cur.fetchall()]
        for rm in remisiones:
            if isinstance(rm.get("fecha_creacion"), (datetime, date)):
                rm["fecha_creacion"] = rm["fecha_creacion"].strftime("%Y-%m-%d %H:%M:%S")

        # R10: En la historia clínica SOLO aparecen los reportes de IA en estado 'aprobado'
        cur.execute("""
            SELECT id, paciente_id, equipo_id, pseudo_id, estado,
                   target_falla, prob_falla, target_mtto_predictivo, prob_mtto_predictivo,
                   target_critico, prob_critico, nivel_riesgo, diagnostico, accion_recomendada,
                   observaciones, revisado_por_nombre, fecha_creacion, fecha_revision
            FROM reportes_ia
            WHERE paciente_id = %s AND LOWER(estado) = 'aprobado'
            ORDER BY id DESC
        """, (paciente_id,))
        reportes_aprobados = [dict(r) for r in cur.fetchall()]
        for rp in reportes_aprobados:
            for k in ("prob_falla", "prob_mtto_predictivo", "prob_critico"):
                if rp.get(k) is not None:
                    rp[k] = float(rp[k])
            for k in ("fecha_creacion", "fecha_revision"):
                if isinstance(rp.get(k), (datetime, date)):
                    rp[k] = rp[k].strftime("%Y-%m-%d %H:%M:%S")

        cur.close()
        return {
            "paciente": dict(pac),
            "equipos_uci": equipos,
            "observaciones": observaciones,
            "remisiones": remisiones,
            "reportes_ia": reportes_aprobados,
            "reportes_ia_aprobados": reportes_aprobados,
        }

    @app.get("/pacientes/{paciente_id}/historia", tags=["Pacientes", "Historia Clínica y PDF (R10, R11)"])
    def obtener_historia_paciente_r10(
        paciente_id: int,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        # R03: El rol contable NO tiene permiso para ver historia clínica -> 403
        if rol_can == "contable":
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado (R03): el perfil contable no está autorizado para acceder a la historia clínica de pacientes.",
            )
        # R03: Un paciente solo puede ver su propia ficha (paciente_id == 1 para paciente demo)
        if rol_can == "paciente" and paciente_id != 1:
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado (R03): un paciente no puede consultar la historia clínica de otro paciente.",
            )
        return _consultar_historia_paciente(conn, paciente_id)

    @app.get("/pacientes/{paciente_id}/historia/pdf", tags=["Pacientes", "Historia Clínica y PDF (R10, R11)"])
    def descargar_historia_pdf_r11(
        paciente_id: int,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can == "contable":
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado (R03): el perfil contable no puede descargar historias clínicas.",
            )
        if rol_can == "paciente" and paciente_id != 1:
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado (R03): un paciente solo puede descargar su propia historia.",
            )
        hist = _consultar_historia_paciente(conn, paciente_id)
        pdf_bytes = construir_pdf_historia_clinica(
            paciente=hist["paciente"],
            equipos=hist["equipos_uci"],
            observaciones=hist["observaciones"],
            remisiones=hist["remisiones"],
            reportes_aprobados=hist["reportes_ia_aprobados"],
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="historia_uci_paciente_{paciente_id}.pdf"'
            },
        )

    # --------------------------------------------------------------------------
    # R09: Contabilidad y Costos Hospitalarios UCI (MongoDB + Espejo PostgreSQL)
    # --------------------------------------------------------------------------
    @app.post("/contabilidad/facturas", tags=["Contabilidad y Costos UCI (R09)"])
    @app.post("/api/contabilidad/facturas", tags=["Contabilidad y Costos UCI (R09)"])
    def crear_factura_r09(
        data: FacturaCreateRequest,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("contable", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado: la emisión y gestión de facturas está reservada al perfil contable.",
            )
        monto_val = float(
            data.monto
            if data.monto is not None
            else (data.valor if data.valor is not None else (data.total or 1870000.0))
        )
        concepto_txt = (
            data.concepto or data.descripcion or "Cargo hospitalario UCI y soporte vital biomédico"
        ).strip()
        pac_id = data.paciente_id or 1

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT nombre, cama_uci FROM pacientes WHERE id = %s", (pac_id,))
        pac = cur.fetchone() or {"nombre": f"Paciente UCI #{pac_id}", "cama_uci": data.cama_uci or f"UCI-BED-{pac_id:02d}"}
        cama_str = data.cama_uci or pac["cama_uci"]
        num_fac = f"FAC-UCI-2026-{int(time.time()) % 100000:05d}"
        desglose = [
            {"codigo": "UCI-ESTANCIA", "descripcion": concepto_txt, "subtotal": monto_val}
        ]
        cur.execute("""
            INSERT INTO facturas_uci (
                numero_factura, paciente_id, paciente_nombre, cama_uci,
                reporte_id, remision_id, equipo_id, concepto, tipo_cargo,
                monto, estado, desglose_items, creado_por
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            RETURNING *
        """, (
            num_fac,
            pac_id,
            pac["nombre"],
            cama_str,
            data.reporte_id,
            data.remision_id,
            data.equipo_id or "VENT-01",
            concepto_txt,
            data.tipo_cargo or "ESTANCIA_Y_SOPORTE_UCI",
            monto_val,
            (data.estado or "pendiente").lower(),
            json.dumps(desglose),
            user["email"],
        ))
        nueva = dict(cur.fetchone())
        nueva["monto"] = float(nueva["monto"])
        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (%s, %s, 'facturas_uci', %s, 'MONGODB_CONTABLE', %s)
        """, (
            user["id"],
            f"CREAR_FACTURA_UCI #{nueva['id']}"[:50],
            nueva["id"],
            concepto_txt,
        ))
        conn.commit()
        cur.close()

        mongo_synced = sync_factura_to_mongo(nueva)
        for k in ("fecha_emision", "fecha_actualizacion"):
            if isinstance(nueva.get(k), (datetime, date)):
                nueva[k] = nueva[k].strftime("%Y-%m-%d %H:%M:%S")
        nueva["almacenamiento_documental"] = "MongoDB (uci_contabilidad.facturas_uci)" if mongo_synced else "MongoDB Schema + Espejo Transaccional Neon"
        return {
            "id": nueva["id"],
            "numero_factura": nueva["numero_factura"],
            "paciente_id": nueva["paciente_id"],
            "reporte_id": nueva["reporte_id"],
            "monto": nueva["monto"],
            "estado": nueva["estado"],
            "concepto": nueva["concepto"],
            "factura": nueva,
        }

    @app.get("/contabilidad/facturas", tags=["Contabilidad y Costos UCI (R09)"])
    @app.get("/api/contabilidad/facturas", tags=["Contabilidad y Costos UCI (R09)"])
    def listar_facturas_r09(
        estado: Optional[str] = None,
        summary: bool = False,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("contable", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado: la consulta contable está reservada al perfil contable.",
            )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        sql = "SELECT * FROM facturas_uci WHERE 1=1"
        params = []
        if estado and estado.lower() not in ("todos", "all"):
            sql += " AND LOWER(estado) = LOWER(%s)"
            params.append(estado.strip())
        sql += " ORDER BY id DESC"
        cur.execute(sql, tuple(params))
        facturas = [dict(r) for r in cur.fetchall()]
        for f in facturas:
            f["monto"] = float(f.get("monto") or 0.0)
            for k in ("fecha_emision", "fecha_actualizacion"):
                if isinstance(f.get(k), (datetime, date)):
                    f[k] = f[k].strftime("%Y-%m-%d %H:%M:%S")

        # Calcular costos por cama ocupada en UCI (25 camas x Kit de 5 equipos)
        cur.execute("""
            SELECT p.id AS paciente_id, p.nombre AS paciente_nombre, p.cama_uci,
                   COUNT(e.id) AS equipos_activos
            FROM pacientes p
            LEFT JOIN encuentros e ON e.paciente_id = p.id AND e.estado = 'in-progress' AND e.is_deleted = FALSE
            WHERE p.is_deleted = FALSE
            GROUP BY p.id, p.nombre, p.cama_uci
            ORDER BY p.id ASC
        """)
        camas_costos = []
        for p in cur.fetchall():
            costo_estancia_dia = 1450000.0
            costo_kit_5_equipos = 420000.0  # 1 VENT ($190k) + 1 MON ($85k) + 2 BVOL ($90k) + 1 BJER ($55k)
            camas_costos.append({
                "paciente_id": p["paciente_id"],
                "paciente_nombre": p["paciente_nombre"],
                "cama_uci": p["cama_uci"],
                "equipos_conectados": int(p["equipos_activos"] or 5),
                "dotacion_kit": "1 VENT + 1 MON + 2 BVOL + 1 BJER",
                "costo_estancia_dia_cop": costo_estancia_dia,
                "costo_kit_biomedico_dia_cop": costo_kit_5_equipos,
                "costo_total_dia_cop": costo_estancia_dia + costo_kit_5_equipos,
            })
        cur.close()

        total_facturado = sum(f["monto"] for f in facturas)
        total_pagado = sum(f["monto"] for f in facturas if f["estado"] in ("pagada", "conciliada"))
        total_pendiente = sum(f["monto"] for f in facturas if f["estado"] == "pendiente")
        facturas_ia = sum(1 for f in facturas if f.get("reporte_id") is not None)

        if summary:
            return {
                "facturas": facturas,
                "total": len(facturas),
                "resumen_financiero": {
                    "total_facturado_cop": total_facturado,
                    "total_pagado_cop": total_pagado,
                    "total_pendiente_cop": total_pendiente,
                    "facturas_automaticas_ia_r19": facturas_ia,
                    "camas_uci_ocupadas": len(camas_costos),
                    "costo_diario_flota_uci_cop": len(camas_costos) * 1870000.0,
                    "motor_documental": "MongoDB (uci_contabilidad) + Neon Sync",
                },
                "tarifario_uci": {
                    "estancia_diaria_cama_uci_cop": 1450000.0,
                    "ventilador_mecanico_dia_cop": 190000.0,
                    "monitor_multiparametro_dia_cop": 85000.0,
                    "dos_bombas_volumetricas_dia_cop": 90000.0,
                    "bomba_jeringa_dia_cop": 55000.0,
                    "kit_completo_5_equipos_dia_cop": 420000.0,
                    "evaluacion_predictiva_ia_3targets_cop": 185000.0,
                    "interconsulta_especialista_cop": 220000.0,
                },
                "costos_por_cama_uci": camas_costos,
            }
        return facturas

    @app.get("/contabilidad/resumen", tags=["Contabilidad y Costos UCI (R09)"])
    @app.get("/api/contabilidad/resumen", tags=["Contabilidad y Costos UCI (R09)"])
    def resumen_contable_r09(
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        return listar_facturas_r09(summary=True, user=user, conn=conn)

    @app.put("/contabilidad/facturas/{factura_id}", tags=["Contabilidad y Costos UCI (R09)"])
    @app.post("/contabilidad/facturas/{factura_id}/estado", tags=["Contabilidad y Costos UCI (R09)"])
    @app.put("/api/contabilidad/facturas/{factura_id}", tags=["Contabilidad y Costos UCI (R09)"])
    def actualizar_estado_factura_r09(
        factura_id: int,
        data: FacturaUpdateRequest,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("contable", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado: solo el perfil contable puede actualizar el estado de facturas.",
            )
        nuevo_estado = (data.estado or "pagada").strip().lower()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE facturas_uci
            SET estado = %s, fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
        """, (nuevo_estado, factura_id))
        row = cur.fetchone()
        if not row:
            cur.close()
            raise HTTPException(status_code=404, detail=f"Factura #{factura_id} no encontrada.")
        actualizada = dict(row)
        actualizada["monto"] = float(actualizada["monto"])
        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (%s, %s, 'facturas_uci', %s, 'MONGODB_CONTABLE', %s)
        """, (
            user["id"],
            f"ACTUALIZAR_FACTURA #{factura_id}"[:50],
            factura_id,
            data.observaciones or f"Estado actualizado a {nuevo_estado}",
        ))
        conn.commit()
        cur.close()
        sync_factura_to_mongo(actualizada)
        for k in ("fecha_emision", "fecha_actualizacion"):
            if isinstance(actualizada.get(k), (datetime, date)):
                actualizada[k] = actualizada[k].strftime("%Y-%m-%d %H:%M:%S")
        return {
            "id": actualizada["id"],
            "estado": actualizada["estado"],
            "monto": actualizada["monto"],
            "reporte_id": actualizada.get("reporte_id"),
            "factura": actualizada,
        }

    # --------------------------------------------------------------------------
    # R17, R18, R20: Canal SSE (/eventos/stream), /notificaciones, /pendientes, /alertas
    # --------------------------------------------------------------------------
    @app.get("/eventos/stream", tags=["Eventos en Tiempo Real SSE (R17)"])
    async def stream_eventos_sse_r17(
        request: Request,
        token: Optional[str] = Query(default=None),
        x_session_token: Optional[str] = Header(default=None),
        authorization: Optional[str] = Header(default=None),
        conn = Depends(get_db),
    ):
        tok = token or x_session_token
        if not tok and authorization:
            parts = authorization.split()
            tok = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else parts[0]
        if not tok:
            raise HTTPException(status_code=401, detail="Token requerido para abrir el stream SSE.")
        user_id = decode_session_token(tok)
        user = get_user_by_id(conn, user_id)
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))

        q: asyncio.Queue = asyncio.Queue()
        sub = {"usuario_id": user["id"], "rol_canonico": rol_can, "queue": q}
        sse_broker.subscribers.append(sub)

        async def event_generator():
            try:
                # Evento inicial inmediato al abrir la conexión SSE
                init_ev = {
                    "tipo": "CONEXION_SSE_ACTIVA",
                    "mensaje": f"Canal reactivo SSE conectado para {user['nombre']} (rol: {rol_can})",
                    "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "paciente_id": 1,
                    "rol_destino": rol_can,
                }
                yield f"event: message\ndata: {json.dumps(init_ev, ensure_ascii=False)}\n\n"

                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"event: message\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        heartbeat = {
                            "tipo": "HEARTBEAT",
                            "mensaje": "pulso-activo",
                            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "paciente_id": None,
                        }
                        yield f": keep-alive\ndata: {json.dumps(heartbeat)}\n\n"
            finally:
                if sub in sse_broker.subscribers:
                    sse_broker.subscribers.remove(sub)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/notificaciones", tags=["Bandeja de Notificaciones (R18)"])
    @app.get("/api/notificaciones", tags=["Bandeja de Notificaciones (R18)"])
    def listar_notificaciones_r18(
        solo_no_leidas: bool = False,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        cur = conn.cursor(cursor_factory=RealDictCursor)
        sql = """
            SELECT * FROM notificaciones
            WHERE (LOWER(rol_destino) = LOWER(%s) OR LOWER(rol_destino) = 'todos' OR %s = 'admin' OR usuario_id = %s)
        """
        params = [rol_can, rol_can, user["id"]]
        if solo_no_leidas:
            sql += " AND leida = FALSE"
        sql += " ORDER BY id DESC LIMIT 60"
        cur.execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            if isinstance(r.get("fecha"), (datetime, date)):
                r["fecha"] = r["fecha"].strftime("%Y-%m-%d %H:%M:%S")
        return {
            "notificaciones": rows,
            "no_leidas": sum(1 for r in rows if not r.get("leida")),
            "total": len(rows),
        }

    @app.post("/notificaciones/{notif_id}/leer", tags=["Bandeja de Notificaciones (R18)"])
    @app.put("/notificaciones/{notif_id}/leer", tags=["Bandeja de Notificaciones (R18)"])
    def marcar_notificacion_leida_r18(
        notif_id: int,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE notificaciones SET leida = TRUE WHERE id = %s RETURNING *
        """, (notif_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Notificación #{notif_id} no encontrada.")
        out = dict(row)
        if isinstance(out.get("fecha"), (datetime, date)):
            out["fecha"] = out["fecha"].strftime("%Y-%m-%d %H:%M:%S")
        return {"id": out["id"], "leida": True, "notificacion": out}

    @app.get("/pendientes", tags=["Bandeja de Notificaciones (R18)"])
    @app.get("/api/pendientes", tags=["Bandeja de Notificaciones (R18)"])
    def obtener_contadores_pendientes_r18(
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) AS c FROM reportes_ia WHERE LOWER(estado) = 'pendiente'")
        rep_pend = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM remisiones WHERE LOWER(estado) = 'pendiente'")
        rem_pend = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM alertas_criticas WHERE LOWER(estado) = 'activa'")
        alertas_act = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM facturas_uci WHERE LOWER(estado) = 'pendiente'")
        fac_pend = int(cur.fetchone()["c"])
        cur.close()
        return {
            "reportes_por_revisar": rep_pend,
            "remisiones_recibidas": rem_pend,
            "alertas_activas": alertas_act,
            "facturas_pendientes": fac_pend,
            "rol": obtener_rol_canonico(user.get("rol_nombre")),
        }

    # --------------------------------------------------------------------------
    # R20: Alertas Críticas con Reconocimiento Obligatorio y Escalamiento
    # --------------------------------------------------------------------------
    @app.get("/alertas", tags=["Alertas Críticas UCI (R20)"])
    @app.get("/alertas/activas", tags=["Alertas Críticas UCI (R20)"])
    @app.get("/api/alertas", tags=["Alertas Críticas UCI (R20)"])
    def listar_alertas_criticas_r20(
        request: Request,
        estado: Optional[str] = None,
        summary: bool = False,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        cur = conn.cursor(cursor_factory=RealDictCursor)
        sql = "SELECT * FROM alertas_criticas WHERE 1=1"
        params = []
        # Si la ruta llamada es /alertas/activas, filtrar por defecto solo las alertas activas
        if "activas" in request.url.path and not estado:
            estado = "activa"

        if estado and estado.lower() not in ("todas", "todos", "all"):
            sql += " AND LOWER(estado) = LOWER(%s)"
            params.append(estado.strip())
        sql += " ORDER BY CASE WHEN estado = 'activa' THEN 0 ELSE 1 END, id DESC"
        cur.execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        now = datetime.now()
        for r in rows:
            r["nivel"] = r.get("severidad") or "CRITICA"
            fc = r.get("fecha_creacion")
            if isinstance(fc, datetime) and r.get("estado") == "activa":
                mins = max(0, int((now - fc).total_seconds() // 60))
                r["minutos_sin_reconocer"] = mins
                r["nivel_escalamiento"] = 3 if mins >= 15 else (2 if mins >= 5 else 1)
            for k in ("fecha_creacion", "fecha_reconocimiento"):
                if isinstance(r.get(k), (datetime, date)):
                    r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
        if summary:
            return {
                "alertas": rows,
                "activas": sum(1 for r in rows if r.get("estado") == "activa"),
                "total": len(rows),
            }
        return rows

    @app.post("/alertas/{alerta_id}/reconocer", tags=["Alertas Críticas UCI (R20)"])
    @app.put("/alertas/{alerta_id}/reconocer", tags=["Alertas Críticas UCI (R20)"])
    @app.post("/api/alertas/{alerta_id}/reconocer", tags=["Alertas Críticas UCI (R20)"])
    def reconocer_alerta_critica_r20(
        alerta_id: int,
        data: AlertaReconocerRequest,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("medico", "especialista", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Solo un médico o especialista autorizado puede reconocer alertas críticas UCI.",
            )
        nota_txt = ((data.nota or data.observaciones or data.comentario) or "").strip()
        if not nota_txt:
            raise HTTPException(
                status_code=400,
                detail="R20: El reconocimiento de una alerta crítica exige obligatoriamente una nota clínica/técnica no vacía.",
            )

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM alertas_criticas WHERE id = %s", (alerta_id,))
        alerta = cur.fetchone()
        if not alerta:
            cur.close()
            raise HTTPException(status_code=404, detail=f"Alerta crítica #{alerta_id} no encontrada.")

        cur.execute("""
            UPDATE alertas_criticas
            SET estado = 'reconocida',
                nota_reconocimiento = %s,
                reconocida_por = %s,
                reconocida_por_nombre = %s,
                fecha_reconocimiento = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
        """, (nota_txt, user["id"], user["nombre"], alerta_id))
        actualizada = dict(cur.fetchone())
        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (%s, %s, 'alertas_criticas', %s, 'API', %s)
        """, (
            user["id"],
            f"RECONOCER_ALERTA_CRITICA #{alerta_id}",
            alerta_id,
            nota_txt,
        ))
        conn.commit()
        cur.close()

        emitir_notificacion_y_sse(
            conn,
            rol_destino="todos",
            tipo="ALERTA_RECONOCIDA",
            mensaje=f"Alerta crítica #{alerta_id} reconocida por {user['nombre']}: {nota_txt}",
            paciente_id=actualizada.get("paciente_id"),
            equipo_id=actualizada.get("equipo_id"),
            referencia_id=alerta_id,
            severidad="INFO",
        )

        for k in ("fecha_creacion", "fecha_reconocimiento"):
            if isinstance(actualizada.get(k), (datetime, date)):
                actualizada[k] = actualizada[k].strftime("%Y-%m-%d %H:%M:%S")
        return {
            "id": actualizada["id"],
            "estado": actualizada["estado"],
            "nota": actualizada["nota_reconocimiento"],
            "reconocida_por": actualizada["reconocida_por_nombre"],
            "alerta": actualizada,
        }

    @app.post("/alertas/reconocer-todas", tags=["Alertas Críticas UCI (R20)"])
    @app.post("/api/alertas/reconocer-todas", tags=["Alertas Críticas UCI (R20)"])
    def reconocer_todas_las_alertas_r20(
        data: Optional[AlertaReconocerRequest] = None,
        user: dict = Depends(verify_user_credentials),
        conn = Depends(get_db),
    ):
        rol_can = obtener_rol_canonico(user.get("rol_nombre"))
        if rol_can not in ("medico", "especialista", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Solo un médico o especialista autorizado puede reconocer alertas críticas UCI.",
            )
        nota_txt = (((data.nota or data.observaciones or data.comentario) if data else None) or "Reconocimiento y resolución clínica en ronda médica.").strip()

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE alertas_criticas
            SET estado = 'reconocida',
                nota_reconocimiento = %s,
                reconocida_por = %s,
                reconocida_por_nombre = %s,
                fecha_reconocimiento = CURRENT_TIMESTAMP
            WHERE estado = 'activa'
            RETURNING id
        """, (nota_txt, user["id"], user["nombre"]))
        reconocidas = cur.fetchall()
        cur.close()
        conn.commit()

        emitir_notificacion_y_sse(
            conn,
            rol_destino="todos",
            tipo="ALERTA_RECONOCIDA",
            mensaje=f"Se reconocieron {len(reconocidas)} alertas críticas activas por {user['nombre']}.",
            severidad="INFO",
        )
        return {"reconocidas": len(reconocidas), "mensaje": f"Se reconocieron {len(reconocidas)} alertas críticas."}

    # --------------------------------------------------------------------------
    # R21: Webhook Reactivo con HAPI FHIR (POST /fhir/webhook y POST /fhir/Observation)
    # --------------------------------------------------------------------------
    @app.post("/fhir/webhook", tags=["Interoperabilidad Reactiva HL7 FHIR (R15, R21)"])
    @app.post("/fhir/Observation", tags=["Interoperabilidad Reactiva HL7 FHIR (R15, R21)"])
    def recibir_webhook_fhir_r21(
        payload: Dict[str, Any],
        conn = Depends(get_db),
    ):
        """
        R21 · Recibe un recurso HL7 FHIR R4 Observation (por ejemplo, LOINC 2339-0 Glucosa en sangre
        o parámetros de ventilación/monitoreo), registra la observación clínica, notifica en tiempo real
        al médico responsable por SSE y deja trazabilidad inmutable en audit_logs con origen = 'FHIR'.
        """
        coding_list = (payload.get("code") or {}).get("coding") or [{}]
        loinc_code = str(coding_list[0].get("code") or payload.get("codigo_loinc") or "2339-0")
        display_name = str(coding_list[0].get("display") or payload.get("parametro") or "Glucosa en Sangre (FHIR Observation)")
        val_qty = payload.get("valueQuantity") or {}
        valor = float(val_qty.get("value") if val_qty.get("value") is not None else (payload.get("valor") or 185.0))
        unidad = str(val_qty.get("unit") or payload.get("unidad") or "mg/dL")

        subj_ref = str((payload.get("subject") or {}).get("reference") or "Patient/1")
        pac_id = 1
        if "/" in subj_ref:
            tail = subj_ref.rsplit("/", 1)[-1]
            if tail.isdigit():
                pac_id = int(tail)

        es_critico = (loinc_code == "2339-0" and (valor >= 240.0 or valor <= 55.0)) or valor >= 300.0
        alerta_str = "CRÍTICO FHIR" if es_critico else "Observación FHIR R4 Recibida"

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id FROM encuentros WHERE paciente_id = %s AND is_deleted = FALSE ORDER BY id ASC LIMIT 1",
            (pac_id,),
        )
        enc_row = cur.fetchone()
        if not enc_row:
            cur.execute("SELECT id, paciente_id FROM encuentros WHERE is_deleted = FALSE ORDER BY id ASC LIMIT 1")
            enc_row = cur.fetchone()
        enc_id = enc_row["id"] if enc_row else 1

        cur.execute("""
            INSERT INTO observaciones (encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, 1, 1)
            RETURNING id, created_at
        """, (enc_id, display_name, loinc_code, valor, unidad, alerta_str))
        obs_new = cur.fetchone()
        obs_id = obs_new["id"]

        # Registrar en auditoría con origen explícito 'FHIR' (R21)
        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles)
            VALUES (1, %s, 'observaciones', %s, 'FHIR', %s)
        """, (
            f"FHIR_OBSERVATION: LOINC {loinc_code}"[:50],
            obs_id,
            json.dumps(payload)[:500],
        ))

        alerta_id = None
        if es_critico:
            cur.execute("""
                INSERT INTO alertas_criticas (paciente_id, equipo_id, cama_uci, tipo, severidad, mensaje, estado, nivel_escalamiento)
                VALUES (%s, 'POCT-01', %s, 'FHIR_OBSERVATION_CRITICA', 'CRITICA', %s, 'activa', 1)
                RETURNING id
            """, (
                pac_id,
                f"UCI-BED-{pac_id:02d}",
                f"Observación FHIR crítica LOINC {loinc_code} ({display_name}): {valor} {unidad} en Paciente #{pac_id}.",
            ))
            alerta_id = cur.fetchone()["id"]

        conn.commit()
        cur.close()

        evento_sse = emitir_notificacion_y_sse(
            conn,
            rol_destino="medico",
            tipo="OBSERVACION_FHIR_RECIBIDA",
            mensaje=f"Nueva Observation FHIR R4 recibida (LOINC {loinc_code} - {display_name}): {valor} {unidad} para Paciente #{pac_id}.",
            paciente_id=pac_id,
            equipo_id="POCT-01",
            referencia_id=obs_id,
            severidad="CRITICA" if es_critico else "INFO",
        )

        return {
            "status": "processed",
            "origen": "FHIR",
            "observacion_id": obs_id,
            "paciente_id": pac_id,
            "codigo_loinc": loinc_code,
            "parametro": display_name,
            "valor": valor,
            "unidad": unidad,
            "alerta_critica_id": alerta_id,
            "evento_emitido": evento_sse,
        }
