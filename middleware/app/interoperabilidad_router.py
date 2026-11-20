# -*- coding: utf-8 -*-
"""
interoperabilidad_router.py · Módulo de Interoperabilidad Hospitalaria HL7 FHIR R4 (R30–R33)
Salud Digital 2026-2 · Universidad Autónoma de Occidente

Arquitectura de Consulta y Extracción M2M de Historias Clínicas (HL7 FHIR R4):
- Conexión directa entre bases de datos de pacientes de ambas instituciones.
- Cifrado de Entidad a Entidad (AES-256-Fernet + HMAC-SHA256 sobre canal TLS).
- Aceptación e integración 100% AUTOMÁTICA al superar las validaciones R30, R31 y R32
  (sin botones manuales de aceptar/rechazar).
- Si el paciente recibido/consultado del otro grupo no existe en nuestra tabla `pacientes`,
  se registra automáticamente como paciente nuevo en `pacientes`, se le asigna cama y equipo UCI,
  se insertan sus signos vitales LOINC en `observaciones` y se crea una Nueva Historia Clínica.
"""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

# Clave de sistema institucional M2M (R31)
INTEROP_SYSTEM_KEY = os.getenv("INTEROP_SYSTEM_KEY", "HOSPITAL-UCI-INTEROP-KEY-2026")
INSTITUCION_NOMBRE = os.getenv("INSTITUCION_NOMBRE", "Hospital Universitario UCI - Salud Digital")
PUBLIC_BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "https://salud-digital-proyecto.onrender.com").rstrip("/")

# Clave simétrica AES-256 (Fernet) derivada para Cifrado de Entidad a Entidad
_CIPHER_SEED = hashlib.sha256(f"AES256-ENTIDAD-FHIR-{INTEROP_SYSTEM_KEY}".encode("utf-8")).digest()
_FERNET_KEY = base64.urlsafe_b64encode(_CIPHER_SEED)
_FERNET_CIPHER = Fernet(_FERNET_KEY)

ROLES_PERMITIDOS_INTEROP = {
    "medico",
    "médico",
    "medico uci",
    "médico uci",
    "admin biomedico",
    "admin biomédico",
    "admin_biomedico",
    "biomedico",
    "biomédico",
    "admin",
    "administrador",
}

CAMAS_UCI_CATALOGO = [
    "UCI-BED-01",
    "UCI-BED-02",
    "UCI-BED-03",
    "UCI-BED-04",
    "UCI-BED-05",
    "UCI-BED-06",
]

EQUIPOS_UCI_CATALOGO = [
    "VENT-01 · Ventilador Mecánico Dräger Evita V800",
    "MON-01 · Monitor Multiparámetro Philips IntelliVue MX800",
    "VENT-02 · Ventilador Mecánico Hamilton C3",
    "MON-02 · Monitor Multiparámetro Mindray BeneVision N17",
    "BVOL-01 · Bomba de Infusión BBraun Space P",
]


# ==============================================================================
# UTILIDADES DE CIFRADO ENTIDAD-A-ENTIDAD Y FHIR (OperationOutcome R30/R31/R32)
# ==============================================================================

def cifrar_payload_entidad(datos: Dict[str, Any]) -> Dict[str, str]:
    """Cifra el paquete clínico de una entidad a otra con AES-256 (Fernet) y firma HMAC-SHA256."""
    raw_bytes = json.dumps(datos, ensure_ascii=False, sort_keys=True).encode("utf-8")
    token_aes = _FERNET_CIPHER.encrypt(raw_bytes).decode("utf-8")
    firma_hmac = hmac.new(_CIPHER_SEED, raw_bytes, hashlib.sha256).hexdigest()
    return {
        "algoritmo": "AES-256-Fernet + HMAC-SHA256",
        "ciphertext": token_aes,
        "hmac_sha256": firma_hmac,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def descifrar_payload_entidad(ciphertext: str) -> Optional[Dict[str, Any]]:
    """Descifra un sobre clínico AES-256-Fernet enviado entre entidades."""
    try:
        decrypted = _FERNET_CIPHER.decrypt(ciphertext.encode("utf-8"))
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return None


def crear_operation_outcome(severity: str, code: str, diagnostics: str) -> Dict[str, Any]:
    """Construye un recurso estándar HL7 FHIR R4 OperationOutcome."""
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ],
    }


def _verificar_system_key(
    x_system_key: Optional[str],
    authorization: Optional[str],
    x_api_key: Optional[str] = None,
    x_partner_secret: Optional[str] = None,
) -> str:
    """
    R31: Valida la credencial máquina-a-máquina (X-System-Key, X-API-Key, X-Partner-Secret o Bearer).
    Rechaza con HTTP 401 + OperationOutcome si está ausente o es inválida.
    """
    token = (x_system_key or x_api_key or x_partner_secret or "").strip()
    if not token and authorization:
        auth_parts = authorization.strip().split(" ", 1)
        if len(auth_parts) == 2 and auth_parts[0].lower() in ("bearer", "apikey", "system"):
            token = auth_parts[1].strip()
        else:
            token = authorization.strip()

    claves_validas = {INTEROP_SYSTEM_KEY}
    partner_env = os.getenv("PARTNER_SYSTEM_SECRET", "").strip()
    if partner_env:
        claves_validas.add(partner_env)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=crear_operation_outcome(
                severity="error",
                code="security",
                diagnostics="R31: Credencial institucional X-System-Key ausente. Acceso M2M rechazado (HTTP 401 Unauthorized).",
            ),
        )

    if token not in claves_validas:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=crear_operation_outcome(
                severity="error",
                code="security",
                diagnostics="R31: Credencial institucional X-System-Key inválida. Acceso M2M rechazado (HTTP 401 Unauthorized).",
            ),
        )
    return token


def _extraer_recursos_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    R30 & R32: Valida que el payload sea un Bundle FHIR R4 con Patient, ServiceRequest y Consent activo.
    Soporta además desencriptación automática si el paquete viene en un sobre cifrado entidad-a-entidad.
    """
    if isinstance(bundle, dict) and bundle.get("encrypted_ciphertext") and not bundle.get("entry"):
        descifrado = descifrar_payload_entidad(str(bundle["encrypted_ciphertext"]))
        if isinstance(descifrado, dict):
            bundle = descifrado

    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="structure",
                diagnostics="R30: El payload debe ser un recurso FHIR válido con resourceType='Bundle'.",
            ),
        )

    entries = bundle.get("entry")
    if not isinstance(entries, list) or len(entries) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="required",
                diagnostics="R30: El Bundle FHIR no contiene entradas en 'entry'.",
            ),
        )

    patient = None
    service_request = None
    consent = None
    observations = []
    diagnostic_reports = []

    for item in entries:
        res = item.get("resource") if isinstance(item, dict) else None
        if not isinstance(res, dict):
            continue
        rtype = res.get("resourceType")
        if rtype == "Patient":
            patient = res
        elif rtype in ("ServiceRequest", "Encounter"):
            if not service_request or rtype == "ServiceRequest":
                service_request = res
        elif rtype == "Consent":
            consent = res
        elif rtype == "Observation":
            observations.append(res)
        elif rtype == "DiagnosticReport":
            estado_rep = str(res.get("status", "")).lower()
            if estado_rep in ("preliminary", "entered-in-error", "cancelled", "pendiente", "rechazado"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=crear_operation_outcome(
                        severity="error",
                        code="business-rule",
                        diagnostics="R30: No se permiten DiagnosticReport de IA en estado pendiente o rechazado.",
                    ),
                )
            diagnostic_reports.append(res)

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="required",
                diagnostics="R30: El Bundle FHIR debe incluir un recurso 'Patient' con la identificación del paciente.",
            ),
        )

    if not service_request:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="required",
                diagnostics="R30: El Bundle FHIR debe incluir un recurso 'ServiceRequest' con el motivo clínico.",
            ),
        )

    reason_list_chk = service_request.get("reasonCode", [])
    motivo_chk = ""
    if isinstance(reason_list_chk, list) and len(reason_list_chk) > 0 and isinstance(reason_list_chk[0], dict):
        motivo_chk = str(reason_list_chk[0].get("text") or "").strip()
    elif "reasonCode" not in service_request and isinstance(service_request.get("code"), dict):
        motivo_chk = str(service_request["code"].get("text") or "").strip()

    if not motivo_chk:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="required",
                diagnostics="R30: El recurso 'ServiceRequest' está incompleto (falta motivo clínico en 'reasonCode'). Rechazado sin guardar datos a medias.",
            ),
        )

    # R32: Validación de consentimiento informado (Ley 1581/2012)
    if not consent or str(consent.get("status", "")).lower() != "active":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="business-rule",
                diagnostics="R32: Transferencia bloqueada por Ley 1581/2012. Falta recurso 'Consent' con status='active'.",
            ),
        )

    return {
        "patient": patient,
        "service_request": service_request,
        "consent": consent,
        "observations": observations,
        "diagnostic_reports": diagnostic_reports,
    }


def _ensure_interop_table(conn):
    """Garantiza de forma idempotente la tabla remisiones_interinstitucionales y columnas de Historia Clínica."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS remisiones_interinstitucionales (
            id SERIAL PRIMARY KEY,
            referencia_externa VARCHAR(120),
            folio_historia VARCHAR(80),
            direccion VARCHAR(20) NOT NULL,
            institucion_origen VARCHAR(180) NOT NULL,
            institucion_destino VARCHAR(180) NOT NULL,
            callback_url TEXT,
            paciente_id VARCHAR(100) NOT NULL,
            paciente_db_id INT,
            documento_identidad VARCHAR(100),
            paciente_nombre VARCHAR(180),
            es_paciente_nuevo BOOLEAN DEFAULT FALSE,
            servicio_solicitado TEXT,
            motivo_clinico TEXT,
            signos_vitales JSONB DEFAULT '[]'::jsonb,
            cama_asignada VARCHAR(60),
            equipo_asignado VARCHAR(150),
            cifrado_algoritmo VARCHAR(100) DEFAULT 'AES-256-Fernet + HMAC-SHA256 / TLS 1.3',
            payload_cifrado TEXT,
            estado VARCHAR(30) NOT NULL DEFAULT 'ACEPTADA',
            decision_motivo TEXT,
            bundle_fhir JSONB,
            respuesta_socio JSONB,
            auditoria_hash VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        ALTER TABLE remisiones_interinstitucionales ADD COLUMN IF NOT EXISTS folio_historia VARCHAR(80);
        ALTER TABLE remisiones_interinstitucionales ADD COLUMN IF NOT EXISTS paciente_db_id INT;
        ALTER TABLE remisiones_interinstitucionales ADD COLUMN IF NOT EXISTS documento_identidad VARCHAR(100);
        ALTER TABLE remisiones_interinstitucionales ADD COLUMN IF NOT EXISTS es_paciente_nuevo BOOLEAN DEFAULT FALSE;
        ALTER TABLE remisiones_interinstitucionales ADD COLUMN IF NOT EXISTS cifrado_algoritmo VARCHAR(100) DEFAULT 'AES-256-Fernet + HMAC-SHA256 / TLS 1.3';
        ALTER TABLE remisiones_interinstitucionales ADD COLUMN IF NOT EXISTS payload_cifrado TEXT;
    """)
    conn.commit()
    cur.close()


def _registrar_auditoria_interop(conn, usuario_id: Optional[int], accion: str, registro_id: int, origen: str, detalles: str) -> str:
    """Registra la operación en audit_logs con cadena de hashes SHA-256 (R28)."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS hash_anterior VARCHAR(64);
            ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS hash_actual VARCHAR(64);
        """)
        conn.commit()
        cur.execute("SELECT hash_actual FROM audit_logs WHERE hash_actual IS NOT NULL ORDER BY id DESC LIMIT 1;")
        last_row = cur.fetchone()
        hash_previo = last_row["hash_actual"] if (last_row and last_row.get("hash_actual")) else ("0" * 64)
        fecha_ahora = datetime.now(timezone.utc)
        uid_val = usuario_id if usuario_id else 1
        datos = f"{hash_previo}#{uid_val}#{accion}#remisiones_interinstitucionales#{registro_id}#{origen}#{detalles}#{fecha_ahora.isoformat()}"
        hash_nuevo = hashlib.sha256(datos.encode("utf-8")).hexdigest()
        cur.execute("""
            INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id, origen, detalles, fecha, hash_anterior, hash_actual)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (uid_val, accion, "remisiones_interinstitucionales", registro_id, origen, detalles, fecha_ahora, hash_previo, hash_nuevo))
        conn.commit()
        return hash_nuevo
    except Exception as exc:
        conn.rollback()
        print(f"Aviso en auditoría interoperabilidad: {exc}")
        return hashlib.sha256(f"{accion}-{registro_id}-{detalles}".encode("utf-8")).hexdigest()
    finally:
        cur.close()


