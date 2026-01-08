"""
SQLAlchemy database models
"""
from datetime import datetime
from app import db


class Setting(db.Model):
    """Configuration settings stored in database"""
    __tablename__ = 'settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceHistory(db.Model):
    """Historical price data"""
    __tablename__ = 'price_history'

    id = db.Column(db.Integer, primary_key=True)
    instrument_id = db.Column(db.String(50), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False, index=True)
    price = db.Column(db.Float, nullable=False)
    volume = db.Column(db.Float)

    __table_args__ = (
        db.UniqueConstraint('instrument_id', 'timestamp', name='uq_price_inst_time'),
    )


class BasisHistory(db.Model):
    """Historical basis spread data"""
    __tablename__ = 'basis_history'

    id = db.Column(db.Integer, primary_key=True)
    futures_id = db.Column(db.String(50), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False, index=True)
    spot_price = db.Column(db.Float, nullable=False)
    futures_price = db.Column(db.Float, nullable=False)
    basis = db.Column(db.Float, nullable=False)
    basis_pct = db.Column(db.Float, nullable=False)
    z_score = db.Column(db.Float)

    __table_args__ = (
        db.UniqueConstraint('futures_id', 'timestamp', name='uq_basis_fut_time'),
    )


class FundingHistory(db.Model):
    """Historical funding rate data"""
    __tablename__ = 'funding_history'

    id = db.Column(db.Integer, primary_key=True)
    instrument_id = db.Column(db.String(50), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False, index=True)
    funding_rate = db.Column(db.Float, nullable=False)
    predicted_rate = db.Column(db.Float)
    z_score = db.Column(db.Float)

    __table_args__ = (
        db.UniqueConstraint('instrument_id', 'timestamp', name='uq_funding_inst_time'),
    )


class Trade(db.Model):
    """Trade journal entries"""
    __tablename__ = 'trades'

    id = db.Column(db.Integer, primary_key=True)
    strategy = db.Column(db.String(20), nullable=False)  # 'basis' or 'funding'
    trade_type = db.Column(db.String(10), nullable=False)  # 'entry' or 'exit'
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Spot leg
    spot_side = db.Column(db.String(10))  # 'long' or 'short'
    spot_instrument = db.Column(db.String(50))
    spot_size = db.Column(db.Float)
    spot_price = db.Column(db.Float)

    # Derivative leg
    derivative_side = db.Column(db.String(10))
    derivative_instrument = db.Column(db.String(50))
    derivative_size = db.Column(db.Float)
    derivative_price = db.Column(db.Float)

    # Signal info
    signal_value = db.Column(db.Float)
    signal_type = db.Column(db.String(20))  # 'zscore', 'time_stop', 'loss_stop'

    # P&L (populated on exit)
    realized_pnl = db.Column(db.Float)
    funding_collected = db.Column(db.Float)
    fees_paid = db.Column(db.Float)

    notes = db.Column(db.Text)

    position_id = db.Column(db.Integer, db.ForeignKey('positions.id'))


class Position(db.Model):
    """Active and historical positions"""
    __tablename__ = 'positions'

    id = db.Column(db.Integer, primary_key=True)
    strategy = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(10), default='open')  # 'open' or 'closed'
    opened_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    # Instruments
    spot_instrument = db.Column(db.String(50))
    derivative_instrument = db.Column(db.String(50))

    # Entry details
    entry_spot_price = db.Column(db.Float)
    entry_derivative_price = db.Column(db.Float)
    entry_signal = db.Column(db.Float)

    # Position size
    spot_size = db.Column(db.Float)
    derivative_size = db.Column(db.Float)

    # Current state
    unrealized_pnl = db.Column(db.Float, default=0)
    total_funding = db.Column(db.Float, default=0)

    # Exit details
    exit_spot_price = db.Column(db.Float)
    exit_derivative_price = db.Column(db.Float)
    exit_signal = db.Column(db.Float)
    exit_reason = db.Column(db.String(50))

    # Final P&L
    realized_pnl = db.Column(db.Float)
    total_fees = db.Column(db.Float)

    # Relationships
    trades = db.relationship('Trade', backref='position', lazy='dynamic')
    funding_payments = db.relationship('FundingPayment', backref='position', lazy='dynamic')


class FundingPayment(db.Model):
    """Funding payments received or paid"""
    __tablename__ = 'funding_payments'

    id = db.Column(db.Integer, primary_key=True)
    position_id = db.Column(db.Integer, db.ForeignKey('positions.id'))
    timestamp = db.Column(db.DateTime, nullable=False)
    instrument_id = db.Column(db.String(50), nullable=False)
    funding_rate = db.Column(db.Float, nullable=False)
    position_size = db.Column(db.Float, nullable=False)
    payment_amount = db.Column(db.Float, nullable=False)  # positive = received
    mark_price = db.Column(db.Float)
