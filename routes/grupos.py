from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import psycopg2.extras

from services.database import get_db_connection

grupos_bp = Blueprint('grupos', __name__)


@grupos_bp.route('/group_request_alt')
def group_request_alt():
    return render_template('group_request_alt.html')

# rotas para grupos
@grupos_bp.route('/groups', methods=['GET', 'POST'])
def groups():
    if 'username' not in session:
        flash('Por favor, faça login para acessar esta página.')
        return redirect(url_for('index'))

    user_id = session['user_id']
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Obter informações do usuário
        cursor.execute(
            'SELECT is_leader, is_member, is_group, is_evaluator FROM users WHERE id = %s',
            (user_id,)
        )
        row = cursor.fetchone()
        user = {
            'is_leader': row['is_leader'] if row else 0,
            'is_member': row['is_member'] if row else 0,
            'is_group': row['is_group'] if row else None,
            'is_evaluator': row['is_evaluator'] if row else 0
        }

        if request.method == 'POST':
            group_name = request.form['group-name']
            members = request.form.getlist('members')
            created_by = user_id

            # Cria o novo grupo
            cursor.execute(
                '''
                INSERT INTO groups (name, created_by)
                VALUES (%s, %s)
                RETURNING id
                ''',
                (group_name, created_by)
            )
            group_id = cursor.fetchone()['id']

            # Adiciona o criador como líder
            cursor.execute(
                '''
                INSERT INTO group_members (group_id, user_id, status)
                VALUES (%s, %s, %s)
                ''',
                (group_id, created_by, "Líder")
            )

            # Atualiza o status do criador
            cursor.execute(
                '''
                UPDATE users
                SET is_leader = 1, is_group = %s
                WHERE id = %s
                ''',
                (group_id, created_by)
            )

            # Remove o criador de outros grupos pendentes
            cursor.execute(
                '''
                DELETE FROM group_members
                WHERE user_id = %s
                  AND status IN ('Aguardando', 'Solicitando')
                  AND group_id != %s
                ''',
                (created_by, group_id)
            )

            # Processa os membros convidados
            for member_id in members:
                member_id = int(member_id)
                if member_id != created_by:

                    # Cria a notificação PADRÃO (via service)
                    base_message = f'Você foi convidado para o grupo {group_name}.'
                    notification_id = add_notification(
                        user_id=member_id,
                        title='Convite para grupo',
                        message=base_message,
                        link=None,
                        type_='info'
                    )

                    # Gera os links
                    accept_url = url_for(
                        'notificacoes.accept_member_request',
                        notification_id=notification_id
                    )
                    reject_url = url_for(
                        'notificacoes.reject_member_request',
                        notification_id=notification_id
                    )

                    # Atualiza a mensagem com os links (continua usando cursor.execute)
                    cursor.execute(
                        '''
                        UPDATE notifications
                        SET message = %s
                        WHERE id = %s
                        ''',
                        (
                            f'{base_message} '
                            f'<a href="{accept_url}">Aceitar</a> ou '
                            f'<a href="{reject_url}">Recusar</a>',
                            notification_id
                        )
                    )

                    # Adiciona como "Aguardando"
                    cursor.execute(
                        '''
                        INSERT INTO group_members (group_id, user_id, status)
                        VALUES (%s, %s, 'Aguardando')
                        ''',
                        (group_id, member_id)
                    )

            conn.commit()
            flash('Grupo criado com sucesso!')
            return redirect(url_for('groups'))

        # GET - usuários disponíveis
        cursor.execute(
            '''
            SELECT id, username
            FROM users
            WHERE is_member = 0
              AND is_leader = 0
              AND is_evaluator = 0
            '''
        )
        filtered_users = cursor.fetchall()

        # Grupos existentes
        cursor.execute(
            '''
            SELECT g.*,
                   (SELECT COUNT(*)
                    FROM group_members
                    WHERE group_id = g.id
                      AND status IN ('Líder', 'Membro')) AS member_count
            FROM groups g
            '''
        )
        groups = cursor.fetchall()

        message = request.args.get('message')
        return render_template(
            'groups.html',
            users=filtered_users,
            groups=groups,
            user=user,
            message=message
        )

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro na rota /groups: {e}")
        flash('Ocorreu um erro ao processar sua solicitação.')
        return redirect(url_for('groups'))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para excluir o grupo
