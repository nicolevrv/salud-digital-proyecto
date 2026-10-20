# Documentación de Datasets de Imágenes (PACS) y Datos Tabulares con Agrupamiento por Clusters (R06 & R07)

Este documento declara formalmente las fuentes de datos reales de referencia (imágenes médicas/biomédicas y registros tabulares de mantenimiento e ingeniería clínica), su almacenamiento en el servidor PACS / repositorio multimedia hospitalario, y la metodología de agrupamiento por clusters (**K-Means, $k = 4$**) empleada para estructurar y etiquetar los registros sintéticos (`origen = 'sintetico'`) en **Neon PostgreSQL**.

---

## Imagen

### 1. Declaración General del Dataset de Imágenes (R06)

- **Nombre del Dataset**:
  1. **Roboflow Universe — Hospital & Medical Equipment Detection Dataset** (Clases de soporte vital UCI: *Ventilator*, *Patient Monitor*, *Infusion Pump*, *Syringe Pump*, *Defibrillator*, *ECG Machine*, *Ultrasound*).
  2. **Wikimedia Commons — Medical & Intensive Care Unit Equipment Repository** (Fotografías técnicas reales de activos biomédicos de soporte vital y diagnóstico en UCI).
  3. **U.S. National Library of Medicine (NLM) Open-i & FDA AccessGUDID Biomedical Device Image Database** (Referencia visual y regulatoria de dispositivos médicos registrados).
- **Fuente (URL)**:
  - Repositorio Principal 1 (Roboflow Hospital Equipment Detection): https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection
  - Repositorio Principal 2 (Roboflow Medical Equipment Detection): https://universe.roboflow.com/bsbi/medical-equipment-detection
  - Repositorio Principal 3 (Wikimedia Commons Medical Equipment): https://commons.wikimedia.org/wiki/Category:Medical_equipment
  - Repositorio Complementario (NLM Open-i / FDA AccessGUDID): https://openi.nlm.nih.gov/ y https://accessgudid.nlm.nih.gov/
- **Licencia**:
  - **Creative Commons Attribution 4.0 International (`CC BY 4.0`)** (Roboflow Universe / NLM Open-i).
  - **Creative Commons Attribution-ShareAlike 4.0 (`CC BY-SA 4.0`) / Dominio Público** (Wikimedia Commons / FDA AccessGUDID).
- **Número de imágenes cargadas**:
  - **12 fotografías clínicas oficiales de referencia** (correspondientes a los **12 modelos oficiales** de las **10 familias tecnológicas de UCI** registrados en la base de datos), vinculadas en el almacenamiento PACS a las **232 unidades biomédicas** del inventario hospitalario y a los **125 equipos en uso activo** conectados a los **25 pacientes hospitalizados en UCI** (5 imágenes de equipos irrepetibles por cada paciente en cama ocupada: `1 VENT + 1 MON + 2 BVOL + 1 BJER`).
- **Por qué este dataset responde al problema clínico y biomédico del equipo**:
  - El problema abordado por nuestro sistema (**BIOT UAO — Salud Digital y Mantenimiento Predictivo en UCI**) se centra en la **gestión metrológica, seguridad eléctrica (norma IEC 62353), trazabilidad clínica y predicción de fallas de equipos biomédicos de soporte vital conectados a pacientes críticos en la Unidad de Cuidados Intensivos (UCI Adultos)**.
  - En Ingeniería Clínica hospitalaria y tecnovigilancia (Decreto 4725 de 2005 / Resolución 3100 de 2019 en Colombia), la ficha clínica del paciente crítico en UCI y la hoja de vida metrológica exigen la **identificación visual inequívoca en PACS del activo biomédico conectado a la cama del paciente** (ventilador mecánico, monitor multiparámetro, bombas de infusión peristálticas y a jeringa, máquina de reemplazo renal continuo CRRT, desfibrilador, etc.).
  - Contar con las fotografías reales provenientes de repositorios públicos abiertos de equipamiento hospitalario permite:
    1. **Verificación visual desde la Ficha del Paciente**: El personal médico y de ingeniería biomédica visualiza y descarga desde la ficha de cada paciente (`GET /pacientes/{id}/imagenes` y Visor PACS integrado) las imágenes exactas de los 5 equipos de soporte vital asignados a su cama (`UCI-BED-01` a `UCI-BED-25`).
    2. **Inspección pre-operativa y tecnovigilancia**: Permite contrastar el modelo físico real (p. ej., *Hamilton Medical C3* vs. *Dräger Evita V800*, o *Mindray BeneVision N17* vs. *Philips IntelliVue MX800*) frente a su certificado de calibración ONAC e historial de eventos adversos.

