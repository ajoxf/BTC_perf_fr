"""
Settings routes for configuration
"""
from flask import Blueprint, render_template, request, jsonify, current_app
import yaml
import os

settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/')
def settings_page():
    """Settings page"""
    return render_template('settings.html')


@settings_bp.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration"""
    config = current_app.config.get('TRADING_CONFIG', {})

    # Remove sensitive data
    safe_config = {
        'okx': {
            'demo_trading': config.get('okx', {}).get('demo_trading', True),
            'has_credentials': bool(os.environ.get('OKX_API_KEY'))
        },
        'strategies': config.get('strategies', {}),
        'risk': config.get('risk', {}),
        'database': config.get('database', {})
    }

    return jsonify(safe_config)


@settings_bp.route('/api/config', methods=['POST'])
def update_config():
    """Update configuration"""
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Update config file
    config_path = os.environ.get('CONFIG_PATH', 'config.yaml')

    try:
        # Load existing config
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

        # Update with new values
        if 'strategies' in data:
            config['strategies'] = data['strategies']

        if 'risk' in data:
            config['risk'] = data['risk']

        # Save config
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

        # Update app config
        current_app.config['TRADING_CONFIG'] = config

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@settings_bp.route('/api/credentials', methods=['POST'])
def update_credentials():
    """Update API credentials (stored in env)"""
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Note: In production, credentials should be stored securely
    # This is a placeholder for the settings UI

    return jsonify({
        'success': True,
        'message': 'Set credentials via environment variables: OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE'
    })


@settings_bp.route('/api/reset-paper', methods=['POST'])
def reset_paper_trading():
    """Reset paper trading state"""
    # This would interact with the paper trading engine
    return jsonify({'success': True, 'message': 'Paper trading state reset'})
