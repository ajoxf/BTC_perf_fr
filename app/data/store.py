"""
In-memory data store for real-time market data
"""
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List
from collections import deque
from dataclasses import dataclass

from app.api.okx_client import Ticker, FundingRate


@dataclass
class PriceSnapshot:
    """Price data snapshot"""
    price: float
    timestamp: datetime


class DataStore:
    """Thread-safe in-memory data store"""

    def __init__(self, max_history: int = 1000):
        """
        Initialize data store.

        Args:
            max_history: Maximum number of historical records to keep
        """
        self.max_history = max_history
        self._lock = threading.RLock()

        # Current data
        self._tickers: Dict[str, Ticker] = {}
        self._funding_rates: Dict[str, FundingRate] = {}

        # Historical data
        self._price_history: Dict[str, deque] = {}
        self._funding_history: Dict[str, deque] = {}

    def update_ticker(self, ticker: Ticker) -> None:
        """Update ticker data"""
        with self._lock:
            self._tickers[ticker.inst_id] = ticker

            # Add to history
            if ticker.inst_id not in self._price_history:
                self._price_history[ticker.inst_id] = deque(maxlen=self.max_history)

            self._price_history[ticker.inst_id].append(PriceSnapshot(
                price=ticker.last_price,
                timestamp=ticker.timestamp
            ))

    def update_funding_rate(self, funding: FundingRate) -> None:
        """Update funding rate data"""
        with self._lock:
            self._funding_rates[funding.inst_id] = funding

            # Add to history
            if funding.inst_id not in self._funding_history:
                self._funding_history[funding.inst_id] = deque(maxlen=self.max_history)

            self._funding_history[funding.inst_id].append(funding)

    def get_ticker(self, inst_id: str) -> Optional[Ticker]:
        """Get ticker for instrument"""
        with self._lock:
            return self._tickers.get(inst_id)

    def get_price(self, inst_id: str) -> Optional[float]:
        """Get last price for instrument"""
        ticker = self.get_ticker(inst_id)
        return ticker.last_price if ticker else None

    def get_funding_rate(self, inst_id: str) -> Optional[FundingRate]:
        """Get funding rate for instrument"""
        with self._lock:
            return self._funding_rates.get(inst_id)

    def get_price_history(self, inst_id: str, periods: int = None) -> List[float]:
        """
        Get historical prices for instrument.

        Args:
            inst_id: Instrument ID
            periods: Number of periods to return (None = all)

        Returns:
            List of prices (oldest first)
        """
        with self._lock:
            history = self._price_history.get(inst_id, deque())
            prices = [s.price for s in history]

            if periods and len(prices) > periods:
                return prices[-periods:]
            return prices

    def get_funding_history(self, inst_id: str, periods: int = None) -> List[float]:
        """
        Get historical funding rates for instrument.

        Args:
            inst_id: Instrument ID
            periods: Number of periods to return (None = all)

        Returns:
            List of funding rates (oldest first)
        """
        with self._lock:
            history = self._funding_history.get(inst_id, deque())
            rates = [f.funding_rate for f in history]

            if periods and len(rates) > periods:
                return rates[-periods:]
            return rates

    def get_basis(self, spot_inst: str, futures_inst: str) -> Optional[float]:
        """
        Calculate current basis (futures - spot).

        Returns:
            Basis in absolute terms, or None if data unavailable
        """
        spot = self.get_price(spot_inst)
        futures = self.get_price(futures_inst)

        if spot and futures:
            return futures - spot
        return None

    def get_basis_pct(self, spot_inst: str, futures_inst: str) -> Optional[float]:
        """
        Calculate current basis as percentage.

        Returns:
            Basis as percentage of spot, or None if data unavailable
        """
        spot = self.get_price(spot_inst)
        futures = self.get_price(futures_inst)

        if spot and futures:
            return (futures - spot) / spot
        return None

    def get_basis_history(
        self,
        spot_inst: str,
        futures_inst: str,
        periods: int = None
    ) -> List[float]:
        """
        Get historical basis percentages.

        Returns:
            List of basis percentages (oldest first)
        """
        spot_history = self.get_price_history(spot_inst, periods)
        futures_history = self.get_price_history(futures_inst, periods)

        if not spot_history or not futures_history:
            return []

        # Align histories by taking minimum length
        min_len = min(len(spot_history), len(futures_history))
        spot_history = spot_history[-min_len:]
        futures_history = futures_history[-min_len:]

        return [(f - s) / s for s, f in zip(spot_history, futures_history)]

    def get_all_tickers(self) -> Dict[str, Ticker]:
        """Get all current tickers"""
        with self._lock:
            return dict(self._tickers)

    def get_all_funding_rates(self) -> Dict[str, FundingRate]:
        """Get all current funding rates"""
        with self._lock:
            return dict(self._funding_rates)

    def is_data_fresh(self, inst_id: str, max_age_seconds: float = 5.0) -> bool:
        """
        Check if data for instrument is fresh.

        Args:
            inst_id: Instrument ID
            max_age_seconds: Maximum age in seconds

        Returns:
            True if data is fresh
        """
        ticker = self.get_ticker(inst_id)
        if not ticker:
            return False

        age = (datetime.now(timezone.utc) - ticker.timestamp).total_seconds()
        return age <= max_age_seconds

    def clear(self) -> None:
        """Clear all stored data"""
        with self._lock:
            self._tickers.clear()
            self._funding_rates.clear()
            self._price_history.clear()
            self._funding_history.clear()

    def get_stats(self) -> Dict:
        """Get store statistics"""
        with self._lock:
            return {
                'tickers': len(self._tickers),
                'funding_rates': len(self._funding_rates),
                'price_history_instruments': len(self._price_history),
                'funding_history_instruments': len(self._funding_history),
                'total_price_points': sum(len(h) for h in self._price_history.values()),
                'total_funding_points': sum(len(h) for h in self._funding_history.values())
            }
