# -*- coding: utf-8 -*-
"""
rag/router.py · Endpoint RAG Biomédico para el Hospital Universitario UCI Digital
Ruta: POST /rag/preguntar
"""
import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

try:
    from app.rag.motor import responder
except ImportError:
    from rag.motor import responder

router = APIRouter(prefix="/rag", tags=["RAG Biomédico"])

class ConsultaRAG(BaseModel):
    pregunta: str = Field(min_length=3, max_length=500, description="Pregunta técnica o clínica sobre normatividad, metrología o manuales de equipos UCI")
    base_filtro: str | None = Field(default=None, description="Opcional: 'general' o 'especifica'")
    k: int = Field(default=3, ge=1, le=8, description="Número de fuentes a recuperar")

@router.post("/preguntar")
def consultar_asistente_rag(body: ConsultaRAG):
    """
    Consulta al asistente RAG clínico/biomédico con evidencia de normas IEC, INVIMA y manuales técnicos de UCI.
    """
    try:
        resultado = responder(body.pregunta, base_filtro=body.base_filtro, k=body.k)
        return {
            "pregunta": body.pregunta,
            "respuesta": resultado["respuesta"],
            "modo": resultado["modo"],
            "fuentes": resultado["fuentes"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en motor RAG: {str(e)}")

@router.get("/estado")
def estado_rag():
    try:
        from app.rag.motor import FRAGMENTOS
    except ImportError:
        from rag.motor import FRAGMENTOS
    return {
        "estado": "operativo",
        "total_fragmentos_indexados": len(FRAGMENTOS),
        "corpus": "Hospital Universitario UCI Digital - 56 Documentos Oficiales"
    }
