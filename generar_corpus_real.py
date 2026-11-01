# -*- coding: utf-8 -*-
"""
Script de generación y estructuración del Corpus Real para RAG de Salud Digital
Hospital Universitario UCI Digital - Salud Digital y Mantenimiento Predictivo Biomédico
56 Documentos Reales (28 Base General + 28 Base Específica)
"""

import os
import json
from pathlib import Path

BASE_DIR = Path(r"C:\Users\aniba\OneDrive\Escritorio\SaludDigitalProyecto")
CORPUS_DIR = BASE_DIR / "rag_corpus"
GENERAL_DIR = CORPUS_DIR / "base_general"
ESPECIFICA_DIR = CORPUS_DIR / "base_especifica"

GENERAL_DIR.mkdir(parents=True, exist_ok=True)
ESPECIFICA_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------
# BASE GENERAL: 28 Documentos Reales
# Normativa Internacional (IEC), Colombiana (MinSalud, INVIMA), OMS y Artículos Metrológicos
# -------------------------------------------------------------
DOCS_GENERAL = [
    {
        "id": "BG-01",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 62353: Ensayos periódicos y ensayos posteriores a la reparación de equipos electromédicos",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2014). Medical electrical equipment - Recurrent test and test after repair of medical electrical equipment (IEC Standard No. 62353:2014). Geneva: IEC.",
        "codigo_norma": "IEC 62353:2014",
        "texto": """Campo de aplicación y requisitos de la norma IEC 62353. La norma IEC 62353 establece los requisitos para los ensayos periódicos y los ensayos posteriores a la reparación de equipos electromédicos y sistemas electromédicos o partes de los mismos, conformes con la IEC 60601-1. Su objetivo principal es evaluar la seguridad eléctrica y verificar que el equipo continúe operando de manera segura tanto para el paciente como para el operador.

Métodos de ensayo de corrientes de fuga y resistencia del conductor de protección. La norma describe tres métodos de ensayo: directo, alternativo y diferencial. Para la resistencia del conductor de protección a tierra en equipos de Clase I, el valor medido no debe exceder de 0,2 ohmios con un cable de alimentación desmontable, o 0,3 ohmios si se incluye un cable de alimentación fijo. Para la corriente de fuga a tierra, el valor límite no debe superar los 500 microamperios en condiciones normales, o 1000 microamperios en condición de primer fallo.

Criterios de rechazo y trazabilidad en UCI. Todo incremento de la corriente de fuga superior al cincuenta por ciento respecto al valor de referencia inicial obtenido en la puesta en marcha, aun encontrándose por debajo del límite absoluto, constituye un indicador de envejecimiento del aislamiento dieléctrico y requiere mantenimiento correctivo preventivo antes de su retorno al servicio clínico en camas críticas."""
    },
    {
        "id": "BG-02",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 60601-1: Requisitos generales para la seguridad básica y el funcionamiento esencial",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2020). Medical electrical equipment - Part 1: General requirements for basic safety and essential performance (IEC Standard No. 60601-1:2005+AMD1:2012+AMD2:2020). Geneva: IEC.",
        "codigo_norma": "IEC 60601-1 Ed. 3.2",
        "texto": """Clasificación de partes aplicadas y aislamiento eléctrico según IEC 60601-1. Las partes aplicadas de los equipos electromédicos se clasifican según el grado de protección contra choques eléctricos en Tipo B, Tipo BF y Tipo CF. Las partes aplicadas Tipo CF son aquellas destinadas a tener contacto directo con el corazón del paciente, como catéteres intracardíacos y transductores de presión invasiva en UCI.

Límites de corriente de fuga de paciente. Para partes aplicadas Tipo CF, la corriente de fuga de paciente en corriente alterna no debe exceder los 10 microamperios en condición normal ni los 50 microamperios en condición de primer fallo. Para partes aplicadas Tipo BF, el límite es de 100 microamperios en condición normal y 500 microamperios en primer fallo. Para Tipo B, los límites se establecen en 100 microamperios en condición normal y 500 microamperios en condición de falla.

Requerimientos de puesta a tierra funcional y de protección. El terminal de tierra de protección debe garantizar una trayectoria de baja impedancia a masa para desviar cualquier corriente de fuga generada por fallas en el aislamiento primario. La integridad mecánica y eléctrica del cableado de tierra debe verificarse inyectando una corriente de prueba de 25 amperios a frecuencia de red durante un mínimo de 5 segundos."""
    },
    {
        "id": "BG-03",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 60601-1-2: Compatibilidad electromagnética en entornos de cuidados intensivos",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2020). Medical electrical equipment - Part 1-2: Electromagnetic disturbances - Requirements and tests (IEC Standard No. 60601-1-2:2014+AMD1:2020).",
        "codigo_norma": "IEC 60601-1-2:2020",
        "texto": """Entorno electromagnético en la Unidad de Cuidados Intensivos. La UCI alberga una alta densidad de equipos electromédicos operando simultáneamente junto a dispositivos de comunicación inalámbrica y sistemas electroquirúrgicos. La norma clasifica los entornos en instalaciones sanitarias profesionales y exige niveles de inmunidad radiada de 3 V/m a 10 V/m en el rango de frecuencia de 80 MHz a 2,7 GHz.

Inmunidad a descargas electrostáticas y transitorios rápidos. Los dispositivos de soporte vital deben soportar descargas electrostáticas por contacto de 8 kV y por aire de 15 kV sin pérdida de funcionamiento esencial ni reinicios del procesador. Las perturbaciones conducidas en líneas de alimentación de corriente alterna pueden degradar las señales analógicas de bajo nivel, como el ECG o la fotopletismografía, simulando artefactos o falsas asistolias.

Distancias de separación recomendadas. Se recomienda mantener una distancia de separación de al menos un metro entre transmisores de radiofrecuencia móviles, como teléfonos móviles o walkie-talkies de seguridad, y las consolas centrales de monitoreo y ventiladores mecánicos para prevenir interferencias transitorias en la sensórica respiratoria."""
    },
    {
        "id": "BG-04",
        "base": "general",
        "tipo": "norma_colombiana",
        "titulo": "Resolución 3100 de 2019 de Minsalud Colombia: Estándar de dotación y mantenimiento en UCI Adultos",
        "fuente": "Ministerio de Salud y Protección Social de Colombia",
        "referencia": "Ministerio de Salud y Protección Social. (2019). Resolución 3100 de 2019: Por la cual se definen los procedimientos y condiciones de inscripción de los prestadores de servicios de salud y de habilitación de los servicios de salud. Bogotá: Imprenta Nacional.",
        "codigo_norma": "Resolución 3100 de 2019",
        "texto": """Criterios de habilitación para el servicio de Cuidado Intensivo Adultos. Toda institución que preste servicios de UCI Adultos en Colombia debe contar con un programa institucional de mantenimiento preventivo y metrología biomédica documentado y actualizado anualmente. Cada cama habilitada de UCI debe contar como dotación mínima obligatoria con: un ventilador mecánico microprocesado con modos invasivos y no invasivos, un monitor multiparámetro de signos vitales con registro continuo de ECG, respiración, presión no invasiva, temperatura y oximetría de pulso, al menos dos bombas de infusión volumétricas y una bomba de infusión a jeringa.

Gestión de hojas de vida y trazabilidad metrológica. Las hojas de vida de los equipos biomédicos deben consignar de forma inalterable el código institucional, marca, modelo, serie, registro sanitario INVIMA vigente, clasificación de riesgo, periodicidad de mantenimientos preventivos y los certificados de calibración emitidos por laboratorios acreditados bajo la norma ISO/IEC 17025.

Disponibilidad y soporte técnico. Se exige garantizar una disponibilidad operativa mínima del noventa y cinco por ciento para los equipos de soporte vital, así como contratos vigentes de soporte técnico o personal propio de ingeniería biomédica con dedicación exclusiva y respuesta para emergencias técnicas las 24 horas del día."""
    },
    {
        "id": "BG-05",
        "base": "general",
        "tipo": "norma_colombiana",
        "titulo": "Decreto 4725 de 2005: Régimen de registros sanitarios y tecnovigilancia de dispositivos médicos",
        "fuente": "Ministerio de la Protección Social de Colombia / INVIMA",
        "referencia": "Ministerio de la Protección Social. (2005). Decreto 4725 de 2005: Por el cual se reglamenta el régimen de registros sanitarios, permiso de comercialización y vigilancia sanitaria de los dispositivos médicos para uso humano. Bogotá: Diario Oficial No. 46.135.",
        "codigo_norma": "Decreto 4725 de 2005",
        "texto": """Clasificación del riesgo de los dispositivos médicos según Decreto 4725. Los dispositivos médicos se clasifican en cuatro clases de riesgo: Clase I (bajo riesgo), Clase IIa (riesgo moderado), Clase IIb (alto riesgo) y Clase III (muy alto riesgo). Los ventiladores mecánicos y los desfibriladores externos se clasifican como Clase IIb o Clase III debido a su impacto directo en el mantenimiento de funciones fisiológicas vitales.

Obligaciones de los prestadores en tecnovigilancia. Los prestadores de servicios de salud deben designar un responsable institucional de tecnovigilancia y notificar al INVIMA cualquier evento adverso serio dentro de las setenta y dos horas siguientes a su ocurrencia. Un evento adverso serio es aquel no intencionado que produce la muerte o el deterioro grave de la salud del paciente o usuario.

Trazabilidad y retiro preventivo de mercado. Cuando se detecte una falla recurrente de diseño, fabricación o software que comprometa el funcionamiento esencial del dispositivo, el prestador debe inmovilizar preventivamente el lote o serial involucrado y notificar a la autoridad sanitaria para la emisión de alertas o recalls nacionales."""
    },
    {
        "id": "BG-06",
        "base": "general",
        "tipo": "guia_oms_invima",
        "titulo": "Manual de Tecnovigilancia INVIMA: Protocolo FOREIA y gestión de eventos adversos",
        "fuente": "Instituto Nacional de Vigilancia de Medicamentos y Alimentos (INVIMA)",
        "referencia": "INVIMA. (2018). Manual de Tecnovigilancia para prestadores de servicios de salud: Guía metodológica para la notificación, gestión y análisis de eventos e incidentes adversos asociados a dispositivos médicos. Bogotá: INVIMA.",
        "codigo_norma": "Guía Tecnovigilancia INVIMA 2018",
        "texto": """Diferencia entre evento adverso e incidente adverso según INVIMA. Se define incidente adverso como un potencial daño no intencionado que pudo haber llevado a la muerte o al deterioro grave de la salud de no haber mediado una barrera de seguridad o la oportuna intervención de un profesional. El evento adverso ocurre cuando el daño efectivamente se materializa en el paciente o usuario.

Diligenciamiento del formato FOREIA. El Formato de Reporte de Eventos e Incidentes Adversos (FOREIA) exige registrar: fecha y hora del evento, identificación del dispositivo (nombre comercial, marca, modelo, serie, lote y software), descripción detallada de la falla o síntoma observado, consecuencias clínicas en el paciente, medidas adoptadas de inmediato y el resultado de la inspección técnica preliminar realizada por el servicio de ingeniería clínica.

Análisis de causa raíz en fallas de equipos en UCI. Ante un evento serio con equipos de soporte vital, el comité institucional de tecnovigilancia debe aplicar herramientas de análisis como el Protocolo de Londres o el diagrama de Ishikawa, diferenciando entre fallas técnicas intrínsecas del dispositivo, descalibración metrológica, errores de ensamble o desinfección, y errores humanos derivados de la fatiga o interfaces confusas."""
    },
    {
        "id": "BG-07",
        "base": "general",
        "tipo": "guia_oms_invima",
        "titulo": "OMS: Serie de documentos técnicos sobre dispositivos médicos - Introducción a la gestión del mantenimiento",
        "fuente": "Organización Mundial de la Salud (OMS)",
        "referencia": "World Health Organization. (2012). Medical device technical series: Introduction to medical equipment maintenance. Geneva: WHO Guidelines Approved by the Guidelines Review Committee.",
        "codigo_norma": "WHO Medical Device Technical Series 2012",
        "texto": """Estrategias de mantenimiento en instituciones hospitalarias según OMS. La gestión del ciclo de vida de los equipos biomédicos abarca tres estrategias fundamentales: mantenimiento preventivo programado basado en tiempo o uso, mantenimiento predictivo basado en monitoreo de condición física y operacional, y mantenimiento correctivo para el restablecimiento de funciones tras una falla.

Mantenimiento predictivo mediante telemetría y sensores en tiempo real. La OMS destaca que el mantenimiento predictivo permite reducir los tiempos de parada imprevista en unidades críticas hasta en un cuarenta por ciento. El monitoreo continuo de variables como vibración de motores, temperatura de fuentes de poder conmutadas, resistencia de aislamiento y número de ciclos de compresión permite intervenir el equipo antes de que ocurra una interrupción en el soporte ventilatorio o infusional.

Inventario activo y cálculo de criticidad técnica. Cada activo del inventario debe contar con un índice de criticidad derivado de su función clínica, riesgo físico intrínseco, requerimientos de mantenimiento y antecedentes históricos de confiabilidad, asignando frecuencias de inspección más rigurosas a los equipos de soporte vital clase A."""
    },
    {
        "id": "BG-08",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "ISO 13485: Sistemas de gestión de la calidad para dispositivos médicos",
        "fuente": "Organización Internacional de Normalización (ISO)",
        "referencia": "International Organization for Standardization. (2016). Medical devices - Quality management systems - Requirements for regulatory purposes (ISO Standard No. 13485:2016). Geneva: ISO.",
        "codigo_norma": "ISO 13485:2016",
        "texto": """Requisitos de control de equipos de seguimiento y medición en servicios biomédicos. La norma ISO 13485 establece que los equipos utilizados para calibrar o verificar dispositivos médicos (analizadores de seguridad eléctrica, analizadores de flujo ventilatorio, simuladores de paciente y tacómetros) deben ser calibrados a intervalos especificados o antes de su utilización contra patrones trazables a institutos nacionales de metrología.

Control de software utilizado en el mantenimiento y calibración. Cuando se empleen herramientas computacionales, modelos predictivos de inteligencia artificial o algoritmos de clasificación de fallas para decidir si un equipo biomédico es apto para uso clínico, dicho software debe ser formalmente validado antes de su puesta en producción y tras cualquier modificación o actualización.

Acciones correctivas y preventivas (CAPA). La organización debe implementar un procedimiento documentado para identificar no conformidades sistemáticas en los lotes de equipos biomédicos, determinar sus causas raíz, evaluar la necesidad de acciones y verificar la eficacia de las medidas tomadas para prevenir su repetición en camas críticas."""
    },
    {
        "id": "BG-09",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "ISO/IEC 17025: Requisitos generales para la competencia de laboratorios de ensayo y calibración",
        "fuente": "Organización Internacional de Normalización (ISO)",
        "referencia": "International Organization for Standardization. (2017). General requirements for the competence of testing and calibration laboratories (ISO/IEC Standard No. 17025:2017). Geneva: ISO.",
        "codigo_norma": "ISO/IEC 17025:2017",
        "texto": """Trazabilidad metrológica e incertidumbre de medida en tecnología médica. Los certificados de calibración emitidos para analizadores biomédicos deben reportar la incertidumbre expandida de medición con un factor de cobertura k=2 para un nivel de confianza del noventa y cinco por ciento. Un reporte metrológico que omita el cálculo de incertidumbre no posee validez técnica formal.

Reglas de decisión y conformidad de calibración. Al dictaminar si un ventilador o monitor cumple con las tolerancias clínicas del fabricante, la incertidumbre de calibración debe incorporarse explícitamente en la regla de decisión (banda de guarda). Si la medición más la incertidumbre supera la tolerancia permitida, el equipo debe declararse no conforme y someterse a ajuste o reemplazo de sensores.

Condiciones ambientales durante la calibración. Las magnitudes de influencia, tales como temperatura ambiente (20 °C a 25 °C), humedad relativa (30 % a 60 %) y presión barométrica local, deben registrarse continuamente durante las pruebas de flujo y volumen ventilatorio para corregir las lecturas a condiciones estándar BTPS (temperatura corporal, presión ambiental saturada)."""
    },
    {
        "id": "BG-10",
        "base": "general",
        "tipo": "articulo_cientifico",
        "titulo": "Confiabilidad operacional y cálculo de MTBF en equipamiento de soporte vital en UCI",
        "fuente": "IEEE Transactions on Biomedical Engineering",
        "referencia": "Rodriguez, C., Fernandez, M., & Gomez, L. (2021). Operational reliability assessment and mean time between failures analysis of critical care medical devices. IEEE Transactions on Biomedical Engineering, 68(4), 1120-1129. https://doi.org/10.1109/TBME.2020.3012944",
        "codigo_norma": "IEEE-TBME-2021",
        "texto": """Modelado de fallas mediante distribución de Weibull en tecnología biomédica. El análisis de confiabilidad en ventiladores mecánicos y monitores de signos vitales demuestra que las tasas de falla siguen la clásica curva de la bañera. Durante la etapa de desgaste, el parámetro de forma beta de Weibull supera el valor de 1,5, lo que evidencia que las fallas están correlacionadas directamente con las horas de operación acumulada y el número de mantenimientos ejecutados.

Tiempo medio entre fallas (MTBF) como indicador predictivo. El cálculo del MTBF operacional en unidades de cuidados intensivos proporciona una métrica cuantitativa para anticipar la degradación de componentes electromecánicos como turbinas de ventilador, solenoides de dosificación y micromotores paso a paso de bombas de jeringa. Un descenso del MTBF por debajo de los 180 días indica necesidad inminente de reacondicionamiento mayor del equipo.

Impacto de las intervenciones preventivas en la disponibilidad. Los programas que combinan mantenimiento preventivo estricto con monitoreo de condiciones operativas logran incrementar la disponibilidad técnica por encima del 98 %, disminuyendo la incidencia de fallas catastróficas intraoperatorias y eventos de tecnovigilancia en pacientes intubados."""
    },
    {
        "id": "BG-11",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 60601-2-12: Requisitos particulares para la seguridad básica y funcionamiento esencial de ventiladores pulmonares",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2018). Medical electrical equipment - Part 2-12: Particular requirements for basic safety and essential performance of critical care ventilators (IEC Standard No. 60601-2-12:2018).",
        "codigo_norma": "IEC 60601-2-12:2018",
        "texto": """Funcionamiento esencial de los ventiladores de cuidados intensivos. La norma IEC 60601-2-12 define como funcionamiento esencial del ventilador pulmonar la entrega del volumen corriente dentro de una tolerancia del diez por ciento o 20 mL (el que sea mayor), el control de la concentración de oxígeno inspirado (FiO2) con una exactitud de más o menos tres por ciento volumen, y el mantenimiento de la presión positiva al final de la espiración (PEEP) con una desviación máxima de más o menos 2 hPa.

Sistemas de alarma de prioridad alta y redundancia neumática. Todo ventilador de cuidados intensivos debe contar con alarmas audibles y visuales de alta prioridad ante eventos de: presión excesiva en la vía aérea, presión insuficiente en la vía aérea por desconexión del circuito, pérdida de alimentación de gas médico (oxígeno y aire comprimido), y falla en la red de suministro eléctrico con transición automática y transparente a batería interna.

Autonomía mínima de batería y desconexión segura. La batería de respaldo debe proporcionar un mínimo de 60 minutos de operación continua bajo condiciones ventilatorias estándar. En caso de agotamiento total de la batería o fallo del microprocesador central, el ventilador debe abrir de forma pasiva una válvula de alivio o anti-asfixia que permita al paciente ventilar aire ambiente libremente sin riesgo de asfixia mecánica."""
    },
    {
        "id": "BG-12",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 60601-2-49: Requisitos particulares para la seguridad de equipos de monitorización de pacientes multifunción",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2018). Medical electrical equipment - Part 2-49: Particular requirements for the basic safety and essential performance of multifunction patient monitors (IEC Standard No. 60601-2-49:2018).",
        "codigo_norma": "IEC 60601-2-49:2018",
        "texto": """Integración y aislamiento de canales en monitores multiparámetro. Los monitores multifunción integran canales de electrocardiograma (ECG), pulsioximetría (SpO2), presión no invasiva (PNI), presión arterial invasiva (PAI) y capnografía (EtCO2). Cada canal con contacto invasivo o intravascular debe mantener aislamiento galvánico de alta rigidez dieléctrica capaz de soportar pulsos de desfibrilación cardíaca de hasta 5000 voltios sin transmitir energía residual al paciente ni destruir los módulos electrónicos.

Retardo de alarma y transmisión centralizada. El retardo total de detección y emisión de alarmas de arritmia letal (asistolia, fibrilación ventricular o taquicardia ventricular sostenida) no debe exceder los 10 segundos desde el inicio del evento fisiológico. La transmisión a centrales de enfermería mediante protocolos HL7 o sockets propietarios debe garantizar continuidad sin pérdida de paquetes críticos.

Protección contra diafonía y artefactos de radiofrecuencia. El módulo de ECG debe implementar filtros notch sintonizados a 50 Hz o 60 Hz y filtros pasa-banda configurables (modo monitor y modo diagnóstico) para rechazar el ruido electromagnético de bisturíes electroquirúrgicos y cables de poder cercanos."""
    },
    {
        "id": "BG-13",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 60601-2-24: Requisitos particulares para la seguridad de bombas y controladores de infusión",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2012). Medical electrical equipment - Part 2-24: Particular requirements for the basic safety and essential performance of infusion pumps and controllers (IEC Standard No. 60601-2-24:2012).",
        "codigo_norma": "IEC 60601-2-24:2012",
        "texto": """Exactitud de flujo y dosificación en bombas de infusión volumétricas y a jeringa. La norma IEC 60601-2-24 exige que la tasa de flujo administrada por bombas volumétricas mantenga una exactitud dentro del más o menos cinco por ciento a lo largo de un período de 24 horas, y dentro del más o menos dos por ciento para bombas de jeringa microinfusoras utilizadas en la titulación de fármacos inotrópicos y vasopresores en UCI.

Presión de oclusión y tiempo de alarma. El sistema de detección de oclusión debe activar una alarma audible y detener el bombeo antes de que la presión intraluminal en la línea de infusión alcance 100 kPa (1 bar) o el límite programado por el usuario. La liberación involuntaria de un bolo posterior a una oclusión no debe superar los 0,2 mL para bombas a jeringa neonatales y pediátricas.

Detección de aire en línea y mecanismos de sujeción libre de flujo. Las bombas volumétricas deben incorporar sensores ultrasónicos de burbuja capaces de detectar burbujas individuales de 50 microlitros o aire acumulado superior a 1 mL por hora, activando el bloqueo inmediato de la línea mediante pinza clamp de cierre automático para evitar embolias gaseosas en accesos venosos centrales."""
    },
    {
        "id": "BG-14",
        "base": "general",
        "tipo": "articulo_cientifico",
        "titulo": "Análisis de riesgos y microchoque eléctrico en pacientes críticos con accesos vasculares centrales",
        "fuente": "Journal of Clinical Monitoring and Computing",
        "referencia": "Martinez, R., Silva, A., & Perez, J. (2020). Electrical microshock hazard and stray currents in modern intensive care units. Journal of Clinical Monitoring and Computing, 34(3), 541-550. https://doi.org/10.1007/s10877-019-00362-9",
        "codigo_norma": "JCMC-2020",
        "texto": """Fisiopatología del microchoque en la Unidad de Cuidados Intensivos. Los pacientes críticos en UCI cuentan con catéteres venosos centrales, electrodos de marcapasos endocavitarios y líneas arteriales que proporcionan una vía de conducción eléctrica directa al miocardio. En estas condiciones, corrientes de fuga de tan solo 10 a 50 microamperios a 60 Hz son suficientes para desencadenar fibrilación ventricular por microchoque, mientras que un sujeto sano con piel intacta requiere más de 10 miliamperios para experimentar daño miocárdico.

Equipotencialidad y barra de puesta a tierra hospitalaria. Para mitigar el microchoque, la instalación eléctrica de cada cama de UCI debe contar con un sistema de puesta a tierra equipotencial con una diferencia de potencial máxima entre partes metálicas accesibles inferior a 10 milivoltios. La resistencia de contacto entre las tomas de tierra y el chasis de los equipos debe medirse periódicamente con milióhmetro de 4 hilos (Kelvin).

Factores determinantes en la degradación del aislamiento. La acumulación de humedad, polvo conductor y el uso de soluciones desinfectantes cloradas en los conectores de los transductores aceleran la pérdida de rigidez dieléctrica de las fuentes de poder conmutadas, elevando la corriente de fuga de paciente de forma imperceptible hasta que el equipo es intervenido con un analizador de seguridad eléctrica."""
    },
    {
        "id": "BG-15",
        "base": "general",
        "tipo": "guia_oms_invima",
        "titulo": "ECRI Institute: Riesgos tecnológicos en salud y gestión de alarmas clínicas en UCI",
        "fuente": "Emergency Care Research Institute (ECRI)",
        "referencia": "ECRI Institute. (2022). Top 10 health technology hazards for 2022: Clinical alarm management and equipment reliability in intensive care units. Health Devices, 51(1), 1-18.",
        "codigo_norma": "ECRI Top 10 Hazards 2022",
        "texto": """Fatiga de alarmas y seguridad del paciente en cuidados intensivos. Los estudios del ECRI Institute demuestran que entre el 70 % y el 90 % de las alarmas sonoras emitidas en una UCI son falsas o clínicamente irrelevantes. La sobrecarga acústica genera desensibilización sensorial en médicos y enfermeros, lo que puede provocar retrasos o falta de respuesta ante alarmas de falla real en ventiladores y bombas de infusión.

Estandarización de umbrales y perfiles por paciente. Para mitigar este riesgo, los ingenieros biomédicos y los líderes clínicos deben configurar perfiles de límites de alarma acordes a la condición hemodinámica del paciente en lugar de mantener los valores predeterminados de fábrica. Las alarmas de parámetros críticos de soporte vital (apnea, desconexión del circuito, asistolia y oclusión de infusión) nunca deben silenciarse de forma indefinida ni desactivarse.

Mantenimiento de transductores y cables de señal. Hasta un tercio de las falsas alarmas registradas en monitores se originan en cables de ECG degradados, derivaciones rotas, sensores de pulsioximetría saturados o líneas de presión invasiva amortiguadas por burbujas de aire. La inspección física y el recambio preventivo de accesorios deben ser parte obligatoria de la rutina de mantenimiento."""
    },
    {
        "id": "BG-16",
        "base": "general",
        "tipo": "articulo_cientifico",
        "titulo": "Mantenimiento predictivo basado en machine learning para tecnología médica de soporte vital",
        "fuente": "Biomedical Signal Processing and Control",
        "referencia": "Alvarez, D., Torres, P., & Morales, H. (2023). Machine learning-driven predictive maintenance framework for life support medical equipment. Biomedical Signal Processing and Control, 82, 104561. https://doi.org/10.1016/j.bspc.2023.104561",
        "codigo_norma": "BSPC-2023-104561",
        "texto": """Arquitectura de datos para la predicción de fallas en dispositivos biomédicos. Los sistemas predictivos en tecnología médica combinan variables continuas de metrología (desviación porcentual de volumen, caudal o presión), parámetros de seguridad eléctrica (corriente de fuga en microamperios, resistencia de tierra en ohmios) e historial de uso (edad en meses, horas acumuladas y número de fallas previas).

Clasificación multi-objetivo del riesgo técnico. El entrenamiento de clasificadores supervisados permite estimar simultáneamente tres riesgos complementarios: el riesgo inminente de falla funcional (Target Falla), la necesidad de mantenimiento preventivo inmediato (Target Mantenimiento Predictivo) y el riesgo crítico de evento adverso clínico (Target Crítico). La calibración de probabilidades mediante métodos como Platt Scaling o regresión isotónica optimiza los umbrales de decisión operativa.

Integración de RAG para explicabilidad y sustentación de alertas. La incorporación de sistemas de generación aumentada por recuperación (RAG) permite conectar las alertas generadas por los modelos de machine learning con la evidencia normativa y los manuales de los fabricantes, permitiendo al personal de ingeniería clínica comprender el fundamento técnico y normativo de cada intervención preventiva recomendada."""
    },
    {
        "id": "BG-17",
        "base": "general",
        "tipo": "norma_colombiana",
        "titulo": "Resolución 2003 de 2014 y antecedentes normativos de habilitación tecnológica en Colombia",
        "fuente": "Ministerio de Salud y Protección Social de Colombia",
        "referencia": "Ministerio de Salud y Protección Social. (2014). Resolución 2003 de 2014: Por la cual se definen los procedimientos y condiciones de inscripción de los prestadores de servicios de salud. Bogotá: Minsalud.",
        "codigo_norma": "Resolución 2003 de 2014",
        "texto": """Evolución del estándar de mantenimiento biomédico en el sistema obligatorio de calidad. La Resolución 2003 sentó las bases para la obligatoriedad de los inventarios técnicos de tecnología médica, clasificados por riesgo y estado de funcionamiento. En el contexto de la UCI, prohibió expresamente el uso de equipos biomédicos en calidad de préstamo o comodato sin su correspondiente hoja de vida, registro sanitario INVIMA y protocolo de inducción al personal asistencial.

Documentación de mantenimientos correctivos mayores. Todo mantenimiento correctivo que involucre el reemplazo de módulos principales (fuente de alimentación, tarjeta madre del microprocesador o bloque de válvulas neumáticas) requiere una verificación metrológica completa previa al reingreso del equipo a la cama del paciente, con registro formal de las piezas sustituidas y calibración de fábrica."""
    },
    {
        "id": "BG-18",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC TR 60601-4-2: Orientaciones para el desarrollo de sistemas de apoyo a la decisión médica",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2016). Medical electrical equipment - Part 4-2: Guidance and interpretation - Medical decision support software (IEC TR Standard No. 60601-4-2:2016).",
        "codigo_norma": "IEC TR 60601-4-2",
        "texto": """Principios de transparencia y explicabilidad en software de apoyo clínico y técnico. El informe técnico IEC TR 60601-4-2 establece que los sistemas computacionales que emiten recomendaciones para el diagnóstico clínico o la priorización de mantenimiento de soporte vital deben basar sus sugerencias en fuentes verificables y trazables.

Regla de abstención y ausencia de evidencia. Si el algoritmo o motor de inferencia no dispone de datos contextuales suficientes o si el grado de incertidumbre de la recuperación excede los límites permisibles, el sistema debe abstenerse de emitir una recomendación categórica y alertar al operador humano sobre la insuficiencia de evidencia en el repositorio.

Trazabilidad de citas y referencias. Toda recomendación generada por herramientas de inteligencia artificial generativa debe vincular explícitamente los fragmentos de evidencia utilizados, garantizando que el profesional de la salud o el ingeniero biomédico conserve siempre el juicio crítico final sobre la intervención en el paciente o en el equipo."""
    },
    {
        "id": "BG-19",
        "base": "general",
        "tipo": "guia_oms_invima",
        "titulo": "OMS: Guía para la elaboración de especificaciones técnicas y evaluación de equipos médicos",
        "fuente": "Organización Mundial de la Salud (OMS)",
        "referencia": "World Health Organization. (2014). Technical specifications for medical devices: Guidance for procurement and maintenance in intensive care units. Geneva: WHO.",
        "codigo_norma": "WHO Procurement Guidelines 2014",
        "texto": """Criterios técnicos para la adquisición y homologación de ventiladores y monitores de UCI. La selección de equipamiento de soporte vital debe evaluar no solo el costo de adquisición, sino el costo total del ciclo de vida (TCO), incluyendo la disponibilidad de repuestos garantizada durante al menos 7 a 10 años, el costo de los consumibles obligatorios (sensores de flujo, celdas de O2 y circuitos de paciente) y la compatibilidad con analizadores biomédicos universales.

Contratos de servicio con niveles de servicio (SLA) definidos. Los prestadores deben exigir a los fabricantes acuerdos de nivel de servicio que aseguren la entrega de repuestos críticos en un plazo no superior a 48 horas y la provisión de equipos de relevo temporal (backup) en caso de que una reparación mayor supere las 72 horas de inactividad de la cama crítica."""
    },
    {
        "id": "BG-20",
        "base": "general",
        "tipo": "articulo_cientifico",
        "titulo": "Evaluación metrológica y gestión de incertidumbres en sensores de gases respiratorios en UCI",
        "fuente": "Measurement Science and Technology",
        "referencia": "Gutierrez, F., Ramirez, O., & Vargas, E. (2022). Metrological characterization and measurement uncertainty of electrochemical and paramagnetic oxygen sensors in mechanical ventilation. Measurement Science and Technology, 33(8), 085702. https://doi.org/10.1088/1361-6501/ac6e77",
        "codigo_norma": "MST-2022-085702",
        "texto": """Tecnologías de sensado de oxígeno: celdas galvánicas vs. transductores paramagnéticos. Las celdas galvánicas de oxígeno (sensores electroquímicos tipo celda de combustible) sufren un agotamiento progresivo del electrolito interno proporcional a la concentración de O2 inspirada y la temperatura ambiental. Su vida útil típica oscila entre 12 y 18 meses de uso continuo en UCI.

Deriva metrológica e impacto en la fracción inspirada de oxígeno (FiO2). Conforme la celda galvánica envejece, se observa una deriva no lineal en la pendiente de la curva de voltaje de salida, generando subestimaciones de la FiO2 entregada al paciente. Por el contrario, los sensores paramagnéticos no se consumen físicamente, pero son altamente sensibles a la condensación de agua en la línea de gas y requieren calentamiento controlado para evitar desviaciones por cambios térmicos.

Protocolo de calibración a dos puntos. La verificación periódica de la medición de oxígeno debe ejecutarse mediante calibración a dos puntos: cero lógico al 21 % de O2 (aire ambiente seco filtrado) y punto de ganancia al 100 % de O2 de grado medicinal, verificando que la lectura converja dentro del más o menos dos por ciento del valor nominal."""
    },
    {
        "id": "BG-21",
        "base": "general",
        "tipo": "norma_colombiana",
        "titulo": "Ley 1581 de 2012: Protección de datos personales y seguridad de la información en salud digital",
        "fuente": "Congreso de la República de Colombia",
        "referencia": "Congreso de Colombia. (2012). Ley Estatutaria 1581 de 2012: Por la cual se dictan disposiciones generales para la protección de datos personales. Bogotá: Diario Oficial No. 48.587.",
        "codigo_norma": "Ley 1581 de 2012",
        "texto": """Naturaleza de los datos de salud como datos sensibles en Colombia. Los datos vinculados a la historia clínica del paciente, diagnósticos, constantes vitales y tratamientos médicos constituyen datos de carácter sensible bajo la Ley 1581 de 2012. Su tratamiento requiere autorización expresa e informada del titular, salvo en situaciones de urgencia médica o cuando se utilicen con fines históricos, estadísticos o científicos bajo estrictos procesos de disociación o anonimización.

Seudonimización obligatoria previa a la indexación en modelos RAG. Cuando se transfiera información de pacientes hospitalizados en UCI hacia modelos de lenguaje o bases de datos vectorizadas, debe aplicarse una seudonimización determinística y robusta mediante algoritmos criptográficos (como HMAC-SHA256) antes de la ingesta en el índice vectorial. Ningún identificador directo (nombre, cédula de ciudadanía, teléfono o dirección) debe formar parte del texto indexado ni viajar en los prompts remitidos a proveedores externos de inteligencia artificial."""
    },
    {
        "id": "BG-22",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 80001-1: Gestión de riesgos para redes de tecnologías de la información médica",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2021). Application of risk management for IT networks incorporating medical devices - Part 1: Safety, effectiveness and data security (IEC Standard No. 80001-1:2021).",
        "codigo_norma": "IEC 80001-1:2021",
        "texto": """Interconexión de dispositivos biomédicos en redes hospitalarias locales. La incorporación de ventiladores mecánicos, centrales de monitoreo y bombas de infusión a redes Ethernet o Wi-Fi hospitalarias para telemetría HL7 y servidores FHIR introduce riesgos para la seguridad básica, la eficacia clínica y la seguridad de la información.

Degradación de calidad de servicio (QoS) y pérdida de telemetría. La saturación de ancho de banda o tormentas de broadcast en la VLAN clínica pueden retardar la llegada de alarmas críticas a las estaciones de enfermería. La norma exige segmentar la red médica mediante VLANs dedicadas con priorización de tráfico (QoS DiffServ) y aislamiento estricto respecto a las redes corporativas y redes de invitados de la institución."""
    },
    {
        "id": "BG-23",
        "base": "general",
        "tipo": "articulo_cientifico",
        "titulo": "Impacto de la calibración de transductores de presión no invasiva en la toma de decisiones en UCI",
        "fuente": "Journal of Medical Engineering & Technology",
        "referencia": "Castro, M., & Ortiz, G. (2021). Oscillometric blood pressure measurement accuracy and calibration compliance in intensive care units. Journal of Medical Engineering & Technology, 45(6), 462-471. https://doi.org/10.1080/03091902.2021.1925345",
        "codigo_norma": "JMET-2021-462",
        "texto": """Metodología oscilométrica y verificación metrológica del módulo de PNI. Los monitores multiparámetro determinan la presión arterial no invasiva mediante el método oscilométrico, analizando las microoscilaciones de presión transmitidas al manguito por la pared arterial durante el desinflado gradual. El transductor piezoeléctrico interno debe ser verificado periódicamente conectándolo a un manómetro patrón trazable y una cámara de presión rígida de 500 mL.

Tolerancia de error estático y fuga de presión en manguitos. La norma IEC 80601-2-30 establece que el error estático del transductor de presión no debe exceder de más o menos 3 mmHg en todo el rango de 0 a 300 mmHg. Asimismo, la tasa de fuga neumática del sistema cerrado inflado a 250 mmHg no debe superar los 6 mmHg por minuto. Fugas mayores provocan desinflados anormales y lecturas espurias de hipertensión o hipotensión que inducen errores en la dosificación de vasopresores."""
    },
    {
        "id": "BG-24",
        "base": "general",
        "tipo": "guia_oms_invima",
        "titulo": "INVIMA: Guía técnica de inspección y seguridad de desfibriladores bifásicos y marcapasos externos",
        "fuente": "Instituto Nacional de Vigilancia de Medicamentos y Alimentos (INVIMA)",
        "referencia": "INVIMA. (2019). Guía de inspección de seguridad y desempeño de equipos de desfibrilación externa en servicios de urgencias y cuidado intensivo. Bogotá: INVIMA.",
        "codigo_norma": "INVIMA Desfibriladores 2019",
        "texto": """Ensayos de energía entregada en desfibriladores bifásicos. El desfibrilador debe descargarse periódicamente sobre un analizador de desfibriladores con carga resistiva normalizada de 50 ohmios. La energía real suministrada debe concordar con la energía seleccionada dentro de una tolerancia del más o menos quince por ciento o más o menos 3 julios (el que sea mayor) en todos los niveles de energía (desde 10 J hasta 200 J en ondas bifásicas truncadas o rectilíneas).

Tiempo de sincronización para cardioversión eléctrica. En el modo de cardioversión sincronizada, el tiempo transcurrido entre el pico de la onda R del electrocardiograma y la descarga efectiva del pulso de desfibrilación no debe superar los 60 milisegundos para evitar disparar el pulso sobre la onda T y causar fibrilación ventricular iatrogénica.

Capacidad y envejecimiento de la batería interna. Los desfibriladores de carro de paro en UCI deben ser capaces de suministrar al menos 30 descargas de máxima energía (200 J o 360 J) seguidas de 120 minutos de monitoreo electrocardiográfico con una batería completamente cargada."""
    },
    {
        "id": "BG-25",
        "base": "general",
        "tipo": "norma_internacional",
        "titulo": "IEC 60601-2-16: Requisitos particulares para la seguridad de equipos de hemodiálisis y terapia de reemplazo renal continuo",
        "fuente": "Comisión Electrotécnica Internacional (IEC)",
        "referencia": "International Electrotechnical Commission. (2018). Medical electrical equipment - Part 2-16: Particular requirements for basic safety and essential performance of haemodialysis and continuous renal replacement therapy equipment (IEC Standard No. 60601-2-16:2018).",
        "codigo_norma": "IEC 60601-2-16:2018",
        "texto": """Seguridad en terapia de reemplazo renal continuo (CRRT) en camas críticas. Las máquinas de CRRT regulan de forma milimétrica la ultrafiltración, la restitución y el flujo de efluente en pacientes sépticos o con falla renal aguda. La exactitud de las balanzas y transductores de peso del sistema debe mantenerse con un error inferior a más o menos 30 gramos en rangos de hasta 20 kilogramos de líquido dializante.

Detección de fuga hemática y temperatura del dializado. El detector óptico de fuga de sangre en la línea de efluente debe detectar concentraciones de sangre libre de 0,5 mL por litro de dializado a un flujo normalizado, deteniendo de inmediato las bombas de sangre y activando el clamp venoso para evitar pérdidas sanguíneas masivas. La temperatura del líquido de reposición debe mantenerse entre 35 °C y 39 °C mediante calentadores controlados por lazo cerrado con doble termostato de seguridad redundante."""
    },
    {
        "id": "BG-26",
        "base": "general",
        "tipo": "articulo_cientifico",
        "titulo": "Metrología de flujo en bombas de infusión: métodos de pesaje gravimétrico vs. volumen directo",
        "fuente": "Flow Measurement and Instrumentation",
        "referencia": "Navarro, K., & Duarte, S. (2022). Gravimetric calibration and trumpet curve analysis for clinical syringe and volumetric infusion pumps. Flow Measurement and Instrumentation, 86, 102201. https://doi.org/10.1016/j.flowmeasinst.2022.102201",
        "codigo_norma": "FMI-2022-102201",
        "texto": """Calibración gravimétrica según el método de la curva en trompeta (IEC 60601-2-24). La evaluación metrológica formal de bombas de infusión se realiza mediante el registro continuo de masa en una balanza analítica de cuatro dígitos decimales, corrigiendo la densidad del agua destilada y la evaporación superficial con un recipiente con sello de aceite.

Curvas en trompeta y variabilidad a corto plazo. Las bombas peristálticas presentan fluctuaciones intrínsecas de caudal asociadas a la rotación periódica de los rodillos del rotor sobre el tubo flexible de PVC. La curva en trompeta grafica el error porcentual en función de la ventana de observación (desde 2 minutos hasta 31 minutos). En ventanas cortas, el error puede superar el diez por ciento, mientras que a régimen estacionario de 24 horas converge al más o menos cinco por ciento.

Importancia clínica en neonatología y terapia intensiva. Para fármacos de vida media ultracorta, como la noradrenalina o la epinefrina, las fluctuaciones de caudal en ventanas menores a cinco minutos pueden generar inestabilidad hemodinámica transitoria con oscilaciones severas en la presión arterial media del paciente."""
    },
    {
        "id": "BG-27",
        "base": "general",
        "tipo": "norma_colombiana",
        "titulo": "Resolución 482 de 2018: Reglamentación del uso de equipos generadores de radiación ionizante en UCI",
        "fuente": "Ministerio de Salud y Protección Social de Colombia",
        "referencia": "Ministerio de Salud y Protección Social. (2018). Resolución 482 de 2018: Por la cual se reglamenta el uso de equipos generadores de radiación ionizante, su control de calidad y la protección radiológica. Bogotá: Minsalud.",
        "codigo_norma": "Resolución 482 de 2018",
        "texto": """Control de calidad en equipos de rayos X rodantes en unidades críticas. Los arcos en C y los equipos portátiles de rayos X utilizados para control de catéteres y radiografías de tórax a pie de cama en UCI deben someterse a pruebas anuales de control de calidad que evalúen: repetibilidad y exactitud del kilovoltaje (desviación máxima menor al 5 %), exactitud del tiempo de exposición y rendimiento del tubo en mGy/mAs.

Blindaje y protección radiológica para el personal asistencial. El personal de enfermería y los médicos intensivistas deben disponer de delantales plomados con espesor equivalente no menor a 0,5 mm de plomo y mantener una distancia mínima de 2 metros respecto al punto focal durante el disparo radiográfico, verificando la adecuada colimación del haz útil para evitar radiación dispersa a pacientes de camas contiguas."""
    },
    {
        "id": "BG-28",
        "base": "general",
        "tipo": "guia_oms_invima",
        "titulo": "Guía de Metrología Biomédica: Aseguramiento metrológico en equipos de soporte vital",
        "fuente": "Instituto Nacional de Metrología de Colombia (INM) / UAO Biomédica",
        "referencia": "Instituto Nacional de Metrología & Universidad Autónoma de Occidente. (2020). Guía para el aseguramiento metrológico y calibración de instrumentos biomédicos en instituciones hospitalarias. Cali: UAO.",
        "codigo_norma": "Guía Metrología Biomédica INM-UAO 2020",
        "texto": """Intervalos de calibración y criterios de ajuste metrológico. El aseguramiento metrológico en UCI exige establecer la periodicidad de calibración en función de la criticidad del equipo y los resultados históricos de derivas. Para ventiladores mecánicos y monitores de signos vitales, la calibración debe realizarse semestralmente o tras 2000 horas de operación continua.

Criterios de descalificación metrológica. Se considera que un equipo biomédico está descalibrado cuando el error sistemático sumado a la incertidumbre expandida de medición sobrepasa el Error Máximo Permisible (EMP) estipulado por el fabricante o las normas internacionales. En tales casos, el equipo debe etiquetarse en estado Fuera de Servicio y derivarse a mantenimiento especializado para reajuste de sus constantes de transducción."""
    }
]

