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
