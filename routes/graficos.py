from flask import Blueprint, render_template, redirect, url_for, flash, current_app
import psycopg2.extras

from services.database import get_db_connection

graficos_bp = Blueprint('graficos', __name__)


@graficos_bp.route('/graficos')
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
        current_app.logger.error(f"Erro ao gerar gráficos: {e}")
        flash('Ocorreu um erro ao carregar os dados dos gráficos.')
        return redirect(url_for('auth.index'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()