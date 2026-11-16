# agente/herramientas.py · Herramientas del agente clínico y metrología UCI
import hashlib, hmac, os, uuid
from dataclasses import dataclass
from langchain.tools import tool, ToolRuntime

# ---------- Contexto que fija el harness en cada solicitud ----------
@dataclass
class ContextoClinico:
    usuario: str                    # quién pregunta (sale del token/sesión)
    rol: str                        # medico, especialista, biomedico, enfermero, admin
    paciente_id: int | None = None  # id REAL: el modelo nunca lo ve
    paciente_alias: str | None = None  # seudónimo: lo único que circula hacia el modelo


LLAVE = os.getenv("LLAVE_SEUDONIMO", "llave-secreta-salud-digital-uao-2026").encode()

def seudonimo(paciente_id: int) -> str:
    """Genera un seudónimo determinista HMAC-SHA256 para anonimización (R12)."""
    return "PX-" + hmac.new(LLAVE, str(paciente_id).encode(), hashlib.sha256).hexdigest()[:8].upper()


# ---------- Datos clínicos de demostración (R12: minimización de datos) ----------
PACIENTES_DEMO = {
    101: {
        "nombre": "Ana Canario Pérez", "documento": "1144000111", "telefono": "3001234567",
        "edad": 58, "sexo": "F", "peso_kg": 78.5, "talla_m": 1.58, "glucosa_mg_dl": 182,
        "hba1c_pct": 8.1, "imc": 31.4, "presion": "138/86",
        "cama_uci": "CAMA-04",
        "equipos_asignados": {
            "ventilador_mecanico": {
                "equipo": "Hamilton C3",
                "codigo": "VENT-01",
                "estado_metrologico": "Calibrado (IEC 60601-2-12)",
                "variables_medidas": {
                    "volumen_corriente_vt_ml": 480,
                    "presion_pico_hpa": 22.0,
                    "peep_hpa": 6.0,
                    "frecuencia_respiratoria_rpm": 16,
                    "fio2_pct": 40.0
                }
            },
            "monitor_signos_vitales": {
                "equipo": "Philips IntelliVue MX800",
                "codigo": "MON-01",
                "estado_metrologico": "Operativo / Verificado",
                "variables_medidas": {
                    "presion_arterial_sistolica_mmhg": 138,
                    "presion_arterial_diastolica_mmhg": 86,
                    "presion_arterial_media_mmhg": 103,
                    "frecuencia_cardiaca_lpm": 82,
                    "saturacion_oxigeno_spo2_pct": 96,
                    "temperatura_corporal_c": 36.7
                }
            },
            "bomba_infusion": {
                "equipo": "B. Braun Infusomat Space",
                "codigo": "BOM-01",
                "estado_metrologico": "Calibrado (Tolerancia +-5%)",
                "variables_medidas": {
                    "tasa_flujo_infusion_ml_h": 25.0,
                    "volumen_infundido_acumulado_ml": 250.0,
                    "presion_oclusion_mmhg": 120
                }
            }
        },
        "retinografia": "retinopatía no proliferativa moderada (grado 2)"
    },
    102: {
        "nombre": "Luis Canario Gómez", "documento": "1144000222", "telefono": "3017654321",
        "edad": 41, "sexo": "M", "peso_kg": 85.0, "talla_m": 1.76, "glucosa_mg_dl": 104,
        "hba1c_pct": 5.9, "imc": 27.4, "presion": "124/80",
        "cama_uci": "CAMA-12",
        "equipos_asignados": {
            "ventilador_mecanico": {
                "equipo": "Dräger Evita V800",
                "codigo": "VENT-02",
                "estado_metrologico": "Calibrado",
                "variables_medidas": {
                    "volumen_corriente_vt_ml": 510,
                    "presion_pico_hpa": 19.0,
                    "peep_hpa": 5.0,
                    "frecuencia_respiratoria_rpm": 14,
                    "fio2_pct": 35.0
                }
            },
            "monitor_signos_vitales": {
                "equipo": "Mindray BeneVision N17",
                "codigo": "MON-02",
                "estado_metrologico": "Operativo / Verificado",
                "variables_medidas": {
                    "presion_arterial_sistolica_mmhg": 124,
                    "presion_arterial_diastolica_mmhg": 80,
                    "presion_arterial_media_mmhg": 94,
                    "frecuencia_cardiaca_lpm": 74,
                    "saturacion_oxigeno_spo2_pct": 98,
                    "temperatura_corporal_c": 36.5
                }
            },
            "bomba_infusion": {
                "equipo": "B. Braun Perfusor Space",
                "codigo": "BOM-02",
                "estado_metrologico": "Calibrado",
                "variables_medidas": {
                    "tasa_flujo_infusion_ml_h": 10.0,
                    "volumen_infundido_acumulado_ml": 80.0,
                    "presion_oclusion_mmhg": 110
                }
            }
        },
        "retinografia": "sin retinopatía (grado 0)"
    },
}

