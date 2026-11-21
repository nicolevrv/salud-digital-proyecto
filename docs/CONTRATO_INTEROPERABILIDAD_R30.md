# 🏥 Contrato de Interoperabilidad Hospitalaria HL7 FHIR R4 (R30 · R31 · R32 · R33)

**Institución:** Hospital Universitario UCI - Salud Digital  
**Estándar:** HL7 FHIR R4 (`Bundle` con recursos `Patient`, `ServiceRequest`, `Consent` y `Observation`)  
**Cifrado:** Entidad-a-Entidad `AES-256-Fernet + HMAC-SHA256` sobre `TLS 1.3`  
**Normatividad:** Ley 2015 de 2020 (IHCE) y Ley 1581 de 2012 (Habeas Data / Consentimiento Informado)

---

## 1. Credenciales y Endpoints Oficiales (Para conectar con nuestro servidor)

| Parámetro | Valor Oficial |
| :--- | :--- |
| **URL Base Pública** | `https://salud-digital-proyecto.onrender.com` |
| **Endpoint Recepción de Remisiones FHIR (`POST`)** | `https://salud-digital-proyecto.onrender.com/interoperabilidad/remisiones` |
| **Endpoint Directorio de Pacientes BD (`GET`)** | `https://salud-digital-proyecto.onrender.com/interoperabilidad/pacientes` |
| **Endpoint Consulta de Paciente Específico (`GET`)** | `https://salud-digital-proyecto.onrender.com/interoperabilidad/pacientes/{paciente_id}` |
| **Endpoint Callback de Estado (`POST`)** | `https://salud-digital-proyecto.onrender.com/interoperabilidad/callback` |
| **Header de Autenticación M2M (R31)** | `X-System-Key` *(también acepta `X-API-Key` o `Authorization: Bearer <API_KEY>`)* |
| **API Key Institucional (R31)** | `HOSPITAL-UCI-INTEROP-KEY-2026` |
| **Content-Type** | `application/json` |

---

## 2. Headers HTTP Requeridos (R31)

Todas las peticiones máquina a máquina (M2M) hacia nuestros endpoints deben incluir la credencial institucional en los headers:

```http
Content-Type: application/json
X-System-Key: HOSPITAL-UCI-INTEROP-KEY-2026
X-Institucion-Origen: Nombre de su Institución / Grupo
```

*(Nota: Nuestro servidor también acepta la clave en `X-API-Key: HOSPITAL-UCI-INTEROP-KEY-2026` o `Authorization: Bearer HOSPITAL-UCI-INTEROP-KEY-2026` para máxima compatibilidad).*

Si la clave no se envía o es incorrecta, el servidor rechaza la petición con **`HTTP 401 Unauthorized`** devolviendo un recurso estándar `OperationOutcome` (R31).

---

## 3. Contrato del Payload para Enviar una Remisión (`POST /interoperabilidad/remisiones`) — R30 & R32

Para enviarnos un paciente o historia clínica, deben hacer un `POST` a:
`https://salud-digital-proyecto.onrender.com/interoperabilidad/remisiones`

El cuerpo (`body`) debe ser un JSON con `resourceType: "Bundle"` que incluya en su arreglo `entry`:
1. **`Patient`** (Obligatorio - R30): Datos de identificación (`id`, `identifier`, `name`).
2. **`ServiceRequest`** (Obligatorio - R30): Solicitud clínica con motivo obligatorio en `reasonCode[0].text`.
3. **`Consent`** (Obligatorio - R32): Consentimiento informado Ley 1581/2012 con `"status": "active"`. Si no está activo, se rechaza con `HTTP 422 Unprocessable Entity`.
4. **`Observation`** (Opcional/Recomendado): Signos vitales codificados en estándar **LOINC** (`8867-4` Frecuencia cardíaca, `8480-6` Presión sistólica, `2708-6` SpO2, `8310-5` Temperatura).
