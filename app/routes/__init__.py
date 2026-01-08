"""Flask route blueprints"""
from app.routes.dashboard import dashboard_bp
from app.routes.api_routes import api_bp
from app.routes.settings import settings_bp

__all__ = ['dashboard_bp', 'api_bp', 'settings_bp']
