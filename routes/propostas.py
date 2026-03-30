from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import psycopg2.extras

from services.database import get_db_connection
from services.s3 import upload_file_to_s3, delete_file_from_s3
from utils.files import allowed_file

propostas_bp = Blueprint('propostas', __name__)


# Página de criação de propostas (acesso restrito a avaliadores)
@propostas_bp.route('/proposta', methods=['GET', 'POST'])
def proposta():
    if 'username' not in session or session.get('is_evaluator') != 1:
        return redirect(url_for('auth.index'))

    if request.method == 'POST':
        proposta_nome = request.form['proposta_nome']
        descricao = request.form['descricao']
        arquivos = request.files.getlist('arquivos')

        if not proposta_nome or not descricao:
            flash('Nome e descrição da proposta são obrigatórios!')
            return redirect(url_for('propostas.proposta'))

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cursor.execute(
                'INSERT INTO propostas (nome, descricao, arquivos, avaliador_id) VALUES (%s, %s, %s, %s) RETURNING id',
                (proposta_nome, descricao, '', session['user_id'])
            )
            proposta_id = cursor.fetchone()['id']
            conn.commit()

            # Salva os arquivos no S3
            saved_files = []
            for file in arquivos:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    s3_path = f"propostas/proposta_{proposta_id}/{filename}"
                    
                    if upload_file_to_s3(file, S3_BUCKET_NAME, s3_path):
                        saved_files.append(filename)

            # Atualiza a lista de arquivos no banco de dados
            cursor.execute(
                'UPDATE propostas SET arquivos = %s WHERE id = %s',
                (','.join(saved_files), proposta_id)
            )
            conn.commit()
            
            flash('Proposta criada com sucesso!')
            return redirect(url_for('propostas.tarefas'),
                   S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME'))
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Erro ao criar proposta: {e}")
            flash('Ocorreu um erro ao criar a proposta.')
            return redirect(url_for('propostas.proposta'))
        finally:
            cursor.close()
            conn.close()

    return render_template('proposta.html')

# Página para listar as tarefas
@propostas_bp.route('/tarefas')
def tarefas():
    # Verifica se o usuário está logado
    if 'username' not in session:
        return redirect(url_for('auth.index'))

    conn = None
    cursor = None
    try:
        # Verifica se o usuário é um avaliador
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Consulta se o usuário é avaliador
        cursor.execute('SELECT is_evaluator FROM users WHERE username = %s', (session['username'],))
        user = cursor.fetchone()
        is_evaluator = user['is_evaluator'] == 1 if user else 0

        # Se o usuário NÃO for um avaliador, verifica o tempo restante
        if not is_evaluator:
            cursor.execute('SELECT start_time, total_time FROM cronometro LIMIT 1')
            registro = cursor.fetchone()
            if registro:
                start_time = registro['start_time']  # Já é datetime no PostgreSQL
                total_time = registro['total_time']
                tempo_passado = (datetime.now() - start_time).total_seconds()
                tempo_restante = max(total_time - tempo_passado, 0)

                if tempo_restante <= 0:
                    return redirect(url_for('cronometro.tempo_esgotado'))

        # Consulta as propostas/tarefas
        cursor.execute('''
            SELECT p.id, p.nome, p.descricao, p.arquivos,
                   STRING_AGG(g.name, ', ') AS equipes
            FROM propostas p
            LEFT JOIN tarefa_equipes te ON p.id = te.tarefa_id
            LEFT JOIN groups g ON te.grupo_id = g.id
            GROUP BY p.id
        ''')
        propostas = cursor.fetchall()

        # Obtém informações adicionais do usuário
        cursor.execute('SELECT is_leader, is_group FROM users WHERE username = %s', (session['username'],))
        user = cursor.fetchone()
        is_leader = user['is_leader'] == 1 if user else 0
        user_group_id = user['is_group'] if user else None

        return render_template('tarefas.html', 
                            propostas=propostas, 
                            is_leader=is_leader, 
                            user_group_id=user_group_id,
                            S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME'))

    except Exception as e:
        app.logger.error(f"Erro na rota /tarefas: {e}")
        flash('Ocorreu um erro ao carregar as tarefas')
        return redirect(url_for('auth.index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Aceita as tarefas
@propostas_bp.route('/aceitar_tarefa/<int:tarefa_id>')
def aceitar_tarefa(tarefa_id):
    if 'username' not in session:
        flash('Você precisa estar logado para acessar essa página.')
        return redirect(url_for('auth.index'))
    
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        username = session['username']

        # Verificar se o usuário é líder
        cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
        usuario = cursor.fetchone()
        
        if not usuario or usuario['is_leader'] != 1:
            flash('Você não tem permissão para aceitar esta tarefa.')
            return redirect(url_for('propostas.tarefas'))

        # Verificar se a tarefa existe
        cursor.execute('SELECT * FROM propostas WHERE id = %s', (tarefa_id,))
        tarefa = cursor.fetchone()
        
        if not tarefa:
            flash('Tarefa não encontrada.')
            return redirect(url_for('propostas.tarefas'))

        grupo_id = usuario['is_group']

        # Verificar se o grupo já aceitou a tarefa
        cursor.execute(
            'SELECT 1 FROM tarefa_equipes WHERE tarefa_id = %s AND grupo_id = %s',
            (tarefa_id, grupo_id)
        )
        ja_aceitou = cursor.fetchone()

        if ja_aceitou:
            flash('Seu grupo já aceitou esta tarefa.')
        else:
            try:
                cursor.execute(
                    'INSERT INTO tarefa_equipes (tarefa_id, grupo_id) VALUES (%s, %s)',
                    (tarefa_id, grupo_id)
                )
                conn.commit()
                flash('Tarefa aceita com sucesso!')
            except Exception as e:
                conn.rollback()
                app.logger.error(f"Erro ao aceitar tarefa: {e}")
                flash('Ocorreu um erro ao aceitar a tarefa.')

        return redirect(url_for('propostas.tarefas'))

    except Exception as e:
        app.logger.error(f"Erro na rota aceitar_tarefa: {e}")
        flash('Ocorreu um erro no processamento.')
        return redirect(url_for('propostas.tarefas'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Exclui as tarefas
@propostas_bp.route('/excluir_tarefa/<int:tarefa_id>')
def excluir_tarefa(tarefa_id):
    if 'username' not in session or not session.get('is_evaluator'):
        flash('Acesso não autorizado')
        return redirect(url_for('auth.index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Verifica se a tarefa existe
        cursor.execute('SELECT * FROM propostas WHERE id = %s', (tarefa_id,))
        tarefa = cursor.fetchone()

        if not tarefa:
            flash('Tarefa não encontrada')
            return redirect(url_for('propostas.tarefas'))

        # 1. Primeiro remove todos os arquivos relacionados (proposta e respostas)
        if s3_client and S3_BUCKET_NAME:
            try:
                # Remove arquivos da proposta
                proposta_prefix = f"propostas/proposta_{tarefa_id}/"
                delete_s3_prefix(proposta_prefix)
                
                # Remove arquivos de todas as respostas
                cursor.execute('SELECT id, grupo_id FROM respostas WHERE tarefa_id = %s', (tarefa_id,))
                respostas = cursor.fetchall()
                
                for resposta in respostas:
                    resposta_prefix = f"respostas/proposta_{tarefa_id}/grupo_{resposta['grupo_id']}/resposta_{resposta['id']}/"
                    delete_s3_prefix(resposta_prefix)
                    
            except Exception as e:
                app.logger.error(f"Erro ao excluir arquivos do S3: {e}")
                flash('Erro ao excluir arquivos da tarefa')
                return redirect(url_for('propostas.tarefas'))

        # 2. Remove todas as respostas da tarefa
        cursor.execute('DELETE FROM respostas WHERE tarefa_id = %s', (tarefa_id,))
        
        # 3. Remove as associações de grupos com a tarefa
        cursor.execute('DELETE FROM tarefa_equipes WHERE tarefa_id = %s', (tarefa_id,))
        
        # 4. Remove a proposta/tarefa
        cursor.execute('DELETE FROM propostas WHERE id = %s', (tarefa_id,))
        
        conn.commit()
        flash('Tarefa e todos os dados relacionados foram excluídos com sucesso!')

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao excluir tarefa {tarefa_id}: {e}")
        flash('Ocorreu um erro durante a exclusão')
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for('propostas.tarefas'))