---
name: metrologia-uci
description: Procedimiento para verificar tolerancias de ventiladores mecánicos, bombas de infusión, seguridad eléctrica IEC 62353 y telemetría de variables para interoperabilidad.
---
# Verificación Metrológica de Equipos UCI

1. Llama a `consultar_paciente` para identificar la cama y el equipo biomédico asignado (ej. Hamilton C3, Dräger Evita, Infusomat).
2. Llama a `buscar_guias` con el parámetro evaluado (ej. "tolerancia volumen corriente ventilador UCI" o "límite corriente de fuga IEC 62353").
3. Compara el valor registrado frente a los límites normativos:
   - Resistencia de tierra: <= 0.2 ohmios (o 0.3 con cable separable).
   - Corriente de fuga a tierra: <= 500 uA.
   - Error de volumen corriente (Vt): maximo +-10%.
   - Exactitud de flujo en bombas: +-5%.
4. Si hay desviación fuera de tolerancia, llama a `proponer_remision` hacia "ingenieria_biomedica" solicitando calibración correctiva inmediata.

---

## 5. Telemetría y Variables Asignadas al Paciente para Interoperabilidad (HL7 FHIR)
Cuando el clínico o el sistema solicite las variables del paciente o los equipos biomédicos a los que está conectado:
1. Llama a `consultar_telemetria_equipo` (o `consultar_paciente`) para extraer el mapeo completo del paciente con sus dispositivos biomédicos de soporte vital activos:
   - **Ventilador Mecánico (ej. Hamilton C3 / Dräger Evita V800):**
     * Volumen Corriente (Vt): ej. 480 mL (LOINC 76288-0).
     * Presión Pico (Ppico): ej. 22 hPa (LOINC 76540-4).
     * PEEP: ej. 6 hPa (LOINC 76541-2).
     * Frecuencia Respiratoria: ej. 16 rpm (LOINC 9279-1).
     * Fracción Inspirada de Oxígeno (FiO2): ej. 40% (LOINC 19996-8).
   - **Monitor de Signos Vitales (ej. Philips IntelliVue MX800 / Mindray BeneVision N17):**
     * Presión Arterial No Invasiva (PNI): Sistólica/Diastólica/Media ej. 138/86 mmHg (Media: 103 mmHg) (LOINC 85354-9).
     * Frecuencia Cardíaca (FC): ej. 82 lpm (LOINC 8867-4).
     * Saturación de Oxígeno (SpO2): ej. 96% (LOINC 2708-6).
     * Temperatura Corporal: ej. 36.7 °C (LOINC 8310-5).
   - **Bomba de Infusión Volumétrica (ej. B. Braun Infusomat Space):**
     * Tasa de Flujo de Infusión: ej. 25.0 mL/h (LOINC 93040-4).
     * Volumen Infundido Acumulado: ej. 250.0 mL.
     * Presión de Oclusión en Línea: ej. 120 mmHg.
2. Si el usuario solicita exportar o interoperar estos datos con otra institución o equipo socio (R30-R33), presenta la tabla estructurada con sus unidades y ofrece la exportación a formato XML interoperable o documento Word mediante `formatear_salida(datos, formato="xml")` o `formatear_salida(datos, formato="word")`.
