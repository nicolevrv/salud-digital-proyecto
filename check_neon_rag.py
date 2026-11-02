# -*- coding: utf-8 -*-
import os, psycopg2
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(r"C:\Users\aniba\OneDrive\Escritorio\SaludDigitalProyecto\.env"))
url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE '%rag%';")
print("Tablas RAG en Neon:", cur.fetchall())
cur.close()
conn.close()
