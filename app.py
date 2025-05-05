from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import sqlite3
import hashlib
import shutil
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Configuração para uploads
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # Pega o diretório do app.py
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'arquivos', 'proposta')  # Caminho absoluto
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

# Garante que a pasta de upload existe
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # Limite de 50MB para uploads

def get_db_connection():
    # Caminho absoluto para o database.db
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# Verifica se o arquivo tem uma extensão permitida
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

        # Salva os dados da proposta no banco de dados primeiro
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO propostas (nome, descricao, arquivos, avaliador_id) VALUES (?, ?, ?, ?)',
            (proposta_nome, descricao, '', session['user_id'])
        )
        proposta_id = cursor.lastrowid  # Pega o ID gerado para a proposta
        conn.commit()

        # Cria pasta específica para a proposta baseada no ID
        proposta_folder = os.path.join(app.config['UPLOAD_FOLDER'], f'proposta_{proposta_id}')
        os.makedirs(proposta_folder, exist_ok=True)

        # Salva os arquivos enviados
        saved_files = []
        for file in arquivos:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(proposta_folder, filename))
                saved_files.append(filename)

        # Atualiza a lista de arquivos no banco de dados
        cursor.execute(
            'UPDATE propostas SET arquivos = ? WHERE id = ?',
            (','.join(saved_files), proposta_id)
        )
        conn.commit()
        conn.close()

        flash('Proposta criada com sucesso!')
        return redirect(url_for('tarefas'))

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
    # Remove qualquer registro existente
    conn.execute('DELETE FROM cronometro')
    # Insere o novo registro
    conn.execute('INSERT INTO cronometro (start_time, total_time) VALUES (?, ?)',
                 (datetime.now(), total_time))
    conn.commit()
    conn.close()

    return jsonify({'mensagem': 'Cronômetro iniciado'}), 200

@app.route('/tempo_restante')
def tempo_restante():
    conn = get_db_connection()
    registro = conn.execute('SELECT start_time, total_time FROM cronometro LIMIT 1').fetchone()
    conn.close()

    if not registro:
        return jsonify({'tempo_restante': 0})

    start_time = datetime.strptime(registro['start_time'], '%Y-%m-%d %H:%M:%S.%f')
    total_time = registro['total_time']
    tempo_passado = (datetime.now() - start_time).total_seconds()
    tempo_restante = max(total_time - tempo_passado, 0)

    return jsonify({'tempo_restante': tempo_restante})

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

    # Verifica se o usuário é um avaliador
    conn = get_db_connection()
    user = conn.execute('SELECT is_evaluator FROM users WHERE username = ?', (session['username'],)).fetchone()
    is_evaluator = user['is_evaluator'] == 1 if user else False

    # Se o usuário NÃO for um avaliador, verifica o tempo restante
    if not is_evaluator:
        registro = conn.execute('SELECT start_time, total_time FROM cronometro LIMIT 1').fetchone()
        if registro:
            start_time = datetime.strptime(registro['start_time'], '%Y-%m-%d %H:%M:%S.%f')
            total_time = registro['total_time']
            tempo_passado = (datetime.now() - start_time).total_seconds()
            tempo_restante = max(total_time - tempo_passado, 0)

            # Se o tempo estiver zerado, redireciona para a página de tempo esgotado
            if tempo_restante <= 0:
                conn.close()
                return redirect(url_for('tempo_esgotado'))

    conn = get_db_connection()
    propostas = conn.execute('''
        SELECT p.id, p.nome, p.descricao, p.arquivos,
               GROUP_CONCAT(g.name, ', ') AS equipes
        FROM propostas p
        LEFT JOIN tarefa_equipes te ON p.id = te.tarefa_id
        LEFT JOIN groups g ON te.grupo_id = g.id
        GROUP BY p.id
    ''').fetchall()

    username = session.get('username')
    user = conn.execute('SELECT is_leader, is_group FROM users WHERE username = ?', (username,)).fetchone()
    is_leader = user['is_leader'] == 1 if user else False
    user_group_id = user['is_group'] if user else None

    conn.close()
    return render_template('tarefas.html', propostas=propostas, is_leader=is_leader, user_group_id=user_group_id)

# Rota de verificação Comum-Avaliador
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        username = request.form['username']
        password = hashlib.sha256(request.form['password'].encode()).hexdigest()
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
        conn.close()
        
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
    
    return render_template('index.html')

@app.route('/resposta')
def resposta():
    # Verifica se o usuário está logado
    if 'username' not in session:
        return redirect(url_for('index'))

    # Verifica se o usuário é um avaliador
    conn = get_db_connection()
    user = conn.execute('SELECT is_evaluator FROM users WHERE username = ?', (session['username'],)).fetchone()
    is_evaluator = user['is_evaluator'] == 1 if user else False

    # Se o usuário NÃO for um avaliador, verifica o tempo restante
    if not is_evaluator:
        registro = conn.execute('SELECT start_time, total_time FROM cronometro LIMIT 1').fetchone()
        if registro:
            start_time = datetime.strptime(registro['start_time'], '%Y-%m-%d %H:%M:%S.%f')
            total_time = registro['total_time']
            tempo_passado = (datetime.now() - start_time).total_seconds()
            tempo_restante = max(total_time - tempo_passado, 0)

            # Se o tempo estiver zerado, redireciona para a página de tempo esgotado
            if tempo_restante <= 0:
                conn.close()
                return redirect(url_for('tempo_esgotado'))

    # Obtém o usuário logado
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index'))  # Redireciona para a página inicial se o usuário não estiver autenticado

    
    user_data = conn.execute('''
        SELECT is_group FROM users WHERE id = ?
    ''', (user_id,)).fetchone()

    if not user_data or not user_data['is_group']:
        # Redireciona para a página group_request_alt.html se o usuário não pertence a um grupo
        return redirect(url_for('group_request_alt'))

    try:
        # Converte o is_group para inteiro
        user_group = int(user_data['is_group'])
    except ValueError:
        # Redireciona para a página group_request_alt.html se o ID do grupo for inválido
        return redirect(url_for('group_request_alt'))

    # Consulta as propostas aceitas pelo grupo
    propostas_aceitas = conn.execute('''
        SELECT propostas.id, propostas.nome 
        FROM propostas 
        WHERE propostas.id IN (
            SELECT tarefa_id 
            FROM tarefa_equipes 
            WHERE grupo_id = ?
        )
    ''', (user_group,)).fetchall()

    # Busca todas as categorias do banco de dados
    categorias = conn.execute('SELECT id, categoria, detalhes FROM base_pontos').fetchall()
    categorias_dict = {categoria['id']: categoria for categoria in categorias}

    conn.close()
    return render_template(
        'resposta.html', 
        propostas_aceitas=propostas_aceitas, 
        categorias=categorias_dict
    )

