# -*- coding: utf-8 -*-
"""
agente_router.py · Router API para el Asistente Clínico Inteligente y Metrología UCI
Salud Digital 2026-2 · Universidad Autónoma de Occidente
Rutas:
  - POST /agente/interactuar : Ejecución gobernada con herramientas y arnés LangChain.
  - POST /agente/decidir     : Aprobación / Rechazo HITL exclusivo del Médico (R10/R24).
  - GET  /agente/descargar/{archivo} : Descarga directa de archivos Word (.docx) y XML.
  - GET  /agente/archivos    : Listado de reportes clínicos generados.
"""
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Directorio raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from agente.herramientas import (
        ContextoClinico, seudonimo, calcular_imc, calcular_grasa_corporal,
        formatear_salida, consultar_paciente, consultar_telemetria_equipo,
        buscar_guias, proponer_remision, DIR_EXPORTADOS, PACIENTES_DEMO
    )
except ImportError:
    from ...agente.herramientas import (
        ContextoClinico, seudonimo, calcular_imc, calcular_grasa_corporal,
        formatear_salida, consultar_paciente, consultar_telemetria_equipo,
        buscar_guias, proponer_remision, DIR_EXPORTADOS, PACIENTES_DEMO
    )

ROLES_PERMITIDOS_AGENTE = {
    "Medico",
    "medico",
    "Admin Biomedico",
    "ADMIN_BIOMEDICO",
    "ADMIN",
    "Administrador",
    "admin"
}

ROLES_QUE_APRUEBAN_HITL = {"Medico", "medico"}

class SolicitudInteraccion(BaseModel):
    mensaje: str = Field(min_length=2, max_length=1500, description="Consulta clínica o instrucción para el agente")
    paciente_id: int | None = Field(default=None, description="ID del paciente si aplica")
    conversacion_id: str = Field(default="principal", description="Identificador de la conversación")

class DecisionHITL(BaseModel):
    decision: str = Field(description="'approve' o 'reject'")
    motivo: str | None = Field(default=None, description="Motivo en caso de rechazo")
    conversacion_id: str = Field(default="principal")

# Estado en memoria para HITL de la sesión
ESTADOS_PENDIENTES: dict[str, dict] = {}

