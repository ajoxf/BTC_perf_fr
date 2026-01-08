"""
Dashboard routes for web interface
"""
from flask import Blueprint, render_template, jsonify
from datetime import datetime

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')


@dashboard_bp.route('/positions')
def positions():
    """Positions page"""
    return render_template('positions.html')


@dashboard_bp.route('/trades')
def trades():
    """Trade history page"""
    return render_template('trades.html')


@dashboard_bp.route('/signals')
def signals():
    """Signals visualization page"""
    return render_template('signals.html')