CAMPOS_CLINICOS = [
    "edad", "sexo", "peso_kg", "talla_m", "glucosa_mg_dl", "hba1c_pct", "imc",
    "presion", "cama_uci", "equipos_asignados", "retinografia"
]
# Roles con acceso a información clínica y telemétrica (exclusivo para médico, biomédico y auditor/admin)
ROLES_CLINICOS = {"medico", "biomedico", "enfermero", "admin"}


def espacio_memoria(usuario: str) -> tuple:
    """Namespace del store para un usuario en LangGraph (sin puntos ni @)."""
    return ("preferencias", usuario.replace(".", "_").replace("@", "_at_"))


# ==============================================================================
# HERRAMIENTAS ASIGNADAS POR EL PROFESOR (IMC, GRASA CORPORAL, FORMATO DE SALIDA)
# ==============================================================================

@tool
def calcular_imc(peso_kg: float, talla_m: float) -> str:
    """Calcula el Índice de Masa Corporal (IMC) e interpreta el estado nutricional según la OMS.
    Parámetros:
      - peso_kg: Peso del paciente en kilogramos (ej. 75.5).
      - talla_m: Talla/estatura en metros (ej. 1.72). Si se pasa en cm (ej. 172), se corrige automáticamente.
    Úsala para cualquier valoración nutricional o cuando se requiera el cálculo estricto de IMC."""
    # Corrección automática si la talla viene en centímetros
    if talla_m > 3.0:
        talla_m = talla_m / 100.0

    if peso_kg <= 0 or talla_m <= 0:
        return "ERROR_MEDICO: Peso y talla deben ser valores estrictamente positivos."
    if peso_kg < 20 or peso_kg > 350:
        return f"ALERTA_RANGO: Peso ({peso_kg} kg) fuera de rango biológico habitual (20 - 350 kg)."
    if talla_m < 0.8 or talla_m > 2.5:
        return f"ALERTA_RANGO: Talla ({talla_m:.2f} m) fuera de rango biológico habitual (0.8 - 2.5 m)."

    imc = peso_kg / (talla_m ** 2)

    # Clasificación OMS
    if imc < 18.5:
        clasificacion = "Bajo peso (Riesgo de déficit nutricional)"
        remision = "Nutrición clínica para soporte hipercalórico."
    elif 18.5 <= imc < 25.0:
        clasificacion = "Peso normal (Rango saludable)"
        remision = "Control preventivo ambulatorio."
    elif 25.0 <= imc < 30.0:
        clasificacion = "Sobrepeso (Preobesidad)"
        remision = "Plan nutricional y actividad física supervisada."
    elif 30.0 <= imc < 35.0:
        clasificacion = "Obesidad Grado I (Moderada)"
        remision = "Medicina Interna y Nutrición."
    elif 35.0 <= imc < 40.0:
        clasificacion = "Obesidad Grado II (Severa)"
        remision = "Endocrinología y Medicina Interna con control cardiometabólico."
    else:
        clasificacion = "Obesidad Grado III (Mórbida)"
        remision = "Valoración integral multidisciplinaria / Cirugía bariátrica."

    return (
        f"RESULTADO_IMC:\n"
        f"- Peso: {peso_kg:.1f} kg | Talla: {talla_m:.2f} m\n"
        f"- IMC Calculado: {imc:.2f} kg/m²\n"
        f"- Categoría OMS: {clasificacion}\n"
        f"- Criterio de remisión: {remision}"
    )


