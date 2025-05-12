from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, current_app
import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
import hashlib
import shutil
from werkzeug.utils import secure_filename
from datetime import datetime
from psycopg2 import extras, pool
import psycopg2.extras
import boto3

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Carrega variáveis de ambiente
load_dotenv()

# Configuração de caminhos
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # Limite de 50MB para uploads

# Certifique-se que o diretório de upload existe
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configuração do AWS S3
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-2')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

# Configuração do cliente S3
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
) if all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME]) else None

if not s3_client:
    app.logger.warning("Configuração do S3 incompleta - funcionalidades de upload serão limitadas")

ALLOWED_EXTENSIONS = {
    # Imagens
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'tiff', 'ico',
    # Vídeos
    'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv',
    # Áudio
    'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a',
    # Documentos
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'odt',
    # Compactados
    'zip', 'rar', '7z', 'tar', 'gz',
}

def get_db_connection():
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('POSTGRES_DB', 'postgres'),
            user=os.getenv('POSTGRES_USER', 'postgres'),
            password=os.getenv('POSTGRES_PASSWORD', 'CODR@@stro7410'),
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=os.getenv('POSTGRES_PORT', '5433')
        )
        return conn
    except psycopg2.OperationalError as e:
        app.logger.error(f"Erro ao conectar ao PostgreSQL: {e}")
        raise

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_file_to_s3(file, bucket_name, s3_path):
    """Faz upload de um arquivo para o S3"""
    if not s3_client:
        app.logger.error("Tentativa de upload sem cliente S3 configurado")
        return False
        
    try:
        s3_client.upload_fileobj(
            file,
            bucket_name,
            s3_path,
            ExtraArgs={
                'ACL': 'public-read',
                'ContentType': file.content_type
            }
        )
        return True
    except Exception as e:
        app.logger.error(f"Erro ao fazer upload para S3: {e}")
        return False

def generate_presigned_url(bucket_name, object_name, expiration=3600):
    """Gera uma URL assinada para acesso temporário ao arquivo"""
    if not s3_client:
        return None
        
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                  Params={'Bucket': bucket_name,
                                                          'Key': object_name},
                                                  ExpiresIn=expiration)
        return response
    except Exception as e:
        app.logger.error(f"Erro ao gerar URL assinada: {e}")
        return None

def delete_file_from_s3(bucket_name, object_name):
    """Remove um arquivo do S3"""
    if not s3_client:
        return False
        
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=object_name)
        return True
    except Exception as e:
        app.logger.error(f"Erro ao deletar arquivo do S3: {e}")
        return False

