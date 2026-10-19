import psycopg2

DATABASE_URL = "postgresql://neondb_owner:npg_9ETzJKWp7jeO@ep-sweet-unit-aemm14bw-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def poblar_uci_y_pacientes():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("1. Insertando 25 pacientes para las camas UCI (UCI-BED-01 hasta UCI-BED-25)...")
    pacientes_datos = [
        (1, '1001234567', 'Carlos Mendoza', 'UCI-BED-01'),
        (2, '1106529423', 'Marta Rodriguez', 'UCI-BED-02'),
        (3, '1105362529', 'Anibal Lopez', 'UCI-BED-03'),
        (4, '1005744402', 'Alexander Castro', 'UCI-BED-04'),
        (5, '1004829101', 'Valentina Rios', 'UCI-BED-05'),
        (6, '1007293845', 'Jorge Enrique Gomez', 'UCI-BED-06'),
        (7, '1003482910', 'Diana Marcela Perez', 'UCI-BED-07'),
        (8, '1009182374', 'Santiago Restrepo', 'UCI-BED-08'),
        (9, '1005829103', 'Lucia Fernandez', 'UCI-BED-09'),
        (10, '1006192834', 'Andres Felipe Morales', 'UCI-BED-10'),
        (11, '1002938475', 'Camila Andrea Torres', 'UCI-BED-11'),
        (12, '1008472910', 'Mateo Vargas', 'UCI-BED-12'),
        (13, '1001928374', 'Isabella Castillo', 'UCI-BED-13'),
        (14, '1003847291', 'Daniel Ospina', 'UCI-BED-14'),
        (15, '1009384756', 'Mariana Gutierrez', 'UCI-BED-15'),
        (16, '1004729183', 'Julian David Salazar', 'UCI-BED-16'),
        (17, '1007482910', 'Sofia Helena Ramirez', 'UCI-BED-17'),
        (18, '1002837465', 'Gabriel Eduardo Cruz', 'UCI-BED-18'),
        (19, '1008273645', 'Paula Jimena Herrera', 'UCI-BED-19'),
        (20, '1005928374', 'Nicolas Alvarado', 'UCI-BED-20'),
        (21, '1006839201', 'Daniela Mejia', 'UCI-BED-21'),
        (22, '1001827394', 'Alejandro Munoz', 'UCI-BED-22'),
        (23, '1007384920', 'Sara Valentina Duque', 'UCI-BED-23'),
        (24, '1004928371', 'Esteban Cardenas', 'UCI-BED-24'),
        (25, '1008392019', 'Laura Vanessa Ortiz', 'UCI-BED-25')
    ]

    for pid, doc, nom, cama in pacientes_datos:
        cur.execute('''
            INSERT INTO pacientes (id, documento_identidad, nombre, cama_uci, created_by)
            VALUES (%s, %s, %s, %s, 2)
            ON CONFLICT (id) DO UPDATE SET
                documento_identidad = EXCLUDED.documento_identidad,
                nombre = EXCLUDED.nombre,
                cama_uci = EXCLUDED.cama_uci,
                is_deleted = FALSE;
        ''', (pid, doc, nom, cama))

    cur.execute("SELECT setval('pacientes_id_seq', (SELECT MAX(id) FROM pacientes));")
    conn.commit()
    print("  -> 25 Pacientes registrados correctamente.")

    # 2. Dotación de equipos de cabecera para las 30 camas:
    # Ventiladores (Catálogo 1) y Monitores (Catálogo 2) para cada cama (1 a 30)
    print("2. Creando Hojas de Vida y Equipos UCI para dotar las 30 camas con Ventilador y Monitor...")
    
    # Asegurar que las secuencias estén sincronizadas
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM hoja_vida_equipos;")
    max_hv = cur.fetchone()[0]

    # Modelos y marcas de referencia
    marcas_vent = [('Hamilton Medical', 'C3'), ('Dräger', 'Evita V800'), ('Mindray', 'SV800'), ('Medtronic', 'Puritan Bennett 980')]
    marcas_mon = [('Philips', 'IntelliVue MX800'), ('Mindray', 'BeneVision N17'), ('GE Healthcare', 'Carescape B850'), ('Nihon Kohden', 'Life Scope G9')]

    for i in range(1, 31):
        cama_tag = f"UCI-BED-{i:02d}"
        
        # Ventilador para la cama i
        cod_vent = f"VENT-{i:02d}"
        m_vent, mod_vent = marcas_vent[(i - 1) % len(marcas_vent)]
        
        # Verificar si ya existe hoja de vida con este código
        cur.execute("SELECT id FROM hoja_vida_equipos WHERE codigo_inventario = %s;", (cod_vent,))
        row_vent = cur.fetchone()
        if not row_vent:
            cur.execute('''
                INSERT INTO hoja_vida_equipos (equipo_catalogo_id, codigo_inventario, marca, modelo, numero_serie, registro_invima, es_equipo_uci, servicio_asignado, fecha_adquisicion, created_by)
                VALUES (1, %s, %s, %s, %s, 'INVIMA 2022EBC-00094', TRUE, 'UCI Adultos', '2023-01-15', 1)
                RETURNING id;
            ''', (cod_vent, m_vent, mod_vent, f"SN-VNT-{i:04d}"))
            hv_vent_id = cur.fetchone()[0]
        else:
            hv_vent_id = row_vent[0]

        # Equipo UCI para el ventilador
        cur.execute("SELECT id FROM equipos_uci WHERE hoja_vida_id = %s;", (hv_vent_id,))
        row_eq_vent = cur.fetchone()
        estado_vent = "En Uso" if i <= 25 else "Disponible"
        if not row_eq_vent:
            cur.execute('''
                INSERT INTO equipos_uci (hoja_vida_id, ubicacion_uci, estado_operativo, bateria_backup_porcentaje, created_by)
                VALUES (%s, %s, %s, 98, 1)
                RETURNING id;
            ''', (hv_vent_id, cama_tag, estado_vent))
            eq_vent_id = cur.fetchone()[0]
        else:
            eq_vent_id = row_eq_vent[0]
            cur.execute("UPDATE equipos_uci SET ubicacion_uci = %s, estado_operativo = %s WHERE id = %s;", (cama_tag, estado_vent, eq_vent_id))

        # Monitor para la cama i
        cod_mon = f"MON-{i:02d}"
        m_mon, mod_mon = marcas_mon[(i - 1) % len(marcas_mon)]
        cur.execute("SELECT id FROM hoja_vida_equipos WHERE codigo_inventario = %s;", (cod_mon,))
        row_mon = cur.fetchone()
        if not row_mon:
            cur.execute('''
                INSERT INTO hoja_vida_equipos (equipo_catalogo_id, codigo_inventario, marca, modelo, numero_serie, registro_invima, es_equipo_uci, servicio_asignado, fecha_adquisicion, created_by)
                VALUES (2, %s, %s, %s, %s, 'INVIMA 2020EBC-00112', TRUE, 'UCI Adultos', '2023-02-10', 1)
                RETURNING id;
            ''', (cod_mon, m_mon, mod_mon, f"SN-MON-{i:04d}"))
            hv_mon_id = cur.fetchone()[0]
        else:
            hv_mon_id = row_mon[0]

        # Equipo UCI para el monitor
        cur.execute("SELECT id FROM equipos_uci WHERE hoja_vida_id = %s;", (hv_mon_id,))
        row_eq_mon = cur.fetchone()
        estado_mon = "En Uso" if i <= 25 else "Disponible"
        if not row_eq_mon:
            cur.execute('''
                INSERT INTO equipos_uci (hoja_vida_id, ubicacion_uci, estado_operativo, bateria_backup_porcentaje, created_by)
                VALUES (%s, %s, %s, 95, 1)
                RETURNING id;
            ''', (hv_mon_id, cama_tag, estado_mon))
            eq_mon_id = cur.fetchone()[0]
        else:
            eq_mon_id = row_eq_mon[0]
            cur.execute("UPDATE equipos_uci SET ubicacion_uci = %s, estado_operativo = %s WHERE id = %s;", (cama_tag, estado_mon, eq_mon_id))

        # 3. Para los pacientes asignados (camas 1 a 25), crear encuentros activos con el ventilador y el monitor
        if i <= 25:
            # Encuentro para el Ventilador
            cur.execute('''
                SELECT id FROM encuentros 
                WHERE paciente_id = %s AND equipo_uci_id = %s AND estado = 'in-progress' AND NOT is_deleted;
            ''', (i, eq_vent_id))
            if not cur.fetchone():
                cur.execute('''
                    INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by)
                    VALUES (%s, %s, 'in-progress', 2);
                ''', (i, eq_vent_id))

            # Encuentro para el Monitor
            cur.execute('''
                SELECT id FROM encuentros 
                WHERE paciente_id = %s AND equipo_uci_id = %s AND estado = 'in-progress' AND NOT is_deleted;
            ''', (i, eq_mon_id))
            if not cur.fetchone():
                cur.execute('''
                    INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by)
                    VALUES (%s, %s, 'in-progress', 2);
                ''', (i, eq_mon_id))

    # Sincronizar secuencias
    cur.execute("SELECT setval('hoja_vida_equipos_id_seq', (SELECT MAX(id) FROM hoja_vida_equipos));")
    cur.execute("SELECT setval('equipos_uci_id_seq', (SELECT MAX(id) FROM equipos_uci));")
    cur.execute("SELECT setval('encuentros_id_seq', (SELECT MAX(id) FROM encuentros));")

    conn.commit()
    print("  -> Ventiladores, Monitores y Encuentros dobles para 25 pacientes creados exitosamente.")

    # Conteo final
    cur.execute("SELECT count(*) FROM pacientes WHERE NOT is_deleted;")
    print(f"Total Pacientes: {cur.fetchone()[0]}")
    cur.execute("SELECT count(*) FROM equipos_uci WHERE NOT is_deleted;")
    print(f"Total Equipos UCI: {cur.fetchone()[0]}")
    cur.execute("SELECT count(*) FROM encuentros WHERE estado = 'in-progress' AND NOT is_deleted;")
    print(f"Total Encuentros in-progress: {cur.fetchone()[0]}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    poblar_uci_y_pacientes()