### 2. Catálogo Detallado de las 12 Imágenes Reales Cargadas en el PACS y su URL de Origen

Cada imagen está almacenada en el servicio PACS / almacenamiento estático del servidor (`/static/equipos/`), registrada en la tabla `equipos_documentos` de **Neon PostgreSQL** y disponible para visualización y descarga directa desde la **Ficha del Paciente**:

| Código Base (`equipo_id`) | Familia Tecnológica UCI | Marca y Modelo Oficial | Archivo en PACS / Almacenamiento | Dataset Público de Origen | URL Fuente del Dataset | Licencia |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`VENT-01`** | Ventilador Mecánico | Hamilton Medical C3 | `/static/equipos/vent_hamilton_c3.jpeg` | Roboflow Hospital Equipment Detection / Wikimedia | https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection | `CC BY 4.0` |
| **`VENT-02`** | Ventilador Mecánico | Dräger Evita V800 | `/static/equipos/vent_drager_evita_v800.png` | Wikimedia Commons Medical Equipment (Dräger) | https://commons.wikimedia.org/wiki/Category:Dr%C3%A4gerwerk | `CC BY-SA 4.0` |
| **`MON-01`** | Monitor Multiparámetro | Mindray BeneVision N17 | `/static/equipos/mon_mindray_n17.png` | Roboflow Medical Equipment Detection (*Monitor*) | https://universe.roboflow.com/bsbi/medical-equipment-detection | `CC BY 4.0` |
| **`MON-02`** | Monitor Multiparámetro | Philips IntelliVue MX800 | `/static/equipos/mon_philips_mx800.png` | Wikimedia Commons Medical Monitors | https://commons.wikimedia.org/wiki/Category:Medical_monitors | `CC BY-SA 4.0` |
| **`BVOL-01`** | Bomba de Infusión Volumétrica | BBraun Space P | `/static/equipos/bvol_bbraun_space.jpg` | Wikimedia Commons Infusion Pumps | https://commons.wikimedia.org/wiki/Category:Infusion_pumps | `CC BY-SA 4.0` |
| **`BJER-01`** | Bomba de Infusión a Jeringa | Fresenius Kabi Injectomat Agilia | `/static/equipos/bjer_fresenius_agilia.png` | Wikimedia Commons Syringe Infusion Pumps | https://commons.wikimedia.org/wiki/Category:Syringe_drivers | `CC BY-SA 4.0` |
| **`DEF-01`** | Desfibrilador Bifásico | Mindray / Zoll BeneHeart D3 | `/static/equipos/def_mindray_beneheart_d3.jpeg` | Roboflow Hospital Equipment Detection (*Defibrillator*) | https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection | `CC BY 4.0` |
| **`CRRT-01`** | Máquina CRRT | Baxter PrisMax | `/static/equipos/crrt_baxter_prismax.jpeg` | Wikimedia Commons Hemofiltration & AccessGUDID | https://commons.wikimedia.org/wiki/Category:Hemofiltration | `CC BY-SA 4.0` |
| **`ECG-01`** | Electrocardiógrafo 12 Deriv. | GE Healthcare MAC 2000 | `/static/equipos/ecg_ge_mac2000.jpeg` | Roboflow Medical Equipment Detection (*ECG*) | https://universe.roboflow.com/bsbi/medical-equipment-detection | `CC BY 4.0` |
| **`POCT-01`** | Analizador de Gases (POCT) | Radiometer ABL90 FLEX | `/static/equipos/poct_radiometer_abl90.jpg` | Wikimedia Commons Blood Gas Analyzers | https://commons.wikimedia.org/wiki/Category:Blood_gas_analyzers | `CC BY-SA 4.0` |
| **`ECO-01`** | Ecógrafo Portátil Doppler | Sonosite Edge II | `/static/equipos/eco_sonosite_edge.jpg` | Wikimedia Commons Medical Ultrasound Equipment | https://commons.wikimedia.org/wiki/Category:Medical_ultrasound_equipment | `CC BY-SA 4.0` |
| **`RX-01`** | Equipo de Rayos X Portátil | Carestream Motion Mobile | `/static/equipos/rx_mobilett_elara.jpg` | Wikimedia Commons Mobile Medical X-Ray Equipment | https://commons.wikimedia.org/wiki/Category:Medical_X-ray_equipment | `CC BY-SA 4.0` |

