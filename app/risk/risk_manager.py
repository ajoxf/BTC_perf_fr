"""
Risk management for trading system
"""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
from loguru import logger


class RiskCheck(Enum):
    """Types of risk checks"""
    MAX_POSITION_SIZE = "max_position_size"
    MAX_TOTAL_EXPOSURE = "max_total_exposure"
    MAX_LOSS_PER_TRADE = "max_loss_per_trade"
    MAX_DAILY_LOSS = "max_daily_loss"
    REGIME_CHECK = "regime_check"
    MIN_CONFIDENCE = "min_confidence"


@dataclass
class RiskCheckResult:
    """Result of a risk check"""
    check: RiskCheck
    passed: bool
    message: str
    value: float = 0.0
    limit: float = 0.0


class RiskManager:
    """Manage trading risk limits and checks"""

    def __init__(
        self,
        max_position_usd: float = 10000.0,
        max_total_exposure_usd: float = 25000.0,
        max_loss_per_trade_usd: float = 500.0,
        max_daily_loss_usd: float = 1000.0,
        hurst_threshold: float = 0.5,
        min_confidence: float = 0.6
    ):
        """
        Initialize risk manager.

        Args:
            max_position_usd: Maximum size for a single position
            max_total_exposure_usd: Maximum total exposure across all positions
            max_loss_per_trade_usd: Maximum loss before stopping a trade
            max_daily_loss_usd: Maximum daily loss before stopping trading
            hurst_threshold: Maximum Hurst exponent for favorable regime
            min_confidence: Minimum signal confidence to trade
        """
        self.max_position_usd = max_position_usd
        self.max_total_exposure_usd = max_total_exposure_usd
        self.max_loss_per_trade_usd = max_loss_per_trade_usd
        self.max_daily_loss_usd = max_daily_loss_usd
        self.hurst_threshold = hurst_threshold
        self.min_confidence = min_confidence

        self._daily_pnl = 0.0
        self._daily_reset_time: Optional[datetime] = None
        self._trade_losses: List[float] = []

    def check_entry(
        self,
        position_size_usd: float,
        current_exposure_usd: float,
        hurst: float,
        confidence: float
    ) -> List[RiskCheckResult]:
        """
        Run all risk checks for a potential entry.

        Args:
            position_size_usd: Proposed position size
            current_exposure_usd: Current total exposure
            hurst: Current Hurst exponent
            confidence: Signal confidence

        Returns:
            List of risk check results
        """
        self._check_daily_reset()
        results = []

        # Check position size
        results.append(RiskCheckResult(
            check=RiskCheck.MAX_POSITION_SIZE,
            passed=position_size_usd <= self.max_position_usd,
            message=f"Position size ${position_size_usd:.0f} vs limit ${self.max_position_usd:.0f}",
            value=position_size_usd,
            limit=self.max_position_usd
        ))

        # Check total exposure
        new_exposure = current_exposure_usd + position_size_usd
        results.append(RiskCheckResult(
            check=RiskCheck.MAX_TOTAL_EXPOSURE,
            passed=new_exposure <= self.max_total_exposure_usd,
            message=f"Total exposure ${new_exposure:.0f} vs limit ${self.max_total_exposure_usd:.0f}",
            value=new_exposure,
            limit=self.max_total_exposure_usd
        ))

        # Check daily loss
        remaining_loss = self.max_daily_loss_usd + self._daily_pnl
        results.append(RiskCheckResult(
            check=RiskCheck.MAX_DAILY_LOSS,
            passed=remaining_loss > 0,
            message=f"Daily P&L ${self._daily_pnl:.2f}, remaining loss capacity ${remaining_loss:.2f}",
            value=self._daily_pnl,
            limit=self.max_daily_loss_usd
        ))

        # Check regime
        results.append(RiskCheckResult(
            check=RiskCheck.REGIME_CHECK,
            passed=hurst <= self.hurst_threshold,
            message=f"Hurst {hurst:.3f} vs threshold {self.hurst_threshold:.2f}",
            value=hurst,
            limit=self.hurst_threshold
        ))

        # Check confidence
        results.append(RiskCheckResult(
            check=RiskCheck.MIN_CONFIDENCE,
            passed=confidence >= self.min_confidence,
            message=f"Confidence {confidence:.2f} vs minimum {self.min_confidence:.2f}",
            value=confidence,
            limit=self.min_confidence
        ))

        return results

    def can_enter(
        self,
        position_size_usd: float,
        current_exposure_usd: float,
        hurst: float,
        confidence: float
    ) -> tuple:
        """
        Check if entry is allowed.

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        results = self.check_entry(position_size_usd, current_exposure_usd, hurst, confidence)

        failed = [r for r in results if not r.passed]
        if failed:
            reasons = "; ".join(r.message for r in failed)
            return False, reasons

        return True, "All checks passed"

    def check_stop_loss(
        self,
        unrealized_pnl: float,
        position_size_usd: float
    ) -> bool:
        """
        Check if stop loss should be triggered.

        Args:
            unrealized_pnl: Current unrealized P&L
            position_size_usd: Position size

        Returns:
            True if stop loss triggered
        """
        if unrealized_pnl <= -self.max_loss_per_trade_usd:
            logger.warning(f"Stop loss triggered: ${unrealized_pnl:.2f} <= -${self.max_loss_per_trade_usd:.2f}")
            return True
        return False

    def check_daily_stop(self) -> bool:
        """
        Check if daily loss limit hit.

        Returns:
            True if daily stop triggered
        """
        self._check_daily_reset()
        if self._daily_pnl <= -self.max_daily_loss_usd:
            logger.warning(f"Daily stop triggered: ${self._daily_pnl:.2f} <= -${self.max_daily_loss_usd:.2f}")
            return True
        return False

    def record_trade_pnl(self, pnl: float) -> None:
        """Record P&L from a closed trade"""
        self._check_daily_reset()
        self._daily_pnl += pnl
        self._trade_losses.append(pnl)

        if pnl < 0:
            logger.info(f"Trade loss recorded: ${pnl:.2f}, daily P&L: ${self._daily_pnl:.2f}")

    def get_position_size(
        self,
        target_size_usd: float,
        current_exposure_usd: float
    ) -> float:
        """
        Get allowed position size respecting limits.

        Args:
            target_size_usd: Desired position size
            current_exposure_usd: Current exposure

        Returns:
            Allowed position size
        """
        # Cap at max position size
        size = min(target_size_usd, self.max_position_usd)

        # Cap at remaining exposure capacity
        remaining_exposure = self.max_total_exposure_usd - current_exposure_usd
        size = min(size, remaining_exposure)

        # Must be positive
        size = max(0, size)

        if size < target_size_usd:
            logger.info(f"Position size reduced: ${target_size_usd:.0f} -> ${size:.0f}")

        return size

    def get_stop_loss_price(
        self,
        entry_price: float,
        side: str,  # 'long' or 'short'
        position_size_usd: float
    ) -> float:
        """
        Calculate stop loss price.

        Args:
            entry_price: Entry price
            side: Position side
            position_size_usd: Position size

        Returns:
            Stop loss price
        """
        # Calculate stop loss percentage based on max loss
        stop_pct = self.max_loss_per_trade_usd / position_size_usd

        if side == 'long':
            return entry_price * (1 - stop_pct)
        else:
            return entry_price * (1 + stop_pct)

    def _check_daily_reset(self) -> None:
        """Check if daily P&L should be reset (new day)"""
        now = datetime.utcnow()

        if self._daily_reset_time is None:
            self._daily_reset_time = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Check if we've passed midnight UTC
        next_reset = self._daily_reset_time + timedelta(days=1)
        if now >= next_reset:
            logger.info(f"Daily P&L reset: ${self._daily_pnl:.2f}")
            self._daily_pnl = 0.0
            self._daily_reset_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            self._trade_losses.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get risk management statistics"""
        self._check_daily_reset()

        win_trades = [p for p in self._trade_losses if p > 0]
        loss_trades = [p for p in self._trade_losses if p < 0]

        return {
            'daily_pnl': self._daily_pnl,
            'daily_loss_remaining': self.max_daily_loss_usd + self._daily_pnl,
            'daily_trades': len(self._trade_losses),
            'winning_trades': len(win_trades),
            'losing_trades': len(loss_trades),
            'total_wins': sum(win_trades),
            'total_losses': sum(loss_trades),
            'limits': {
                'max_position_usd': self.max_position_usd,
                'max_exposure_usd': self.max_total_exposure_usd,
                'max_loss_per_trade': self.max_loss_per_trade_usd,
                'max_daily_loss': self.max_daily_loss_usd
            }
        }

    def update_limits(
        self,
        max_position_usd: float = None,
        max_total_exposure_usd: float = None,
        max_loss_per_trade_usd: float = None,
        max_daily_loss_usd: float = None,
        hurst_threshold: float = None,
        min_confidence: float = None
    ) -> None:
        """Update risk limits"""
        if max_position_usd is not None:
            self.max_position_usd = max_position_usd
        if max_total_exposure_usd is not None:
            self.max_total_exposure_usd = max_total_exposure_usd
        if max_loss_per_trade_usd is not None:
            self.max_loss_per_trade_usd = max_loss_per_trade_usd
        if max_daily_loss_usd is not None:
            self.max_daily_loss_usd = max_daily_loss_usd
        if hurst_threshold is not None:
            self.hurst_threshold = hurst_threshold
        if min_confidence is not None:
            self.min_confidence = min_confidence

        logger.info("Risk limits updated")