@app.route('/group_request_alt')
def group_request_alt():
    return render_template('group_request_alt.html')

# Rota responsável por processar o envio da resposta
@app.route('/enviar_resposta', methods=['POST'])
def enviar_resposta():
    conn = get_db_connection()
    user_id = session.get('user_id')

    if not user_id:
        return "Erro: Usuário não está autenticado.", 403

    # Buscar informações do usuário
    user_data = conn.execute('SELECT is_group FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_data or not user_data['is_group']:
        return "Erro: Usuário não pertence a um grupo.", 403

    try:
        user_group = int(user_data['is_group'])
    except ValueError:
        return "Erro: ID do grupo no formato inválido.", 400

    # Processar o envio da resposta
    proposta_id = request.form['proposta']
    categoria = request.form.get('categorias')  # Agora é um único valor, não uma lista
    titulo = request.form['titulo']
    descricao = request.form['descricao']
    link = request.form.get('link', '')  # Captura o link (opcional)
    arquivos = request.files.getlist('arquivos')

    if not arquivos:
        return "Erro: Nenhum arquivo foi enviado.", 400

    # Cria a entrada no banco de dados antes para obter o ID da resposta
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO respostas (tarefa_id, grupo_id, titulo, descricao, categorias, link, arquivos) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (proposta_id, user_group, titulo, descricao, categoria, link, '')  # Adicionado o campo `link`
    )
    resposta_id = cursor.lastrowid  # ID gerado para a resposta
    conn.commit()

    # Criar pasta específica para os arquivos da resposta baseada no ID
    resposta_folder = os.path.join(
        app.config['UPLOAD_FOLDER'],
        f'proposta_{proposta_id}',
        f'grupo_{user_group}',
        f'resposta_{resposta_id}'
    )
    os.makedirs(resposta_folder, exist_ok=True)

    # Salvar os arquivos enviados
    saved_files = []
    for arquivo in arquivos:
        if arquivo and allowed_file(arquivo.filename):
            filename = secure_filename(arquivo.filename)
            arquivo_path = os.path.join(resposta_folder, filename)
            arquivo.save(arquivo_path)
            saved_files.append(filename)

    # Atualiza a lista de arquivos no banco de dados
    cursor.execute(
        'UPDATE respostas SET arquivos = ? WHERE id = ?',
        (','.join(saved_files), resposta_id)
    )
    conn.commit()
    conn.close()

    flash('Resposta enviada com sucesso!')
    return redirect(url_for('respostas_enviadas'))

# Nova rota para exibir as respostas enviadas
@app.route('/respostas_enviadas', methods=['GET'])
def respostas_enviadas():
    conn = get_db_connection()
    user_id = session.get('user_id')

    if not user_id:
        return "Erro: Usuário não está autenticado.", 403

    # Buscar informações do usuário
    user_data = conn.execute('SELECT is_group FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_data or not user_data['is_group']:
        return "Erro: Usuário não pertence a um grupo.", 403

    try:
        user_group = int(user_data['is_group'])
    except ValueError:
        return "Erro: ID do grupo no formato inválido.", 400

    # Obter respostas relacionadas ao grupo
    respostas = conn.execute(
        'SELECT * FROM respostas WHERE grupo_id = ?', (user_group,)
    ).fetchall()

    # Buscar todas as categorias do banco de dados
    categorias = conn.execute('SELECT id, categoria FROM base_pontos').fetchall()
    categorias_dict = {categoria['id']: categoria for categoria in categorias}  # Dicionário para fácil acesso

    respostas_com_arquivos = []
    for resposta in respostas:
        # Buscar o nome da proposta
        proposta_nome = conn.execute(
            'SELECT nome FROM propostas WHERE id = ?', (resposta['tarefa_id'],)
        ).fetchone()['nome']

        # Ajustar o caminho conforme o novo formato
        resposta_path = os.path.join(
            app.root_path,
            'static',
            'arquivos',
            'proposta',
            f'proposta_{resposta["tarefa_id"]}',
            f'grupo_{user_group}',
            f'resposta_{resposta["id"]}'
        )

        if os.path.exists(resposta_path):
            arquivos = os.listdir(resposta_path)
        else:
            arquivos = []

        # Adicionar o nome da proposta ao dicionário da resposta
        respostas_com_arquivos.append({
            **resposta,
            'arquivos': arquivos if arquivos else ["sem anexos"],
            'proposta_id': resposta['tarefa_id'],  # ID da proposta
            'grupo_id': str(user_group),          # ID do grupo
            'proposta_nome': proposta_nome,       # Nome da proposta
            'link': resposta['link']              # Novo campo de link
        })

    conn.close()
    return render_template('respostas_enviadas.html', respostas=respostas_com_arquivos, categorias=categorias_dict)

# Rotas para excluir e aceitar tarefas
@app.route('/excluir_tarefa/<int:tarefa_id>')
def excluir_tarefa(tarefa_id):
    if 'username' not in session or not session.get('is_evaluator'):
        return redirect(url_for('index'))

    conn = get_db_connection()
    tarefa = conn.execute('SELECT * FROM propostas WHERE id = ?', (tarefa_id,)).fetchone()

    if tarefa:
        # Remove os arquivos relacionados à tarefa
        pasta_tarefa = os.path.join(app.root_path, 'static/arquivos/proposta', tarefa['nome'])
        if os.path.exists(pasta_tarefa):
            import shutil
            shutil.rmtree(pasta_tarefa)

        # Remove os registros da tabela "tarefa_equipes"
        conn.execute('DELETE FROM tarefa_equipes WHERE tarefa_id = ?', (tarefa_id,))

        # Remove a tarefa da tabela "propostas"
        conn.execute('DELETE FROM propostas WHERE id = ?', (tarefa_id,))

        # Confirma as alterações
        conn.commit()

    conn.close()
    flash('Tarefa e registros associados excluídos com sucesso!')
    return redirect(url_for('tarefas'))

@app.route('/aceitar_tarefa/<int:tarefa_id>')
def aceitar_tarefa(tarefa_id):
    if 'username' not in session:
        flash('Você precisa estar logado para acessar essa página.')
        return redirect(url_for('index'))
    
    username = session['username']
    conn = get_db_connection()

    # Verificar se o usuário é líder
    usuario = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    if not usuario or usuario['is_leader'] != 1:
        flash('Você não tem permissão para aceitar esta tarefa.')
        conn.close()
        return redirect(url_for('tarefas'))

    # Verificar se a tarefa existe
    tarefa = conn.execute('SELECT * FROM propostas WHERE id = ?', (tarefa_id,)).fetchone()
    if not tarefa:
        flash('Tarefa não encontrada.')
        conn.close()
        return redirect(url_for('tarefas'))

    grupo_id = usuario['is_group']

    # Verificar se o grupo já aceitou a tarefa
    ja_aceitou = conn.execute(
        'SELECT 1 FROM tarefa_equipes WHERE tarefa_id = ? AND grupo_id = ?',
        (tarefa_id, grupo_id)
    ).fetchone()

    if ja_aceitou:
        flash('Seu grupo já aceitou esta tarefa.')
    else:
        conn.execute(
            'INSERT INTO tarefa_equipes (tarefa_id, grupo_id) VALUES (?, ?)',
            (tarefa_id, grupo_id)
        )
        conn.commit()
        flash('Tarefa aceita com sucesso!')

    conn.close()
    return redirect(url_for('tarefas'))

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

    # Verificar a chave de autenticação com base no papel selecionado
    if role == 'evaluator':
        if not auth_key or not evaluator_email:
            return render_template('index.html', error='Por favor, preencha a chave de autenticação e o e-mail para avaliadores.')

        conn = get_db_connection()
        stored_key = conn.execute('SELECT auth_key FROM evaluator_key').fetchone()
        conn.close()

        if not stored_key or stored_key[0] != auth_key:
            return render_template('index.html', error='Chave de autenticação inválida para avaliadores.')
    elif role == 'student':
        if not student_auth_key:
            return render_template('index.html', error='Por favor, insira a chave de autenticação para alunos.')

        conn = get_db_connection()
        stored_student_key = conn.execute('SELECT auth_key FROM aluno_key').fetchone()
        conn.close()

        if not stored_student_key or stored_student_key[0] != student_auth_key:
            return render_template('index.html', error='Chave de autenticação inválida para alunos.')

    # Criptografar a senha antes de salvar
    hashed_password = hashlib.sha256(password.encode()).hexdigest()

    # Inserir o usuário no banco de dados
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (username, password, is_evaluator, evaluator_email) VALUES (?, ?, ?, ?)',
                     (username, hashed_password, 1 if role == 'evaluator' else 0, evaluator_email))
        conn.commit()
    except sqlite3.IntegrityError:
        return render_template('index.html', error='Usuário já existe.')
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return render_template('index.html', error='Todos os campos devem ser preenchidos.')

    # Criptografar a senha para comparação
    hashed_password = hashlib.sha256(password.encode()).hexdigest()

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, hashed_password)).fetchone()
    conn.close()

    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_evaluator'] = user['is_evaluator']

        if user['is_evaluator']:  
            return redirect(url_for('home_avaliador'))  
        return redirect(url_for('home'))  
    else:
        return render_template('index.html', error='Usuário ou senha incorretos.')

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
    conn = get_db_connection()
    
    # Consulta corrigida com contagem de respostas
    grupos = conn.execute('''
        SELECT 
            g.id, 
            g.name, 
            SUM(r.pontuacao) AS total_pontos,
            COUNT(r.id) AS total_respostas
        FROM groups g
        JOIN respostas r ON g.id = r.grupo_id
        WHERE r.is_avaliada = 1
        GROUP BY g.id
        ORDER BY total_pontos DESC, g.name ASC
    ''').fetchall()

    # Prepara os dados
    grupos_nomes = [grupo['name'] for grupo in grupos]
    grupos_pontos = [grupo['total_pontos'] for grupo in grupos]
    grupos_respostas = [grupo['total_respostas'] for grupo in grupos]

    conn.close()
    return render_template('graficos.html', 
                         grupos=grupos_nomes, 
                         pontos=grupos_pontos,
                         respostas=grupos_respostas)

