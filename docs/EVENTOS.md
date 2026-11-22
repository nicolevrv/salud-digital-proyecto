# Arquitectura Reactiva, Eventos en Tiempo Real (SSE) y Escalamiento de Alertas · R16–R21

Este documento describe el sistema de eventos en tiempo real (**Server-Sent Events — SSE**), la bandeja persistente de notificaciones, los **cuatro flujos reactivos automáticos (R19)**, la gestión de **alertas críticas con reconocimiento obligatorio (R20)** y el **webhook reactivo HL7 FHIR (R21)**.

---

## 1. Canal de Eventos en Tiempo Real (`GET /eventos/stream`) · R17

El backend expone un canal persistente **Server-Sent Events (`text/event-stream`)** en `/eventos/stream` (autenticado mediante token de sesión en cabecera `Authorization: Bearer <token>`, `X-Session-Token` o parámetro `?token=<token>`).

- **Infraestructura sin Buffering (R05)**: El proxy inverso `nginx` incluye `proxy_buffering off; proxy_cache off; chunked_transfer_encoding off;` en la ruta `/eventos/stream` y la API emite la cabecera `X-Accel-Buffering: no`.
- **Filtrado por Rol (RBAC en vivo)**: Cada conexión SSE queda suscrita únicamente a los eventos pertinentes al rol del usuario autenticado (`medico`, `especialista`, `contable`, `admin`), evitando fugas de información clínica hacia perfiles administrativos o contables.

### Esquema Estándar de Evento Emitido (`JSON`)
```json
{
  "id": 42,
  "tipo": "ALERTA_CRITICA_UCI",
  "mensaje": "Evento crítico detectado en Cama 1 - UCI Adultos (VENT-01: Target_Critico=1, Gravedad >= 8)",
  "fecha": "2026-10-19T02:10:00",
  "paciente_id": 1,
  "equipo_id": "VENT-01",
  "rol_destino": "medico",
  "referencia_id": 15,
  "severidad": "CRITICA"
}
```

---

## 2. Los 4 Flujos Reactivos Automáticos · R19

Cada acción de negocio dispara de forma atómica y sin intervención manual la cadena completa: **persistencia en base de datos + registro en tabla `notificaciones` + emisión inmediata por SSE + registro en `audit_logs`**.

```mermaid
flowchart TD
    subgraph Flujo1["1. Remisión Clínica/Biomédica (R08 → R19)"]
        M1["Médico crea remisión (POST /remisiones)"] --> DB1["Guarda remisión (estado: pendiente) + FHIR ServiceRequest"]
        DB1 --> N1["Crea notificación para rol: especialista"]
        N1 --> SSE1["Emite evento SSE REMISION_NUEVA al Especialista"]
    end

    subgraph Flujo2["2. Aceptación de Remisión (R08 → R19)"]
        E2["Especialista acepta remisión (PUT /remisiones/{id}/aceptar)"] --> DB2["Actualiza remisión (estado: aceptada)"]
        DB2 --> N2["Crea notificación para el Médico remitente"]
        N2 --> SSE2["Emite evento SSE REMISION_ACEPTADA al Médico"]
    end

    subgraph Flujo3["3. Aprobación de Reporte IA y Facturación Automática (R10 → R09/R19)"]
        D3["Médico/Especialista aprueba reporte IA (PUT /ia/reportes/{id})"] --> DB3["Reporte pasa a estado: aprobado (visible en Historia y PDF)"]
        DB3 --> F3["Crea factura automática en MongoDB/PostgreSQL con reporte_id"]
        F3 --> N3["Crea notificación para rol: contable"]
        N3 --> SSE3["Emite evento SSE FACTURA_GENERADA_IA al Contable"]
    end

    subgraph Flujo4["4. Bloqueo de Cuenta por Intentos Fallidos (R02 → R19)"]
        L4["3 intentos fallidos de login en /login"] --> DB4["Bloquea cuenta 15 min (locked_until) + login_logs (LOCKED)"]
        DB4 --> N4["Crea notificación de seguridad para rol: admin"]
        N4 --> SSE4["Emite evento SSE CUENTA_BLOQUEADA al Administrador"]
    end
```

### Detalle de los 4 Reactores

| # | Evento Disparador | Origen | Acción Reactiva Automática | Destinatario SSE y Bandeja |
| :-: | :--- | :--- | :--- | :--- |
| **1** | Creación de remisión (`POST /remisiones`) | `medico` | Registra la remisión en estado `pendiente`, sincroniza `ServiceRequest` en HAPI FHIR, incrementa `remisiones_recibidas` en `/pendientes` y notifica al especialista. | `especialista` |
| **2** | Aceptación de remisión (`PUT /remisiones/{id}/aceptar`) | `especialista` | Cambia estado a `aceptada`, adjunta dictamen técnico/clínico y notifica en vivo al médico que originó la remisión. | `medico` |
| **3** | Aprobación de reporte IA (`PUT /ia/reportes/{id}` con `estado="aprobado"`) | `medico` o `especialista` | Incorpora el diagnóstico de los 3 targets (`Target_Falla`, `Target_Mtto_Predictivo`, `Target_Critico`) a la historia clínica y **crea automáticamente una factura en `MongoDB` (`facturas_uci`) con `reporte_id`** asociado. | `contable` |
| **4** | Tres (3) intentos fallidos de inicio de sesión (`POST /login`) | Cualquier usuario/IP | Bloquea la cuenta por 15 minutos (`403 Forbidden`), registra el evento `LOCKED` en `login_logs` y alerta inmediatamente al administrador. | `admin` |

