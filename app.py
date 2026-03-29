from flask import Flask
from config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Registro dos Blueprints
    from routes.auth import auth_bp
    from routes.home import home_bp
    from routes.propostas import propostas_bp
    from routes.respostas import respostas_bp
    from routes.avaliar import avaliar_bp
    from routes.pontuacao import pontuacao_bp
    from routes.grupos import grupos_bp
    from routes.notificacoes import notificacoes_bp
    from routes.graficos import graficos_bp
    from routes.cronometro import cronometro_bp
    from routes.errors import errors_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(propostas_bp)
    app.register_blueprint(respostas_bp)
    app.register_blueprint(avaliar_bp)
    app.register_blueprint(pontuacao_bp)
    app.register_blueprint(grupos_bp)
    app.register_blueprint(notificacoes_bp)
    app.register_blueprint(graficos_bp)
    app.register_blueprint(cronometro_bp)
    app.register_blueprint(errors_bp)

    return app


# Execução local
if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)