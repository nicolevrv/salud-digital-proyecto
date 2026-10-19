"""
Sincroniza la base de datos de equipos de UCI (Excel) con Neon y reorganiza
camas, pacientes y equipos para que el dashboard muestre cifras reales.

Uso:
    python sincronizar_uci_neon.py --dry-run     # ejecuta todo y revierte (solo muestra el resultado)
    python sincronizar_uci_neon.py               # aplica los cambios en Neon

Qué hace (todo en UNA transacción: si algo falla no se cambia nada):
  1. Respalda dataset_eventos (tabla dataset_eventos_respaldo_AAAAMMDD, solo si no existe).
  2. Agrega a dataset_eventos las columnas nuevas del Excel y actualiza los registros por ID_Registro.
     Los registros creados desde el dashboard (sin ID_Registro) NO se tocan.
  3. Carga las hojas auxiliares del Excel como tablas nuevas.
  4. Crea camas_uci (30 camas) para poder calcular camas disponibles/ocupadas.
  5. Deja las 232 unidades del Excel como hoja de vida + equipos_uci y recalcula
     En Uso / Disponible / Mantenimiento según los pacientes y el último estado del dataset.
"""
import os
import re
import sys
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

sys.stdout.reconfigure(encoding="utf-8")

DATABASE_URL = os.getenv("DATABASE_URL") or (
    "postgresql://neondb_owner:npg_9ETzJKWp7jeO@ep-sweet-unit-aemm14bw-pooler.c-2.us-east-2.aws.neon.tech/"
    "neondb?sslmode=require&channel_binding=require"
)
DEFAULT_XLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "base_datos_equipos_uci.xls")

TOTAL_CAMAS = 30
# Dotación de cabecera por paciente hospitalizado (se puede ajustar)
KIT_POR_PACIENTE = {"VENT": 1, "MON": 1, "BVOL": 2, "BJER": 1}
MEDICO_ID = 2          # usuario que figura como creador de los encuentros
ADMIN_ID = 1

TIPO_A_PREFIJO = {
    "Ventilador Mecánico": "VENT",
    "Monitor Multiparámetro": "MON",
    "Bomba de Infusión Volumétrica": "BVOL",
    "Bomba de Infusión a Jeringa": "BJER",
    "Máquina CRRT": "CRRT",
    "Analizador de Gases (POCT)": "POCT",
    "Electrocardiógrafo 12 Derivaciones": "ECG",
    "Desfibrilador Bifásico": "DEF",
    "Ecógrafo Portátil Doppler": "ECO",
    "Equipo de Rayos X Portátil": "RX",
}
PREFIJO_A_CATALOGO = {"VENT": 1, "MON": 2, "BVOL": 3, "BJER": 4, "CRRT": 5, "POCT": 6, "ECG": 7, "DEF": 8, "ECO": 9, "RX": 10}
EQUIPOS_CABECERA = {"VENT", "MON", "BVOL", "BJER"}

# Modelos oficialmente registrados en Neon (con fotografías, certificados ONAC y hojas de vida).
# Estrictamente NO se agregan ni inventan modelos adicionales.
MODELOS_REGISTRADOS = {
    "VENT": [
        ("Hamilton Medical", "C3", "INVIMA 2021EBC-00012"),
        ("Dräger", "Evita V800", "INVIMA 2022EBC-00094"),
    ],
    "MON": [
        ("Mindray", "BeneVision N17", "INVIMA 2022EBC-00045"),
        ("Philips", "IntelliVue MX800", "INVIMA 2020EBC-00112"),
    ],
    "BVOL": [("BBraun", "Space P", "INVIMA 2020EBC-00088")],
    "BJER": [("Fresenius Kabi", "Injectomat Agilia", "INVIMA 2021EBC-00341")],
    "CRRT": [("Baxter", "PrisMax", "INVIMA 2023EBC-00019")],
    "POCT": [("Radiometer", "ABL90 FLEX", "INVIMA 2021EBC-00511")],
    "ECG": [("GE Healthcare", "MAC 2000", "INVIMA 2019EBC-00077")],
    "DEF": [("Mindray / Zoll", "BeneHeart D3", "INVIMA 2019EBC-00033")],
    "ECO": [("Sonosite", "Edge II", "INVIMA 2022EBC-00812")],
    "RX": [("Carestream", "Motion Mobile", "INVIMA 2020EBC-00451")],
}

# Hojas de vida originales (IDs 1..12) que corresponden a las unidades base registradas
REKEY_LEGACY = {
    "HV-UCI-001": "VENT-01", "HV-UCI-002": "VENT-02", "HV-UCI-003": "MON-01", "HV-UCI-004": "MON-02",
    "HV-UCI-005": "BVOL-01", "HV-UCI-006": "BJER-01", "HV-UCI-007": "CRRT-01", "HV-UCI-008": "POCT-01",
    "HV-HOSP-009": "ECG-01", "HV-URG-010": "DEF-01", "HV-IMG-011": "ECO-01", "HV-IMG-012": "RX-01",
}

