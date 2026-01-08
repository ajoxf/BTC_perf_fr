"""
Paper trading simulation engine
"""
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import uuid
from loguru import logger


@dataclass
class PaperOrder:
    """Simulated order"""
    order_id: str
    inst_id: str
    side: str  # 'buy' or 'sell'
    order_type: str  # 'market', 'limit'
    size: float
    price: Optional[float]
    status: str = "filled"  # Assume immediate fill for market orders
    filled_price: float = 0.0
    filled_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PaperPosition:
    """Simulated position"""
    inst_id: str
    side: str  # 'long' or 'short'
    size: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    def update_price(self, price: float) -> None:
        self.current_price = price
        if self.side == 'long':
            self.unrealized_pnl = (price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.size


class PaperTradingEngine:
    """Simulate trading without real orders"""

    def __init__(self, initial_balance: float = 100000.0):
        """
        Initialize paper trading engine.

        Args:
            initial_balance: Starting balance in USD
        """
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.realized_pnl = 0.0
        self.total_fees = 0.0

        self._orders: Dict[str, PaperOrder] = {}
        self._positions: Dict[str, PaperPosition] = {}
        self._trade_history: List[Dict] = []

        # Fee configuration (OKX taker fees)
        self.spot_fee = 0.001  # 0.10%
        self.derivative_fee = 0.0005  # 0.05%

    def place_order(
        self,
        inst_id: str,
        side: str,
        order_type: str,
        size: float,
        current_price: float,
        price: float = None
    ) -> PaperOrder:
        """
        Simulate placing an order.

        Args:
            inst_id: Instrument ID
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit'
            size: Order size
            current_price: Current market price
            price: Limit price (ignored for market orders)

        Returns:
            Simulated order
        """
        order_id = f"paper_{uuid.uuid4().hex[:8]}"

        # Determine fill price
        if order_type == 'market':
            # Add slippage simulation (0.01%)
            slippage = 0.0001
            if side == 'buy':
                fill_price = current_price * (1 + slippage)
            else:
                fill_price = current_price * (1 - slippage)
        else:
            fill_price = price if price else current_price

        order = PaperOrder(
            order_id=order_id,
            inst_id=inst_id,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
            status='filled',
            filled_price=fill_price,
            filled_at=datetime.utcnow()
        )

        self._orders[order_id] = order

        # Calculate and deduct fees
        is_derivative = 'SWAP' in inst_id or inst_id.count('-') > 1
        fee_rate = self.derivative_fee if is_derivative else self.spot_fee
        fee = size * fill_price * fee_rate
        self.total_fees += fee
        self.balance -= fee

        # Update positions
        self._update_position(order)

        # Record trade
        self._trade_history.append({
            'timestamp': datetime.utcnow(),
            'order_id': order_id,
            'inst_id': inst_id,
            'side': side,
            'size': size,
            'price': fill_price,
            'fee': fee
        })

        logger.info(f"[PAPER] {side.upper()} {size:.6f} {inst_id} @ {fill_price:.2f} "
                   f"(fee: ${fee:.4f})")

        return order

    def _update_position(self, order: PaperOrder) -> None:
        """Update positions based on filled order"""
        inst_id = order.inst_id
        existing = self._positions.get(inst_id)

        if existing:
            # Modify existing position
            if (order.side == 'buy' and existing.side == 'long') or \
               (order.side == 'sell' and existing.side == 'short'):
                # Adding to position
                total_size = existing.size + order.size
                avg_price = (existing.entry_price * existing.size +
                            order.filled_price * order.size) / total_size
                existing.size = total_size
                existing.entry_price = avg_price
            else:
                # Reducing or closing position
                if order.size >= existing.size:
                    # Close position
                    pnl = existing.unrealized_pnl
                    self.realized_pnl += pnl
                    self.balance += pnl
                    del self._positions[inst_id]

                    remaining = order.size - existing.size
                    if remaining > 0:
                        # Open opposite position with remaining
                        new_side = 'long' if order.side == 'buy' else 'short'
                        self._positions[inst_id] = PaperPosition(
                            inst_id=inst_id,
                            side=new_side,
                            size=remaining,
                            entry_price=order.filled_price,
                            current_price=order.filled_price
                        )
                else:
                    # Partial close
                    close_ratio = order.size / existing.size
                    partial_pnl = existing.unrealized_pnl * close_ratio
                    self.realized_pnl += partial_pnl
                    self.balance += partial_pnl
                    existing.size -= order.size
        else:
            # Open new position
            side = 'long' if order.side == 'buy' else 'short'
            self._positions[inst_id] = PaperPosition(
                inst_id=inst_id,
                side=side,
                size=order.size,
                entry_price=order.filled_price,
                current_price=order.filled_price
            )

    def update_prices(self, prices: Dict[str, float]) -> None:
        """Update all position prices"""
        for inst_id, price in prices.items():
            if inst_id in self._positions:
                self._positions[inst_id].update_price(price)

    def execute_spread_entry(
        self,
        side: str,  # 'long_spread' or 'short_spread'
        spot_inst: str,
        derivative_inst: str,
        size_usd: float,
        spot_price: float,
        derivative_price: float
    ) -> Dict[str, PaperOrder]:
        """
        Execute spread entry (two legs).

        Returns:
            Dictionary with spot and derivative orders
        """
        spot_size = size_usd / spot_price
        derivative_size = size_usd / derivative_price

        if side == 'long_spread':
            spot_side = 'buy'
            derivative_side = 'sell'
        else:
            spot_side = 'sell'
            derivative_side = 'buy'

        spot_order = self.place_order(
            inst_id=spot_inst,
            side=spot_side,
            order_type='market',
            size=spot_size,
            current_price=spot_price
        )

        derivative_order = self.place_order(
            inst_id=derivative_inst,
            side=derivative_side,
            order_type='market',
            size=derivative_size,
            current_price=derivative_price
        )

        return {
            'spot': spot_order,
            'derivative': derivative_order
        }

    def execute_spread_exit(
        self,
        side: str,  # 'long_spread' or 'short_spread'
        spot_inst: str,
        derivative_inst: str,
        spot_size: float,
        derivative_size: float,
        spot_price: float,
        derivative_price: float
    ) -> Dict[str, PaperOrder]:
        """Execute spread exit (close both legs)"""
        if side == 'long_spread':
            spot_side = 'sell'
            derivative_side = 'buy'
        else:
            spot_side = 'buy'
            derivative_side = 'sell'

        spot_order = self.place_order(
            inst_id=spot_inst,
            side=spot_side,
            order_type='market',
            size=spot_size,
            current_price=spot_price
        )

        derivative_order = self.place_order(
            inst_id=derivative_inst,
            side=derivative_side,
            order_type='market',
            size=derivative_size,
            current_price=derivative_price
        )

        return {
            'spot': spot_order,
            'derivative': derivative_order
        }

    def simulate_funding_payment(
        self,
        derivative_inst: str,
        funding_rate: float,
        mark_price: float
    ) -> float:
        """
        Simulate funding payment for derivative position.

        Returns:
            Funding amount (positive = received)
        """
        position = self._positions.get(derivative_inst)
        if not position:
            return 0.0

        position_value = position.size * mark_price

        # Long pays positive funding, short receives positive funding
        if position.side == 'short':
            funding_amount = position_value * funding_rate
        else:
            funding_amount = -position_value * funding_rate

        self.realized_pnl += funding_amount
        self.balance += funding_amount

        logger.info(f"[PAPER] Funding payment: ${funding_amount:.4f} "
                   f"(rate: {funding_rate:.4%})")

        return funding_amount

    def get_position(self, inst_id: str) -> Optional[PaperPosition]:
        """Get position for instrument"""
        return self._positions.get(inst_id)

    def get_all_positions(self) -> List[PaperPosition]:
        """Get all positions"""
        return list(self._positions.values())

    def get_unrealized_pnl(self) -> float:
        """Get total unrealized P&L"""
        return sum(p.unrealized_pnl for p in self._positions.values())

    def get_total_pnl(self) -> float:
        """Get total P&L (realized + unrealized)"""
        return self.realized_pnl + self.get_unrealized_pnl()

    def get_account_value(self) -> float:
        """Get total account value"""
        return self.balance + self.get_unrealized_pnl()

    def get_stats(self) -> Dict[str, Any]:
        """Get paper trading statistics"""
        return {
            'initial_balance': self.initial_balance,
            'current_balance': self.balance,
            'account_value': self.get_account_value(),
            'realized_pnl': self.realized_pnl,
            'unrealized_pnl': self.get_unrealized_pnl(),
            'total_pnl': self.get_total_pnl(),
            'total_fees': self.total_fees,
            'return_pct': (self.get_account_value() - self.initial_balance) / self.initial_balance,
            'num_trades': len(self._trade_history),
            'num_positions': len(self._positions)
        }

    def reset(self) -> None:
        """Reset paper trading state"""
        self.balance = self.initial_balance
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self._orders.clear()
        self._positions.clear()
        self._trade_history.clear()
        logger.info("[PAPER] Trading state reset")
