from flask import Blueprint, request, jsonify, render_template, current_app
from datetime import datetime
import psycopg2.extras

from services.database import get_db_connection

cronometro_bp = Blueprint('cronometro', __name__)


@cronometro_bp.route('/iniciar_cronometro', methods=['POST'])
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
        current_app.logger.error(f"Erro ao iniciar cronômetro: {e}")
        return jsonify({'erro': 'Falha ao iniciar cronômetro'}), 500
    finally:
        cursor.close()
        conn.close()

@cronometro_bp.route('/tempo_restante')
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
        current_app.logger.error(f"Erro ao verificar tempo restante: {e}")
        return jsonify({'erro': 'Falha ao verificar tempo restante'}), 500
    finally:
        cursor.close()
        conn.close()

# Rota para a página de tempo esgotado
@cronometro_bp.route('/tempo')
def tempo_esgotado():
    return render_template('tempo.html')
