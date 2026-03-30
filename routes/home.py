from flask import Blueprint, render_template, session, redirect, url_for

home_bp = Blueprint('home', __name__)


# Rotas da página HOME
@home_bp.route('/home')
def home():
    if 'username' not in session:
        return redirect(url_for('auth.index'))
    if session.get('is_evaluator') == 1:
        return redirect(url_for('home.home_avaliador'))
    return render_template('home.html', username=session['username'])

@home_bp.route('/home-avaliador')
def home_avaliador():
    if 'username' not in session or session.get('is_evaluator') != 1:
        return redirect(url_for('auth.index'))
    return render_template('home-avaliador.html', username=session['username'])