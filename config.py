import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()


class Config:
    
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'super-secret-key')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    # Caminhos base
    BASE_DIR = os.path.abspath(os.getcwd())

    # Upload local
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB

    # PostgreSQL
    POSTGRES_DB = os.getenv('POSTGRES_DB', 'postgres')
    POSTGRES_USER = os.getenv('POSTGRES_USER', 'postgres')
    POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD')
    POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
    POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')

    # AWS S3
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
    AWS_REGION = os.getenv('AWS_REGION', 'us-east-2')
    S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

    # Segurança de sessão
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'