---

## 3. Alertas Críticas con Reconocimiento Obligatorio y Escalamiento · R20

### 3.1. Generación de la Alerta Crítica
Una alerta crítica (`estado = "activa"`) se dispara automáticamente en cualquiera de los siguientes escenarios:
1. **Modelo Predictivo Multi-Objetivo (Target 3)**: Cuando la inferencia del modelo arroja **`Target_Critico = 1`** (`probabilidad >= 0.38`, correspondiente a `Gravedad_Evento >= 8` en equipos de soporte vital UCI).
2. **Telemetría / Observación Clínica o Webhook FHIR Crítico**: Cuando ingresa una medición LOINC o parámetro IEC 62353 fuera de umbral seguro (por ejemplo, `SpO2 < 88%`, `Presión Pico > 40 cmH2O`, `Glucosa > 250 mg/dL` o ` < 55 mg/dL`, `Corriente de Fuga > 150 µA`).

### 3.2. Reconocimiento Obligatorio con Nota Clínica/Técnica
- Mientras la alerta permanezca en `estado = "activa"`, se contabiliza en `GET /pendientes` (`alertas_activas > 0`) y se muestra en un **banner rojo persistente** en la parte superior del dashboard.
- Una alerta crítica **no puede cerrarse como una notificación común**: requiere invocar `POST /alertas/{id}/reconocer` (o `PUT /alertas/{id}/reconocer`) por parte de un `medico` o `especialista` incluyendo obligatoriamente una **`nota` no vacía** con la acción correctiva tomada.
- Al reconocerse:
  1. Su estado pasa de `activa` a `reconocida`.
  2. Se almacena el usuario responsable, la marca de tiempo y la nota clínica/técnica.
  3. Se escribe un registro inmutable en `audit_logs` (`RECONOCER_ALERTA_CRITICA`).

### 3.3. Regla de Escalamiento por Tiempo de Respuesta
El sistema evalúa la antigüedad de toda alerta crítica en `estado = "activa"`:
- **Nivel 1 (`0 a 5 minutos`)**: Alerta activa asignada al `medico` de turno en UCI y al `especialista` de soporte vital en cama.
- **Nivel 2 (`5 a 15 minutos sin reconocimiento`)**: La alerta se marca con prioridad `ESCALADA_ESPECIALISTA`, re-emitiendo pulso SSE prioritario a todos los ingenieros clínicos (`ING_CORRECTIVO` / `especialista`).
- **Nivel 3 (`> 15 minutos sin reconocimiento`)**: La alerta escala automáticamente a la **Dirección Biomédica y Administración (`admin`)**, generando una notificación de incumplimiento de SLA crítico en UCI.

---

## 4. Webhook Reactivo con HAPI FHIR · R21

El endpoint `POST /fhir/webhook` (y su alias compatible `POST /fhir/Observation`) recibe recursos estándar **HL7 FHIR R4 `Observation`** (por ejemplo, glucemia LOINC `2339-0`, saturación de oxígeno `59408-5` o presión inspiratoria `19994-3`):

1. Extrae el código LOINC (`code.coding[0].code`), el valor numérico (`valueQuantity.value`), la unidad y la referencia al paciente (`subject.reference`, ej. `"Patient/1"`).
2. Persiste la observación en la base de datos clínica vinculada al encuentro activo del paciente en UCI.
3. Si el valor supera umbrales críticos, crea una alerta crítica activa (**R20**).
4. Emite en tiempo real por **SSE** (`OBSERVACION_FHIR_RECIBIDA`) la notificación al médico responsable y registra el evento en `audit_logs` con `origen = "FHIR"`.

---

## 5. Catálogo de Eventos Externos de Interoperabilidad Interinstitucional (HL7 FHIR R4) · R19, R30–R33

Para el intercambio máquina a máquina (M2M) con la institución socia (**Pre-Triaje**), el catálogo reactivo incorpora los **tres eventos externos obligatorios** con cifrado entidad-a-entidad (`AES-256-Fernet + HMAC-SHA256`) y auditoría encadenada (`SHA-256`, **R28**):

