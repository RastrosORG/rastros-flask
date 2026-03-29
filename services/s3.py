import boto3
from botocore.exceptions import ClientError
from flask import current_app


def get_s3_client():
    """
    Cria e retorna o cliente S3 baseado nas configs do Flask.
    Retorna None se a configuração estiver incompleta.
    """
    cfg = current_app.config

    if not all([
        cfg.get('AWS_ACCESS_KEY_ID'),
        cfg.get('AWS_SECRET_ACCESS_KEY'),
        cfg.get('S3_BUCKET_NAME')
    ]):
        current_app.logger.warning(
            "Configuração do S3 incompleta - uploads desativados"
        )
        return None

    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=cfg['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=cfg['AWS_SECRET_ACCESS_KEY'],
            region_name=cfg['AWS_REGION']
        )

        # Testa acesso ao bucket
        s3.head_bucket(Bucket=cfg['S3_BUCKET_NAME'])
        return s3

    except ClientError as e:
        current_app.logger.error(f"Erro ao conectar no S3: {e}")
        return None


def upload_file_to_s3(file, key, content_type=None):
    s3 = get_s3_client()
    if not s3:
        return None

    try:
        s3.upload_fileobj(
            file,
            current_app.config['S3_BUCKET_NAME'],
            key,
            ExtraArgs={'ContentType': content_type} if content_type else {}
        )
        return key

    except ClientError as e:
        current_app.logger.error(f"Erro ao enviar arquivo para o S3: {e}")
        return None


def delete_file_from_s3(key):
    s3 = get_s3_client()
    if not s3:
        return False

    try:
        s3.delete_object(
            Bucket=current_app.config['S3_BUCKET_NAME'],
            Key=key
        )
        return True

    except ClientError as e:
        current_app.logger.error(f"Erro ao deletar arquivo no S3: {e}")
        return False


def generate_presigned_url(key, expires_in=3600):
    s3 = get_s3_client()
    if not s3:
        return None

    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': current_app.config['S3_BUCKET_NAME'],
                'Key': key
            },
            ExpiresIn=expires_in
        )

    except ClientError as e:
        current_app.logger.error(f"Erro ao gerar URL assinada: {e}")
        return None

def delete_s3_prefix(prefix):
    """
    Deleta todos os objetos com um prefixo específico no S3
    """
    s3 = get_s3_client()
    if not s3:
        return False

    bucket = current_app.config.get('S3_BUCKET_NAME')
    if not bucket:
        return False

    try:
        objects_to_delete = []
        paginator = s3.get_paginator('list_objects_v2')

        # Lista todos os objetos com o prefixo
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                objects_to_delete.extend(
                    {'Key': obj['Key']} for obj in page['Contents']
                )

        # Nada para deletar
        if not objects_to_delete:
            return True

        # Deleta em lotes de 1000 (limite da API S3)
        for i in range(0, len(objects_to_delete), 1000):
            s3.delete_objects(
                Bucket=bucket,
                Delete={'Objects': objects_to_delete[i:i + 1000]}
            )

        current_app.logger.info(
            f"Removidos {len(objects_to_delete)} objetos com prefixo {prefix}"
        )
        return True

    except ClientError as e:
        current_app.logger.error(
            f"Erro ao deletar objetos com prefixo {prefix} no S3: {e}"
        )
        return False
        
    except Exception as e:
        app.logger.error(f"Erro ao deletar prefixo {prefix} no S3: {e}")
        return False