### 3. Verificación Automática por API (`Auto (API)`) y Visor PACS en Ficha del Paciente

- **Lista de imágenes de un paciente**:
  - `GET /pacientes/{paciente_id}/imagenes` (también disponible en `GET /api/pacientes/{paciente_id}/imagenes` y embebido en `GET /pacientes` bajo la llave `imagenes`).
  - Devuelve las **5 imágenes PACS** correspondientes a la dotación básica irrepetible conectada al paciente (`1 VENT`, `1 MON`, `2 BVOL`, `1 BJER`), incluyendo `imagen_id`, `equipo_id`, `titulo`, `dataset_nombre`, `fuente_url`, `licencia`, `url` y `download_url`.
- **Descarga directa de cada imagen**:
  - `GET /pacientes/{paciente_id}/imagenes/{imagen_id}/descargar` y `GET /pacs/imagenes/{imagen_id}/descargar` (además de la ruta estática `/static/equipos/{archivo}`).
  - Descarga el archivo binario real (`.jpg` / `.png`) con encabezados HTTP `200 OK` y `Content-Disposition: attachment`.

---

## Tabular

### 1. Declaración de la Base Teórica Tabular Real (R07)

- **Nombre del Dataset (Base Teórica de Referencia)**:
  1. **Kaggle — Medical Device Failure Dataset (Anonymized Clinical Engineering Maintenance Logs)** (Registros históricos de fallas, edad operativa, intervalos de mantenimiento preventivo y confiabilidad MTBF en equipamiento médico hospitalario).
  2. **UCI Machine Learning Repository — AI4I 2020 Predictive Maintenance Dataset** (Estructura de modos de falla por desgaste acumulado, fatiga operativa y deriva de parámetros físicos para mantenimiento predictivo).
  3. **Estándar Internacional IEC 62353 / FDA MAUDE Biomedical Device Reliability Benchmarks** (Umbrales normativos reales de corriente de fuga en $\mu\text{A}$, resistencia de tierra de protección en $\Omega$ y tolerancia de desviación metrológica en $\%$).
- **Fuente (URL)**:
  - Fuente Principal 1 (Kaggle Medical Device Failure Dataset): https://www.kaggle.com/datasets/prashant111/medical-device-failure-dataset
  - Fuente Principal 2 (UCI AI4I 2020 Predictive Maintenance Dataset): https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset
  - Fuente Regulatoria Complementaria (FDA MAUDE Adverse Event & Device Failure Reports): https://www.fda.gov/medical-devices/mandatory-reporting-requirements-manufacturers-importers-and-device-user-facilities/manufacturer-and-user-facility-device-experience-database-maude
- **Licencia**:
  - **CC BY 4.0** (UCI Machine Learning Repository — AI4I 2020) y **Open Data / CC0 Public Domain** (Kaggle Medical Device Failure Dataset / FDA MAUDE).
- **Número de registros cargados y marcados como sintéticos (`origen = 'sintetico'`)**:
  - **7,631 registros de eventos biomédicos y pruebas metrológicas** en la tabla `dataset_eventos` de **Neon PostgreSQL**, correspondientes a las **232 unidades de equipos biomédicos** en `hoja_vida_equipos` y a los **25 pacientes hospitalizados en UCI** en la tabla `pacientes`.
  - **100% de los pacientes (`25/25`) y 100% de los registros de eventos (`7,631/7,631`) están marcados explícitamente con `origen = 'sintetico'`** y ligados a su respectivo grupo (`cluster_id` $\in \{0, 1, 2, 3\}$ y `cluster_nombre`).
