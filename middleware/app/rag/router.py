# -*- coding: utf-8 -*-
"""
rag/router.py · Endpoint RAG Biomédico y Agente Clínico (R23, R24, R25)
Salud Digital 2026-2 · Universidad Autónoma de Occidente
Rutas: POST /rag/preguntar, POST /api/rag/preguntar
Control de Acceso RBAC:
 - Médicos y personal biomédico acceden a guías, normas y parámetros del paciente.
 - Rol 'contable' y 'paciente' reciben 403 en consultas clínicas (R24).
 - Rol 'especialista' descontinuado en favor del 'medico' con potestad exclusiva (R10/R24).
 - Detección y neutralización de Prompt Injection con registro de IP/usuario para bloqueo (R25).
"""
import re
import os
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Request, status
from pydantic import BaseModel, Field

try:
    from app.rag.motor import responder
except ImportError:
    from rag.motor import responder

# Roles autorizados para interactuar con el motor RAG clínico y biomédico
ROLES_PERMITIDOS_RAG = {
    "Admin Biomedico",
    "ADMIN_BIOMEDICO",
    "ADMIN",
    "Administrador",
    "admin",
    "Biomedico",
    "biomedico",
    "Ingeniero Biomedico",
    "Medico",
    "medico",
    "ING_PREVENTIVO",
    "ING_CORRECTIVO",
    "Ingeniero Preventivo",
    "Ingeniero Correctivo"
}

# Patrones de defensa contra Inyección de Prompt (R25)
PATRONES_PROMPT_INJECTION = [
    r"(?i)(ignora|olvida)\s+(todas?\s+)?(tus|las)\s+instrucciones",
    r"(?i)ignore\s+(all\s+)?(previous\s+)?instructions",
    r"(?i)(system\s+prompt|prompt\s+del\s+sistema|jailbreak|bypass|dan\s+mode)",
    r"(?i)muestra\s+(todos\s+los\s+)?(pacientes|usuarios|passwords|claves|tokens)",
    r"(?i)(drop\s+table|delete\s+from|insert\s+into|select\s+\*\s+from\s+usuarios)",
    r"(?i)revela\s+(el\s+)?(prompt|secreto|llave|token)",
    r"(?i)actua\s+como\s+(un\s+)?hacker",
]

# Pacientes demo con telemetría de soporte vital seudonimizados (R12 / R24)
PACIENTES_CLINICOS = {
    1: {
        "alias": "PX-989385A8",
        "cama_uci": "CAMA-04 (Cama 1)",
        "edad": 58, "sexo": "F", "peso_kg": 78.5, "talla_m": 1.58, "imc": 31.45,
        "equipos": "Ventilador Hamilton C3 (VENT-01), Monitor Philips IntelliVue MX800 (MON-01), Bomba B. Braun Infusomat Space (BOM-01)",
        "parametros": {
            "presion_arterial": "138/86 mmHg (Media 103 mmHg)",
            "frecuencia_cardiaca": "82 lpm",
            "spo2": "96%",
            "glucosa_ayunas": "182 mg/dL (HbA1c 8.1%)",
            "volumen_corriente_vt": "480 mL (LOINC 76288-0)",
            "peep": "6.0 hPa",
            "tasa_infusion": "25.0 mL/h"
        }
    },
    101: {
        "alias": "PX-989385A8",
        "cama_uci": "CAMA-04",
        "edad": 58, "sexo": "F", "peso_kg": 78.5, "talla_m": 1.58, "imc": 31.45,
        "equipos": "Ventilador Hamilton C3 (VENT-01), Monitor Philips IntelliVue MX800 (MON-01), Bomba B. Braun Infusomat Space (BOM-01)",
        "parametros": {
            "presion_arterial": "138/86 mmHg",
            "frecuencia_cardiaca": "82 lpm",
            "spo2": "96%",
            "glucosa_ayunas": "182 mg/dL",
            "volumen_corriente_vt": "480 mL",
            "peep": "6.0 hPa"
        }
    },
    102: {
        "alias": "PX-696B3E9F",
        "cama_uci": "CAMA-12",
        "edad": 41, "sexo": "M", "peso_kg": 85.0, "talla_m": 1.76, "imc": 27.4,
        "equipos": "Ventilador Dräger Evita V800 (VENT-02), Monitor Mindray BeneVision N17 (MON-02)",
        "parametros": {
            "presion_arterial": "124/80 mmHg",
            "frecuencia_cardiaca": "74 lpm",
            "spo2": "98%",
            "glucosa_ayunas": "104 mg/dL",
            "volumen_corriente_vt": "510 mL",
            "peep": "5.0 hPa"
        }
    }
}

class ConsultaRAG(BaseModel):
    pregunta: str = Field(min_length=3, max_length=1000, description="Pregunta clínica o técnica")
    base_filtro: str | None = Field(default=None, description="Opcional: 'general' o 'especifica'")
    k: int = Field(default=3, ge=1, le=8, description="Número de fuentes a recuperar")
    paciente_id: int | None = Field(default=None, description="ID opcional del paciente para contextualización")

