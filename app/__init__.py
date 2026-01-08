"""
BTC Basis & Funding Rate Trading System
Flask application factory
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
import yaml

db = SQLAlchemy()
migrate = Migrate()

# Get the base directory (project root)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app(config_path: str = None) -> Flask:
    """Create and configure the Flask application"""
    app = Flask(__name__)

    # Load configuration
    config_file = config_path or os.environ.get('CONFIG_PATH', 'config.yaml')
    if os.path.exists(config_file):
        with open(config_file) as f:
            config = yaml.safe_load(f)
    else:
        config = {}

    # Ensure required directories exist (use absolute paths)
    db_relative_path = config.get('database', {}).get('path', 'instance/trading.db')
    db_path = os.path.join(BASE_DIR, db_relative_path)
    db_dir = os.path.dirname(db_path)
    logs_dir = os.path.join(BASE_DIR, 'logs')

    os.makedirs(db_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)

    # Flask config - use absolute path for SQLite
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Store trading config
    app.config['TRADING_CONFIG'] = config

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # Register blueprints
    from app.routes.dashboard import dashboard_bp
    from app.routes.api_routes import api_bp
    from app.routes.settings import settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(settings_bp, url_prefix='/settings')

    # Create database tables
    with app.app_context():
        db.create_all()

    return app