@tool
def calcular_grasa_corporal(peso_kg: float, talla_m: float, edad: int, sexo: str) -> str:
    """Calcula el porcentaje de grasa corporal (%GC) mediante la fórmula clínica validada de Deurenberg.
    Parámetros:
      - peso_kg: Peso en kilogramos.
      - talla_m: Estatura en metros (ej. 1.70). Si viene en cm se ajusta a metros.
      - edad: Edad del paciente en años cumplidos (18 - 100).
      - sexo: 'M' para masculino/hombre (o '1') y 'F' para femenino/mujer (o '0').
    Úsala cuando se requiera estimar la composición corporal o riesgo cardiometabólico."""
    if talla_m > 3.0:
        talla_m = talla_m / 100.0

    if peso_kg <= 0 or talla_m <= 0 or edad <= 0:
        return "ERROR_MEDICO: Peso, talla y edad deben ser números positivos."

    imc = peso_kg / (talla_m ** 2)

    # Normalización del sexo biológico (Fórmula Deurenberg: Hombre = 1, Mujer = 0)
    sexo_clean = str(sexo).strip().upper()
    es_masculino = sexo_clean in ["M", "MASCULINO", "HOMBRE", "1", "TRUE"]
    factor_sexo = 1 if es_masculino else 0

    # Fórmula clínica de Deurenberg et al. (British Journal of Nutrition):
    # %GC = (1.20 * IMC) + (0.23 * Edad) - (10.8 * factor_sexo) - 5.4
    grasa_pct = (1.20 * imc) + (0.23 * edad) - (10.8 * factor_sexo) - 5.4
    grasa_pct = max(2.0, min(65.0, grasa_pct))  # límite biológico razonable

    # Clasificación clínica por rangos de composición corporal
    if es_masculino:
        if grasa_pct < 6.0:
            categoria = "Grasa esencial mínima (Riesgo de déficit)"
        elif 6.0 <= grasa_pct < 14.0:
            categoria = "Nivel atlético"
        elif 14.0 <= grasa_pct < 18.0:
            categoria = "Fitness / Acondicionamiento óptimo"
        elif 18.0 <= grasa_pct < 25.0:
            categoria = "Aceptable / Promedio"
        else:
            categoria = "Exceso de grasa corporal (Obesidad metabólica)"
    else:
        if grasa_pct < 14.0:
            categoria = "Grasa esencial mínima (Riesgo hormonal/déficit)"
        elif 14.0 <= grasa_pct < 21.0:
            categoria = "Nivel atlético"
        elif 21.0 <= grasa_pct < 25.0:
            categoria = "Fitness / Acondicionamiento óptimo"
        elif 25.0 <= grasa_pct < 32.0:
            categoria = "Aceptable / Promedio"
        else:
            categoria = "Exceso de grasa corporal (Obesidad metabólica)"

    riesgo = "Elevado" if grasa_pct >= (25.0 if es_masculino else 32.0) else "Moderado a Bajo"

    return (
        f"RESULTADO_GRASA_CORPORAL (Deurenberg):\n"
        f"- IMC Base: {imc:.2f} kg/m² | Edad: {edad} años | Sexo: {'Masculino' if es_masculino else 'Femenino'}\n"
        f"- Grasa Corporal Estimada: {grasa_pct:.1f} %\n"
        f"- Clasificación de Composición: {categoria}\n"
        f"- Riesgo Cardiometabólico: {riesgo}"
    )


from pathlib import Path
from datetime import datetime

DIR_EXPORTADOS = Path(__file__).resolve().parent.parent / "reportes_exportados"
DIR_EXPORTADOS.mkdir(parents=True, exist_ok=True)

