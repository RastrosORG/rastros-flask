from flask import (
    Blueprint,
    render_template,
    request,
    session,
    redirect,
    url_for,
    flash,
    jsonify,
    current_app
)

from services.database import get_db_connection
from services.notifications import add_notification

import psycopg2.extras

avaliar_bp = Blueprint('avaliar', __name__)


@avaliar_bp.route('/avaliar_respostas', methods=['GET', 'POST'])
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
            return redirect(url_for('auth.index'))

        cursor.execute('SELECT is_evaluator FROM users WHERE id = %s', (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data or user_data['is_evaluator'] == 0:
            flash('Erro: Apenas avaliadores podem acessar esta página.', 'error')
            return redirect(url_for('auth.index'))

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
                # Primeiro, obter informações da resposta antes de atualizar
                cursor.execute('''
                    SELECT r.*, p.nome AS proposta_nome, g.name AS grupo_nome
                    FROM respostas r
                    JOIN propostas p ON r.tarefa_id = p.id
                    JOIN groups g ON r.grupo_id = g.id
                    WHERE r.id = %s
                ''', (resposta_id,))
                resposta = cursor.fetchone()

                if acao == 'aceitar':
                    cursor.execute('SELECT categorias FROM respostas WHERE id = %s', (resposta_id,))
                    resposta_categorias = cursor.fetchone()
                    pontuacao = 0
                    
                    if resposta_categorias and resposta_categorias['categorias']:
                        categoria_id = resposta_categorias['categorias'].split(',')[0]
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
                    
                    mensagem = f"A resposta '{resposta['titulo']}' para a proposta '{resposta['proposta_nome']}' foi ACEITA. Pontuação: {pontuacao}. Observação: {observacao}"

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
                    
                    mensagem = f"A resposta '{resposta['titulo']}' para a proposta '{resposta['proposta_nome']}' foi ACEITA COM ALTERAÇÕES. Nova pontuação: {pontuacao}. Observação: {observacao}"

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
                    
                    mensagem = f"A resposta '{resposta['titulo']}' para a proposta '{resposta['proposta_nome']}' foi REJEITADA. Observação: {observacao}"

                # Enviar notificação para todos os membros do grupo
                cursor.execute('''
                    SELECT user_id FROM group_members 
                    WHERE group_id = %s AND (status = 'Líder' OR status = 'Membro')
                ''', (resposta['grupo_id'],))
                membros = cursor.fetchall()
                
                for membro in membros:
                    cursor.execute('''
                        INSERT INTO notifications (user_id, message, group_id, read)
                        VALUES (%s, %s, %s, 0)
                    ''', (membro['user_id'], mensagem, resposta['grupo_id']))

                conn.commit()
                flash('Avaliação registrada com sucesso!', 'success')
                return redirect(url_for('avaliar.avaliar_respostas', proposta_id=proposta_id, grupo_id=grupo_id))

        # Restante do código permanece igual...
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
        arquivos = []
        if s3_client and S3_BUCKET_NAME:
            try:
                prefix = f"respostas/proposta_{resposta['tarefa_id']}/grupo_{resposta['grupo_id']}/resposta_{resposta['id']}/"
                response = s3_client.list_objects_v2(
                    Bucket=S3_BUCKET_NAME,
                    Prefix=prefix
                )
                arquivos = [obj['Key'].split('/')[-1] for obj in response.get('Contents', [])]
                current_app.logger.debug(f"Arquivos encontrados no S3: {arquivos}")
            except Exception as e:
                current_app.logger.error(f"Erro ao listar arquivos no S3: {e}")
                arquivos = []

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
        current_app.logger.error(f"Erro em avaliar_respostas: {e}")
        flash('Ocorreu um erro ao processar sua solicitação.', 'error')
        return redirect(url_for('auth.index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@avaliar_bp.route('/marcar_favorito/<int:resposta_id>', methods=['POST'])
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
        current_app.logger.error(f"Erro ao marcar favorito: {e}")
        return jsonify({'success': False, 'error': 'Erro interno no servidor'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@avaliar_bp.route('/excluir_resposta/<int:resposta_id>', methods=['DELETE'])
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

        # Remove os arquivos da resposta no S3 (apenas a pasta específica da resposta)
        if s3_client and S3_BUCKET_NAME:
            try:
                prefix = f"respostas/proposta_{resposta['tarefa_id']}/grupo_{grupo_id}/resposta_{resposta_id}/"
                
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
                    current_app.logger.info(f"Removendo {len(objects_to_delete)} arquivos do S3 para resposta {resposta_id}")
                    s3_client.delete_objects(
                        Bucket=S3_BUCKET_NAME,
                        Delete={'Objects': objects_to_delete}
                    )
                    
            except Exception as e:
                current_app.logger.error(f"Erro ao remover arquivos do S3: {e}")
                return jsonify({
                    'success': False,
                    'message': 'Erro ao remover arquivos da resposta no S3'
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
        current_app.logger.error(f"Erro ao excluir resposta {resposta_id}: {e}")
        return jsonify({
            'success': False,
            'message': 'Ocorreu um erro ao excluir a resposta'
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@avaliar_bp.route('/favoritos')
def favoritos():
    if 'user_id' not in session:
        return redirect(url_for('auth.index'))
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            'SELECT is_evaluator FROM users WHERE id = %s', 
            (session['user_id'],)
        )
        user = cursor.fetchone()
        
        if not user or user['is_evaluator'] != 1:
            return redirect(url_for('auth.index'))
        
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
            resposta_dict['caminho_base'] = f"respostas/proposta_{resposta_dict['proposta_id']}/grupo_{resposta_dict['grupo_id']}/resposta_{resposta_dict['id']}"
            
            respostas_processadas.append(resposta_dict)
        
        return render_template(
            'favoritos.html', 
            respostas=respostas_processadas,
            categorias_map=categorias_map,
            S3_BUCKET_NAME=os.getenv('S3_BUCKET_NAME')
        )
        
    except Exception as e:
        current_app.logger.error(f"Erro ao buscar favoritos: {str(e)}", exc_info=True)
        return f"Erro ao carregar respostas favoritas: {str(e)}", 500
    finally:
        cursor.close()
        conn.close()
