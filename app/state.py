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
        self._spot_bid = 0.0
        self._spot_ask = 0.0
        self._perp_price = 0.0
        self._perp_bid = 0.0
        self._perp_ask = 0.0
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

        # Hurst settings
        self._hurst_enabled = True
        self._hurst_threshold = 0.5

        # Trading costs (configurable)
        self._trading_fee = 0.0005  # 0.05% per trade

        # Auto-trading toggle (master switch)
        self._auto_trading_enabled = False  # Default OFF for safety

        self._funding_enabled = True
        self._funding_zscore = 0.0
        self._funding_signal = 'none'
        self._funding_has_position = False

        # Last update time
        self._last_update = datetime.utcnow()

    def update_market_data(self, spot: float, perp: float, funding: float,
                           predicted: float = None, futures: list = None,
                           spot_bid: float = 0, spot_ask: float = 0,
                           perp_bid: float = 0, perp_ask: float = 0):
        """Update market data from collector"""
        with self._data_lock:
            self._spot_price = spot or 0.0
            self._spot_bid = spot_bid or 0.0
            self._spot_ask = spot_ask or 0.0
            self._perp_price = perp or 0.0
            self._perp_bid = perp_bid or 0.0
            self._perp_ask = perp_ask or 0.0
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

    def set_hurst_enabled(self, enabled: bool):
        """Enable or disable Hurst filter"""
        with self._data_lock:
            self._hurst_enabled = enabled

    def set_hurst_threshold(self, threshold: float):
        """Set Hurst threshold (0.0 to 1.0)"""
        with self._data_lock:
            self._hurst_threshold = max(0.0, min(1.0, threshold))

    def get_hurst_settings(self) -> Dict[str, Any]:
        """Get Hurst settings"""
        with self._data_lock:
            return {
                'enabled': self._hurst_enabled,
                'threshold': self._hurst_threshold
            }

    def is_hurst_enabled(self) -> bool:
        """Check if Hurst filter is enabled"""
        with self._data_lock:
            return self._hurst_enabled

    def get_hurst_threshold(self) -> float:
        """Get Hurst threshold"""
        with self._data_lock:
            return self._hurst_threshold

    def get_trading_costs(self) -> Dict[str, float]:
        """Get trading cost configuration"""
        with self._data_lock:
            return {
                'fee_per_trade': self._trading_fee,
                'round_trip_cost': self._trading_fee * 4,  # 4 trades: buy spot, sell futures, sell spot, buy futures
            }

    def set_auto_trading(self, enabled: bool):
        """Enable or disable auto-trading"""
        with self._data_lock:
            self._auto_trading_enabled = enabled

    def is_auto_trading_enabled(self) -> bool:
        """Check if auto-trading is enabled"""
        with self._data_lock:
            return self._auto_trading_enabled

    def get_market_data(self) -> Dict[str, Any]:
        """Get current market data for API"""
        with self._data_lock:
            spot_spread = self._spot_ask - self._spot_bid if self._spot_ask and self._spot_bid else 0
            perp_spread = self._perp_ask - self._perp_bid if self._perp_ask and self._perp_bid else 0
            return {
                'spot': {
                    'instrument': 'BTC-USDT',
                    'price': self._spot_price,
                    'bid': self._spot_bid,
                    'ask': self._spot_ask,
                    'spread': spot_spread,
                    'timestamp': self._last_update.isoformat()
                },
                'perp': {
                    'instrument': 'BTC-USDT-SWAP',
                    'price': self._perp_price,
                    'bid': self._perp_bid,
                    'ask': self._perp_ask,
                    'spread': perp_spread,
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
                'auto_trading': self._auto_trading_enabled,
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
                },
                'hurst': {
                    'enabled': self._hurst_enabled,
                    'threshold': self._hurst_threshold
                },
                'costs': {
                    'fee_per_trade': self._trading_fee,
                    'round_trip_pct': self._trading_fee * 4 * 100  # As percentage
                }
            }


# Global singleton instance
trading_state = TradingState()