# Rota para exibir as notificações
@app.route('/notificacoes')
def notificacoes():
    if 'username' not in session:
        return redirect(url_for('index'))
    
    # Busca todas as notificações do usuário, ordenadas por data (mais recentes primeiro)
    conn = get_db_connection()
    notifications = conn.execute(
        'SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 10', 
        (session['user_id'],)
    ).fetchall()
    conn.close()

    return render_template('notificacoes.html', notifications=notifications)

# Processsa a aceitação do convite da notificação
@app.route('/notifications/<int:notification_id>/accept', methods=['GET', 'POST'])
def accept_member_request(notification_id):
    conn = get_db_connection()
    notification = conn.execute('SELECT * FROM notifications WHERE id = ?', (notification_id,)).fetchone()

    if notification is None:
        flash('Notificação não encontrada.')
        return redirect(url_for('notificacoes'))

    user_id = notification['user_id']
    group_id = notification['group_id']

    # Obtém o nome do grupo com base no group_id
    group = conn.execute('SELECT name FROM groups WHERE id = ?', (group_id,)).fetchone()
    if group is None:
        flash('Grupo não encontrado.')
        return redirect(url_for('notificacoes'))

    group_name = group['name']

    # Verifica se o usuário já faz parte de um grupo
    user = conn.execute('SELECT is_group FROM users WHERE id = ?', (user_id,)).fetchone()
    if user is None:
        flash('Usuário não encontrado.')
        return redirect(url_for('notificacoes'))

    if user['is_group'] != "none":
        # Se o usuário já faz parte de um grupo, envia uma notificação informando isso
        conn.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', 
                     (user_id, 'Você já faz parte de um grupo e não pode aceitar novos convites.'))
        
        # Exclui a notificação original com o convite
        conn.execute('DELETE FROM notifications WHERE id = ?', (notification_id,))

        conn.commit()
        conn.close()
        flash('Você já faz parte de um grupo e não pode aceitar novos convites.')
        return redirect(url_for('notificacoes'))

    # Verifica se o grupo está cheio (1 líder + 3 membros)
    members = conn.execute('''
        SELECT COUNT(*) as count 
        FROM group_members 
        WHERE group_id = ? AND status IN ('Líder', 'Membro')
    ''', (group_id,)).fetchone()
    is_group_full = members['count'] >= 4

    if is_group_full:
        # Remove o usuário do status "Aguardando" para este grupo
        conn.execute('''
            DELETE FROM group_members 
            WHERE group_id = ? AND user_id = ? AND status = 'Aguardando'
        ''', (group_id, user_id))

        # Cria uma notificação informando que o grupo está cheio
        conn.execute('''
            INSERT INTO notifications (user_id, message, group_id)
            VALUES (?, ?, ?)
        ''', (user_id, f'O grupo {group_name} está cheio. Você não pôde ser adicionado.', group_id))

        conn.commit()
        conn.close()
        flash('O grupo está cheio. Você não pôde ser adicionado.')
        return redirect(url_for('notificacoes'))

    # Verificar se o usuário já é membro do grupo atual
    member_check = conn.execute('SELECT * FROM group_members WHERE group_id = ? AND user_id = ?', (group_id, user_id)).fetchone()

    if member_check is None:
        # Adicionar o usuário ao grupo com status de 'Membro'
        conn.execute('INSERT INTO group_members (group_id, user_id, status) VALUES (?, ?, "Membro")', (group_id, user_id))
    else:
        # Caso o usuário já exista como membro, apenas atualize o status
        conn.execute('UPDATE group_members SET status = "Membro" WHERE group_id = ? AND user_id = ?', (group_id, user_id))

    # Remove o usuário de todos os outros grupos onde ele está com status "aguardando" ou "solicitando"
    conn.execute('''
        DELETE FROM group_members
        WHERE user_id = ? AND UPPER(status) IN ('AGUARDANDO', 'SOLICITANDO') AND group_id != ?
    ''', (user_id, group_id))

    # Exclui a notificação original com o convite
    conn.execute('DELETE FROM notifications WHERE id = ?', (notification_id,))

    # Envia uma nova notificação informando que o usuário foi aceito no grupo
    conn.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (user_id, f'Você foi aceito no grupo {group_name}.'))

    # Atualiza a tabela de usuários para indicar que o usuário agora é membro do grupo
    conn.execute('''
        UPDATE users 
        SET is_member = 1, is_group = ? 
        WHERE id = ?
        ''', (group_id, user_id))

    conn.commit()
    conn.close()

    flash('Solicitação aceita e usuário adicionado ao grupo.')
    return redirect(url_for('notificacoes'))

