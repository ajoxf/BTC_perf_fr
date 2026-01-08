"""
Shared state between trading engine and web interface
"""
from datetime import datetime
from typing import Dict, Any, Optional
import threading


class TradingState:
    """Thread-safe shared state for trading system"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._data_lock = threading.RLock()

        # Market data
        self._spot_price = 0.0
        self._perp_price = 0.0
        self._funding_rate = 0.0
        self._predicted_rate = 0.0
        self._futures = []

        # Strategy state
        self._basis_enabled = True
        self._basis_zscore = 0.0
        self._basis_hurst = 0.5
        self._basis_signal = 'none'
        self._basis_pct = 0.0
        self._basis_has_position = False

        self._funding_enabled = True
        self._funding_zscore = 0.0
        self._funding_signal = 'none'
        self._funding_has_position = False

        # Last update time
        self._last_update = datetime.utcnow()

    def update_market_data(self, spot: float, perp: float, funding: float,
                           predicted: float = None, futures: list = None):
        """Update market data from collector"""
        with self._data_lock:
            self._spot_price = spot or 0.0
            self._perp_price = perp or 0.0
            self._funding_rate = funding or 0.0
            self._predicted_rate = predicted or 0.0
            self._futures = futures or []
            self._last_update = datetime.utcnow()

    def update_basis_strategy(self, zscore: float, hurst: float, signal: str,
                               basis_pct: float = 0, has_position: bool = False):
        """Update basis strategy state"""
        with self._data_lock:
            self._basis_zscore = zscore
            self._basis_hurst = hurst
            self._basis_signal = signal
            self._basis_pct = basis_pct
            self._basis_has_position = has_position

    def update_funding_strategy(self, zscore: float, signal: str,
                                 has_position: bool = False):
        """Update funding strategy state"""
        with self._data_lock:
            self._funding_zscore = zscore
            self._funding_signal = signal
            self._funding_has_position = has_position

    def set_strategy_enabled(self, strategy: str, enabled: bool):
        """Enable or disable a strategy"""
        with self._data_lock:
            if strategy == 'basis':
                self._basis_enabled = enabled
            elif strategy == 'funding':
                self._funding_enabled = enabled

    def is_strategy_enabled(self, strategy: str) -> bool:
        """Check if strategy is enabled"""
        with self._data_lock:
            if strategy == 'basis':
                return self._basis_enabled
            elif strategy == 'funding':
                return self._funding_enabled
        return False

    def get_market_data(self) -> Dict[str, Any]:
        """Get current market data for API"""
        with self._data_lock:
            return {
                'spot': {
                    'instrument': 'BTC-USDT',
                    'price': self._spot_price,
                    'timestamp': self._last_update.isoformat()
                },
                'perp': {
                    'instrument': 'BTC-USDT-SWAP',
                    'price': self._perp_price,
                    'funding_rate': self._funding_rate,
                    'predicted_rate': self._predicted_rate,
                    'timestamp': self._last_update.isoformat()
                },
                'futures': self._futures
            }

    def get_strategies(self) -> Dict[str, Any]:
        """Get strategy status for API"""
        with self._data_lock:
            return {
                'basis': {
                    'enabled': self._basis_enabled,
                    'has_position': self._basis_has_position,
                    'current_zscore': self._basis_zscore,
                    'current_hurst': self._basis_hurst,
                    'current_basis_pct': self._basis_pct,
                    'signal': self._basis_signal
                },
                'funding': {
                    'enabled': self._funding_enabled,
                    'has_position': self._funding_has_position,
                    'current_rate': self._funding_rate,
                    'current_zscore': self._funding_zscore,
                    'signal': self._funding_signal
                }
            }


# Global singleton instance
trading_state = TradingState()
