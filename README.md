# PECHY'S IOT - Salud Digital

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

3. **Restricciones de Autoría y Soft Delete**:
   - **Soft Edit / Soft Delete**: Los médicos solo pueden modificar sus propios registros clínicos.
   - **Restauración clínica**: Reservada al médico autor.
   - **Bloqueo de acceso**: La cuenta se bloquea durante 15 minutos tras tres contraseñas incorrectas; el sistema informa el intento alcanzado.

4. **Pista de Auditoría Inmutable (`audit_logs`)**:
   - Trazabilidad estricta de cada evento `SOFT_EDIT`, `SOFT_DELETE` y `RESTORE` con marca temporal y usuario responsable.

5. **Datos normalizados**:
   - Las hojas de vida se relacionan con el catálogo de tipos de equipos.
   - Las observaciones se relacionan con un catálogo único de parámetros LOINC y unidades.

---

## 👥 Roles y Credenciales de Prueba

El sistema implementa cuatro perfiles con permisos diferenciados:

| Perfil | Correo | Contraseña demo | Permisos |
| :--- | :--- | :--- | :--- |
| **Paciente** | `paciente@hospital.com` | `paciente123` | Consulta únicamente su propio registro, encuentros, mediciones y equipos asignados. |
| **Médico** | `medico@hospital.com` | `med123` | Consulta y crea registros clínicos; solo modifica los registros que creó. |
| **Administrador biomédico** | `admin.biomedico@hospital.com` | `admin123` | Crea y administra hojas de vida/equipos y consulta auditoría. No accede a datos clínicos. |
| **Servicio IoT** | `servicio.iot@hospital.com` | `service123` | Registra telemetría; no consulta datos ni administra cuentas. |

Estas credenciales son únicamente para demostración local. Las contraseñas se convierten a PBKDF2 tras el primer inicio de sesión correcto. No las uses en producción.

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

En Adminer selecciona **PostgreSQL**, servidor `host.docker.internal:5433`, usuario `admin`, contraseña `adminpassword` y base de datos `uci_telemetria`. El esquema y los datos demo se cargan automáticamente al crear el volumen por primera vez. La cuenta de paciente demo solo consulta el paciente asociado a su usuario.

`docker compose down` detiene los servicios y conserva la base de datos. `docker compose down -v` elimina los volúmenes y todos sus datos; úsalo solo cuando quieras borrar la base de datos local.

## Despliegue en Render (opcional)

Para publicar el proyecto en Render se necesita una base PostgreSQL aprovisionada y aplicar el esquema antes del primer inicio de sesión:

### Paso 1: Subir tus Cambios a GitHub
Asegúrate de hacer push de este repositorio a tu cuenta de GitHub:
```bash
git add .
git commit -m "feat: dashboard interactivo uci y soporte para despliegue en render con neon"
git push origin main
```

### Paso 2: Crear el Web Service en Render
1. Ve a [dashboard.render.com](https://dashboard.render.com) e inicia sesión.
2. Haz clic en **New +** y selecciona **Web Service**.
3. Conecta tu repositorio de GitHub `salud-digital-proyecto`.
4. Render detectará la configuración. Verifica o ajusta lo siguiente:
   - **Name**: `salud-digital-proyecto` (o el nombre que desees)
   - **Language / Runtime**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn middleware.app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`

### Paso 3: Configurar la Base de Datos (Neon PostgreSQL)
1. En la sección **Environment Variables** en Render, agrega la siguiente variable:
   - **Key**: `DATABASE_URL`
   - **Value**: Tu cadena de conexión de Neon (ejemplo: `postgresql://neondb_owner:tu_password@ep-cold-lake-123456.us-east-2.aws.neon.tech/neondb?sslmode=require`)
2. Haz clic en **Deploy Web Service**.

### Paso 4: Aplicar el esquema
Antes de iniciar sesión, ejecuta el esquema contra la base configurada en `DATABASE_URL`:

```bash
docker run --rm -v "$PWD/init-scripts:/schema:ro" postgres:15-alpine \
   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /schema/01_schema.sql
```

El endpoint `/init-db` requiere autenticación administrativa y sirve para actualizar un esquema ya inicializado; no es un mecanismo de aprovisionamiento anónimo.

---

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