@grupos_bp.route('/groups/<int:group_id>/delete', methods=['POST'])
def delete_group(group_id):
    if 'user_id' not in session:
        flash('Acesso não autorizado. Por favor, faça login.')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Verifica se o grupo existe e se o usuário é o criador
        cursor.execute('SELECT created_by FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        
        if group and group['created_by'] == session['user_id']:
            # 1. Primeiro remove todas as respostas do grupo
            cursor.execute('SELECT id, tarefa_id FROM respostas WHERE grupo_id = %s', (group_id,))
            respostas = cursor.fetchall()
            
            # Remove os arquivos físicos das respostas no S3
            if s3_client and S3_BUCKET_NAME:
                for resposta in respostas:
                    try:
                        prefix = f"respostas/proposta_{resposta['tarefa_id']}/grupo_{group_id}/resposta_{resposta['id']}/"
                        
                        # Lista todos os objetos na pasta da resposta
                        objects_to_delete = []
                        list_objects = s3_client.list_objects_v2(
                            Bucket=S3_BUCKET_NAME,
                            Prefix=prefix
                        )
                        
                        if 'Contents' in list_objects:
                            objects_to_delete = [{'Key': obj['Key']} for obj in list_objects['Contents']]
                        
                        # Remove todos os objetos da pasta da resposta
                        if objects_to_delete:
                            app.logger.info(f"Removendo {len(objects_to_delete)} arquivos do S3 para resposta {resposta['id']}")
                            s3_client.delete_objects(
                                Bucket=S3_BUCKET_NAME,
                                Delete={'Objects': objects_to_delete}
                            )
                    except Exception as e:
                        app.logger.error(f"Erro ao remover arquivos da resposta {resposta['id']} no S3: {e}")
                        continue
            
            cursor.execute('DELETE FROM respostas WHERE grupo_id = %s', (group_id,))
            
            # 2. Remove todas as notificações relacionadas ao grupo
            cursor.execute('DELETE FROM notifications WHERE group_id = %s', (group_id,))
            
            # 3. Remove todos os membros do grupo
            cursor.execute('DELETE FROM group_members WHERE group_id = %s', (group_id,))
            
            # 4. Remove todas as associações de tarefas
            cursor.execute('DELETE FROM tarefa_equipes WHERE grupo_id = %s', (group_id,))
            
            # 5. Atualiza status dos usuários que estavam no grupo
            cursor.execute('''
                UPDATE users 
                SET is_member = 0, is_leader = 0, is_group = 'none' 
                WHERE is_group = %s
            ''', (str(group_id),))
            
            # 6. Finalmente remove o grupo
            cursor.execute('DELETE FROM groups WHERE id = %s', (group_id,))
            
            conn.commit()
            flash('Grupo e todos os dados relacionados foram excluídos com sucesso!')
        else:
            flash('Você não tem permissão para excluir este grupo.')

        return redirect(url_for('groups'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao excluir grupo {group_id}: {e}")
        flash('Ocorreu um erro ao excluir o grupo. Verifique se não há dados dependentes.')
        return redirect(url_for('groups'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para expulsar um membro
@grupos_bp.route('/groups/<int:group_id>/kick/<int:user_id>', methods=['POST'])
def kick_member(group_id, user_id):
    if 'user_id' not in session:
        flash('Acesso não autorizado. Por favor, faça login.')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Verifica se o grupo existe e se o usuário atual é o criador
        cursor.execute('SELECT created_by FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        
        if group and group['created_by'] == session['user_id']:
            # Verifica se o usuário a ser removido não é o próprio criador
            if user_id == session['user_id']:
                flash('Você não pode remover a si mesmo do grupo. Use a opção de deletar grupo.')
                return redirect(url_for('group_detail', group_id=group_id))

            # Remove status de membro do usuário (usando 0 para false)
            cursor.execute('''
                UPDATE users 
                SET is_member = 0, is_group = 'none' 
                WHERE id = %s
            ''', (user_id,))
            
            # Remove o usuário da lista de membros
            cursor.execute('''
                DELETE FROM group_members 
                WHERE group_id = %s AND user_id = %s
            ''', (group_id, user_id))
            
            # Notifica o usuário removido
            add_notification(
                user_id=user_id,
                title='Grupo',
                message=f'Você foi removido do grupo {group_id} pelo líder.',
                link=None,
                type_='warning'
            )
            
            conn.commit()
            flash('Membro removido com sucesso!')
        else:
            flash('Você não tem permissão para remover membros deste grupo.')

        return redirect(url_for('group_detail', group_id=group_id))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao remover membro {user_id} do grupo {group_id}: {e}")
        flash('Ocorreu um erro ao remover o membro do grupo.')
        return redirect(url_for('group_detail', group_id=group_id))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para um membro deixar o grupo
@grupos_bp.route('/groups/<int:group_id>/leave', methods=['POST'])
def leave_group(group_id):
    if 'user_id' not in session:
        flash('Por favor, faça login para realizar esta ação.')
        return redirect(url_for('index'))

    user_id = session['user_id']
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verifica se o usuário é o líder do grupo
        cursor.execute('''
            SELECT 1 FROM group_members 
            WHERE group_id = %s AND user_id = %s AND status = 'Líder'
        ''', (group_id, user_id))
        is_leader = cursor.fetchone()

        if is_leader:
            # Se for líder, verifica se há outros membros para transferir liderança
            cursor.execute('''
                SELECT user_id FROM group_members 
                WHERE group_id = %s AND user_id != %s AND status = 'Membro'
                LIMIT 1
            ''', (group_id, user_id))
            new_leader = cursor.fetchone()

            if new_leader:
                # Transfere a liderança
                cursor.execute('''
                    UPDATE group_members 
                    SET status = 'Líder' 
                    WHERE group_id = %s AND user_id = %s
                ''', (group_id, new_leader[0]))
                
                # Atualiza o status do novo líder na tabela users
                cursor.execute('''
                    UPDATE users 
                    SET is_leader = 1 
                    WHERE id = %s
                ''', (new_leader[0],))
            else:
                # Se não houver membros, remove o grupo completamente
                cursor.execute('DELETE FROM groups WHERE id = %s', (group_id,))
                cursor.execute('DELETE FROM group_members WHERE group_id = %s', (group_id,))
                flash('Grupo removido pois não havia outros membros.')

        # Remove o status do usuário que está saindo
        cursor.execute('''
            UPDATE users 
            SET is_member = 0, is_group = 'none', is_leader = 0 
            WHERE id = %s
        ''', (user_id,))
        
        # Remove o usuário da lista de membros
        cursor.execute('''
            DELETE FROM group_members 
            WHERE group_id = %s AND user_id = %s
        ''', (group_id, user_id))

        # Notifica os membros restantes (se aplicável)
        if not is_leader:
            cursor.execute('''
                INSERT INTO notifications (user_id, message, group_id)
                SELECT user_id, %s, %s
                FROM group_members
                WHERE group_id = %s AND user_id != %s
            ''', (f'Um membro saiu do grupo {group_id}.', group_id, group_id, user_id))

        conn.commit()
        flash('Você saiu do grupo com sucesso.')
        return redirect(url_for('groups'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao sair do grupo {group_id}: {e}")
        flash('Ocorreu um erro ao sair do grupo.')
        return redirect(url_for('groups'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@grupos_bp.route('/groups/<int:group_id>')
def group_detail(group_id):
    if 'username' not in session:
        flash('Por favor, faça login para visualizar este grupo.')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Obtém os detalhes do grupo
        cursor.execute('SELECT * FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        
        if group is None:
            flash('Grupo não encontrado.')
            return redirect(url_for('groups'))

        # Obtém informações do usuário logado
        cursor.execute('SELECT * FROM users WHERE username = %s', (session['username'],))
        user = cursor.fetchone()

        # Verifica o grupo atual do usuário
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user['id'],))
        current_group = cursor.fetchone()
        current_group_id = current_group['is_group'] if current_group and current_group['is_group'] != 'none' else None

        # Verifica status do usuário no grupo atual
        cursor.execute('''
            SELECT status FROM group_members 
            WHERE group_id = %s AND user_id = %s
        ''', (group_id, user['id']))
        current_user_status = cursor.fetchone()

        # Obtém membros ativos do grupo (Líder e Membro)
        cursor.execute('''
            SELECT users.id, users.username, group_members.status 
            FROM group_members 
            JOIN users ON group_members.user_id = users.id 
            WHERE group_members.group_id = %s AND group_members.status IN ('Líder', 'Membro')
        ''', (group_id,))
        members = cursor.fetchall()
        is_group_full = len(members) >= 4

        # Se usuário não for membro do grupo
        if current_user_status is None:
            if is_group_full:
                flash('Este grupo já está cheio.')
                return redirect(url_for('groups'))
            else:
                cursor.execute('SELECT created_by FROM groups WHERE id = %s', (group_id,))
                leader = cursor.fetchone()
                return render_template('group_request.html', 
                                    group=group, 
                                    leader=leader, 
                                    user=user, 
                                    is_group=current_group['is_group'], 
                                    current_group_id=current_group_id)

        # Obtém convites pendentes
        cursor.execute('''
            SELECT users.id, users.username, group_members.status 
            FROM group_members 
            JOIN users ON group_members.user_id = users.id 
            WHERE group_members.group_id = %s AND group_members.status IN ('Solicitando', 'Aguardando')
        ''', (group_id,))
        pending_invitations = cursor.fetchall()

        # Obtém usuários disponíveis para convite
        cursor.execute('''
            SELECT id, username 
            FROM users 
            WHERE is_evaluator = 0 
              AND is_leader = 0 
              AND is_member = 0 
              AND is_group = 'none'
        ''')
        available_users = cursor.fetchall()

        return render_template('group_detail.html', 
                            group=group, 
                            members=members, 
                            pending_invitations=pending_invitations, 
                            current_user_status=current_user_status['status'] if current_user_status else None,
                            available_users=available_users,
                            is_group_full=is_group_full)

    except Exception as e:
        app.logger.error(f"Erro ao carregar detalhes do grupo {group_id}: {e}")
        flash('Ocorreu um erro ao carregar o grupo.')
        return redirect(url_for('groups'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@grupos_bp.route('/groups/<int:group_id>/request', methods=['POST'])
def request_group_invitation(group_id):
    if 'username' not in session:
        flash('Por favor, faça login para solicitar entrada no grupo.')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Verifica se o usuário já está em um grupo
        cursor.execute('SELECT is_group FROM users WHERE username = %s', (session['username'],))
        user = cursor.fetchone()
        
        if not user:
            flash('Usuário não encontrado.')
            return redirect(url_for('groups'))

        if user['is_group'] and user['is_group'] != 'none':
            flash('Você já faz parte de um grupo e não pode solicitar entrada em outro.')
            return redirect(url_for('groups'))

        # Verifica se já existe uma solicitação pendente
        cursor.execute('''
            SELECT 1 FROM group_members 
            WHERE group_id = %s AND user_id = %s AND status = 'Solicitando'
        ''', (group_id, user['id']))
        existing_request = cursor.fetchone()

        if existing_request:
            flash('Você já possui uma solicitação pendente para este grupo.')
            return redirect(url_for('groups'))

        # Adiciona solicitação ao grupo
        cursor.execute('''
            INSERT INTO group_members (group_id, user_id, status)
            VALUES (%s, %s, 'Solicitando')
        ''', (group_id, user['id']))

        # Notifica o líder do grupo
        cursor.execute('''
            SELECT created_by FROM groups WHERE id = %s
        ''', (group_id,))
        leader = cursor.fetchone()

        if leader:
            add_notification(
                user_id=leader['created_by'],
                title='Solicitação de grupo',
                message=f'Novo pedido de entrada no grupo de {session["username"]}.',
                link=None,
                type_='info'
            )

        conn.commit()
        flash('Solicitação enviada. Aguarde a aprovação do líder do grupo.')
        return redirect(url_for('groups'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao solicitar entrada no grupo {group_id}: {e}")
        flash('Ocorreu um erro ao enviar sua solicitação.')
        return redirect(url_for('groups'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Envia a notificação para os convidados, com o grupo já criado
@grupos_bp.route('/groups/<int:group_id>/add_members', methods=['POST'])
def add_members(group_id):
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Usuário não autenticado'}), 401

    conn = None
    cursor = None
    try:
        # Verifica se o usuário é líder do grupo
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Verifica se o usuário atual é o líder do grupo
        cursor.execute('''
            SELECT 1 FROM groups 
            WHERE id = %s AND created_by = %s
        ''', (group_id, session['user_id']))
        is_leader = cursor.fetchone()
        
        if not is_leader:
            return jsonify({
                'success': False,
                'error': 'Apenas o líder do grupo pode adicionar membros'
            }), 403

        # Obtém os dados JSON da requisição
        data = request.get_json()
        if not data or 'members' not in data:
            return jsonify({
                'success': False,
                'error': 'Dados de membros ausentes'
            }), 400

        selected_members = data['members']
        if not isinstance(selected_members, list):
            return jsonify({
                'success': False,
                'error': 'Formato de membros inválido'
            }), 400

        # Obtém o nome do grupo
        cursor.execute('SELECT name FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        if not group:
            return jsonify({
                'success': False,
                'error': 'Grupo não encontrado'
            }), 404

        group_name = group['name']
        added_members = 0

        for member_id in selected_members:
            try:
                member_id = int(member_id)
            except (ValueError, TypeError):
                continue  # Pula IDs inválidos

            # Verifica se o usuário já está em algum grupo
            cursor.execute('''
                SELECT is_group FROM users 
                WHERE id = %s AND is_evaluator = 0
            ''', (member_id,))
            user = cursor.fetchone()
            
            if not user or user['is_group'] not in (None, 'none'):
                continue  # Usuário já está em um grupo

            # Verifica se o grupo está cheio (1 líder + 3 membros)
            cursor.execute('''
                SELECT COUNT(*) as count 
                FROM group_members 
                WHERE group_id = %s AND status IN ('Líder', 'Membro')
            ''', (group_id,))
            members_count = cursor.fetchone()['count']
            
            if members_count >= 4:
                return jsonify({
                    'success': False,
                    'error': 'O grupo já está cheio (máximo de 4 membros)'
                }), 400

            # Verifica se já existe um convite pendente
            cursor.execute('''
                SELECT 1 FROM group_members 
                WHERE group_id = %s AND user_id = %s
            ''', (group_id, member_id))
            existing_invite = cursor.fetchone()
            
            if existing_invite:
                continue  # Já existe um convite para este usuário

            # Cria o convite
            cursor.execute('''
                INSERT INTO group_members (group_id, user_id, status)
                VALUES (%s, %s, 'Aguardando')
            ''', (group_id, member_id))

            # Cria a notificação (sem URLs primeiro)
            notification_id = add_notification(
                user_id=member_id,
                title='Convite para grupo',
                message=f'Você foi convidado para o grupo {group_name}.',
                link=None,
                type_='info'
            )

            # Agora gera os URLs com o notification_id real
            accept_url = url_for(
                'notificacoes.accept_member_request',
                notification_id=notification_id,
                _external=True
            )
            reject_url = url_for(
                'notificacoes.reject_member_request',
                notification_id=notification_id,
                _external=True
            )

            # Atualiza a notificação com URLs completos
            cursor.execute(
                '''
                UPDATE notifications
                SET message = %s
                WHERE id = %s
                ''',
                (
                    f'Você foi convidado para o grupo {group_name}. '
                    f'<a href="{accept_url}">Aceitar</a> ou '
                    f'<a href="{reject_url}">Recusar</a>',
                    notification_id
                )
            )

            added_members += 1

        conn.commit()
        
        return jsonify({
            'success': True,
            'message': f'Convites enviados com sucesso para {added_members} membro(s)'
        })

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao adicionar membros ao grupo {group_id}: {e}")
        return jsonify({
            'success': False,
            'error': 'Ocorreu um erro ao enviar os convites',
            'details': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@grupos_bp.route('/groups/<int:group_id>/accept/<int:user_id>', methods=['POST'])
def add_member_request(group_id, user_id):
    if 'username' not in session:
        flash('Acesso não autorizado. Por favor, faça login.', 'error')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verifica se o usuário atual tem permissão para aceitar membros
        cursor.execute('''
            SELECT created_by FROM groups WHERE id = %s
        ''', (group_id,))
        group_owner = cursor.fetchone()
        
        if not group_owner or group_owner['created_by'] != session['user_id']:
            flash('Você não tem permissão para aceitar membros neste grupo.', 'error')
            return redirect(url_for('group_detail', group_id=group_id))

        # Verifica se o grupo está cheio (1 líder + 3 membros)
        cursor.execute('''
            SELECT COUNT(*) FROM group_members 
            WHERE group_id = %s AND status IN ('Líder', 'Membro')
        ''', (group_id,))
        member_count = cursor.fetchone()[0]
        
        if member_count >= 4:
            flash('O grupo já está cheio (máximo de 4 membros).', 'error')
            return redirect(url_for('group_detail', group_id=group_id))

        # Atualiza o status do usuário
        cursor.execute('''
            UPDATE users
            SET is_member = 1, is_group = %s
            WHERE id = %s
        ''', (group_id, user_id))

        # Atualiza o status no grupo
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

        # Notifica o usuário aceito
        cursor.execute('SELECT name FROM groups WHERE id = %s', (group_id,))
        group_name = cursor.fetchone()['name']

        add_notification(
            user_id=user_id,
            title='Grupo',
            message=f'Seu pedido para o grupo {group_name} foi aceito!',
            link=None,
            type_='success'
        )

        conn.commit()
        flash(f'Usuário aceito no grupo {group_name} com sucesso!', 'success')

    except Exception as e:
        if conn:
            conn.rollback()
        flash('Ocorreu um erro ao processar a solicitação.', 'error')
        app.logger.error(f"Erro ao aceitar usuário {user_id} no grupo {group_id}: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for('group_detail', group_id=group_id))

@grupos_bp.route('/groups/<int:group_id>/reject/<int:user_id>', methods=['POST'])
def recusar_member_request(group_id, user_id):
    if 'username' not in session:
        flash('Acesso não autorizado. Por favor, faça login.', 'error')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verifica se o usuário atual tem permissão para recusar membros
        cursor.execute('SELECT created_by FROM groups WHERE id = %s', (group_id,))
        group_owner = cursor.fetchone()
        
        if not group_owner or group_owner['created_by'] != session['user_id']:
            flash('Você não tem permissão para recusar membros neste grupo.', 'error')
            return redirect(url_for('group_detail', group_id=group_id))

        # Verifica se o usuário existe no grupo
        cursor.execute('''
            SELECT status FROM group_members 
            WHERE group_id = %s AND user_id = %s
        ''', (group_id, user_id))
        member_status = cursor.fetchone()

        if not member_status:
            flash('Usuário não encontrado neste grupo.', 'error')
            return redirect(url_for('group_detail', group_id=group_id))

        # Remove o usuário do grupo
        cursor.execute('''
            DELETE FROM group_members
            WHERE group_id = %s AND user_id = %s
        ''', (group_id, user_id))

        # Se o usuário não estava em outro grupo, limpa seus status
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user_group = cursor.fetchone()

        if user_group and user_group['is_group'] == group_id:
            cursor.execute('''
                UPDATE users
                SET is_member = 0, is_group = 'none'
                WHERE id = %s
            ''', (user_id,))

        # Notifica o usuário recusado
        cursor.execute('SELECT name FROM groups WHERE id = %s', (group_id,))
        group_name = cursor.fetchone()['name']
        
        add_notification(
            user_id=user_id,
            title='Grupo',
            message=f'Seu pedido para o grupo {group_name} foi recusado.',
            link=None,
            type_='info'
        )

        conn.commit()
        flash('Usuário recusado e removido do grupo com sucesso.', 'success')

    except Exception as e:
        if conn:
            conn.rollback()
        flash('Ocorreu um erro ao processar a recusa.', 'error')
        app.logger.error(f"Erro ao recusar usuário {user_id} no grupo {group_id}: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for('group_detail', group_id=group_id))