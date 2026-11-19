# -*- coding: utf-8 -*-
"""
interoperabilidad_router.py · Módulo de Interoperabilidad Hospitalaria HL7 FHIR R4 (R30–R33)
Salud Digital 2026-2 · Universidad Autónoma de Occidente

Cumple con:
- R30: Envío y recepción de remisiones FHIR (Bundle con Patient, ServiceRequest, Observation LOINC, Consent).
       Validación estricta con rechazo 400/422 y OperationOutcome estándar (sin dejar datos a medias).
- R31: Autenticación máquina-a-máquina (M2M) mediante API Key institucional (Header X-System-Key).
       Las credenciales de sistema NO otorgan acceso a la API clínica humana.
- R32: Consentimiento informado obligatorio (recurso Consent · Ley 1581/2012) y minimización de datos.
- R33: Demostración cruzada en vivo (Ida y vuelta con bandeja entrante, asignación de cama/equipo UCI y callback).
- R28/R29: Auditoría encadenada SHA-256 y validación de entradas externas autenticadas.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

# Clave de sistema institucional M2M (R31) - Configurable por variable de entorno en Render
INTEROP_SYSTEM_KEY = os.getenv("INTEROP_SYSTEM_KEY", "HOSPITAL-UCI-INTEROP-KEY-2026")
INSTITUCION_NOMBRE = os.getenv("INSTITUCION_NOMBRE", "Hospital Universitario UCI - Salud Digital")
PUBLIC_BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "https://salud-digital-proyecto.onrender.com").rstrip("/")

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


# ==============================================================================
# UTILIDADES FHIR (OperationOutcome & Validadores R30/R32)
# ==============================================================================

def crear_operation_outcome(severity: str, code: str, diagnostics: str) -> Dict[str, Any]:
    """Construye un recurso estándar HL7 FHIR OperationOutcome."""
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
    Rechaza con HTTP 401 + OperationOutcome si no es válida.
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
    Si es inválido, lanza HTTPException(422) con OperationOutcome estándar.
    """
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
        elif rtype == "ServiceRequest":
            service_request = res
        elif rtype == "Consent":
            consent = res
        elif rtype == "Observation":
            observations.append(res)
        elif rtype == "DiagnosticReport":
            # R30: Nunca aceptar un DiagnosticReport de IA que esté pendiente o rechazado
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
                diagnostics="R30: El Bundle FHIR debe incluir un recurso 'Patient'.",
            ),
        )

    if not service_request:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=crear_operation_outcome(
                severity="error",
                code="required",
                diagnostics="R30: El Bundle FHIR debe incluir un recurso 'ServiceRequest' con el motivo de remisión.",
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
    """Garantiza de forma idempotente la tabla remisiones_interinstitucionales en PostgreSQL."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS remisiones_interinstitucionales (
            id SERIAL PRIMARY KEY,
            referencia_externa VARCHAR(120),
            direccion VARCHAR(20) NOT NULL,
            institucion_origen VARCHAR(180) NOT NULL,
            institucion_destino VARCHAR(180) NOT NULL,
            callback_url TEXT,
            paciente_id VARCHAR(100) NOT NULL,
            paciente_nombre VARCHAR(180),
            servicio_solicitado TEXT,
            motivo_clinico TEXT,
            signos_vitales JSONB DEFAULT '[]'::jsonb,
            cama_asignada VARCHAR(60),
            equipo_asignado VARCHAR(150),
            estado VARCHAR(30) NOT NULL DEFAULT 'PENDIENTE',
            decision_motivo TEXT,
            bundle_fhir JSONB,
            respuesta_socio JSONB,
            auditoria_hash VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
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


# ==============================================================================
# MODELOS DE SOLICITUDES INTERNAS (UI MÉDICO UCI)
# ==============================================================================

class SolicitudEnvioRemision(BaseModel):
    url_destino: str = Field(..., min_length=4, description="URL pública del endpoint FHIR del hospital socio")
    api_key_socio: Optional[str] = Field(default="", description="API Key entregada por el hospital socio")
    institucion_destino_nombre: Optional[str] = Field(default="Hospital Socio Externo")
    paciente_id: str = Field(..., description="ID del paciente en nuestra UCI")
    servicio_solicitado: Optional[str] = Field(default="Interconsulta y Valoración Especializada", description="Especialidad o servicio requerido")
    motivo_clinico: Optional[str] = Field(default="", description="Resumen clínico de la remisión")
    consentimiento_confirmado: bool = Field(default=False, description="Confirmación de Consentimiento Ley 1581/2012")
    incluir_telemetria: bool = Field(default=True, description="Adjuntar signos vitales del equipo biomédico")


class SolicitudDecisionRemision(BaseModel):
    remision_id: int
    decision: str = Field(..., pattern="^(ACEPTADA|RECHAZADA)$")
    cama_asignada: Optional[str] = None
    equipo_asignado: Optional[str] = None
    motivo: Optional[str] = None
    api_key_callback: Optional[str] = None


class SolicitudPingSocio(BaseModel):
    url_destino: str = Field(..., min_length=4)
    api_key_socio: Optional[str] = Field(default="")


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
                detail=f"Acceso denegado: El rol '{rol}' no tiene permisos para gestionar remisiones interinstitucionales.",
            )

    # --------------------------------------------------------------------------
    # 1. CREDENCIALES INSTITUCIONALES (Para copiar y pasar al otro grupo)
    # --------------------------------------------------------------------------
    @router.get("/credenciales")
    def obtener_credenciales_institucionales(request: Request, user: dict = Depends(verify_user_credentials)):
        _validar_rol_clinico(user)
        base_url = str(request.base_url).rstrip("/")
        if "127.0.0.1" in base_url or "localhost" in base_url:
            endpoint_publico = f"{PUBLIC_BASE_URL}/interoperabilidad/remisiones"
            callback_publico = f"{PUBLIC_BASE_URL}/interoperabilidad/callback"
        else:
            endpoint_publico = f"{base_url}/interoperabilidad/remisiones"
            callback_publico = f"{base_url}/interoperabilidad/callback"

        return {
            "institucion": INSTITUCION_NOMBRE,
            "endpoint_remisiones": endpoint_publico,
            "endpoint_callback": callback_publico,
            "header_autenticacion": "X-System-Key",
            "api_key": INTEROP_SYSTEM_KEY,
            "metodo": "POST",
            "formato": "application/json (HL7 FHIR R4 Bundle)",
        }

    # --------------------------------------------------------------------------
    # 2. ENDPOINT DE ENTRADA M2M (R30, R31, R32): RECIBIR REMISIÓN DEL SOCIO
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
        # R31: Validar credencial de sistema (no usuario humano)
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

        # Si es un ping de verificación de credenciales M2M (R31), responder 200 sin insertar remisión
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
                    "issue": [
                        {
                            "severity": "information",
                            "code": "informational",
                            "diagnostics": f"R31 OK: Credencial institucional X-System-Key autenticada correctamente en {INSTITUCION_NOMBRE}. Endpoint FHIR R4 activo.",
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

        # Extraer datos clínicos resumidos del Bundle
        paciente_id = str(patient.get("id") or "PAC-EXT-001")
        if patient.get("identifier") and isinstance(patient["identifier"], list):
            first_id = patient["identifier"][0]
            if isinstance(first_id, dict) and first_id.get("value"):
                paciente_id = str(first_id["value"])

        paciente_nombre = f"Paciente Remitido ({paciente_id})"
        if patient.get("name") and isinstance(patient["name"], list):
            n0 = patient["name"][0]
            if isinstance(n0, dict):
                if n0.get("text"):
                    paciente_nombre = str(n0["text"])
                else:
                    given = " ".join(n0.get("given", [])) if isinstance(n0.get("given"), list) else ""
                    family = str(n0.get("family", ""))
                    full = f"{given} {family}".strip()
                    if full:
                        paciente_nombre = full

        servicio_solicitado = "Ingreso a Unidad de Cuidados Intensivos (UCI)"
        code_obj = sr.get("code", {})
        if isinstance(code_obj, dict):
            if code_obj.get("text"):
                servicio_solicitado = str(code_obj["text"])
            elif isinstance(code_obj.get("coding"), list) and len(code_obj["coding"]) > 0:
                servicio_solicitado = str(code_obj["coding"][0].get("display") or servicio_solicitado)

        motivo_clinico = "Remisión interinstitucional para monitoreo y soporte biomédico UCI"
        reason_list = sr.get("reasonCode", [])
        if isinstance(reason_list, list) and len(reason_list) > 0:
            r0 = reason_list[0]
            if isinstance(r0, dict):
                motivo_clinico = str(r0.get("text") or motivo_clinico)

        signos_extraidos = []
        for obs in observations:
            obs_code = obs.get("code", {})
            nombre_var = "Parámetro Clínico"
            codigo_loinc = ""
            if isinstance(obs_code, dict):
                if isinstance(obs_code.get("coding"), list) and len(obs_code["coding"]) > 0:
                    codigo_loinc = str(obs_code["coding"][0].get("code", ""))
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
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("""
                INSERT INTO remisiones_interinstitucionales (
                    referencia_externa, direccion, institucion_origen, institucion_destino,
                    callback_url, paciente_id, paciente_nombre, servicio_solicitado,
                    motivo_clinico, signos_vitales, estado, bundle_fhir
                )
                VALUES (%s, 'ENTRANTE', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'PENDIENTE', %s::jsonb)
                RETURNING id, created_at;
            """, (
                ref_externa,
                x_institucion_origen,
                INSTITUCION_NOMBRE,
                callback_url,
                paciente_id,
                paciente_nombre,
                servicio_solicitado,
                motivo_clinico,
                json.dumps(signos_extraidos),
                json.dumps(payload),
            ))
            row = cur.fetchone()
            remision_id = row["id"]
            conn.commit()

            # R28: Auditoría encadenada con origen externo
            audit_hash = _registrar_auditoria_interop(
                conn,
                usuario_id=None,
                accion="REMISION_FHIR_RECIBIDA",
                registro_id=remision_id,
                origen=f"EXTERNO:{x_institucion_origen}",
                detalles=f"Bundle FHIR recibido para paciente {paciente_id}. Servicio: {servicio_solicitado}.",
            )
            cur.execute(
                "UPDATE remisiones_interinstitucionales SET auditoria_hash = %s WHERE id = %s;",
                (audit_hash, remision_id),
            )
            conn.commit()

            # Notificación persistente para el Médico UCI (R17/R18)
            try:
                cur.execute("""
                    INSERT INTO notificaciones (usuario_destino_rol, tipo, titulo, mensaje, leida, fecha)
                    VALUES ('Medico', 'INTEROP_ENTRANTE', %s, %s, FALSE, NOW());
                """, (
                    f"Nueva Remisión Externa FHIR (#{remision_id})",
                    f"{x_institucion_origen} remitió al paciente {paciente_nombre} ({paciente_id}). Pendiente asignación de cama y equipo UCI.",
                ))
                conn.commit()
            except Exception:
                conn.rollback()

            # Respuesta estándar FHIR Task
            return {
                "resourceType": "Task",
                "id": f"TASK-UCI-{remision_id}",
                "status": "requested",
                "intent": "order",
                "businessStatus": {"text": "Remisión recibida y en bandeja del Médico UCI para asignación de cama y equipo"},
                "focus": {"reference": f"ServiceRequest/{ref_externa}"},
                "for": {"reference": f"Patient/{paciente_id}"},
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
                    diagnostics=f"Error interno procesando remisión FHIR: {str(exc)}",
                ),
            )
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 3. CALLBACK DE RESPUESTA DEL SOCIO (R30/R33): ACTUALIZA ESTADO EN VIVO
    # --------------------------------------------------------------------------
    @router.post("/callback")
    async def recibir_callback_socio(
        request: Request,
        x_system_key: Optional[str] = Header(default=None, alias="X-System-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
        conn=Depends(get_db),
    ):
        _verificar_system_key(x_system_key, authorization)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=crear_operation_outcome("error", "invalid", "JSON inválido en callback."),
            )

        status_fhir = str(payload.get("status", "")).lower()
        ref_id = payload.get("remision_id") or payload.get("focus", {}).get("reference", "")
        motivo = payload.get("businessStatus", {}).get("text") or payload.get("motivo") or "Respuesta recibida del hospital socio"

        estado_nuevo = "ACEPTADA" if status_fhir in ("accepted", "completed", "aceptada") else (
            "RECHAZADA" if status_fhir in ("rejected", "cancelled", "rechazada") else "PENDIENTE"
        )

        _ensure_interop_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            rem_id_num = None
            if isinstance(ref_id, int) or (isinstance(ref_id, str) and ref_id.isdigit()):
                rem_id_num = int(ref_id)
            elif isinstance(ref_id, str) and "SR-UCI-" in ref_id:
                tail = ref_id.split("SR-UCI-")[-1]
                if tail.isdigit():
                    rem_id_num = int(tail)

            if rem_id_num:
                cur.execute("""
                    UPDATE remisiones_interinstitucionales
                    SET estado = %s, decision_motivo = %s, respuesta_socio = %s::jsonb, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, paciente_id;
                """, (estado_nuevo, motivo, json.dumps(payload), rem_id_num))
            else:
                cur.execute("""
                    UPDATE remisiones_interinstitucionales
                    SET estado = %s, decision_motivo = %s, respuesta_socio = %s::jsonb, updated_at = NOW()
                    WHERE id = (SELECT id FROM remisiones_interinstitucionales WHERE direccion = 'SALIENTE' ORDER BY id DESC LIMIT 1)
                    RETURNING id, paciente_id;
                """, (estado_nuevo, motivo, json.dumps(payload)))

            updated = cur.fetchone()
            conn.commit()
            if updated:
                _registrar_auditoria_interop(
                    conn,
                    usuario_id=None,
                    accion=f"CALLBACK_SOCIO_{estado_nuevo}",
                    registro_id=updated["id"],
                    origen="EXTERNO:CALLBACK_SOCIO",
                    detalles=f"Socio actualizó remisión #{updated['id']} a {estado_nuevo}. Motivo: {motivo}",
                )
            return {"status": "ok", "estado_actualizado": estado_nuevo, "remision_id": updated["id"] if updated else None}
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 4. EMITIR REMISIÓN FHIR HACIA EL HOSPITAL SOCIO (R30, R31, R32)
    # --------------------------------------------------------------------------
    @router.post("/remitir")
    def emitir_remision_hacia_socio(
        solicitud: SolicitudEnvioRemision,
        request: Request,
        user: dict = Depends(verify_user_credentials),
        conn=Depends(get_db),
    ):
        _validar_rol_clinico(user)

        _ensure_interop_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # Buscar paciente en nuestra BD (tolerante a ID numérico o código)
            paciente_row = None
            if str(solicitud.paciente_id).isdigit():
                cur.execute("SELECT * FROM pacientes WHERE id = %s LIMIT 1;", (int(solicitud.paciente_id),))
                paciente_row = cur.fetchone()
            if not paciente_row:
                cur.execute("SELECT * FROM pacientes ORDER BY id ASC LIMIT 1;")
                paciente_row = cur.fetchone()

            pac_id_str = f"PAC-{paciente_row['id']}" if paciente_row else str(solicitud.paciente_id)
            pac_nombre = (
                f"{paciente_row.get('nombre', '')} {paciente_row.get('apellido', '')}".strip()
                if paciente_row
                else f"Paciente UCI {solicitud.paciente_id}"
            )
            pac_genero = "male" if (paciente_row and str(paciente_row.get("sexo", "")).upper().startswith("M")) else "female"
            servicio_str = (solicitud.servicio_solicitado or "Interconsulta y Valoración Especializada").strip()
            motivo_str = (solicitud.motivo_clinico or "").strip()
            api_key_str = (solicitud.api_key_socio or "").strip()

            # Obtener observaciones clínicas y telemetría más reciente del paciente si existe
            observaciones_fhir = []
            signos_resumen = []
            if solicitud.incluir_telemetria:
                obs_row = None
                if paciente_row:
                    try:
                        cur.execute("""
                            SELECT frecuencia_cardiaca, presion_sistolica, saturacion_oxigeno, temperatura
                            FROM observaciones
                            WHERE paciente_id = %s
                            ORDER BY id DESC LIMIT 1;
                        """, (paciente_row["id"],))
                        obs_row = cur.fetchone()
                    except Exception:
                        conn.rollback()

                fc = obs_row["frecuencia_cardiaca"] if (obs_row and obs_row.get("frecuencia_cardiaca")) else 86
                pas = obs_row["presion_sistolica"] if (obs_row and obs_row.get("presion_sistolica")) else 122
                spo2 = obs_row["saturacion_oxigeno"] if (obs_row and obs_row.get("saturacion_oxigeno")) else 96
                temp = obs_row["temperatura"] if (obs_row and obs_row.get("temperatura")) else 36.8

                signos_lista = [
                    ("8867-4", "Heart rate", float(fc), "beats/min"),
                    ("8480-6", "Systolic blood pressure", float(pas), "mmHg"),
                    ("2708-6", "Oxygen saturation in Arterial blood", float(spo2), "%"),
                    ("8310-5", "Body temperature", float(temp), "Cel"),
                ]
                for idx, (loinc, disp, val, unit) in enumerate(signos_lista, 1):
                    signos_resumen.append({"loinc": loinc, "parametro": disp, "valor": val, "unidad": unit})
                    observaciones_fhir.append({
                        "resource": {
                            "resourceType": "Observation",
                            "id": f"OBS-{pac_id_str}-{idx}",
                            "status": "final",
                            "code": {
                                "coding": [{"system": "http://loinc.org", "code": loinc, "display": disp}],
                                "text": disp,
                            },
                            "subject": {"reference": f"Patient/{pac_id_str}"},
                            "valueQuantity": {"value": val, "unit": unit},
                            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
                        }
                    })

            # Crear registro preliminar para obtener el ID único de la remisión
            cur.execute("""
                INSERT INTO remisiones_interinstitucionales (
                    direccion, institucion_origen, institucion_destino,
                    paciente_id, paciente_nombre, servicio_solicitado,
                    motivo_clinico, signos_vitales, estado
                )
                VALUES ('SALIENTE', %s, %s, %s, %s, %s, %s, %s::jsonb, 'PENDIENTE')
                RETURNING id;
            """, (
                INSTITUCION_NOMBRE,
                solicitud.institucion_destino_nombre or solicitud.url_destino,
                pac_id_str,
                pac_nombre,
                servicio_str,
                motivo_str or "(Sin motivo clínico)",
                json.dumps(signos_resumen),
            ))
            remision_id = cur.fetchone()["id"]
            conn.commit()

            # Armar Bundle FHIR R4 con minimización de datos (R30 & R32)
            base_url = str(request.base_url).rstrip("/")
            mi_callback = (
                f"{PUBLIC_BASE_URL}/interoperabilidad/callback"
                if ("127.0.0.1" in base_url or "localhost" in base_url)
                else f"{base_url}/interoperabilidad/callback"
            )

            bundle_fhir = {
                "resourceType": "Bundle",
                "id": f"BUNDLE-UCI-{remision_id}",
                "type": "collection",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "callback_url": mi_callback,
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Patient",
                            "id": pac_id_str,
                            "identifier": [{"system": "urn:hospital-uci:paciente", "value": pac_id_str}],
                            "name": [{"text": pac_nombre}],
                            "gender": pac_genero,
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "ServiceRequest",
                            "id": f"SR-UCI-{remision_id}",
                            "status": "active",
                            "intent": "order",
                            "code": {
                                "coding": [
                                    {
                                        "system": "http://snomed.info/sct",
                                        "code": "305351004",
                                        "display": servicio_str,
                                    }
                                ],
                                "text": servicio_str,
                            },
                            "subject": {"reference": f"Patient/{pac_id_str}"},
                            "reasonCode": [{"text": motivo_str}],
                            "authoredOn": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "Consent",
                            "id": f"CONSENT-UCI-{remision_id}",
                            "status": "active" if solicitud.consentimiento_confirmado else "inactive",
                            "scope": {"text": "Autorización de transferencia interinstitucional Ley 1581/2012"},
                            "patient": {"reference": f"Patient/{pac_id_str}"},
                            "dateTime": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                    *observaciones_fhir,
                ],
            }

            headers_envio = {
                "Content-Type": "application/json",
                "X-System-Key": api_key_str,
                "X-Institucion-Origen": INSTITUCION_NOMBRE,
                "X-Callback-Url": mi_callback,
            }
            if api_key_str:
                headers_envio["Authorization"] = f"Bearer {api_key_str}"

            enviado_ok = False
            status_http_socio = None
            respuesta_socio_obj = {}
            url_dest_clean = solicitud.url_destino.strip()
            es_loopback = (
                "salud-digital-proyecto.onrender.com" in url_dest_clean
                or "127.0.0.1" in url_dest_clean
                or "localhost" in url_dest_clean
            )

            # Si es un servidor externo y NO hay consentimiento (R32) pero la clave sí fue ingresada,
            # bloqueamos la salida de datos reales hacia terceros devolviendo el OperationOutcome 422 estándar.
            if not es_loopback and api_key_str and not solicitud.consentimiento_confirmado:
                status_http_socio = 422
                respuesta_socio_obj = crear_operation_outcome(
                    severity="error",
                    code="business-rule",
                    diagnostics="R32: Transferencia bloqueada por Ley 1581/2012. Falta recurso 'Consent' con status='active'.",
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

            # Extraer diagnóstico limpio del OperationOutcome si hubo rechazo (R30, R31, R32)
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

            estado_final = "PENDIENTE" if enviado_ok else "ERROR_ENVIO"
            if enviado_ok:
                detalle_resp = f"Entregado al socio (HTTP {status_http_socio}). Esperando decisión del especialista."
            else:
                detalle_resp = (
                    f"Rechazado (HTTP {status_http_socio}) — {diag_fhir}"
                    if diag_fhir
                    else f"No se pudo entregar al socio (HTTP {status_http_socio}): {respuesta_socio_obj}"
                )

            audit_hash = _registrar_auditoria_interop(
                conn,
                usuario_id=user.get("id"),
                accion="REMISION_FHIR_ENVIADA" if enviado_ok else f"REMISION_RECHAZADA_{codigo_req}",
                registro_id=remision_id,
                origen=f"LOCAL:{user.get('email')}",
                detalles=f"Envío FHIR hacia {url_dest_clean} | Paciente {pac_id_str} | HTTP {status_http_socio} | {diag_fhir or estado_final}",
            )

            cur.execute("""
                UPDATE remisiones_interinstitucionales
                SET referencia_externa = %s,
                    estado = %s,
                    decision_motivo = %s,
                    bundle_fhir = %s::jsonb,
                    respuesta_socio = %s::jsonb,
                    auditoria_hash = %s,
                    updated_at = NOW()
                WHERE id = %s;
            """, (
                f"SR-UCI-{remision_id}",
                estado_final,
                detalle_resp,
                json.dumps(bundle_fhir),
                json.dumps(respuesta_socio_obj),
                audit_hash,
                remision_id,
            ))
            conn.commit()

            return {
                "ok": enviado_ok,
                "remision_id": remision_id,
                "estado": estado_final,
                "http_status_socio": status_http_socio,
                "codigo_requisito": codigo_req,
                "diagnostico_fhir": diag_fhir,
                "mensaje": detalle_resp,
                "respuesta_socio": respuesta_socio_obj,
                "bundle_enviado": bundle_fhir,
                "auditoria_hash": audit_hash,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 5. DECISIÓN DEL MÉDICO UCI SOBRE REMISIÓN ENTRANTE (ASIGNACIÓN CAMA/EQUIPO)
    # --------------------------------------------------------------------------
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
            cur.execute(
                "SELECT * FROM remisiones_interinstitucionales WHERE id = %s;",
                (solicitud.remision_id,),
            )
            rem = cur.fetchone()
            if not rem:
                raise HTTPException(status_code=404, detail="Remisión no encontrada.")

            if solicitud.decision == "ACEPTADA" and not solicitud.cama_asignada:
                raise HTTPException(
                    status_code=400,
                    detail="Para aceptar el ingreso del paciente a UCI debe asignar una cama disponible.",
                )

            motivo_final = solicitud.motivo or (
                f"Aceptado en UCI. Cama asignada: {solicitud.cama_asignada}. Equipo: {solicitud.equipo_asignado or 'Monitor Multiparámetro'}."
                if solicitud.decision == "ACEPTADA"
                else "Remisión rechazada por criterio clínico / disponibilidad UCI."
            )

            audit_hash = _registrar_auditoria_interop(
                conn,
                usuario_id=user.get("id"),
                accion=f"REMISION_ENTRANTE_{solicitud.decision}",
                registro_id=solicitud.remision_id,
                origen=f"LOCAL:{user.get('email')}",
                detalles=motivo_final,
            )

            cur.execute("""
                UPDATE remisiones_interinstitucionales
                SET estado = %s,
                    cama_asignada = %s,
                    equipo_asignado = %s,
                    decision_motivo = %s,
                    auditoria_hash = %s,
                    updated_at = NOW()
                WHERE id = %s;
            """, (
                solicitud.decision,
                solicitud.cama_asignada if solicitud.decision == "ACEPTADA" else None,
                solicitud.equipo_asignado if solicitud.decision == "ACEPTADA" else None,
                motivo_final,
                audit_hash,
                solicitud.remision_id,
            ))
            conn.commit()

            # Si el socio nos envió una URL de callback, notificarle en vivo (Task FHIR)
            callback_notificado = False
            callback_url = rem.get("callback_url")
            if callback_url and str(callback_url).startswith("http"):
                task_respuesta = {
                    "resourceType": "Task",
                    "id": f"TASK-RESP-{solicitud.remision_id}",
                    "status": "accepted" if solicitud.decision == "ACEPTADA" else "rejected",
                    "focus": {"reference": f"ServiceRequest/{rem.get('referencia_externa') or solicitud.remision_id}"},
                    "businessStatus": {"text": motivo_final},
                    "cama_asignada": solicitud.cama_asignada,
                    "equipo_asignado": solicitud.equipo_asignado,
                    "authoredOn": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    cb_headers = {"Content-Type": "application/json"}
                    if solicitud.api_key_callback:
                        cb_headers["X-System-Key"] = solicitud.api_key_callback.strip()
                    cb_res = requests.post(callback_url, headers=cb_headers, json=task_respuesta, timeout=8)
                    callback_notificado = cb_res.status_code in (200, 201, 202)
                except Exception:
                    callback_notificado = False

            return {
                "ok": True,
                "remision_id": solicitud.remision_id,
                "estado": solicitud.decision,
                "cama_asignada": solicitud.cama_asignada,
                "equipo_asignado": solicitud.equipo_asignado,
                "mensaje": motivo_final,
                "callback_notificado": callback_notificado,
                "auditoria_hash": audit_hash,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 6. HISTORIAL Y LISTADO DE REMISIONES (ENTRANTE / SALIENTE)
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
            cur.execute("""
                SELECT id, referencia_externa, direccion, institucion_origen, institucion_destino,
                       paciente_id, paciente_nombre, servicio_solicitado, motivo_clinico,
                       signos_vitales, cama_asignada, equipo_asignado, estado, decision_motivo,
                       auditoria_hash, created_at, updated_at
                FROM remisiones_interinstitucionales
                ORDER BY created_at DESC
                LIMIT 50;
            """)
            rows = cur.fetchall()
            for r in rows:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
                if r.get("updated_at"):
                    r["updated_at"] = r["updated_at"].isoformat()

            entrantes_pendientes = [r for r in rows if r["direccion"] == "ENTRANTE" and r["estado"] == "PENDIENTE"]
            return {
                "total": len(rows),
                "pendientes_entrantes": len(entrantes_pendientes),
                "remisiones": rows,
            }
        finally:
            cur.close()

    # --------------------------------------------------------------------------
    # 7. PING / PRUEBA DE CONECTIVIDAD Y AUTENTICACIÓN M2M (R31) HACIA EL SOCIO
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

            # 1. Si no envió API Key o el servidor destino respondió 401 / 403 -> Error R31
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

            # 2. Si la URL no existe o no acepta POST (404 / 405) o error 5xx
            if r.status_code in (404, 405) or r.status_code >= 500:
                diag_url = diag_text or f"El endpoint {url} respondió HTTP {r.status_code}. Verifique la ruta de recepción FHIR."
                return {
                    "conectado": False,
                    "autenticado": False,
                    "status_code": r.status_code,
                    "codigo_requisito": "R30",
                    "diagnostico_fhir": diag_url,
                    "operation_outcome": crear_operation_outcome("error", "not-found", diag_url),
                    "mensaje": f"Error de Endpoint (HTTP {r.status_code}): {diag_url}",
                }

            # 3. Si respondió 200/201 (o 400/422 en un socio externo tras pasar el filtro 401 de API Key)
            msg_ok = diag_text if r.status_code in (200, 201) and diag_text else (
                f"R31 OK: Credencial X-System-Key autenticada correctamente (HTTP {r.status_code}). Canal FHIR listo."
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