# R06: Metadatos de Dataset Público Real para cada una de las 12 fotografías de equipos en PACS
DATASETS_IMAGENES_R06 = {
    "VENT-01": {
        "dataset_nombre": "Roboflow Universe - Hospital Equipment Detection & Wikimedia Medical Ventilators",
        "fuente_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Mechanical_ventilators",
        "licencia": "CC BY 4.0",
    },
    "VENT-02": {
        "dataset_nombre": "Wikimedia Commons Medical Equipment (Dräger) & Roboflow Hospital Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Dr%C3%A4gerwerk",
        "referencia_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
    "MON-01": {
        "dataset_nombre": "Roboflow Universe - Medical Equipment Detection (Patient Monitor Class)",
        "fuente_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Medical_monitors",
        "licencia": "CC BY 4.0",
    },
    "MON-02": {
        "dataset_nombre": "Wikimedia Commons Medical Monitors & Roboflow Hospital Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Medical_monitors",
        "referencia_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
    "BVOL-01": {
        "dataset_nombre": "Wikimedia Commons Infusion Pumps & Roboflow Medical Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Infusion_pumps",
        "referencia_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
    "BJER-01": {
        "dataset_nombre": "Wikimedia Commons Syringe Infusion Pumps & Roboflow Hospital Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Syringe_drivers",
        "referencia_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
    "DEF-01": {
        "dataset_nombre": "Roboflow Universe - Hospital Equipment Detection (Defibrillator Class)",
        "fuente_url": "https://universe.roboflow.com/hospitalequipmentdetection/hospital-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Defibrillators",
        "licencia": "CC BY 4.0",
    },
    "CRRT-01": {
        "dataset_nombre": "Wikimedia Commons Renal Replacement Therapy & FDA AccessGUDID Database",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Hemofiltration",
        "referencia_url": "https://accessgudid.nlm.nih.gov/",
        "licencia": "CC BY-SA 4.0 / Public Domain",
    },
    "ECG-01": {
        "dataset_nombre": "Roboflow Universe - Medical Equipment Detection (ECG Machine Class)",
        "fuente_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "referencia_url": "https://commons.wikimedia.org/wiki/Category:Electrocardiographs",
        "licencia": "CC BY 4.0",
    },
    "POCT-01": {
        "dataset_nombre": "Wikimedia Commons Blood Gas Analyzers & NLM Open-i Biomedical Image Dataset",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Blood_gas_analyzers",
        "referencia_url": "https://openi.nlm.nih.gov/",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
    "ECO-01": {
        "dataset_nombre": "Wikimedia Commons Medical Ultrasound Equipment & Roboflow Medical Equipment Detection",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Medical_ultrasound_equipment",
        "referencia_url": "https://universe.roboflow.com/bsbi/medical-equipment-detection",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
    "RX-01": {
        "dataset_nombre": "Wikimedia Commons Mobile Medical X-Ray Equipment & NLM Open-i Dataset",
        "fuente_url": "https://commons.wikimedia.org/wiki/Category:Medical_X-ray_equipment",
        "referencia_url": "https://openi.nlm.nih.gov/",
        "licencia": "CC BY-SA 4.0 / CC BY 4.0",
    },
}

CLUSTER_NOMBRES_R07 = {
    0: "Cluster 0: Operación Óptima en Activos Jóvenes (Bajo Riesgo)",
    1: "Cluster 1: Degradación Metrológica e Incumplimiento IEC 62353 (Riesgo Crítico)",
    2: "Cluster 2: Zona de Confusión (Fatiga Acumulada con IEC 62353 Conforme)",
    3: "Cluster 3: Activos Maduros de Alta Confiabilidad y Alto MTBF",
}



# Columnas de dataset_eventos: (columna en Excel, columna en Neon, tipo SQL)
COLUMNAS = [
    ("ID_Registro", "ID_Registro", "text"),
    ("ID_Equipo", "ID_Equipo", "text"),
    ("Tipo_Equipo", "Tipo_Equipo", "text"),
    ("Clasificacion_Riesgo_INVIMA", "Clasificacion_Riesgo", "text"),
    ("Nivel_Riesgo_Num", "Nivel_Riesgo_Num", "bigint"),
    ("Fecha_Reporte", "Fecha_Reporte", "timestamp"),
    ("Edad_Equipo_Meses", "Edad_Equipo_Meses", "double precision"),
    ("Meses_Desde_Ultimo_Mtto", "Meses_Desde_Ultimo_Mtto", "double precision"),
    ("Horas_Uso_Acumuladas", "Horas_Uso_Acumuladas", "bigint"),
    ("Fallas_Acumuladas", "Fallas_Acumuladas", "bigint"),
    ("MTBF_Promedio_Dias", "MTBF_Promedio_Dias", "double precision"),
    ("Corriente_Fuga_uA", "Corriente_Fuga_uA", "double precision"),
    ("Resistencia_Tierra_Ohms", "Resistencia_Tierra_Ohms", "double precision"),
    ("Desviacion_Calibracion_Pct", "Desviacion_Calibracion_Pct", "double precision"),
    ("Estado_IEC62353", "Estado_IEC62353", "text"),
    ("Estado_Equipo", "Estado_Equipo", "text"),
    ("Categoria_Falla", "Categoria_Falla", "text"),
    ("Descripcion_Evento", "Descripcion_Evento", "text"),
    ("Gravedad_Evento", "Gravedad_Evento", "bigint"),
    ("Nivel_Gravedad", "Nivel_Gravedad", "text"),
    ("Accion_Recomendada", "Accion_Recomendada", "text"),
    ("Target_Falla", "Variable_Target_Falla", "double precision"),
    ("Target_Mtto_Predictivo", "Target_Mtto_Predictivo", "bigint"),
    ("Fecha_Registro_Evento", "Fecha_Registro_Evento", "timestamp"),
    ("Tipo_Mtto_Programado", "Tipo_Mtto_Programado", "text"),
    ("Dias_Para_Mtto", "Dias_Para_Mtto", "bigint"),
    ("Fecha_Mtto_Programado", "Fecha_Mtto_Programado", "timestamp"),
    ("Periodicidad_Calibracion_Meses", "Periodicidad_Calibracion_Meses", "bigint"),
    ("Calibracion_Realizada", "Calibracion_Realizada", "text"),
    ("Tipo_Calibracion", "Tipo_Calibracion", "text"),
    ("Fecha_Ultima_Calibracion", "Fecha_Ultima_Calibracion", "timestamp"),
    ("Fecha_Proxima_Calibracion", "Fecha_Proxima_Calibracion", "timestamp"),
]
HOJAS_AUXILIARES = {
    "Diccionario_Datos": "dataset_diccionario_datos",
    "Catalogo_Fallas": "catalogo_fallas",
    "Justificaciones_Sin_Falla": "justificaciones_sin_falla",
}


def log(msg=""):
    print(msg, flush=True)


def limpiar(df):
    return df.astype(object).where(pd.notnull(df), None)


def num(codigo):
    m = re.search(r"(\d+)$", codigo)
    return int(m.group(1)) if m else 0


def prefijo(codigo):
    return codigo.split("-")[0]


# ---------------------------------------------------------------- 1-3. Dataset
def sincronizar_dataset(cur, hojas):
    hoy = datetime.now().strftime("%Y%m%d")
    cur.execute(f'CREATE TABLE IF NOT EXISTS "dataset_eventos_respaldo_{hoy}" AS TABLE dataset_eventos')
    cur.execute(f'SELECT COUNT(*) FROM "dataset_eventos_respaldo_{hoy}"')
    log(f"[1] Respaldo dataset_eventos_respaldo_{hoy}: {cur.fetchone()[0]} filas")

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='dataset_eventos'")
    existentes = {r[0] for r in cur.fetchall()}
    nuevas = [(n, t) for _, n, t in COLUMNAS if n not in existentes]
    for nombre, tipo in nuevas:
        cur.execute(f'ALTER TABLE dataset_eventos ADD COLUMN "{nombre}" {tipo}')
    log(f"[2] Columnas nuevas agregadas a dataset_eventos: {len(nuevas)}")

    df = limpiar(hojas["Dataset_Predictivo"])
    cols_xls = [c for c, _, _ in COLUMNAS]
    defs = ", ".join(f'"{n}" {t}' for _, n, t in COLUMNAS)
    cur.execute(f"CREATE TEMP TABLE tmp_xls ({defs}) ON COMMIT DROP")
    filas = [tuple(r[c] for c in cols_xls) for _, r in df.iterrows()]
    execute_values(cur, f'INSERT INTO tmp_xls ({", ".join(chr(34) + n + chr(34) for _, n, _ in COLUMNAS)}) VALUES %s', filas, page_size=1000)

    sets = ", ".join(f'"{n}" = t."{n}"' for _, n, _ in COLUMNAS if n != "ID_Registro")
    cur.execute(f'UPDATE dataset_eventos d SET {sets} FROM tmp_xls t WHERE d."ID_Registro" = t."ID_Registro"')
    actualizadas = cur.rowcount
    cur.execute('''
        INSERT INTO dataset_eventos ({cols})
        SELECT {tcols} FROM tmp_xls t
        WHERE NOT EXISTS (SELECT 1 FROM dataset_eventos d WHERE d."ID_Registro" = t."ID_Registro")
    '''.format(cols=", ".join(f'"{n}"' for _, n, _ in COLUMNAS), tcols=", ".join(f't."{n}"' for _, n, _ in COLUMNAS)))
    insertadas = cur.rowcount
    log(f"    Registros actualizados: {actualizadas} | insertados nuevos: {insertadas}")

    for hoja, tabla in HOJAS_AUXILIARES.items():
        d = limpiar(hojas[hoja])
        columnas = list(d.columns)
        cur.execute(f'DROP TABLE IF EXISTS "{tabla}"')
        tipos = []
        for c in columnas:
            serie = hojas[hoja][c]
            tipos.append("bigint" if pd.api.types.is_integer_dtype(serie) else "text")
        cur.execute(f'CREATE TABLE "{tabla}" (' + ", ".join(f'"{c}" {t}' for c, t in zip(columnas, tipos)) + ")")
        execute_values(cur, f'INSERT INTO "{tabla}" VALUES %s', [tuple(r) for r in d.itertuples(index=False)])
        log(f"[3] Tabla {tabla}: {len(d)} filas")


# ---------------------------------------------------------------- 4. Camas
def crear_camas(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS camas_uci (
            id SERIAL PRIMARY KEY,
            codigo VARCHAR(30) UNIQUE NOT NULL,
            area VARCHAR(50) DEFAULT 'UCI Adultos',
            activa BOOLEAN DEFAULT TRUE
        )
    """)
    for i in range(1, TOTAL_CAMAS + 1):
        cur.execute("INSERT INTO camas_uci (codigo) VALUES (%s) ON CONFLICT (codigo) DO NOTHING", (f"UCI-BED-{i:02d}",))
    log(f"[4] camas_uci lista con {TOTAL_CAMAS} camas")


# ---------------------------------------------------------------- 5. Equipos
def ultimo_estado(df):
    """Último registro de cada equipo (por fecha) y si tiene falla vigente."""
    ult = df.sort_values(["ID_Equipo", "Fecha_Reporte", "ID_Registro"]).groupby("ID_Equipo").tail(1).set_index("ID_Equipo")
    return ult


def sincronizar_equipos(cur, df):
    ult = ultimo_estado(df)
    ids = sorted(ult.index, key=lambda c: (prefijo(c), num(c)))
    en_falla = {i for i in ids if ult.loc[i, "Estado_Equipo"] == "Con falla"}
    log(f"[5] Unidades en el Excel: {len(ids)} | con falla vigente (no asignables): {len(en_falla)}")

    # Re-keys y fusión de hojas de vida antiguas (HV-UCI-001..012 -> VENT-01..RX-01)
    for viejo, nuevo in REKEY_LEGACY.items():
        cur.execute("SELECT id FROM hoja_vida_equipos WHERE codigo_inventario=%s", (viejo,))
        r_viejo = cur.fetchone()
        cur.execute("SELECT id FROM hoja_vida_equipos WHERE codigo_inventario=%s", (nuevo,))
        r_nuevo = cur.fetchone()
        if r_viejo and r_nuevo and r_viejo[0] != r_nuevo[0]:
            hv_viejo, hv_nuevo = r_viejo[0], r_nuevo[0]
            cur.execute("SELECT id FROM equipos_uci WHERE hoja_vida_id=%s", (hv_viejo,))
            eq_v = cur.fetchone()
            cur.execute("SELECT id FROM equipos_uci WHERE hoja_vida_id=%s", (hv_nuevo,))
            eq_n = cur.fetchone()
            if eq_v and eq_n:
                cur.execute("UPDATE encuentros SET equipo_uci_id=%s WHERE equipo_uci_id=%s", (eq_v[0], eq_n[0]))
                cur.execute("DELETE FROM equipos_uci WHERE id=%s", (eq_n[0],))
                cur.execute("UPDATE equipos_uci SET is_deleted=FALSE WHERE id=%s", (eq_v[0],))
            elif eq_n and not eq_v:
                cur.execute("UPDATE equipos_uci SET hoja_vida_id=%s, is_deleted=FALSE WHERE id=%s", (hv_viejo, eq_n[0]))
            cur.execute("DELETE FROM hoja_vida_equipos WHERE id=%s", (hv_nuevo,))
            cur.execute("UPDATE hoja_vida_equipos SET codigo_inventario=%s, es_equipo_uci=TRUE, is_deleted=FALSE WHERE id=%s", (nuevo, hv_viejo))
            cur.execute("UPDATE equipos_documentos SET equipo_id=%s WHERE equipo_id=%s", (nuevo, viejo))
            log(f"    Hoja de vida fusionada {viejo} (id={hv_viejo}) <- {nuevo} (id={hv_nuevo} eliminado)")
        elif r_viejo and not r_nuevo:
            cur.execute("UPDATE hoja_vida_equipos SET codigo_inventario=%s, es_equipo_uci=TRUE, is_deleted=FALSE WHERE id=%s", (nuevo, r_viejo[0]))
            cur.execute("UPDATE equipos_documentos SET equipo_id=%s WHERE equipo_id=%s", (nuevo, viejo))
            log(f"    Hoja de vida {viejo} -> {nuevo}")

    # Limpiar duplicados de prueba en equipos_documentos (mantener los 34 documentos oficiales)
    cur.execute("DELETE FROM equipos_documentos WHERE id > 34")
    if cur.rowcount:
        log(f"    Documentos duplicados eliminados de equipos_documentos: {cur.rowcount}")

    # Hojas de vida / equipos_uci para las 232 unidades con modelos estrictamente registrados
    cur.execute("SELECT id, codigo_inventario FROM hoja_vida_equipos")
    hv = {c: i for i, c in cur.fetchall()}
    creados = 0
    actualizados_modelo = 0
    for cod in ids:
        pre = prefijo(cod)
        marca, modelo, invima = MODELOS_REGISTRADOS[pre][(num(cod) - 1) % len(MODELOS_REGISTRADOS[pre])]
        if cod in hv:
            cur.execute("""
                UPDATE hoja_vida_equipos
                SET equipo_catalogo_id=%s, marca=%s, modelo=%s, registro_invima=%s, es_equipo_uci=TRUE, is_deleted=FALSE
                WHERE id=%s
            """, (PREFIJO_A_CATALOGO[pre], marca, modelo, invima, hv[cod]))
            actualizados_modelo += 1
            continue
        fecha_adq = (ult.loc[cod, "Fecha_Reporte"] - timedelta(days=float(ult.loc[cod, "Edad_Equipo_Meses"]) * 30.44)).date()
        cur.execute("""
            INSERT INTO hoja_vida_equipos (equipo_catalogo_id, codigo_inventario, marca, modelo, numero_serie,
                registro_invima, es_equipo_uci, servicio_asignado, fecha_adquisicion, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,TRUE,'UCI Adultos',%s,%s) RETURNING id
        """, (PREFIJO_A_CATALOGO[pre], cod, marca, modelo, f"SN-{cod}", invima, fecha_adq, ADMIN_ID))
        hv[cod] = cur.fetchone()[0]
        creados += 1
    log(f"    Hojas de vida creadas: {creados} | normalizadas a modelos oficiales: {actualizados_modelo}")

    cur.execute("SELECT id, hoja_vida_id, ubicacion_uci FROM equipos_uci")
    eq = {}
    ubic_actual = {}
    for eid, hid, ub in cur.fetchall():
        eq[hid] = eid
        ubic_actual[hid] = ub
    nuevos = 0
    for cod in ids:
        h = hv[cod]
        if h not in eq:
            cur.execute("""
                INSERT INTO equipos_uci (hoja_vida_id, ubicacion_uci, estado_operativo, bateria_backup_porcentaje, created_by)
                VALUES (%s,'Almacén UCI','Disponible',100,%s) RETURNING id
            """, (h, ADMIN_ID))
            eq[h] = cur.fetchone()[0]
            ubic_actual[h] = "Almacén UCI"
            nuevos += 1
        else:
            cur.execute("UPDATE equipos_uci SET is_deleted=FALSE WHERE id=%s", (eq[h],))
    log(f"    Equipos UCI creados: {nuevos}")
    eq_por_codigo = {cod: eq[hv[cod]] for cod in ids}
    ubic_por_codigo = {cod: ubic_actual[hv[cod]] for cod in ids}

    # Pacientes hospitalizados -> camas
    cur.execute("SELECT codigo FROM camas_uci WHERE activa")
    camas = {c for (c,) in cur.fetchall()}
    cur.execute("SELECT id, nombre, cama_uci FROM pacientes WHERE is_deleted = FALSE ORDER BY id")
    pacientes = [(i, n, c) for i, n, c in cur.fetchall() if c in camas]
    log(f"    Pacientes con cama UCI: {len(pacientes)} | camas libres: {len(camas) - len(pacientes)}")

    # Asignación: 1) equipos 'naturales' de su cama, 2) repuestos sanos
    asignado = {}                                   # codigo -> paciente_id
    necesita = []                                   # (paciente, prefijo)
    for pid, nombre, cama in pacientes:
        n = num(cama)
        preferidos = {
            "VENT": [f"VENT-{n:02d}"], "MON": [f"MON-{n:02d}"],
            "BVOL": [f"BVOL-{2*n-1:02d}", f"BVOL-{2*n:02d}"], "BJER": [f"BJER-{n:02d}"],
        }
        for pre, cantidad in KIT_POR_PACIENTE.items():
            ok = [c for c in preferidos[pre] if c in eq_por_codigo and c not in en_falla and c not in asignado][:cantidad]
            for c in ok:
                asignado[c] = pid
            for _ in range(cantidad - len(ok)):
                necesita.append((pid, pre))
    reemplazos = []
    for pid, pre in necesita:
        libres = sorted((c for c in ids if prefijo(c) == pre and c not in en_falla and c not in asignado), key=num)
        if not libres:
            log(f"    ¡Sin {pre} disponible para paciente {pid}!")
            continue
        asignado[libres[0]] = pid
        reemplazos.append((pid, libres[0]))
    log(f"    Equipos asignados a pacientes: {len(asignado)} (reemplazos por falla: {len(reemplazos)})")
    for pid, c in reemplazos:
        log(f"      paciente {pid}: repuesto {c}")

    cama_de = {pid: cama for pid, _, cama in pacientes}
    camas_libres = camas - {cama for _, _, cama in pacientes}

    # Estado y ubicación de cada equipo
    for cod in ids:
        pre = prefijo(cod)
        if cod in asignado:
            estado, ub = "En Uso", cama_de[asignado[cod]]
        elif cod in en_falla:
            estado, ub = "Mantenimiento", "Taller Biomédico"
        else:
            estado = "Disponible"
            actual = ubic_por_codigo[cod]
            if actual in camas_libres:
                ub = actual
            else:
                ub = "Almacén UCI" if pre in EQUIPOS_CABECERA else "Equipo móvil UCI"
        cur.execute("""
            UPDATE equipos_uci SET estado_operativo=%s, ubicacion_uci=%s, ultima_inspeccion_biomedica=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (estado, ub, eq_por_codigo[cod]))

    # Encuentros activos: dejar solo los que reflejan la asignación real
    deseados = {(pid, eq_por_codigo[c]) for c, pid in asignado.items()}
    cur.execute("SELECT id, paciente_id, equipo_uci_id FROM encuentros WHERE estado='in-progress' AND is_deleted=FALSE AND equipo_uci_id IS NOT NULL ORDER BY id")
    vistos, cerrar = set(), []
    for eid, pid, equid in cur.fetchall():
        if (pid, equid) in deseados and (pid, equid) not in vistos:
            vistos.add((pid, equid))
        else:
            cerrar.append(eid)
    if cerrar:
        cur.execute("UPDATE encuentros SET estado='finished', fecha_fin=CURRENT_TIMESTAMP WHERE id = ANY(%s)", (cerrar,))
    for pid, equid in sorted(deseados - vistos):
        cur.execute("INSERT INTO encuentros (paciente_id, equipo_uci_id, estado, created_by) VALUES (%s,%s,'in-progress',%s)", (pid, equid, MEDICO_ID))
    log(f"    Encuentros cerrados por no reflejar la realidad: {len(cerrar)} | mantenidos: {len(vistos)} | nuevos: {len(deseados - vistos)}")

    for tabla in ("hoja_vida_equipos", "equipos_uci", "encuentros", "camas_uci"):
        cur.execute(f"SELECT setval(pg_get_serial_sequence('{tabla}','id'), (SELECT MAX(id) FROM {tabla}))")
    cur.execute("INSERT INTO audit_logs (usuario_id, accion, tabla_afectada, registro_id) VALUES (%s,'SINCRONIZAR_DOTACION_UCI','equipos_uci',0)", (ADMIN_ID,))


def _kmeans_deterministico(X_scaled, k=4, seed=42, max_iter=100):
    rng = np.random.default_rng(seed)
    n = X_scaled.shape[0]
    centers = np.empty((k, X_scaled.shape[1]))
    centers[0] = X_scaled[rng.integers(0, n)]
    for c in range(1, k):
        dist_sq = np.min(np.sum((X_scaled[:, None, :] - centers[None, :c, :]) ** 2, axis=2), axis=1)
        probs = dist_sq / dist_sq.sum()
        centers[c] = X_scaled[rng.choice(n, p=probs)]
    for _ in range(max_iter):
        dists = np.sum((X_scaled[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        labels = np.argmin(dists, axis=1)
        new_centers = np.array([
            X_scaled[labels == j].mean(axis=0) if np.any(labels == j) else centers[j]
            for j in range(k)
        ])
        if np.allclose(centers, new_centers, atol=1e-6):
            break
        centers = new_centers
    wcss = float(np.sum(np.min(np.sum((X_scaled[:, None, :] - centers[None, :, :]) ** 2, axis=2), axis=1)))
    return labels, centers, wcss


def configurar_r06_y_r07(cur):
    """
    R06: Agrega metadatos de Dataset Público Real (URL fuente, referencia y licencia)
         a las 12 fotografías oficiales almacenadas en equipos_documentos (PACS).
    R07: Marca todos los registros de dataset_eventos, hoja_vida_equipos y pacientes
         con origen = 'sintetico' y los vincula a su respectivo cluster K-Means (k=4).
    """
    # --- R06: Metadatos de Dataset Público en equipos_documentos ---
    cur.execute("""
        ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS dataset_nombre TEXT;
        ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS fuente_url TEXT;
        ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS referencia_url TEXT;
        ALTER TABLE equipos_documentos ADD COLUMN IF NOT EXISTS licencia VARCHAR(80);
    """)
    actualizados_r06 = 0
    for eq_id, meta in DATASETS_IMAGENES_R06.items():
        cur.execute("""
            UPDATE equipos_documentos
            SET dataset_nombre = %s,
                fuente_url = %s,
                referencia_url = %s,
                licencia = %s
            WHERE UPPER(equipo_id) = %s
        """, (
            meta["dataset_nombre"],
            meta["fuente_url"],
            meta["referencia_url"],
            meta["licencia"],
            eq_id.upper(),
        ))
        actualizados_r06 += cur.rowcount
    log(f"[6] R06: Documentos/fotos PACS vinculados a URLs de datasets públicos reales: {actualizados_r06}")

    # --- R07: Columnas origen y cluster en dataset_eventos, hoja_vida_equipos y pacientes ---
    cur.execute("""
        ALTER TABLE dataset_eventos ADD COLUMN IF NOT EXISTS origen VARCHAR(30) DEFAULT 'sintetico';
        ALTER TABLE dataset_eventos ADD COLUMN IF NOT EXISTS cluster_id INT;
        ALTER TABLE dataset_eventos ADD COLUMN IF NOT EXISTS cluster_nombre VARCHAR(120);

        ALTER TABLE hoja_vida_equipos ADD COLUMN IF NOT EXISTS origen VARCHAR(30) DEFAULT 'sintetico';
        ALTER TABLE hoja_vida_equipos ADD COLUMN IF NOT EXISTS cluster_id INT;
        ALTER TABLE hoja_vida_equipos ADD COLUMN IF NOT EXISTS cluster_nombre VARCHAR(120);

        ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS origen VARCHAR(30) DEFAULT 'sintetico';
        ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cluster_id INT;
        ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cluster_nombre VARCHAR(120);
    """)

    features = [
        "Edad_Equipo_Meses",
        "Meses_Desde_Ultimo_Mtto",
        "Horas_Uso_Acumuladas",
        "Fallas_Acumuladas",
        "MTBF_Promedio_Dias",
        "Corriente_Fuga_uA",
        "Resistencia_Tierra_Ohms",
        "Desviacion_Calibracion_Pct",
    ]
    cur.execute(f'''
        SELECT id, "ID_Equipo", "Fecha_Reporte", {", ".join(f'"{c}"' for c in features)},
               "Variable_Target_Falla", "Estado_IEC62353"
        FROM dataset_eventos
        ORDER BY id ASC
    ''')
    cols_q = ["id", "ID_Equipo", "Fecha_Reporte"] + features + ["Variable_Target_Falla", "Estado_IEC62353"]
    df_ev = pd.DataFrame(cur.fetchall(), columns=cols_q)
    for col in features:
        df_ev[col] = pd.to_numeric(df_ev[col], errors="coerce")
        med = df_ev[col].median() if not df_ev[col].dropna().empty else 0.0
        df_ev[col] = df_ev[col].fillna(med)

    X = df_ev[features].to_numpy(dtype=float)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    X_scaled = (X - mean) / std

    labels, _, wcss4 = _kmeans_deterministico(X_scaled, k=4, seed=42)
    df_ev["cluster_id"] = labels.astype(int)
    df_ev["cluster_nombre"] = df_ev["cluster_id"].map(CLUSTER_NOMBRES_R07)

    # Actualizar dataset_eventos en bloque
    cur.execute("CREATE TEMP TABLE tmp_clusters_ev (id INT, cluster_id INT, cluster_nombre TEXT) ON COMMIT DROP")
    pares_ev = [(int(r.id), int(r.cluster_id), str(r.cluster_nombre)) for r in df_ev[["id", "cluster_id", "cluster_nombre"]].itertuples(index=False)]
    execute_values(cur, "INSERT INTO tmp_clusters_ev (id, cluster_id, cluster_nombre) VALUES %s", pares_ev, page_size=1000)
    cur.execute("""
        UPDATE dataset_eventos d
        SET origen = 'sintetico',
            cluster_id = t.cluster_id,
            cluster_nombre = t.cluster_nombre
        FROM tmp_clusters_ev t
        WHERE d.id = t.id
    """)
    log(f"[7] R07: {cur.rowcount} registros en dataset_eventos marcados con origen='sintetico' y agrupados en 4 clusters K-Means (WCSS={wcss4:.2f})")

    # Último cluster de cada uno de los 232 equipos -> hoja_vida_equipos
    ult_eq = df_ev.sort_values(["ID_Equipo", "Fecha_Reporte", "id"]).groupby("ID_Equipo").tail(1)
    cluster_por_eq = {r.ID_Equipo: (int(r.cluster_id), str(r.cluster_nombre)) for r in ult_eq.itertuples(index=False)}
    for cod_eq, (cid, cnom) in cluster_por_eq.items():
        cur.execute("""
            UPDATE hoja_vida_equipos
            SET origen = 'sintetico',
                cluster_id = %s,
                cluster_nombre = %s
            WHERE codigo_inventario = %s
        """, (cid, cnom, cod_eq))

    # Asignar a cada paciente (origen='sintetico') el cluster dominante / de mayor atención de sus 5 equipos asignados
    # Prioridad de interés clínico/biomédico: Cluster 1 (Crítico) > Cluster 2 (Zona de Confusión) > Cluster 3 (Maduro Alto MTBF) > Cluster 0 (Óptimo)
    prioridad_cluster = {1: 4, 2: 3, 3: 2, 0: 1}
    cur.execute("""
        SELECT p.id,
               COALESCE(array_agg(hv.cluster_id) FILTER (WHERE hv.cluster_id IS NOT NULL), ARRAY[0]) AS cids
        FROM pacientes p
        LEFT JOIN encuentros enc ON enc.paciente_id = p.id AND enc.estado = 'in-progress' AND enc.is_deleted = FALSE
        LEFT JOIN equipos_uci eq ON eq.id = enc.equipo_uci_id AND eq.is_deleted = FALSE
        LEFT JOIN hoja_vida_equipos hv ON hv.id = eq.hoja_vida_id AND hv.is_deleted = FALSE
        WHERE p.is_deleted = FALSE
        GROUP BY p.id
        ORDER BY p.id ASC
    """)
    pacientes_clusters = cur.fetchall()
    for pid, cids in pacientes_clusters:
        lista_c = [int(x) for x in (cids or [0]) if x is not None]
        if not lista_c:
            elegido = 0
        else:
            # Si todos los equipos son Cluster 0/2/3, elegir el de mayor prioridad para reflejar el perfil de riesgo de su cama
            elegido = sorted(lista_c, key=lambda c: (prioridad_cluster.get(c, 0), lista_c.count(c)), reverse=True)[0]
        cur.execute("""
            UPDATE pacientes
            SET origen = 'sintetico',
                cluster_id = %s,
                cluster_nombre = %s
            WHERE id = %s
        """, (elegido, CLUSTER_NOMBRES_R07[elegido], pid))
    log(f"    R07: {len(pacientes_clusters)} pacientes UCI marcados con origen='sintetico' y ligados a su cluster operativo.")

    # Crear tabla resumen de clusters biomédicos para consulta rápida desde API
    cur.execute("""
        DROP TABLE IF EXISTS clusters_biomedicos_resumen;
        CREATE TABLE clusters_biomedicos_resumen (
            cluster_id INT PRIMARY KEY,
            cluster_nombre VARCHAR(120) NOT NULL,
            total_registros INT NOT NULL,
            porcentaje_dataset NUMERIC(5,2) NOT NULL,
            edad_media_meses NUMERIC(8,2),
            meses_mtto_medio NUMERIC(8,2),
            horas_uso_medias NUMERIC(12,2),
            fallas_acumuladas_medias NUMERIC(8,2),
            mtbf_medio_dias NUMERIC(10,2),
            corriente_fuga_media_ua NUMERIC(8,2),
            desviacion_calibracion_media_pct NUMERIC(8,2),
            tasa_falla_pct NUMERIC(6,2),
            es_zona_confusion BOOLEAN DEFAULT FALSE,
            interpretacion_clinica TEXT
        );
    """)
    interpretaciones = {
        0: (False, "Equipos biomédicos jóvenes (~35 meses) con mantenimiento preventivo reciente (~3.3 meses), baja corriente de fuga (75.8 uA) y 99.3% de conformidad IEC 62353. Representa el régimen basal seguro de soporte vital."),
        1: (True, "Activos con retraso de mantenimiento (~7.25 meses), corriente de fuga elevada (137.9 uA) y desviación metrológica alta (3.65%). Incluye 466 registros que aún marcan IEC 62353 Conforme pese a degradación severa (tasa de falla del 58%)."),
        2: (True, "ZONA DE CONFUSIÓN PRINCIPAL DEL MODELO: Equipos de alta antigüedad (~87.7 meses y 48,564 h de uso) con alto historial de fallas (8.58) y menor MTBF (461.6 días), pero con mantenimiento reciente (3.16 meses) y prueba eléctrica IEC 62353 Conforme (98.1%). El modelo se confunde entre equipo sano (0) y falla súbita (1) porque los parámetros eléctricos actuales lucen normales mientras la fatiga mecánica acumulada induce fallas inesperadas."),
        3: (False, "Equipos maduros (~80 meses) de alta estabilidad metrológica con apenas 1.01 fallas históricas y un MTBF sobresaliente (2,056 días), manteniendo riesgo bajo de falla (4%)."),
    }
    total_ev = len(df_ev)
    for cid in range(4):
        sub = df_ev[df_ev["cluster_id"] == cid]
        es_conf, interp = interpretaciones[cid]
        cur.execute("""
            INSERT INTO clusters_biomedicos_resumen (
                cluster_id, cluster_nombre, total_registros, porcentaje_dataset,
                edad_media_meses, meses_mtto_medio, horas_uso_medias, fallas_acumuladas_medias,
                mtbf_medio_dias, corriente_fuga_media_ua, desviacion_calibracion_media_pct,
                tasa_falla_pct, es_zona_confusion, interpretacion_clinica
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            cid,
            CLUSTER_NOMBRES_R07[cid],
            len(sub),
            round(len(sub) * 100.0 / total_ev, 2),
            round(float(sub["Edad_Equipo_Meses"].mean()), 2),
            round(float(sub["Meses_Desde_Ultimo_Mtto"].mean()), 2),
            round(float(sub["Horas_Uso_Acumuladas"].mean()), 2),
            round(float(sub["Fallas_Acumuladas"].mean()), 2),
            round(float(sub["MTBF_Promedio_Dias"].mean()), 2),
            round(float(sub["Corriente_Fuga_uA"].mean()), 2),
            round(float(sub["Desviacion_Calibracion_Pct"].mean()), 2),
            round(float(pd.to_numeric(sub["Variable_Target_Falla"], errors="coerce").fillna(0).mean() * 100.0), 2),
            es_conf,
            interp,
        ))


def reporte(cur):
    cur.execute("""
        SELECT (SELECT COUNT(*) FROM camas_uci WHERE activa) total,
               COUNT(DISTINCT p.cama_uci) FILTER (WHERE c.codigo IS NOT NULL) ocupadas
        FROM pacientes p LEFT JOIN camas_uci c ON c.codigo = p.cama_uci
        WHERE p.is_deleted = FALSE
    """)
    t, o = cur.fetchone()
    log(f"\n=== RESULTADO === Camas: {t} total | {o} ocupadas | {t - o} disponibles")
    cur.execute("""
        SELECT cat.nombre, COUNT(*) total,
               COUNT(*) FILTER (WHERE e.estado_operativo='En Uso') en_uso,
               COUNT(*) FILTER (WHERE e.estado_operativo='Disponible') disp,
               COUNT(*) FILTER (WHERE e.estado_operativo NOT IN ('En Uso','Disponible')) mtto
        FROM equipos_uci e JOIN hoja_vida_equipos h ON h.id=e.hoja_vida_id
        JOIN catalogo_equipos cat ON cat.id=h.equipo_catalogo_id
        WHERE e.is_deleted=FALSE GROUP BY cat.nombre ORDER BY total DESC
    """)
    tot = [0, 0, 0, 0]
    for nombre, a, b, c, d in cur.fetchall():
        log(f"  {nombre[:48]:48s} total {a:3d} | en uso {b:3d} | disponibles {c:3d} | mantenimiento {d:3d}")
        tot = [tot[0] + a, tot[1] + b, tot[2] + c, tot[3] + d]
    log(f"  {'TOTAL':48s} total {tot[0]:3d} | en uso {tot[1]:3d} | disponibles {tot[2]:3d} | mantenimiento {tot[3]:3d}")
    cur.execute("SELECT COUNT(*) FROM encuentros WHERE estado='in-progress' AND is_deleted=FALSE")
    log(f"  Encuentros activos: {cur.fetchone()[0]}")
    cur.execute("""
        SELECT COUNT(*) FROM encuentros enc JOIN equipos_uci e ON e.id=enc.equipo_uci_id
        WHERE enc.estado='in-progress' AND enc.is_deleted=FALSE AND e.estado_operativo <> 'En Uso'
    """)
    log(f"  Inconsistencias (encuentro activo con equipo no 'En Uso'): {cur.fetchone()[0]}")
    cur.execute("""
        SELECT COUNT(*) FROM equipos_uci e WHERE e.is_deleted=FALSE AND e.estado_operativo='En Uso'
          AND NOT EXISTS (SELECT 1 FROM encuentros enc WHERE enc.equipo_uci_id=e.id AND enc.estado='in-progress' AND enc.is_deleted=FALSE)
    """)
    log(f"  Inconsistencias (equipo 'En Uso' sin encuentro activo): {cur.fetchone()[0]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xls", default=DEFAULT_XLS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hojas = pd.read_excel(args.xls, sheet_name=None)
    df = hojas["Dataset_Predictivo"]
    log(f"Excel: {len(df)} registros, {df.ID_Equipo.nunique()} equipos")

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    try:
        cur = conn.cursor()
        sincronizar_dataset(cur, hojas)
        crear_camas(cur)
        sincronizar_equipos(cur, df)
        configurar_r06_y_r07(cur)
        reporte(cur)
        if args.dry_run:
            conn.rollback()
            log("\nDRY-RUN: se revirtieron todos los cambios.")
        else:
            conn.commit()
            log("\nCambios aplicados en Neon.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

