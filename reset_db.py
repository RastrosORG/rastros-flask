import psycopg2
import os
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()

def reset_db():
    conn = psycopg2.connect(
        dbname=os.getenv('POSTGRES_DB'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        host=os.getenv('POSTGRES_HOST'),
        port=os.getenv('POSTGRES_PORT', '5432')
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Dropa todas as tabelas em cascata
    print("Deletando todas as tabelas...")
    cur.execute("""
        DO $$ DECLARE
            r RECORD;
        BEGIN
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
    """)

    conn.commit()
    conn.close()
    print("Tabelas deletadas com sucesso!")

    # Agora recria todas as tabelas com seu método já existente
    from database import create_db
    create_db()

if __name__ == '__main__':
    reset_db()
