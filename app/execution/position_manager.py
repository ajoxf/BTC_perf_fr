"""
Position management and tracking
"""
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from loguru import logger

from app.models import Position, Trade, FundingPayment
from app import db


@dataclass
class LivePosition:
    """Active position state"""
    id: int
    strategy: str
    side: str  # 'long_spread' or 'short_spread'

    # Instruments
    spot_instrument: str
    derivative_instrument: str

    # Entry
    entry_spot_price: float
    entry_derivative_price: float
    entry_time: datetime
    entry_signal: float

    # Size
    spot_size: float
    derivative_size: float
    notional_usd: float

    # Current state
    current_spot_price: float = 0.0
    current_derivative_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    total_funding: float = 0.0
    settlements_count: int = 0

    def update_prices(self, spot_price: float, derivative_price: float) -> None:
        """Update current prices and calculate P&L"""
        self.current_spot_price = spot_price
        self.current_derivative_price = derivative_price

        # Calculate P&L based on position side
        if self.side == 'long_spread':
            # Long spot, short derivative
            spot_pnl = (spot_price - self.entry_spot_price) * self.spot_size
            deriv_pnl = (self.entry_derivative_price - derivative_price) * self.derivative_size
        else:
            # Short spot, long derivative
            spot_pnl = (self.entry_spot_price - spot_price) * self.spot_size
            deriv_pnl = (derivative_price - self.entry_derivative_price) * self.derivative_size

        self.unrealized_pnl = spot_pnl + deriv_pnl
        self.unrealized_pnl_pct = self.unrealized_pnl / self.notional_usd if self.notional_usd else 0

    def add_funding(self, amount: float) -> None:
        """Add funding payment"""
        self.total_funding += amount
        self.settlements_count += 1


