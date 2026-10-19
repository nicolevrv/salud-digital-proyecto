# Módulo de Contabilidad y Costos Hospitalarios UCI (`MongoDB` + `PostgreSQL`) · R09

Este documento describe el diseño, colecciones documentales, esquemas JSON, índices y justificación arquitectónica del módulo contable de la plataforma **BIOT UAO — Salud Digital y Metrología Predictiva en UCI**, diseñado para el rol **`contable`** (`contable@hospital.com`) bajo el cumplimiento del requisito **R09** y el flujo reactivo **R19**.

---

## 1. Justificación Arquitectónica: ¿Por qué separar Contabilidad en `MongoDB`?

En una Unidad de Cuidados Intensivos (UCI) de alta complejidad (Nivel III), la información se divide en dos dominios con requisitos transaccionales y estructurales radicalmente distintos:

1. **Dominio Clínico, Metrológico e Interoperable (`PostgreSQL` + `HAPI FHIR` + `Orthanc PACS`)**:
   - Requiere integridad referencial estricta (ACID) entre `pacientes`, `camas_uci`, `encuentros`, `equipos_uci` (dotación fija irrepetible por cama: `1 Ventilador + 1 Monitor + 2 Bombas Volumétricas + 1 Bomba de Jeringa`), `observaciones_clinicas` (LOINC) y `reportes_ia`.
   - Está sujeto a la **Resolución 1995 de 1999** (reserva de historia clínica) y a la **Ley 2015 de 2020** (interoperabilidad clínica HL7 FHIR).
2. **Dominio Financiero, Tarifario y de Facturación Hospitalaria (`MongoDB` — Base Documental)**:
   - Cada evento facturable en UCI (liquidación de estancia diaria en cama crítica, arriendo operativo del kit de 5 equipos de soporte vital, cargo automático por aprobación de reporte predictivo de IA según **R19**, o interconsulta con ingeniería clínica/especialista) posee una **estructura polimórfica de ítems, desgloses tarifarios SOAT/CUPS, centros de costo e impuestos** que evoluciona sin alterar el esquema clínico.
   - Separar la contabilidad en `MongoDB` (`uci_contabilidad`) garantiza el **principio de mínimo privilegio (Ley 1581 de 2012)**: el perfil **`contable`** consulta y concilia documentos financieros (`facturas_uci`, `tarifario_biomedico_uci`, `costos_operativos_equipos`) sin tener acceso a los diagnósticos clínicos ni a las observaciones fisiológicas del paciente (`403 Forbidden` en `/pacientes/{id}/historia`).
   - Adicionalmente, el middleware implementa sincronización híbrida (`pymongo` hacia el contenedor `mongo:27017` y respaldo espejo en `facturas_uci` de Neon PostgreSQL) para garantizar alta disponibilidad tanto en despliegues `docker compose` como en la nube (`Render`).

---

## 2. Colecciones Documentales en `MongoDB` (`uci_contabilidad`)

### 2.1. Colección `facturas_uci`
Almacena todas las facturas, cargos por estancia UCI, costos de soporte vital biomédico y cargos automáticos generados cuando un médico o especialista aprueba un reporte del modelo predictivo de IA (**R10 / R19**).

#### Esquema del Documento (`JSON Schema`)
```json
{
  "_id": "ObjectId('67134f9a8c12e40015a9b101')",
  "id": 1001,
  "numero_factura": "FAC-UCI-2026-0001",
  "paciente_id": 1,
  "cama_uci": "Cama 1 - UCI Adultos",
  "reporte_id": 12,
  "remision_id": null,
  "concepto": "Diagnóstico Predictivo IA Aprobado (3 Targets Biomédicos) + Kit Soporte Vital UCI",
  "tipo_cargo": "REPORTE_IA_APROBADO",
  "monto": 485000.0,
  "moneda": "COP",
  "estado": "pendiente",
  "desglose_items": [
    {
      "codigo": "CUPS-890701",
      "descripcion": "Evaluación Predictiva Multi-Objetivo IA (Target_Falla, Target_Mtto_Predictivo, Target_Critico)",
      "valor_unitario": 185000.0,
      "cantidad": 1,
      "subtotal": 185000.0
    },
    {
      "codigo": "BIO-KIT-UCI5",
      "descripcion": "Operación diaria Kit Básico Cama UCI (1 VENT + 1 MON + 2 BVOL + 1 BJER)",
      "valor_unitario": 300000.0,
      "cantidad": 1,
      "subtotal": 300000.0
    }
  ],
  "creado_por": "sistema_reactivo_r19",
  "fecha_emision": "2026-10-19T02:00:00Z",
  "fecha_actualizacion": "2026-10-19T02:00:00Z"
}
```

