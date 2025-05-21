import os
from psycopg2 import connect, sql
from dotenv import load_dotenv

load_dotenv()

def nuclear_reset():
    conn = connect(os.getenv('DATABASE_URL'))  # Usa a URL do Render
    conn.autocommit = True  # Permite operações DDL
    cursor = conn.cursor()

    try:
        print("💣 Iniciando reset nuclear...")

        # 1. Destruição total (exceto o banco em si)
        cursor.execute("""
        DROP SCHEMA public CASCADE;
        CREATE SCHEMA public;
        GRANT ALL ON SCHEMA public TO current_user;
        """)

        # 2. Recriação completa (igual à primeira execução)
        print("🔄 Recriando toda a estrutura...")
        from database import create_db
        create_db()  # Usa sua função original que já insere os valores padrão

        print("✅ Banco resetado para o estado inicial!")

    except Exception as e:
        print(f"❌ Falha catastrófica: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    nuclear_reset()