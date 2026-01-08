"""
REST API routes for frontend
"""
from flask import Blueprint, jsonify, request, current_app
from datetime import datetime
from typing import Dict, Any

from app.models import Position, Trade, FundingHistory, BasisHistory
from app import db

api_bp = Blueprint('api', __name__)


@api_bp.route('/status')
def status():
    """Get system status"""
    # This would be populated by the trading engine
    return jsonify({
        'status': 'running',
        'timestamp': datetime.utcnow().isoformat(),
        'paper_mode': True
    })


@api_bp.route('/market-data')
def market_data():
    """Get current market data"""
    # This would be populated by the data collector
    return jsonify({
        'spot': {
            'instrument': 'BTC-USDT',
            'price': 0,
            'timestamp': datetime.utcnow().isoformat()
        },
        'perp': {
            'instrument': 'BTC-USDT-SWAP',
            'price': 0,
            'funding_rate': 0,
            'predicted_rate': 0,
            'timestamp': datetime.utcnow().isoformat()
        },
        'futures': []
    })


@api_bp.route('/strategies')
def strategies():
    """Get strategy status"""
    return jsonify({
        'basis': {
            'enabled': True,
            'has_position': False,
            'current_zscore': 0,
            'current_hurst': 0.5,
            'signal': 'none'
        },
        'funding': {
            'enabled': True,
            'has_position': False,
            'current_rate': 0,
            'current_zscore': 0,
            'signal': 'none'
        }
    })


@api_bp.route('/positions')
def get_positions():
    """Get all positions"""
    positions = Position.query.filter_by(status='open').all()

    return jsonify([{
        'id': p.id,
        'strategy': p.strategy,
        'opened_at': p.opened_at.isoformat(),
        'spot_instrument': p.spot_instrument,
        'derivative_instrument': p.derivative_instrument,
        'entry_spot_price': p.entry_spot_price,
        'entry_derivative_price': p.entry_derivative_price,
        'spot_size': p.spot_size,
        'derivative_size': p.derivative_size,
        'unrealized_pnl': p.unrealized_pnl or 0,
        'total_funding': p.total_funding or 0
    } for p in positions])


@api_bp.route('/positions/history')
def position_history():
    """Get closed positions"""
    positions = Position.query.filter_by(status='closed').order_by(
        Position.closed_at.desc()
    ).limit(50).all()

    return jsonify([{
        'id': p.id,
        'strategy': p.strategy,
        'opened_at': p.opened_at.isoformat(),
        'closed_at': p.closed_at.isoformat() if p.closed_at else None,
        'exit_reason': p.exit_reason,
        'realized_pnl': p.realized_pnl,
        'total_funding': p.total_funding,
        'net_pnl': (p.realized_pnl or 0) + (p.total_funding or 0)
    } for p in positions])


@api_bp.route('/trades')
def get_trades():
    """Get trade history"""
    trades = Trade.query.order_by(Trade.timestamp.desc()).limit(100).all()

    return jsonify([{
        'id': t.id,
        'strategy': t.strategy,
        'trade_type': t.trade_type,
        'timestamp': t.timestamp.isoformat(),
        'spot_side': t.spot_side,
        'spot_instrument': t.spot_instrument,
        'spot_price': t.spot_price,
        'derivative_side': t.derivative_side,
        'derivative_instrument': t.derivative_instrument,
        'derivative_price': t.derivative_price,
        'signal_value': t.signal_value,
        'realized_pnl': t.realized_pnl
    } for t in trades])


@api_bp.route('/funding-history')
def funding_history():
    """Get funding rate history"""
    history = FundingHistory.query.order_by(
        FundingHistory.timestamp.desc()
    ).limit(100).all()

    return jsonify([{
        'timestamp': h.timestamp.isoformat(),
        'funding_rate': h.funding_rate,
        'zscore': h.z_score
    } for h in history])


@api_bp.route('/basis-history')
def basis_history():
    """Get basis history"""
    history = BasisHistory.query.order_by(
        BasisHistory.timestamp.desc()
    ).limit(100).all()

    return jsonify([{
        'timestamp': h.timestamp.isoformat(),
        'futures_id': h.futures_id,
        'basis_pct': h.basis_pct,
        'zscore': h.z_score
    } for h in history])


@api_bp.route('/risk')
def risk_status():
    """Get risk management status"""
    return jsonify({
        'daily_pnl': 0,
        'daily_loss_remaining': 1000,
        'current_exposure': 0,
        'max_exposure': 25000,
        'limits': {
            'max_position': 10000,
            'max_loss_per_trade': 500,
            'max_daily_loss': 1000
        }
    })


@api_bp.route('/strategy/<strategy_name>/enable', methods=['POST'])
def enable_strategy(strategy_name: str):
    """Enable a strategy"""
    # This would interact with the trading engine
    return jsonify({'success': True, 'strategy': strategy_name, 'enabled': True})


@api_bp.route('/strategy/<strategy_name>/disable', methods=['POST'])
def disable_strategy(strategy_name: str):
    """Disable a strategy"""
    return jsonify({'success': True, 'strategy': strategy_name, 'enabled': False})


@api_bp.route('/position/<int:position_id>/close', methods=['POST'])
def close_position(position_id: int):
    """Manually close a position"""
    # This would interact with the trading engine
    return jsonify({'success': True, 'position_id': position_id})
