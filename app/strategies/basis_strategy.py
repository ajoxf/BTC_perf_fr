"""
BTC Futures vs Spot Basis Trading Strategy

Concept:
- Monitor the spread between BTC spot and BTC quarterly futures
- Enter when basis deviates significantly from mean (z-score based)
- Exit when basis reverts to mean
- Capture the convergence as futures approach expiry
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from loguru import logger

from app.strategies.base_strategy import BaseStrategy
from app.analytics.signals import Signal, SignalType, SignalGenerator
from app.analytics.statistics import RollingStats


@dataclass
class FuturesContract:
    """Information about a futures contract"""
    inst_id: str
    expiry: datetime
    days_to_expiry: int
    last_price: float
    basis: float
    basis_pct: float
    annualized_basis: float


class BasisStrategy(BaseStrategy):
    """Spot-Futures basis trading strategy"""

    # Roll management
    ROLL_WARNING_DAYS = 7
    FORCE_ROLL_DAYS = 3
    MIN_LIQUIDITY_DAYS = 5

    def __init__(
        self,
        lookback_period: int = 168,  # 7 days in hours
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        hurst_threshold: float = 0.5,
        max_position_usd: float = 10000.0,
        stop_loss_pct: float = 0.02,
        time_stop_hours: int = 168  # 7 days
    ):
        super().__init__(
            name='basis',
            lookback_period=lookback_period,
            entry_zscore=entry_zscore,
            exit_zscore=exit_zscore,
            hurst_threshold=hurst_threshold,
            max_position_usd=max_position_usd,
            stop_loss_pct=stop_loss_pct
        )
        self.time_stop_hours = time_stop_hours
        self.signal_generator = SignalGenerator(
            entry_zscore=entry_zscore,
            exit_zscore=exit_zscore,
            hurst_threshold=hurst_threshold
        )

        # Track data
        self._basis_history: List[float] = []
        self._current_contract: Optional[str] = None
        self._spot_price = 0.0
        self._futures_price = 0.0
        self._current_basis = 0.0
        self._current_basis_pct = 0.0
        self._current_zscore = 0.0

    def update(self, data: Dict[str, Any]) -> Signal:
        """
        Update strategy with new market data.

        Expected data format:
        {
            'spot_price': float,
            'futures': [
                {
                    'inst_id': str,
                    'price': float,
                    'expiry': datetime
                },
                ...
            ]
        }
        """
        if not self.enabled:
            return Signal(
                signal_type=SignalType.NONE,
                strategy='basis',
                timestamp=datetime.utcnow(),
                zscore=0,
                hurst=self._current_hurst,
                confidence=0,
                reason="Strategy disabled"
            )

        spot_price = data.get('spot_price', 0)
        futures_list = data.get('futures', [])

        if not spot_price or not futures_list:
            return Signal(
                signal_type=SignalType.NONE,
                strategy='basis',
                timestamp=datetime.utcnow(),
                zscore=0,
                hurst=self._current_hurst,
                confidence=0,
                reason="Missing data"
            )

        self._spot_price = spot_price

        # Select best contract
        contracts = self._analyze_contracts(spot_price, futures_list)
        selected = self._select_contract(contracts)

        if not selected:
            return Signal(
                signal_type=SignalType.NONE,
                strategy='basis',
                timestamp=datetime.utcnow(),
                zscore=0,
                hurst=self._current_hurst,
                confidence=0,
                reason="No suitable contract"
            )

        # Update tracking
        self._current_contract = selected.inst_id
        self._futures_price = selected.last_price
        self._current_basis = selected.basis
        self._current_basis_pct = selected.basis_pct

        # Update rolling statistics
        stats = self.rolling_stats.update(selected.basis_pct)
        self._current_zscore = stats.zscore

        # Track for Hurst calculation
        self._basis_history.append(selected.basis_pct)
        if len(self._basis_history) > 500:
            self._basis_history = self._basis_history[-500:]

        # Update Hurst exponent periodically
        if len(self._basis_history) >= 50:
            self.update_hurst(self._basis_history)

        # Calculate unrealized P&L if in position
        unrealized_pnl_pct = 0.0
        if self._has_position:
            _, unrealized_pnl_pct = self.calculate_unrealized_pnl(
                spot_price, self._futures_price
            )

        # Generate signal
        signal = self.signal_generator.generate_basis_signal(
            basis_zscore=self._current_zscore,
            hurst=self._current_hurst,
            has_position=self._has_position,
            position_side=self._position_side,
            time_in_position=self.get_time_in_position(),
            unrealized_pnl_pct=unrealized_pnl_pct,
            stop_loss_pct=self.stop_loss_pct,
            time_stop_hours=self.time_stop_hours
        )

        # Check for roll warning
        if self._has_position and selected.days_to_expiry <= self.ROLL_WARNING_DAYS:
            logger.warning(f"[basis] Contract {selected.inst_id} expiring in "
                          f"{selected.days_to_expiry} days - consider rolling")

        self._current_signal = signal
        return signal

    def calculate_spread(self, data: Dict[str, Any]) -> float:
        """Calculate basis spread percentage"""
        spot_price = data.get('spot_price', 0)
        futures_price = data.get('futures_price', 0)

        if not spot_price or not futures_price:
            return 0.0

        return (futures_price - spot_price) / spot_price

    def _analyze_contracts(
        self,
        spot_price: float,
        futures_list: List[Dict]
    ) -> List[FuturesContract]:
        """Analyze available futures contracts"""
        now = datetime.now(timezone.utc)
        contracts = []

        for fut in futures_list:
            expiry = fut.get('expiry')
            if not expiry:
                continue

            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)

            days_to_expiry = (expiry - now).days
            if days_to_expiry < 0:
                continue

            price = fut.get('price', 0)
            if not price:
                continue

            basis = price - spot_price
            basis_pct = basis / spot_price

            # Annualized basis
            if days_to_expiry > 0:
                annualized = basis_pct * (365 / days_to_expiry)
            else:
                annualized = 0

            contracts.append(FuturesContract(
                inst_id=fut.get('instrument') or fut.get('inst_id'),
                expiry=expiry,
                days_to_expiry=days_to_expiry,
                last_price=price,
                basis=basis,
                basis_pct=basis_pct,
                annualized_basis=annualized
            ))

        return sorted(contracts, key=lambda x: x.expiry)

    def _select_contract(
        self,
        contracts: List[FuturesContract]
    ) -> Optional[FuturesContract]:
        """Select the best contract for trading"""
        if not contracts:
            return None

        # Filter for minimum liquidity
        eligible = [c for c in contracts if c.days_to_expiry >= self.MIN_LIQUIDITY_DAYS]

        if not eligible:
            # If no eligible contracts, use the longest dated
            return contracts[-1] if contracts else None

        # If we have a position, try to stay in same contract
        if self._has_position and self._current_contract:
            current = next((c for c in eligible if c.inst_id == self._current_contract), None)
            if current and current.days_to_expiry >= self.FORCE_ROLL_DAYS:
                return current

        # Otherwise, prefer front-month if > 7 days, else next
        front = eligible[0]
        if front.days_to_expiry >= self.ROLL_WARNING_DAYS:
            return front
        elif len(eligible) > 1:
            return eligible[1]

        return front

    def needs_roll(self) -> bool:
        """Check if current position needs to be rolled"""
        if not self._has_position or not self._current_contract:
            return False

        # Would need contract info to check - placeholder
        return False

    def get_contract_info(self) -> Optional[Dict]:
        """Get current contract information"""
        if not self._current_contract:
            return None

        return {
            'inst_id': self._current_contract,
            'spot_price': self._spot_price,
            'futures_price': self._futures_price,
            'basis': self._current_basis,
            'basis_pct': self._current_basis_pct,
            'zscore': self._current_zscore,
            'hurst': self._current_hurst
        }

    @property
    def current_zscore(self) -> float:
        return self._current_zscore

    @property
    def current_basis_pct(self) -> float:
        return self._current_basis_pct