- **Por qué este dataset responde al problema del equipo**:
  - La predicción de fallas en equipos de soporte vital en UCI requiere modelar simultáneamente **variables de confiabilidad histórica** (edad del activo en meses, horas de uso acumuladas, tiempo transcurrido desde el último mantenimiento preventivo, número de fallas acumuladas y tiempo medio entre fallas $\text{MTBF}$) y **variables metrológicas y de seguridad eléctrica bajo la norma IEC 62353** (corriente de fuga en $\mu\text{A}$, resistencia de puesta a tierra en $\Omega$ y porcentaje de desviación de calibración).
  - A partir de las distribuciones paramétricas reales reportadas en el *Medical Device Failure Dataset* y el *AI4I 2020 Predictive Maintenance Dataset*, se estructuraron los grupos operativos mediante **K-Means** y se generaron los registros sintéticos (`origen = 'sintetico'`) conservando las correlaciones físicas reales entre envejecimiento mecánico, deriva metrológica y probabilidad de falla en UCI.

---

### 2. Metodología de Agrupamiento por Clusters (K-Means) y Generación Sintética (`origen = 'sintetico'`)

Para representar fielmente los regímenes de operación y degradación del parque tecnológico de UCI, se aplicó el algoritmo **K-Means (inicialización determinística `k-means++`, `seed = 42`)** sobre las **8 variables numéricas continuas estandarizadas ($Z\text{-score}$)**:

1. `Edad_Equipo_Meses` (Antigüedad del equipo biomédico en meses)
2. `Meses_Desde_Ultimo_Mtto` (Tiempo transcurrido desde la última intervención preventiva)
3. `Horas_Uso_Acumuladas` (Carga operativa acumulada en horas de servicio clínico)
4. `Fallas_Acumuladas` (Historial acumulado de fallas correctivas previas)
5. `MTBF_Promedio_Dias` (*Mean Time Between Failures* en días)
6. `Corriente_Fuga_uA` (Corriente de fuga de envolvente/paciente en $\mu\text{A}$ según IEC 62353)
7. `Resistencia_Tierra_Ohms` (Resistencia del conductor de tierra de protección en $\Omega$ según IEC 62353)
8. `Desviacion_Calibracion_Pct` (Error relativo porcentual medido frente al patrón metrológico ONAC)

---

### 3. Justificación Matemática del Número de Grupos ($k = 4$): Criterio del Codo (WCSS) y Coeficiente de Silueta

Se evaluó el particionamiento para $k \in \{2, 3, 4, 5, 6\}$ sobre la matriz estandarizada de los **7,631 registros** en **Neon PostgreSQL**, obteniendo las siguientes métricas exactas:

| Número de Clusters ($k$) | Inercia Intra-Cluster (WCSS) | Reducción Marginal de WCSS ($\Delta\text{WCSS}$) | Coeficiente de Silueta (*Silhouette Score*) | Evaluación del Criterio |
| :---: | :---: | :---: | :---: | :--- |
| **$k = 2$** | $47,690.07$ | — | $0.2232$ | Sub-segmentación: mezcla activos jóvenes y maduros con la zona de transición. |
| **$k = 3$** | $41,468.15$ | $-6,221.92$ | $0.2116$ | No separa los equipos maduros de alto MTBF de aquellos con fatiga acumulada. |
| **$k = 4$ (Óptimo)** | **$36,276.72$** | **$-5,191.43$** | **$0.2364$ (Máximo)** | **Punto de inflexión del Codo + Máximo global del Coeficiente de Silueta.** |
| **$k = 5$** | $32,773.99$ | $-3,502.73$ | $0.2078$ | Caída en el coeficiente de silueta; fragmenta artificialmente el grupo óptimo. |
| **$k = 6$** | $29,902.45$ | $-2,871.54$ | $0.2095$ | Sobre-segmentación sin ganancia en separabilidad clínica ni metrológica. |

**Conclusión de la justificación**:
- Se selecciona **$k = 4$** porque:
  1. **Maximiza el Coeficiente de Silueta ($0.2364$)** entre todas las configuraciones evaluadas ($k = 2..6$), garantizando la mayor cohesión interna y separación entre grupos.
  2. **Corresponde al punto de codo en la curva WCSS ($36,276.72$)**: a partir de $k > 4$, la reducción marginal de inercia cae por debajo de $3,503$ unidades y el índice de silueta disminuye a $0.2078$.
  3. **Sentido físico y clínico en UCI**: Los 4 grupos reproducen exactamente los cuatro estados reales del ciclo de vida biomédico en una UCI (activos jóvenes óptimos, activos en degradación metrológica aguda, activos envejecidos en **zona de confusión**, y activos maduros de alta confiabilidad).

