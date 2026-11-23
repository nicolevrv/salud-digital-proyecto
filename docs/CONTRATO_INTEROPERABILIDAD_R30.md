# Contrato de Interoperabilidad R30–R33 · Hospital Universitario UCI - Salud Digital (Doble Seguridad: API Key + OAuth2 Token)

Documento oficial para el **equipo socio**. Nuestro servidor en Render (**`https://salud-digital-proyecto.onrender.com`**) soporta **ambos mecanismos de autenticación M2M (R31)**:
1. **Doble Seguridad con Token OAuth2 (`client_credentials`)** (token temporal firmado `uci_oauth_...` de 10 minutos).
2. **API Key Institucional Directa (`X-System-Key` o `Authorization: Bearer`)**.

Así, cualquier grupo socio (ya sea que use solo Token OAuth2 o solo API Key) puede conectarse con nosotros sin modificar su arquitectura.

---

## 1. Autenticación M2M (R31) — Dos Modos Soportados

### Modo A: Doble Seguridad con Token OAuth2 (`client_credentials`)
Primero solicitan un `access_token` temporal (duración: **600 segundos / 10 minutos**):

```http
POST https://salud-digital-proyecto.onrender.com/interoperabilidad/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=hospital-uci-client&client_secret=HOSPITAL-UCI-INTEROP-KEY-2026
```
*(También disponible en `POST https://salud-digital-proyecto.onrender.com/api/oauth/token` y acepta cuerpo JSON o form-urlencoded).*

* **`client_id`:** `hospital-uci-client`
* **`client_secret`:** `HOSPITAL-UCI-INTEROP-KEY-2026`
* **Respuesta (`HTTP 200 OK`):**
  ```json
  {
    "access_token": "uci_oauth_gAAAAAB...",
    "token_type": "Bearer",
    "expires_in": 600,
    "scope": "bundle:write task:write",
    "client_id": "hospital-uci-client"
  }
  ```
* Luego envían ese token en la cabecera de sus peticiones FHIR:
  ```http
  Authorization: Bearer <access_token>
  ```

### Modo B: API Key Institucional Directa (En un solo paso)
También pueden autenticar directamente cada petición enviando la clave institucional en cualquiera de estas cabeceras:
```http
X-System-Key: HOSPITAL-UCI-INTEROP-KEY-2026
```
*(O `X-API-Key: HOSPITAL-UCI-INTEROP-KEY-2026` o `Authorization: Bearer HOSPITAL-UCI-INTEROP-KEY-2026`).*

> **Validación R31:** Si la petición no incluye un Token OAuth2 vigente ni la API Key válida, el servidor rechaza el acceso con **`HTTP 401 Unauthorized`** devolviendo un recurso `OperationOutcome`.

---

## 2. Endpoints Oficiales de Nuestro Servidor

| Operación | Método y URL Oficial | Alias Compatible |
| :--- | :--- | :--- |
| **1. Obtener Token OAuth2 (R31)** | `POST https://salud-digital-proyecto.onrender.com/interoperabilidad/oauth/token` | `POST .../api/oauth/token` |
| **2. Enviar Remisión FHIR a nuestra UCI (R30)** | `POST https://salud-digital-proyecto.onrender.com/interoperabilidad/remisiones` | `POST .../api/fhir/remisiones-r30/recibir` |
| **3. Enviar Respuesta / Callback `Task`** | `POST https://salud-digital-proyecto.onrender.com/interoperabilidad/callback` | `POST .../api/fhir/remisiones-r30/respuesta` |
| **4. Consultar Pacientes UCI (Opcional)** | `GET https://salud-digital-proyecto.onrender.com/interoperabilidad/pacientes` | `GET .../api/pacientes` |

---

## 3. Envío de Remisiones (`POST /interoperabilidad/remisiones`) — R30 & R32

El cuerpo debe ser un JSON HL7 FHIR R4 con `"resourceType": "Bundle"` (`type: "collection"` o `"transaction"`) que incluya en `"entry"`:
1. **`Patient` (Obligatorio - R30):** Con `"id"`, `"identifier"` y/o `"name"` (también soporta paciente seudonimizado).
2. **`ServiceRequest` (Obligatorio - R30):** Con servicio en `code` (SNOMED CT / LOINC) y **motivo clínico obligatorio en `reasonCode[0].text`**. Si falta el motivo, se rechaza con `HTTP 422 OperationOutcome` sin guardar datos a medias.
3. **`Consent` (Obligatorio - R32 Ley 1581/2012):** Con **`"status": "active"`**. Si no está activo, se rechaza con `HTTP 422 OperationOutcome`.
4. **`Observation` (0 o más):** Signos vitales codificados en **LOINC** (`8867-4` FC, `8480-6` PA, `2708-6` SpO2, `8310-5` Temperatura).
5. **`DiagnosticReport` (Opcional - R30):** Solo reportes de IA **aprobados** (`status = "final"`).

### Respuesta Automática (`HTTP 201 Created`):
Al superar R31, R32 y R30, nuestro sistema **acepta automáticamente** la remisión, registra al paciente en nuestra BD UCI con cama (`UCI-BED-XX`) y equipo biomédico, crea su **Nueva Historia Clínica (`HC-UCI-2026-XXXX`)**, registra el hash encadenado **SHA-256 (R28)** en `audit_logs` y devuelve el recurso FHIR `Task` con `status: "completed"`.