@app.route('/notifications/<int:notification_id>/reject', methods=['GET', 'POST'])
def reject_member_request(notification_id):
    conn = get_db_connection()
    notification = conn.execute('SELECT * FROM notifications WHERE id = ?', (notification_id,)).fetchone()

    if notification is None:
        flash('Notificação não encontrada.')
        return redirect(url_for('notificacoes'))

    user_id = notification['user_id']
    group_id = notification['group_id']

    # Remover o usuário da tabela de membros, caso ele esteja como "Aguardando"
    conn.execute('DELETE FROM group_members WHERE group_id = ? AND user_id = ? AND status = "Aguardando"', (group_id, user_id))

    # Exclui a notificação
    conn.execute('DELETE FROM notifications WHERE id = ?', (notification_id,))

    # Envia uma nova notificação informando que a solicitação foi recusada
    conn.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (user_id, f'Sua solicitação para participar no grupo {group_id} foi recusada.'))

    conn.commit()
    conn.close()

    flash('Solicitação recusada.')
    return redirect(url_for('notificacoes'))

# Rota para excluir uma notificação
@app.route('/notifications/<int:notification_id>/delete', methods=['POST'])
def delete_notification(notification_id):
    conn = get_db_connection()
    notification = conn.execute('SELECT * FROM notifications WHERE id = ?', (notification_id,)).fetchone()

    if notification is None:
        flash('Notificação não encontrada.')
        return redirect(url_for('notificacoes'))

    user_id = notification['user_id']
    group_id = notification['group_id']

    # Se a notificação for de convite, remove o usuário do grupo com status "aguardando"
    if group_id is not None:
        conn.execute('DELETE FROM group_members WHERE group_id = ? AND user_id = ? AND status = "Aguardando"', (group_id, user_id))

    # Exclui a notificação
    conn.execute('DELETE FROM notifications WHERE id = ?', (notification_id,))

    conn.commit()
    conn.close()

    flash('Notificação excluída com sucesso.')
    return redirect(url_for('notificacoes'))

# Rota para verificar novas notificações (usada pelo JavaScript)
@app.route('/notificacoes/verificar')
def verificar_notificacoes():
    if 'username' not in session:
        return jsonify({'has_new': False})
    
    conn = get_db_connection()
    new_notifications = conn.execute(
        'SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0', 
        (session['user_id'],)
    ).fetchone()[0]
    conn.close()

    return jsonify({'has_new': new_notifications > 0})

# Função para adicionar uma nova notificação
def add_notification(user_id, message):
    conn = get_db_connection()
    
    # Verifica se já existem 10 notificações
    count = conn.execute('SELECT COUNT(*) FROM notifications WHERE user_id = ?', (user_id,)).fetchone()[0]
    if count >= 10:
        # Remove a notificação mais antiga
        oldest_notification = conn.execute(
            'SELECT id FROM notifications WHERE user_id = ? ORDER BY created_at ASC LIMIT 1', 
            (user_id,)
        ).fetchone()
        if oldest_notification:
            conn.execute('DELETE FROM notifications WHERE id = ?', (oldest_notification['id'],))
    
    # Adiciona a nova notificação
    conn.execute(
        'INSERT INTO notifications (user_id, message, read, created_at) VALUES (?, ?, 0, CURRENT_TIMESTAMP)', 
        (user_id, message)
    )
    conn.commit()
    conn.close()

# Novas rotas para grupos
@app.route('/groups', methods=['GET', 'POST'])
def groups():
    if 'username' not in session:
        return redirect(url_for('index'))

    user_id = session['user_id']
    conn = get_db_connection()
    row = conn.execute('SELECT is_leader, is_member, is_group, is_evaluator FROM users WHERE id = ?', (user_id,)).fetchone()
    user = {'is_leader': row[0], 'is_member': row[1], 'is_group': row[2], 'is_evaluator': row[3]} if row else {'is_leader': 0, 'is_member': 0, 'is_group': 'none', 'is_evaluator': 0}

    if request.method == 'POST':
        group_name = request.form['group-name']
        members = request.form.getlist('members')
        created_by = user_id

        # Cria o novo grupo
        conn.execute('INSERT INTO groups (name, created_by) VALUES (?, ?)', (group_name, created_by))
        group_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

        # Adicionar o criador ao grupo com status de 'Líder'
        conn.execute('INSERT INTO group_members (group_id, user_id, status) VALUES (?, ?, "Líder")', (group_id, created_by))

        # Atualiza a tabela de usuários após a criação do grupo
        conn.execute('''
            UPDATE users 
            SET is_leader = 1, is_group = ? 
            WHERE id = ?
        ''', (group_id, created_by))

        # Remove o criador de todos os outros grupos onde ele está com status "aguardando" ou "solicitando"
        conn.execute('''
            DELETE FROM group_members
            WHERE user_id = ? AND UPPER(status) IN ('AGUARDANDO', 'SOLICITANDO') AND group_id != ?
        ''', (created_by, group_id))

        # Enviar notificações para os membros convidados
        for member in members:
            if int(member) != created_by:
                conn.execute('INSERT INTO notifications (user_id, message, group_id) VALUES (?, ?, ?)', 
                            (member, f'Você foi adicionado ao grupo {group_name}.', group_id))
                notification_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                conn.execute('UPDATE notifications SET message = ? WHERE id = ?',
                             (f'Você foi adicionado ao grupo {group_name}. <a href="{url_for("accept_member_request", notification_id=notification_id)}">Aceitar</a> ou <a href="{url_for("reject_member_request", notification_id=notification_id)}">Recusar</a>',
                                notification_id))
                conn.execute('INSERT INTO group_members (group_id, user_id, status) VALUES (?, ?, "Aguardando")', 
                            (group_id, member))

        conn.commit()
        conn.close()
        return redirect(url_for('groups'))

    # Filtra os usuários que não são membros, líderes ou avaliadores
    users = conn.execute('SELECT id, username, is_member, is_leader, is_evaluator FROM users').fetchall()
    filtered_users = [user for user in users if not user['is_member'] and not user['is_leader'] and not user['is_evaluator']]

    # Obtém os grupos e verifica se estão cheios
    groups = conn.execute('SELECT * FROM groups').fetchall()
    groups = [dict(group) for group in groups]  # Converte cada sqlite3.Row para um dicionário

    for group in groups:
        members = conn.execute('''
            SELECT COUNT(*) as count 
            FROM group_members 
            WHERE group_id = ? AND status IN ('Líder', 'Membro')
        ''', (group['id'],)).fetchone()
        group['is_full'] = members['count'] >= 4  # Agora podemos adicionar a chave 'is_full'

    conn.close()

    # Captura a mensagem da URL (se houver)
    message = request.args.get('message')
    return render_template('groups.html', users=filtered_users, groups=groups, user=user, message=message)

