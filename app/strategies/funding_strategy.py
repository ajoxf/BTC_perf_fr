"""
BTC Perpetual Funding Rate Arbitrage Strategy

Concept:
- Perpetual futures have funding rates paid every 8 hours
- When funding is high positive: Long spot + Short perp (collect funding)
- When funding is high negative: Short spot + Long perp (collect funding)
- Funding rates are mean-reverting
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
from loguru import logger

from app.strategies.base_strategy import BaseStrategy
from app.analytics.signals import Signal, SignalType, SignalGenerator
from app.analytics.statistics import RollingStats


class FundingWindow(Enum):
    """Funding settlement timing windows"""
    BEFORE_SETTLEMENT = "before"   # Good time to enter
    AT_SETTLEMENT = "at"           # Settlement happening
    AFTER_SETTLEMENT = "after"     # Just settled


@dataclass
class FundingState:
    """Current funding rate state"""
    current_rate: float
    predicted_rate: Optional[float]
    next_settlement: datetime
    time_to_settlement: timedelta
    window: FundingWindow
    zscore: float
    annualized_rate: float


class FundingTimer:
    """Manage funding settlement timing"""

    SETTLEMENT_HOURS = [0, 8, 16]  # UTC hours
    ENTRY_WINDOW_MINUTES = 30     # Enter up to 30 min before
    SETTLEMENT_BUFFER_MINUTES = 2  # Buffer around settlement

    @classmethod
    def next_settlement(cls) -> datetime:
        """Get next funding settlement time"""
        now = datetime.now(timezone.utc)

        for hour in cls.SETTLEMENT_HOURS:
            settlement = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if settlement > now:
                return settlement

        # Next day's first settlement
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    @classmethod
    def time_to_settlement(cls) -> timedelta:
        """Time until next settlement"""
        return cls.next_settlement() - datetime.now(timezone.utc)

    @classmethod
    def get_window(cls) -> FundingWindow:
        """Determine current funding window"""
        minutes_to_settlement = cls.time_to_settlement().total_seconds() / 60

        if minutes_to_settlement <= cls.SETTLEMENT_BUFFER_MINUTES:
            return FundingWindow.AT_SETTLEMENT
        elif minutes_to_settlement <= cls.ENTRY_WINDOW_MINUTES:
            return FundingWindow.BEFORE_SETTLEMENT
        else:
            return FundingWindow.AFTER_SETTLEMENT

    @classmethod
    def is_entry_window(cls) -> bool:
        """Check if it's a good time to enter for funding"""
        return cls.get_window() == FundingWindow.BEFORE_SETTLEMENT

    @classmethod
    def settlements_between(cls, start: datetime, end: datetime) -> int:
        """Count settlements between two times"""
        count = 0
        current = start

        while current <= end:
            for hour in cls.SETTLEMENT_HOURS:
                settlement = current.replace(hour=hour, minute=0, second=0, microsecond=0)
                if start < settlement <= end:
                    count += 1
            current += timedelta(days=1)

        return count


