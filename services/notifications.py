import psycopg2.extras
from services.database import get_db_connection


def add_notification(
    user_id: int,
    title: str,
    message: str,
    link: None,
    type_: str = 'info'
):
    """
    Cria uma nova notificação para um usuário.

    :param user_id: ID do usuário que receberá a notificação
    :param title: Título da notificação
    :param message: Mensagem
    :param link: URL relacionada (opcional)
    :param type_: Tipo da notificação (info, success, warning, error)
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO notifications (user_id, title, message, link, type)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, title, message, link, type_))

        conn.commit()

    finally:
        cur.close()
        conn.close()


def mark_as_read(notification_id: int):
    """
    Marca uma notificação como lida.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE notifications
            SET is_read = TRUE
            WHERE id = %s
        """, (notification_id,))

        conn.commit()

    finally:
        cur.close()
        conn.close()


def delete_notification(notification_id: int):
    """
    Remove uma notificação.
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            DELETE FROM notifications
            WHERE id = %s
        """, (notification_id,))

        conn.commit()

    finally:
        cur.close()
        conn.close()