---
name: remision-especialista
description: Procedimiento clínico del médico para evaluar, decidir y emitir una remisión médica o interconsulta a especialista (oftalmología, endocrinología, nutrición o institución socia).
---
# Remisión Médica e Interconsulta Especializada (Rol Médico)

Este procedimiento es de uso prioritario para el **Médico Tratante / Médico de UCI**, quien ostenta la potestad clínica y legal exclusiva de ordenar, aprobar o rechazar cualquier remisión médica o técnica antes de su despacho formal:

1. **Revisión del Paciente por el Médico**:
   - Llama a `consultar_paciente` o `consultar_telemetria_equipo` para obtener las variables clínicas, signos vitales y dispositivos de soporte vital del paciente en cama UCI.

2. **Búsqueda de Evidencia en Guías Clínicas**:
   - Llama a `buscar_guias` con el hallazgo clínico específico detectado (ej. *"retinopatía diabética grado 2 criterios de remisión a oftalmología"*, *"meta de HbA1c descompensada remisión a endocrinología"*, o *"desviación metrológica en ventilador soporte biomédico"*).

3. **Justificación Médica con Evidencia**:
   - La remisión solo procede si está respaldada por una guía de práctica clínica o norma técnica del sistema.
   - El médico estructura el motivo clínico bajo el formato:
     `"<hallazgo clínico/fisiológico> + <criterio de guía o norma [n]>"`.
     *Ejemplo:* *"Paciente con HbA1c 8.1% y retinopatía no proliferativa moderada grado 2; la Guía de Práctica Clínica [GC-02] exige remisión prioritaria a oftalmología para tamizaje con retinografía."*

4. **Emisión de la Propuesta de Remisión**:
   - Llama a `proponer_remision` especificando la especialidad requerida (ej. *"oftalmologia"*, *"endocrinologia"*, *"nutricion"*, *"ingenieria_biomedica"*) y el motivo estructurado.

5. **Aprobación y Ratificación Médica Exclusiva (HITL / R10 / R24)**:
   - Todo lo que produce el agente entra como **borrador en estado pendiente** (*soft put*).
   - **Únicamente el Médico** tiene la autorización de aprobar (`approve`), editar (`edit`) o rechazar (`reject`) la remisión en el arnés clínico.
   - Si el médico decide rechazarla, el agente no insiste, resume el motivo clínico del descarte y solicita instrucciones adicionales.
   - Si el médico la aprueba, la remisión queda lista para ser empaquetada como recurso FHIR `ServiceRequest` / `Bundle` para su envío interinstitucional seguro (R30).
