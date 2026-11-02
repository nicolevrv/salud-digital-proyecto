# -*- coding: utf-8 -*-
"""
rag/router.py · Endpoint RAG Biomédico para el Hospital Universitario UCI Digital
Ruta: POST /rag/preguntar
Control de Acceso RBAC: Limitado a Ingeniería Biomédica (Preventivo/Correctivo/Admin) y Médicos de UCI.
"""
import os
from fastapi import APIRouter, HTTPException, Depends, status
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
    "Especialista",
    "especialista",
    "ING_PREVENTIVO",
    "ING_CORRECTIVO",
    "Ingeniero Preventivo",
    "Ingeniero Correctivo"
}

class ConsultaRAG(BaseModel):
    pregunta: str = Field(min_length=3, max_length=500, description="Pregunta técnica o clínica sobre normatividad, metrología o manuales de equipos UCI")
    base_filtro: str | None = Field(default=None, description="Opcional: 'general' o 'especifica'")
    k: int = Field(default=3, ge=1, le=8, description="Número de fuentes a recuperar")

def get_rag_router(verify_credentials_dep = None):
    router = APIRouter(prefix="/rag", tags=["RAG Biomédico"])

    def validar_acceso_rag(user: dict = Depends(verify_credentials_dep) if verify_credentials_dep else None):
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Autenticación requerida para acceder al Asistente RAG."
            )
        rol = user.get("rol_nombre", "")
        if rol not in ROLES_PERMITIDOS_RAG:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: El perfil '{rol}' no está autorizado para interactuar con el Asistente RAG. Módulo reservado exclusivamente para Ingeniería Biomédica (Preventivo/Correctivo/Admin) y Médicos de UCI."
            )
        return user

    @router.post("/preguntar")
    def consultar_asistente_rag(body: ConsultaRAG, user: dict = Depends(validar_acceso_rag)):
        """
        Consulta al asistente RAG clínico/biomédico con evidencia de normas IEC, INVIMA y manuales técnicos de UCI.
        Restringido por RBAC a Biomédica, Médicos y Mantenimiento Técnico (Preventivo/Correctivo).
        """
        try:
            resultado = responder(body.pregunta, base_filtro=body.base_filtro, k=body.k)
            return {
                "pregunta": body.pregunta,
                "respuesta": resultado["respuesta"],
                "modo": resultado["modo"],
                "fuentes": resultado["fuentes"],
                "usuario": {
                    "id": user.get("id"),
                    "nombre": user.get("nombre"),
                    "rol": user.get("rol_nombre")
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
            "corpus": "Hospital Universitario UCI Digital - 56 Documentos Oficiales",
            "usuario_autorizado": user.get("rol_nombre")
        }

    return router

# Router por defecto
router = get_rag_router(None)