# Rota para excluir o grupo
@app.route('/groups/<int:group_id>/delete', methods=['GET', 'POST'])
def delete_group(group_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()
    group = conn.execute('SELECT * FROM groups WHERE id = ?', (group_id,)).fetchone()
    
    if group and group['created_by'] == session['user_id']:
        # Remove status de membro de todos os usuários do grupo
        conn.execute('UPDATE users SET is_member = 0, is_group = "none" WHERE is_group = ?', (group_id,))
        
        # Remove status de líder do criador do grupo
        conn.execute('UPDATE users SET is_leader = 0 WHERE id = ?', (session['user_id'],))
        
        # Exclui todos os membros e o grupo
        conn.execute('DELETE FROM group_members WHERE group_id = ?', (group_id,))
        conn.execute('DELETE FROM groups WHERE id = ?', (group_id,))
        conn.execute('DELETE FROM notifications WHERE group_id = ?', (group_id,))
        conn.commit()

    conn.close()
    return redirect(url_for('groups'))

# Rota para expulsar um membro
@app.route('/groups/<int:group_id>/kick/<int:user_id>', methods=['GET', 'POST'])
def kick_member(group_id, user_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()
    group = conn.execute('SELECT * FROM groups WHERE id = ?', (group_id,)).fetchone()

    if group and group['created_by'] == session['user_id']:
        # Remove o status de membro do usuário expulso
        conn.execute('UPDATE users SET is_member = 0, is_group = "none" WHERE id = ?', (user_id,))
        
        # Remove o usuário da lista de membros
        conn.execute('DELETE FROM group_members WHERE group_id = ? AND user_id = ?', (group_id, user_id))
        conn.commit()
    
    conn.close()
    return redirect(url_for('group_detail', group_id=group_id))

# Rota para um membro deixar o grupo
@app.route('/groups/<int:group_id>/leave', methods=['GET', 'POST'])
def leave_group(group_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))

    user_id = session['user_id']
    conn = get_db_connection()
    
    # Remove o status de membro do usuário que saiu do grupo
    conn.execute('UPDATE users SET is_member = 0, is_group = "none" WHERE id = ?', (user_id,))
    
    # Remove o usuário da lista de membros
    conn.execute('DELETE FROM group_members WHERE group_id = ? AND user_id = ?', (group_id, user_id))
    conn.commit()
    conn.close()

    return redirect(url_for('groups'))

@app.route('/groups/<int:group_id>')
def group_detail(group_id):
    if 'username' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()

    # Obtém os detalhes do grupo
    group = conn.execute('SELECT * FROM groups WHERE id = ?', (group_id,)).fetchone()
    if group is None:
        return redirect(url_for('groups'))  # Redireciona se o grupo não existir

    # Obtém o usuário logado
    user = conn.execute('SELECT * FROM users WHERE username = ?', (session['username'],)).fetchone()

    # Verifica se o usuário faz parte de algum grupo
    current_group = conn.execute('SELECT is_group FROM users WHERE id = ?', (user['id'],)).fetchone()
    current_group_id = current_group['is_group'] if current_group and current_group['is_group'] != "none" else None

    # Verifica se o usuário faz parte do grupo atual
    current_user_status = conn.execute('''
        SELECT status FROM group_members WHERE group_id = ? AND user_id = ?
    ''', (group_id, user['id'])).fetchone()

    # Obtém os membros do grupo (Líder e Membro)
    members = conn.execute('''
        SELECT users.id, users.username, group_members.status 
        FROM group_members 
        JOIN users ON group_members.user_id = users.id 
        WHERE group_members.group_id = ? AND group_members.status IN ('Líder', 'Membro')
    ''', (group_id,)).fetchall()

    # Verifica se o grupo está cheio (1 líder + 3 membros)
    is_group_full = len(members) >= 4

    # Se o usuário não for membro do grupo
    if current_user_status is None:
        # Se o grupo estiver cheio, redireciona com uma mensagem
        if is_group_full:
            conn.close()
            return redirect(url_for('groups', message='Este grupo já está cheio.'))
        # Se o grupo não estiver cheio, redireciona para a página de solicitação
        else:
            leader = conn.execute('SELECT created_by FROM groups WHERE id = ?', (group_id,)).fetchone()
            conn.close()
            return render_template('group_request.html', 
                                  group=group, 
                                  leader=leader, 
                                  user=user, 
                                  is_group=current_group['is_group'], 
                                  current_group_id=current_group_id)

    # Obtém os convites pendentes (Solicitando e Aguardando)
    pending_invitations = conn.execute('''
        SELECT users.id, users.username, group_members.status 
        FROM group_members 
        JOIN users ON group_members.user_id = users.id 
        WHERE group_members.group_id = ? AND group_members.status IN ('Solicitando', 'Aguardando')
    ''', (group_id,)).fetchall()

    # Obtém os usuários que podem ser adicionados ao grupo
    available_users = conn.execute('''
        SELECT id, username 
        FROM users 
        WHERE is_evaluator = 0 
          AND is_leader = 0 
          AND is_member = 0 
          AND is_group = "none"
    ''').fetchall()

    conn.close()

    return render_template('group_detail.html', 
                          group=group, 
                          members=members, 
                          pending_invitations=pending_invitations, 
                          current_user_status=current_user_status['status'] if current_user_status else None,
                          available_users=available_users,
                          is_group_full=is_group_full)  # Passa a flag de grupo cheio para o template

@app.route('/groups/<int:group_id>/request', methods=['GET', 'POST'])
def request_group_invitation(group_id):
    if 'username' not in session:
        return redirect(url_for('index'))

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (session['username'],)).fetchone()

    # Adiciona o usuário ao grupo com status "Solicitando"
    conn.execute('''
        INSERT INTO group_members (group_id, user_id, status)
        VALUES (?, ?, "Solicitando")
    ''', (group_id, user['id']))

    conn.commit()
    conn.close()

    flash('Solicitação enviada. Aguarde a aprovação do líder do grupo.')
    return redirect(url_for('groups'))

