---
name: control-glucemico
description: Cómo interpretar glucosa y HbA1c de un paciente frente a metas clínicas y reportar al médico.
---
# Interpretación del control glucémico

1. Obtén HbA1c y glucosa con `consultar_paciente`.
2. Busca la meta con `buscar_guias("meta de HbA1c en adultos")`.
3. Clasifica: en meta (< 7.0%), fuera de meta leve (7.0 - 8.0%) o fuera de meta marcado (> 8.0%).
4. Si la glucosa es menor a 70 mg/dL, prioriza la conducta de hipoglucemia urgente.
5. Reporta: valor medido, meta de referencia, clasificación clínica y fuentes con [n]. Nunca formules dosis farmacológicas.