```mermaid
sequenceDiagram
    autonumber
    participant Socio as Sistema Socio (Pre-Triaje)
    participant API as FastAPI UCI (/interoperabilidad)
    participant DB as PostgreSQL Neon (pacientes + remisiones + audit_logs)
    participant UI as Dashboard Clínico UCI (Bandeja & Historial)

    Note over Socio,UI: 1. Evento Externo: Remisión Recibida de Otro Sistema (POST /interoperabilidad/remisiones)
    Socio->>API: POST /interoperabilidad/remisiones (Bundle FHIR + X-System-Key)
    API->>API: Valida R31 (API Key), R32 (Consent=active) y R30 (Patient + ServiceRequest)
    API->>DB: Registra paciente nuevo en `pacientes`, asigna cama/equipo UCI y crea Historia Clínica (HC-UCI-2026-XXXX)
    API->>DB: INSERT audit_logs (accion='HISTORIA_CLINICA_FHIR_INTEGRADA_AUTO', origen='EXTERNO:Socio', hash SHA-256)
    API-->>Socio: HTTP 201 Created (FHIR Task status='completed', folio_historia_clinica)
    API-->>UI: Actualización en vivo en Bandeja de Entrada (📥 ENTRANTE / RECIBIDA · 🟢 ACEPTADA AUTOMÁTICAMENTE)

    Note over Socio,UI: 2. Evento Externo: Envío y Respuesta del Socio (POST /interoperabilidad/remitir & /callback)
    UI->>API: Médico emite Bundle FHIR de paciente UCI (POST /interoperabilidad/remitir)
    API->>Socio: POST <url_socio> (Bundle FHIR R4 + Cifrado AES-256 + X-System-Key)
    Socio-->>API: HTTP 200/201 Respuesta del Socio (Bundle / Task)
    API->>DB: INSERT remisiones_interinstitucionales (direccion='SALIENTE', estado='ACEPTADA') + audit_logs SHA-256
    API-->>UI: Confirmación en vivo (📤 ENVIADO / RECIBIDO · Folio HC-UCI-2026-XXXX)

    Note over Socio,UI: 3. Evento Externo: Rechazo Automático por Validación (R30 / R31 / R32)
    Socio->>API: POST /interoperabilidad/remisiones (Sin API Key, sin Consent active o sin motivo en ServiceRequest)
    API->>DB: INSERT audit_logs (accion='INTEROP_RECHAZADA_R30/R31/R32', hash SHA-256) — Sin guardar datos a medias
    API-->>Socio: HTTP 401 (R31) o HTTP 422 (R30/R32) con recurso FHIR OperationOutcome
    API-->>UI: Visualización de alerta roja con OperationOutcome y trazabilidad en tabla de auditoría
```

### Detalle de los 3 Eventos Externos (R30–R33)

| # | Evento Externo | Endpoint / Disparador | Validaciones y Acción Reactiva Automática | Persistencia y Auditoría (`R28`) |
| :-: | :--- | :--- | :--- | :--- |
| **5.1** | **Remisión recibida de otro sistema** (`HISTORIA_CLINICA_FHIR_INTEGRADA_AUTO`) | `POST /interoperabilidad/remisiones` | Valida credencial de sistema `X-System-Key` (**R31**), `Consent.status == 'active'` (**R32**) y estructura del `Bundle` (**R30**). **Acepta automáticamente** al paciente, si no existe en `pacientes` lo crea con cama UCI (`UCI-BED-XX`) y equipo biomédico, inserta sus signos vitales LOINC en `observaciones` y crea su **Nueva Historia Clínica (`HC-UCI-2026-XXXX`)**. | Tabla `remisiones_interinstitucionales` (`direccion = 'ENTRANTE'`, `estado = 'ACEPTADA'`) + `audit_logs` con `origen = 'EXTERNO:<Institución>'` y hash encadenado SHA-256. |
| **5.2** | **Envío y respuesta del socio** (`REMISION_FHIR_ENVIADA_A_SOCIO` / `CALLBACK_SOCIO`) | `POST /interoperabilidad/remitir` y `POST /interoperabilidad/callback` | Empaqueta al paciente de nuestra UCI en un `Bundle` FHIR R4 (`Patient`, `ServiceRequest` SNOMED, `Consent` Ley 1581 y `Observation` LOINC) con sobre cifrado `AES-256-Fernet`, lo transmite al endpoint público del socio, procesa la respuesta (`Task` / `Bundle`) y actualiza el estado en vivo. | Tabla `remisiones_interinstitucionales` (`direccion = 'SALIENTE'`, `estado = 'ACEPTADA'`) + `audit_logs` con hash encadenado SHA-256. |
| **5.3** | **Rechazo por validación o seguridad** (`INTEROP_RECHAZADA_R30` / `R31` / `R32`) | `POST /interoperabilidad/remisiones`, `/ping` o `/remitir` | Si falta o es inválida la API Key (**R31** $\rightarrow$ `HTTP 401`), si el paciente no tiene consentimiento activo Ley 1581 (**R32** $\rightarrow$ `HTTP 422`), o si el `Bundle` está incompleto o incluye un `DiagnosticReport` de IA no aprobado (**R30** $\rightarrow$ `HTTP 422`), rechaza la operación devolviendo un recurso estándar **`OperationOutcome`** **sin dejar datos a medias**. | Registro del rechazo en `audit_logs` (`INTEROP_RECHAZADA_R31`, `INTEROP_RECHAZADA_R32`, `INTEROP_RECHAZADA_R30`) con hash SHA-256 inmutable. |

