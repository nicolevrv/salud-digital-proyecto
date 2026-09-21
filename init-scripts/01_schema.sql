-- =========================================================================
-- ESTRUCTURA DE TABLAS - SALUD DIGITAL / TELEMETRÍA UCI
-- =========================================================================

-- 1. Tabla de Roles
CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL -- 'Admin Biomedico', 'Medico', 'Servicio'
);

-- 2. Tabla de Usuarios
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    rol_id INT REFERENCES roles(id),
    is_deleted BOOLEAN DEFAULT FALSE
);

-- 3. Tabla de Catálogo General de Equipos (Definición de tipos de tecnología biomédica e industrial del hospital)
CREATE TABLE IF NOT EXISTS catalogo_equipos (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    tipo_servicio VARCHAR(50) NOT NULL, -- 'UCI', 'Hospitalización', 'Quirófano', 'Urgencias', 'Imagenología', 'Laboratorio'
    clasificacion_riesgo VARCHAR(10) NOT NULL, -- 'I', 'IIA', 'IIB', 'III' (Según Res. 3100 / Decreto 4725)
    tecnologia_predominante VARCHAR(50) DEFAULT 'Electromédico',
    is_deleted BOOLEAN DEFAULT FALSE
);

-- 4. Tabla de Hoja de Vida de Equipos (Registro legal/metrológico de TODOS los equipos del hospital)
CREATE TABLE IF NOT EXISTS hoja_vida_equipos (
    id SERIAL PRIMARY KEY,
    equipo_catalogo_id INT REFERENCES catalogo_equipos(id),
    codigo_inventario VARCHAR(50) UNIQUE NOT NULL, -- ej: 'HV-EQUIPO-001'
    marca VARCHAR(50) NOT NULL,
    modelo VARCHAR(50) NOT NULL,
    numero_serie VARCHAR(50) UNIQUE NOT NULL,
    registro_invima VARCHAR(50) NOT NULL,
    es_equipo_uci BOOLEAN DEFAULT FALSE, -- Distingue equipos de UCI vs otros servicios del hospital
    servicio_asignado VARCHAR(50) NOT NULL, -- 'UCI Adultos', 'UCI Neonatal', 'Cirugía', 'Hospitalización Piso 3', 'Urgencias'
    fecha_adquisicion DATE NOT NULL,
    vida_util_anios INT DEFAULT 8,
    frecuencia_mantenimiento_meses INT DEFAULT 6,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id)
);

-- 5. Tabla de Equipos UCI (Exclusiva para monitoreo telemétrico en tiempo real y asignación a camas UCI)
CREATE TABLE IF NOT EXISTS equipos_uci (
    id SERIAL PRIMARY KEY,
    hoja_vida_id INT UNIQUE REFERENCES hoja_vida_equipos(id), -- Vinculado a su hoja de vida
    ubicacion_uci VARCHAR(50) NOT NULL, -- ej: 'Cama 01 - Box A', 'Módulo Respiratorio UCI-2'
    estado_operativo VARCHAR(50) NOT NULL DEFAULT 'Disponible', -- 'En Uso', 'Disponible', 'Mantenimiento', 'Descalibrado', 'Fuera de Servicio'
    bateria_backup_porcentaje INT DEFAULT 100,
    ultima_inspeccion_biomedica TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id)
);

-- 6. Tabla de Pacientes
CREATE TABLE IF NOT EXISTS pacientes (
    id SERIAL PRIMARY KEY,
    documento_identidad VARCHAR(50) UNIQUE NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    cama_uci VARCHAR(20) NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id)
);

-- 7. Tabla de Encuentros Clínicos / Monitoreo en UCI
CREATE TABLE IF NOT EXISTS encuentros (
    id SERIAL PRIMARY KEY,
    paciente_id INT REFERENCES pacientes(id),
    equipo_uci_id INT REFERENCES equipos_uci(id), -- Relación directa con el equipo UCI activo
    fecha_inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fecha_fin TIMESTAMP,
    estado VARCHAR(50) DEFAULT 'in-progress',
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id)
);

-- Migraciones automáticas por si las tablas ya existían en esquemas antiguos de Neon
ALTER TABLE encuentros ADD COLUMN IF NOT EXISTS equipo_uci_id INT REFERENCES equipos_uci(id);
ALTER TABLE encuentros ADD COLUMN IF NOT EXISTS fecha_fin TIMESTAMP;
ALTER TABLE encuentros ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;
ALTER TABLE encuentros ADD COLUMN IF NOT EXISTS created_by INT REFERENCES usuarios(id);

