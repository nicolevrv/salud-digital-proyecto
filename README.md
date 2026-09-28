# BIOT UAO - Salud Digital

Aplicación para gestionar hojas de vida de equipos biomédicos, inventario UCI y variables clínicas identificadas por código LOINC. Usa **FastAPI**, **PostgreSQL** y un panel de inspección de base de datos; toda la aplicación local se ejecuta con Docker Compose.

---

## 🚀 Características Principales

1. **Dashboard Web Clínico en Tiempo Real**:
   - Monitoreo dinámico de señales vitales con **Chart.js** (SpO2, Frecuencia Cardíaca, Presión Vía Aérea Pico).
   - Vista en tiempo real del estado de camas UCI y asignación tecnológica.
   - Simulación de telemetría disponible únicamente para la cuenta de servicio IoT.
   - Gestión de pacientes y asignación a camas UCI (`+ Nuevo Paciente`, `+ Iniciar Encuentro`).

2. **Gestión Tecnológica Biomédica & Hojas de Vida**:
   - Inventario metrológico de ventiladores mecánicos (Hamilton, Dräger), monitores multiparámetro (Mindray, Philips), bombas de infusión (BBraun, Baxter) y analizadores de gases.
   - Control de nivel de batería de backup, clasificación de riesgo INVIMA (I, IIA, IIB, III) y cambio de estado operativo (*Disponible*, *En Uso*, *Mantenimiento*, *Descalibrado*).

3. **Autenticación y control de acceso**:
   - Tres perfiles funcionales: **Administrador biomédico**, **Médico** y **Servicio IoT**. No existe rol de paciente.
   - Las contraseñas se almacenan mediante PBKDF2 y las credenciales demo se migran de forma compatible con instalaciones anteriores.
   - El navegador conserva únicamente un token de sesión firmado; nunca almacena la contraseña para consumir la API.
   - La cuenta se bloquea durante 15 minutos tras tres contraseñas incorrectas.

4. **Pista de Auditoría Inmutable (`audit_logs`)**:
   - Trazabilidad estricta de cada evento `SOFT_EDIT`, `SOFT_DELETE` y `RESTORE` con marca temporal y usuario responsable.

5. **Datos normalizados**:
   - Las hojas de vida se relacionan con el catálogo de tipos de equipos.
   - Las observaciones se relacionan con un catálogo único de parámetros LOINC y unidades.

---

## 👥 Roles y Credenciales de Prueba

El sistema implementa tres perfiles con permisos diferenciados:

| Perfil | Correo | Contraseña demo | Permisos |
| :--- | :--- | :--- | :--- |
| **Médico** | `medico@hospital.com` | `med123` | Consulta y crea registros clínicos; solo modifica los registros que creó. |
| **Administrador biomédico** | `admin.biomedico@hospital.com` | `admin123` | Crea y administra hojas de vida/equipos y consulta auditoría. No accede a datos clínicos. |
| **Servicio IoT** | `servicio.iot@hospital.com` | `service123` | Registra telemetría; no consulta datos ni administra cuentas. |

Los pacientes son registros clínicos y no tienen cuentas de acceso. Estas credenciales son únicamente para demostración local. Las contraseñas se convierten a PBKDF2 tras el primer inicio de sesión correcto. No las uses en producción.

---

## Ejecución con Docker

Desde la carpeta `salud-digital-proyecto-main`, inicia la API, PostgreSQL, el visor de base de datos y el servidor FHIR:

```bash
docker compose up --build -d
docker compose ps
```

- Aplicación: <http://localhost:8000>
- Documentación de la API: <http://localhost:8000/docs>
- Revisión de tablas y registros: <http://localhost:8081>
- Servidor FHIR: <http://localhost:8080/fhir>

En Adminer selecciona **PostgreSQL**, servidor `host.docker.internal:5433`, usuario `admin`, contraseña `adminpassword` y base de datos `uci_telemetria`. El esquema y los datos demo se cargan automáticamente al crear el volumen por primera vez.

`docker compose down` detiene los servicios y conserva la base de datos. `docker compose down -v` elimina los volúmenes y todos sus datos; úsalo solo cuando quieras borrar la base de datos local.

## Despliegue en Render