# Página de criação de propostas (acesso restrito a avaliadores)
@app.route('/proposta', methods=['GET', 'POST'])
def proposta():
    if 'username' not in session or session.get('is_evaluator') != 1:
        return redirect(url_for('index'))

    if request.method == 'POST':
        proposta_nome = request.form['proposta_nome']
        descricao = request.form['descricao']
        arquivos = request.files.getlist('arquivos')

        if not proposta_nome or not descricao:
            flash('Nome e descrição da proposta são obrigatórios!')
            return redirect(url_for('proposta'))

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
            return redirect(url_for('tarefas'),
                   S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME'))
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Erro ao criar proposta: {e}")
            flash('Ocorreu um erro ao criar a proposta.')
            return redirect(url_for('proposta'))
        finally:
            cursor.close()
            conn.close()

    return render_template('proposta.html')

@app.route('/iniciar_cronometro', methods=['POST'])
def iniciar_cronometro():
    data = request.get_json()
    horas = data.get('horas', 0)
    minutos = data.get('minutos', 0)
    total_time = (horas * 3600) + (minutos * 60)

    if total_time <= 0:
        return jsonify({'erro': 'Tempo inválido'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Remove qualquer registro existente
        cursor.execute('DELETE FROM cronometro')
        # Insere o novo registro
        cursor.execute(
            'INSERT INTO cronometro (start_time, total_time) VALUES (%s, %s)',
            (datetime.now(), total_time)
        )
        conn.commit()
        return jsonify({'mensagem': 'Cronômetro iniciado'}), 200
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Erro ao iniciar cronômetro: {e}")
        return jsonify({'erro': 'Falha ao iniciar cronômetro'}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/tempo_restante')
def tempo_restante():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute('SELECT start_time, total_time FROM cronometro LIMIT 1')
        registro = cursor.fetchone()

        if not registro:
            return jsonify({'tempo_restante': 0})

        # No PostgreSQL, start_time já vem como objeto datetime
        start_time = registro['start_time']
        total_time = registro['total_time']
        tempo_passado = (datetime.now() - start_time).total_seconds()
        tempo_restante = max(total_time - tempo_passado, 0)

        return jsonify({'tempo_restante': tempo_restante})
    except Exception as e:
        app.logger.error(f"Erro ao verificar tempo restante: {e}")
        return jsonify({'erro': 'Falha ao verificar tempo restante'}), 500
    finally:
        cursor.close()
        conn.close()

# Rota para a página de tempo esgotado
@app.route('/tempo')
def tempo_esgotado():
    return render_template('tempo.html')

# Página para listar as tarefas
@app.route('/tarefas')
def tarefas():
    # Verifica se o usuário está logado
    if 'username' not in session:
        return redirect(url_for('index'))

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
                    return redirect(url_for('tempo_esgotado'))

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
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota de verificação Comum-Avaliador
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form['username']
        password = hashlib.sha256(request.form['password'].encode()).hexdigest()
        
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            cursor.execute(
                'SELECT * FROM users WHERE username = %s AND password = %s', 
                (username, password)
            )
            user = cursor.fetchone()
            
            if user:
                session['user_id'] = user['id']
                session['username'] = username
                session['is_evaluator'] = user['is_evaluator']  # Armazena o indicador de avaliador na sessão
                
                # Redireciona com base em 'is_evaluator'
                if user['is_evaluator'] == 1:
                    return redirect(url_for('home_avaliador'))
                else:
                    return redirect(url_for('home'))
            else:
                return render_template('index.html', error='Usuário ou senha incorretos.')
        
        except Exception as e:
            app.logger.error(f"Erro durante o login: {e}")
            return render_template('index.html', error='Ocorreu um erro durante o login. Tente novamente.')
        
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    return render_template('index.html')

@app.route('/resposta')
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

@app.route('/group_request_alt')
def group_request_alt():
    return render_template('group_request_alt.html')

# Rota responsável por processar o envio da resposta
@app.route('/enviar_resposta', methods=['POST'])
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

# Nova rota para exibir as respostas enviadas
@app.route('/respostas_enviadas', methods=['GET'])
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

# Rotas para excluir e aceitar tarefas
@app.route('/excluir_tarefa/<int:tarefa_id>')
def excluir_tarefa(tarefa_id):
    if 'username' not in session or not session.get('is_evaluator'):
        flash('Acesso não autorizado')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cursor.execute('SELECT * FROM propostas WHERE id = %s', (tarefa_id,))
        tarefa = cursor.fetchone()

        if not tarefa:
            flash('Tarefa não encontrada')
            return redirect(url_for('tarefas'))

        # Remover arquivos relacionados à tarefa no S3
        try:
            # Lista todos os objetos no prefixo da proposta
            response = s3_client.list_objects_v2(
                Bucket=S3_BUCKET_NAME,
                Prefix=f"propostas/proposta_{tarefa_id}/"
            )
            
            if 'Contents' in response:
                for obj in response['Contents']:
                    s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=obj['Key'])
                    
            # Também remove respostas relacionadas
            response = s3_client.list_objects_v2(
                Bucket=S3_BUCKET_NAME,
                Prefix=f"respostas/proposta_{tarefa_id}/"
            )
            
            if 'Contents' in response:
                for obj in response['Contents']:
                    s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=obj['Key'])
        except Exception as e:
            app.logger.error(f"Erro ao excluir arquivos do S3: {e}")
            flash('Erro ao excluir arquivos da tarefa')

        try:
            cursor.execute('DELETE FROM tarefa_equipes WHERE tarefa_id = %s', (tarefa_id,))
            cursor.execute('DELETE FROM propostas WHERE id = %s', (tarefa_id,))
            conn.commit()
            flash('Tarefa e registros associados excluídos com sucesso!')
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Erro ao excluir tarefa: {e}")
            flash('Erro ao excluir tarefa do banco de dados')

    except Exception as e:
        app.logger.error(f"Erro geral na exclusão: {e}")
        flash('Ocorreu um erro durante a exclusão')
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for('tarefas'))

@app.route('/aceitar_tarefa/<int:tarefa_id>')
def aceitar_tarefa(tarefa_id):
    if 'username' not in session:
        flash('Você precisa estar logado para acessar essa página.')
        return redirect(url_for('index'))
    
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
            return redirect(url_for('tarefas'))

        # Verificar se a tarefa existe
        cursor.execute('SELECT * FROM propostas WHERE id = %s', (tarefa_id,))
        tarefa = cursor.fetchone()
        
        if not tarefa:
            flash('Tarefa não encontrada.')
            return redirect(url_for('tarefas'))

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

        return redirect(url_for('tarefas'))

    except Exception as e:
        app.logger.error(f"Erro na rota aceitar_tarefa: {e}")
        flash('Ocorreu um erro no processamento.')
        return redirect(url_for('tarefas'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rotas de login e cadastro
@app.route('/signup', methods=['POST'])
def signup():
    # Obter os dados do formulário
    username = request.form.get('new-username')
    password = request.form.get('new-password')
    confirm_password = request.form.get('confirm-password')
    role = request.form.get('role')  # Pode ser "evaluator" ou "student"
    evaluator_email = request.form.get('evaluator-email') if role == 'evaluator' else None
    auth_key = request.form.get('auth-key') if role == 'evaluator' else None
    student_auth_key = request.form.get('student-auth-key') if role == 'student' else None

    # Verificar se todos os campos necessários foram preenchidos
    if not username or not password or not confirm_password:
        return render_template('index.html', error='Todos os campos devem ser preenchidos.')

    # Verificar se as senhas coincidem
    if password != confirm_password:
        return render_template('index.html', error='As senhas não coincidem.')

    # Verificar se uma opção foi selecionada
    if not role:
        return render_template('index.html', error='Por favor, selecione uma opção: "Sou Avaliador" ou "Sou Aluno".')

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verificar a chave de autenticação com base no papel selecionado
        if role == 'evaluator':
            if not auth_key or not evaluator_email:
                return render_template('index.html', error='Por favor, preencha a chave de autenticação e o e-mail para avaliadores.')

            cursor.execute('SELECT auth_key FROM evaluator_key LIMIT 1')
            stored_key = cursor.fetchone()
            
            if not stored_key or stored_key[0] != auth_key:
                return render_template('index.html', error='Chave de autenticação inválida para avaliadores.')
        
        elif role == 'student':
            if not student_auth_key:
                return render_template('index.html', error='Por favor, insira a chave de autenticação para alunos.')

            cursor.execute('SELECT auth_key FROM aluno_key LIMIT 1')
            stored_student_key = cursor.fetchone()
            
            if not stored_student_key or stored_student_key[0] != student_auth_key:
                return render_template('index.html', error='Chave de autenticação inválida para alunos.')

        # Criptografar a senha antes de salvar
        hashed_password = hashlib.sha256(password.encode()).hexdigest()

        # Inserir o usuário no banco de dados
        try:
            cursor.execute(
                'INSERT INTO users (username, password, is_evaluator, evaluator_email) VALUES (%s, %s, %s, %s)',
                (username, hashed_password, 1 if role == 'evaluator' else 0, evaluator_email)
            )
            conn.commit()
            flash('Cadastro realizado com sucesso! Faça login para continuar.')
            return redirect(url_for('index'))
        
        except psycopg2.errors.UniqueViolation:
            return render_template('index.html', error='Usuário já existe.')
        
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Erro ao cadastrar usuário: {e}")
            return render_template('index.html', error='Ocorreu um erro ao cadastrar. Tente novamente.')

    except Exception as e:
        app.logger.error(f"Erro no processo de cadastro: {e}")
        return render_template('index.html', error='Ocorreu um erro no processamento.')
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return render_template('index.html', error='Todos os campos devem ser preenchidos.')

    # Criptografar a senha para comparação
    hashed_password = hashlib.sha256(password.encode()).hexdigest()

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Consulta segura usando parâmetros
        cursor.execute(
            'SELECT id, username, is_evaluator FROM users WHERE username = %s AND password = %s',
            (username, hashed_password)
        )
        user = cursor.fetchone()

        if user:
            # Configurar sessão do usuário
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_evaluator'] = user['is_evaluator']
            
            # Redirecionar conforme o tipo de usuário
            if user['is_evaluator']:
                return redirect(url_for('home_avaliador'))
            return redirect(url_for('home'))
        else:
            return render_template('index.html', error='Usuário ou senha incorretos.')

    except Exception as e:
        app.logger.error(f"Erro durante o login: {e}")
        return render_template('index.html', error='Ocorreu um erro durante o login. Tente novamente.')
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rotas da página HOME
@app.route('/home')
def home():
    if 'username' not in session:
        return redirect(url_for('index'))
    if session.get('is_evaluator') == 1:
        return redirect(url_for('home_avaliador'))
    return render_template('home.html', username=session['username'])

@app.route('/home-avaliador')
def home_avaliador():
    if 'username' not in session or session.get('is_evaluator') != 1:
        return redirect(url_for('index'))
    return render_template('home-avaliador.html', username=session['username'])

@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/graficos')
def graficos():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Consulta otimizada para PostgreSQL
        cursor.execute('''
            SELECT 
                g.id, 
                g.name, 
                COALESCE(SUM(r.pontuacao), 0) AS total_pontos,
                COUNT(r.id) AS total_respostas
            FROM groups g
            LEFT JOIN respostas r ON g.id = r.grupo_id AND r.is_avaliada = 1
            GROUP BY g.id, g.name
            ORDER BY total_pontos DESC, g.name ASC
        ''')

        grupos = cursor.fetchall()

        # Prepara os dados
        grupos_nomes = [grupo['name'] for grupo in grupos]
        grupos_pontos = [float(grupo['total_pontos']) for grupo in grupos]  # Convertendo para float
        grupos_respostas = [grupo['total_respostas'] for grupo in grupos]

        return render_template('graficos.html', 
                            grupos=grupos_nomes, 
                            pontos=grupos_pontos,
                            respostas=grupos_respostas)

    except Exception as e:
        app.logger.error(f"Erro ao gerar gráficos: {e}")
        flash('Ocorreu um erro ao carregar os dados dos gráficos.')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para exibir as notificações
@app.route('/notificacoes')
def notificacoes():
    if 'username' not in session:
        flash('Por favor, faça login para acessar suas notificações.')
        return redirect(url_for('index'))
    
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
        app.logger.error(f"Erro ao carregar notificações: {e}")
        flash('Ocorreu um erro ao carregar suas notificações.')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/notifications/<int:notification_id>/accept', methods=['GET', 'POST'])
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
            return redirect(url_for('notificacoes'))

        user_id = notification['user_id']
        group_id = notification['group_id']

        # Obtém o nome do grupo
        cursor.execute('SELECT name FROM groups WHERE id = %s', (group_id,))
        group = cursor.fetchone()
        
        if group is None:
            flash('Grupo não encontrado.')
            return redirect(url_for('notificacoes'))

        group_name = group['name']

        # Verifica se o usuário já faz parte de um grupo
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        
        if user is None:
            flash('Usuário não encontrado.')
            return redirect(url_for('notificacoes'))

        if user['is_group'] is not None and str(user['is_group']).lower() != 'none':
            # Usuário já em um grupo - cria notificação
            cursor.execute('''
                INSERT INTO notifications (user_id, message) 
                VALUES (%s, %s)
            ''', (user_id, 'Você já faz parte de um grupo e não pode aceitar novos convites.'))
            
            # Remove notificação original
            cursor.execute('DELETE FROM notifications WHERE id = %s', (notification_id,))
            
            conn.commit()
            flash('Você já faz parte de um grupo e não pode aceitar novos convites.')
            return redirect(url_for('notificacoes'))

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
            cursor.execute('''
                INSERT INTO notifications (user_id, message, group_id)
                VALUES (%s, %s, %s)
            ''', (user_id, f'O grupo {group_name} está cheio. Você não pôde ser adicionado.', group_id))

            conn.commit()
            flash('O grupo está cheio. Você não pôde ser adicionado.')
            return redirect(url_for('notificacoes'))

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
        cursor.execute('''
            INSERT INTO notifications (user_id, message) 
            VALUES (%s, %s)
        ''', (user_id, f'Você foi aceito no grupo {group_name}.'))

        # Atualiza status do usuário
        cursor.execute('''
            UPDATE users 
            SET is_member = 1, is_group = %s 
            WHERE id = %s
        ''', (group_id, user_id))

        conn.commit()
        flash('Solicitação aceita e usuário adicionado ao grupo.')
        return redirect(url_for('notificacoes'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao processar aceitação de convite: {e}")
        flash('Ocorreu um erro ao processar sua solicitação.')
        return redirect(url_for('notificacoes'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/notifications/<int:notification_id>/reject', methods=['GET', 'POST'])
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
            return redirect(url_for('notificacoes'))

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
        cursor.execute('''
            INSERT INTO notifications (user_id, message) 
            VALUES (%s, %s)
        ''', (user_id, f'Sua solicitação para participar do grupo {group_name} foi recusada.'))

        conn.commit()
        flash('Solicitação recusada com sucesso.')
        return redirect(url_for('notificacoes'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao rejeitar solicitação: {e}")
        flash('Ocorreu um erro ao processar a recusa.')
        return redirect(url_for('notificacoes'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para excluir uma notificação
@app.route('/notifications/<int:notification_id>/delete', methods=['POST'])
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
            return redirect(url_for('notificacoes'))

        user_id = notification['user_id']
        group_id = notification['group_id']

        # Se for notificação de convite, remove do grupo com status 'Aguardando'
        if group_id is not None:
            cursor.execute('''
                DELETE FROM group_members 
                WHERE group_id = %s AND user_id = %s AND status = 'Aguardando'
            ''', (group_id, user_id))

            # Opcional: Notificar o usuário sobre o cancelamento
            cursor.execute('''
                INSERT INTO notifications (user_id, message)
                VALUES (%s, %s)
            ''', (user_id, 'Um convite de grupo foi cancelado pelo remetente.'))

        # Exclui a notificação original
        cursor.execute('DELETE FROM notifications WHERE id = %s', (notification_id,))

        conn.commit()
        flash('Notificação removida com sucesso.')
        return redirect(url_for('notificacoes'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao excluir notificação: {e}")
        flash('Ocorreu um erro ao excluir a notificação.')
        return redirect(url_for('notificacoes'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para verificar novas notificações (usada pelo JavaScript)
@app.route('/notificacoes/verificar')
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
        app.logger.error(f"Erro ao verificar notificações: {e}")
        return jsonify({'has_new': False, 'error': 'Erro ao verificar notificações'})
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Função para adicionar uma nova notificação
def add_notification(user_id, message, group_id=None):
    """
    Adiciona uma nova notificação para o usuário, mantendo no máximo 10 notificações
    por usuário (remove a mais antiga se necessário).
    
    Args:
        user_id (int): ID do usuário destinatário
        message (str): Mensagem da notificação
        group_id (int, optional): ID do grupo relacionado. Defaults to None.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verifica quantidade de notificações existentes
        cursor.execute('SELECT COUNT(*) FROM notifications WHERE user_id = %s', (user_id,))
        count = cursor.fetchone()[0]
        
        # Remove a mais antiga se houver 10 ou mais notificações
        if count >= 10:
            cursor.execute('''
                SELECT id FROM notifications 
                WHERE user_id = %s 
                ORDER BY created_at ASC 
                LIMIT 1
            ''', (user_id,))
            oldest_notification = cursor.fetchone()
            
            if oldest_notification:
                cursor.execute('DELETE FROM notifications WHERE id = %s', (oldest_notification[0],))

        # Adiciona a nova notificação
        if group_id:
            cursor.execute('''
                INSERT INTO notifications 
                (user_id, message, read, created_at, group_id) 
                VALUES (%s, %s, 0, NOW(), %s)
            ''', (user_id, message, group_id))
        else:
            cursor.execute('''
                INSERT INTO notifications 
                (user_id, message, read, created_at) 
                VALUES (%s, %s, 0, NOW())
            ''', (user_id, message))
        
        conn.commit()

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao adicionar notificação: {e}")
        raise  # Re-lança a exceção para ser tratada pelo chamador
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Novas rotas para grupos
@app.route('/groups', methods=['GET', 'POST'])
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
        cursor.execute('SELECT is_leader, is_member, is_group, is_evaluator FROM users WHERE id = %s', (user_id,))
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
            cursor.execute('''
                INSERT INTO groups (name, created_by) 
                VALUES (%s, %s) 
                RETURNING id
            ''', (group_name, created_by))
            group_id = cursor.fetchone()['id']

            # Adiciona o criador como líder
            cursor.execute('''
                INSERT INTO group_members (group_id, user_id, status) 
                VALUES (%s, %s, %s)
            ''', (group_id, created_by, "Líder"))

            # Atualiza o status do criador
            cursor.execute('''
                UPDATE users 
                SET is_leader = 1, is_group = %s 
                WHERE id = %s
            ''', (group_id, created_by))

            # Remove o criador de outros grupos pendentes
            cursor.execute('''
                DELETE FROM group_members
                WHERE user_id = %s AND status IN ('Aguardando', 'Solicitando') AND group_id != %s
            ''', (created_by, group_id))

            # Processa os membros convidados
            for member_id in members:
                member_id = int(member_id)
                if member_id != created_by:
                    # Cria notificação
                    message = f'Você foi convidado para o grupo {group_name}.'
                    cursor.execute('''
                        INSERT INTO notifications (user_id, message, group_id)
                        VALUES (%s, %s, %s)
                        RETURNING id
                    ''', (member_id, message, group_id))
                    notification_id = cursor.fetchone()['id']

                    # Atualiza a mensagem com links
                    accept_url = url_for('accept_member_request', notification_id=notification_id)
                    reject_url = url_for('reject_member_request', notification_id=notification_id)
                    cursor.execute('''
                        UPDATE notifications 
                        SET message = %s 
                        WHERE id = %s
                    ''', (f'{message} <a href="{accept_url}">Aceitar</a> ou <a href="{reject_url}">Recusar</a>', notification_id))

                    # Adiciona como "Aguardando" confirmação
                    cursor.execute('''
                        INSERT INTO group_members (group_id, user_id, status)
                        VALUES (%s, %s, 'Aguardando')
                    ''', (group_id, member_id))

            conn.commit()
            flash('Grupo criado com sucesso!')
            return redirect(url_for('groups'))

        # GET Request - Mostrar grupos e usuários disponíveis
        # Filtra usuários que podem ser adicionados
        cursor.execute('''
            SELECT id, username 
            FROM users 
            WHERE is_member = 0 
            AND is_leader = 0 
            AND is_evaluator = 0
        ''')
        filtered_users = cursor.fetchall()

        # Obtém todos os grupos com informação se estão cheios
        cursor.execute('''
            SELECT g.*, 
                   (SELECT COUNT(*) 
                    FROM group_members 
                    WHERE group_id = g.id AND status IN ('Líder', 'Membro')) AS member_count
            FROM groups g
        ''')
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
@app.route('/groups/<int:group_id>/delete', methods=['POST'])
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
            
            # Remove os arquivos físicos das respostas
            for resposta in respostas:
                resposta_path = os.path.join(
                    app.config['UPLOAD_FOLDER'],
                    f'proposta_{resposta["tarefa_id"]}',
                    f'grupo_{group_id}',
                    f'resposta_{resposta["id"]}'
                )
                if os.path.exists(resposta_path):
                    try:
                        shutil.rmtree(resposta_path)
                    except OSError as e:
                        app.logger.error(f"Erro ao remover arquivos da resposta {resposta['id']}: {e}")
            
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
@app.route('/groups/<int:group_id>/kick/<int:user_id>', methods=['POST'])
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
            cursor.execute('''
                INSERT INTO notifications (user_id, message)
                VALUES (%s, %s)
            ''', (user_id, f'Você foi removido do grupo {group_id} pelo líder.'))
            
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
@app.route('/groups/<int:group_id>/leave', methods=['POST'])
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

@app.route('/groups/<int:group_id>')
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

@app.route('/groups/<int:group_id>/request', methods=['POST'])
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
            cursor.execute('''
                INSERT INTO notifications (user_id, message, group_id)
                VALUES (%s, %s, %s)
            ''', (leader['created_by'], f'Novo pedido de entrada no grupo de {session["username"]}.', group_id))

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
@app.route('/groups/<int:group_id>/add_members', methods=['POST'])
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
            cursor.execute('''
                INSERT INTO notifications (user_id, message, group_id)
                VALUES (%s, %s, %s)
                RETURNING id
            ''', (member_id, f'Você foi convidado para o grupo {group_name}.', group_id))
            notification_id = cursor.fetchone()['id']

            # Agora gera os URLs com o notification_id real
            accept_url = url_for('accept_member_request', notification_id=notification_id, _external=True)
            reject_url = url_for('reject_member_request', notification_id=notification_id, _external=True)
            
            # Atualiza a notificação com URLs completos
            cursor.execute('''
                UPDATE notifications 
                SET message = %s 
                WHERE id = %s
            ''', (
                f'Você foi convidado para o grupo {group_name}. '
                f'<a href="{accept_url}">Aceitar</a> ou '
                f'<a href="{reject_url}">Recusar</a>',
                notification_id
            ))

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

@app.route('/groups/<int:group_id>/accept/<int:user_id>', methods=['POST'])
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
        cursor.execute('''
            INSERT INTO notifications (user_id, message)
            VALUES (%s, %s)
        ''', (user_id, f'Seu pedido para o grupo {group_name} foi aceito!'))

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

@app.route('/groups/<int:group_id>/reject/<int:user_id>', methods=['POST'])
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
        cursor.execute('''
            INSERT INTO notifications (user_id, message)
            VALUES (%s, %s)
        ''', (user_id, f'Seu pedido para o grupo {group_name} foi recusado.'))

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

@app.route('/avaliar_respostas', methods=['GET', 'POST'])
def avaliar_respostas():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        user_id = session.get('user_id')

        # Verificar autenticação e permissões (mantendo 1/0 para booleanos)
        if not user_id:
            flash('Erro: Usuário não está autenticado.', 'error')
            return redirect(url_for('index'))

        cursor.execute('SELECT is_evaluator FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data or user_data['is_evaluator'] == 0:
            flash('Erro: Apenas avaliadores podem acessar esta página.', 'error')
            return redirect(url_for('index'))

        # Buscar categorias de pontuação
        cursor.execute('SELECT id, categoria, valor, detalhes FROM base_pontos')
        categorias = cursor.fetchall()
        categorias_dict = {categoria['id']: categoria for categoria in categorias}

        # Parâmetros da requisição
        proposta_id = request.args.get('proposta_id', type=int)
        grupo_id = request.args.get('grupo_id', type=int)
        resposta_id = request.args.get('resposta_id', type=int)

        # Processar POST (avaliação de resposta) - mantendo 1/0 para booleanos
        if request.method == 'POST':
            acao = request.form.get('acao')
            observacao = request.form.get('observacao', '')
            resposta_id = request.form.get('resposta_id', type=int)

            if resposta_id:
                if acao == 'aceitar':
                    cursor.execute('SELECT categorias FROM respostas WHERE id = %s', (resposta_id,))
                    resposta = cursor.fetchone()
                    pontuacao = 0
                    
                    if resposta and resposta['categorias']:
                        categoria_id = resposta['categorias'].split(',')[0]
                        pontuacao = categorias_dict[int(categoria_id)]['valor']

                    cursor.execute('''
                        UPDATE respostas SET 
                            is_avaliada = 1, 
                            is_reject = 0, 
                            is_modify = 0, 
                            observacao = %s, 
                            pontuacao = %s 
                        WHERE id = %s
                    ''', (observacao, pontuacao, resposta_id))

                elif acao == 'aceitar_com_alteracoes':
                    categoria_nova = request.form.get('categorias_novas')
                    pontuacao = categorias_dict[int(categoria_nova)]['valor'] if categoria_nova else 0

                    cursor.execute('''
                        UPDATE respostas SET 
                            is_avaliada = 1, 
                            is_reject = 0, 
                            is_modify = 1, 
                            observacao = %s, 
                            categorias = %s, 
                            pontuacao = %s 
                        WHERE id = %s
                    ''', (observacao, categoria_nova, pontuacao, resposta_id))

                elif acao == 'rejeitar':
                    cursor.execute('''
                        UPDATE respostas SET 
                            is_avaliada = 1, 
                            is_reject = 1, 
                            is_modify = 0, 
                            observacao = %s, 
                            pontuacao = 0 
                        WHERE id = %s
                    ''', (observacao, resposta_id))

                conn.commit()
                flash('Avaliação registrada com sucesso!', 'success')
                return redirect(url_for('avaliar_respostas', proposta_id=proposta_id, grupo_id=grupo_id))

        # Fluxo GET - Navegação hierárquica (mantendo comparações com 1/0)
        if not proposta_id:
            cursor.execute('''
                SELECT p.id, p.nome,
                    COUNT(CASE WHEN r.is_avaliada = 0 THEN 1 END) AS pendentes,
                    COUNT(CASE WHEN r.is_avaliada = 1 THEN 1 END) AS avaliadas
                FROM propostas p
                LEFT JOIN respostas r ON p.id = r.tarefa_id
                WHERE p.avaliador_id = %s
                GROUP BY p.id, p.nome
            ''', (user_id,))
            propostas = cursor.fetchall()
            return render_template('avaliar_respostas.html', 
                                 propostas=propostas, 
                                 categorias=categorias_dict)

        if not grupo_id:
            cursor.execute('''
                SELECT g.id, g.name,
                    COUNT(CASE WHEN r.is_avaliada = 0 AND r.tarefa_id = %s THEN 1 END) AS pendentes,
                    COUNT(CASE WHEN r.is_avaliada = 1 AND r.tarefa_id = %s THEN 1 END) AS avaliadas
                FROM groups g
                JOIN tarefa_equipes te ON g.id = te.grupo_id
                LEFT JOIN respostas r ON g.id = r.grupo_id AND r.tarefa_id = %s
                WHERE te.tarefa_id = %s
                GROUP BY g.id, g.name
            ''', (proposta_id, proposta_id, proposta_id, proposta_id))
            grupos = cursor.fetchall()
            return render_template('avaliar_respostas.html', 
                                 grupos=grupos, 
                                 proposta_id=proposta_id, 
                                 categorias=categorias_dict)

        if not resposta_id:
            cursor.execute('''
                SELECT r.*, p.nome AS proposta_nome
                FROM respostas r
                JOIN propostas p ON r.tarefa_id = p.id
                WHERE r.grupo_id = %s AND r.tarefa_id = %s AND r.is_avaliada = 0
            ''', (grupo_id, proposta_id))
            respostas = cursor.fetchall()
            return render_template('avaliar_respostas.html', 
                                 respostas=respostas, 
                                 proposta_id=proposta_id, 
                                 grupo_id=grupo_id, 
                                 categorias=categorias_dict)

        # Detalhes da resposta específica
        cursor.execute('''
            SELECT r.*, p.nome AS proposta_nome, g.name AS grupo_nome
            FROM respostas r
            JOIN propostas p ON r.tarefa_id = p.id
            JOIN groups g ON r.grupo_id = g.id
            WHERE r.id = %s
        ''', (resposta_id,))
        resposta = cursor.fetchone()

        # Buscar arquivos da resposta (código mantido igual)
        resposta_path = os.path.join(
            app.config['UPLOAD_FOLDER'],
            f'proposta_{resposta["tarefa_id"]}',
            f'grupo_{resposta["grupo_id"]}',
            f'resposta_{resposta["id"]}'
        )
        arquivos = []
        if os.path.exists(resposta_path):
            try:
                arquivos = [f for f in os.listdir(resposta_path) 
                          if os.path.isfile(os.path.join(resposta_path, f))]
            except OSError as e:
                app.logger.error(f"Erro ao acessar arquivos da resposta: {e}")

        return render_template('avaliar_respostas.html',
                            resposta=resposta,
                            proposta_id=proposta_id,
                            grupo_id=grupo_id,
                            categorias=categorias_dict,
                            arquivos=arquivos,
                            S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME'))

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro em avaliar_respostas: {e}")
        flash('Ocorreu um erro ao processar sua solicitação.', 'error')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/marcar_favorito/<int:resposta_id>', methods=['POST'])
def marcar_favorito(resposta_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Usuário não autenticado'}), 401

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Verifica se a resposta existe e obtém o status atual
        cursor.execute('SELECT is_favor FROM respostas WHERE id = %s', (resposta_id,))
        resposta = cursor.fetchone()
        
        if resposta:
            # Alterna entre 1 e 0 (mantendo valores numéricos)
            novo_valor = 1 if resposta['is_favor'] == 0 else 0
            
            # Atualiza o status
            cursor.execute('''
                UPDATE respostas 
                SET is_favor = %s 
                WHERE id = %s
            ''', (novo_valor, resposta_id))
            
            conn.commit()
            return jsonify({
                'success': True,
                'is_favor': novo_valor,
                'message': 'Resposta marcada como favorita' if novo_valor == 1 else 'Resposta desmarcada como favorita'
            })

        return jsonify({'success': False, 'error': 'Resposta não encontrada'}), 404

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao marcar favorito: {e}")
        return jsonify({'success': False, 'error': 'Erro interno no servidor'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Rota para a página de pontuação
@app.route('/pontuacao')
def pontuacao():
    if 'user_id' not in session:
        flash('Por favor, faça login para acessar esta página.', 'error')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        user_id = session['user_id']

        # Verifica grupo do usuário
        cursor.execute('SELECT is_group FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data or user_data['is_group'] == 'none':
            flash('Você precisa estar em um grupo para ver pontuações.', 'error')
            return redirect(url_for('group_request_alt'))

        grupo_id = user_data['is_group']

        # Busca propostas do grupo
        cursor.execute('''
            SELECT p.id, p.nome, p.descricao
            FROM propostas p
            JOIN tarefa_equipes te ON p.id = te.tarefa_id
            WHERE te.grupo_id = %s
        ''', (grupo_id,))
        propostas_aceitas = cursor.fetchall()

        propostas_com_pontuacao = []
        for proposta in propostas_aceitas:
            # Busca respostas para cada proposta
            cursor.execute('''
                SELECT id, titulo, pontuacao, is_avaliada
                FROM respostas
                WHERE tarefa_id = %s AND grupo_id = %s
            ''', (proposta['id'], grupo_id))
            respostas = cursor.fetchall()

            # Calcula pontuação (mantendo 1 para true)
            pontuacao_total = sum(
                resposta['pontuacao'] for resposta in respostas 
                if resposta['is_avaliada'] == 1
            )

            # Separa respostas
            respostas_avaliadas = [
                resposta for resposta in respostas 
                if resposta['is_avaliada'] == 1
            ]
            respostas_nao_avaliadas = [
                resposta for resposta in respostas 
                if resposta['is_avaliada'] == 0
            ]

            propostas_com_pontuacao.append({
                'id': proposta['id'],
                'nome': proposta['nome'],
                'descricao': proposta['descricao'],
                'pontuacao_total': pontuacao_total,
                'respostas_avaliadas': respostas_avaliadas,
                'respostas_nao_avaliadas': respostas_nao_avaliadas
            })

        return render_template('pontuacao.html', 
               propostas=propostas_com_pontuacao,
               S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME'))

    except Exception as e:
        app.logger.error(f"Erro na rota pontuacao: {e}")
        flash('Ocorreu um erro ao carregar as pontuações.', 'error')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/pontuacao_avaliador')
def pontuacao_avaliador():
    # Verificação de autenticação
    if 'user_id' not in session:
        flash('Por favor, faça login para acessar esta página.', 'error')
        return redirect(url_for('index'))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        user_id = session['user_id']

        # Verifica se o usuário é avaliador (usando 1/0 para booleanos)
        cursor.execute('SELECT is_evaluator FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data or user_data['is_evaluator'] == 0:
            flash('Acesso restrito a avaliadores.', 'error')
            return redirect(url_for('index'))

        # Busca todas as propostas (tarefas)
        cursor.execute('SELECT id, nome, descricao FROM propostas')
        propostas = cursor.fetchall()

        # Debug opcional (remova em produção)
        app.logger.debug(f"Propostas encontradas: {propostas}")

        return render_template('pontuacao_avaliador.html', propostas=propostas)

    except Exception as e:
        app.logger.error(f"Erro em pontuacao_avaliador: {e}")
        flash('Ocorreu um erro ao carregar as propostas.', 'error')
        return redirect(url_for('index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/get_grupos_por_proposta/<int:proposta_id>')
def get_grupos_por_proposta(proposta_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Consulta para obter grupos associados à proposta
        cursor.execute('''
            SELECT g.id, g.name AS nome
            FROM groups g
            JOIN tarefa_equipes te ON g.id = te.grupo_id
            WHERE te.tarefa_id = %s
            ORDER BY g.name
        ''', (proposta_id,))
        
        grupos = cursor.fetchall()
        
        # Converter para lista de dicionários
        grupos_list = []
        for grupo in grupos:
            grupos_list.append({
                'id': grupo['id'],
                'nome': grupo['nome']
            })
        
        return jsonify(grupos_list)

    except Exception as e:
        app.logger.error(f"Erro ao buscar grupos para proposta {proposta_id}: {e}")
        return jsonify({
            "error": "Ocorreu um erro ao buscar os grupos",
            "details": str(e)
        }), 500
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/get_respostas_avaliadas/<int:proposta_id>/<int:grupo_id>')
def get_respostas_avaliadas(proposta_id, grupo_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Busca as respostas avaliadas (mantendo 1 para booleanos)
        cursor.execute('''
            SELECT r.id, r.titulo, r.descricao, r.categorias, r.arquivos, 
                   r.observacao, r.pontuacao, g.name AS grupo_nome
            FROM respostas r
            JOIN groups g ON r.grupo_id = g.id
            WHERE r.tarefa_id = %s AND r.grupo_id = %s AND r.is_avaliada = 1
        ''', (proposta_id, grupo_id))
        respostas = cursor.fetchall()

        # Buscar nomes das categorias
        cursor.execute('SELECT id, categoria FROM base_pontos')
        categorias = cursor.fetchall()
        categorias_dict = {categoria['id']: categoria['categoria'] for categoria in categorias}

        respostas_completa = []
        for resposta in respostas:
            resposta_dict = dict(resposta)  # Converter para dicionário

            # Processar categorias
            categorias_resposta = []
            if resposta_dict['categorias']:
                try:
                    for cat_id in resposta_dict['categorias'].split(','):
                        cat_id = cat_id.strip()
                        if cat_id and int(cat_id) in categorias_dict:
                            categorias_resposta.append(categorias_dict[int(cat_id)])
                except (ValueError, AttributeError) as e:
                    app.logger.warning(f"Erro ao processar categorias: {e}")

            # Processar arquivos
            arquivos = []
            if resposta_dict['arquivos']:
                try:
                    arquivos = [arq.strip() for arq in resposta_dict['arquivos'].split(',') if arq.strip()]
                except AttributeError as e:
                    app.logger.warning(f"Erro ao processar arquivos: {e}")

            # Montar resposta completa
            resposta_completa = {
                'id': resposta_dict['id'],
                'titulo': resposta_dict['titulo'],
                'descricao': resposta_dict['descricao'],
                'categorias': categorias_resposta,
                'arquivos': arquivos,
                'arquivos_completos': [
                    f"arquivos/proposta/proposta_{proposta_id}/grupo_{grupo_id}/resposta_{resposta_dict['id']}/{arquivo}"
                    for arquivo in arquivos
                ],
                'observacao': resposta_dict['observacao'],
                'pontuacao': resposta_dict['pontuacao'],
                'grupo_nome': resposta_dict['grupo_nome']
            }

            respostas_completa.append(resposta_completa)

        app.logger.debug(f"Respostas retornadas para proposta {proposta_id}, grupo {grupo_id}: {len(respostas_completa)} itens")
        return jsonify(respostas_completa)

    except Exception as e:
        app.logger.error(f"Erro ao buscar respostas avaliadas: {e}")
        return jsonify({
            "error": "Ocorreu um erro ao buscar as respostas",
            "details": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/excluir_resposta/<int:resposta_id>', methods=['DELETE'])
def excluir_resposta(resposta_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Busca a resposta
        cursor.execute('SELECT * FROM respostas WHERE id = %s', (resposta_id,))
        resposta = cursor.fetchone()
        
        if not resposta:
            return jsonify({'success': False, 'message': 'Resposta não encontrada'}), 404

        # Busca o líder do grupo
        grupo_id = resposta['grupo_id']
        cursor.execute('SELECT created_by FROM groups WHERE id = %s', (grupo_id,))
        lider = cursor.fetchone()
        
        if not lider:
            return jsonify({'success': False, 'message': 'Líder do grupo não encontrado'}), 404

        # Remove os arquivos da resposta
        caminho_pasta = os.path.join(
            app.config['UPLOAD_FOLDER'],
            f'proposta_{resposta["tarefa_id"]}',
            f'grupo_{grupo_id}',
            f'resposta_{resposta_id}'
        )
        
        if os.path.exists(caminho_pasta):
            app.logger.info(f"Removendo arquivos da resposta {resposta_id} em {caminho_pasta}")
            try:
                shutil.rmtree(caminho_pasta)
            except OSError as e:
                app.logger.error(f"Erro ao remover arquivos: {e}")
                return jsonify({
                    'success': False,
                    'message': 'Erro ao remover arquivos da resposta'
                }), 500

        # Remove a resposta do banco de dados
        cursor.execute('DELETE FROM respostas WHERE id = %s', (resposta_id,))

        # Notifica o líder do grupo
        mensagem = f"A resposta '{resposta['titulo']}' foi invalidada. Foram retirados {resposta['pontuacao']} pontos do grupo."
        cursor.execute('''
            INSERT INTO notifications (user_id, message, group_id)
            VALUES (%s, %s, %s)
        ''', (lider['created_by'], mensagem, grupo_id))

        conn.commit()
        return jsonify({'success': True, 'message': 'Resposta excluída com sucesso'})

    except Exception as e:
        if conn:
            conn.rollback()
        app.logger.error(f"Erro ao excluir resposta {resposta_id}: {e}")
        return jsonify({
            'success': False,
            'message': 'Ocorreu um erro ao excluir a resposta'
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/favoritos')
def favoritos():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            'SELECT is_evaluator FROM users WHERE id = %s', 
            (session['user_id'],)
        )
        user = cursor.fetchone()
        
        if not user or user['is_evaluator'] != 1:
            return redirect(url_for('index'))
        
        cursor.execute('''
            SELECT 
                r.id,
                r.titulo,
                r.categorias,
                r.descricao,
                r.link,
                r.arquivos,
                r.tarefa_id AS proposta_id,
                r.grupo_id,
                g.name AS grupo_nome,
                p.nome AS proposta_nome
            FROM respostas r
            JOIN groups g ON r.grupo_id = g.id
            JOIN propostas p ON r.tarefa_id = p.id
            WHERE r.is_favor = 1
            ORDER BY r.id DESC
        ''')
        respostas = cursor.fetchall()
        
        cursor.execute('SELECT id, categoria FROM base_pontos')
        categorias_db = cursor.fetchall()
        categorias_map = {str(cat['id']): cat['categoria'] for cat in categorias_db}
        
        respostas_processadas = []
        for resposta in respostas:
            resposta_dict = dict(resposta)
            
            # Processamento de categorias
            if resposta_dict['categorias']:
                ids_categorias = resposta_dict['categorias'].split(',')
                resposta_dict['categorias_nomes'] = ', '.join(
                    categorias_map.get(id.strip(), id.strip()) 
                    for id in ids_categorias
                )
            else:
                resposta_dict['categorias_nomes'] = 'Sem categoria'
            
            # Processamento robusto de arquivos
            arquivos = resposta_dict['arquivos']
            if not arquivos or str(arquivos).strip().lower() in ('', 'sem anexos', 'null', 'none'):
                resposta_dict['arquivos'] = []
            else:
                resposta_dict['arquivos'] = [arq.strip() for arq in str(arquivos).split(',') if arq.strip()]
            
            # Adiciona caminho base para os arquivos
            resposta_dict['caminho_base'] = f"proposta/proposta_{resposta_dict['proposta_id']}/grupo_{resposta_dict['grupo_id']}/resposta_{resposta_dict['id']}"
            
            respostas_processadas.append(resposta_dict)
        
        return render_template(
            'favoritos.html', 
            respostas=respostas_processadas,
            categorias_map=categorias_map,
            S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME')
        )
        
    except Exception as e:
        app.logger.error(f"Erro ao buscar favoritos: {str(e)}", exc_info=True)
        return f"Erro ao carregar respostas favoritas: {str(e)}", 500
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
