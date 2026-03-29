from flask import Blueprint, render_template, request, redirect, url_for, session, flash
import hashlib
import psycopg2.extras

from services.database import get_db_connection

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/', methods=['GET', 'POST'])
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


# Rotas de login e cadastro
@auth_bp.route('/signup', methods=['POST'])
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

@auth_bp.route('/login', methods=['POST'])
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

@auth_bp.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('user_id', None)
    return redirect(url_for('index'))