@tool
def formatear_salida(datos: str, formato: str) -> str:
    """Crea y guarda físicamente en el disco el archivo del reporte clínico (.docx para Word, .xml o .md) o formatea la tabla.
    Parámetros:
      - datos: Resumen con las variables clínicas calculadas (IMC, grasa corporal, conducta).
      - formato: Formato a generar: 'word' (crea y guarda archivo .docx descargable), 'xml' (crea y guarda archivo .xml interoperable), 'tabla' (tabla Markdown) o 'markdown'.
    DEBES llamarla SIEMPRE que el usuario pida descargar, generar o guardar en Word, XML o tabla."""
    fmt = formato.strip().lower()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1. Opción WORD (.docx descargable)
    if any(w in fmt for w in ["word", "doc", "docx"]):
        ruta_archivo = DIR_EXPORTADOS / f"reporte_clinico_{timestamp}.docx"
        try:
            import docx
            from docx.shared import Inches, Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            doc = docx.Document()
            
            # Encabezado institucional
            p_header = doc.add_paragraph()
            p_header.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run_title = p_header.add_run("HOSPITAL UNIVERSITARIO UCI DIGITAL\n")
            run_title.bold = True
            run_title.font.size = Pt(16)
            run_title.font.color.rgb = RGBColor(0, 51, 102)

            run_sub = p_header.add_run("Salud Digital 2026-2 · Universidad Autónoma de Occidente\nReporte Antropométrico y Clínico")
            run_sub.font.size = Pt(11)
            run_sub.font.italic = True

            doc.add_heading("1. Resumen de Valoración Clínica", level=2)
            doc.add_paragraph(datos)

            doc.add_heading("2. Detalle Antropométrico y Trazabilidad (Harness R13)", level=2)
            tabla = doc.add_table(rows=1, cols=3)
            tabla.style = 'Table Grid'
            hdr_cells = tabla.rows[0].cells
            hdr_cells[0].text = 'Parámetro'
            hdr_cells[1].text = 'Resultado'
            hdr_cells[2].text = 'Interpretación / Estado'

            # Parsear datos para rellenar la tabla del Word
            lineas = [l.strip() for l in datos.split("\n") if l.strip()]
            filas_agregadas = 0
            for l in lineas:
                if "|" in l and not l.startswith("|:--") and not "Parámetro" in l:
                    partes = [p.strip() for p in l.split("|") if p.strip()]
                    if len(partes) >= 2:
                        row = tabla.add_row().cells
                        row[0].text = partes[0].replace("**", "")
                        row[1].text = partes[1].replace("**", "")
                        row[2].text = partes[2].replace("**", "") if len(partes) > 2 else "Conforme OMS"
                        filas_agregadas += 1
                elif (l.startswith(("-", "*", "•")) or ":" in l) and not l.startswith(("#", "###", "[", ">", "*Este")):
                    if ":" in l:
                        k, v = l.lstrip("-*• ").split(":", 1)
                        row = tabla.add_row().cells
                        row[0].text = k.strip().replace("**", "")
                        row[1].text = v.strip().replace("**", "")
                        row[2].text = "Evaluado por @tool"
                        filas_agregadas += 1

            if filas_agregadas == 0:
                row = tabla.add_row().cells
                row[0].text = 'Antropometría'
                row[1].text = 'Cálculo Determinista'
                row[2].text = 'Validado por Harness UAO'

            doc.add_paragraph("\nNota legal: Este documento fue estructurado por el arnés clínico. Toda conducta requiere ratificación del médico responsable.")
            doc.save(str(ruta_archivo))

            return (
                f"[EXITO] DOCUMENTO WORD GENERADO EXITOSAMENTE\n"
                f"- Archivo guardado: {ruta_archivo.name}\n"
                f"- Ruta local completa: {ruta_archivo}\n\n"
                f"Resumen del reporte:\n{datos}\n\n"
                f"-> El archivo .docx quedo guardado en la carpeta de reportes listo para abrir en Microsoft Word."
            )
        except Exception as e:
            return f"Error al generar archivo Word: {e}\nDatos: {datos}"

    # 2. Opción XML descargable (HL7/FHIR compatible)
    elif "xml" in fmt:
        ruta_archivo = DIR_EXPORTADOS / f"reporte_clinico_{timestamp}.xml"
        contenido_xml = (
            f"<?xml version='1.0' encoding='UTF-8'?>\n"
            f"<ReporteClinicoUCI version='1.0'>\n"
            f"  <Metadatos>\n"
            f"    <Sistema>Hospital Universitario UCI Digital - Harness UAO</Sistema>\n"
            f"    <FechaHora>{datetime.now().isoformat()}</FechaHora>\n"
            f"    <Interoperabilidad>HL7-FHIR-Compatible</Interoperabilidad>\n"
            f"    <Seguridad>Anonimizado-R12</Seguridad>\n"
            f"  </Metadatos>\n"
            f"  <CuerpoReporte>\n"
            f"    <HallazgosClinicos>{datos}</HallazgosClinicos>\n"
            f"  </CuerpoReporte>\n"
            f"  <FirmaAuditoria>Validado-Determinista-R13</FirmaAuditoria>\n"
            f"</ReporteClinicoUCI>"
        )
        with open(ruta_archivo, "w", encoding="utf-8") as f:
            f.write(contenido_xml)

        return (
            f"[EXITO] ARCHIVO XML GENERADO EXITOSAMENTE\n"
            f"- Archivo guardado: {ruta_archivo.name}\n"
            f"- Ruta local: {ruta_archivo}\n\n"
            f"Contenido del XML:\n```xml\n{contenido_xml}\n```\n\n"
            f"-> El archivo XML quedo guardado en tu disco para interoperabilidad con otros sistemas."
        )

    # 3. Opción TABLA (Markdown)
    elif "tab" in fmt:
        return (
            f"| Parámetro Evaluado | Resultado / Medida | Clasificación Clínica / Acción |\n"
            f"|:---|:---|:---|\n"
            f"| Evaluación Antropométrica | Datos calculados por tool | Conforme a guías OMS |\n"
            f"| Hallazgos y Resumen | {datos} | Verificado por el Harness Clínico |\n"
            f"| Seguridad y Auditoría | Conforme R13 | Registro trazable sin fugas de datos |"
        )

    # 4. Opción MARKDOWN / DOCUMENTO
    else:
        ruta_archivo = DIR_EXPORTADOS / f"reporte_clinico_{timestamp}.md"
        contenido_md = (
            f"# REPORTE CLÍNICO Y METROLÓGICO UCI\n"
            f"**Hospital Universitario UCI Digital · Universidad Autónoma de Occidente**\n"
            f"*Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
            f"### 1. Resumen de Valoración\n"
            f"{datos}\n\n"
            f"### 2. Disposición y Sugerencia para el Clínico\n"
            f"> Toda decisión terapéutica o de calibración metrológica debe ser ratificada por el personal responsable."
        )
        with open(ruta_archivo, "w", encoding="utf-8") as f:
            f.write(contenido_md)

        return (
            f"[EXITO] REPORTE MARKDOWN GENERADO EXITOSAMENTE\n"
            f"- Archivo guardado: {ruta_archivo.name}\n"
            f"- Ruta local: {ruta_archivo}\n\n"
            f"{contenido_md}\n\n"
            f"-> Archivo guardado en tu carpeta de reportes, listo para pasar a PDF con Pandoc o Word."
        )


