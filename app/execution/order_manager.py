"""
Order management for trade execution
"""
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger

from app.api.okx_client import OKXClient


class OrderStatus(Enum):
    """Order status"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderSide(Enum):
    """Order side"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type"""
    MARKET = "market"
    LIMIT = "limit"
    POST_ONLY = "post_only"


@dataclass
class Order:
    """Order data structure"""
    client_order_id: str
    inst_id: str
    side: OrderSide
    order_type: OrderType
    size: float
    price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    exchange_order_id: Optional[str] = None
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    error_message: Optional[str] = None

    def is_complete(self) -> bool:
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED]


@dataclass
class SpreadOrder:
    """Spread order (two legs)"""
    spread_id: str
    strategy: str
    spot_order: Order
    derivative_order: Order
    side: str  # 'long_spread' or 'short_spread'
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"

    def is_complete(self) -> bool:
        return self.spot_order.is_complete() and self.derivative_order.is_complete()

    def is_filled(self) -> bool:
        return (self.spot_order.status == OrderStatus.FILLED and
                self.derivative_order.status == OrderStatus.FILLED)


class OrderManager:
    """Manage order submission and tracking"""

    def __init__(self, client: OKXClient, paper_mode: bool = True):
        """
        Initialize order manager.

        Args:
            client: OKX API client
            paper_mode: Use paper trading mode
        """
        self.client = client
        self.paper_mode = paper_mode
        self._orders: Dict[str, Order] = {}
        self._spread_orders: Dict[str, SpreadOrder] = {}

    def _generate_order_id(self) -> str:
        """Generate unique client order ID"""
        return f"btc_arb_{uuid.uuid4().hex[:12]}"

    def _generate_spread_id(self) -> str:
        """Generate unique spread order ID"""
        return f"spread_{uuid.uuid4().hex[:8]}"

    def create_order(
        self,
        inst_id: str,
        side: str,
        order_type: str,
        size: float,
        price: float = None
    ) -> Order:
        """
        Create a new order.

        Args:
            inst_id: Instrument ID
            side: 'buy' or 'sell'
            order_type: 'market', 'limit', 'post_only'
            size: Order size
            price: Price for limit orders

        Returns:
            Order object
        """
        order = Order(
            client_order_id=self._generate_order_id(),
            inst_id=inst_id,
            side=OrderSide(side),
            order_type=OrderType(order_type),
            size=size,
            price=price
        )
        self._orders[order.client_order_id] = order
        return order

    def submit_order(self, order: Order) -> bool:
        """
        Submit order to exchange.

        Args:
            order: Order to submit

        Returns:
            True if submission successful
        """
        try:
            # Determine trade mode based on instrument
            if 'SWAP' in order.inst_id or '-' in order.inst_id and order.inst_id.count('-') > 1:
                trade_mode = 'cross'  # Derivatives use cross margin
            else:
                trade_mode = 'cash'  # Spot uses cash

            result = self.client.place_order(
                inst_id=order.inst_id,
                side=order.side.value,
                order_type=order.order_type.value,
                size=order.size,
                price=order.price,
                trade_mode=trade_mode,
                client_order_id=order.client_order_id
            )

            if result.get('code') == '0':
                order.status = OrderStatus.SUBMITTED
                order.exchange_order_id = result['data'][0].get('ordId')
                order.updated_at = datetime.utcnow()
                logger.info(f"Order submitted: {order.client_order_id} -> {order.exchange_order_id}")
                return True
            else:
                order.status = OrderStatus.FAILED
                order.error_message = result.get('msg', 'Unknown error')
                order.updated_at = datetime.utcnow()
                logger.error(f"Order failed: {order.client_order_id} - {order.error_message}")
                return False

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error_message = str(e)
            order.updated_at = datetime.utcnow()
            logger.error(f"Order exception: {order.client_order_id} - {e}")
            return False

    def update_order_status(self, order: Order) -> None:
        """Update order status from exchange"""
        try:
            result = self.client.get_order(
                inst_id=order.inst_id,
                client_order_id=order.client_order_id
            )

            if result.get('code') == '0' and result.get('data'):
                data = result['data'][0]
                state = data.get('state', '')

                if state == 'filled':
                    order.status = OrderStatus.FILLED
                    order.filled_size = float(data.get('fillSz', 0))
                    order.avg_fill_price = float(data.get('avgPx', 0))
                elif state == 'partially_filled':
                    order.status = OrderStatus.PARTIALLY_FILLED
                    order.filled_size = float(data.get('fillSz', 0))
                elif state == 'canceled':
                    order.status = OrderStatus.CANCELLED
                elif state == 'live':
                    order.status = OrderStatus.SUBMITTED

                order.updated_at = datetime.utcnow()

        except Exception as e:
            logger.error(f"Failed to update order status: {e}")

    def cancel_order(self, order: Order) -> bool:
        """Cancel an order"""
        try:
            result = self.client.cancel_order(
                inst_id=order.inst_id,
                client_order_id=order.client_order_id
            )

            if result.get('code') == '0':
                order.status = OrderStatus.CANCELLED
                order.updated_at = datetime.utcnow()
                logger.info(f"Order cancelled: {order.client_order_id}")
                return True
            else:
                logger.error(f"Cancel failed: {result.get('msg')}")
                return False

        except Exception as e:
            logger.error(f"Cancel exception: {e}")
            return False

    def create_spread_order(
        self,
        strategy: str,
        side: str,  # 'long_spread' or 'short_spread'
        spot_inst: str,
        derivative_inst: str,
        size_usd: float,
        spot_price: float,
        derivative_price: float,
        order_type: str = 'market'
    ) -> SpreadOrder:
        """
        Create a spread order (two legs).

        Args:
            strategy: Strategy name ('basis' or 'funding')
            side: 'long_spread' (buy spot, sell derivative) or 'short_spread'
            spot_inst: Spot instrument ID
            derivative_inst: Derivative instrument ID
            size_usd: Total position size in USD
            spot_price: Current spot price
            derivative_price: Current derivative price
            order_type: Order type

        Returns:
            SpreadOrder object
        """
        # Calculate sizes
        spot_size = size_usd / spot_price
        derivative_size = size_usd / derivative_price

        # Determine sides based on spread direction
        if side == 'long_spread':
            spot_side = 'buy'
            derivative_side = 'sell'
        else:  # short_spread
            spot_side = 'sell'
            derivative_side = 'buy'

        spot_order = self.create_order(
            inst_id=spot_inst,
            side=spot_side,
            order_type=order_type,
            size=spot_size
        )

        derivative_order = self.create_order(
            inst_id=derivative_inst,
            side=derivative_side,
            order_type=order_type,
            size=derivative_size
        )

        spread = SpreadOrder(
            spread_id=self._generate_spread_id(),
            strategy=strategy,
            spot_order=spot_order,
            derivative_order=derivative_order,
            side=side
        )

        self._spread_orders[spread.spread_id] = spread
        return spread

    def execute_spread_order(self, spread: SpreadOrder) -> bool:
        """
        Execute a spread order (both legs).

        Args:
            spread: Spread order to execute

        Returns:
            True if both legs submitted successfully
        """
        # Submit spot leg first
        spot_success = self.submit_order(spread.spot_order)
        if not spot_success:
            spread.status = "failed"
            return False

        # Submit derivative leg
        deriv_success = self.submit_order(spread.derivative_order)
        if not deriv_success:
            # Try to cancel spot order
            self.cancel_order(spread.spot_order)
            spread.status = "failed"
            return False

        spread.status = "submitted"
        return True

    def get_order(self, client_order_id: str) -> Optional[Order]:
        """Get order by client ID"""
        return self._orders.get(client_order_id)

    def get_spread_order(self, spread_id: str) -> Optional[SpreadOrder]:
        """Get spread order by ID"""
        return self._spread_orders.get(spread_id)

    def get_pending_orders(self) -> List[Order]:
        """Get all pending orders"""
        return [o for o in self._orders.values()
                if o.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED]]

    def get_pending_spreads(self) -> List[SpreadOrder]:
        """Get all pending spread orders"""
        return [s for s in self._spread_orders.values() if not s.is_complete()]