class PositionManager:
    """Manage trading positions"""

    def __init__(self):
        self._positions: Dict[int, LivePosition] = {}

    def open_position(
        self,
        strategy: str,
        side: str,
        spot_instrument: str,
        derivative_instrument: str,
        spot_price: float,
        derivative_price: float,
        spot_size: float,
        derivative_size: float,
        signal_value: float
    ) -> LivePosition:
        """
        Open a new position.

        Args:
            strategy: Strategy name
            side: 'long_spread' or 'short_spread'
            spot_instrument: Spot instrument ID
            derivative_instrument: Derivative instrument ID
            spot_price: Entry spot price
            derivative_price: Entry derivative price
            spot_size: Spot position size
            derivative_size: Derivative position size
            signal_value: Entry signal value (z-score)

        Returns:
            LivePosition object
        """
        # Calculate notional
        notional = (spot_price * spot_size + derivative_price * derivative_size) / 2

        # Create database record
        db_position = Position(
            strategy=strategy,
            status='open',
            opened_at=datetime.utcnow(),
            spot_instrument=spot_instrument,
            derivative_instrument=derivative_instrument,
            entry_spot_price=spot_price,
            entry_derivative_price=derivative_price,
            entry_signal=signal_value,
            spot_size=spot_size,
            derivative_size=derivative_size,
            unrealized_pnl=0,
            total_funding=0
        )
        db.session.add(db_position)
        db.session.commit()

        # Create entry trade record
        spot_side = 'long' if side == 'long_spread' else 'short'
        deriv_side = 'short' if side == 'long_spread' else 'long'

        entry_trade = Trade(
            strategy=strategy,
            trade_type='entry',
            position_id=db_position.id,
            spot_side=spot_side,
            spot_instrument=spot_instrument,
            spot_size=spot_size,
            spot_price=spot_price,
            derivative_side=deriv_side,
            derivative_instrument=derivative_instrument,
            derivative_size=derivative_size,
            derivative_price=derivative_price,
            signal_value=signal_value,
            signal_type='zscore'
        )
        db.session.add(entry_trade)
        db.session.commit()

        # Create live position object
        live_pos = LivePosition(
            id=db_position.id,
            strategy=strategy,
            side=side,
            spot_instrument=spot_instrument,
            derivative_instrument=derivative_instrument,
            entry_spot_price=spot_price,
            entry_derivative_price=derivative_price,
            entry_time=db_position.opened_at,
            entry_signal=signal_value,
            spot_size=spot_size,
            derivative_size=derivative_size,
            notional_usd=notional,
            current_spot_price=spot_price,
            current_derivative_price=derivative_price
        )

        self._positions[db_position.id] = live_pos

        logger.info(f"Opened position {db_position.id}: {strategy} {side} "
                   f"notional=${notional:.2f}")

        return live_pos

    def close_position(
        self,
        position_id: int,
        spot_price: float,
        derivative_price: float,
        signal_value: float,
        reason: str
    ) -> Dict[str, float]:
        """
        Close a position.

        Args:
            position_id: Position ID to close
            spot_price: Exit spot price
            derivative_price: Exit derivative price
            signal_value: Exit signal value
            reason: Reason for closing

        Returns:
            Dictionary with P&L details
        """
        live_pos = self._positions.get(position_id)
        if not live_pos:
            logger.error(f"Position {position_id} not found")
            return {}

        # Update with final prices
        live_pos.update_prices(spot_price, derivative_price)

        # Calculate final P&L
        realized_pnl = live_pos.unrealized_pnl
        net_pnl = realized_pnl + live_pos.total_funding

        # Update database
        db_position = Position.query.get(position_id)
        if db_position:
            db_position.status = 'closed'
            db_position.closed_at = datetime.utcnow()
            db_position.exit_spot_price = spot_price
            db_position.exit_derivative_price = derivative_price
            db_position.exit_signal = signal_value
            db_position.exit_reason = reason
            db_position.realized_pnl = realized_pnl
            db_position.total_funding = live_pos.total_funding

            # Create exit trade record
            spot_side = 'short' if live_pos.side == 'long_spread' else 'long'
            deriv_side = 'long' if live_pos.side == 'long_spread' else 'short'

            exit_trade = Trade(
                strategy=live_pos.strategy,
                trade_type='exit',
                position_id=position_id,
                spot_side=spot_side,
                spot_instrument=live_pos.spot_instrument,
                spot_size=live_pos.spot_size,
                spot_price=spot_price,
                derivative_side=deriv_side,
                derivative_instrument=live_pos.derivative_instrument,
                derivative_size=live_pos.derivative_size,
                derivative_price=derivative_price,
                signal_value=signal_value,
                signal_type=reason,
                realized_pnl=realized_pnl,
                funding_collected=live_pos.total_funding
            )
            db.session.add(exit_trade)
            db.session.commit()

        # Remove from active positions
        del self._positions[position_id]

        result = {
            'realized_pnl': realized_pnl,
            'funding_collected': live_pos.total_funding,
            'net_pnl': net_pnl,
            'pnl_pct': live_pos.unrealized_pnl_pct,
            'reason': reason
        }

        logger.info(f"Closed position {position_id}: pnl=${realized_pnl:.2f}, "
                   f"funding=${live_pos.total_funding:.4f}, net=${net_pnl:.2f}")

        return result

    def record_funding_payment(
        self,
        position_id: int,
        funding_rate: float,
        mark_price: float
    ) -> float:
        """
        Record a funding payment for a position.

        Args:
            position_id: Position ID
            funding_rate: Current funding rate
            mark_price: Mark price at settlement

        Returns:
            Funding amount (positive = received)
        """
        live_pos = self._positions.get(position_id)
        if not live_pos:
            return 0.0

        # Calculate funding amount
        # For long_spread: short derivative, so if funding > 0, we receive
        # For short_spread: long derivative, so if funding > 0, we pay
        position_value = live_pos.derivative_size * mark_price

        if live_pos.side == 'long_spread':
            # Short perp - receive positive funding, pay negative
            funding_amount = position_value * funding_rate
        else:
            # Long perp - pay positive funding, receive negative
            funding_amount = -position_value * funding_rate

        live_pos.add_funding(funding_amount)

        # Record in database
        payment = FundingPayment(
            position_id=position_id,
            timestamp=datetime.utcnow(),
            instrument_id=live_pos.derivative_instrument,
            funding_rate=funding_rate,
            position_size=live_pos.derivative_size,
            payment_amount=funding_amount,
            mark_price=mark_price
        )
        db.session.add(payment)
        db.session.commit()

        logger.debug(f"Funding payment for position {position_id}: ${funding_amount:.4f}")

        return funding_amount

    def update_position_prices(
        self,
        position_id: int,
        spot_price: float,
        derivative_price: float
    ) -> None:
        """Update position with current prices"""
        live_pos = self._positions.get(position_id)
        if live_pos:
            live_pos.update_prices(spot_price, derivative_price)

            # Update database
            db_position = Position.query.get(position_id)
            if db_position:
                db_position.unrealized_pnl = live_pos.unrealized_pnl
                db.session.commit()

    def get_position(self, position_id: int) -> Optional[LivePosition]:
        """Get position by ID"""
        return self._positions.get(position_id)

    def get_positions_by_strategy(self, strategy: str) -> List[LivePosition]:
        """Get all positions for a strategy"""
        return [p for p in self._positions.values() if p.strategy == strategy]

    def get_all_positions(self) -> List[LivePosition]:
        """Get all active positions"""
        return list(self._positions.values())

    def has_position(self, strategy: str) -> bool:
        """Check if strategy has an open position"""
        return any(p.strategy == strategy for p in self._positions.values())

    def get_total_exposure(self) -> float:
        """Get total USD exposure across all positions"""
        return sum(p.notional_usd for p in self._positions.values())

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L"""
        return sum(p.unrealized_pnl for p in self._positions.values())

    def load_open_positions(self) -> None:
        """Load open positions from database on startup"""
        open_positions = Position.query.filter_by(status='open').all()

        for db_pos in open_positions:
            live_pos = LivePosition(
                id=db_pos.id,
                strategy=db_pos.strategy,
                side='long_spread',  # Determine from entry trade
                spot_instrument=db_pos.spot_instrument,
                derivative_instrument=db_pos.derivative_instrument,
                entry_spot_price=db_pos.entry_spot_price,
                entry_derivative_price=db_pos.entry_derivative_price,
                entry_time=db_pos.opened_at,
                entry_signal=db_pos.entry_signal,
                spot_size=db_pos.spot_size,
                derivative_size=db_pos.derivative_size,
                notional_usd=(db_pos.entry_spot_price * db_pos.spot_size +
                             db_pos.entry_derivative_price * db_pos.derivative_size) / 2,
                total_funding=db_pos.total_funding or 0
            )

            # Determine side from entry trade
            entry_trade = Trade.query.filter_by(
                position_id=db_pos.id,
                trade_type='entry'
            ).first()
            if entry_trade and entry_trade.spot_side == 'short':
                live_pos.side = 'short_spread'

            # Count funding settlements
            live_pos.settlements_count = FundingPayment.query.filter_by(
                position_id=db_pos.id
            ).count()

            self._positions[db_pos.id] = live_pos

        logger.info(f"Loaded {len(open_positions)} open positions from database")
