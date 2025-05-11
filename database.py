import psycopg2
from psycopg2 import sql
import os
from dotenv import load_dotenv
from psycopg2 import extras  # Adicione esta linha

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

def create_db():
    # Conexão com o PostgreSQL - use variáveis de ambiente para segurança
    conn = psycopg2.connect(
        dbname=os.getenv('POSTGRES_DB'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        host=os.getenv('POSTGRES_HOST'),
        port=os.getenv('POSTGRES_PORT', '5432')  # Render usa 5432 por padrão
    )
    conn.autocommit = True
    c = conn.cursor()

    # Verifica se o banco de dados existe, se não, cria
    try:
        c.execute("CREATE DATABASE {}".format(os.getenv('POSTGRES_DB', 'postgres')))
    except psycopg2.errors.DuplicateDatabase:
        pass  # Banco já existe
    finally:
        conn.close()

    # Reconecta ao banco de dados específico
    conn = psycopg2.connect(
        dbname=os.getenv('POSTGRES_DB', 'postgres'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD', 'postgres'),
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=os.getenv('POSTGRES_PORT', '5432')
    )
    c = conn.cursor()

    # Cria a tabela de usuários
    c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        is_evaluator INTEGER DEFAULT 0,
        evaluator_email TEXT,
        is_leader INTEGER DEFAULT 0,
        is_member INTEGER DEFAULT 0,
        is_group TEXT DEFAULT 'none'
    )
    ''')

    # Cria a tabela de grupos (precisa vir antes de notifications por causa da FK)
    c.execute('''
    CREATE TABLE IF NOT EXISTS groups (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users (id)
    )
    ''')

    # Cria a tabela de notificações
    c.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        read INTEGER NOT NULL DEFAULT 0,
        group_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (group_id) REFERENCES groups (id)
    )
    ''')

    # Cria a tabela de membros de grupos
    c.execute('''
    CREATE TABLE IF NOT EXISTS group_members (
        group_id INTEGER,
        user_id INTEGER,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'aguardando',
        PRIMARY KEY (group_id, user_id),
        FOREIGN KEY (group_id) REFERENCES groups (id),
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    # Cria a tabela de convites para grupos
    c.execute('''
    CREATE TABLE IF NOT EXISTS group_invitations (
        id SERIAL PRIMARY KEY,
        group_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        FOREIGN KEY (group_id) REFERENCES groups (id),
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    # Cria a tabela de propostas
    c.execute('''
    CREATE TABLE IF NOT EXISTS propostas (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        descricao TEXT NOT NULL,
        arquivos TEXT,
        avaliador_id INTEGER,
        FOREIGN KEY (avaliador_id) REFERENCES users (id)
    )
    ''')

    # Cria a tabela de equipes participantes das tarefas
    c.execute('''
    CREATE TABLE IF NOT EXISTS tarefa_equipes (
        tarefa_id INTEGER,
        grupo_id INTEGER,
        FOREIGN KEY (tarefa_id) REFERENCES propostas (id),
        FOREIGN KEY (grupo_id) REFERENCES groups (id)
    )
    ''')

    # Cria a tabela de respostas com a nova coluna 'pontuacao'
    c.execute('''
    CREATE TABLE IF NOT EXISTS respostas (
        id SERIAL PRIMARY KEY,
        tarefa_id INTEGER,
        grupo_id INTEGER,
        titulo TEXT NOT NULL,
        descricao TEXT NOT NULL,
        categorias TEXT,  -- Agora armazena IDs de categorias (ex.: "1,3,5")
        link TEXT,
        arquivos TEXT,
        is_avaliada INTEGER DEFAULT 0,
        is_reject INTEGER DEFAULT 0,
        is_modify INTEGER DEFAULT 0,
        is_favor INTEGER DEFAULT 0,
        observacao TEXT,
        pontuacao INTEGER DEFAULT 0,
        FOREIGN KEY (tarefa_id) REFERENCES propostas (id),
        FOREIGN KEY (grupo_id) REFERENCES groups (id)
    )
    ''')

    # Cria a tabela base_pontos
    c.execute('''
    CREATE TABLE IF NOT EXISTS base_pontos (
        id SERIAL PRIMARY KEY,
        categoria TEXT NOT NULL UNIQUE,
        valor INTEGER NOT NULL,
        detalhes TEXT
    )
    ''')

    # Insere os valores iniciais na tabela base_pontos
    categorias = [
        (1, "Notícias", 5, "Detalhes sobre amigos da pessoa."),
        (2, "Amigos", 10, "1 - Perfis de amigos em Mídias Sociais, interagindo com o alvo;\n"
                        "2 - Interações e fotos relevantes em Mídias Sociais;\n"
                        "3 - Comentários de amigos mostrando preocupação ou mencionando desaparecimento."),
        (3, "Empregos", 15, "1 - Nome do atual ou de antigo empregador;\n"
                            "2 - Endereço do atual ou de antigo empregador;\n"
                            "3 - Informações sobre o comportamento do alvo no trabalho, seus sentimentos sobre o empregador e o ambiente de trabalho, etc."),
        (4, "Família", 20, "1 - Perfis de Mídia Social de familiares relevantes;\n"
                        "2 - Comentários em Mídias Sociais de familiares relevantes;\n"
                        "3 - Quaisquer outras informações de familiares que sejam relevantes para a investigação."),
        (5, "Informações_básicas", 50, "1 - Apelidos ou abreviações;\n"
                                    "2 - Fotos relevantes:\n"
                                    "   A - Diferentes cortes de cabelo;\n"
                                    "   B - Formas de se vestir;\n"
                                    "   C - Outras características físicas não mencionadas no Report inicial;\n"
                                    "3 - Perfis e posts relevantes em fóruns;\n"
                                    "4 - Perfis e posts em sites de relacionamento;\n"
                                    "5 - Perfis de Mídias Sociais:\n"
                                    "   A - Facebook;\n"
                                    "   B - Twitter;\n"
                                    "   C - TikTok;\n"
                                    "   D - Reddit;\n"
                                    "   E - Instagram;\n"
                                    "   F - LinkedIn;\n"
                                    "   G - Github;\n"
                                    "   H - Sites adultos;\n"
                                    "   I - Gaming;\n"
                                    "   J - Etsy;\n"
                                    "   K - Pinterest;\n"
                                    "6 - Website pessoal ou blog;\n"
                                    "7 - Endereços de email;\n"
                                    "8 - Outras que possam ser relevantes para investigação."),
        (6, "Informações_avançadas", 100, "1 - Características físicas únicas:\n"
                                        "   A - Tatuagens;\n"
                                        "   B - Piercings;\n"
                                        "   C - Cicatrizes;\n"
                                        "2 - Condições médicas físicas ou psicológicas;\n"
                                        "3 - Qualquer informação sobre onde o alvo poderia ter ido:\n"
                                        "   A - Posts de mídia social, interações de mídia social ou lembranças de amigos/família;\n"
                                        "4 - Placas de veículos;\n"
                                        "5 - Marca e modelo de veículo em que o alvo pode ter viajado;\n"
                                        "6 - Histórico de desaparecimento anterior:\n"
                                        "   A - Notícias sobre outros desaparecimentos e retornos;\n"
                                        "7 - Evidências de que tenha falecido;\n"
                                        "8 - Evidências de que não esteja mais desaparecido."),
        (7, "Dia_desaparecimento", 300, "1 - Detalhes sobre a aparência física do alvo no dia do desaparecimento:\n"
                                        "   A - Roupas;\n"
                                        "   B - Cabelo, etc.;\n"
                                        "2 - Detalhes sobre o estado psicológico do alvo no dia do desaparecimento:\n"
                                        "   A - Humor;\n"
                                        "   B - Alterações;\n"
                                        "   C - Conversas;\n"
                                        "3 - Qualquer outro detalhe relevante sobre o dia do desaparecimento."),
        (8, "Atividades_pós-desaparecimento", 700, "1 - Atividade em Mídias Sociais (incluindo personas) exclusivamente controladas pelo alvo, depois do desaparecimento;\n"
                                                "2 - Informações de localização aproximada (entre a data do desaparecimento até o dia de hoje);\n"
                                                "3 - Criação de contas;\n"
                                                "4 - Imagens de CCTV."),
        (9, "DarkWeb", 1000, "1 - Tem que serem sites .onion;\n"
                            "2 - Imagens ou detalhes do alvo em sites de tráfico de pessoas;\n"
                            "3 - Venda de bens do alvo;\n"
                            "4 - Qualquer atividade ou post do alvo em fóruns."),
        (10, "Localização", 5000, "1 - Localização ou endereço exato onde o alvo tenha estado ou estará em 24h;\n"
                                "2 - Tem de ter certeza. Sem especulações."),
    ]

    # Usando ON CONFLICT DO NOTHING ao invés de INSERT OR IGNORE
    insert_query = sql.SQL('''
    INSERT INTO base_pontos (id, categoria, valor, detalhes) 
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
    ''')
    
    c.executemany(insert_query, categorias)

    c.execute('''
    CREATE TABLE IF NOT EXISTS cronometro (
        id SERIAL PRIMARY KEY,
        start_time TIMESTAMP NOT NULL,
        total_time INTEGER NOT NULL
    )
    ''')

    # Adiciona a tabela de chave de autenticação se ainda não existir
    c.execute('''
    CREATE TABLE IF NOT EXISTS evaluator_key (
        id SERIAL PRIMARY KEY,
        auth_key TEXT NOT NULL
    )
    ''')

    # Verifica se a tabela está vazia e insere a chave
    c.execute('SELECT COUNT(*) FROM evaluator_key')
    if c.fetchone()[0] == 0:
        c.execute('INSERT INTO evaluator_key (auth_key) VALUES (%s)', ('5279A3D9F6',))

    # Adiciona a tabela de chave de autenticação para ALUNO se ainda não existir
    c.execute('''
    CREATE TABLE IF NOT EXISTS aluno_key (
        id SERIAL PRIMARY KEY,
        auth_key TEXT NOT NULL
    )
    ''')

    # Verifica se a tabela está vazia e insere a chave
    c.execute('SELECT COUNT(*) FROM aluno_key')
    if c.fetchone()[0] == 0:
        c.execute('INSERT INTO aluno_key (auth_key) VALUES (%s)', ('F3A7D9C2B8',))

    conn.commit()
    conn.close()

if __name__ == '__main__':
    create_db()