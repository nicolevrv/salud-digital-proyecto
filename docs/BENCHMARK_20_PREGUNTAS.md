# Benchmark Oficial de Evaluación: 20 Preguntas Técnicas y Clínicas de UCI
**Hospital Universitario UCI Digital · RAG Biomédico (Semana 12 · Salud Digital UAO)**

Este documento contiene las **20 preguntas técnicas y clínicas** utilizadas para la evaluación formal de recuperación (**Recall@1, Recall@3, MRR**) y generación con **RAGAS** (Fidelidad, Relevancia, Precisión y Exhaustividad). Cada pregunta cuenta con su documento esperado (*ground truth*), código de norma o manual oficial, y el fragmento textual de referencia.

---

## 1. Preguntas sobre la Base General (Normativa, Seguridad y Metrología)

### Pregunta 1
* **Pregunta:** ¿Cuál es el límite máximo de resistencia de tierra en equipos Clase I según IEC 62353?
* **Documento Esperado:** `[BG-01]` IEC 62353:2014 (Ensayos periódicos de seguridad eléctrica).
* **Fuente:** Comisión Electrotécnica Internacional (IEC).
* **Respuesta Técnica / Criterio:**  
  Para la resistencia del conductor de protección a tierra en equipos de Clase I, el valor medido no debe exceder de **0,2 ohmios** con cable desmontable, o **0,3 ohmios** con cable fijo.

---

### Pregunta 2
* **Pregunta:** ¿Cuánto es la corriente de fuga de paciente admisible en partes aplicadas Tipo CF?
* **Documento Esperado:** `[BG-02]` IEC 60601-1 Ed. 3.2 (Seguridad básica y funcionamiento esencial).
* **Fuente:** Comisión Electrotécnica Internacional (IEC).
* **Respuesta Técnica / Criterio:**  
  Para partes aplicadas Tipo CF (contacto cardíaco directo), la corriente de fuga de paciente no debe exceder los **10 microamperios (µA)** en condición normal ni **50 microamperios (µA)** en condición de primer fallo.

---

### Pregunta 3
* **Pregunta:** ¿Qué distancia de separación se recomienda frente a transmisores de radiofrecuencia en UCI?
* **Documento Esperado:** `[BG-03]` IEC 60601-1-2:2020 (Compatibilidad electromagnética).
* **Fuente:** Comisión Electrotécnica Internacional (IEC).
* **Respuesta Técnica / Criterio:**  
  Se recomienda mantener una distancia mínima de **al menos un metro** entre transmisores móviles de radiofrecuencia y las consolas o ventiladores de UCI para evitar interferencias en la sensórica.

---

### Pregunta 4
* **Pregunta:** ¿Qué dotación mínima obligatoria por cama de UCI adultos exige la Resolución 3100 de 2019?
* **Documento Esperado:** `[BG-04]` Resolución 3100 de 2019 (Habilitación de servicios de salud en Colombia).
* **Fuente:** Ministerio de Salud y Protección Social de Colombia.
* **Respuesta Técnica / Criterio:**  
  Cada cama de UCI Adultos debe contar con: un ventilador mecánico microprocesado, un monitor multiparámetro (ECG, PNI, SpO2, temperatura), al menos dos bombas de infusión volumétricas y una bomba de infusión a jeringa.

---

### Pregunta 5
* **Pregunta:** ¿En qué plazo debe reportarse al INVIMA un evento adverso serio según el Decreto 4725?
* **Documento Esperado:** `[BG-05]` Decreto 4725 de 2005 (Dispositivos médicos y tecnovigilancia).
* **Fuente:** Ministerio de la Protección Social / INVIMA.
* **Respuesta Técnica / Criterio:**  
  Los prestadores deben notificar cualquier evento adverso serio al INVIMA dentro de las **setenta y dos (72) horas** siguientes a su ocurrencia.

---

### Pregunta 6
* **Pregunta:** ¿Cuál es la diferencia entre evento adverso e incidente adverso en tecnovigilancia?
* **Documento Esperado:** `[BG-06]` Manual de Tecnovigilancia INVIMA (Protocolo FOREIA).
* **Fuente:** Instituto Nacional de Vigilancia de Medicamentos y Alimentos (INVIMA).
* **Respuesta Técnica / Criterio:**  
  El incidente adverso es un potencial daño que no se materializó gracias a una barrera de seguridad o intervención profesional; el evento adverso ocurre cuando el daño no intencionado sí se materializa en el paciente o usuario.

