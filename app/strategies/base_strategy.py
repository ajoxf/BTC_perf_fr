"""
Base strategy class with common functionality
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
from loguru import logger

from app.analytics.signals import Signal, SignalType
from app.analytics.statistics import RollingStats
from app.analytics.hurst import calculate_hurst_exponent


@dataclass
class StrategyState:
    """Current state of a strategy"""
    name: str
    enabled: bool
    has_position: bool
    position_side: Optional[str]  # 'long_spread' or 'short_spread'
    position_size: float
    entry_price_spot: float
    entry_price_derivative: float
    entry_time: Optional[datetime]
    unrealized_pnl: float
    unrealized_pnl_pct: float
    total_funding: float
    current_signal: Optional[Signal]
    last_update: datetime


class BaseStrategy(ABC):
    """Abstract base class for trading strategies"""

    def __init__(
        self,
        name: str,
        lookback_period: int,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        hurst_threshold: float = 0.5,
        max_position_usd: float = 10000.0,
        stop_loss_pct: float = 0.02
    ):
        """
        Initialize base strategy.

        Args:
            name: Strategy name
            lookback_period: Number of periods for rolling statistics
            entry_zscore: Z-score threshold for entry
            exit_zscore: Z-score threshold for exit
            hurst_threshold: Maximum Hurst for favorable regime
            max_position_usd: Maximum position size in USD
            stop_loss_pct: Stop loss percentage
        """
        self.name = name
        self.lookback_period = lookback_period
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.hurst_threshold = hurst_threshold
        self.max_position_usd = max_position_usd
        self.stop_loss_pct = stop_loss_pct

        self.enabled = True
        self.rolling_stats = RollingStats(lookback_period)
        self._values_for_hurst: List[float] = []

        # Position state
        self._has_position = False
        self._position_side: Optional[str] = None
        self._position_size = 0.0
        self._entry_spot_price = 0.0
        self._entry_derivative_price = 0.0
        self._entry_time: Optional[datetime] = None
        self._total_funding = 0.0

        self._current_signal: Optional[Signal] = None
        self._current_hurst = 0.5

    @abstractmethod
    def update(self, data: Dict[str, Any]) -> Signal:
        """
        Update strategy with new data and generate signal.

        Args:
            data: Market data dictionary

        Returns:
            Generated signal
        """
        pass

    @abstractmethod
    def calculate_spread(self, data: Dict[str, Any]) -> float:
        """
        Calculate the spread value for this strategy.

        Args:
            data: Market data dictionary

        Returns:
            Spread value
        """
        pass

    def update_hurst(self, values: List[float]) -> float:
        """
        Update Hurst exponent calculation.

        Args:
            values: Time series values

        Returns:
            Current Hurst exponent
        """
        if len(values) >= 50:
            self._current_hurst = calculate_hurst_exponent(values[-200:])
        return self._current_hurst

    def open_position(
        self,
        side: str,
        size: float,
        spot_price: float,
        derivative_price: float
    ) -> None:
        """
        Record opening a position.

        Args:
            side: 'long_spread' or 'short_spread'
            size: Position size in USD
            spot_price: Spot entry price
            derivative_price: Derivative entry price
        """
        self._has_position = True
        self._position_side = side
        self._position_size = size
        self._entry_spot_price = spot_price
        self._entry_derivative_price = derivative_price
        self._entry_time = datetime.utcnow()
        self._total_funding = 0.0

        logger.info(f"[{self.name}] Opened {side} position: size=${size:.2f}, "
                   f"spot={spot_price:.2f}, derivative={derivative_price:.2f}")

    def close_position(
        self,
        spot_price: float,
        derivative_price: float,
        reason: str
    ) -> Dict[str, float]:
        """
        Record closing a position.

        Args:
            spot_price: Spot exit price
            derivative_price: Derivative exit price
            reason: Reason for closing

        Returns:
            Dictionary with P&L details
        """
        if not self._has_position:
            return {}

        # Calculate P&L
        spot_pnl = 0.0
        derivative_pnl = 0.0

        if self._position_side == 'long_spread':
            # Long spot, short derivative
            spot_pnl = (spot_price - self._entry_spot_price) / self._entry_spot_price
            derivative_pnl = (self._entry_derivative_price - derivative_price) / self._entry_derivative_price
        else:
            # Short spot, long derivative
            spot_pnl = (self._entry_spot_price - spot_price) / self._entry_spot_price
            derivative_pnl = (derivative_price - self._entry_derivative_price) / self._entry_derivative_price

        total_pnl_pct = (spot_pnl + derivative_pnl) / 2
        total_pnl_usd = total_pnl_pct * self._position_size

        result = {
            'spot_pnl_pct': spot_pnl,
            'derivative_pnl_pct': derivative_pnl,
            'total_pnl_pct': total_pnl_pct,
            'total_pnl_usd': total_pnl_usd,
            'funding_collected': self._total_funding,
            'net_pnl_usd': total_pnl_usd + self._total_funding,
            'reason': reason
        }

        logger.info(f"[{self.name}] Closed position: reason={reason}, "
                   f"pnl={total_pnl_pct:.2%} (${total_pnl_usd:.2f}), "
                   f"funding=${self._total_funding:.2f}")

        # Reset state
        self._has_position = False
        self._position_side = None
        self._position_size = 0.0
        self._entry_spot_price = 0.0
        self._entry_derivative_price = 0.0
        self._entry_time = None
        self._total_funding = 0.0

        return result

    def add_funding(self, amount: float) -> None:
        """Add funding payment to position"""
        self._total_funding += amount
        logger.debug(f"[{self.name}] Funding added: ${amount:.4f}, "
                    f"total: ${self._total_funding:.4f}")

    def calculate_unrealized_pnl(
        self,
        current_spot: float,
        current_derivative: float
    ) -> tuple:
        """
        Calculate unrealized P&L for current position.

        Returns:
            Tuple of (pnl_usd, pnl_pct)
        """
        if not self._has_position:
            return 0.0, 0.0

        spot_pnl = 0.0
        derivative_pnl = 0.0

        if self._position_side == 'long_spread':
            spot_pnl = (current_spot - self._entry_spot_price) / self._entry_spot_price
            derivative_pnl = (self._entry_derivative_price - current_derivative) / self._entry_derivative_price
        else:
            spot_pnl = (self._entry_spot_price - current_spot) / self._entry_spot_price
            derivative_pnl = (current_derivative - self._entry_derivative_price) / self._entry_derivative_price

        pnl_pct = (spot_pnl + derivative_pnl) / 2
        pnl_usd = pnl_pct * self._position_size

        return pnl_usd, pnl_pct

    def get_time_in_position(self) -> float:
        """Get hours in current position"""
        if not self._entry_time:
            return 0.0
        delta = datetime.utcnow() - self._entry_time
        return delta.total_seconds() / 3600

    def get_state(self) -> StrategyState:
        """Get current strategy state"""
        return StrategyState(
            name=self.name,
            enabled=self.enabled,
            has_position=self._has_position,
            position_side=self._position_side,
            position_size=self._position_size,
            entry_price_spot=self._entry_spot_price,
            entry_price_derivative=self._entry_derivative_price,
            entry_time=self._entry_time,
            unrealized_pnl=0,  # Updated by caller
            unrealized_pnl_pct=0,
            total_funding=self._total_funding,
            current_signal=self._current_signal,
            last_update=datetime.utcnow()
        )

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable strategy"""
        self.enabled = enabled
        logger.info(f"[{self.name}] Strategy {'enabled' if enabled else 'disabled'}")

    @property
    def has_position(self) -> bool:
        return self._has_position

    @property
    def position_side(self) -> Optional[str]:
        return self._position_side

    @property
    def current_hurst(self) -> float:
        return self._current_hurst