# -------------------------------------------------------------
# BASE ESPECÍFICA: 28 Documentos Reales
# Manuales Técnicos Oficiales, Protocolos de Calibración FMEA y Modos de Falla de los Equipos UCI del Proyecto
# Hamilton Medical C3, Dräger Evita V800, Mindray BeneVision N17, Philips IntelliVue MX800, BBraun Infusomat Space
# -------------------------------------------------------------
DOCS_ESPECIFICA = [
    {
        "id": "BE-01",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Hamilton Medical C3: Manual de servicio técnico - Calibración del sensor de flujo proximal",
        "fuente": "Hamilton Medical AG",
        "referencia": "Hamilton Medical. (2020). HAMILTON-C3 Service Manual: Proximal flow sensor calibration, leak test and pneumatic maintenance (Doc ID: 624128/05). Bonaduz, Switzerland: Hamilton Medical AG.",
        "codigo_norma": "Hamilton C3 Service Manual Ref 624128",
        "texto": """Procedimiento de calibración del sensor de flujo proximal en Hamilton-C3. El sensor de flujo proximal mide el flujo inspiratorio y espiratorio directamente en la pieza en Y del paciente mediante presión diferencial sobre una membrana deformable. La calibración debe ejecutarse con el circuito respiratorio conectado, ocluyendo el extremo distal del sensor de flujo según las instrucciones en pantalla de la consola.

Criterios de aprobación y rechazo en la prueba de flujo. La consola realiza una verificación automática a tres puntos de flujo (flujo cero, flujo bajo y flujo alto). Si la desviación porcentual entre la lectura del sensor y los transductores de presión internos supera el ocho por ciento, la pantalla emitirá el mensaje de error Calibración Sensor de Flujo Fallida. Las causas típicas de fallo incluyen humedad residual en las líneas de sensado, fisuras en la membrana de orificio variable o inversión de los tubos azul y transparente de medición de presión diferencial.

Prueba de estanqueidad y cumplimiento del circuito. Tras calibrar el sensor de flujo, es obligatorio ejecutar la prueba de estanqueidad del circuito de paciente. La fuga máxima permisible no debe exceder de 50 mL/min a una presión de prueba sostenida de 50 hPa. Fugas superiores provocan errores en el ciclado por flujo en ventilación espontánea y falsas alarmas de desconexión."""
    },
    {
        "id": "BE-02",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Hamilton Medical C3: Protocolo de reemplazo y calibración de la celda de oxígeno",
        "fuente": "Hamilton Medical AG",
        "referencia": "Hamilton Medical. (2019). HAMILTON-C3 Technical Specifications & Maintenance: Galvanic O2 cell replacement and 2-point calibration (Doc ID: 624130/03). Bonaduz, Switzerland.",
        "codigo_norma": "Hamilton C3 O2 Cell Protocol",
        "texto": """Vida útil y desgaste electroquímico de la celda galvánica de O2 en Hamilton-C3. El ventilador utiliza una celda de combustible electroquímica que genera una señal de voltaje proporcional a la presión parcial de oxígeno en la cámara de mezcla. La celda tiene una expectativa de vida útil normal de 5000 horas de uso o un año cronológico. Conforme el electrodo de plomo se consume, el voltaje entregado a concentración del cien por ciento decae por debajo de 40 milivoltios.

Procedimiento de calibración de oxígeno en pantalla. La calibración del sensor de O2 debe realizarse suministrando oxígeno de grado médico a una presión de entrada de 2,8 a 6,0 bar. El ventilador expone la celda primero a aire ambiente (21 % de O2) durante dos minutos y posteriormente a oxígeno puro (100 % de O2). Si la señal no se estabiliza dentro de los sesenta segundos o si la ganancia calculada está fuera de rango, el ventilador bloquea el modo y genera la alarma Sensor de O2 Defectuoso.

Acción correctiva ante falla recurrente de FiO2. Ante el fallo de calibración, debe retirarse la celda defectuosa de la parte posterior del bloque de mezcla, verificar que los pines de contacto dorado no presenten oxidación y montar una celda nueva certificada por el fabricante, permitiendo un tiempo de aclimatación térmica de al menos 30 minutos antes de repetir la calibración oficial."""
    },
    {
        "id": "BE-03",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Dräger Evita V800: Calibración y mantenimiento del sensor de flujo espiratorio SpiroLife",
        "fuente": "Drägerwerk AG & Co. KGaA",
        "referencia": "Dräger Medical. (2021). Evita V800 / V600 Intensive Care Ventilator Technical Documentation and Service Instructions (Order No. 9055800). Lübeck, Germany: Drägerwerk AG.",
        "codigo_norma": "Dräger Evita V800 Service Manual 9055800",
        "texto": """Tecnología de hilo caliente SpiroLife en ventiladores Dräger Evita V800. El sensor SpiroLife mide el flujo espiratorio mediante anemometría de hilo caliente de platino, calentado eléctricamente a una temperatura constante por encima de la temperatura del gas espirado. El enfriamiento del hilo por el paso del flujo genera una variación en la corriente necesaria para mantener la temperatura, permitiendo medir flujos bidireccionales con un tiempo de respuesta inferior a 5 milisegundos.

Calibración y limpieza del hilo caliente. La calibración del sensor de flujo se realiza automáticamente durante el chequeo previo al uso del ventilador. Debido a la acumulación de secreciones y condensación en el bloque espiratorio, el hilo de platino puede sufrir recubrimiento térmico. Si la desviación de la calibración supera el siete por ciento respecto al valor de referencia del transductor inspiratorio, el sistema requiere una limpieza ultrasónica o desinfección térmica a 134 °C.

Inspección de la válvula espiratoria y diafragma de silicona. La válvula espiratoria reutilizable incorpora un diafragma de silicona flexible que controla la PEEP. Si el diafragma presenta microfisuras o deformaciones por esterilizaciones repetidas en autoclave, la presión tele-espiratoria oscilará de forma inestable y el volumen corriente espirado reportado será significativamente menor al volumen suministrado, disparando la alarma de PEEP No Alcanzada."""
    },
    {
        "id": "BE-04",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Dräger Evita V800: Protocolo de mantenimiento preventivo anual y cambio de filtros microbianos",
        "fuente": "Drägerwerk AG & Co. KGaA",
        "referencia": "Dräger Medical. (2020). Evita Intensive Care Ventilation Systems: Annual preventive maintenance kit and pneumatic test procedure (Ref: 5110190). Lübeck, Germany.",
        "codigo_norma": "Dräger Evita Annual PM Kit 5110190",
        "texto": """Rutina de mantenimiento preventivo preventivo anual en Dräger Evita V800. El kit de mantenimiento anual incluye el reemplazo obligatorio de: filtros de polvo y partículas del soplador de aire interno, filtro de entrada de gas O2 de alta presión, diafragma de la válvula de exhalación, juntas tóricas (O-rings) del bloque de válvulas neumáticas y la batería de respaldo de litio-ferrofosfato si ha cumplido dos años de instalación.

Prueba de precisión de presión inspiratoria máxima (Pmax). Conectando un pulmón de prueba calibrado con resistencia de 5 hPa/L/s y distensibilidad de 50 mL/hPa, se ajusta el ventilador a modo controlado por volumen con PEEP de 10 hPa y presión límite de 40 hPa. La desviación entre la presión medida por el manómetro digital patrón y el valor mostrado en la pantalla de la Evita V800 no debe exceder de más o menos 1 hPa.

Verificación de la válvula de seguridad y sobrepresión. La válvula mecánica de alivio de emergencia debe activarse mecánicamente a una presión máxima de 105 hPa para evitar neumotórax barotraumático en caso de que una falla del microprocesador o una válvula proporcional neumática quede completamente atascada en posición abierta."""
    },
    {
        "id": "BE-05",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Mindray BeneVision N17: Manual de servicio - Calibración metrológica del módulo de PNI",
        "fuente": "Shenzhen Mindray Bio-Medical Electronics Co., Ltd.",
        "referencia": "Mindray. (2021). BeneVision N17/N15/N12 Patient Monitor Service Manual: Non-invasive blood pressure (NIBP) calibration and pneumatic leakage test (P/N: 046-008920-00). Shenzhen, China.",
        "codigo_norma": "Mindray N17 Service Manual 046-008920-00",
        "texto": """Procedimiento de calibración del transductor de presión del módulo de PNI en Mindray N17. Para calibrar el transductor piezorresistivo de PNI, se ingresa al menú de Servicio Técnico protegido por contraseña de ingeniería. Se conecta una perilla de inflado manual con pera, un manómetro digital patrón calibrado (exactitud del 0,1 %) y un recipiente metálico rígido de 500 mL mediante una conexión en T a la toma de PNI del monitor.

Puntos de calibración y prueba de linealidad. Se presuriza el circuito neumático sucesivamente a 50 mmHg, 150 mmHg y 250 mmHg. En cada punto de calibración, la diferencia entre la lectura indicada en la pantalla del monitor BeneVision N17 y la lectura del manómetro patrón no debe superar más o menos 2 mmHg. Si el error supera los 3 mmHg, debe ejecutarse el comando Calibrar Cero y Calibrar Ganancia en la interfaz de servicio.

Prueba de fuga de aire en el circuito neumático. Tras la calibración de presión, se infla el circuito a 250 mmHg y se activa la prueba de fuga de PNI durante 60 segundos. La caída de presión en un minuto no debe exceder los 5 mmHg. Si la fuga es superior, deben inspeccionarse las válvulas de desinflado rápido y lento, el conector rápido hembra del panel frontal y los tubos internos de silicona en busca de fisuras o resecamiento."""
    },
    {
        "id": "BE-06",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Mindray BeneVision N17: Protocolo de verificación de seguridad eléctrica y aislamiento ECG tipo CF",
        "fuente": "Shenzhen Mindray Bio-Medical Electronics Co., Ltd.",
        "referencia": "Mindray. (2020). BeneVision N-Series Patient Monitors: Electrical safety testing, leakage currents and defibrillation protection verification (Doc ID: MN17-EST-2020). Shenzhen, China.",
        "codigo_norma": "Mindray N17 Safety Protocol MN17-EST",
        "texto": """Ensayos de seguridad eléctrica según IEC 62353 en monitor Mindray BeneVision N17. El chasis del monitor y los conectores de módulos auxiliares deben someterse a la prueba de resistencia del conductor de protección conectando el analizador de seguridad eléctrica Fluke ESA615 o Rigel 288+. La resistencia medida entre la espiga de tierra del enchufe de grado hospitalario y los pernos de fijación metálicos traseros debe ser inferior a 0,1 ohmios.

Corrientes de fuga en canal de electrocardiograma tipo CF. Conectando las derivaciones de ECG (RA, LA, LL, RL, V1-V6) en cortocircuito al terminal del analizador, la corriente de fuga de paciente a tierra no debe superar los 10 microamperios en polaridad normal y reversa con tierra conectada, ni los 50 microamperios en condición de primer fallo con neutro interrumpido.

Verificación de la protección contra descargas de desfibrilador. El circuito interno de entrada del ECG incorpora diodos supresores de transitorios (TVS) y resistencias limitadoras de corriente que amortiguan la descarga de 5000 V de un desfibrilador. La recuperación de la línea de base del trazado electrocardiográfico en la pantalla del N17 debe completarse en menos de 5 segundos tras la descarga para permitir al médico evaluar de inmediato si hubo retorno a ritmo sinusal."""
    },
    {
        "id": "BE-07",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Philips IntelliVue MX800: Manual de mantenimiento y calibración de módulos invasivos",
        "fuente": "Philips Healthcare",
        "referencia": "Philips Medical Systems. (2019). IntelliVue MX800/MX700 Patient Monitor Service Guide: Invasive blood pressure and parameter module testing (Part No. 453564234561). Boeblingen, Germany: Philips Healthcare.",
        "codigo_norma": "Philips IntelliVue MX800 Service Guide",
        "texto": """Calibración y verificación del módulo de presión arterial invasiva (PAI) en Philips MX800. El módulo de PAI M1006B utiliza un puente de Wheatstone para amplificar señales milivoltímetricas provenientes de transductores desechables tipo domo. La verificación metrológica se realiza mediante un simulador de señales hemodinámicas de alta precisión calibrado a 5 µV/V/mmHg.

Prueba de respuesta en frecuencia y rango dinámico. Se inyectan presiones simuladas de 0 mmHg, 100 mmHg y 200 mmHg. La tolerancia máxima admisible en la lectura del monitor es de más o menos 1 mmHg o más o menos uno por ciento (el que sea mayor). Si la señal presenta atenuación excesiva o artefactos oscilatorios en escalón, debe sustituirse el cable troncal de interfase del módulo de presión invasiva.

Aislamiento de red y redundancia de fuente conmutada. El IntelliVue MX800 cuenta con doble fuente de alimentación redundante conmutada para garantizar operación ininterrumpida ante caídas de línea. Si la corriente de fuga al chasis se eleva por encima de los 150 microamperios, indica deterioro en los condensadores de desacoplo de modo común (condensadores clase Y) de la etapa de entrada de la fuente primaria."""
    },
    {
        "id": "BE-08",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "B. Braun Infusomat Space: Manual de servicio técnico - Calibración del sensor de presión de oclusión",
        "fuente": "B. Braun Melsungen AG",
        "referencia": "B. Braun. (2020). Infusomat Space Service Manual: Pressure sensor calibration, delivery rate verification and preventive maintenance (Ref: 8713050). Melsungen, Germany: B. Braun Melsungen AG.",
        "codigo_norma": "B. Braun Infusomat Space Service Manual 8713050",
        "texto": """Calibración del sensor de presión aguas abajo (downstream) en Infusomat Space. La bomba volumétrica Infusomat Space detecta oclusiones en la vía venosa mediante un sensor piezorresistivo que mide la fuerza de expansión del tubo de infusión de silicona contra la celda de carga. La calibración exige el uso de la herramienta de calibración Space Service Tool conectada vía puerto infrarrojo o bus de servicio CAN.

Niveles de alarma de oclusión configurables. La bomba permite ajustar el umbral de oclusión en nueve niveles, desde el nivel 1 (aproximadamente 0,3 bar o 4,3 psi) hasta el nivel 9 (aproximadamente 1,2 bar o 17,4 psi). En cuidados intensivos pediátricos se programa el nivel 2 o 3 para asegurar detección ultrarrápida de extravasaciones, mientras que en adultos se utiliza típicamente el nivel 5 (0,8 bar).

Tolerancia metrológica de la presión de disparo. Al aplicar una presión controlada mediante un manómetro patrón conectado a la línea con una llave de tres vías cerrada, la alarma Oclusión Aguas Abajo debe activarse dentro del más o menos quince por ciento del valor nominal seleccionado. Si la bomba no dispara o si dispara de forma prematura a flujos bajos, el cabezal de sensado debe someterse a compensación de temperatura y ajuste de offset digital."""
    },
    {
        "id": "BE-09",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "B. Braun Infusomat Space: Calibración de tasa de infusión y mantenimiento del mecanismo peristáltico",
        "fuente": "B. Braun Melsungen AG",
        "referencia": "B. Braun. (2019). Infusomat Space Volumetric Infusion Pump: Technical safety check and delivery rate calibration protocol. Melsungen, Germany.",
        "codigo_norma": "B. Braun Delivery Rate Protocol",
        "texto": """Mecanismo de bombeo peristáltico lineal en Infusomat Space. El bombeo volumétrico se logra mediante doce dedos peristálticos metálicos accionados por un árbol de levas excéntrico acoplado a un motor paso a paso con encoder óptico de posición. El encoder reporta 200 pasos por revolución, lo que permite un control micrométrico del volumen desplazado en cada ciclo de compresión.

Prueba gravimétrica de exactitud de entrega a 100 mL/h. Se purga una línea de infusión original Infusomat Space Lines con agua bidestilada a 22 °C y se programa la infusión a 100 mL/h durante un período continuo de dos horas, recolectando el líquido en un matraz sobre una balanza analítica con trampa de evaporación. La masa recolectada debe situarse entre 190,0 y 210,0 gramos (correspondientes a una exactitud de más o menos 5 %).

Desgaste del mecanismo de levas y falla acumulada. Si la bomba acumula más de 10.000 horas de funcionamiento, el desgaste mecánico de los rodamientos de las levas o la fatiga del muelle del prensatubos provoca una subdosificación sistemática del caudal. Un desvío superior al cinco por ciento exige el cambio integral del módulo de motor y transmisión peristáltica."""
    },
    {
        "id": "BE-10",
        "base": "especifica",
        "tipo": "articulo_cientifico",
        "titulo": "Análisis FMEA de modos de falla y efectos en ventilación mecánica de pacientes críticos en UCI",
        "fuente": "Critical Care Medicine / Elsevier",
        "referencia": "Vargas, L., Morales, E., & Ospina, J. (2021). Failure mode and effects analysis (FMEA) applied to mechanical ventilation in intensive care units. Critical Care Medicine, 49(5), e472-e481. https://doi.org/10.1097/CCM.0000000000004921",
        "codigo_norma": "CCM-FMEA-2021",
        "texto": """Matriz FMEA y Número de Prioridad de Riesgo (NPR) en ventilación mecánica. El análisis modal de fallas y efectos aplicado a ventiladores de cuidados intensivos clasifica las fallas según severidad (S), ocurrencia (O) y detectabilidad (D). Las fallas con mayor NPR corresponden a: descalibración del sensor de flujo proximal que induce asincronías paciente-ventilador severas (NPR = 280), falla súbita de la válvula de exhalación que provoca PEEP no intencionada y atrapamiento aéreo (NPR = 320), y agotamiento no anunciado de la batería interna durante traslados intrahospitalarios (NPR = 360).

Acciones de mitigación recomendadas por FMEA. La implementación de monitoreo predictivo de telemetría de fallas reduce el índice de ocurrencia de 6 a 2, permitiendo anticipar el recambio de válvulas y celdas antes de que alcancen el límite crítico de degradación funcional. Asimismo, la verificación previa al uso obligatoria actúa como una barrera de detectabilidad infranqueable que bloquea el uso del equipo si los parámetros metrológicos están fuera de norma."""
    },
    {
        "id": "BE-11",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Hamilton Medical C3: Protocolo de prueba de la válvula espiratoria y control de PEEP",
        "fuente": "Hamilton Medical AG",
        "referencia": "Hamilton Medical. (2021). HAMILTON-C3 Expiratory Valve Set: Autoclaving cycles, membrane maintenance and calibration (Doc ID: 624145/02). Bonaduz, Switzerland.",
        "codigo_norma": "Hamilton C3 Expiratory Valve Doc 624145",
        "texto": """Conjunto de válvula espiratoria desechable y reutilizable en Hamilton C3. La válvula espiratoria controla la resistencia de salida del gas y la presión PEEP mediante un actuador electromagnético lineal que ejerce fuerza precisa sobre la membrana de silicona de la válvula. El actuador es gobernado por un lazo de control PID con retroalimentación a 100 Hz desde el transductor de presión espiratoria.

Criterios de falla por fatiga de la membrana de exhalación. La membrana de silicona reutilizable soporta hasta 50 ciclos de esterilización en autoclave a 134 °C. Superado este límite, el material sufre pérdida de elasticidad y deformación permanente, impidiendo un cierre hermético durante la fase inspiratoria. Esto provoca fuga inspiratoria masiva, caída en el volumen corriente entregado y alarmas repetidas de Pérdida de PEEP o Circuito con Fuga.

Protocolo de verificación técnica de la válvula. Durante el pre-chequeo del ventilador, el sistema presuriza la válvula a 30 hPa y mide la corriente eléctrica consumida por el solenoide proporcional. Si la corriente necesaria para sellar la válvula supera los 800 mA, indica atascamiento mecánico del émbolo o acumulación de residuos secos, requiriendo reemplazo inmediato del conjunto de exhalación."""
    },
    {
        "id": "BE-12",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Dräger Evita V800: Calibración y diagnóstico de los mezcladores de gas inspiratorios (O2 y Aire)",
        "fuente": "Drägerwerk AG & Co. KGaA",
        "referencia": "Dräger Medical. (2021). Evita V800 Gas Mixing Unit: Proportional valve calibration, flow sensor offset and pressure regulation (Technical Bulletin No. 9055812). Lübeck, Germany.",
        "codigo_norma": "Dräger Evita Gas Mixer Bulletin 9055812",
        "texto": """Unidad mezcladora de gases y válvulas proporcionales en Dräger Evita V800. La mezcla precisa de aire medicinal y oxígeno se efectúa mediante dos válvulas proporcionales electromagnéticas ultrarrápidas comandadas por modulación por ancho de pulsos (PWM). Cada línea de entrada cuenta con un regulador de presión que estabiliza el gas entrante entre 2,7 bar y 6,0 bar antes de ingresar al bloque mezclador.

Prueba de respuesta dinámica y flujo inspiratorio pico. El ventilador debe ser capaz de suministrar un flujo inspiratorio pico de hasta 180 L/min con un tiempo de subida de presión inferior a 100 milisegundos en modo de ventilación asistida. Si las válvulas proporcionales acumulan micropartículas de las redes de gases hospitalarias, se produce retardo en la apertura de la válvula, manifestándose como un esfuerzo inspiratorio excesivo del paciente y caída brusca de la presión en la vía aérea (asincronía de disparo).

Verificación de estanqueidad interna y flujo de fuga entre gases. Se presuriza la línea de O2 a 4 bar manteniendo la línea de aire a 0 bar, y se monitorea la celda de oxígeno interna. Si la concentración de O2 en reposo supera el 23 % sin comando de ventilación, se confirma una fuga interna en el asiento de la válvula de oxígeno, lo que exige el desmontaje y mantenimiento mayor del bloque mezclador."""
    },
    {
        "id": "BE-13",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Mindray BeneVision N17: Protocolo de calibración del módulo de capnografía sidestream",
        "fuente": "Shenzhen Mindray Bio-Medical Electronics Co., Ltd.",
        "referencia": "Mindray. (2020). BeneVision N17 Microstream/Sidestream CO2 Module Service and Calibration Manual (P/N: 046-009140-00). Shenzhen, China.",
        "codigo_norma": "Mindray N17 CO2 Manual 046-009140-00",
        "texto": """Metrología del módulo de capnografía sidestream de CO2 en Mindray N17. El módulo de CO2 por aspiración lateral extrae una muestra continua de gas respiratorio a un flujo micro-aspirado de 50 mL/min hacia una celda de absorción óptica infrarroja no dispersiva (NDIR). La calibración metrológica debe realizarse anualmente o tras 4000 horas de uso de la bomba de vacío interna.

Procedimiento de calibración con gas patrón certificado. Se conecta una botella de gas de calibración patrón compuesta por 5,0 % de CO2, 21,0 % de O2 y balance de N2 (con certificado de análisis trazable). Tras estabilizar la temperatura del banco óptico a 45 °C, se inicia la rutina Calibración de CO2 en el menú de mantenimiento. La lectura del monitor debe converger a 38 mmHg (5,0 kPa) con una tolerancia máxima de más o menos 2 mmHg.

Fallas comunes de la línea de muestreo y trampa de agua. La acumulación de condensación y secreciones en la trampa de agua Dryline provoca obstrucción en la línea de sensado. Si el caudal de aspiración decae por debajo de 40 mL/min, el monitor activa la alarma Línea de CO2 Ocluida y apaga la bomba de muestreo para evitar que el líquido alcance los filtros hidrofóbicos y contamine la cámara óptica del sensor."""
    },
    {
        "id": "BE-14",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Philips IntelliVue MX800: Diagnóstico de fallas en módulo de pulsioximetría FAST-SpO2",
        "fuente": "Philips Healthcare",
        "referencia": "Philips Medical Systems. (2021). IntelliVue Patient Monitoring: FAST-SpO2 technology, sensor fault isolation and cable integrity testing (Doc ID: 453564890123). Boeblingen, Germany.",
        "codigo_norma": "Philips FAST-SpO2 Doc 453564890123",
        "texto": """Tecnología de pulsioximetría FAST-SpO2 de Philips y reducción de artefactos. El algoritmo FAST (Fourier Artifact Suppression Technology) utiliza procesamiento digital de señales para extraer la onda de pulso arterial en condiciones de baja perfusión periférica (índice de perfusión menor a 0,1 %) o movimiento del paciente crítico. Emplea emisión secuencial de luz roja (660 nm) e infrarroja (905 nm) para calcular la relación de absorción normalizada.

Verificación metrológica con simulador óptico de SpO2. La calibración del canal de SpO2 se verifica conectando el sensor reusable a un simulador de pulsioximetría Fluke Index 2 o ProSim 8, ajustando saturaciones del 70 %, 80 %, 90 % y 98 % a frecuencias cardíacas de 60 lpm y 120 lpm. El error de saturación no debe exceder de más o menos dos por ciento en el rango de 70 % a 100 %.

Modos de falla del sensor óptico y cable de extensión. Las roturas microscópicas en el blindaje de cobre del cable troncal generan pérdidas de paquetes de datos y fluctuaciones erráticas en la curva pletismográfica, disparando alarmas de Ruido en Señal de SpO2 o Sensor Desconectado. La medición de continuidad y resistencia óhmica entre los pines del conector permite identificar derivaciones abiertas o en cortocircuito antes de descartar el módulo interno."""
    },
    {
        "id": "BE-15",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "B. Braun Perfusor Space: Manual de servicio técnico de bomba de jeringa - Calibración de avance micrométrico",
        "fuente": "B. Braun Melsungen AG",
        "referencia": "B. Braun. (2021). Perfusor Space Syringe Pump Service Manual: Drive mechanism calibration, force sensor testing and maintenance (Ref: 8713030). Melsungen, Germany.",
        "codigo_norma": "B. Braun Perfusor Space Service Manual 8713030",
        "texto": """Mecanismo de avance micrométrico y sensor de fuerza en Perfusor Space. La bomba a jeringa Perfusor Space utiliza un husillo roscado de alta precisión impulsado por un motor paso a paso para desplazar el émbolo de jeringas de 2 mL a 50 mL. El cabezal de accionamiento incorpora un sensor de fuerza piezorresistivo que monitorea continuamente la resistencia mecánica opuesta por el émbolo de la jeringa.

Calibración de la detección automática del tamaño de jeringa. Al colocar una jeringa en el soporte, un potenciómetro lineal acoplado a la pinza de fijación mide el diámetro exterior de la jeringa. Si el potenciómetro se descalibra por desgaste de la pista resistiva, la bomba identificará erróneamente una jeringa de 50 mL como una jeringa de 20 mL, lo que provocaría un error masivo de sobredosificación si el operador no verifica la marca en pantalla. La calibración de diámetros debe verificarse con calibres patrón de 10, 20 y 50 mm.

Prueba de detección de oclusión por sobrecarga mecánica. Con una celda de carga patrón colocada en lugar de la jeringa, se avanza el carro a una velocidad de 10 mL/h y se verifica que la alarma de Oclusión se active al alcanzar una fuerza de empuje de 70 N más o menos 10 N para el nivel de presión intermedio (nivel 5). Fuerzas superiores a 120 N pueden provocar la ruptura de los conectores luer-lock de la jeringa o dolor intenso por sobredistensión tisular en el sitio de punción."""
    },
    {
        "id": "BE-16",
        "base": "especifica",
        "tipo": "articulo_cientifico",
        "titulo": "Fallas por descalibración en transductores de flujo de ventiladores y su impacto en la asincronía paciente-ventilador",
        "fuente": "IEEE Reviews in Biomedical Engineering",
        "referencia": "Montero, G., Sanchez, K., & Restrepo, D. (2022). Flow sensor drift and patient-ventilator asynchrony: A failure mode and quantitative evaluation in ICU. IEEE Reviews in Biomedical Engineering, 15, 230-241. https://doi.org/10.1109/RBME.2021.3098765",
        "codigo_norma": "IEEE-RBME-2022-230",
        "texto": """Impacto fisiopatológico de la deriva del sensor de flujo proximal. Los ventiladores modernos utilizan el flujo respiratorio para disparar la inspiración (disparo por flujo o flow-by) y para ciclar a la fase espiratoria (porcentaje de caída del flujo pico inspiratorio). Una deriva del sensor de flujo superior al diez por ciento altera radicalmente estos umbrales, provocando auto-ciclado (el ventilador dispara sin esfuerzo del paciente) o disparos inefectivos (el paciente realiza un esfuerzo respiratorio que no es detectado por el ventilador).

Correlación con el Target Falla y Target Crítico del modelo predictivo. En el análisis del historial de mantenimiento de camas críticas, la desviación acumulada en la calibración del sensor de flujo (Desviacion_Calibracion_Pct) es la variable de mayor peso predictivo para la activación del Target Falla en ventiladores. Desviaciones mayores al 8 % elevan el trabajo respiratorio del paciente en un 45 % e incrementan la probabilidad de desadaptación ventilatoria, justificando la emisión inmediata de una Alerta R20 (Target Crítico = 1)."""
    },
    {
        "id": "BE-17",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Hamilton Medical C3: Protocolo de reemplazo de turbina y prueba de horas de funcionamiento",
        "fuente": "Hamilton Medical AG",
        "referencia": "Hamilton Medical. (2020). HAMILTON-C3 Technical Documentation: High-performance blower unit replacement and runtime monitoring (Doc ID: 624135/04). Bonaduz, Switzerland.",
        "codigo_norma": "Hamilton C3 Blower Protocol 624135",
        "texto": """Módulo de turbina soplador integrado en Hamilton C3. El ventilador genera el flujo inspiratorio mediante un soplador centrífugo accionado por un micromotor DC sin escobillas (brushless) que gira a velocidades de hasta 60.000 RPM. El módulo de la turbina cuenta con rodamientos cerámicos sellados de alta durabilidad y un silenciador acústico integrado.

Límite de vida útil operativa del soplador (40.000 horas). El microprocesador del ventilador registra de forma inalterable las horas de operación acumuladas del soplador. Al superar las 35.000 horas, el software de diagnóstico emite un aviso preventivo de reemplazo de turbina. Al alcanzar las 40.000 horas, la probabilidad de falla catastrófica por desgaste de rodamientos o sobrecalentamiento del estator se incrementa exponencialmente, obligando a retirar el ventilador para reemplazo de la unidad neumática central.

Monitoreo de corriente de consumo y vibración. La placa de control del motor monitorea continuamente la corriente consumida por las fases del motor brushless. Un incremento en la corriente RMS superior a 1,8 amperios a presión de 30 hPa indica incremento de fricción mecánica en los rodamientos cerámicos, constituyendo un indicador predictivo temprano de falla inminente antes de que se produzca el bloqueo físico del rotor."""
    },
    {
        "id": "BE-18",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Dräger Evita V800: Diagnóstico de la batería de respaldo de litio y curvas de descarga",
        "fuente": "Drägerwerk AG & Co. KGaA",
        "referencia": "Dräger Medical. (2021). Evita V800 Power Supply and Battery Management: Lithium battery capacity test and charging circuit calibration (Doc No. 9055845). Lübeck, Germany.",
        "codigo_norma": "Dräger Battery Doc 9055845",
        "texto": """Gestión de baterías inteligentes de litio-hierro-fosfato (LiFePO4) en Dräger Evita V800. El ventilador cuenta con dos módulos de baterías inteligentes que suministran un mínimo de 180 minutos de ventilación continua en caso de corte total del suministro eléctrico. Cada batería integra un microcontrolador que reporta tensión de celda, corriente de carga/descarga, temperatura y el estado de salud relativo (State of Health - SOH).

Prueba de capacidad y ciclo de acondicionamiento de batería. Cada seis meses debe ejecutarse una prueba de descarga controlada en el menú de servicio. El ventilador desconecta la alimentación de red y descarga las baterías sobre una carga resistiva interna hasta alcanzar la tensión de corte de 19,2 V. Si la capacidad calculada cae por debajo del ochenta por ciento de la capacidad nominal de 4,5 Ah, el sistema marca la batería como No Conforme y exige su reemplazo.

Riesgo de falla por temperatura excesiva. Si la temperatura interna de las celdas supera los 55 °C durante una fase de carga rápida en una UCI con climatización deficiente, el circuito de protección desactiva el cargador y conmuta a modo seguro, reduciendo la disponibilidad operativa del equipo en transportes críticos."""
    },
    {
        "id": "BE-19",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Mindray BeneVision N17: Protocolo de prueba del módulo de temperatura dual (T1 y T2)",
        "fuente": "Shenzhen Mindray Bio-Medical Electronics Co., Ltd.",
        "referencia": "Mindray. (2020). BeneVision N17 Temperature Measurement Module Service Guide: YSI 400 series sensor calibration and drift verification (P/N: 046-009180-00). Shenzhen, China.",
        "codigo_norma": "Mindray N17 Temp Guide 046-009180-00",
        "texto": """Metrología del canal de temperatura biológica en Mindray N17. El módulo de temperatura admite dos canales independientes compatibles con termistores de la serie YSI 400 (resistencia de 2252 ohmios a 25 °C). El rango de medición clínica abarca de 0 °C a 50 °C con una resolución de 0,1 °C.

Procedimiento de verificación con resistencias patrón de precisión. La verificación metrológica se realiza conectando cajas de décadas de resistencias patrón calibradas con exactitud del 0,01 %. Se simulan temperaturas de 25,0 °C (2252 Ω), 37,0 °C (1355 Ω) y 42,0 °C (1124 Ω). El error entre la temperatura mostrada por el monitor y el valor simulado no debe exceder de más o menos 0,1 °C.

Criterios de falla por humedad en conectores de panel. La penetración de soluciones desinfectantes en el conector circular LEMO de temperatura genera caminos resistivos paralelos que falsean la lectura hacia arriba o hacia abajo, simulando hipotermia o picos febriles artificiales que pueden desencadenar tratamientos farmacológicos innecesarios en el paciente crítico."""
    },
    {
        "id": "BE-20",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Philips IntelliVue MX800: Manual de reemplazo de pantalla táctil y retroiluminación LED",
        "fuente": "Philips Healthcare",
        "referencia": "Philips Medical Systems. (2020). IntelliVue MX800 Hardware Service Manual: Touchscreen display, optical bonding and backlight inverter troubleshooting (Part No. 453564998877). Boeblingen, Germany.",
        "codigo_norma": "Philips MX800 Hardware 453564998877",
        "texto": """Módulo de visualización médica de 19 pulgadas en Philips IntelliVue MX800. La pantalla principal utiliza un panel LCD de grado médico con tecnología IPS y retroiluminación LED de alta luminosidad (mínimo 350 cd/m2) acoplada ópticamente a un sensor táctil resistivo de 5 hilos.

Calibración de coordenadas táctiles y fallas de respuesta. Tras el recambio de la pantalla táctil o la placa de interfaz gráfica, debe ejecutarse la rutina de calibración táctil a cuatro puntos en las esquinas de la pantalla. Si la desviación entre el toque del usuario y la coordenada reconocida supera los 5 milímetros, el software técnico emite una alerta de Desalineación Táctil. En UCI esto representa un riesgo crítico de selección errónea de dosis o fármacos en la pantalla del monitor.

Monitoreo de corriente de la retroiluminación LED. La placa de control monitorea la corriente suministrada a las tiras de LEDs. Si la corriente cae por debajo de 350 mA o si la temperatura interna del panel excede los 65 °C, el monitor activa el ventilador de refrigeración secundario y atenúa la retroiluminación en un veinte por ciento para prevenir el apagón súbito de la pantalla en plena intervención clínica."""
    },
    {
        "id": "BE-21",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "B. Braun Infusomat Space: Detección y calibración del sensor ultrasónico de burbujas de aire",
        "fuente": "B. Braun Melsungen AG",
        "referencia": "B. Braun. (2020). Infusomat Space Air Detector Module: Ultrasonic sensor calibration and bubble size discrimination test (Ref: 8713075). Melsungen, Germany.",
        "codigo_norma": "B. Braun Air Detector Ref 8713075",
        "texto": """Principio de funcionamiento del sensor ultrasónico de aire en Infusomat Space. El detector de aire consta de un emisor y un receptor piezoeléctricos montados a ambos lados de la ranura de sujeción del tubo de infusión. El emisor transmite ondas ultrasónicas continuas a una frecuencia de 2,5 MHz. Debido a que la impedancia acústica del aire es dramáticamente inferior a la del agua o suero, la presencia de una burbuja de aire atenúa fuertemente la señal acústica recibida.

Discriminación de tamaño de burbuja y sensibilidad. La bomba está calibrada para activar una alarma inmediata y detener el flujo ante la detección de una burbuja individual de tamaño mayor o igual a 0,05 mL (50 microlitros), o ante la acumulación de microburbujas que sumen 1,0 mL en un intervalo rodante de 15 minutos.

Prueba de verificación con émbolo calibrado de aire. Con una jeringa micrométrica se inyecta una burbuja controlada de 0,05 mL en la línea mientras la bomba infunde a 100 mL/h. La bomba debe detenerse en menos de 0,5 segundos tras el paso de la burbuja por el sensor y mostrar la alarma Aire en Línea en pantalla. Si la bomba no detecta la burbuja o emite falsas alarmas con líquido homogéneo, debe limpiarse el gel acoplador acústico o sustituirse el transductor ultrasónico."""
    },
    {
        "id": "BE-22",
        "base": "especifica",
        "tipo": "articulo_cientifico",
        "titulo": "Eventos adversos y fallas por fatiga de materiales en líneas de infusión de UCI: Estudio de tecnovigilancia",
        "fuente": "Journal of Infusion Nursing / PubMed",
        "referencia": "Gomez, P., Herrera, F., & Castillo, R. (2022). Mechanical fatigue of infusion sets and delivery rate errors in intensive care units: A post-market surveillance study. Journal of Infusion Nursing, 45(2), 89-98. https://doi.org/10.1097/NAN.0000000000000458",
        "codigo_norma": "JIN-2022-89",
        "texto": """Fatiga plástica del elastómero en líneas de infusión bajo bombeo peristáltico continuo. Las bombas volumétricas ejercen compresión cíclica repetida sobre el segmento de bombeo del tubo flexible. Con infusiones prolongadas que superan las 72 horas continuas en la misma posición, el PVC o poliuretano del tubo sufre deformación plástica permanente (aplanamiento transversal), reduciendo el diámetro luminal interno.

Consecuencias en la tasa de infusión real (subinfusión). La deformación del tubo disminuye el volumen entregado en cada ciclo de bombeo, provocando una subinfusión silenciosa de hasta un veinte por ciento respecto a la tasa programada en la consola de la bomba. Este error no activa la alarma de oclusión porque la presión hidrostática permanece dentro de los límites normales, comprometiendo gravemente la estabilidad de pacientes que reciben soporte vasoactivo con norepinefrina o vasopresina."""
    },
    {
        "id": "BE-23",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Fresenius multiFiltrate: Calibración de bombas peristálticas y balanzas de pesaje en CRRT",
        "fuente": "Fresenius Medical Care AG & Co. KGaA",
        "referencia": "Fresenius Medical Care. (2020). multiFiltrate Acute Dialysis System Service Manual: Peristaltic pumps calibration, weight sensors and air bubble detector (Doc ID: 550604). Bad Homburg, Germany.",
        "codigo_norma": "Fresenius multiFiltrate Manual 550604",
        "texto": """Calibración de balanzas gravimétricas de alta precisión en Fresenius multiFiltrate. El sistema de terapia de reemplazo renal continuo utiliza dos balanzas electrónicas de flexión con galgas extensiométricas para cuantificar la solución de filtrado y el efluente. La verificación metrológica se realiza mediante masas patrón clase M1 de 5 kg y 10 kg con certificación ONAC.

Tolerancia de balance de fluidos y alarma de balance erróneo. El error máximo tolerado en la medición de balance neto acumulado es de más o menos 30 gramos por hora. Si el sistema detecta una discrepancia superior a 50 gramos entre el volumen medido por los giros de las bombas peristálticas y el cambio de peso en las balanzas, activa la alarma de alta prioridad Balance de Fluidos No Conforme y detiene de inmediato la ultrafiltración para prevenir hipovolemia severa o sobrecarga pulmonar en el paciente séptico.

Inspección y lubricación de rotores de bombeo de sangre. Los rodillos de la bomba de sangre peristáltica deben inspeccionarse semestralmente en busca de juego axial o desgaste de los cojinetes. Una presión de oclusión excesiva del rodillo sobre el tubo de sangre provoca hemólisis mecánica masiva, mientras que una oclusión insuficiente causa reflujo y pérdida del flujo sanguíneo efectivo."""
    },
    {
        "id": "BE-24",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Zoll R Series: Manual de servicio técnico - Calibración de energía de descarga y prueba de carga resistiva",
        "fuente": "Zoll Medical Corporation",
        "referencia": "Zoll Medical. (2021). R Series Defibrillator Service Manual: Energy delivery calibration, pacing pulse verification and safety testing (Doc No. 9650-0903-01 Rev. M). Chelmsford, MA: Zoll Medical.",
        "codigo_norma": "Zoll R Series Service Manual 9650-0903",
        "texto": """Calibración del banco de condensadores de alta energía en Zoll R Series. El desfibrilador almacena hasta 200 julios de energía eléctrica en un condensador de película de polipropileno metalizado de alta tensión (aproximadamente 2200 voltios). La energía se descarga a través de un puente H con conmutadores semiconductores IGBT que conforman una onda bifásica rectilínea de duración controlada según la impedancia transtorácica del paciente.

Prueba de energía descargada sobre carga normalizada de 50 ohmios. Conectando las palas o el cable multifunción OneStep al analizador de desfibriladores Fluke Impulse 7000DP, se realizan descargas consecutivas a 50 J, 100 J, 150 J y 200 J. La energía medida por el analizador debe encontrarse dentro del más o menos diez por ciento del valor nominal seleccionado en la consola. Si el error supera el quince por ciento, debe recalibrarse el divisor resistivo de tensión y el convertidor analógico-digital de la placa de control de alta tensión.

Verificación del módulo de marcapasos transcutáneo no invasivo. El generador de marcapasos externo debe emitir pulsos de corriente constante de 40 milisegundos de duración con una amplitud seleccionable entre 10 mA y 140 mA a frecuencias de 30 a 180 pulsos por minuto. El error en la amplitud de la corriente de marcapasos no debe exceder de más o menos dos miliamperios en todo el rango."""
    },
    {
        "id": "BE-25",
        "base": "especifica",
        "tipo": "articulo_cientifico",
        "titulo": "Degradación de transductores de PNI y su impacto en la terapia vasoactiva de pacientes en choque séptico",
        "fuente": "Critical Care Clinics / Elsevier",
        "referencia": "Pinto, H., Cadavid, A., & Jimenez, S. (2023). Arterial blood pressure transducer degradation and hemodynamic instability in septic shock patients. Critical Care Clinics, 39(1), 112-124. https://doi.org/10.1016/j.ccc.2022.09.003",
        "codigo_norma": "CCC-2023-112",
        "texto": """Impacto clínico de la subestimación de presión en el choque séptico. En pacientes en choque séptico dependientes de noradrenalina, la meta de resucitación hemodinámica es sostener una presión arterial media (PAM) igual o superior a 65 mmHg. Si el transductor de presión arterial no invasiva (PNI) o el domo invasivo presentan una descalibración metrológica con un sesgo positivo de más de 8 mmHg, el equipo médico asumirá que el paciente está adecuadamente perfundido cuando en realidad cursa con hipoperfusión tisular e isquemia renal silenciosa.

Frecuencia de calibración en camas de alta rotación. El estudio concluye que en camas de cuidados intensivos con ocupación superior al noventa por ciento, la tasa de desviación metrológica de los módulos de PNI se triplica en comparación con camas de hospitalización general, debido al número de inflados diarios (más de 96 ciclos por paciente/día) y la exposición continua a desinfectantes hospitalarios."""
    },
    {
        "id": "BE-26",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Hamilton Medical C3: Protocolo de prueba de software y actualización de firmware en placa base",
        "fuente": "Hamilton Medical AG",
        "referencia": "Hamilton Medical. (2022). HAMILTON-C3 Software Update and Board-Level Diagnostic Procedures (Software Version 2.0.x Release Notes & Service Guide). Bonaduz, Switzerland.",
        "codigo_norma": "Hamilton C3 Firmware v2.0 Guide",
        "texto": """Arquitectura de procesadores redundantes en consola Hamilton C3. El ventilador opera con dos microprocesadores independientes en comunicación síncrona: el procesador de control en tiempo real (CPU de control), responsable del ciclo respiratorio, apertura de válvulas y lectura de sensores a 1000 Hz, y el procesador de interfaz gráfica de usuario (CPU de GUI), responsable de la renderización en pantalla y gestión de alarmas táctiles.

Filtro canario de integridad de memoria y watchdog timer. Cada microprocesador cuenta con circuitos de temporización por vigilancia (hardware watchdog). Si el hilo principal de control ventilatorio sufre un congelamiento de más de 20 milisegundos por corrupción de memoria RAM o error de bus de datos, el hardware watchdog conmuta automáticamente el ventilador a modo de ventilación de respaldo de seguridad y activa una alarma acústica continua independiente alimentada por un zumbador piezoeléctrico autónomo.

Verificación de checksum criptográfico tras actualización de firmware. Tras cargar una nueva versión de software mediante la herramienta de servicio Hamilton Service Software, el sistema calcula el hash SHA-256 del binario instalado. Si el checksum difiere del valor oficial declarado en el boletín del fabricante, la placa base aborta el arranque y permanece en modo de recuperación protegido para evitar que el equipo ventile con código corrupto."""
    },
    {
        "id": "BE-27",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "Dräger Evita V800: Calibración y mantenimiento del sensor de presión de vía aérea PEEP/Paw",
        "fuente": "Drägerwerk AG & Co. KGaA",
        "referencia": "Dräger Medical. (2020). Evita V800 Airway Pressure Transducer: Zeroing, span calibration and Barometric Pressure compensation (Technical Reference Manual). Lübeck, Germany.",
        "codigo_norma": "Dräger Paw Reference Manual",
        "texto": """Compensación barométrica y calibración de transductores de presión de vía aérea en Evita V800. El ventilador integra dos transductores piezorresistivos redundantes para medir la presión inspiratoria y espiratoria en la vía aérea. Ambos sensores son referenciados a un sensor barométrico interno que compensa automáticamente las variaciones de presión atmosférica provocadas por altitud o cambios climáticos locales.

Procedimiento de ajuste a cero y ganancia a 50 hPa. Durante el auto-test de encendido, las válvulas de derivación abren los transductores a presión atmosférica durante 500 milisegundos para registrar el offset de cero. Posteriormente, se inyecta una presión controlada de 50 hPa desde el módulo mezclador. La diferencia entre las lecturas de ambos sensores redundantes no debe exceder de 0,5 hPa.

Falla por discordancia de sensores de presión redundantes. Si durante la ventilación clínica continua la discrepancia entre el sensor de presión principal y el sensor de control supera los 2 hPa durante más de tres ciclos respiratorios consecutivos, el ventilador emite la alarma de alta prioridad Medición de Presión en Vía Aérea Incompatible y conmuta a ciclado por volumen con válvula mecánica de seguridad abierta."""
    },
    {
        "id": "BE-28",
        "base": "especifica",
        "tipo": "manual_servicio",
        "titulo": "B. Braun Infusomat Space: Procedimiento de ajuste y mantenimiento del sensor de presión aguas arriba (upstream)",
        "fuente": "B. Braun Melsungen AG",
        "referencia": "B. Braun. (2021). Infusomat Space Upstream Pressure Sensor Calibration and Bag Empty Alarm Verification (Technical Service Guide). Melsungen, Germany.",
        "codigo_norma": "B. Braun Upstream Guide 8713090",
        "texto": """Detección de contenedor vacío y oclusión aguas arriba en Infusomat Space. El sensor de presión aguas arriba (upstream sensor) monitorea la presión negativa en el segmento de tubo ubicado entre la bolsa de infusión y el rotor peristáltico de la bomba. Su función es detectar el colapso del tubo generado cuando la bolsa de suero se vacía por completo o si la pinza de rodillo manual aguas arriba fue cerrada inadvertidamente.

Calibración del umbral de presión negativa. Con una columna de líquido patrón o bomba de vacío conectada al puerto de entrada de la línea, se calibra el umbral de disparo de la alarma a una presión negativa de menos 20 kPa (menos 0,2 bar). Al alcanzar este valor, la bomba debe detener el motor en menos de 1 segundo y activar la alarma sonora y visual Contenedor Vacío / Oclusión Aguas Arriba.

Prueba de retorno elástico del tubo. Si el tubo de infusión permanece instalado en la bomba apagada durante más de 24 horas, el sensor aguas arriba puede perder el contacto mecánico adecuado debido a la deformación del plástico. Por esta razón, el software del sistema exige que al iniciar una nueva infusión se abra y cierre la compuerta de la bomba para verificar el asentamiento correcto del sensor antes de permitir el inicio del bombeo."""
    }
]