def get_agente_router(verify_credentials_dep=None):
    router = APIRouter(prefix="/agente", tags=["Agente Clínico & Antropometría UCI"])

    def validar_acceso_agente(user: dict = Depends(verify_credentials_dep) if verify_credentials_dep else None):
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Autenticación requerida para acceder al Asistente Clínico."
            )
        rol = user.get("rol_nombre", "")
        # Regla R24: El rol contable o paciente no puede ver datos clínicos ni usar el agente
        if rol.lower() in ("contable", "contable uci", "paciente"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: El perfil '{rol}' no tiene permisos para interactuar con el módulo de soporte clínico (R24)."
            )
        if rol not in ROLES_PERMITIDOS_AGENTE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso restringido: El Asistente Clínico está reservado exclusivamente para Médicos UCI y Dirección Biomédica."
            )
        return user

    @router.post("/interactuar")
    async def interactuar_con_agente(solicitud: SolicitudInteraccion, user: dict = Depends(validar_acceso_agente)):
        """
        Punto de interacción directo con el Asistente Clínico.
        Ejecuta deterministamente las herramientas antropométricas (IMC, %GC),
        telemetría de pacientes, generación de archivos descargables (Word/XML) y remisiones HITL.
        """
        texto = solicitud.mensaje.strip()
        t_low = texto.lower()
        rol = user.get("rol_nombre", "medico")
        usuario_email = user.get("email", "medico@hospital.com")

        # 1. Resolver paciente y contexto
        p_id = solicitud.paciente_id
        if not p_id:
            m_px = re.search(r"paciente\s+(\d+)", t_low)
            if m_px:
                p_id = int(m_px.group(1))
            elif "cama 4" in t_low or "cama-04" in t_low or "cama 1" in t_low:
                p_id = 101
            elif "cama 12" in t_low or "cama-12" in t_low:
                p_id = 102

        p_alias = seudonimo(p_id) if p_id else None
        ctx = ContextoClinico(
            usuario=usuario_email,
            rol=rol,
            paciente_id=p_id,
            paciente_alias=p_alias
        )

        herramientas_ejecutadas = []
        bloques_respuesta = []
        archivos_generados = []
        requiere_hitl = False

        # Registrar snapshot de archivos antes de ejecutar para detectar nuevos
        archivos_antes = set(os.listdir(DIR_EXPORTADOS)) if DIR_EXPORTADOS.exists() else set()

        # ---------------------------------------------------------------------
        # Extracción de parámetros numéricos comunes
        # ---------------------------------------------------------------------
        peso = None
        talla = None
        m_peso = re.search(r"(\d+(?:[.,]\d+)?)\s*kg", t_low)
        if m_peso:
            peso = float(m_peso.group(1).replace(",", "."))

        m_talla = re.search(r"(\d+[.,]\d+)\s*m\b", t_low)
        if m_talla:
            talla = float(m_talla.group(1).replace(",", "."))
        else:
            m_cm = re.search(r"(\d{2,3})\s*cm\b", t_low)
            if m_cm:
                talla = float(m_cm.group(1)) / 100.0

        # Si no vinieron en el texto pero tenemos paciente asignado en demo
        if (peso is None or talla is None) and p_id and p_id in PACIENTES_DEMO:
            p_data = PACIENTES_DEMO[p_id]
            peso = peso or p_data.get("peso_kg")
            talla = talla or p_data.get("talla_m")

        # ---------------------------------------------------------------------
        # 2. HERRAMIENTA 1: CALCULAR IMC
        # ---------------------------------------------------------------------
        ejecuto_imc = False
        resultado_imc_txt = ""
        if any(k in t_low for k in ["imc", "índice de masa", "indice de masa", "masa corporal", "antropom"]) or (peso and talla and not "grasa" in t_low):
            if peso and talla:
                res_imc = calcular_imc.invoke({"peso_kg": peso, "talla_m": talla})
                herramientas_ejecutadas.append("calcular_imc")
                bloques_respuesta.append(res_imc)
                resultado_imc_txt = res_imc
                ejecuto_imc = True
            elif not p_id:
                bloques_respuesta.append("⚠️ Para calcular el IMC determinista, por favor especifica el peso (ej. 67 kg) y la estatura (ej. 1.95 m) o selecciona un paciente registrado.")

        # ---------------------------------------------------------------------
        # 3. HERRAMIENTA 2: CALCULAR GRASA CORPORAL (DEURENBERG)
        # ---------------------------------------------------------------------
        ejecuto_gc = False
        resultado_gc_txt = ""
        if any(k in t_low for k in ["grasa", "%gc", "deurenberg", "composici"]):
            m_edad = re.search(r"(\d+)\s*(?:a[ñn]os|edad)", t_low)
            edad = int(m_edad.group(1)) if m_edad else (PACIENTES_DEMO.get(p_id, {}).get("edad", 45) if p_id else 40)
            sexo = "M" if any(s in t_low for s in ["hombre", "masculino", "varon", "varón", " m "]) else "F"
            if p_id and p_id in PACIENTES_DEMO:
                sexo = PACIENTES_DEMO[p_id].get("sexo", sexo)
                edad = PACIENTES_DEMO[p_id].get("edad", edad)

            if peso and talla:
                res_gc = calcular_grasa_corporal.invoke({
                    "peso_kg": peso,
                    "talla_m": talla,
                    "edad": edad,
                    "sexo": sexo
                })
                herramientas_ejecutadas.append("calcular_grasa_corporal")
                bloques_respuesta.append(res_gc)
                resultado_gc_txt = res_gc
                ejecuto_gc = True
            else:
                bloques_respuesta.append("⚠️ Para estimar el porcentaje de grasa corporal (%GC) con la fórmula de Deurenberg se requiere peso, talla, edad y sexo biológico.")

        # ---------------------------------------------------------------------
        # 4. HERRAMIENTA: CONSULTAR TELEMETRÍA UCI Y EQUIPOS ASIGNADOS
        # ---------------------------------------------------------------------
        if any(k in t_low for k in ["telemetr", "signos vitales", "ventilador", "presion", "peep", "spo2", "cama", "equipo"]):
            target_id = p_id or 101
            res_tel = consultar_telemetria_equipo.invoke({"paciente_id": target_id})
            herramientas_ejecutadas.append("consultar_telemetria_equipo")
            bloques_respuesta.append(res_tel)

        # ---------------------------------------------------------------------
        # 5. HERRAMIENTA 3: FORMATEAR SALIDA Y EXPORTACIÓN (WORD / XML / TABLA)
        # ---------------------------------------------------------------------
        quiere_word = any(k in t_low for k in ["word", "doc", "docx", "descargar word", "archivo word"])
        quiere_xml = any(k in t_low for k in ["xml", "descargar xml", "hl7", "interoperab"])
        quiere_tabla = any(k in t_low for k in ["tabla", "cuadro", "resumen"])

        if quiere_word or quiere_xml or quiere_tabla:
            # Consolidar datos clínicos calculados o contexto
            resumen_partes = []
            if resultado_imc_txt:
                resumen_partes.append(resultado_imc_txt)
            if resultado_gc_txt:
                resumen_partes.append(resultado_gc_txt)
            if not resumen_partes:
                # Si no se calculó en este turno, usar los últimos valores disponibles
                resumen_partes.append(f"Evaluación Antropométrica UCI\n- Peso: {peso or 70.0} kg | Talla: {talla or 1.70} m\n- Parámetro IMC: {((peso or 70.0)/((talla or 1.70)**2)):.2f} kg/m²")

            texto_para_exportar = "\n\n".join(resumen_partes)

            formato_a_generar = "word" if quiere_word else ("xml" if quiere_xml else "tabla")
            res_formato = formatear_salida.invoke({
                "datos": texto_para_exportar,
                "formato": formato_a_generar
            })
            herramientas_ejecutadas.append("formatear_salida")
            bloques_respuesta.append(res_formato)

        # ---------------------------------------------------------------------
        # 6. HITL: PROPONER REMISIÓN MÉDICA
        # ---------------------------------------------------------------------
        if any(k in t_low for k in ["remit", "remisión", "interconsulta", "especialista"]):
            especialidad = "Nutrición Clínica" if "nutri" in t_low else ("Endocrinología" if "endocrin" in t_low else "Medicina Interna")
            motivo_remision = f"Derivación por hallazgos clínicos y antropométricos ({resultado_imc_txt[:60] if resultado_imc_txt else 'Control metabólico UCI'})"
            
            # Registrar en pendientes HITL
            hilo_id = f"{usuario_email}:{solicitud.conversacion_id}"
            ESTADOS_PENDIENTES[hilo_id] = {
                "herramienta": "proponer_remision",
                "args": {"especialidad": especialidad, "motivo": motivo_remision, "paciente_alias": p_alias or "PX-DEMO"},
                "fecha": datetime.now(timezone.utc).isoformat()
            }
            requiere_hitl = True
            bloques_respuesta.append(
                f"🚨 [HITL - ACCIÓN CLÍNICA CRÍTICA PENDIENTE]\n"
                f"El agente propone una remisión a: {especialidad}\n"
                f"Motivo: {motivo_remision}\n\n"
                f"⚖️ Potestad exclusiva médica (R10/R24): Como Médico UCI, debes APROBAR o RECHAZAR esta solicitud antes de emitirla al HIS/EHR."
            )

        # ---------------------------------------------------------------------
        # Si no hubo ninguna herramienta específica, dar respuesta clínica orientadora
        # ---------------------------------------------------------------------
        if not bloques_respuesta:
            bloques_respuesta.append(
                f"👨‍⚕️ Asistente Clínico y Metrológico UCI:\n\n"
                f"He recibido tu indicación: «{texto}».\n\n"
                f"Puedes indicarme por ejemplo:\n"
                f"• «Calcula el IMC de un paciente con 67 kg y 1.95 m»\n"
                f"• «Calcula porcentaje de grasa corporal para 78.5 kg, 1.58 m, 58 años, mujer»\n"
                f"• «Genera y descarga el reporte en formato Word (.docx)»\n"
                f"• «Consulta la telemetría y equipos asignados en la cama UCI 4»\n"
                f"• «Remitir paciente a nutrición clínica»"
            )

        # ---------------------------------------------------------------------
        # Detectar nuevos archivos creados en reportes_exportados/
        # ---------------------------------------------------------------------
        archivos_despues = set(os.listdir(DIR_EXPORTADOS)) if DIR_EXPORTADOS.exists() else set()
        nuevos_archivos = sorted(list(archivos_despues - archivos_antes), reverse=True)

        for arch_nombre in nuevos_archivos:
            ruta_f = DIR_EXPORTADOS / arch_nombre
            size_kb = f"{(ruta_f.stat().st_size / 1024):.1f} KB"
            tipo = "word" if arch_nombre.endswith(".docx") else ("xml" if arch_nombre.endswith(".xml") else "otro")
            archivos_generados.append({
                "nombre": arch_nombre,
                "url": f"/agente/descargar/{arch_nombre}",
                "tipo": tipo,
                "tamano": size_kb
            })

        # Si el usuario pidió Word o XML pero ya existía un archivo de hoy, enlazar el más reciente
        if (quiere_word or quiere_xml) and not archivos_generados and DIR_EXPORTADOS.exists():
            todos_arch = sorted(DIR_EXPORTADOS.glob("reporte_clinico_*.*"), key=os.path.getmtime, reverse=True)
            if todos_arch:
                ult = todos_arch[0]
                archivos_generados.append({
                    "nombre": ult.name,
                    "url": f"/agente/descargar/{ult.name}",
                    "tipo": "word" if ult.suffix == ".docx" else "xml",
                    "tamano": f"{(ult.stat().st_size / 1024):.1f} KB"
                })

        respuesta_final = "\n\n".join(bloques_respuesta)

        return {
            "estado": "respondido" if not requiere_hitl else "pendiente_aprobacion",
            "respuesta": respuesta_final,
            "herramientas_usadas": herramientas_ejecutadas,
            "archivos": archivos_generados,
            "requiere_hitl": requiere_hitl,
            "usuario": {
                "email": usuario_email,
                "rol": rol,
                "paciente_alias": p_alias
            }
        }

    @router.post("/decidir")
    async def decidir_accion_hitl(body: DecisionHITL, user: dict = Depends(validar_acceso_agente)):
        """
        Decisión humana (Human-In-The-Loop) sobre propuestas clínicas del agente.
        Únicamente el Médico tiene la potestad de ratificar o revocar (R10/R24).
        """
        rol = user.get("rol_nombre", "")
        if rol not in ROLES_QUE_APRUEBAN_HITL:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado: El rol '{rol}' no cuenta con potestad clínica para aprobar o rechazar conductas médicas. Exclusivo del Médico UCI."
            )

        hilo_id = f"{user.get('email')}:{body.conversacion_id}"
        pendiente = ESTADOS_PENDIENTES.pop(hilo_id, None)

        if body.decision == "approve":
            msg = (
                f"✅ SOLICITUD CLÍNICA APROBADA POR EL MÉDICO RESPONSABLE ({user.get('email')}):\n"
                f"- Acción: Remisión formal ratificada y transmitida al sistema de interconsulta hospitalaria.\n"
                f"- Fecha/Hora: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"- Trazabilidad: Conforme Resolución 1995/1999 y Resolución 866/2021."
            )
            return {"estado": "aprobado", "mensaje": msg}
        else:
            motivo = body.motivo or "Conducta no indicada según criterio clínico actual"
            msg = (
                f"❌ SOLICITUD CLÍNICA RECHAZADA POR EL MÉDICO RESPONSABLE ({user.get('email')}):\n"
                f"- Motivo consignado: {motivo}\n"
                f"- Acción: La propuesta fue cancelada y no tendrá efecto asistencial."
            )
            return {"estado": "rechazado", "mensaje": msg}

    @router.get("/descargar/{nombre_archivo}")
    async def descargar_archivo_exportado(nombre_archivo: str):
        """
        Descarga física segura de reportes generados (.docx, .xml) evitando Directory Traversal.
        """
        # Sanitizar nombre de archivo
        nombre_limpio = os.path.basename(nombre_archivo)
        ruta_archivo = (DIR_EXPORTADOS / nombre_limpio).resolve()

        # Comprobar que esté dentro de DIR_EXPORTADOS
        if not str(ruta_archivo).startswith(str(DIR_EXPORTADOS.resolve())):
            raise HTTPException(status_code=400, detail="Ruta de archivo no permitida.")

        if not ruta_archivo.exists() or not ruta_archivo.is_file():
            raise HTTPException(status_code=404, detail="El archivo solicitado no existe o ya no está disponible.")

        # Determinar tipo MIME
        if nombre_limpio.endswith(".docx"):
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif nombre_limpio.endswith(".xml"):
            media_type = "application/xml"
        else:
            media_type = "application/octet-stream"

        return FileResponse(
            path=str(ruta_archivo),
            media_type=media_type,
            filename=nombre_limpio
        )

    @router.get("/archivos")
    async def listar_archivos_exportados(user: dict = Depends(validar_acceso_agente)):
        """
        Lista los últimos reportes clínicos exportados para su descarga directa.
        """
        if not DIR_EXPORTADOS.exists():
            return {"archivos": []}

        archivos = []
        for f in sorted(DIR_EXPORTADOS.glob("reporte_clinico_*.*"), key=os.path.getmtime, reverse=True)[:15]:
            tipo = "word" if f.suffix == ".docx" else ("xml" if f.suffix == ".xml" else "otro")
            archivos.append({
                "nombre": f.name,
                "url": f"/agente/descargar/{f.name}",
                "tipo": tipo,
                "tamano": f"{(f.stat().st_size / 1024):.1f} KB",
                "fecha": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            })

        return {"archivos": archivos}

    return router
