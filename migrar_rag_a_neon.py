# -*- coding: utf-8 -*-
"""
Script para migrar e insertar los 182 fragmentos de las dos bases de conocimiento
en la base de datos Neon PostgreSQL (Tabla rag_chunks)
"""
import os
import json
import psycopg2
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(r"C:\Users\aniba\OneDrive\Escritorio\SaludDigitalProyecto")
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
RUTA_CHUNKS = BASE_DIR / "middleware" / "app" / "rag" / "fragmentos.json"

def migrar():
    print("Conectando a Neon PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # 1. Crear tabla rag_chunks
    cur.execute("""
    CREATE TABLE IF NOT EXISTS rag_chunks (
        chunk_id VARCHAR(50) PRIMARY KEY,
        doc_id VARCHAR(50) NOT NULL,
        base VARCHAR(50) NOT NULL,
        tipo VARCHAR(50) NOT NULL,
        titulo TEXT NOT NULL,
        codigo_norma TEXT,
        fuente TEXT NOT NULL,
        palabras INT NOT NULL,
        texto TEXT NOT NULL,
        fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    print("Tabla 'rag_chunks' creada o verificada en Neon.")
    
    # 2. Cargar los 182 fragmentos del JSON
    with open(RUTA_CHUNKS, "r", encoding="utf-8") as f:
        chunks = json.load(f)
        
    print(f"Insertando {len(chunks)} fragmentos en Neon PostgreSQL...")
    insertados = 0
    for c in chunks:
        cur.execute("""
        INSERT INTO rag_chunks (chunk_id, doc_id, base, tipo, titulo, codigo_norma, fuente, palabras, texto)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET
            titulo = EXCLUDED.titulo,
            codigo_norma = EXCLUDED.codigo_norma,
            fuente = EXCLUDED.fuente,
            palabras = EXCLUDED.palabras,
            texto = EXCLUDED.texto;
        """, (
            c["chunk_id"],
            c["doc_id"],
            c["base"],
            c["tipo"],
            c["titulo"],
            c.get("codigo_norma", ""),
            c["fuente"],
            c["palabras"],
            c["texto"]
        ))
        insertados += 1
        
    conn.commit()
    print(f"Éxito: {insertados} fragmentos guardados en Neon.")
    
    # 3. Comprobar conteo por base
    cur.execute("SELECT base, count(*) FROM rag_chunks GROUP BY base;")
    conteo = cur.fetchall()
    print("Conteo de fragmentos por base en Neon:")
    for base, cnt in conteo:
        print(f"  • Base '{base}': {cnt} fragmentos")
        
    cur.close()
    conn.close()

if __name__ == "__main__":
    migrar()