# Envia a notificação para os convidados, com o grupo já criado
@app.route('/groups/<int:group_id>/add_members', methods=['POST'])
def add_members(group_id):
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Usuário não autenticado'})

    data = request.get_json()
    selected_members = data.get('members', [])

    conn = get_db_connection()
    group_name = conn.execute('SELECT name FROM groups WHERE id = ?', (group_id,)).fetchone()['name']

    for member_id in selected_members:
        # Verifica se o usuário já está no grupo
        existing_member = conn.execute('''
            SELECT * FROM group_members WHERE group_id = ? AND user_id = ?
        ''', (group_id, member_id)).fetchone()

        if not existing_member:
            # Enviar notificação para o usuário
            conn.execute('INSERT INTO notifications (user_id, message, group_id) VALUES (?, ?, ?)', 
                         (member_id, f'Você foi convidado a participar do grupo {group_name}.', group_id))
            notification_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('UPDATE notifications SET message = ? WHERE id = ?',
                          (f'Você foi convidado a participar do grupo {group_name}. <a href="{url_for("accept_member_request", notification_id=notification_id)}">Aceitar</a> ou <a href="{url_for("reject_member_request", notification_id=notification_id)}">Recusar</a>',
                           notification_id))
            conn.execute('INSERT INTO group_members (group_id, user_id, status) VALUES (?, ?, "Aguardando")', 
                          (group_id, member_id))

    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/groups/<int:group_id>/accept/<int:user_id>', methods=['POST'])
def add_member_request(group_id, user_id):
    conn = get_db_connection()

    try:
        # Atualiza o status do usuário na tabela `users`
        conn.execute('''
            UPDATE users
            SET is_member = 1, is_group = ?
            WHERE id = ?
        ''', (group_id, user_id))

        # Atualiza o status do usuário para "Membro" no grupo atual
        conn.execute('''
            UPDATE group_members
            SET status = 'Membro'
            WHERE group_id = ? AND user_id = ?
        ''', (group_id, user_id))

        # Log para depuração: Verifica os grupos onde o usuário está com status "aguardando" ou "solicitando"
        app.logger.info(f"Verificando grupos do usuário {user_id} com status 'aguardando' ou 'solicitando'...")
        grupos_para_remover = conn.execute('''
            SELECT group_id FROM group_members
            WHERE user_id = ? AND UPPER(status) IN ('AGUARDANDO', 'SOLICITANDO') AND group_id != ?
        ''', (user_id, group_id)).fetchall()
        app.logger.info(f"Grupos encontrados para remoção: {grupos_para_remover}")

        # Remove o usuário de todos os outros grupos onde ele está com status "aguardando" ou "solicitando"
        conn.execute('''
            DELETE FROM group_members
            WHERE user_id = ? AND UPPER(status) IN ('AGUARDANDO', 'SOLICITANDO') AND group_id != ?
        ''', (user_id, group_id))

        conn.commit()
        flash('Usuário aceito no grupo e removido de outros grupos pendentes.', 'success')
    except Exception as e:
        conn.rollback()
        flash('Ocorreu um erro ao processar a solicitação.', 'error')
        app.logger.error(f"Erro ao aceitar usuário no grupo: {e}")
    finally:
        conn.close()

    return redirect(url_for('group_detail', group_id=group_id))

@app.route('/groups/<int:group_id>/reject/<int:user_id>', methods=['POST'])
def recusar_member_request(group_id, user_id):
    conn = get_db_connection()

    # Remove o usuário do grupo
    conn.execute('''
        DELETE FROM group_members
        WHERE group_id = ? AND user_id = ?
    ''', (group_id, user_id))

    conn.commit()
    conn.close()

    flash('Usuário recusado e removido do grupo.')
    return redirect(url_for('group_detail', group_id=group_id))