---

### 4. Interpretación Clínica y Biomédica de los 4 Clusters e Identificación de la Zona de Confusión del Modelo

A continuación se presentan los centroides reales almacenados en la tabla `clusters_biomedicos_resumen` de **Neon PostgreSQL** y su interpretación frente al problema estudiado:

| Cluster ID | Nombre del Cluster | Registros (`origen = sintetico`) | % del Total | Edad Media (meses) | Meses sin Mtto | Horas de Uso | Fallas Acum. | MTBF Medio (días) | Corriente Fuga ($\mu\text{A}$) | Desviación Calib. ($\%$) | Tasa de Falla ($\%$) |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`0`** | **Operación Óptima en Activos Jóvenes (Bajo Riesgo)** | $3,095$ | $40.56\%$ | $35.19$ | $3.34$ | $18,077.8$ | $2.15$ | $600.55$ | $75.79$ | $1.26\%$ | $4.0\%$ |
| **`1`** | **Degradación Metrológica e Incumplimiento IEC 62353 (Riesgo Crítico)** | $874$ | $11.45\%$ | $71.93$ | $7.25$ | $35,489.9$ | $7.36$ | $537.97$ | $137.86$ | $3.65\%$ | $58.0\%$ |
| **`2`** | **Zona de Confusión (Fatiga Acumulada con IEC 62353 Conforme)** | $2,709$ | $35.50\%$ | $87.66$ | $3.16$ | $48,563.7$ | $8.58$ | $461.59$ | $91.95$ | $1.51\%$ | $10.0\%$ |
| **`3`** | **Activos Maduros de Alta Confiabilidad y Alto MTBF** | $953$ | $12.49\%$ | $79.96$ | $3.63$ | $45,252.3$ | $1.01$ | $2,056.06$ | $89.10$ | $1.48\%$ | $4.0\%$ |

#### Interpretación detallada de cada grupo y análisis de las Zonas de Confusión del Modelo Predictivo:

1. **Cluster 0 — Operación Óptima en Activos Jóvenes (`cluster_id = 0`, $n = 3,095$)**:
   - **Perfil**: Equipos biomédicos con baja antigüedad ($\approx 35.2$ meses), mantenimiento preventivo al día ($3.34$ meses), corriente de fuga muy por debajo del límite normativo ($75.79\ \mu\text{A}$) y error de calibración mínimo ($1.26\%$). El $99.3\%$ aprueba la norma **IEC 62353 (`Conforme`)**.
   - **Comportamiento del modelo**: El clasificador identifica correctamente estos activos como **Operación Normal / Sano (`Target = 0`)** con una tasa de falsos positivos mínima.

2. **Cluster 1 — Degradación Metrológica e Incumplimiento IEC 62353 (`cluster_id = 1`, $n = 874$) — *Sub-zona de Confusión por Retraso de Mantenimiento***:
   - **Perfil**: Equipos que superan el umbral semestral sin mantenimiento preventivo ($7.25$ meses en promedio), con alta deriva metrológica ($3.65\%$), incremento en corriente de fuga ($137.86\ \mu\text{A}$) y resistencia de tierra ($0.14\ \Omega$), concentrando una **tasa real de falla del $58.0\%$**.
   - **Por qué genera confusión parcial en el modelo**: Aunque $289$ registros ya son `No conforme` y $119$ están en `Alerta`, existen **$466$ registros dentro de este cluster que todavía reportan `Estado_IEC62353 = Conforme`** porque su corriente de fuga aún no cruza el techo extremo, pero su deriva de calibración ($> 3.5\%$) y atraso de mantenimiento ($> 7$ meses) provocan fallas funcionales inminentes.