#### Índices Definidos en `facturas_uci`
- `idx_factura_id_unique`: `{ "id": 1 }` (`unique: true`) — Búsqueda inmediata por identificador de contrato API (`/contabilidad/facturas/{id}`).
- `idx_reporte_id`: `{ "reporte_id": 1 }` — Trazabilidad inmediata del flujo reactivo **R19** (verificar qué factura fue generada al aprobar el reporte `reporte_id`).
- `idx_paciente_estado`: `{ "paciente_id": 1, "estado": 1, "fecha_emision": -1 }` — Consulta rápida de cartera pendiente o pagada por paciente/cama UCI.

---

### 2.2. Colección `tarifario_biomedico_uci`
Define las tarifas institucionales en Pesos Colombianos (`COP`) para la hospitalización en UCI y el uso de tecnología biomédica por cama ocupada.

#### Esquema del Documento
```json
{
  "codigo_tarifa": "TAR-UCI-KIT5",
  "categoria": "SOPORTE_VITAL_UCI",
  "nombre": "Dotación Biomédica Básica por Cama UCI ( Día )",
  "componentes": [
    {"tipo": "Ventilador Mecánico", "cantidad": 1, "tarifa_dia_cop": 190000},
    {"tipo": "Monitor Multiparámetro", "cantidad": 1, "tarifa_dia_cop": 85000},
    {"tipo": "Bomba de Infusión Volumétrica", "cantidad": 2, "tarifa_dia_cop": 90000},
    {"tipo": "Bomba de Infusión a Jeringa", "cantidad": 1, "tarifa_dia_cop": 55000}
  ],
  "tarifa_estancia_cama_uci_dia_cop": 1450000,
  "tarifa_evaluacion_ia_3targets_cop": 185000,
  "tarifa_interconsulta_especialista_cop": 220000,
  "vigencia": "2026"
}
```

#### Índices Definidos en `tarifario_biomedico_uci`
- `idx_codigo_tarifa`: `{ "codigo_tarifa": 1 }` (`unique: true`).

---

### 2.3. Colección `costos_operativos_equipos`
Registra el costo de mantenimiento preventivo, calibración metrológica ISO/IEC 17025 (ONAC) y mantenimiento correctivo por unidad de la flota de 232 equipos.

#### Esquema del Documento
```json
{
  "equipo_id": "VENT-01",
  "tipo_equipo": "Ventilador Mecánico",
  "costo_calibracion_anual_cop": 680000,
  "costo_mtto_preventivo_cop": 420000,
  "costo_hora_paro_uci_cop": 145000,
  "cluster_r07": 0,
  "ultima_actualizacion": "2026-10-19T02:00:00Z"
}
```

#### Índices Definidos en `costos_operativos_equipos`
- `idx_equipo_id`: `{ "equipo_id": 1 }` (`unique: true`).

---

## 3. Operaciones Expuestas al Rol `contable`

| Operación | Método y Ruta | Roles Autorizados | Descripción |
| :--- | :--- | :--- | :--- |
| **Crear Factura / Cargo UCI** | `POST /contabilidad/facturas` | `contable`, `admin` | Registra un nuevo documento de cobro en `MongoDB` (`facturas_uci`) y su espejo transaccional, devolviendo `{id, estado, monto, paciente_id, reporte_id}`. |
| **Listar Facturas y Resumen Financiero** | `GET /contabilidad/facturas` | `contable`, `admin` | Devuelve la lista de facturas (`pendiente`, `pagada`, `anulada`), KPIs de facturación UCI y liquidación por cama ocupada. |
| **Actualizar Estado de Factura** | `PUT /contabilidad/facturas/{id}` | `contable`, `admin` | Actualiza el estado del documento (`pendiente` → `pagada` / `conciliada`) y registra auditoría inmutable. |
| **Flujo Automático por Aprobación IA (R19)** | Disparado por `PUT /ia/reportes/{id}` (`estado="aprobado"`) | Automático (Sistema Reactivo) | Crea automáticamente una factura con `reporte_id` vinculado al reporte de IA aprobado y emite evento SSE al perfil `contable`. |