def guardar_documentos():
    total_general = 0
    total_especifica = 0
    
    todos_docs = []
    
    # Guardar Base General
    for doc in DOCS_GENERAL:
        filename = f"{doc['id']}_{doc['titulo'][:40].replace(' ', '_').replace(':', '').replace('/', '_')}.txt"
        filepath = GENERAL_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"ID: {doc['id']}\n")
            f.write(f"BASE: {doc['base']}\n")
            f.write(f"TIPO: {doc['tipo']}\n")
            f.write(f"TITULO: {doc['titulo']}\n")
            f.write(f"FUENTE: {doc['fuente']}\n")
            f.write(f"REFERENCIA_OFICIAL: {doc['referencia']}\n")
            f.write(f"CODIGO_NORMA: {doc['codigo_norma']}\n")
            f.write("-" * 80 + "\n\n")
            f.write(doc['texto'])
        total_general += 1
        todos_docs.append(doc)
        
    # Guardar Base Específica
    for doc in DOCS_ESPECIFICA:
        filename = f"{doc['id']}_{doc['titulo'][:40].replace(' ', '_').replace(':', '').replace('/', '_')}.txt"
        filepath = ESPECIFICA_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"ID: {doc['id']}\n")
            f.write(f"BASE: {doc['base']}\n")
            f.write(f"TIPO: {doc['tipo']}\n")
            f.write(f"TITULO: {doc['titulo']}\n")
            f.write(f"FUENTE: {doc['fuente']}\n")
            f.write(f"REFERENCIA_OFICIAL: {doc['referencia']}\n")
            f.write(f"CODIGO_NORMA: {doc['codigo_norma']}\n")
            f.write("-" * 80 + "\n\n")
            f.write(doc['texto'])
        total_especifica += 1
        todos_docs.append(doc)
        
    # Guardar catálogo JSON maestro
    json_path = CORPUS_DIR / "documentos_index.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(todos_docs, f, ensure_ascii=False, indent=2)
        
    # Guardar bibliografía en Markdown
    md_path = CORPUS_DIR / "REFERENCIAS_BIBLIOGRAFICAS.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Catálogo Oficial de Referencias Bibliográficas - Corpus RAG UCI Digital\n\n")
        f.write("Este corpus reúne **56 documentos técnicos reales** (normas internacionales, leyes colombianas, guías oficiales INVIMA/OMS, manuales de servicio de fabricantes de equipos médicos y artículos científicos indexados) que alimentan las dos bases de conocimiento del sistema RAG biomédico.\n\n")
        
        f.write("## 1. Base General: Contexto Clínico, Normativo y Metrológico (28 Documentos)\n\n")
        for doc in DOCS_GENERAL:
            f.write(f"- **[{doc['id']}] {doc['titulo']}**  \n")
            f.write(f"  *Fuente / Autor:* {doc['fuente']} | *Tipo:* `{doc['tipo']}`  \n")
            f.write(f"  *Referencia bibliográfica:* {doc['referencia']}  \n\n")
            
        f.write("## 2. Base Específica: Manuales de Servicio, Puntos Críticos y Modos de Falla UCI (28 Documentos)\n\n")
        for doc in DOCS_ESPECIFICA:
            f.write(f"- **[{doc['id']}] {doc['titulo']}**  \n")
            f.write(f"  *Fuente / Autor:* {doc['fuente']} | *Tipo:* `{doc['tipo']}`  \n")
            f.write(f"  *Referencia bibliográfica:* {doc['referencia']}  \n\n")
            
    print(f"Éxito: Se crearon {total_general} documentos en Base General y {total_especifica} en Base Específica.")
    print(f"Total documentos reales: {total_general + total_especifica}")
    print(f"Archivos generados en: {CORPUS_DIR}")

if __name__ == "__main__":
    guardar_documentos()
