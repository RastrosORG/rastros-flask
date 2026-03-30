from flask import (
    Blueprint,
    render_template,
    request,
    session,
    redirect,
    url_for,
    flash,
    jsonify
)

from services.database import get_db_connection
from services.notifications import add_notification  # se alguma rota notificar

import psycopg2.extras

pontuacao_bp = Blueprint('pontuacao', __name__)


# Rota para a página de pontuação
@pontuacao_bp.route('/pontuacao')
def pontuacao():
    if 'user_id' not in session:
        flash('Por favor, faça login para acessar esta página.', 'error')
        return redirect(url_for('auth.index'))

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
            return redirect(url_for('grupos.group_request_alt'))

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
        return redirect(url_for('auth.index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@pontuacao_bp.route('/pontuacao_avaliador')
def pontuacao_avaliador():
    # Verificação de autenticação
    if 'user_id' not in session:
        flash('Por favor, faça login para acessar esta página.', 'error')
        return redirect(url_for('auth.index'))

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
            return redirect(url_for('auth.index'))

        # Busca todas as propostas (tarefas)
        cursor.execute('SELECT id, nome, descricao FROM propostas')
        propostas = cursor.fetchall()

        # Debug opcional (remova em produção)
        app.logger.debug(f"Propostas encontradas: {propostas}")

        return render_template('pontuacao_avaliador.html', 
               propostas=propostas,
               S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME')
               )

    except Exception as e:
        app.logger.error(f"Erro em pontuacao_avaliador: {e}")
        flash('Ocorreu um erro ao carregar as propostas.', 'error')
        return redirect(url_for('auth.index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@pontuacao_bp.route('/get_grupos_por_proposta/<int:proposta_id>')
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

# ROTA AUXILIAR DE PONTUACAO_AVALIADOR
@pontuacao_bp.route('/get_respostas_avaliadas/<int:proposta_id>/<int:grupo_id>')
def get_respostas_avaliadas(proposta_id, grupo_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Busca as respostas avaliadas (mantendo 1 para booleanos)
        cursor.execute('''
            SELECT r.id, r.titulo, r.descricao, r.categorias, r.arquivos, 
                   r.observacao, r.pontuacao, g.name AS grupo_nome, r.tarefa_id, r.grupo_id
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

            # Processar arquivos - agora vamos buscar diretamente do S3
            arquivos = []
            arquivos_completos = []
            
            if s3_client and S3_BUCKET_NAME:
                try:
                    prefix = f"respostas/proposta_{resposta_dict['tarefa_id']}/grupo_{resposta_dict['grupo_id']}/resposta_{resposta_dict['id']}/"
                    response = s3_client.list_objects_v2(
                        Bucket=S3_BUCKET_NAME,
                        Prefix=prefix
                    )
                    arquivos_completos = [obj['Key'] for obj in response.get('Contents', [])]
                    arquivos = [arq.split('/')[-1] for arq in arquivos_completos]
                except Exception as e:
                    app.logger.error(f"Erro ao listar arquivos no S3: {e}")

            # Montar resposta completa
            resposta_completa = {
                'id': resposta_dict['id'],
                'titulo': resposta_dict['titulo'],
                'descricao': resposta_dict['descricao'],
                'categorias': categorias_resposta,
                'arquivos': arquivos,
                'arquivos_completos': arquivos_completos,
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

@pontuacao_bp.route('/get_categorias')
def get_categorias():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT id, categoria, valor, detalhes FROM base_pontos ORDER BY valor DESC')
        categorias = cursor.fetchall()
        return jsonify([dict(categoria) for categoria in categorias])
    except Exception as e:
        app.logger.error(f"Erro ao buscar categorias: {e}")
        return jsonify({'error': 'Erro ao buscar categorias'}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@pontuacao_bp.route('/alterar_pontuacao_resposta/<int:resposta_id>', methods=['POST'])
def alterar_pontuacao_resposta(resposta_id):
    try:
        data = request.get_json()
        categoria_id = data.get('categoria_id')
        pontos = data.get('pontos')
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # 1. Atualiza a pontuação da resposta
        cursor.execute('''
            UPDATE respostas 
            SET categorias = %s, pontuacao = %s 
            WHERE id = %s
            RETURNING titulo, grupo_id, tarefa_id
        ''', (str(categoria_id), pontos, resposta_id))
        
        resposta = cursor.fetchone()
        
        # 2. Obtém o nome da proposta
        cursor.execute('SELECT nome FROM propostas WHERE id = %s', (resposta['tarefa_id'],))
        proposta_nome = cursor.fetchone()['nome']
        
        # 3. Obtém os membros do grupo
        cursor.execute('''
            SELECT user_id FROM group_members 
            WHERE group_id = %s AND status IN ('Líder', 'Membro')
        ''', (resposta['grupo_id'],))
        membros = cursor.fetchall()
        
        # 4. Cria notificação para cada membro do grupo
        for membro in membros:
            mensagem = f"A pontuação da resposta '{resposta['titulo']}' para a proposta '{proposta_nome}' foi alterada para {pontos} pontos"
            
            cursor.execute('''
                INSERT INTO notifications (user_id, message, group_id)
                VALUES (%s, %s, %s)
            ''', (membro['user_id'], mensagem, resposta['grupo_id']))
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Erro ao alterar pontuação: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()