3. **Cluster 2 — Zona de Confusión Principal del Modelo: "Fatiga Mecánica Acumulada con Prueba Eléctrica IEC 62353 Conforme" (`cluster_id = 2`, $n = 2,709$)**:
   - **Perfil**: Es el grupo más desafiante para la ingeniería clínica en UCI. Son equipos con **alta antigüedad ($87.66$ meses $\approx 7.3$ años)**, **uso intensivo acumulado ($48,563.7$ horas)**, **alto historial de fallas previas ($8.58$ fallas)** y un **MTBF reducido ($461.59$ días)**, pero que **acaban de recibir mantenimiento preventivo hace poco ($3.16$ meses)** y presentan **parámetros eléctricos instantáneos normales** (corriente de fuga de $91.95\ \mu\text{A}$, desviación de $1.51\%$ y **$98.1\%$ con dictamen `Estado_IEC62353 = Conforme`**).
   - **Por qué el modelo se confunde en este grupo**:
     - Un modelo basado únicamente en el último mantenimiento o en la prueba eléctrica puntual clasifica erróneamente estos equipos como **Sanos (`0`)** porque `Meses_Desde_Ultimo_Mtto` es bajo ($3.16$) y `Estado_IEC62353` es `Conforme` (falso negativo).
     - Por el contrario, un modelo basado únicamente en antigüedad (`Edad_Equipo_Meses` $> 85$) y `Fallas_Acumuladas` ($\ge 8$) tiende a disparar alarma de **Falla (`1`)** aunque el activo esté operando estable tras su mantenimiento reciente (falso positivo, dado que el $90\%$ continúa operando y el $10\%$ presenta falla súbita por fatiga de componentes internos como válvulas proporcionales en ventiladores o sensores de presión en bombas de infusión).

4. **Cluster 3 — Activos Maduros de Alta Confiabilidad y Alto MTBF (`cluster_id = 3`, $n = 953$)**:
   - **Perfil**: Equipos con antigüedad elevada ($79.96$ meses y $45,252.3$ horas de servicio), pero con un diseño robusto y excelente historial metrológico: apenas $1.01$ fallas en toda su vida útil y un **MTBF extraordinario de $2,056.06$ días**.
   - **Importancia para separar la confusión**: Este cluster demuestra por qué la edad del equipo por sí sola no determina la falla; a diferencia del Cluster 2, el alto $\text{MTBF}$ y el bajo número de fallas previas permiten al modelo distinguir activos longevos confiables ($4\%$ de falla) de activos longevos fatigados (Cluster 2).

---

### 5. Trazabilidad en Base de Datos (Neon PostgreSQL) y API REST

- **Tabla `pacientes`**:
  - Columnas `origen = 'sintetico'`, `cluster_id` y `cluster_nombre` activas en los **25 pacientes hospitalizados en UCI**, asociando cada paciente al cluster operativo dominante de los 5 equipos de soporte vital conectados a su cama.
- **Tabla `dataset_eventos`**:
  - Columnas `origen = 'sintetico'`, `cluster_id` y `cluster_nombre` activas en los **7,631 registros**.
- **Tabla `hoja_vida_equipos`**:
  - Columnas `origen = 'sintetico'`, `cluster_id` y `cluster_nombre` activas en las **232 unidades del inventario UCI**.
- **Tabla `clusters_biomedicos_resumen` y Endpoint `/api/clusters-resumen`**:
  - Expone vía API los 4 clusters, sus centroides, el análisis de la **Zona de Confusión** y las métricas de validación (Codo WCSS y Coeficiente de Silueta).


### 4. Notebook de Evaluación y Agrupamiento (`evaluation of biomedical`)
El código completo de partición temporal, validación cruzada, entrenamiento de los modelos para los 3 objetivos, calibración isotónica y análisis del método del codo y silueta para $k=4$ clusters se encuentra disponible y enlazado en:
- [Notebook de Entrenamiento y Evaluación: `notebooks/mantenimiento_biomedico.ipynb`](../notebooks/mantenimiento_biomedico.ipynb)
- [Notebook de Evaluación Biomédica (`evaluation_of_biomedical.ipynb`)](../notebooks/evaluation_of_biomedical.ipynb)
- [Dataset Nivel 3: `dataset_predictivo_metrologia_nivel3.xls`](../dataset_predictivo_metrologia_nivel3.xls)
- [Resumen de Clusters Exportado](../notebooks/resultados_notebook/resumen_clusters.csv)