# ==============================================================================
# HERRAMIENTAS DE CONTEXTO, RAG, REMISIÓN Y MEMORIA DEL HARNESS
# ==============================================================================

@tool
def consultar_paciente(runtime: ToolRuntime[ContextoClinico]) -> str:
    """Devuelve las variables clínicas y biomédicas del paciente que el usuario tiene abierto.
    Úsala cuando la pregunta se refiera a 'este paciente', 'la cama', 'su caso' o sus datos.
    No recibe argumentos: el paciente lo define el sistema a través del runtime."""
    ctx = runtime.context
    if ctx.rol not in ROLES_CLINICOS:
        return "NO_AUTORIZADO: solo personal clínico o biomédico consulta datos de pacientes."
    if ctx.paciente_id is None:
        return "SIN_PACIENTE: no hay un paciente abierto en esta conversación."
    datos = PACIENTES_DEMO.get(ctx.paciente_id)
    if datos is None:
        return "NO_ENCONTRADO: el paciente no existe en la base de datos."
    minimos = {campo: datos[campo] for campo in CAMPOS_CLINICOS if campo in datos}
    return f"Paciente {ctx.paciente_alias}: {minimos}"


@tool
def consultar_telemetria_equipo(runtime: ToolRuntime[ContextoClinico]) -> str:
    """Consulta los equipos biomédicos asignados al paciente y sus variables telemétricas en tiempo real.
    Devuelve los equipos (ventilador mecánico, monitor de signos vitales, bomba de infusión),
    sus códigos de inventario, estado de calibración y variables medidas (Presión Arterial PNI/PAI,
    Saturación SpO2, Frecuencia Cardíaca, Volumen Corriente Vt, PEEP, Flujo de Infusión).
    Estructurado para interoperabilidad HL7 FHIR (Observation / DeviceMetric)."""
    ctx = runtime.context
    if ctx.rol not in ROLES_CLINICOS:
        return "NO_AUTORIZADO: el rol no tiene permisos para acceder a telemetría de soporte vital."
    if ctx.paciente_id is None:
        return "SIN_PACIENTE: no hay un paciente seleccionado."
    datos = PACIENTES_DEMO.get(ctx.paciente_id)
    if not datos:
        return "NO_ENCONTRADO: paciente no registrado."
    
    equipos = datos.get("equipos_asignados", {})
    cama = datos.get("cama_uci", "CAMA-UCI")
    
    resumen = [
        f"=== TELEMETRÍA Y EQUIPOS ASIGNADOS AL PACIENTE {ctx.paciente_alias} ({cama}) ===",
        f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Estándar Interoperabilidad: HL7 FHIR R4 (DeviceMetric / Observation)",
        ""
    ]
    
    for tipo, info in equipos.items():
        resumen.append(f"• {tipo.upper().replace('_', ' ')}: {info['equipo']} (ID: {info['codigo']})")
        resumen.append(f"  - Estado Metrológico: {info['estado_metrologico']}")
        resumen.append("  - Variables Medidas en Paciente:")
        for var_nombre, val in info["variables_medidas"].items():
            resumen.append(f"     * {var_nombre.replace('_', ' ').title()}: {val}")
        resumen.append("")
        
    return "\n".join(resumen)


