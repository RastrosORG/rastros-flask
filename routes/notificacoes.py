from flask import Blueprint, render_template, redirect, url_for, session, flash, jsonify, current_app
import psycopg2.extras

from services.database import get_db_connection
from services.notifications import add_notification

notificacoes_bp = Blueprint('notificacoes', __name__)


@notificacoes_bp.route('/notificacoes')
def notificacoes():
    if 'username' not in session:
        flash('Por favor, faça login para acessar suas notificações.')
        return redirect(url_for('auth.index'))
    
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Busca as notificações do usuário ordenadas por data (mais recentes primeiro)
        cursor.execute(
            '''SELECT id, message, read, created_at 
               FROM notifications 
               WHERE user_id = %s 
               ORDER BY created_at DESC 
               LIMIT 10''', 
            (session['user_id'],)
        )
        notifications = cursor.fetchall()

        # Marcar notificações como lidas
        if notifications:
            cursor.execute(
                '''UPDATE notifications 
                   SET read = 1 
                   WHERE user_id = %s AND read = 0''',
                (session['user_id'],)
            )
            conn.commit()

        return render_template('notificacoes.html', notifications=notifications)

    except Exception as e:
        current_app.logger.error(f"Erro ao carregar notificações: {e}")
        flash('Ocorreu um erro ao carregar suas notificações.')
        return redirect(url_for('auth.index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@notificacoes_bp.route('/notifications/<int:notification_id>/accept', methods=['GET', 'POST'])
def accept_member_request(notification_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Busca a notificação
        cursor.execute('SELECT * FROM notifications WHERE id = %s', (notification_id,))
        notification = cursor.fetchone()

        if notification is None:
            flash('Notificação não encontrada.')
            return redirect(url_for('notificacoes.notificacoes'))

        user_id = notification['user_id']
        group_id = notification['group_id']

        # Obtém o nome do grupo
        cursor.execute('SELECT name FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        
        if group is None:
            flash('Grupo não encontrado.')
            return redirect(url_for('notificacoes.notificacoes'))

        group_name = group['name']

        # Verifica se o usuário já faz parte de um grupo
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        
        if user is None:
            flash('Usuário não encontrado.')
            return redirect(url_for('notificacoes.notificacoes'))

        if user['is_group'] is not None and str(user['is_group']).lower() != 'none':
            # Usuário já em um grupo - cria notificação
            add_notification(
                user_id=user_id,
                title='Grupo',
                message='Você já faz parte de um grupo e não pode aceitar novos convites.',
                link=None,
                type_='warning'
            )
            
            # Remove notificação original
            cursor.execute('DELETE FROM notifications WHERE id = %s', (notification_id,))
            
            conn.commit()
            flash('Você já faz parte de um grupo e não pode aceitar novos convites.')
            return redirect(url_for('notificacoes.notificacoes'))

        # Verifica se o grupo está cheio (1 líder + 3 membros)
        cursor.execute('''
            SELECT COUNT(*) as count 
            FROM group_members 
            WHERE group_id = %s AND status IN ('Líder', 'Membro')
        ''', (group_id,))
        members = cursor.fetchone()
        is_group_full = members['count'] >= 4

        if is_group_full:
            # Remove usuário do status "Aguardando"
            cursor.execute('''
                DELETE FROM group_members 
                WHERE group_id = %s AND user_id = %s AND status = 'Aguardando'
            ''', (group_id, user_id))

            # Notifica sobre grupo cheio
            add_notification(
                user_id=user_id,
                title='Grupo cheio',
                message=f'O grupo {group_name} está cheio. Você não pôde ser adicionado.',
                link=None,
                type_='error'
            )

            conn.commit()
            flash('O grupo está cheio. Você não pôde ser adicionado.')
            return redirect(url_for('notificacoes.notificacoes'))

        # Verifica se usuário já é membro do grupo
        cursor.execute('''
            SELECT * FROM group_members 
            WHERE group_id = %s AND user_id = %s
        ''', (group_id, user_id))
        member_check = cursor.fetchone()

        if member_check is None:
            # Adiciona como membro
            cursor.execute('''
                INSERT INTO group_members (group_id, user_id, status) 
                VALUES (%s, %s, 'Membro')
            ''', (group_id, user_id))
        else:
            # Atualiza status para membro
            cursor.execute('''
                UPDATE group_members 
                SET status = 'Membro' 
                WHERE group_id = %s AND user_id = %s
            ''', (group_id, user_id))

        # Remove de outros grupos pendentes
        cursor.execute('''
            DELETE FROM group_members
            WHERE user_id = %s AND status IN ('Aguardando', 'Solicitando') AND group_id != %s
        ''', (user_id, group_id))

        # Remove notificação original
        cursor.execute('DELETE FROM notifications WHERE id = %s', (notification_id,))

        # Notifica sobre aceitação
        add_notification(
            user_id=user_id,
            title='Grupo',
            message=f'Você foi aceito no grupo {group_name}.',
            link=None,
            type_='success'
        )

        # Atualiza status do usuário
        cursor.execute('''
            UPDATE users 
            SET is_member = 1, is_group = %s 
            WHERE id = %s
        ''', (group_id, user_id))

        conn.commit()
        flash('Solicitação aceita e usuário adicionado ao grupo.')
        return redirect(url_for('notificacoes.notificacoes'))

    except Exception as e:
        if conn:
            conn.rollback()
        current_app.logger.error(f"Erro ao processar aceitação de convite: {e}")
        flash('Ocorreu um erro ao processar sua solicitação.')
        return redirect(url_for('notificacoes.notificacoes'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@notificacoes_bp.route('/notifications/<int:notification_id>/reject', methods=['GET', 'POST'])
def reject_member_request(notification_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Busca a notificação
        cursor.execute('SELECT * FROM notifications WHERE id = %s', (notification_id,))
        notification = cursor.fetchone()

        if not notification:
            flash('Notificação não encontrada.')
            return redirect(url_for('notificacoes.notificacoes'))

        user_id = notification['user_id']
        group_id = notification['group_id']

        # Obtém o nome do grupo para a mensagem
        cursor.execute('SELECT name FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        group_name = group['name'] if group else f"Grupo {group_id}"

        # Remove o usuário da tabela de membros (status 'Aguardando')
        cursor.execute('''
            DELETE FROM group_members 
            WHERE group_id = %s AND user_id = %s AND status = 'Aguardando'
        ''', (group_id, user_id))

        # Exclui a notificação original
        cursor.execute('DELETE FROM notifications WHERE id = %s', (notification_id,))

        # Notifica o usuário sobre a recusa
        add_notification(
            user_id=user_id,
            title='Grupo',
            message=f'Sua solicitação para participar do grupo {group_name} foi recusada.',
            link=None,
            type_='info'
        )

        conn.commit()
        flash('Solicitação recusada com sucesso.')
        return redirect(url_for('notificacoes.notificacoes'))

    except Exception as e:
        if conn:
            conn.rollback()
        current_app.logger.error(f"Erro ao rejeitar solicitação: {e}")
        flash('Ocorreu um erro ao processar a recusa.')
        return redirect(url_for('notificacoes.notificacoes'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para excluir uma notificação
@notificacoes_bp.route('/notifications/<int:notification_id>/delete', methods=['POST'])
def delete_notification(notification_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Busca a notificação
        cursor.execute('SELECT * FROM notifications WHERE id = %s', (notification_id,))
        notification = cursor.fetchone()

        if not notification:
            flash('Notificação não encontrada.')
            return redirect(url_for('notificacoes.notificacoes'))

        user_id = notification['user_id']
        group_id = notification['group_id']

        # Se for notificação de convite, remove do grupo com status 'Aguardando'
        if group_id is not None:
            cursor.execute('''
                DELETE FROM group_members 
                WHERE group_id = %s AND user_id = %s AND status = 'Aguardando'
            ''', (group_id, user_id))

            # Opcional: Notificar o usuário sobre o cancelamento
            add_notification(
                user_id=user_id,
                title='Grupo',
                message='Um convite de grupo foi cancelado pelo remetente.',
                link=None,
                type_='info'
            )

        # Exclui a notificação original
        cursor.execute('DELETE FROM notifications WHERE id = %s', (notification_id,))

        conn.commit()
        flash('Notificação removida com sucesso.')
        return redirect(url_for('notificacoes.notificacoes'))

    except Exception as e:
        if conn:
            conn.rollback()
        current_app.logger.error(f"Erro ao excluir notificação: {e}")
        flash('Ocorreu um erro ao excluir a notificação.')
        return redirect(url_for('notificacoes.notificacoes'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para verificar novas notificações (usada pelo JavaScript)
@notificacoes_bp.route('/notificacoes/verificar')
def verificar_notificacoes():
    if 'username' not in session:
        return jsonify({'has_new': False})
    
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verifica se há notificações não lidas
        cursor.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id = %s AND read = 0', 
            (session['user_id'],)
        )
        count = cursor.fetchone()[0]
        
        return jsonify({
            'has_new': count > 0,
            'count': count  # Adicionado contagem total para uso futuro
        })

    except Exception as e:
        current_app.logger.error(f"Erro ao verificar notificações: {e}")
        return jsonify({'has_new': False, 'error': 'Erro ao verificar notificações'})
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()