---

### Pregunta 7
* **Pregunta:** ¿Cómo reduce el mantenimiento predictivo las paradas imprevistas según la OMS?
* **Documento Esperado:** `[BG-07]` WHO Medical Device Technical Series (Gestión del mantenimiento de equipos).
* **Fuente:** Organización Mundial de la Salud (OMS).
* **Respuesta Técnica / Criterio:**  
  El mantenimiento predictivo mediante monitoreo de condición física y operativa permite reducir las paradas no programadas en unidades críticas **hasta en un 40 %**, interviniendo antes de la falla catastrófica.

---

### Pregunta 8
* **Pregunta:** ¿Qué exige la norma ISO 13485 sobre la validación de software de mantenimiento y calibración?
* **Documento Esperado:** `[BG-08]` ISO 13485:2016 (Sistemas de gestión de calidad para dispositivos médicos).
* **Fuente:** Organización Internacional de Normalización (ISO).
* **Respuesta Técnica / Criterio:**  
  Todo software o algoritmo de IA utilizado para tomar decisiones sobre la aptitud clínica de un dispositivo médico debe ser **formalmente validado antes de su puesta en producción** y documentado en el sistema de gestión.

---

### Pregunta 9
* **Pregunta:** ¿Qué factor de cobertura k y nivel de confianza exige la norma ISO/IEC 17025 para incertidumbre?
* **Documento Esperado:** `[BG-09]` ISO/IEC 17025:2017 (Laboratorios de ensayo y calibración).
* **Fuente:** Organización Internacional de Normalización (ISO).
* **Respuesta Técnica / Criterio:**  
  Exige reportar la incertidumbre expandida de medición con un **factor de cobertura k=2**, correspondiente a un **nivel de confianza del 95 %**.

---

### Pregunta 10
* **Pregunta:** ¿Qué indica un descenso del MTBF por debajo de 180 días en equipos de UCI?
* **Documento Esperado:** `[BG-10]` Confiabilidad operacional y cálculo de MTBF en soporte vital (IEEE TBME 2021).
* **Fuente:** IEEE Transactions on Biomedical Engineering.
* **Respuesta Técnica / Criterio:**  
  Indica que el equipo ha ingresado a la etapa de desgaste acelerado de la curva de la bañera, requiriendo un reacondicionamiento mayor inminente de sus componentes electromecánicos críticos.

---

### Pregunta 11
* **Pregunta:** ¿Cuál es la exactitud requerida para el volumen corriente en ventiladores según IEC 60601-2-12?
* **Documento Esperado:** `[BG-11]` IEC 60601-2-12:2018 (Ventiladores pulmonares de cuidados intensivos).
* **Fuente:** Comisión Electrotécnica Internacional (IEC).
* **Respuesta Técnica / Criterio:**  
  La entrega del volumen corriente debe mantenerse dentro de una tolerancia de **±10 % o ±20 mL** (el que sea mayor).

---

### Pregunta 12
* **Pregunta:** ¿Cuánto voltaje de descarga de desfibrilador debe soportar el aislamiento de ECG según IEC 60601-2-49?
* **Documento Esperado:** `[BG-12]` IEC 60601-2-49:2018 (Monitores de paciente multifunción).
* **Fuente:** Comisión Electrotécnica Internacional (IEC).
* **Respuesta Técnica / Criterio:**  
  Debe mantener aislamiento galvánico de alta rigidez dieléctrica capaz de soportar pulsos de desfibrilación de **hasta 5000 voltios** sin daño en los módulos electrónicos ni retorno de energía residual al paciente.

---

### Pregunta 13
* **Pregunta:** ¿Cuál es la exactitud de flujo requerida para bombas de infusión volumétricas según IEC 60601-2-24?
* **Documento Esperado:** `[BG-13]` IEC 60601-2-24:2012 (Bombas y controladores de infusión).
* **Fuente:** Comisión Electrotécnica Internacional (IEC).
* **Respuesta Técnica / Criterio:**  
  Debe mantener una exactitud de flujo dentro de **±5 %** para bombas volumétricas en un lapso de 24 horas, y dentro de **±2 %** para bombas a jeringa microinfusoras.

