from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import psycopg2.extras

from services.database import get_db_connection
from services.s3 import upload_file_to_s3
from utils.files import allowed_file

respostas_bp = Blueprint('respostas', __name__)


@respostas_bp.route('/resposta')
def resposta():
    # Verifica se o usuário está logado
    if 'username' not in session:
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Verifica se o usuário é um avaliador
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
                    return redirect(url_for('tempo_esgotado'))

        # Obtém o usuário logado
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('index'))

        # Obtém informações do grupo do usuário
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()

        if not user_data or not user_data['is_group']:
            return redirect(url_for('group_request_alt'))

        try:
            user_group = int(user_data['is_group'])
        except ValueError:
            return redirect(url_for('group_request_alt'))

        # Consulta as propostas aceitas pelo grupo
        cursor.execute('''
            SELECT propostas.id, propostas.nome 
            FROM propostas 
            WHERE propostas.id IN (
                SELECT tarefa_id 
                FROM tarefa_equipes 
                WHERE grupo_id = %s
            )
        ''', (user_group,))
        propostas_aceitas = cursor.fetchall()

        # Busca todas as categorias do banco de dados
        cursor.execute('SELECT id, categoria, detalhes FROM base_pontos')
        categorias = cursor.fetchall()
        categorias_dict = {categoria['id']: categoria for categoria in categorias}

        return render_template(
            'resposta.html', 
            propostas_aceitas=propostas_aceitas, 
            categorias=categorias_dict
        )

    except Exception as e:
        app.logger.error(f"Erro na rota /resposta: {e}")
        flash('Ocorreu um erro ao carregar a página de resposta')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota responsável por processar o envio da resposta
@respostas_bp.route('/enviar_resposta', methods=['POST'])
def enviar_resposta():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        user_id = session.get('user_id')

        if not user_id:
            flash('Erro: Usuário não está autenticado.')
            return redirect(url_for('index'))

        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data or not user_data['is_group']:
            flash('Erro: Usuário não pertence a um grupo.')
            return redirect(url_for('group_request_alt'))

        try:
            user_group = int(user_data['is_group'])
        except ValueError:
            flash('Erro: ID do grupo no formato inválido.')
            return redirect(url_for('group_request_alt'))

        proposta_id = request.form['proposta']
        categoria = request.form.get('categorias')
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        link = request.form.get('link', '')
        arquivos = request.files.getlist('arquivos')

        if not arquivos:
            flash('Erro: Nenhum arquivo foi enviado.')
            return redirect(url_for('resposta'))

        cursor.execute(
            '''INSERT INTO respostas 
               (tarefa_id, grupo_id, titulo, descricao, categorias, link, arquivos) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id''',
            (proposta_id, user_group, titulo, descricao, categoria, link, '')
        )
        resposta_id = cursor.fetchone()['id']
        conn.commit()

        # Salvar os arquivos no S3
        saved_files = []
        for arquivo in arquivos:
            if arquivo and allowed_file(arquivo.filename):
                filename = secure_filename(arquivo.filename)
                s3_path = f"respostas/proposta_{proposta_id}/grupo_{user_group}/resposta_{resposta_id}/{filename}"
                
                if upload_file_to_s3(arquivo, S3_BUCKET_NAME, s3_path):
                    saved_files.append(filename)

        # Atualiza a lista de arquivos no banco de dados
        cursor.execute(
            'UPDATE respostas SET arquivos = %s WHERE id = %s',
            (','.join(saved_files), resposta_id)
        )
        conn.commit()

        flash('Resposta enviada com sucesso!')
        return redirect(url_for('respostas_enviadas'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao enviar resposta: {e}")
        flash('Ocorreu um erro ao enviar sua resposta. Por favor, tente novamente.')
        return redirect(url_for('resposta'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@respostas_bp.route('/respostas_enviadas', methods=['GET'])
def respostas_enviadas():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        user_id = session.get('user_id')

        if not user_id:
            flash('Erro: Usuário não está autenticado.')
            return redirect(url_for('index'))

        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data or not user_data['is_group']:
            flash('Erro: Usuário não pertence a um grupo.')
            return redirect(url_for('group_request_alt'))

        try:
            user_group = int(user_data['is_group'])
        except ValueError:
            flash('Erro: ID do grupo no formato inválido.')
            return redirect(url_for('group_request_alt'))

        cursor.execute('''
            SELECT r.*, p.nome AS proposta_nome, p.id AS proposta_id
            FROM respostas r
            JOIN propostas p ON r.tarefa_id = p.id
            WHERE r.grupo_id = %s
        ''', (user_group,))
        respostas = cursor.fetchall()

        cursor.execute('SELECT id, categoria FROM base_pontos')
        categorias = cursor.fetchall()
        categorias_dict = {categoria['id']: categoria['categoria'] for categoria in categorias}

        respostas_com_arquivos = []
        for resposta in respostas:
            arquivos = []
            if resposta['arquivos']:
                arquivos = [f.strip() for f in resposta['arquivos'].split(',') if f.strip()]

            respostas_com_arquivos.append({
                'id': resposta['id'],
                'tarefa_id': resposta['tarefa_id'],
                'proposta_id': resposta['proposta_id'],
                'titulo': resposta['titulo'],
                'descricao': resposta['descricao'],
                'categorias': resposta['categorias'],
                'link': resposta.get('link', ''),
                'arquivos': arquivos,
                'proposta_nome': resposta['proposta_nome'],
                'grupo_id': user_group,
                'data_envio': resposta.get('created_at', ''),
                'caminho_base': f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/respostas/proposta_{resposta['proposta_id']}/grupo_{user_group}/resposta_{resposta['id']}"
            })

        return render_template(
            'respostas_enviadas.html',
            respostas=respostas_com_arquivos,
            categorias=categorias_dict,
            S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME')
        )

    except Exception as e:
        app.logger.error(f"Erro na rota respostas_enviadas: {e}")
        flash('Ocorreu um erro ao carregar as respostas enviadas.')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