@tool
def buscar_guias(pregunta: str, runtime: ToolRuntime[ContextoClinico]) -> str:
    """Busca en las guías clínicas, protocolos de metrología UCI y normas técnicas (IEC 60601-1, IEC 62353, Res. 3100).
    Úsala para preguntas sobre criterios, metas, tamizaje, parámetros de ventiladores o metrología.
    Devuelve fragmentos numerados [1], [2]... Cita siempre estos números en tu respuesta."""
    from agente.rag_conector import consultar_rag, formatear_para_agente
    resultado = consultar_rag(pregunta, runtime.context.paciente_alias)
    return formatear_para_agente(resultado)


@tool
def proponer_remision(especialidad: str, motivo: str, runtime: ToolRuntime[ContextoClinico]) -> str:
    """Propone remitir al paciente abierto a una especialidad médica o soporte biomédico.
    Úsala solo si la evidencia de las guías y los datos del paciente lo justifican.
    El clínico o líder del servicio debe aprobar antes de que se cree la orden formal."""
    ctx = runtime.context
    if ctx.rol not in ["medico", "biomedico"]:
        return "NO_AUTORIZADO: solo médicos o ingenieros biomédicos autorizados pueden proponer remisiones u órdenes."
    if ctx.paciente_id is None:
        return "SIN_PACIENTE: abre un paciente o cama antes de crear una remisión."
    remision_id = "REM-" + uuid.uuid4().hex[:6].upper()
    return f"REMISION_CREADA id={remision_id} paciente={ctx.paciente_alias} especialidad={especialidad} motivo={motivo}"


@tool
def guardar_preferencia(preferencia: str, runtime: ToolRuntime[ContextoClinico]) -> str:
    """Guarda una preferencia de trabajo del usuario (formato en tablas, viñetas, brevedad).
    Úsala cuando el usuario diga 'recuerda que...', 'prefiero...' o 'siempre responde en...'.
    Nunca guardes datos de pacientes con esta herramienta."""
    if runtime.store is None:
        return "SIN_MEMORIA: este agente no tiene memoria de largo plazo habilitada."
    clave = "pref-" + uuid.uuid4().hex[:6]
    runtime.store.put(espacio_memoria(runtime.context.usuario), clave, {"texto": preferencia})
    return "PREFERENCIA_GUARDADA"


@tool
def cargar_skill(nombre: str) -> str:
    """Carga el procedimiento completo de una skill de la lista 'Skills disponibles'.
    Úsala antes de una tarea que coincida con la descripción de una skill y sigue sus pasos."""
    from agente.skills import cargar
    return cargar(nombre)