---

### Pregunta 14
* **Pregunta:** ¿Por qué corrientes de fuga de 10 a 50 microamperios pueden causar fibrilación ventricular por microchoque?
* **Documento Esperado:** `[BG-14]` Microchoque eléctrico en pacientes críticos con accesos vasculares (JCMC 2020).
* **Fuente:** Journal of Clinical Monitoring and Computing.
* **Respuesta Técnica / Criterio:**  
  Porque los accesos vasculares centrales y electrodos intracardíacos proporcionan una vía de conducción directa al miocardio, eliminando la resistencia aislante de la piel intacta.

---

### Pregunta 15
* **Pregunta:** ¿Qué porcentaje de alarmas en UCI son falsas o clínicamente irrelevantes según ECRI?
* **Documento Esperado:** `[BG-15]` Top 10 Health Technology Hazards: Gestión de alarmas en UCI.
* **Fuente:** Emergency Care Research Institute (ECRI).
* **Respuesta Técnica / Criterio:**  
  Entre el **70 % y el 90 %** de las alarmas sonoras emitidas en UCI son falsas o clínicamente irrelevantes, provocando el fenómeno de fatiga sensorial de alarmas.

---

## 2. Preguntas sobre la Base Específica (Manuales Técnicos de Equipos y Fallas)

### Pregunta 16
* **Pregunta:** ¿Cómo se realiza la calibración del sensor de flujo proximal en el ventilador Hamilton C3?
* **Documento Esperado:** `[BE-01]` Manual de servicio técnico Hamilton Medical C3 (Doc ID: 624128).
* **Fuente:** Hamilton Medical AG.
* **Respuesta Técnica / Criterio:**  
  Se conecta el sensor proximal a la pieza en Y del circuito respiratorio, se ocluye el extremo distal según la guía en pantalla y se verifica la respuesta a tres puntos de flujo (la desviación no debe exceder del 8 %).

---

### Pregunta 17
* **Pregunta:** ¿Cuál es la vida útil promedio de la celda galvánica de oxígeno en el Hamilton C3?
* **Documento Esperado:** `[BE-02]` Especificaciones técnicas y mantenimiento de celda de O2 Hamilton C3 (Doc ID: 624130).
* **Fuente:** Hamilton Medical AG.
* **Respuesta Técnica / Criterio:**  
  Tiene una vida útil nominal aproximada de **5000 horas de uso** o un año calendario, tras lo cual el voltaje a 100 % de O2 decae por debajo de 40 mV.

---

### Pregunta 18
* **Pregunta:** ¿Qué tecnología de hilo caliente utiliza el sensor espiratorio SpiroLife de Dräger Evita V800?
* **Documento Esperado:** `[BE-03]` Documentación técnica Dräger Evita V800 (Order No. 9055800).
* **Fuente:** Drägerwerk AG & Co. KGaA.
* **Respuesta Técnica / Criterio:**  
  Utiliza anemometría de hilo caliente de **platino** calentado a temperatura constante, logrando medir flujo bidireccional con un tiempo de respuesta menor a 5 milisegundos.

---

### Pregunta 19
* **Pregunta:** ¿Cómo se calibra el transductor de presión de PNI en el monitor Mindray BeneVision N17?
* **Documento Esperado:** `[BE-05]` Manual de servicio Mindray BeneVision N17 (P/N: 046-008920-00).
* **Fuente:** Shenzhen Mindray Bio-Medical Electronics Co., Ltd.
* **Respuesta Técnica / Criterio:**  
  Se ingresa al menú de servicio técnico, se conecta una cámara rígida de 500 mL, perilla y manómetro patrón en T, y se verifica linealidad a 50, 150 y 250 mmHg (error máximo ±2 mmHg).

---

### Pregunta 20
* **Pregunta:** ¿En cuántos niveles de presión se puede configurar la alarma de oclusión de la bomba Infusomat Space?
* **Documento Esperado:** `[BE-08]` Manual de servicio B. Braun Infusomat Space (Ref: 8713050).
* **Fuente:** B. Braun Melsungen AG.
* **Respuesta Técnica / Criterio:**  
  Se puede configurar en **9 niveles de presión**, desde el nivel 1 (aprox. 0,3 bar / 4,3 psi) hasta el nivel 9 (aprox. 1,2 bar / 17,4 psi).
