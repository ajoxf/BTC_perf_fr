"""
Signal generation for trading strategies
"""
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


class SignalType(Enum):
    """Types of trading signals"""
    NONE = "none"
    ENTRY_LONG_SPREAD = "entry_long_spread"   # Long spot, short derivative
    ENTRY_SHORT_SPREAD = "entry_short_spread"  # Short spot, long derivative
    EXIT = "exit"
    STOP_LOSS = "stop_loss"
    TIME_STOP = "time_stop"


@dataclass
class Signal:
    """Trading signal"""
    signal_type: SignalType
    strategy: str  # 'basis' or 'funding'
    timestamp: datetime
    zscore: float
    hurst: float
    confidence: float  # 0-1 confidence score
    reason: str
    metadata: dict = None

    def is_entry(self) -> bool:
        return self.signal_type in [SignalType.ENTRY_LONG_SPREAD, SignalType.ENTRY_SHORT_SPREAD]

    def is_exit(self) -> bool:
        return self.signal_type in [SignalType.EXIT, SignalType.STOP_LOSS, SignalType.TIME_STOP]


class SignalGenerator:
    """Generate trading signals based on z-scores and regime"""

    def __init__(
        self,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        hurst_threshold: float = 0.5,
        min_confidence: float = 0.6
    ):
        """
        Initialize signal generator.

        Args:
            entry_zscore: Z-score threshold for entry signals
            exit_zscore: Z-score threshold for exit signals
            hurst_threshold: Maximum Hurst exponent for trading
            min_confidence: Minimum confidence for signal generation
        """
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.hurst_threshold = hurst_threshold
        self.min_confidence = min_confidence

    def generate_basis_signal(
        self,
        basis_zscore: float,
        hurst: float,
        has_position: bool,
        position_side: str = None,  # 'long_spread' or 'short_spread'
        time_in_position: float = 0,  # hours
        unrealized_pnl_pct: float = 0,
        stop_loss_pct: float = 0.02,
        time_stop_hours: int = 168
    ) -> Signal:
        """
        Generate signal for basis trading strategy.

        Args:
            basis_zscore: Current z-score of the basis
            hurst: Current Hurst exponent
            has_position: Whether a position is currently open
            position_side: Side of current position
            time_in_position: Hours in current position
            unrealized_pnl_pct: Unrealized P&L as percentage
            stop_loss_pct: Stop loss threshold
            time_stop_hours: Maximum time in position

        Returns:
            Signal object
        """
        now = datetime.utcnow()

        # Check for stop conditions if in position
        if has_position:
            # Time stop
            if time_in_position >= time_stop_hours:
                return Signal(
                    signal_type=SignalType.TIME_STOP,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=1.0,
                    reason=f"Time stop: {time_in_position:.1f}h >= {time_stop_hours}h"
                )

            # Stop loss
            if unrealized_pnl_pct <= -stop_loss_pct:
                return Signal(
                    signal_type=SignalType.STOP_LOSS,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=1.0,
                    reason=f"Stop loss: {unrealized_pnl_pct:.2%} <= -{stop_loss_pct:.2%}"
                )

            # Exit signal - z-score reverted
            if position_side == 'long_spread' and basis_zscore <= self.exit_zscore:
                return Signal(
                    signal_type=SignalType.EXIT,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=self._calculate_confidence(basis_zscore, hurst, is_exit=True),
                    reason=f"Basis reverted: z={basis_zscore:.2f} <= {self.exit_zscore}"
                )
            elif position_side == 'short_spread' and basis_zscore >= -self.exit_zscore:
                return Signal(
                    signal_type=SignalType.EXIT,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=self._calculate_confidence(basis_zscore, hurst, is_exit=True),
                    reason=f"Basis reverted: z={basis_zscore:.2f} >= -{self.exit_zscore}"
                )

        else:
            # Entry signals - no position
            # Check regime
            if hurst > self.hurst_threshold:
                return Signal(
                    signal_type=SignalType.NONE,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=0,
                    reason=f"Unfavorable regime: Hurst={hurst:.2f} > {self.hurst_threshold}"
                )

            # Basis too high - long spot, short futures
            if basis_zscore >= self.entry_zscore:
                return Signal(
                    signal_type=SignalType.ENTRY_LONG_SPREAD,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=self._calculate_confidence(basis_zscore, hurst),
                    reason=f"Basis extended high: z={basis_zscore:.2f} >= {self.entry_zscore}"
                )

            # Basis too low - short spot, long futures
            if basis_zscore <= -self.entry_zscore:
                return Signal(
                    signal_type=SignalType.ENTRY_SHORT_SPREAD,
                    strategy='basis',
                    timestamp=now,
                    zscore=basis_zscore,
                    hurst=hurst,
                    confidence=self._calculate_confidence(basis_zscore, hurst),
                    reason=f"Basis extended low: z={basis_zscore:.2f} <= -{self.entry_zscore}"
                )

        return Signal(
            signal_type=SignalType.NONE,
            strategy='basis',
            timestamp=now,
            zscore=basis_zscore,
            hurst=hurst,
            confidence=0,
            reason="No signal"
        )

    def generate_funding_signal(
        self,
        funding_rate: float,
        funding_zscore: float,
        predicted_rate: float,
        hurst: float,
        has_position: bool,
        position_side: str = None,
        settlements_collected: int = 0,
        unrealized_pnl_pct: float = 0,
        min_rate_threshold: float = 0.0005,
        stop_loss_pct: float = 0.02,
        min_settlements: int = 1,
        max_settlements: int = 6
    ) -> Signal:
        """
        Generate signal for funding rate strategy.

        Args:
            funding_rate: Current funding rate
            funding_zscore: Z-score of funding rate
            predicted_rate: Predicted next funding rate
            hurst: Current Hurst exponent
            has_position: Whether a position is currently open
            position_side: Side of current position
            settlements_collected: Number of funding settlements collected
            unrealized_pnl_pct: Unrealized P&L as percentage
            min_rate_threshold: Minimum funding rate to consider
            stop_loss_pct: Stop loss threshold
            min_settlements: Minimum settlements before exit
            max_settlements: Maximum settlements to hold

        Returns:
            Signal object
        """
        now = datetime.utcnow()

        if has_position:
            # Max settlements reached
            if settlements_collected >= max_settlements:
                return Signal(
                    signal_type=SignalType.TIME_STOP,
                    strategy='funding',
                    timestamp=now,
                    zscore=funding_zscore,
                    hurst=hurst,
                    confidence=1.0,
                    reason=f"Max settlements: {settlements_collected} >= {max_settlements}",
                    metadata={'funding_rate': funding_rate}
                )

            # Stop loss
            if unrealized_pnl_pct <= -stop_loss_pct:
                return Signal(
                    signal_type=SignalType.STOP_LOSS,
                    strategy='funding',
                    timestamp=now,
                    zscore=funding_zscore,
                    hurst=hurst,
                    confidence=1.0,
                    reason=f"Stop loss: {unrealized_pnl_pct:.2%}",
                    metadata={'funding_rate': funding_rate}
                )

            # Exit if funding normalized (after minimum settlements)
            if settlements_collected >= min_settlements:
                if position_side == 'long_spread' and funding_rate <= min_rate_threshold:
                    return Signal(
                        signal_type=SignalType.EXIT,
                        strategy='funding',
                        timestamp=now,
                        zscore=funding_zscore,
                        hurst=hurst,
                        confidence=self._calculate_confidence(funding_zscore, hurst, is_exit=True),
                        reason=f"Funding normalized: {funding_rate:.4%}",
                        metadata={'funding_rate': funding_rate}
                    )
                elif position_side == 'short_spread' and funding_rate >= -min_rate_threshold:
                    return Signal(
                        signal_type=SignalType.EXIT,
                        strategy='funding',
                        timestamp=now,
                        zscore=funding_zscore,
                        hurst=hurst,
                        confidence=self._calculate_confidence(funding_zscore, hurst, is_exit=True),
                        reason=f"Funding normalized: {funding_rate:.4%}",
                        metadata={'funding_rate': funding_rate}
                    )

        else:
            # Entry signals
            # Check regime
            if hurst > self.hurst_threshold:
                return Signal(
                    signal_type=SignalType.NONE,
                    strategy='funding',
                    timestamp=now,
                    zscore=funding_zscore,
                    hurst=hurst,
                    confidence=0,
                    reason=f"Unfavorable regime: Hurst={hurst:.2f}",
                    metadata={'funding_rate': funding_rate}
                )

            # Check minimum rate threshold
            if abs(funding_rate) < min_rate_threshold:
                return Signal(
                    signal_type=SignalType.NONE,
                    strategy='funding',
                    timestamp=now,
                    zscore=funding_zscore,
                    hurst=hurst,
                    confidence=0,
                    reason=f"Funding too low: |{funding_rate:.4%}| < {min_rate_threshold:.4%}",
                    metadata={'funding_rate': funding_rate}
                )

            # Confirm with predicted rate
            rate_confirmed = (funding_rate > 0 and predicted_rate > 0) or \
                           (funding_rate < 0 and predicted_rate < 0)

            # High positive funding - long spot, short perp (receive funding)
            if funding_rate > 0 and funding_zscore >= self.entry_zscore:
                confidence = self._calculate_confidence(funding_zscore, hurst)
                if rate_confirmed:
                    confidence *= 1.2  # Boost confidence
                return Signal(
                    signal_type=SignalType.ENTRY_LONG_SPREAD,
                    strategy='funding',
                    timestamp=now,
                    zscore=funding_zscore,
                    hurst=hurst,
                    confidence=min(1.0, confidence),
                    reason=f"High positive funding: {funding_rate:.4%} (z={funding_zscore:.2f})",
                    metadata={'funding_rate': funding_rate, 'predicted': predicted_rate}
                )

            # High negative funding - short spot, long perp (receive funding)
            if funding_rate < 0 and funding_zscore <= -self.entry_zscore:
                confidence = self._calculate_confidence(abs(funding_zscore), hurst)
                if rate_confirmed:
                    confidence *= 1.2
                return Signal(
                    signal_type=SignalType.ENTRY_SHORT_SPREAD,
                    strategy='funding',
                    timestamp=now,
                    zscore=funding_zscore,
                    hurst=hurst,
                    confidence=min(1.0, confidence),
                    reason=f"High negative funding: {funding_rate:.4%} (z={funding_zscore:.2f})",
                    metadata={'funding_rate': funding_rate, 'predicted': predicted_rate}
                )

        return Signal(
            signal_type=SignalType.NONE,
            strategy='funding',
            timestamp=now,
            zscore=funding_zscore,
            hurst=hurst,
            confidence=0,
            reason="No signal",
            metadata={'funding_rate': funding_rate}
        )

    def _calculate_confidence(
        self,
        zscore: float,
        hurst: float,
        is_exit: bool = False
    ) -> float:
        """
        Calculate confidence score for a signal.

        Args:
            zscore: Absolute z-score value
            hurst: Hurst exponent
            is_exit: Whether this is an exit signal

        Returns:
            Confidence score between 0 and 1
        """
        if is_exit:
            # Higher confidence for clearer exits
            return min(1.0, 0.7 + 0.3 * (1 - abs(zscore) / self.exit_zscore))

        # Entry confidence
        # Higher z-score = higher confidence
        zscore_factor = min(1.0, abs(zscore) / (self.entry_zscore * 2))

        # Lower Hurst = higher confidence
        hurst_factor = max(0, (self.hurst_threshold - hurst) / self.hurst_threshold)

        confidence = 0.5 * zscore_factor + 0.5 * hurst_factor

        return min(1.0, max(0, confidence))