def registrar_incidente_seguridad(usuario: dict, ip: str, texto_sospechoso: str):
    """Guarda en log de seguridad el intento de prompt injection para identificación y bloqueo (R25)."""
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=3)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO login_logs (usuario_id, email, evento, detalle, ip_origen, fecha)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                usuario.get("id"),
                usuario.get("email", "desconocido"),
                "PROMPT_INJECTION_DETECTADO",
                f"Intento malicioso bloqueado (R25): {texto_sospechoso[:180]}",
                ip,
                datetime.now()
            ))
            # Incrementar contador de intentos para bloqueo preventivo
            if usuario.get("id"):
                cur.execute("""
                    UPDATE usuarios
                    SET failed_login_attempts = COALESCE(failed_login_attempts, 0) + 1
                    WHERE id = %s
                """, (usuario.get("id"),))
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass

def get_rag_router(verify_credentials_dep = None):
    router = APIRouter(prefix="/rag", tags=["RAG Biomédico & Agente Clínico (R23-R25)"])

    def validar_acceso_rag(user: dict = Depends(verify_credentials_dep) if verify_credentials_dep else None):
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Autenticación requerida para acceder al Asistente RAG."
            )
        rol = user.get("rol_nombre", "")
        # Regla R24: El rol contable o paciente NO ve datos ni asistente clínico
        if rol.lower() in ("contable", "contable uci", "paciente"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: El perfil '{rol}' no está autorizado para interactuar con datos clínicos o el Asistente RAG (R24)."
            )
        if rol not in ROLES_PERMITIDOS_RAG:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: El perfil '{rol}' no está autorizado para interactuar con el Asistente RAG. Reservado para Médicos y Mantenimiento Biomédico."
            )
        return user

    @router.post("/preguntar")
    async def consultar_asistente_rag(request: Request, body: ConsultaRAG, user: dict = Depends(validar_acceso_rag)):
        """
        Consulta al asistente RAG clínico/biomédico con evidencia de guías clínicas y normas IEC/INVIMA (R23).
        Control de permisos por rol (R24) y protección activa contra prompt injection (R25).
        """
        ip_cliente = request.client.host if request.client else "127.0.0.1"

        # 1. Defensa contra Prompt Injection (R25)
        for patron in PATRONES_PROMPT_INJECTION:
            if re.search(patron, body.pregunta):
                registrar_incidente_seguridad(user, ip_cliente, body.pregunta)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Detección de intento de inyección de prompt o violación de seguridad (R25). Evento neutralizado y usuario/IP registrados para bloqueo preventivo."
                )

        # 2. Detección y vinculación de contexto del paciente si aplica
        contexto_paciente = None
        rol_usuario = user.get("rol_nombre", "medico")

        # Buscar si la pregunta menciona a un paciente o cama
        paciente_objetivo_id = body.paciente_id
        if not paciente_objetivo_id:
            m = re.search(r"paciente\s+(\d+)", body.pregunta, re.I)
            if m:
                paciente_objetivo_id = int(m.group(1))
            elif re.search(r"cama\s*0?4|cama\s*1", body.pregunta, re.I):
                paciente_objetivo_id = 1
            elif re.search(r"cama\s*12", body.pregunta, re.I):
                paciente_objetivo_id = 102

        if paciente_objetivo_id and paciente_objetivo_id in PACIENTES_CLINICOS:
            contexto_paciente = PACIENTES_CLINICOS[paciente_objetivo_id]

        try:
            resultado = responder(
                body.pregunta,
                base_filtro=body.base_filtro,
                k=body.k,
                rol=rol_usuario,
                contexto_paciente=contexto_paciente
            )

            # Formatear lista de fuentes con chunk_id y título para R23
            fuentes_formateadas = []
            for f in resultado.get("fuentes", []):
                fuentes_formateadas.append({
                    "n": f.get("n"),
                    "chunk_id": f.get("chunk_id"),
                    "titulo": f.get("titulo"),
                    "documento": f.get("titulo"),
                    "norma": f.get("norma", ""),
                    "fragmento": f.get("chunk_id")
                })

            return {
                "pregunta": body.pregunta,
                "respuesta": resultado["respuesta"],
                "modo": resultado["modo"],
                "fuentes": fuentes_formateadas,
                "paciente_contextualizado": contexto_paciente.get("alias") if contexto_paciente else None,
                "usuario": {
                    "id": user.get("id"),
                    "nombre": user.get("nombre"),
                    "rol": rol_usuario
                }
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error en motor RAG: {str(e)}")

    @router.get("/estado")
    def estado_rag(user: dict = Depends(validar_acceso_rag)):
        try:
            from app.rag.motor import FRAGMENTOS
        except ImportError:
            from rag.motor import FRAGMENTOS
        return {
            "estado": "operativo",
            "total_fragmentos_indexados": len(FRAGMENTOS),
            "corpus": "Hospital Universitario UCI Digital - 56 Documentos Oficiales + Guías Clínicas",
            "usuario_autorizado": user.get("rol_nombre")
        }

    return router

router = get_rag_router(None)
