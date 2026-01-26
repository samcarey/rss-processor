from flask import Flask
import yaml
import os


def load_config():
    """Load configuration from config.yaml"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def create_app():
    """Flask application factory"""
    app = Flask(__name__)

    # Load configuration
    config = load_config()
    app.config['APP_CONFIG'] = config
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Ensure storage directories exist
    os.makedirs(config['storage']['original'], exist_ok=True)
    os.makedirs(config['storage']['processed'], exist_ok=True)
    os.makedirs(config['storage']['segments'], exist_ok=True)

    # Initialize database
    from app.database import init_db, Session
    init_db()

    # Register session cleanup
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        if Session is not None:
            Session.remove()

    # Register routes
    from app import routes
    app.register_blueprint(routes.bp)

    return app
