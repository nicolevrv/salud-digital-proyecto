import os
import psycopg2
from dotenv import load_dotenv

# Cargar las variables del archivo .env
load_dotenv()

# Obtener la URL de conexión de Neon
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No se encontró DATABASE_URL en el archivo .env")

try:
    conn = psycopg2.connect(DATABASE_URL)

    cur = conn.cursor()

    cur.execute("SELECT version();")

    version = cur.fetchone()[0]

    print("Conexion a Neon exitosa")
    print("Servidor PostgreSQL:")
    print(version)

    cur.close()
    conn.close()

except Exception as e:
    print("Error de conexion a Neon:")
    print(e)