class FundingStrategy(BaseStrategy):
    """Perpetual funding rate arbitrage strategy"""

    def __init__(
        self,
        lookback_periods: int = 21,  # 21 funding periods = 7 days
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        min_rate_threshold: float = 0.0005,  # 0.05%
        hurst_threshold: float = 0.5,
        max_position_usd: float = 10000.0,
        stop_loss_pct: float = 0.02,
        min_settlements: int = 1,
        max_settlements: int = 6  # 2 days max
    ):
        super().__init__(
            name='funding',
            lookback_period=lookback_periods,
            entry_zscore=entry_zscore,
            exit_zscore=exit_zscore,
            hurst_threshold=hurst_threshold,
            max_position_usd=max_position_usd,
            stop_loss_pct=stop_loss_pct
        )
        self.min_rate_threshold = min_rate_threshold
        self.min_settlements = min_settlements
        self.max_settlements = max_settlements

        self.signal_generator = SignalGenerator(
            entry_zscore=entry_zscore,
            exit_zscore=exit_zscore,
            hurst_threshold=hurst_threshold
        )

        # Track data
        self._funding_history: List[float] = []
        self._current_rate = 0.0
        self._predicted_rate: Optional[float] = None
        self._current_zscore = 0.0
        self._settlements_collected = 0
        self._spot_price = 0.0
        self._perp_price = 0.0

    def update(self, data: Dict[str, Any]) -> Signal:
        """
        Update strategy with new market data.

        Expected data format:
        {
            'spot_price': float,
            'perp_price': float,
            'funding_rate': float,
            'predicted_rate': float (optional),
            'funding_time': datetime
        }
        """
        if not self.enabled:
            return Signal(
                signal_type=SignalType.NONE,
                strategy='funding',
                timestamp=datetime.utcnow(),
                zscore=0,
                hurst=self._current_hurst,
                confidence=0,
                reason="Strategy disabled"
            )

        spot_price = data.get('spot_price', 0)
        perp_price = data.get('perp_price', 0)
        funding_rate = data.get('funding_rate', 0)
        predicted_rate = data.get('predicted_rate')

        if not spot_price or not perp_price:
            return Signal(
                signal_type=SignalType.NONE,
                strategy='funding',
                timestamp=datetime.utcnow(),
                zscore=0,
                hurst=self._current_hurst,
                confidence=0,
                reason="Missing price data"
            )

        self._spot_price = spot_price
        self._perp_price = perp_price
        self._current_rate = funding_rate
        self._predicted_rate = predicted_rate

        # Update rolling statistics with funding rate
        stats = self.rolling_stats.update(funding_rate)
        self._current_zscore = stats.zscore

        # Track for Hurst calculation
        self._funding_history.append(funding_rate)
        if len(self._funding_history) > 100:
            self._funding_history = self._funding_history[-100:]

        # Update Hurst exponent
        if len(self._funding_history) >= 20:
            self.update_hurst(self._funding_history)

        # Calculate unrealized P&L if in position
        unrealized_pnl_pct = 0.0
        if self._has_position:
            _, unrealized_pnl_pct = self.calculate_unrealized_pnl(
                spot_price, perp_price
            )

        # Generate signal
        signal = self.signal_generator.generate_funding_signal(
            funding_rate=funding_rate,
            funding_zscore=self._current_zscore,
            predicted_rate=predicted_rate or funding_rate,
            hurst=self._current_hurst,
            has_position=self._has_position,
            position_side=self._position_side,
            settlements_collected=self._settlements_collected,
            unrealized_pnl_pct=unrealized_pnl_pct,
            min_rate_threshold=self.min_rate_threshold,
            stop_loss_pct=self.stop_loss_pct,
            min_settlements=self.min_settlements,
            max_settlements=self.max_settlements
        )

        # Enhance with timing information
        funding_window = FundingTimer.get_window()
        if signal.signal_type in [SignalType.ENTRY_LONG_SPREAD, SignalType.ENTRY_SHORT_SPREAD]:
            # Only enter during entry window
            if funding_window != FundingWindow.BEFORE_SETTLEMENT:
                signal = Signal(
                    signal_type=SignalType.NONE,
                    strategy='funding',
                    timestamp=datetime.utcnow(),
                    zscore=self._current_zscore,
                    hurst=self._current_hurst,
                    confidence=0,
                    reason=f"Not in entry window (next settlement in {FundingTimer.time_to_settlement()})",
                    metadata={'funding_rate': funding_rate, 'window': funding_window.value}
                )

        self._current_signal = signal
        return signal

    def calculate_spread(self, data: Dict[str, Any]) -> float:
        """Calculate perp premium/discount"""
        spot_price = data.get('spot_price', 0)
        perp_price = data.get('perp_price', 0)

        if not spot_price or not perp_price:
            return 0.0

        return (perp_price - spot_price) / spot_price

    def record_settlement(self, amount: float) -> None:
        """
        Record a funding settlement.

        Args:
            amount: Funding payment amount (positive = received)
        """
        self._settlements_collected += 1
        self.add_funding(amount)
        logger.info(f"[funding] Settlement #{self._settlements_collected}: ${amount:.4f}")

    def open_position(
        self,
        side: str,
        size: float,
        spot_price: float,
        derivative_price: float
    ) -> None:
        """Override to reset settlement counter"""
        super().open_position(side, size, spot_price, derivative_price)
        self._settlements_collected = 0

    def close_position(
        self,
        spot_price: float,
        derivative_price: float,
        reason: str
    ) -> Dict[str, float]:
        """Override to include settlement count"""
        result = super().close_position(spot_price, derivative_price, reason)
        if result:
            result['settlements_collected'] = self._settlements_collected
        self._settlements_collected = 0
        return result

    def get_funding_state(self) -> FundingState:
        """Get current funding rate state"""
        next_settlement = FundingTimer.next_settlement()
        time_to = FundingTimer.time_to_settlement()
        window = FundingTimer.get_window()

        # Annualized rate (3 settlements per day)
        annualized = self._current_rate * 3 * 365

        return FundingState(
            current_rate=self._current_rate,
            predicted_rate=self._predicted_rate,
            next_settlement=next_settlement,
            time_to_settlement=time_to,
            window=window,
            zscore=self._current_zscore,
            annualized_rate=annualized
        )

    def estimate_funding_income(
        self,
        position_size: float,
        num_settlements: int = 1
    ) -> float:
        """
        Estimate funding income for a position.

        Args:
            position_size: Position size in USD
            num_settlements: Number of settlements to estimate

        Returns:
            Estimated funding income in USD
        """
        avg_rate = self._current_rate
        if self._predicted_rate:
            avg_rate = (self._current_rate + self._predicted_rate) / 2

        return position_size * avg_rate * num_settlements

    def should_enter_now(self) -> bool:
        """Check if conditions are favorable for immediate entry"""
        # Check timing
        if not FundingTimer.is_entry_window():
            return False

        # Check rate threshold
        if abs(self._current_rate) < self.min_rate_threshold:
            return False

        # Check z-score
        if abs(self._current_zscore) < self.entry_zscore:
            return False

        # Check regime
        if self._current_hurst > self.hurst_threshold:
            return False

        return True

    @property
    def current_funding_rate(self) -> float:
        return self._current_rate

    @property
    def predicted_funding_rate(self) -> Optional[float]:
        return self._predicted_rate

    @property
    def current_zscore(self) -> float:
        return self._current_zscore

    @property
    def settlements_collected(self) -> int:
        return self._settlements_collected