-- 8. Tabla de Observaciones Telemétricas (Control Predictivo)
CREATE TABLE IF NOT EXISTS observaciones (
    id SERIAL PRIMARY KEY,
    encuentro_id INT REFERENCES encuentros(id),
    parametro VARCHAR(100) NOT NULL, -- ej: 'Presión Vía Aérea (Pik)', 'Frecuencia Cardíaca'
    codigo_loinc VARCHAR(50) NOT NULL, -- ej: '8310-5'
    valor NUMERIC NOT NULL,
    unidad VARCHAR(20) NOT NULL,
    alerta_predictiva TEXT,
    version INT DEFAULT 1,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. Registros de Auditoría
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    usuario_id INT REFERENCES usuarios(id),
    accion VARCHAR(50) NOT NULL, -- 'SOFT_DELETE', 'SOFT_EDIT', 'RESTORE'
    tabla_afectada VARCHAR(50) NOT NULL,
    registro_id INT NOT NULL,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- =========================================================================
-- DATOS SEMILLA Y POBLAMIENTO INICIAL
-- =========================================================================

-- 1. Insertar 3 Roles Requeridos (Actualiza si existían con nombres viejos)
INSERT INTO roles (id, nombre) VALUES 
(1, 'Admin Biomedico'),
(2, 'Medico'),
(3, 'Servicio')
ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre;

-- 2. Insertar Usuarios de Prueba (Garantiza credenciales exactas del proyecto)
INSERT INTO usuarios (id, nombre, email, password_hash, rol_id) VALUES
(1, 'Ing. Biomédico Admin', 'admin.biomedico@hospital.com', 'admin123', 1),
(2, 'Dr. Camilo Torres', 'medico@hospital.com', 'med123', 2),
(3, 'Servicio Telemetria UCI', 'servicio.iot@hospital.com', 'service123', 3)
ON CONFLICT (id) DO UPDATE SET 
    nombre = EXCLUDED.nombre,
    email = EXCLUDED.email,
    password_hash = EXCLUDED.password_hash,
    rol_id = EXCLUDED.rol_id,
    is_deleted = FALSE;

-- 3. Insertar Catálogo General de Equipos Médicos Hospitalarios
INSERT INTO catalogo_equipos (id, nombre, tipo_servicio, clasificacion_riesgo, tecnologia_predominante) VALUES
(1, 'Ventilador Mecánico de Soporte Vital', 'UCI', 'III', 'Electromédico / Neumático'),
(2, 'Monitor Multiparámetro de Signos Vitales', 'UCI', 'IIB', 'Electromédico'),
(3, 'Bomba de Infusión Volumétrica Peristáltica', 'UCI', 'IIB', 'Electromédico'),
(4, 'Bomba de Infusión a Jeringa (Microinfusión)', 'UCI', 'IIB', 'Electromédico'),
(5, 'Máquina de Terapia de Reemplazo Renal Continuo (CRRT / Diálisis)', 'UCI', 'III', 'Electromédico / Hidráulico'),
(6, 'Analizador de Gases en Sangre y Electrolitos (POCT)', 'UCI', 'IIA', 'Electroquímico'),
(7, 'Electrocardiógrafo Digital de 12 Derivaciones', 'Hospitalización', 'IIA', 'Electromédico'),
(8, 'Desfibrilador Bifásico con Marcapasos Transcutáneo', 'Urgencias', 'III', 'Electromédico'),
(9, 'Ecógrafo Portátil Doppler Color', 'Imagenología', 'IIA', 'Ultrasonido'),
(10, 'Equipo de Rayos X Portátil Digital', 'Imagenología', 'IIB', 'Radiación Ionizante')
ON CONFLICT (id) DO NOTHING;

-- 4. Insertar Hojas de Vida (Equipos de UCI y No-UCI)
INSERT INTO hoja_vida_equipos (id, equipo_catalogo_id, codigo_inventario, marca, modelo, numero_serie, registro_invima, es_equipo_uci, servicio_asignado, fecha_adquisicion, created_by) VALUES
-- EQUIPOS ASIGNADOS A UCI (es_equipo_uci = TRUE)
(1, 1, 'HV-UCI-001', 'Hamilton Medical', 'C3', 'HM-998231', 'INVIMA 2021EBC-00012', TRUE, 'UCI Adultos', '2023-05-10', 1),
(2, 1, 'HV-UCI-002', 'Dräger', 'Evita V800', 'DR-884102', 'INVIMA 2022EBC-00094', TRUE, 'UCI Adultos', '2023-08-20', 1),
(3, 2, 'HV-UCI-003', 'Mindray', 'BeneVision N17', 'MY-442109', 'INVIMA 2022EBC-00045', TRUE, 'UCI Adultos', '2023-06-15', 1),
(4, 2, 'HV-UCI-004', 'Philips', 'IntelliVue MX800', 'PH-771203', 'INVIMA 2020EBC-00112', TRUE, 'UCI Adultos', '2022-11-05', 1),
(5, 3, 'HV-UCI-005', 'BBraun', 'Space P', 'BB-102938', 'INVIMA 2020EBC-00088', TRUE, 'UCI Adultos', '2022-11-20', 1),
(6, 4, 'HV-UCI-006', 'Fresenius Kabi', 'Injectomat Agilia', 'FK-332190', 'INVIMA 2021EBC-00341', TRUE, 'UCI Adultos', '2023-01-14', 1),
(7, 5, 'HV-UCI-007', 'Baxter', 'PrisMax', 'BX-665412', 'INVIMA 2023EBC-00019', TRUE, 'UCI Adultos', '2024-02-01', 1),
(8, 6, 'HV-UCI-008', 'Radiometer', 'ABL90 FLEX', 'RM-112094', 'INVIMA 2021EBC-00511', TRUE, 'UCI Adultos', '2023-09-30', 1),

-- EQUIPOS DE OTROS SERVICIOS DEL HOSPITAL (es_equipo_uci = FALSE)
(9, 7, 'HV-HOSP-009', 'GE Healthcare', 'MAC 2000', 'GE-554109', 'INVIMA 2019EBC-00077', FALSE, 'Hospitalización Piso 3', '2021-04-12', 1),
(10, 8, 'HV-URG-010', 'Zoll', 'R Series', 'ZL-883201', 'INVIMA 2019EBC-00033', FALSE, 'Urgencias', '2021-02-18', 1),
(11, 9, 'HV-IMG-011', 'Sonoscape', 'S22', 'SS-901234', 'INVIMA 2022EBC-00812', FALSE, 'Imagenología', '2023-03-25', 1),
(12, 10, 'HV-IMG-012', 'Siemens', 'Mobilett Elara Max', 'SM-441092', 'INVIMA 2020EBC-00451', FALSE, 'Imagenología', '2022-07-19', 1)
ON CONFLICT (id) DO NOTHING;

-- 5. Insertar Equipos UCI (Ubicación física y Estado)
INSERT INTO equipos_uci (id, hoja_vida_id, ubicacion_uci, estado_operativo, bateria_backup_porcentaje, created_by) VALUES
(1, 1, 'UCI-BED-01', 'En Uso', 98, 1),
(2, 3, 'UCI-BED-01', 'En Uso', 100, 1),
(3, 5, 'UCI-BED-01', 'En Uso', 85, 1),
(4, 2, 'Cama 02 - Box B', 'Disponible', 100, 1),
(5, 4, 'Cama 02 - Box B', 'Disponible', 92, 1),
(6, 6, 'Cama 03 - Box C', 'En Uso', 95, 1),
(7, 7, 'Módulo Diálisis UCI-1', 'Mantenimiento', 40, 1),
(8, 8, 'Laboratorio Satélite UCI', 'Disponible', 100, 1)
ON CONFLICT (id) DO UPDATE SET
    hoja_vida_id = EXCLUDED.hoja_vida_id,
    ubicacion_uci = EXCLUDED.ubicacion_uci,
    estado_operativo = EXCLUDED.estado_operativo,
    bateria_backup_porcentaje = EXCLUDED.bateria_backup_porcentaje;

-- 6. Insertar Paciente de Prueba
INSERT INTO pacientes (id, documento_identidad, nombre, cama_uci, created_by) VALUES
(1, '1001234567', 'Carlos Mendoza', 'UCI-BED-01', 1)
ON CONFLICT (id) DO UPDATE SET
    documento_identidad = EXCLUDED.documento_identidad,
    nombre = EXCLUDED.nombre,
    cama_uci = EXCLUDED.cama_uci,
    is_deleted = FALSE;

-- 7. Insertar Encuentro Clínico
INSERT INTO encuentros (id, paciente_id, equipo_uci_id, estado, created_by) VALUES
(1, 1, 1, 'in-progress', 2)
ON CONFLICT (id) DO UPDATE SET
    paciente_id = EXCLUDED.paciente_id,
    equipo_uci_id = EXCLUDED.equipo_uci_id,
    estado = EXCLUDED.estado,
    created_by = EXCLUDED.created_by,
    is_deleted = FALSE;

-- 8. Insertar Observación Telemétrica
INSERT INTO observaciones (id, encuentro_id, parametro, codigo_loinc, valor, unidad, alerta_predictiva, created_by) VALUES
(1, 1, 'Temperatura Turbina Ventilador', '8310-5', 41.8, 'Cel', 'Alerta: Temperatura por encima del umbral óptimo', 3)
ON CONFLICT (id) DO NOTHING;

-- 9. Sincronizar secuencias de auto-incremento para evitar colisiones en futuros inserts
SELECT setval('roles_id_seq', COALESCE((SELECT MAX(id) FROM roles), 1));
SELECT setval('usuarios_id_seq', COALESCE((SELECT MAX(id) FROM usuarios), 1));
SELECT setval('catalogo_equipos_id_seq', COALESCE((SELECT MAX(id) FROM catalogo_equipos), 1));
SELECT setval('hoja_vida_equipos_id_seq', COALESCE((SELECT MAX(id) FROM hoja_vida_equipos), 1));
SELECT setval('equipos_uci_id_seq', COALESCE((SELECT MAX(id) FROM equipos_uci), 1));
SELECT setval('pacientes_id_seq', COALESCE((SELECT MAX(id) FROM pacientes), 1));
SELECT setval('encuentros_id_seq', COALESCE((SELECT MAX(id) FROM encuentros), 1));
SELECT setval('observaciones_id_seq', COALESCE((SELECT MAX(id) FROM observaciones), 1));