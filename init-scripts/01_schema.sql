-- Tabla de Roles
CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL -- 'Admin', 'Biomedico', 'Servicio'
);

-- Tabla de Usuarios
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    rol_id INT REFERENCES roles(id),
    is_deleted BOOLEAN DEFAULT FALSE
);

-- Tabla de Pacientes / Equipos
CREATE TABLE IF NOT EXISTS pacientes (
    id SERIAL PRIMARY KEY,
    documento_identidad VARCHAR(50) UNIQUE NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    cama_uci VARCHAR(20) NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id)
);

-- Tabla de Encuentros Cĺínicos / Monitoreo en UCI
CREATE TABLE IF NOT EXISTS encuentros (
    id SERIAL PRIMARY KEY,
    paciente_id INT REFERENCES pacientes(id),
    fecha_inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    estado VARCHAR(50) DEFAULT 'in-progress',
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id)
);

-- Tabla de Observaciones Telemétricas (Control Predictivo)
CREATE TABLE IF NOT EXISTS observaciones (
    id SERIAL PRIMARY KEY,
    encuentro_id INT REFERENCES encuentros(id),
    parametro VARCHAR(100) NOT NULL, -- ej: "Temperatura Turbina"
    codigo_loinc VARCHAR(50) NOT NULL, -- ej: "8310-5"
    valor NUMERIC NOT NULL,
    unidad VARCHAR(20) NOT NULL,
    alerta_predictiva TEXT,
    version INT DEFAULT 1,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_by INT REFERENCES usuarios(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Registros de Auditoría
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    usuario_id INT REFERENCES usuarios(id),
    accion VARCHAR(50) NOT NULL, -- 'SOFT_DELETE', 'SOFT_EDIT', 'RESTORE'
    tabla_afectada VARCHAR(50) NOT NULL,
    registro_id INT NOT NULL,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);