El repositorio incluye un `render.yaml` para mantener la configuración del servicio como código.

### Variables de entorno

En Render debes proporcionar únicamente:
- `DATABASE_URL`: cadena de conexión de tu PostgreSQL de Neon con `sslmode=require`.
- `SESSION_SECRET`: se genera automáticamente desde el Blueprint.
- `AUTO_INIT_DB=true`: hace que la aplicación verifique/aplique `init-scripts/01_schema.sql` al arrancar.
- `SESSION_TTL_MINUTES=480`: duración de la sesión en minutos.

No debes colocar secretos dentro de GitHub.

### Comportamiento de despliegue

El servicio está configurado con `autoDeployTrigger: commit` sobre la rama `main`. Cuando Render tiene el repositorio GitHub vinculado y los auto-deploys están activos, un push a `main` genera el nuevo despliegue automáticamente. citeturn357666search0turn357666search1

El endpoint `/health` se usa como health check. En el primer arranque con Neon, la aplicación inicializa/verifica las tablas y los datos semilla automáticamente.

### HAPI FHIR

HAPI FHIR es opcional para el funcionamiento del dashboard principal. Si `HAPI_FHIR_URL` no está configurada, las operaciones de sincronización FHIR se omiten sin bloquear la autenticación ni el resto del sistema. Para usar FHIR en producción, configura una instancia HAPI FHIR accesible desde Render y define esa URL en la variable correspondiente.

## Ejecución manual fuera de Docker (opcional)

Si deseas probarlo localmente:

1. Clonar el repositorio y entrar a la carpeta:
   ```bash
   git clone https://github.com/nicolevrv/salud-digital-proyecto.git
   cd salud-digital-proyecto
   ```

2. Crear y activar entorno virtual:
   ```bash
   python -m venv venv
   source venv/bin/activate   # En Linux / Mac
   venv\Scripts\activate      # En Windows
   ```

3. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

4. Configurar variable `DATABASE_URL` en tu archivo `.env`:
   ```env
   DATABASE_URL=postgresql://usuario:password@ep-xyz.neon.tech/neondb?sslmode=require
   ```

La aplicación carga este archivo automáticamente. Para usar PostgreSQL de Docker desde un `uvicorn` ejecutado en el host, la conexión local predeterminada es `localhost:5433`; desde el contenedor de la API se usa el servicio `app-db:5432`.

5. Iniciar el servidor FastAPI:
   ```bash
   uvicorn middleware.app.main:app --reload --port 8000
   ```

6. Abrir en el navegador:
   - Dashboard: `http://localhost:8000/`
   - Documentación Interactiva Swagger: `http://localhost:8000/docs`

---

## 📡 Endpoints Principales de la API REST

- `GET /`: Dashboard web interactivo para monitoreo clínico.
- `GET /health`: Estado de la API y conexión PostgreSQL.
- `POST /init-db`: Actualización autenticada del esquema por administración biomédica.
- `GET /dashboard-stats`: Estadísticas en tiempo real (camas, pacientes, equipos, alertas).
- `GET /pacientes`: Listado de pacientes y estado de encuentros.
- `POST /pacientes`: Admisión de nuevo paciente a UCI.
- `GET /catalogo-equipos`: Tipos de equipo disponibles para el administrador biomédico.
- `POST /equipos-uci`: Creación de hoja de vida y registro UCI por administración biomédica.
- `GET /equipos-uci`: Inventario metrológico de equipos y hojas de vida.
- `PUT /equipos-uci/{id}/soft-edit`: Actualización de estado y batería de backup (*Admin Biomédico*).
- `GET /observaciones`: Mediciones telemétricas con filtro de registros eliminados.
- `POST /observaciones`: Registro de nueva medición telemétrica.
- `PUT /observaciones/{id}/soft-edit`: Modificación con versionamiento y log de auditoría.
- `DELETE /observaciones/{id}/soft-delete`: Soft delete respetando restricciones de autoría.
- `POST /observaciones/{id}/restore`: Restauración del registro propio por su médico autor.
- `GET /audit-logs`: Auditoría administrativa.
- `POST /simulate-telemetry`: Simulación disponible únicamente para el servicio IoT.