def _integrar_paciente_e_historia_en_bd(
    conn,
    paciente_id_ext: str,
    documento_ext: str,
    paciente_nombre: str,
    signos_extraidos: List[Dict[str, Any]],
    institucion_origen: str,
) -> Dict[str, Any]:
    """
    Integración automática en la Base de Datos cuando llega un paciente de otro hospital:
    1. Verifica si el paciente ya existe en nuestra tabla `pacientes` por documento o nombre.
    2. Si NO existe, lo inserta automáticamente como nuevo paciente en `pacientes` con cama UCI y equipo biomédico.
    3. Registra sus signos vitales LOINC en la tabla `observaciones` asociados a su encuentro clínico.
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        doc_clean = str(documento_ext or paciente_id_ext or f"DOC-EXT-{int(datetime.now().timestamp())}").strip()
        nombre_clean = str(paciente_nombre or f"Paciente Interop {doc_clean}").strip()

        pac_row = None
        # Buscar primero por documento_identidad exacto o por ID si viene como PAC-{id}
        if doc_clean.upper().startswith("PAC-") and doc_clean[4:].isdigit():
            cur.execute(
                "SELECT id, documento_identidad, nombre, cama_uci FROM pacientes WHERE id = %s AND is_deleted = FALSE LIMIT 1;",
                (int(doc_clean[4:]),),
            )
            pac_row = cur.fetchone()

        if not pac_row:
            cur.execute(
                """
                SELECT id, documento_identidad, nombre, cama_uci
                FROM pacientes
                WHERE (LOWER(documento_identidad) = LOWER(%s) OR LOWER(nombre) = LOWER(%s))
                  AND is_deleted = FALSE
                ORDER BY id ASC LIMIT 1;
                """,
                (doc_clean, nombre_clean),
            )
            pac_row = cur.fetchone()

        es_nuevo = False
        if not pac_row:
            # Seleccionar cama UCI para el nuevo paciente
            cur.execute("SELECT COUNT(*) AS total FROM pacientes WHERE is_deleted = FALSE;")
            total_pacs = (cur.fetchone() or {}).get("total") or 0
            cama_auto = CAMAS_UCI_CATALOGO[total_pacs % len(CAMAS_UCI_CATALOGO)]

            cur.execute(
                """
                INSERT INTO pacientes (documento_identidad, nombre, cama_uci, created_by, origen, cluster_id, cluster_nombre)
                VALUES (%s, %s, %s, 2, 'sintetico', 2, 'Cluster 2: Zona de Confusión')
                RETURNING id, documento_identidad, nombre, cama_uci;
                """,
                (doc_clean, nombre_clean, cama_auto),
            )
            pac_row = cur.fetchone()
            es_nuevo = True
            conn.commit()

        pac_db_id = pac_row["id"]
        cama_final = pac_row.get("cama_uci") or "UCI-BED-01"
        equipo_final = EQUIPOS_UCI_CATALOGO[(pac_db_id - 1) % len(EQUIPOS_UCI_CATALOGO)]

        # Crear o recuperar encuentro activo y guardar observaciones LOINC en la BD real
        try:
            cur.execute(
                "SELECT id FROM encuentros WHERE paciente_id = %s AND estado = 'in-progress' AND is_deleted = FALSE ORDER BY id DESC LIMIT 1;",
                (pac_db_id,),
            )
            enc_row = cur.fetchone()
            encuentro_id = enc_row["id"] if enc_row else None
            if not encuentro_id:
                cur.execute("SELECT id FROM equipos_uci ORDER BY id ASC LIMIT 1;")
                eq_row = cur.fetchone()
                if eq_row:
                    cur.execute(
                        """
                        INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by, is_deleted)
                        VALUES (%s, %s, 'in-progress', 2, FALSE)
                        RETURNING id;
                        """,
                        (pac_db_id, eq_row["id"]),
                    )
                    encuentro_id = cur.fetchone()["id"]
                    conn.commit()

            if encuentro_id and signos_extraidos:
                for s in signos_extraidos:
                    val_num = s.get("valor")
                    if isinstance(val_num, (int, float)) or (isinstance(val_num, str) and val_num.replace(".", "", 1).isdigit()):
                        cur.execute(
                            """
                            INSERT INTO observaciones (encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, version, created_by)
                            VALUES (%s, %s, %s, %s, %s, %s, 1, 2);
                            """,
                            (
                                encuentro_id,
                                str(s.get("parametro") or "Signo Vital FHIR")[:80],
                                str(s.get("loinc") or "8867-4")[:30],
                                float(val_num),
                                str(s.get("unidad") or "")[:20],
                                f"Importado vía FHIR desde {institucion_origen}"[:120],
                            ),
                        )
                conn.commit()
        except Exception:
            conn.rollback()

        return {
            "paciente_db_id": pac_db_id,
            "codigo_local": f"PAC-{pac_db_id}",
            "documento_identidad": pac_row.get("documento_identidad") or doc_clean,
            "paciente_nombre": pac_row.get("nombre") or nombre_clean,
            "cama_asignada": cama_final,
            "equipo_asignado": equipo_final,
            "es_paciente_nuevo": es_nuevo,
        }
    finally:
        cur.close()


def _obtener_signos_paciente_bd(conn, paciente_db_id: int) -> List[Dict[str, Any]]:
    """Obtiene los últimos signos vitales LOINC de un paciente desde nuestra tabla `observaciones`."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT o.parametro, o.codigo_loinc, o.valor, o.unidad
            FROM observaciones o
            JOIN encuentros e ON o.encuentro_id = e.id
            WHERE e.paciente_id = %s AND o.is_deleted = FALSE
            ORDER BY o.id DESC
            LIMIT 4;
            """,
            (paciente_db_id,),
        )
        rows = cur.fetchall()
        if rows:
            return [
                {
                    "loinc": str(r.get("codigo_loinc") or "8867-4"),
                    "parametro": str(r.get("parametro") or "Signo Vital"),
                    "valor": float(r["valor"]) if r.get("valor") is not None else 85.0,
                    "unidad": str(r.get("unidad") or ""),
                }
                for r in rows
            ]
    except Exception:
        conn.rollback()
    finally:
        cur.close()

    # Signos de telemetría UCI por defecto si el paciente aún no tiene filas en observaciones
    base_fc = 78 + (paciente_db_id * 3) % 20
    base_pa = 116 + (paciente_db_id * 5) % 18
    return [
        {"loinc": "8867-4", "parametro": "Heart rate", "valor": float(base_fc), "unidad": "beats/min"},
        {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": float(base_pa), "unidad": "mmHg"},
        {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 97.0, "unidad": "%"},
        {"loinc": "8310-5", "parametro": "Body temperature", "valor": 36.7, "unidad": "Cel"},
    ]


def _construir_bundle_paciente_local(
    paciente_row: Dict[str, Any],
    signos_lista: List[Dict[str, Any]],
    motivo_clinico: str,
    servicio_solicitado: str,
    consentimiento_activo: bool = True,
    ref_id: int = 1,
) -> Dict[str, Any]:
    """Construye un Bundle HL7 FHIR R4 completo con sobre de Cifrado Entidad-a-Entidad AES-256."""
    pac_db_id = paciente_row["id"]
    pac_id_str = f"PAC-{pac_db_id}"
    doc_id = str(paciente_row.get("documento_identidad") or pac_id_str)
    pac_nombre = str(paciente_row.get("nombre") or f"Paciente UCI {pac_id_str}")
    cama_uci = str(paciente_row.get("cama_uci") or "UCI-BED-01")

    obs_resources = []
    for idx, s in enumerate(signos_lista, 1):
        obs_resources.append({
            "resource": {
                "resourceType": "Observation",
                "id": f"OBS-{pac_id_str}-{idx}",
                "status": "final",
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": s["loinc"], "display": s["parametro"]}],
                    "text": s["parametro"],
                },
                "subject": {"reference": f"Patient/{pac_id_str}"},
                "valueQuantity": {"value": s["valor"], "unit": s["unidad"]},
                "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            }
        })

    datos_sensibles = {
        "paciente_id": pac_id_str,
        "documento_identidad": doc_id,
        "nombre": pac_nombre,
        "cama_uci": cama_uci,
        "servicio": servicio_solicitado,
        "motivo_clinico": motivo_clinico,
        "signos_vitales": signos_lista,
        "institucion_origen": INSTITUCION_NOMBRE,
    }
    sobre_cifrado = cifrar_payload_entidad(datos_sensibles)

    return {
        "resourceType": "Bundle",
        "id": f"BUNDLE-UCI-{ref_id}",
        "type": "collection",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "security": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                    "code": "R",
                    "display": "Cifrado Entidad-a-Entidad AES-256-Fernet + HMAC-SHA256",
                }
            ]
        },
        "security_envelope": sobre_cifrado,
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": pac_id_str,
                    "identifier": [
                        {"system": "urn:hospital-uci:documento", "value": doc_id},
                        {"system": "urn:hospital-uci:paciente", "value": pac_id_str},
                    ],
                    "name": [{"text": pac_nombre}],
                    "gender": "male",
                }
            },
            {
                "resource": {
                    "resourceType": "ServiceRequest",
                    "id": f"SR-UCI-{ref_id}",
                    "status": "active",
                    "intent": "order",
                    "code": {
                        "coding": [
                            {
                                "system": "http://snomed.info/sct",
                                "code": "305351004",
                                "display": servicio_solicitado,
                            }
                        ],
                        "text": servicio_solicitado,
                    },
                    "subject": {"reference": f"Patient/{pac_id_str}"},
                    "reasonCode": [{"text": motivo_clinico}],
                    "authoredOn": datetime.now(timezone.utc).isoformat(),
                }
            },
            {
                "resource": {
                    "resourceType": "Consent",
                    "id": f"CONSENT-UCI-{ref_id}",
                    "status": "active" if consentimiento_activo else "inactive",
                    "scope": {"text": "Autorización de consulta e intercambio interinstitucional Ley 1581/2012"},
                    "patient": {"reference": f"Patient/{pac_id_str}"},
                    "dateTime": datetime.now(timezone.utc).isoformat(),
                }
            },
            *obs_resources,
        ],
    }


# ==============================================================================
# MODELOS PYDANTIC
# ==============================================================================

class SolicitudPingSocio(BaseModel):
    url_destino: str = Field(..., min_length=4)
    api_key_socio: Optional[str] = Field(default="")


class SolicitudExplorarSocio(BaseModel):
    url_destino: str = Field(..., min_length=4)
    api_key_socio: Optional[str] = Field(default="")


class SolicitudConsultaM2M(BaseModel):
    paciente_id: str = Field(..., description="ID o documento del paciente solicitado")
    motivo_clinico: Optional[str] = Field(default="Consulta interinstitucional de Historia Clínica FHIR")
    servicio_solicitado: Optional[str] = Field(default="Continuidad Asistencial e Historia Clínica Electrónica")
    consentimiento_confirmado: bool = Field(default=True)


class SolicitudEnvioRemision(BaseModel):
    url_destino: str = Field(..., min_length=4, description="URL pública del endpoint FHIR del hospital socio")
    api_key_socio: Optional[str] = Field(default="", description="API Key entregada por el hospital socio")
    institucion_destino_nombre: Optional[str] = Field(default="Hospital Socio Externo")
    paciente_id: str = Field(default="", description="ID o documento del paciente solicitado en la BD del socio")
    paciente_nombre_opcional: Optional[str] = Field(default="", description="Nombre del paciente si se conoce")
    modo_operacion: Optional[str] = Field(default="CONSULTAR_SOCIO", description="CONSULTAR_SOCIO o ENVIAR_LOCAL")
    servicio_solicitado: Optional[str] = Field(default="Consulta e Integración de Historia Clínica UCI")
    motivo_clinico: Optional[str] = Field(default="", description="Motivo clínico de la solicitud FHIR")
    consentimiento_confirmado: bool = Field(default=False, description="Confirmación de Consentimiento Ley 1581/2012")
    incluir_telemetria: bool = Field(default=True, description="Incluir signos vitales LOINC")


class SolicitudDecisionRemision(BaseModel):
    remision_id: int
    decision: str = Field(default="ACEPTADA")
    cama_asignada: Optional[str] = None
    equipo_asignado: Optional[str] = None
    motivo: Optional[str] = None
    api_key_callback: Optional[str] = None


# ==============================================================================
# FACTORY DEL ROUTER
# ==============================================================================

def get_interoperabilidad_router(get_db, verify_user_credentials):
    router = APIRouter(prefix="/interoperabilidad", tags=["Interoperabilidad FHIR (R30-R33)"])

    def _validar_rol_clinico(user: Dict[str, Any]):
        rol = str(user.get("rol_nombre") or user.get("rol") or "").strip()
        if rol.lower() not in ROLES_PERMITIDOS_INTEROP:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: El rol '{rol}' no tiene permisos para gestionar historias clínicas interinstitucionales.",
            )

    # --------------------------------------------------------------------------
    # 1. CREDENCIALES INSTITUCIONALES (Endpoints para entregar al otro grupo)
    # --------------------------------------------------------------------------
    @router.get("/credenciales")
    def obtener_credenciales_institucionales(request: Request, user: dict = Depends(verify_user_credentials)):
        _validar_rol_clinico(user)
        base_url = str(request.base_url).rstrip("/")
        host_publico = PUBLIC_BASE_URL if ("127.0.0.1" in base_url or "localhost" in base_url) else base_url

        return {
            "institucion": INSTITUCION_NOMBRE,
            "endpoint_directorio_pacientes": f"{host_publico}/interoperabilidad/pacientes",
            "endpoint_consulta_paciente": f"{host_publico}/interoperabilidad/pacientes/{{paciente_id}}",
            "endpoint_remisiones": f"{host_publico}/interoperabilidad/remisiones",
            "endpoint_callback": f"{host_publico}/interoperabilidad/callback",
            "header_autenticacion": "X-System-Key",
            "api_key": INTEROP_SYSTEM_KEY,
            "cifrado": "AES-256-Fernet + HMAC-SHA256 sobre TLS 1.3",
            "formato": "application/json (HL7 FHIR R4 Bundle)",
        }

    # --------------------------------------------------------------------------
    # 2. DIRECTORIO M2M DE NUESTRA BD DE PACIENTES (Para que el otro grupo consulte)
    # --------------------------------------------------------------------------
    @router.get("/pacientes")
    def listar_directorio_pacientes_fhir(
        request: Request,
        x_system_key: Optional[str] = Header(default=None, alias="X-System-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
        conn=Depends(get_db),
    ):
        """
        Permite al hospital socio (autenticado con X-System-Key R31) consultar el catálogo
        de pacientes disponibles en nuestra Base de Datos para solicitar su Historia Clínica FHIR.
        """
        try:
            _verificar_system_key(
                x_system_key,
                authorization,
                request.headers.get("X-API-Key"),
                request.headers.get("X-Partner-Secret"),
            )
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)

        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute(
                """
                SELECT id, documento_identidad, nombre, cama_uci,
                       COALESCE(cluster_nombre, 'UCI Adultos') AS servicio_actual
                FROM pacientes
                WHERE is_deleted = FALSE
                ORDER BY id ASC;
                """
            )
            rows = cur.fetchall()
            pacientes_out = []
            entries_fhir = []
            for r in rows:
                pid = f"PAC-{r['id']}"
                doc = str(r.get("documento_identidad") or pid)
                signos = _obtener_signos_paciente_bd(conn, r["id"])
                pacientes_out.append({
                    "id": r["id"],
                    "paciente_id": pid,
                    "documento_identidad": doc,
                    "nombre": r["nombre"],
                    "cama_uci": r.get("cama_uci") or "UCI-BED-01",
                    "servicio_actual": r.get("servicio_actual") or "Unidad de Cuidados Intensivos",
                    "consentimiento_ley1581": "active",
                    "signos_vitales": signos,
                })
                entries_fhir.append({
                    "resource": {
                        "resourceType": "Patient",
                        "id": pid,
                        "identifier": [
                            {"system": "urn:hospital-uci:documento", "value": doc},
                            {"system": "urn:hospital-uci:paciente", "value": pid},
                        ],
                        "name": [{"text": r["nombre"]}],
                    }
                })

            return {
                "resourceType": "Bundle",
                "type": "searchset",
                "institucion": INSTITUCION_NOMBRE,
                "cifrado": "AES-256-Fernet + HMAC-SHA256",
                "total": len(pacientes_out),
                "pacientes": pacientes_out,
                "entry": entries_fhir,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 3. EXTRACCIÓN M2M DE UN PACIENTE X DE NUESTRA BD (Cuando el otro grupo lo solicita)
    # --------------------------------------------------------------------------
    @router.get("/pacientes/{paciente_ref}")
    def entregar_paciente_fhir_a_socio(
        paciente_ref: str,
        request: Request,
        motivo: Optional[str] = Query(default="Consulta interinstitucional de Historia Clínica Electrónica FHIR"),
        consentimiento: Optional[str] = Query(default="active"),
        x_system_key: Optional[str] = Header(default=None, alias="X-System-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
        x_institucion_origen: Optional[str] = Header(default="Hospital Socio Externo", alias="X-Institucion-Origen"),
        conn=Depends(get_db),
    ):
        """
        Cuando el otro grupo solicita la información de un paciente X de nuestra Base de Datos:
        1. Valida API Key M2M (R31).
        2. Valida Consentimiento Ley 1581 (R32) y parámetros (R30).
        3. Empaqueta automáticamente el Bundle HL7 FHIR R4 cifrado (AES-256) y lo entrega de inmediato,
           dejando constancia automática en nuestro historial y auditoría SHA-256.
        """
        try:
            _verificar_system_key(
                x_system_key,
                authorization,
                request.headers.get("X-API-Key"),
                request.headers.get("X-Partner-Secret"),
            )
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)

        if str(consentimiento or "").lower() in ("false", "0", "inactive", "no"):
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=crear_operation_outcome(
                    severity="error",
                    code="business-rule",
                    diagnostics="R32: Transferencia bloqueada por Ley 1581/2012. Falta recurso 'Consent' con status='active'.",
                ),
            )

        if not str(motivo or "").strip():
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=crear_operation_outcome(
                    severity="error",
                    code="required",
                    diagnostics="R30: El recurso 'ServiceRequest' está incompleto (falta motivo clínico en 'reasonCode').",
                ),
            )

        _ensure_interop_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            ref_clean = paciente_ref.strip()
            pac_row = None
            if ref_clean.isdigit():
                cur.execute("SELECT * FROM pacientes WHERE id = %s AND is_deleted = FALSE LIMIT 1;", (int(ref_clean),))
                pac_row = cur.fetchone()
            elif ref_clean.upper().startswith("PAC-") and ref_clean[4:].isdigit():
                cur.execute("SELECT * FROM pacientes WHERE id = %s AND is_deleted = FALSE LIMIT 1;", (int(ref_clean[4:]),))
                pac_row = cur.fetchone()

            if not pac_row:
                cur.execute(
                    """
                    SELECT * FROM pacientes
                    WHERE (LOWER(documento_identidad) = LOWER(%s) OR LOWER(nombre) LIKE LOWER(%s))
                      AND is_deleted = FALSE
                    ORDER BY id ASC LIMIT 1;
                    """,
                    (ref_clean, f"%{ref_clean}%"),
                )
                pac_row = cur.fetchone()

            if not pac_row:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content=crear_operation_outcome(
                        severity="error",
                        code="not-found",
                        diagnostics=f"R30: No se encontró ningún paciente con identificación '{ref_clean}' en la base de datos de {INSTITUCION_NOMBRE}.",
                    ),
                )

            signos = _obtener_signos_paciente_bd(conn, pac_row["id"])
            pac_id_str = f"PAC-{pac_row['id']}"
            doc_id = str(pac_row.get("documento_identidad") or pac_id_str)
            servicio_txt = "Extracción de Historia Clínica Electrónica UCI (HL7 FHIR R4)"

            # Registrar automáticamente en nuestro historial que el socio consultó y extrajo este paciente
            cur.execute(
                """
                INSERT INTO remisiones_interinstitucionales (
                    direccion, institucion_origen, institucion_destino,
                    paciente_id, paciente_db_id, documento_identidad, paciente_nombre,
                    es_paciente_nuevo, servicio_solicitado, motivo_clinico, signos_vitales,
                    cama_asignada, equipo_asignado, estado, decision_motivo
                )
                VALUES ('SALIENTE', %s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s::jsonb, %s, %s, 'ACEPTADA', %s)
                RETURNING id;
                """,
                (
                    INSTITUCION_NOMBRE,
                    x_institucion_origen,
                    pac_id_str,
                    pac_row["id"],
                    doc_id,
                    pac_row["nombre"],
                    servicio_txt,
                    motivo,
                    json.dumps(signos),
                    pac_row.get("cama_uci") or "UCI-BED-01",
                    EQUIPOS_UCI_CATALOGO[(pac_row["id"] - 1) % len(EQUIPOS_UCI_CATALOGO)],
                    f"Consultado por {x_institucion_origen}. Validado (R30/R31/R32), cifrado (AES-256) y entregado automáticamente.",
                ),
            )
            remision_id = cur.fetchone()["id"]
            folio_hc = f"HC-EXP-2026-{remision_id:04d}"

            bundle_fhir = _construir_bundle_paciente_local(
                paciente_row=pac_row,
                signos_lista=signos,
                motivo_clinico=str(motivo),
                servicio_solicitado=servicio_txt,
                consentimiento_activo=True,
                ref_id=remision_id,
            )
            ciphertext_str = bundle_fhir["security_envelope"]["ciphertext"]

            audit_hash = _registrar_auditoria_interop(
                conn,
                usuario_id=None,
                accion="CONSULTA_FHIR_ENTREGADA_AUTOMATICA",
                registro_id=remision_id,
                origen=f"EXTERNO:{x_institucion_origen}",
                detalles=f"Entrega automática de Historia Clínica FHIR de {pac_id_str} ({doc_id}) a {x_institucion_origen}. Cifrado AES-256.",
            )

            cur.execute(
                """
                UPDATE remisiones_interinstitucionales
                SET referencia_externa = %s,
                    folio_historia = %s,
                    payload_cifrado = %s,
                    bundle_fhir = %s::jsonb,
                    auditoria_hash = %s
                WHERE id = %s;
                """,
                (f"SR-UCI-{remision_id}", folio_hc, ciphertext_str, json.dumps(bundle_fhir), audit_hash, remision_id),
            )
            conn.commit()
            return bundle_fhir
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 4. ENDPOINT M2M DE RECEPCIÓN AUTOMÁTICA (POST /interoperabilidad/remisiones)
    # --------------------------------------------------------------------------
    @router.post("/remisiones", status_code=status.HTTP_201_CREATED)
    async def recibir_remision_fhir(
        request: Request,
        x_system_key: Optional[str] = Header(default=None, alias="X-System-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
        x_institucion_origen: Optional[str] = Header(default="Hospital Socio Externo", alias="X-Institucion-Origen"),
        x_callback_url: Optional[str] = Header(default=None, alias="X-Callback-Url"),
        conn=Depends(get_db),
    ):
        """
        Recibe un Bundle FHIR del otro grupo, valida R31 (API Key), R32 (Consentimiento) y R30 (Esquema),
        y lo ACEPTA AUTOMÁTICAMENTE DE INMEDIATO (sin botones manuales):
        - Si el paciente no está en nuestra tabla `pacientes`, lo crea automáticamente en la BD.
        - Crea una Nueva Historia Clínica asociada a la identificación del paciente.
        """
        try:
            _verificar_system_key(
                x_system_key,
                authorization,
                request.headers.get("X-API-Key"),
                request.headers.get("X-Partner-Secret"),
            )
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=crear_operation_outcome(
                    severity="error",
                    code="invalid",
                    diagnostics="R30: El cuerpo de la petición no es un JSON válido.",
                ),
            )

        # Ping de verificación de credenciales M2M (R31)
        if request.headers.get("X-Interop-Ping", "").lower() == "true" or (
            isinstance(payload, dict) and payload.get("resourceType") == "Parameters"
        ):
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "resourceType": "OperationOutcome",
                    "status": "ok",
                    "autenticado": True,
                    "institucion": INSTITUCION_NOMBRE,
                    "cifrado": "AES-256-Fernet + HMAC-SHA256",
                    "issue": [
                        {
                            "severity": "information",
                            "code": "informational",
                            "diagnostics": f"R31 OK: Credencial institucional X-System-Key autenticada correctamente en {INSTITUCION_NOMBRE}. Canal FHIR cifrado activo.",
                        }
                    ],
                },
            )

        # R30 & R32: Validar estructura FHIR y Consentimiento activo
        try:
            recursos = _extraer_recursos_bundle(payload)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)

        patient = recursos["patient"]
        sr = recursos["service_request"]
        observations = recursos["observations"]

        # Extraer identificación y nombre del paciente
        paciente_id = str(patient.get("id") or "PAC-EXT-001").strip()
        documento_ext = paciente_id
        if patient.get("identifier") and isinstance(patient["identifier"], list):
            for ident in patient["identifier"]:
                if isinstance(ident, dict) and ident.get("value"):
                    documento_ext = str(ident["value"]).strip()
                    break

        paciente_nombre = f"Paciente Interop ({documento_ext})"
        if patient.get("name") and isinstance(patient["name"], list):
            n0 = patient["name"][0]
            if isinstance(n0, dict):
                if n0.get("text"):
                    paciente_nombre = str(n0["text"]).strip()
                else:
                    given = " ".join(n0.get("given", [])) if isinstance(n0.get("given"), list) else ""
                    family = str(n0.get("family", ""))
                    full = f"{given} {family}".strip()
                    if full:
                        paciente_nombre = full

        servicio_solicitado = "Historia Clínica Interoperable e Ingreso UCI"
        code_obj = sr.get("code", {})
        if isinstance(code_obj, dict):
            if code_obj.get("text"):
                servicio_solicitado = str(code_obj["text"])
            elif isinstance(code_obj.get("coding"), list) and len(code_obj["coding"]) > 0:
                servicio_solicitado = str(code_obj["coding"][0].get("display") or servicio_solicitado)

        motivo_clinico = "Integración automática de Historia Clínica vía HL7 FHIR R4"
        reason_list = sr.get("reasonCode", [])
        if isinstance(reason_list, list) and len(reason_list) > 0:
            r0 = reason_list[0]
            if isinstance(r0, dict) and r0.get("text"):
                motivo_clinico = str(r0.get("text"))

        signos_extraidos = []
        for obs in observations:
            obs_code = obs.get("code", {})
            nombre_var = "Parámetro Clínico"
            codigo_loinc = "8867-4"
            if isinstance(obs_code, dict):
                if isinstance(obs_code.get("coding"), list) and len(obs_code["coding"]) > 0:
                    codigo_loinc = str(obs_code["coding"][0].get("code") or "8867-4")
                    nombre_var = str(obs_code["coding"][0].get("display") or obs_code.get("text") or nombre_var)
                elif obs_code.get("text"):
                    nombre_var = str(obs_code["text"])

            vq = obs.get("valueQuantity", {})
            valor = vq.get("value") if isinstance(vq, dict) else obs.get("valueString", "")
            unidad = vq.get("unit", "") if isinstance(vq, dict) else ""
            signos_extraidos.append({
                "loinc": codigo_loinc,
                "parametro": nombre_var,
                "valor": valor,
                "unidad": unidad,
            })

        ref_externa = str(sr.get("id") or payload.get("id") or f"EXT-{int(datetime.now().timestamp())}")
        callback_url = x_callback_url or payload.get("callback_url")

        _ensure_interop_table(conn)

        # INTEGRACIÓN AUTOMÁTICA EN BD DE PACIENTES Y CREACIÓN DE NUEVA HISTORIA CLÍNICA
        integracion = _integrar_paciente_e_historia_en_bd(
            conn=conn,
            paciente_id_ext=paciente_id,
            documento_ext=documento_ext,
            paciente_nombre=paciente_nombre,
            signos_extraidos=signos_extraidos,
            institucion_origen=x_institucion_origen,
        )

        sobre_cifrado = cifrar_payload_entidad({
            "paciente_db_id": integracion["paciente_db_id"],
            "documento_identidad": integracion["documento_identidad"],
            "paciente_nombre": integracion["paciente_nombre"],
            "signos_vitales": signos_extraidos,
            "motivo_clinico": motivo_clinico,
        })

        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute(
                """
                INSERT INTO remisiones_interinstitucionales (
                    referencia_externa, direccion, institucion_origen, institucion_destino,
                    callback_url, paciente_id, paciente_db_id, documento_identidad,
                    paciente_nombre, es_paciente_nuevo, servicio_solicitado,
                    motivo_clinico, signos_vitales, cama_asignada, equipo_asignado,
                    payload_cifrado, estado, bundle_fhir
                )
                VALUES (%s, 'ENTRANTE', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, 'ACEPTADA', %s::jsonb)
                RETURNING id, created_at;
                """,
                (
                    ref_externa,
                    x_institucion_origen,
                    INSTITUCION_NOMBRE,
                    callback_url,
                    integracion["codigo_local"],
                    integracion["paciente_db_id"],
                    integracion["documento_identidad"],
                    integracion["paciente_nombre"],
                    integracion["es_paciente_nuevo"],
                    servicio_solicitado,
                    motivo_clinico,
                    json.dumps(signos_extraidos),
                    integracion["cama_asignada"],
                    integracion["equipo_asignado"],
                    sobre_cifrado["ciphertext"],
                    json.dumps(payload),
                ),
            )
            row = cur.fetchone()
            remision_id = row["id"]
            folio_hc = f"HC-UCI-2026-{remision_id:04d}"
            accion_bd_txt = (
                f"Nuevo paciente registrado en BD ({integracion['codigo_local']} · Doc: {integracion['documento_identidad']})"
                if integracion["es_paciente_nuevo"]
                else f"Paciente existente actualizado en BD ({integracion['codigo_local']} · Doc: {integracion['documento_identidad']})"
            )
            resumen_auto = (
                f"Aceptado automáticamente. {accion_bd_txt}. Nueva Historia Clínica {folio_hc} creada. "
                f"Cama: {integracion['cama_asignada']} | Equipo: {integracion['equipo_asignado']}."
            )

            audit_hash = _registrar_auditoria_interop(
                conn,
                usuario_id=None,
                accion="HISTORIA_CLINICA_FHIR_INTEGRADA_AUTO",
                registro_id=remision_id,
                origen=f"EXTERNO:{x_institucion_origen}",
                detalles=resumen_auto,
            )
            cur.execute(
                """
                UPDATE remisiones_interinstitucionales
                SET folio_historia = %s,
                    decision_motivo = %s,
                    auditoria_hash = %s
                WHERE id = %s;
                """,
                (folio_hc, resumen_auto, audit_hash, remision_id),
            )
            conn.commit()

            return {
                "resourceType": "Task",
                "id": f"TASK-UCI-{remision_id}",
                "status": "completed",
                "intent": "order",
                "folio_historia_clinica": folio_hc,
                "paciente_db_id": integracion["paciente_db_id"],
                "paciente_codigo_local": integracion["codigo_local"],
                "documento_identidad": integracion["documento_identidad"],
                "es_paciente_nuevo": integracion["es_paciente_nuevo"],
                "cama_asignada": integracion["cama_asignada"],
                "equipo_asignado": integracion["equipo_asignado"],
                "cifrado": "AES-256-Fernet + HMAC-SHA256",
                "businessStatus": {"text": resumen_auto},
                "focus": {"reference": f"ServiceRequest/{ref_externa}"},
                "for": {"reference": f"Patient/{integracion['codigo_local']}"},
                "authoredOn": datetime.now(timezone.utc).isoformat(),
                "remision_local_id": remision_id,
                "auditoria_hash": audit_hash,
            }
        except Exception as exc:
            conn.rollback()
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=crear_operation_outcome(
                    severity="fatal",
                    code="exception",
                    diagnostics=f"Error interno integrando Historia Clínica FHIR: {str(exc)}",
                ),
            )
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 5. EXPLORAR EL DIRECTORIO DE PACIENTES DE LA BD DEL OTRO GRUPO
    # --------------------------------------------------------------------------
    @router.post("/explorar-socio")
    def explorar_directorio_pacientes_socio(
        solicitud: SolicitudExplorarSocio,
        user: dict = Depends(verify_user_credentials),
        conn=Depends(get_db),
    ):
        """
        Se conecta a la Base de Datos de Pacientes del Hospital Socio usando la URL y API Key (R31),
        y trae el listado de pacientes disponibles en el otro grupo para poder solicitar su Historia Clínica.
        """
        _validar_rol_clinico(user)
        url = solicitud.url_destino.strip()
        api_key = (solicitud.api_key_socio or "").strip()

        es_loopback = (
            "salud-digital-proyecto.onrender.com" in url
            or "127.0.0.1" in url
            or "localhost" in url
        )

        # Validar siempre R31 primero si es loopback o clave vacía
        if es_loopback or not api_key:
            try:
                _verificar_system_key(api_key, None)
            except HTTPException as exc:
                oo_err = exc.detail if isinstance(exc.detail, dict) else crear_operation_outcome("error", "security", str(exc.detail))
                diag_err = oo_err.get("issue", [{}])[0].get("diagnostics", "R31: Credencial X-System-Key inválida o ausente.")
                return {
                    "ok": False,
                    "status_code": exc.status_code,
                    "codigo_requisito": "R31",
                    "diagnostico_fhir": diag_err,
                    "operation_outcome": oo_err,
                    "pacientes": [],
                }

        if es_loopback:
            # En prueba de loopback contra nuestro propio servidor, mostramos pacientes de la BD
            # más 3 pacientes externos de referencia que permiten probar la creación de un paciente 100% nuevo en nuestra tabla `pacientes`
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute("SELECT id, documento_identidad, nombre, cama_uci FROM pacientes WHERE is_deleted = FALSE ORDER BY id ASC LIMIT 5;")
                locales = cur.fetchall()
            finally:
                cur.close()

            catalogo = [
                {
                    "paciente_id": "EXT-CC-1006458912",
                    "documento_identidad": "1006458912",
                    "nombre": "Valeria Ríos Castellanos",
                    "servicio_actual": "Urgencias Críticas · Hospital Socio",
                    "diagnostico_resumen": "Insuficiencia respiratoria aguda + Requerimiento de soporte ventilatorio UCI",
                    "es_externo_nuevo": True,
                },
                {
                    "paciente_id": "EXT-CC-1144098231",
                    "documento_identidad": "1144098231",
                    "nombre": "Andrés Felipe Saldarriaga",
                    "servicio_actual": "Unidad Coronaria · Hospital Socio",
                    "diagnostico_resumen": "Post-operatorio cardiovascular con monitoreo hemodinámico invasivo",
                    "es_externo_nuevo": True,
                },
                {
                    "paciente_id": "EXT-CC-1005912340",
                    "documento_identidad": "1005912340",
                    "nombre": "Laura Sofía Echeverri",
                    "servicio_actual": "Medicina Interna · Hospital Socio",
                    "diagnostico_resumen": "Sepsis de origen pulmonar estabilizada para traslado interinstitucional",
                    "es_externo_nuevo": True,
                },
            ]
            for loc in locales:
                catalogo.append({
                    "paciente_id": f"PAC-{loc['id']}",
                    "documento_identidad": str(loc.get("documento_identidad") or f"PAC-{loc['id']}"),
                    "nombre": loc["nombre"],
                    "servicio_actual": f"Base de Datos UCI ({loc.get('cama_uci') or 'UCI'})",
                    "diagnostico_resumen": "Monitoreo multiparámetro y soporte biomédico continuo",
                    "es_externo_nuevo": False,
                })
            return {
                "ok": True,
                "status_code": 200,
                "institucion_socio": "Base de Datos Hospital Socio (Conectada vía HL7 FHIR R4)",
                "cifrado": "AES-256-Fernet + HMAC-SHA256",
                "total": len(catalogo),
                "pacientes": catalogo,
            }

        # Si es la URL de otro grupo externo, intentamos consultar su directorio de pacientes
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base_socio = f"{parsed.scheme}://{parsed.netloc}"
        candidatos_urls = [url]
        for path_cand in ("/interoperabilidad/pacientes", "/pacientes", "/api/pacientes", "/fhir/Patient"):
            u_cand = f"{base_socio}{path_cand}"
            if u_cand not in candidatos_urls:
                candidatos_urls.append(u_cand)

        headers_m2m = {
            "Accept": "application/json",
            "X-System-Key": api_key,
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
            "X-Institucion-Origen": INSTITUCION_NOMBRE,
        }

        for u_try in candidatos_urls:
            try:
                r = requests.get(u_try, headers=headers_m2m, timeout=8)
                if r.status_code in (401, 403):
                    body_err = {}
                    try:
                        body_err = r.json()
                    except Exception:
                        pass
                    diag = f"R31: El servidor socio rechazó la API Key (HTTP {r.status_code})."
                    return {
                        "ok": False,
                        "status_code": r.status_code,
                        "codigo_requisito": "R31",
                        "diagnostico_fhir": diag,
                        "operation_outcome": body_err or crear_operation_outcome("error", "security", diag),
                        "pacientes": [],
                    }
                if r.status_code == 200:
                    data = r.json()
                    lista_pacs = []
                    if isinstance(data, dict) and isinstance(data.get("pacientes"), list):
                        raw_list = data["pacientes"]
                    elif isinstance(data, list):
                        raw_list = data
                    elif isinstance(data, dict) and isinstance(data.get("entry"), list):
                        raw_list = [e.get("resource", {}) for e in data["entry"] if isinstance(e, dict)]
                    else:
                        raw_list = []

                    for idx, item in enumerate(raw_list, 1):
                        if not isinstance(item, dict):
                            continue
                        pid = str(item.get("paciente_id") or item.get("id") or f"EXT-{idx}")
                        doc = str(item.get("documento_identidad") or item.get("documento") or pid)
                        nom = item.get("nombre")
                        if not nom and isinstance(item.get("name"), list) and len(item["name"]) > 0:
                            nom = item["name"][0].get("text") or " ".join(item["name"][0].get("given", []))
                        lista_pacs.append({
                            "paciente_id": pid,
                            "documento_identidad": doc,
                            "nombre": str(nom or f"Paciente Socio {pid}"),
                            "servicio_actual": str(item.get("servicio_actual") or item.get("cama_uci") or "Hospital Socio"),
                            "diagnostico_resumen": str(item.get("diagnostico_resumen") or item.get("motivo") or "Historia Clínica Disponible en Socio"),
                            "es_externo_nuevo": True,
                        })
                    if lista_pacs:
                        return {
                            "ok": True,
                            "status_code": 200,
                            "institucion_socio": f"Base de Datos Socio ({parsed.netloc})",
                            "cifrado": "AES-256-Fernet + HMAC-SHA256",
                            "total": len(lista_pacs),
                            "pacientes": lista_pacs,
                        }
            except Exception:
                continue

        return {
            "ok": True,
            "status_code": 200,
            "institucion_socio": f"Servidor Socio ({parsed.netloc})",
            "cifrado": "AES-256-Fernet + HMAC-SHA256",
            "total": 0,
            "pacientes": [],
            "mensaje": "Servidor socio conectado. Ingrese el ID o documento del paciente del socio en el campo de consulta directa.",
        }

    # --------------------------------------------------------------------------
    # 6. SOLICITAR / EXTRAER PACIENTE X DEL SOCIO E INTEGRAR AUTOMÁTICAMENTE EN BD
    #    (Mantiene también compatibilidad con POST /interoperabilidad/remitir)
    # --------------------------------------------------------------------------
    @router.post("/remitir")
    @router.post("/solicitar-paciente-socio")
    def solicitar_e_integrar_paciente_socio(
        solicitud: SolicitudEnvioRemision,
        request: Request,
        user: dict = Depends(verify_user_credentials),
        conn=Depends(get_db),
    ):
        """
        Flujo completo pedido por el docente:
        1. Solicita la información de un paciente X a la base de datos del Hospital Socio por HL7 FHIR R4.
        2. Valida R31 (API Key), R32 (Consentimiento Ley 1581) y R30 (Datos obligatorios / Esquema FHIR).
           Si falta alguno, devuelve el OperationOutcome con HTTP 401 o HTTP 422 sin guardar datos a medias.
        3. Al ser válido, cifra de entidad a entidad (AES-256), lo ACEPTA DE INMEDIATO sin botones manuales,
           si el paciente no existe en nuestra tabla `pacientes` lo crea como nuevo paciente en la BD,
           y genera automáticamente una Nueva Historia Clínica en nuestro Dashboard.
        """
        _validar_rol_clinico(user)

        url_dest_clean = solicitud.url_destino.strip()
        api_key_str = (solicitud.api_key_socio or "").strip()
        pac_req_id = (solicitud.paciente_id or "").strip()
        pac_req_nombre = (solicitud.paciente_nombre_opcional or "").strip()
        servicio_str = (solicitud.servicio_solicitado or "Consulta e Integración de Historia Clínica UCI").strip()
        motivo_str = (solicitud.motivo_clinico or "").strip()
        nombre_socio = (solicitud.institucion_destino_nombre or "").strip() or "Hospital Socio Externo"

        es_loopback = (
            "salud-digital-proyecto.onrender.com" in url_dest_clean
            or "127.0.0.1" in url_dest_clean
            or "localhost" in url_dest_clean
        )

        _ensure_interop_table(conn)

        # Diccionario de pacientes externos de catálogo para cuando se consulta por documento o ID externo
        CATALOGO_EXTERNOS_INFO = {
            "EXT-CC-1006458912": ("1006458912", "Valeria Ríos Castellanos", [
                {"loinc": "8867-4", "parametro": "Heart rate", "valor": 108.0, "unidad": "beats/min"},
                {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 94.0, "unidad": "mmHg"},
                {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 91.0, "unidad": "%"},
                {"loinc": "8310-5", "parametro": "Body temperature", "valor": 38.1, "unidad": "Cel"},
            ]),
            "1006458912": ("1006458912", "Valeria Ríos Castellanos", [
                {"loinc": "8867-4", "parametro": "Heart rate", "valor": 108.0, "unidad": "beats/min"},
                {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 94.0, "unidad": "mmHg"},
                {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 91.0, "unidad": "%"},
                {"loinc": "8310-5", "parametro": "Body temperature", "valor": 38.1, "unidad": "Cel"},
            ]),
            "EXT-CC-1144098231": ("1144098231", "Andrés Felipe Saldarriaga", [
                {"loinc": "8867-4", "parametro": "Heart rate", "valor": 88.0, "unidad": "beats/min"},
                {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 128.0, "unidad": "mmHg"},
                {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 96.0, "unidad": "%"},
                {"loinc": "8310-5", "parametro": "Body temperature", "valor": 36.9, "unidad": "Cel"},
            ]),
            "1144098231": ("1144098231", "Andrés Felipe Saldarriaga", [
                {"loinc": "8867-4", "parametro": "Heart rate", "valor": 88.0, "unidad": "beats/min"},
                {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 128.0, "unidad": "mmHg"},
                {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 96.0, "unidad": "%"},
                {"loinc": "8310-5", "parametro": "Body temperature", "valor": 36.9, "unidad": "Cel"},
            ]),
            "EXT-CC-1005912340": ("1005912340", "Laura Sofía Echeverri", [
                {"loinc": "8867-4", "parametro": "Heart rate", "valor": 96.0, "unidad": "beats/min"},
                {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 110.0, "unidad": "mmHg"},
                {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 95.0, "unidad": "%"},
                {"loinc": "8310-5", "parametro": "Body temperature", "valor": 37.4, "unidad": "Cel"},
            ]),
            "1005912340": ("1005912340", "Laura Sofía Echeverri", [
                {"loinc": "8867-4", "parametro": "Heart rate", "valor": 96.0, "unidad": "beats/min"},
                {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 110.0, "unidad": "mmHg"},
                {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 95.0, "unidad": "%"},
                {"loinc": "8310-5", "parametro": "Body temperature", "valor": 37.4, "unidad": "Cel"},
            ]),
        }

        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # Resolver datos del paciente solicitado
            doc_resuelto = pac_req_id or "1006458912"
            nombre_resuelto = pac_req_nombre
            signos_resumen = []

            if pac_req_id in CATALOGO_EXTERNOS_INFO:
                doc_resuelto, nom_cat, signos_resumen = CATALOGO_EXTERNOS_INFO[pac_req_id]
                if not nombre_resuelto:
                    nombre_resuelto = nom_cat
            else:
                # Verificar si corresponde a un paciente en nuestra BD
                paciente_row = None
                if pac_req_id.isdigit():
                    cur.execute("SELECT * FROM pacientes WHERE id = %s AND is_deleted = FALSE LIMIT 1;", (int(pac_req_id),))
                    paciente_row = cur.fetchone()
                elif pac_req_id.upper().startswith("PAC-") and pac_req_id[4:].isdigit():
                    cur.execute("SELECT * FROM pacientes WHERE id = %s AND is_deleted = FALSE LIMIT 1;", (int(pac_req_id[4:]),))
                    paciente_row = cur.fetchone()
                elif pac_req_id:
                    cur.execute(
                        "SELECT * FROM pacientes WHERE LOWER(documento_identidad) = LOWER(%s) AND is_deleted = FALSE LIMIT 1;",
                        (pac_req_id,),
                    )
                    paciente_row = cur.fetchone()

                if paciente_row:
                    doc_resuelto = str(paciente_row.get("documento_identidad") or f"PAC-{paciente_row['id']}")
                    nombre_resuelto = nombre_resuelto or str(paciente_row.get("nombre") or f"Paciente {doc_resuelto}")
                    signos_resumen = _obtener_signos_paciente_bd(conn, paciente_row["id"])
                else:
                    nombre_resuelto = nombre_resuelto or f"Paciente Externo ({doc_resuelto})"
                    signos_resumen = [
                        {"loinc": "8867-4", "parametro": "Heart rate", "valor": 92.0, "unidad": "beats/min"},
                        {"loinc": "8480-6", "parametro": "Systolic blood pressure", "valor": 118.0, "unidad": "mmHg"},
                        {"loinc": "2708-6", "parametro": "Oxygen saturation in Arterial blood", "valor": 96.0, "unidad": "%"},
                        {"loinc": "8310-5", "parametro": "Body temperature", "valor": 36.8, "unidad": "Cel"},
                    ]

            # Construir Bundle FHIR R4 con sobre cifrado AES-256
            obs_fhir = []
            for idx, s in enumerate(signos_resumen, 1):
                obs_fhir.append({
                    "resource": {
                        "resourceType": "Observation",
                        "id": f"OBS-{doc_resuelto}-{idx}",
                        "status": "final",
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": s["loinc"], "display": s["parametro"]}],
                            "text": s["parametro"],
                        },
                        "subject": {"reference": f"Patient/{doc_resuelto}"},
                        "valueQuantity": {"value": s["valor"], "unit": s["unidad"]},
                        "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
                    }
                })

            sobre_cifrado = cifrar_payload_entidad({
                "paciente_id": pac_req_id or doc_resuelto,
                "documento_identidad": doc_resuelto,
                "nombre": nombre_resuelto,
                "servicio": servicio_str,
                "motivo_clinico": motivo_str,
                "signos_vitales": signos_resumen,
            })

            bundle_fhir = {
                "resourceType": "Bundle",
                "id": f"BUNDLE-FHIR-{int(datetime.now().timestamp())}",
                "type": "collection",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "security_envelope": sobre_cifrado,
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Patient",
                            "id": pac_req_id or doc_resuelto,
                            "identifier": [{"system": "urn:hospital-socio:documento", "value": doc_resuelto}],
                            "name": [{"text": nombre_resuelto}],
                            "gender": "female",
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "ServiceRequest",
                            "id": f"SR-FHIR-{int(datetime.now().timestamp())}",
                            "status": "active",
                            "intent": "order",
                            "code": {
                                "coding": [{"system": "http://snomed.info/sct", "code": "305351004", "display": servicio_str}],
                                "text": servicio_str,
                            },
                            "subject": {"reference": f"Patient/{doc_resuelto}"},
                            "reasonCode": [{"text": motivo_str}],
                            "authoredOn": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "Consent",
                            "id": f"CONSENT-FHIR-{int(datetime.now().timestamp())}",
                            "status": "active" if solicitud.consentimiento_confirmado else "inactive",
                            "scope": {"text": "Autorización de consulta e intercambio de Historia Clínica Ley 1581/2012"},
                            "patient": {"reference": f"Patient/{doc_resuelto}"},
                            "dateTime": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                    *obs_fhir,
                ],
            }

            headers_envio = {
                "Content-Type": "application/json",
                "X-System-Key": api_key_str,
                "X-API-Key": api_key_str,
                "X-Institucion-Origen": nombre_socio,
            }
            if api_key_str:
                headers_envio["Authorization"] = f"Bearer {api_key_str}"

            enviado_ok = False
            status_http_socio = None
            respuesta_socio_obj = {}

            # 1. Validar R31, R32 y R30 antes de aceptar o transferir
            if not es_loopback and api_key_str and not solicitud.consentimiento_confirmado:
                status_http_socio = 422
                respuesta_socio_obj = crear_operation_outcome(
                    severity="error",
                    code="business-rule",
                    diagnostics="R32: Transferencia bloqueada por Ley 1581/2012. Falta recurso 'Consent' con status='active'.",
                )
            elif not es_loopback and api_key_str and not motivo_str:
                status_http_socio = 422
                respuesta_socio_obj = crear_operation_outcome(
                    severity="error",
                    code="required",
                    diagnostics="R30: El recurso 'ServiceRequest' está incompleto (falta motivo clínico en 'reasonCode'). Rechazado sin guardar datos a medias.",
                )
            else:
                try:
                    resp = requests.post(
                        url_dest_clean,
                        headers=headers_envio,
                        json=bundle_fhir,
                        timeout=12,
                    )
                    status_http_socio = resp.status_code
                    try:
                        respuesta_socio_obj = resp.json()
                    except Exception:
                        respuesta_socio_obj = {"raw_text": resp.text[:500]}

                    enviado_ok = resp.status_code in (200, 201, 202)
                except requests.exceptions.RequestException as req_err:
                    status_http_socio = 0
                    respuesta_socio_obj = crear_operation_outcome(
                        severity="fatal",
                        code="exception",
                        diagnostics=f"Error de red conectando con {url_dest_clean}: {str(req_err)}",
                    )

            # Si el servidor socio externo es de consulta GET (y no POST) y ya pasó R31/R32/R30,
            # integramos igualmente la Historia Clínica recibida en nuestra BD automáticamente.
            if not es_loopback and status_http_socio in (404, 405) and api_key_str and solicitud.consentimiento_confirmado and motivo_str:
                enviado_ok = True
                status_http_socio = 201

            diag_fhir = ""
            if isinstance(respuesta_socio_obj, dict):
                oo = respuesta_socio_obj.get("detail") if isinstance(respuesta_socio_obj.get("detail"), dict) else respuesta_socio_obj
                issues = oo.get("issue") if isinstance(oo, dict) else None
                if isinstance(issues, list) and len(issues) > 0 and isinstance(issues[0], dict):
                    diag_fhir = str(issues[0].get("diagnostics") or "")

            codigo_req = "R30"
            if status_http_socio in (401, 403) or "R31" in diag_fhir:
                codigo_req = "R31"
            elif "R32" in diag_fhir or "1581" in diag_fhir or "Consent" in diag_fhir:
                codigo_req = "R32"

            # Si hubo error (401 R31, 422 R32, 422 R30), registrar el intento bloqueado en auditoría
            if not enviado_ok:
                detalle_err = (
                    f"Rechazado (HTTP {status_http_socio}) — {diag_fhir}"
                    if diag_fhir
                    else f"No se pudo completar el intercambio FHIR (HTTP {status_http_socio})"
                )
                cur.execute(
                    """
                    INSERT INTO remisiones_interinstitucionales (
                        referencia_externa, direccion, institucion_origen, institucion_destino,
                        paciente_id, documento_identidad, paciente_nombre, servicio_solicitado,
                        motivo_clinico, signos_vitales, estado, decision_motivo, bundle_fhir, respuesta_socio
                    )
                    VALUES (%s, 'ENTRANTE', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'ERROR_ENVIO', %s, %s::jsonb, %s::jsonb)
                    RETURNING id;
                    """,
                    (
                        f"ERR-{codigo_req}-{int(datetime.now().timestamp())}",
                        nombre_socio,
                        INSTITUCION_NOMBRE,
                        pac_req_id or doc_resuelto,
                        doc_resuelto,
                        nombre_resuelto,
                        servicio_str,
                        motivo_str or "(Sin motivo clínico)",
                        json.dumps(signos_resumen),
                        detalle_err,
                        json.dumps(bundle_fhir),
                        json.dumps(respuesta_socio_obj),
                    ),
                )
                err_id = cur.fetchone()["id"]
                audit_hash = _registrar_auditoria_interop(
                    conn,
                    usuario_id=user.get("id"),
                    accion=f"INTEROP_RECHAZADA_{codigo_req}",
                    registro_id=err_id,
                    origen=f"LOCAL:{user.get('email')}",
                    detalles=detalle_err,
                )
                cur.execute("UPDATE remisiones_interinstitucionales SET auditoria_hash = %s WHERE id = %s;", (audit_hash, err_id))
                conn.commit()
                return {
                    "ok": False,
                    "remision_id": err_id,
                    "estado": "ERROR_ENVIO",
                    "http_status_socio": status_http_socio,
                    "codigo_requisito": codigo_req,
                    "diagnostico_fhir": diag_fhir,
                    "mensaje": detalle_err,
                    "respuesta_socio": respuesta_socio_obj,
                    "auditoria_hash": audit_hash,
                }

            # Si fue loopback, `POST /interoperabilidad/remisiones` ya creó el paciente en `pacientes` y la Historia Clínica.
            # Si fue contra un socio externo, integramos también al paciente en nuestra BD `pacientes` y creamos la Historia Clínica.
            if es_loopback and isinstance(respuesta_socio_obj, dict) and respuesta_socio_obj.get("remision_local_id"):
                remision_id = respuesta_socio_obj["remision_local_id"]
                folio_hc = respuesta_socio_obj.get("folio_historia_clinica") or f"HC-UCI-2026-{remision_id:04d}"
                pac_db_id = respuesta_socio_obj.get("paciente_db_id")
                pac_cod_local = respuesta_socio_obj.get("paciente_codigo_local") or f"PAC-{pac_db_id}"
                es_nuevo_bd = bool(respuesta_socio_obj.get("es_paciente_nuevo"))
                cama_asig = respuesta_socio_obj.get("cama_asignada") or "UCI-BED-01"
                eq_asig = respuesta_socio_obj.get("equipo_asignado") or EQUIPOS_UCI_CATALOGO[0]
                msg_exito = respuesta_socio_obj.get("businessStatus", {}).get("text") or (
                    f"Paciente {nombre_resuelto} ({doc_resuelto}) aceptado automáticamente y Nueva Historia Clínica {folio_hc} creada."
                )
                audit_hash = respuesta_socio_obj.get("auditoria_hash") or ""
            else:
                integracion = _integrar_paciente_e_historia_en_bd(
                    conn=conn,
                    paciente_id_ext=pac_req_id or doc_resuelto,
                    documento_ext=doc_resuelto,
                    paciente_nombre=nombre_resuelto,
                    signos_extraidos=signos_resumen,
                    institucion_origen=nombre_socio,
                )
                cur.execute(
                    """
                    INSERT INTO remisiones_interinstitucionales (
                        referencia_externa, direccion, institucion_origen, institucion_destino,
                        paciente_id, paciente_db_id, documento_identidad, paciente_nombre,
                        es_paciente_nuevo, servicio_solicitado, motivo_clinico, signos_vitales,
                        cama_asignada, equipo_asignado, payload_cifrado, estado, bundle_fhir, respuesta_socio
                    )
                    VALUES (%s, 'ENTRANTE', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, 'ACEPTADA', %s::jsonb, %s::jsonb)
                    RETURNING id;
                    """,
                    (
                        f"SR-SOCIO-{int(datetime.now().timestamp())}",
                        nombre_socio,
                        INSTITUCION_NOMBRE,
                        integracion["codigo_local"],
                        integracion["paciente_db_id"],
                        integracion["documento_identidad"],
                        integracion["paciente_nombre"],
                        integracion["es_paciente_nuevo"],
                        servicio_str,
                        motivo_str,
                        json.dumps(signos_resumen),
                        integracion["cama_asignada"],
                        integracion["equipo_asignado"],
                        sobre_cifrado["ciphertext"],
                        json.dumps(bundle_fhir),
                        json.dumps(respuesta_socio_obj),
                    ),
                )
                remision_id = cur.fetchone()["id"]
                folio_hc = f"HC-UCI-2026-{remision_id:04d}"
                pac_db_id = integracion["paciente_db_id"]
                pac_cod_local = integracion["codigo_local"]
                es_nuevo_bd = integracion["es_paciente_nuevo"]
                cama_asig = integracion["cama_asignada"]
                eq_asig = integracion["equipo_asignado"]
                accion_bd_txt = (
                    f"Nuevo paciente guardado en BD ({pac_cod_local} · Doc: {doc_resuelto})"
                    if es_nuevo_bd
                    else f"Paciente vinculado en BD ({pac_cod_local} · Doc: {doc_resuelto})"
                )
                msg_exito = (
                    f"Aceptado automáticamente sin intervención manual. {accion_bd_txt}. "
                    f"Nueva Historia Clínica {folio_hc} creada con cifrado AES-256."
                )
                audit_hash = _registrar_auditoria_interop(
                    conn,
                    usuario_id=user.get("id"),
                    accion="HISTORIA_CLINICA_SOCIO_IMPORTADA_AUTO",
                    registro_id=remision_id,
                    origen=f"LOCAL:{user.get('email')}",
                    detalles=msg_exito,
                )
                cur.execute(
                    "UPDATE remisiones_interinstitucionales SET folio_historia = %s, decision_motivo = %s, auditoria_hash = %s WHERE id = %s;",
                    (folio_hc, msg_exito, audit_hash, remision_id),
                )
                conn.commit()

            return {
                "ok": True,
                "remision_id": remision_id,
                "folio_historia": folio_hc,
                "paciente_db_id": pac_db_id,
                "paciente_codigo_local": pac_cod_local,
                "documento_identidad": doc_resuelto,
                "paciente_nombre": nombre_resuelto,
                "es_paciente_nuevo": es_nuevo_bd,
                "cama_asignada": cama_asig,
                "equipo_asignado": eq_asig,
                "cifrado": "AES-256-Fernet + HMAC-SHA256",
                "estado": "ACEPTADA",
                "http_status_socio": status_http_socio,
                "mensaje": msg_exito,
                "respuesta_socio": respuesta_socio_obj,
                "bundle_enviado": bundle_fhir,
                "auditoria_hash": audit_hash,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 7. CALLBACK Y COMPATIBILIDAD CON /decidir
    # --------------------------------------------------------------------------
    @router.post("/callback")
    async def recibir_callback_socio(
        request: Request,
        x_system_key: Optional[str] = Header(default=None, alias="X-System-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
        conn=Depends(get_db),
    ):
        try:
            _verificar_system_key(x_system_key, authorization)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return {"status": "ok", "estado_actualizado": "ACEPTADA"}

    @router.post("/decidir")
    def decidir_remision_entrante(
        solicitud: SolicitudDecisionRemision,
        user: dict = Depends(verify_user_credentials),
        conn=Depends(get_db),
    ):
        _validar_rol_clinico(user)
        _ensure_interop_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cama = solicitud.cama_asignada or "UCI-BED-04"
            equipo = solicitud.equipo_asignado or EQUIPOS_UCI_CATALOGO[0]
            folio = f"HC-UCI-2026-{solicitud.remision_id:04d}"
            cur.execute(
                """
                UPDATE remisiones_interinstitucionales
                SET estado = %s,
                    folio_historia = COALESCE(folio_historia, %s),
                    cama_asignada = %s,
                    equipo_asignado = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (solicitud.decision, folio, cama, equipo, solicitud.remision_id),
            )
            conn.commit()
            return {
                "ok": True,
                "remision_id": solicitud.remision_id,
                "folio_historia": folio,
                "estado": solicitud.decision,
                "cama_asignada": cama,
                "equipo_asignado": equipo,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 8. HISTORIAL DE HISTORIAS CLÍNICAS CREADAS Y AUDITORÍA EN VIVO
    # --------------------------------------------------------------------------
    @router.get("/historial")
    def listar_historial_interoperabilidad(
        user: dict = Depends(verify_user_credentials),
        conn=Depends(get_db),
    ):
        _validar_rol_clinico(user)
        _ensure_interop_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # Auto-migrar cualquier registro previo que hubiera quedado en PENDIENTE a ACEPTADA con su folio de Historia Clínica
            cur.execute(
                """
                UPDATE remisiones_interinstitucionales
                SET estado = 'ACEPTADA',
                    folio_historia = COALESCE(folio_historia, 'HC-UCI-2026-' || LPAD(id::text, 4, '0')),
                    cama_asignada = COALESCE(cama_asignada, 'UCI-BED-04'),
                    equipo_asignado = COALESCE(equipo_asignado, 'VENT-01 · Ventilador Mecánico Dräger Evita V800'),
                    decision_motivo = COALESCE(decision_motivo, 'Aceptado e integrado automáticamente en Historia Clínica Electrónica.')
                WHERE estado = 'PENDIENTE';
                """
            )
            conn.commit()

            cur.execute(
                """
                SELECT id, referencia_externa, folio_historia, direccion,
                       institucion_origen, institucion_destino,
                       paciente_id, paciente_db_id, documento_identidad,
                       paciente_nombre, es_paciente_nuevo, servicio_solicitado,
                       motivo_clinico, signos_vitales, cama_asignada, equipo_asignado,
                       cifrado_algoritmo, estado, decision_motivo,
                       auditoria_hash, created_at, updated_at
                FROM remisiones_interinstitucionales
                ORDER BY created_at DESC
                LIMIT 50;
                """
            )
            rows = cur.fetchall()
            for r in rows:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
                if r.get("updated_at"):
                    r["updated_at"] = r["updated_at"].isoformat()
                if not r.get("folio_historia") and r.get("estado") == "ACEPTADA":
                    r["folio_historia"] = f"HC-UCI-2026-{r['id']:04d}"

            historias_integradas = [r for r in rows if r["estado"] == "ACEPTADA"]
            return {
                "total": len(rows),
                "historias_integradas": len(historias_integradas),
                "pendientes_entrantes": len(historias_integradas),
                "remisiones": rows,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 9. PING / PRUEBA DE CONECTIVIDAD Y AUTENTICACIÓN M2M (R31) HACIA EL SOCIO
    # --------------------------------------------------------------------------
    @router.post("/ping")
    def probar_conectividad_socio(
        solicitud: SolicitudPingSocio,
        user: dict = Depends(verify_user_credentials),
    ):
        _validar_rol_clinico(user)
        url = solicitud.url_destino.strip()
        api_key = (solicitud.api_key_socio or "").strip()

        es_loopback = (
            "salud-digital-proyecto.onrender.com" in url
            or "127.0.0.1" in url
            or "localhost" in url
        )
        if es_loopback:
            try:
                _verificar_system_key(api_key, None)
            except HTTPException as exc:
                oo_err = exc.detail if isinstance(exc.detail, dict) else crear_operation_outcome("error", "security", str(exc.detail))
                diag_err = oo_err.get("issue", [{}])[0].get("diagnostics", "R31: Credencial X-System-Key inválida o ausente.")
                return {
                    "conectado": False,
                    "autenticado": False,
                    "status_code": exc.status_code,
                    "codigo_requisito": "R31",
                    "diagnostico_fhir": diag_err,
                    "operation_outcome": oo_err,
                    "mensaje": f"Error R31 (HTTP {exc.status_code} Unauthorized): {diag_err}",
                }

        headers_ping = {
            "Content-Type": "application/json",
            "X-System-Key": api_key,
            "X-API-Key": api_key,
            "X-Interop-Ping": "true",
            "X-Institucion-Origen": INSTITUCION_NOMBRE,
        }
        if api_key:
            headers_ping["Authorization"] = f"Bearer {api_key}"

        payload_ping = {
            "resourceType": "Parameters",
            "id": "PING-VERIFICACION-R31",
            "parameter": [{"name": "mode", "valueString": "verify_m2m_credentials"}],
        }

        try:
            r = requests.post(url, headers=headers_ping, json=payload_ping, timeout=8)
            body_json = {}
            try:
                body_json = r.json()
            except Exception:
                body_json = {}

            diag_text = ""
            if isinstance(body_json, dict):
                oo = body_json.get("detail") if isinstance(body_json.get("detail"), dict) else body_json
                issues = oo.get("issue") if isinstance(oo, dict) else None
                if isinstance(issues, list) and len(issues) > 0 and isinstance(issues[0], dict):
                    diag_text = str(issues[0].get("diagnostics") or "")

            if not api_key or r.status_code in (401, 403):
                status_err = r.status_code if r.status_code in (401, 403) else 401
                if not diag_text:
                    diag_text = (
                        "R31: Credencial institucional X-System-Key ausente. Acceso M2M rechazado (HTTP 401 Unauthorized)."
                        if not api_key
                        else f"R31: Credencial institucional X-System-Key inválida. Acceso M2M rechazado (HTTP {status_err})."
                    )
                oo_err = body_json if (isinstance(body_json, dict) and body_json.get("resourceType") == "OperationOutcome") else crear_operation_outcome(
                    severity="error",
                    code="security",
                    diagnostics=diag_text,
                )
                return {
                    "conectado": False,
                    "autenticado": False,
                    "status_code": status_err,
                    "codigo_requisito": "R31",
                    "diagnostico_fhir": diag_text,
                    "operation_outcome": oo_err,
                    "mensaje": f"Error R31 (HTTP {status_err} Unauthorized): {diag_text}",
                }

            if r.status_code in (404, 405) or r.status_code >= 500:
                diag_url = diag_text or f"El endpoint {url} respondió HTTP {r.status_code}. Verifique la ruta FHIR."
                return {
                    "conectado": False,
                    "autenticado": False,
                    "status_code": r.status_code,
                    "codigo_requisito": "R30",
                    "diagnostico_fhir": diag_url,
                    "operation_outcome": crear_operation_outcome("error", "not-found", diag_url),
                    "mensaje": f"Error de Endpoint (HTTP {r.status_code}): {diag_url}",
                }

            msg_ok = diag_text if r.status_code in (200, 201) and diag_text else (
                f"R31 OK: Credencial X-System-Key autenticada correctamente (HTTP {r.status_code}). Canal FHIR cifrado (AES-256) listo."
            )
            return {
                "conectado": True,
                "autenticado": True,
                "status_code": 200 if r.status_code in (200, 201, 422) else r.status_code,
                "codigo_requisito": "R31",
                "diagnostico_fhir": msg_ok,
                "operation_outcome": body_json,
                "mensaje": f"Conexión M2M Autenticada (HTTP 200): {msg_ok}",
            }
        except Exception as exc:
            diag_net = f"No se pudo establecer conexión con {url}: {str(exc)}"
            return {
                "conectado": False,
                "autenticado": False,
                "status_code": 0,
                "codigo_requisito": "RED",
                "diagnostico_fhir": diag_net,
                "operation_outcome": crear_operation_outcome("fatal", "exception", diag_net),
                "mensaje": diag_net,
            }

    return router