@app.route('/avaliar_respostas', methods=['GET', 'POST'])
def avaliar_respostas():
    conn = get_db_connection()
    user_id = session.get('user_id')

    # Verificar se o usuário está autenticado e é avaliador
    if not user_id:
        return "Erro: Usuário não está autenticado.", 403

    user_data = conn.execute('SELECT is_evaluator FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_data or user_data['is_evaluator'] == 0:
        return "Erro: Apenas avaliadores podem acessar esta página.", 403

    # Busca todas as categorias do banco de dados
    categorias = conn.execute('SELECT id, categoria, valor, detalhes FROM base_pontos').fetchall()
    categorias_dict = {categoria['id']: categoria for categoria in categorias}  # Dicionário para fácil acesso

    # Verificar parâmetros de seleção no request
    proposta_id = request.args.get('proposta_id', None, type=int)
    grupo_id = request.args.get('grupo_id', None, type=int)
    resposta_id = request.args.get('resposta_id', None, type=int)

    if request.method == 'POST':
        # Processar ações como "Aceitar", "Aceitar com alterações" e "Rejeitar"
        acao = request.form.get('acao')
        observacao = request.form.get('observacao', "")
        resposta_id = request.form.get('resposta_id')

        if resposta_id:
            if acao == 'aceitar':
                # Busca a categoria original da resposta
                resposta = conn.execute('SELECT categorias FROM respostas WHERE id = ?', (resposta_id,)).fetchone()
                if resposta and resposta['categorias']:
                    categoria_id = resposta['categorias'].split(',')[0]  # Pega a primeira (e única) categoria
                    pontuacao = categorias_dict[int(categoria_id)]['valor']  # Obtém o valor da categoria
                else:
                    pontuacao = 0

                # Marca a resposta como avaliada e mantém a categoria original
                conn.execute('''
                    UPDATE respostas 
                    SET is_avaliada = 1, is_reject = 0, is_modify = 0, observacao = ?, pontuacao = ?
                    WHERE id = ?
                ''', (observacao, pontuacao, resposta_id))

            elif acao == 'aceitar_com_alteracoes':
                # Captura a nova categoria selecionada pelo avaliador
                categoria_nova = request.form.get('categorias_novas')  # ID da categoria selecionada
                if categoria_nova:
                    pontuacao = categorias_dict[int(categoria_nova)]['valor']  # Obtém o valor da nova categoria
                else:
                    pontuacao = 0

                # Marca a resposta como avaliada, com modificações e atualiza a categoria e pontuação
                conn.execute('''
                    UPDATE respostas 
                    SET is_avaliada = 1, is_reject = 0, is_modify = 1, observacao = ?, categorias = ?, pontuacao = ?
                    WHERE id = ?
                ''', (observacao, categoria_nova, pontuacao, resposta_id))

            elif acao == 'rejeitar':
                # Marca a resposta como rejeitada e zera a pontuação
                conn.execute('UPDATE respostas SET is_avaliada = 1, is_reject = 1, is_modify = 0, observacao = ?, pontuacao = 0 WHERE id = ?',
                            (observacao, resposta_id))

            conn.commit()
            flash(f"Resposta {resposta_id} processada com sucesso!", "success")
            return redirect(url_for('avaliar_respostas', proposta_id=proposta_id, grupo_id=grupo_id))

    # Etapa 1: Listar propostas do avaliador
    if not proposta_id:
        propostas = conn.execute(
            '''SELECT p.id, p.nome,
                      (SELECT COUNT(*) FROM respostas r WHERE r.tarefa_id = p.id AND r.is_avaliada = 0) AS pendentes,
                      (SELECT COUNT(*) FROM respostas r WHERE r.tarefa_id = p.id AND r.is_avaliada = 1) AS avaliadas
               FROM propostas p WHERE p.avaliador_id = ?''', (user_id,)
        ).fetchall()
        conn.close()
        return render_template('avaliar_respostas.html', propostas=propostas, categorias=categorias_dict)

    # Etapa 2: Listar grupos associados à proposta selecionada
    if not grupo_id:
        grupos = conn.execute(
            '''SELECT g.id, g.name,
                      (SELECT COUNT(*) FROM respostas r WHERE r.grupo_id = g.id AND r.tarefa_id = ? AND r.is_avaliada = 0) AS pendentes,
                      (SELECT COUNT(*) FROM respostas r WHERE r.grupo_id = g.id AND r.tarefa_id = ? AND r.is_avaliada = 1) AS avaliadas
               FROM tarefa_equipes te
               JOIN groups g ON te.grupo_id = g.id
               WHERE te.tarefa_id = ?''', (proposta_id, proposta_id, proposta_id)
        ).fetchall()
        conn.close()
        return render_template('avaliar_respostas.html', grupos=grupos, proposta_id=proposta_id, categorias=categorias_dict)

    # Etapa 3: Listar respostas do grupo selecionado
    if not resposta_id:
        respostas = conn.execute(
            '''SELECT r.*, 
                      (SELECT nome FROM propostas WHERE id = r.tarefa_id) AS proposta_nome
               FROM respostas r
               WHERE r.grupo_id = ? AND r.tarefa_id = ? AND r.is_avaliada = 0''', (grupo_id, proposta_id)
        ).fetchall()
        conn.close()
        return render_template('avaliar_respostas.html', respostas=respostas, proposta_id=proposta_id, grupo_id=grupo_id, categorias=categorias_dict)

    # Etapa 4: Exibir detalhes da resposta selecionada
    resposta = conn.execute(
        '''SELECT r.*, 
                (SELECT nome FROM propostas WHERE id = r.tarefa_id) AS proposta_nome,
                (SELECT name FROM groups WHERE id = r.grupo_id) AS grupo_nome
        FROM respostas r
        WHERE r.id = ?''', (resposta_id,)
    ).fetchone()

    # Buscar os arquivos da resposta
    resposta_path = os.path.join(
        app.root_path,
        'static',
        'arquivos',
        'proposta',
        f'proposta_{resposta["tarefa_id"]}',
        f'grupo_{resposta["grupo_id"]}',
        f'resposta_{resposta["id"]}'
    )

    if os.path.exists(resposta_path):
        arquivos = os.listdir(resposta_path)
    else:
        arquivos = []

    conn.close()
    return render_template('avaliar_respostas.html', resposta=resposta, proposta_id=proposta_id, grupo_id=grupo_id, categorias=categorias_dict, arquivos=arquivos)

@app.route('/marcar_favorito/<int:resposta_id>', methods=['POST'])
def marcar_favorito(resposta_id):
    conn = get_db_connection()
    resposta = conn.execute('SELECT is_favor FROM respostas WHERE id = ?', (resposta_id,)).fetchone()
    
    if resposta:
        novo_valor = 1 if resposta['is_favor'] == 0 else 0
        conn.execute('UPDATE respostas SET is_favor = ? WHERE id = ?', (novo_valor, resposta_id))
        conn.commit()
        conn.close()
        return jsonify(success=True, is_favor=novo_valor)
    
    conn.close()
    return jsonify(success=False)

# Rota para a página de pontuação
@app.route('/pontuacao')
def pontuacao():
    conn = get_db_connection()
    user_id = session.get('user_id')

    if not user_id:
        return redirect(url_for('index'))  # Redireciona se o usuário não estiver autenticado

    # Verifica se o usuário pertence a um grupo
    user_data = conn.execute('SELECT is_group FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_data or user_data['is_group'] == "none":
        conn.close()
        return redirect(url_for('group_request_alt'))  # Redireciona se o usuário não pertence a um grupo

    grupo_id = int(user_data['is_group'])

    # Busca as propostas aceitas pelo grupo
    propostas_aceitas = conn.execute('''
        SELECT p.id, p.nome, p.descricao
        FROM propostas p
        JOIN tarefa_equipes te ON p.id = te.tarefa_id
        WHERE te.grupo_id = ?
    ''', (grupo_id,)).fetchall()

    # Prepara os dados para o template
    propostas_com_pontuacao = []
    for proposta in propostas_aceitas:
        # Converte o objeto Row para um dicionário
        proposta_dict = dict(proposta)

        # Busca as respostas relacionadas à proposta
        respostas = conn.execute('''
            SELECT id, titulo, pontuacao, is_avaliada
            FROM respostas
            WHERE tarefa_id = ? AND grupo_id = ?
        ''', (proposta_dict['id'], grupo_id)).fetchall()

        # Converte os objetos Row das respostas para dicionários
        respostas_dict = [dict(resposta) for resposta in respostas]

        # Calcula a pontuação total das respostas avaliadas
        pontuacao_total = sum(resposta['pontuacao'] for resposta in respostas_dict if resposta['is_avaliada'] == 1)

        # Separa as respostas avaliadas e não avaliadas
        respostas_avaliadas = [resposta for resposta in respostas_dict if resposta['is_avaliada'] == 1]
        respostas_nao_avaliadas = [resposta for resposta in respostas_dict if resposta['is_avaliada'] == 0]

        # Adiciona os dados da proposta ao contexto
        propostas_com_pontuacao.append({
            'id': proposta_dict['id'],
            'nome': proposta_dict['nome'],
            'descricao': proposta_dict['descricao'],
            'pontuacao_total': pontuacao_total,
            'respostas_avaliadas': respostas_avaliadas,
            'respostas_nao_avaliadas': respostas_nao_avaliadas
        })

    conn.close()
    return render_template('pontuacao.html', propostas=propostas_com_pontuacao)

@app.route('/pontuacao_avaliador')
def pontuacao_avaliador():
    conn = get_db_connection()
    user_id = session.get('user_id')

    if not user_id:
        return redirect(url_for('index'))  # Redireciona se o usuário não estiver autenticado

    # Verifica se o usuário é um avaliador
    user_data = conn.execute('SELECT is_evaluator FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_data or user_data['is_evaluator'] == 0:
        conn.close()
        return redirect(url_for('index'))  # Redireciona se o usuário não for um avaliador

    # Busca todas as propostas (tarefas)
    propostas = conn.execute('SELECT id, nome, descricao FROM propostas').fetchall()

    conn.close()

    # Debug: Verifique se as propostas estão sendo buscadas corretamente
    print("Propostas encontradas:", propostas)  # Isso deve aparecer no console do servidor Flask

    return render_template('pontuacao_avaliador.html', propostas=propostas)

@app.route('/get_grupos_por_proposta/<int:proposta_id>')
def get_grupos_por_proposta(proposta_id):
    conn = get_db_connection()
    try:
        grupos = conn.execute('''
            SELECT g.id, g.name AS nome
            FROM groups g
            JOIN tarefa_equipes te ON g.id = te.grupo_id
            WHERE te.tarefa_id = ?
        ''', (proposta_id,)).fetchall()

        # Converter objetos Row para dicionários
        grupos_dict = [dict(grupo) for grupo in grupos]

        return jsonify(grupos_dict)
    except Exception as e:
        print(f"Erro ao buscar grupos: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/get_respostas_avaliadas/<int:proposta_id>/<int:grupo_id>')
def get_respostas_avaliadas(proposta_id, grupo_id):
    conn = get_db_connection()
    try:
        # Busca as respostas avaliadas
        respostas = conn.execute('''
            SELECT r.id, r.titulo, r.descricao, r.categorias, r.arquivos, r.observacao, r.pontuacao, g.name AS grupo_nome
            FROM respostas r
            JOIN groups g ON r.grupo_id = g.id
            WHERE r.tarefa_id = ? AND r.grupo_id = ? AND r.is_avaliada = 1
        ''', (proposta_id, grupo_id)).fetchall()

        # Buscar nomes das categorias
        categorias = conn.execute('SELECT id, categoria FROM base_pontos').fetchall()
        categorias_dict = {categoria['id']: categoria['categoria'] for categoria in categorias}

        respostas_completa = []
        for resposta in respostas:
            # Converter o objeto Row para um dicionário
            resposta_dict = dict(resposta)

            # Converter IDs das categorias para nomes
            categorias_resposta = []
            if resposta_dict['categorias']:
                for cat_id in resposta_dict['categorias'].split(','):
                    if cat_id.strip() and int(cat_id.strip()) in categorias_dict:
                        categorias_resposta.append(categorias_dict[int(cat_id.strip())])

            # Tratar o campo arquivos
            if resposta_dict['arquivos']:
                arquivos = resposta_dict['arquivos'].split(',')  # Converte a string em uma lista
            else:
                arquivos = []

            # Montar caminho dos arquivos
            caminho_arquivos = f"arquivos/proposta/proposta_{proposta_id}/grupo_{grupo_id}/resposta_{resposta_dict['id']}/"
            resposta_dict['arquivos_completos'] = [caminho_arquivos + arquivo for arquivo in arquivos]

            # Atualizar o campo categorias com os nomes
            resposta_dict['categorias'] = categorias_resposta

            # Adicionar à lista de respostas
            respostas_completa.append(resposta_dict)

        print("Respostas retornadas:", respostas_completa)  # Depuração
        return jsonify(respostas_completa)
    except Exception as e:
        print(f"Erro ao buscar respostas: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/excluir_resposta/<int:resposta_id>', methods=['DELETE'])
def excluir_resposta(resposta_id):
    try:
        conn = get_db_connection()
        resposta = conn.execute('SELECT * FROM respostas WHERE id = ?', (resposta_id,)).fetchone()
        if not resposta:
            conn.close()
            return jsonify({'success': False, 'message': 'Resposta não encontrada'}), 404

        # Obtém o líder do grupo
        grupo_id = resposta['grupo_id']
        lider = conn.execute('SELECT created_by FROM groups WHERE id = ?', (grupo_id,)).fetchone()
        if not lider:
            conn.close()
            return jsonify({'success': False, 'message': 'Líder do grupo não encontrado'}), 404

        # Exclui a pasta de arquivos da resposta
        caminho_pasta = os.path.join(
            app.root_path,
            'static',
            'arquivos',
            'proposta',
            f'proposta_{resposta["tarefa_id"]}',
            f'grupo_{grupo_id}',
            f'resposta_{resposta_id}'
        )
        if os.path.exists(caminho_pasta):
            print(f"Excluindo pasta: {caminho_pasta}")  # Depuração
            shutil.rmtree(caminho_pasta)  # Remove a pasta e seu conteúdo

        # Exclui a resposta do banco de dados
        conn.execute('DELETE FROM respostas WHERE id = ?', (resposta_id,))

        # Cria a notificação para o líder do grupo
        mensagem = f"A resposta {resposta['titulo']} foi invalidada por um avaliador. Foram retirados {resposta['pontuacao']} pontos do seu grupo."
        conn.execute('INSERT INTO notifications (user_id, message, group_id) VALUES (?, ?, ?)',
                     (lider['created_by'], mensagem, grupo_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Erro ao excluir resposta: {e}")  # Depuração
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/favoritos')
def favoritos():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    try:
        user = conn.execute(
            'SELECT is_evaluator FROM users WHERE id = ?', 
            (session['user_id'],)
        ).fetchone()
        
        if not user or user['is_evaluator'] != 1:
            return redirect(url_for('index'))
        
        respostas = conn.execute('''
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
        ''').fetchall()
        
        categorias_db = conn.execute(
            'SELECT id, categoria FROM base_pontos'
        ).fetchall()
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
            categorias_map=categorias_map
        )
        
    except Exception as e:
        app.logger.error(f"Erro ao buscar favoritos: {str(e)}", exc_info=True)
        return f"Erro ao carregar respostas favoritas: {str(e)}", 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
