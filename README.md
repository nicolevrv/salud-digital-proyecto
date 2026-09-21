# Telemetría UCI - Sistema de Salud Digital con Dashboard Web

Plataforma integral de telemetría biomédica y control clínico en Unidad de Cuidados Intensivos (UCI), desarrollada con **FastAPI**, base de datos relacional (**Neon PostgreSQL** / PostgreSQL), integración con el estándar **HL7 FHIR R4**, control de acceso basado en 3 roles (*Admin Biomédico*, *Médico*, *Servicio IoT*) y **Dashboard Web Interactivo** listo para despliegue en la nube en **Render**.

---

## 🚀 Características Principales

1. **Dashboard Web Clínico en Tiempo Real**:
   - Monitoreo dinámico de señales vitales con **Chart.js** (SpO2, Frecuencia Cardíaca, Presión Vía Aérea Pico).
   - Vista en tiempo real del estado de camas UCI y asignación tecnológica.
   - Botón de **Simulación Rápida de Telemetría IoT** para pruebas en vivo en Render.
   - Gestión de pacientes y asignación a camas UCI (`+ Nuevo Paciente`, `+ Iniciar Encuentro`).

2. **Gestión Tecnológica Biomédica & Hojas de Vida**:
   - Inventario metrológico de ventiladores mecánicos (Hamilton, Dräger), monitores multiparámetro (Mindray, Philips), bombas de infusión (BBraun, Baxter) y analizadores de gases.
   - Control de nivel de batería de backup, clasificación de riesgo INVIMA (I, IIA, IIB, III) y cambio de estado operativo (*Disponible*, *En Uso*, *Mantenimiento*, *Descalibrado*).

3. **Restricciones de Autoría y Soft Delete**:
   - **Soft Edit**: Versionamiento automático de mediciones clínicas con trazabilidad.
   - **Soft Delete**: Los médicos solo pueden eliminar o editar sus propios registros clínicos.
   - **Restore**: Operación reservada **exclusivamente al Admin Biomédico**.

4. **Pista de Auditoría Inmutable (`audit_logs`)**:
   - Trazabilidad estricta de cada evento `SOFT_EDIT`, `SOFT_DELETE` y `RESTORE` con marca temporal y usuario responsable.

5. **Preparado para Despliegue en Render**:
   - Soporte automático para la variable de entorno `DATABASE_URL` (Neon PostgreSQL).
   - Botón en interfaz `⚡ Sembrar BD` para inicializar el esquema y datos semilla con un solo clic desde el navegador.

---

## 👥 Roles y Credenciales de Prueba

El sistema implementa 3 roles con permisos diferenciados:

| Rol | Usuario | Correo Electrónico | Contraseña | Permisos Clave |
| :--- | :--- | :--- | :--- | :--- |
| **Admin Biomédico** | Ing. Biomédico Admin | `admin.biomedico@hospital.com` | `admin123` | Gestión metrológica de equipos, edición global, **Restauración exclusiva (RESTORE)**, acceso a auditoría. |
| **Médico** | Dr. Camilo Torres | `medico@hospital.com` | `med123` | Registro de pacientes, inicio de encuentros, edición y borrado **únicamente de sus propios registros clínicos**. |
| **Servicio IoT** | Servicio Telemetría UCI | `servicio.iot@hospital.com` | `service123` | Emisión continua de paquetes de telemetría y signos vitales desde sensores biomédicos. |

> 💡 *En el Dashboard web puedes alternar entre cualquiera de estos 3 roles desde el menú superior con un solo clic.*

---

## 🌐 Guía de Despliegue en Render (Paso a Paso)

Para publicar el proyecto en Render y obtener tu URL pública (ejemplo: `https://salud-digital-telemetria-uci.onrender.com`):

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

### Paso 4: Inicializar la Base de Datos desde el Navegador
1. Una vez que Render complete el despliegue, abre la URL que te asigna Render (ejemplo: `https://tu-proyecto.onrender.com`).
2. En la barra superior verás el botón **⚡ Sembrar BD**. Haz clic en él.
3. El sistema ejecutará automáticamente el script `01_schema.sql` en Neon, creando todas las tablas, roles, usuarios, catálogo de equipos biomédicos y datos semilla.
4. ¡Listo! El Dashboard estará 100% operativo con gráficos, botones y control de roles.

---

## 🖥️ Ejecución Local (Opcional)

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
- `GET /health`: Verificación del estado de salud y conectividad con Neon PostgreSQL.
- `POST /init-db`: Inicialización y siembra del esquema de base de datos.
- `GET /dashboard-stats`: Estadísticas en tiempo real (camas, pacientes, equipos, alertas).
- `GET /pacientes`: Listado de pacientes y estado de encuentros.
- `POST /pacientes`: Admisión de nuevo paciente a UCI.
- `GET /equipos-uci`: Inventario metrológico de equipos y hojas de vida.
- `PUT /equipos-uci/{id}/soft-edit`: Actualización de estado y batería de backup (*Admin Biomédico*).
- `GET /observaciones`: Mediciones telemétricas con filtro de registros eliminados.
- `POST /observaciones`: Registro de nueva medición telemétrica.
- `PUT /observaciones/{id}/soft-edit`: Modificación con versionamiento y log de auditoría.
- `DELETE /observaciones/{id}/soft-delete`: Soft delete respetando restricciones de autoría.
- `POST /observaciones/{id}/restore`: **Restauración exclusiva** (*Admin Biomédico*).
- `GET /audit-logs`: Pistas de auditoría inmutables.
- `POST /simulate-telemetry`: Generador de paquetes de telemetría IoT en vivo.
