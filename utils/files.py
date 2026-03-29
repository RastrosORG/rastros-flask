ALLOWED_EXTENSIONS = {
    # Imagens
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'tiff', 'ico',

    # Vídeos
    'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv',

    # Áudio
    'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a',

    # Documentos
    'pdf', 'doc', 'docx', 'xls', 'xlsx',
    'ppt', 'pptx', 'txt', 'csv', 'odt',

    # Compactados
    'zip', 'rar', '7z', 'tar', 'gz',
}


def allowed_file(filename: str) -> bool:
    """
    Verifica se o arquivo possui uma extensão permitida.

    :param filename: nome do arquivo
    :return: True se permitido, False caso contrário
    """
    if not filename or '.' not in filename:
        return False

    extension = filename.rsplit('.', 1)[1].lower()
    return extension in ALLOWED_EXTENSIONS