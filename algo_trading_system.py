#!/usr/bin/env python3
"""
Algorithmic Basis Trading System for OKX
Statistical arbitrage between BTC spot and futures

Based on the MT5 Statistical Arbitrage system, adapted for OKX exchange.
"""

import os
import sys
import time
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from loguru import logger

# Load environment variables
load_dotenv()

# Configure logging
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)
logger.add(
    "logs/algo_trading_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="DEBUG"
)


# ==================== ENUMS ====================

class SignalType(Enum):
    NO_SIGNAL = "NO_SIGNAL"
    SELL_BASIS = "SELL_BASIS"      # Futures expensive: Buy spot, Short futures
    BUY_BASIS = "BUY_BASIS"        # Futures cheap: Sell spot, Long futures
    CLOSE_LONG = "CLOSE_LONG"      # Close SELL_BASIS position
    CLOSE_SHORT = "CLOSE_SHORT"    # Close BUY_BASIS position


class PositionStatus(Enum):
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


class TradingSession(Enum):
    ASIAN_PRE = "ASIAN_PRE"
    ASIAN = "ASIAN"
    EUROPEAN_PRE = "EUROPEAN_PRE"
    EUROPEAN = "EUROPEAN"
    US_PRE = "US_PRE"
    US_OPEN = "US_OPEN"
    US_CLOSE = "US_CLOSE"
    AFTER_HOURS = "AFTER_HOURS"


# ==================== DATA CLASSES ====================

@dataclass
class Trade:
    """Individual trade/order"""
    id: str
    symbol: str
    side: str  # 'buy' or 'sell'
    size: float
    price: float
    status: str = "pending"
    filled_price: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: str = ""


@dataclass
class Position:
    """Paired spot/futures position"""
    id: str
    position_type: str  # 'SELL_BASIS' or 'BUY_BASIS'
    spot_trade: Trade
    futures_trade: Trade
    entry_premium_pct: float
    status: PositionStatus = PositionStatus.ACTIVE
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exit_time: Optional[datetime] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class MarketData:
    """Current market snapshot"""
    spot_price: float = 0.0
    spot_bid: float = 0.0
    spot_ask: float = 0.0
    futures_price: float = 0.0
    futures_bid: float = 0.0
    futures_ask: float = 0.0
    futures_instrument: str = ""
    futures_expiry: datetime = None
    days_to_expiry: int = 0
    basis_usd: float = 0.0
    basis_pct: float = 0.0
    annualized_basis_pct: float = 0.0
    funding_rate: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ==================== CONFIGURATION ====================

class AlgoTradingConfig:
    """Trading system configuration"""

    def __init__(self):
        # Signal thresholds (basis premium/discount percentages)
        self.premium_entry_threshold = 0.005    # 0.5% - Enter SELL_BASIS when futures > spot by this %
        self.premium_exit_threshold = 0.001     # 0.1% - Exit SELL_BASIS when premium falls to this
        self.discount_entry_threshold = -0.003  # -0.3% - Enter BUY_BASIS when futures < spot by this %
        self.discount_exit_threshold = -0.001   # -0.1% - Exit BUY_BASIS when discount rises to this

        # Risk limits
        self.max_positions = 3
        self.max_position_usd = 10000.0
        self.max_daily_trades = 20
        self.stop_loss_pct = 0.02  # 2% adverse move triggers stop
        self.min_signal_interval_seconds = 180  # 3 minutes between signals

        # Execution parameters
        self.slippage_tolerance_pct = 0.001  # 0.1%
        self.order_timeout_seconds = 30

        # Trading costs (OKX)
        self.maker_fee = 0.0002  # 0.02%
        self.taker_fee = 0.0005  # 0.05%
        self.round_trip_cost = 4 * self.taker_fee  # 4 trades total

        # Asset configuration
        self.spot_symbol = "BTC-USDT"
        self.perp_symbol = "BTC-USDT-SWAP"
        self.futures_symbol = None  # Auto-selected based on liquidity

        # Display settings
        self.refresh_interval = 0.5  # seconds
        self.clear_screen_interval = 50  # loops


# ==================== DATABASE LOGGER ====================

class DataLogger:
    """SQLite database for trade logging"""

    def __init__(self, db_path: str = "algo_trading.db"):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Trades table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                position_id TEXT,
                symbol TEXT,
                side TEXT,
                size REAL,
                price REAL,
                filled_price REAL,
                status TEXT,
                timestamp TEXT,
                order_id TEXT
            )
        ''')

        # Positions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                position_type TEXT,
                spot_trade_id TEXT,
                futures_trade_id TEXT,
                entry_premium_pct REAL,
                status TEXT,
                entry_time TEXT,
                exit_time TEXT,
                realized_pnl REAL,
                unrealized_pnl REAL
            )
        ''')

        # Market data table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                spot_price REAL,
                futures_price REAL,
                futures_instrument TEXT,
                basis_usd REAL,
                basis_pct REAL,
                annualized_basis_pct REAL,
                signal TEXT
            )
        ''')

        conn.commit()
        conn.close()

    def log_trade(self, trade: Trade, position_id: str):
        """Log a trade to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO trades
            (id, position_id, symbol, side, size, price, filled_price, status, timestamp, order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (trade.id, position_id, trade.symbol, trade.side, trade.size,
              trade.price, trade.filled_price, trade.status,
              trade.timestamp.isoformat(), trade.order_id))
        conn.commit()
        conn.close()

    def log_position(self, position: Position):
        """Log a position to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO positions
            (id, position_type, spot_trade_id, futures_trade_id, entry_premium_pct,
             status, entry_time, exit_time, realized_pnl, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (position.id, position.position_type, position.spot_trade.id,
              position.futures_trade.id, position.entry_premium_pct,
              position.status.value, position.entry_time.isoformat(),
              position.exit_time.isoformat() if position.exit_time else None,
              position.realized_pnl, position.unrealized_pnl))
        conn.commit()
        conn.close()

    def log_market_data(self, data: MarketData, signal: SignalType):
        """Log market data snapshot"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO market_data
            (timestamp, spot_price, futures_price, futures_instrument,
             basis_usd, basis_pct, annualized_basis_pct, signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data.timestamp.isoformat(), data.spot_price, data.futures_price,
              data.futures_instrument, data.basis_usd, data.basis_pct,
              data.annualized_basis_pct, signal.value))
        conn.commit()
        conn.close()


# ==================== ORDER MANAGER ====================

class OrderManager:
    """Handles order execution on OKX"""

    def __init__(self, client, paper_mode: bool = True):
        self.client = client
        self.paper_mode = paper_mode
        self.slippage_pct = 0.001

    def execute_market_order(self, symbol: str, side: str, size_usd: float,
                             current_price: float) -> Optional[Trade]:
        """Execute a market order"""
        trade_id = f"T{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        # Calculate size in base currency
        size = size_usd / current_price

        # Apply simulated slippage
        if side == 'buy':
            fill_price = current_price * (1 + self.slippage_pct)
        else:
            fill_price = current_price * (1 - self.slippage_pct)

        trade = Trade(
            id=trade_id,
            symbol=symbol,
            side=side,
            size=size,
            price=current_price,
            filled_price=fill_price,
            status="filled" if self.paper_mode else "pending"
        )

        if not self.paper_mode:
            # Execute real order on OKX
            try:
                # Determine trade mode based on instrument type
                if 'SWAP' in symbol or '-2' in symbol:  # Futures/Swap
                    trade_mode = 'cross'
                else:
                    trade_mode = 'cash'

                result = self.client.place_order(
                    inst_id=symbol,
                    side=side,
                    order_type='market',
                    size=size,
                    trade_mode=trade_mode
                )

                if result.get('code') == '0':
                    trade.order_id = result['data'][0].get('ordId', '')
                    trade.status = "filled"
                    logger.info(f"Order executed: {side} {size:.6f} {symbol} @ {fill_price:.2f}")
                else:
                    trade.status = "failed"
                    logger.error(f"Order failed: {result.get('msg')}")
                    return None
            except Exception as e:
                logger.error(f"Order execution error: {e}")
                trade.status = "failed"
                return None
        else:
            logger.info(f"[PAPER] {side.upper()} {size:.6f} {symbol} @ ${fill_price:.2f}")

        return trade

    def execute_trade_pair(self, spot_symbol: str, futures_symbol: str,
                          position_type: str, size_usd: float,
                          spot_price: float, futures_price: float) -> Optional[Dict[str, Trade]]:
        """Execute paired spot/futures trades"""

        if position_type == "SELL_BASIS":
            # Buy spot, Sell futures
            spot_side = "buy"
            futures_side = "sell"
        else:  # BUY_BASIS
            # Sell spot, Buy futures
            spot_side = "sell"
            futures_side = "buy"

        # Execute spot trade
        spot_trade = self.execute_market_order(spot_symbol, spot_side, size_usd, spot_price)
        if not spot_trade:
            return None

        # Execute futures trade
        futures_trade = self.execute_market_order(futures_symbol, futures_side, size_usd, futures_price)
        if not futures_trade:
            # Reverse spot trade on failure
            reverse_side = "sell" if spot_side == "buy" else "buy"
            self.execute_market_order(spot_symbol, reverse_side, size_usd, spot_price)
            return None

        return {"spot": spot_trade, "futures": futures_trade}


# ==================== POSITION MANAGER ====================

class PositionManager:
    """Manages trading positions"""

    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self._lock = threading.Lock()

    def create_position(self, position_type: str, spot_trade: Trade,
                       futures_trade: Trade, entry_premium_pct: float) -> Position:
        """Create a new position"""
        position_id = f"P{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        position = Position(
            id=position_id,
            position_type=position_type,
            spot_trade=spot_trade,
            futures_trade=futures_trade,
            entry_premium_pct=entry_premium_pct
        )

        with self._lock:
            self.positions[position_id] = position

        logger.info(f"Position opened: {position_id} [{position_type}] entry premium: {entry_premium_pct:.4%}")
        return position

    def update_position_pnl(self, position: Position, current_spot: float,
                           current_futures: float) -> float:
        """Update position's unrealized P&L"""
        spot_size = position.spot_trade.size
        futures_size = position.futures_trade.size

        if position.position_type == "SELL_BASIS":
            # Long spot, Short futures
            spot_pnl = (current_spot - position.spot_trade.filled_price) * spot_size
            futures_pnl = (position.futures_trade.filled_price - current_futures) * futures_size
        else:  # BUY_BASIS
            # Short spot, Long futures
            spot_pnl = (position.spot_trade.filled_price - current_spot) * spot_size
            futures_pnl = (current_futures - position.futures_trade.filled_price) * futures_size

        position.unrealized_pnl = spot_pnl + futures_pnl
        return position.unrealized_pnl

    def close_position(self, position: Position, realized_pnl: float):
        """Close a position"""
        position.status = PositionStatus.CLOSED
        position.exit_time = datetime.now(timezone.utc)
        position.realized_pnl = realized_pnl

        logger.info(f"Position closed: {position.id} P&L: ${realized_pnl:.2f}")

    def get_active_positions(self) -> List[Position]:
        """Get all active positions"""
        with self._lock:
            return [p for p in self.positions.values() if p.status == PositionStatus.ACTIVE]

    def get_position_count(self) -> int:
        """Get count of active positions"""
        return len(self.get_active_positions())


# ==================== RISK MANAGER ====================

class RiskManager:
    """Risk management and validation"""

    def __init__(self, config: AlgoTradingConfig):
        self.config = config
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.last_signal_time: Dict[str, datetime] = {}
        self.daily_reset_date = datetime.now(timezone.utc).date()

    def reset_daily_if_needed(self):
        """Reset daily counters if new day"""
        current_date = datetime.now(timezone.utc).date()
        if current_date != self.daily_reset_date:
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.daily_reset_date = current_date
            logger.info("Daily counters reset")

    def validate_new_position(self, position_count: int, signal_type: SignalType) -> tuple[bool, str]:
        """Validate if a new position can be opened"""
        self.reset_daily_if_needed()

        # Check position limit
        if position_count >= self.config.max_positions:
            return False, f"Max positions reached ({self.config.max_positions})"

        # Check daily trade limit
        if self.daily_trades >= self.config.max_daily_trades:
            return False, f"Max daily trades reached ({self.config.max_daily_trades})"

        # Check signal interval
        signal_key = signal_type.value
        if signal_key in self.last_signal_time:
            elapsed = (datetime.now(timezone.utc) - self.last_signal_time[signal_key]).total_seconds()
            if elapsed < self.config.min_signal_interval_seconds:
                remaining = self.config.min_signal_interval_seconds - elapsed
                return False, f"Signal cooldown ({remaining:.0f}s remaining)"

        return True, "OK"

    def check_stop_loss(self, position: Position, current_premium_pct: float) -> bool:
        """Check if stop loss should trigger"""
        premium_change = current_premium_pct - position.entry_premium_pct

        if position.position_type == "SELL_BASIS":
            # Stop if premium increases (adverse for short futures)
            if premium_change > self.config.stop_loss_pct:
                return True
        else:  # BUY_BASIS
            # Stop if discount increases (adverse for long futures)
            if premium_change < -self.config.stop_loss_pct:
                return True

        return False

    def record_trade(self, signal_type: SignalType):
        """Record a trade for risk tracking"""
        self.daily_trades += 1
        self.last_signal_time[signal_type.value] = datetime.now(timezone.utc)

    def record_pnl(self, pnl: float):
        """Record P&L"""
        self.daily_pnl += pnl

    def get_risk_status(self, position_count: int) -> str:
        """Get current risk status"""
        if position_count >= self.config.max_positions:
            return "MAX_POSITIONS"
        if self.daily_trades >= self.config.max_daily_trades * 0.8:
            return "HIGH_FREQUENCY"
        return "NORMAL"


# ==================== SIGNAL GENERATOR ====================

class SignalGenerator:
    """Generate trading signals based on basis"""

    def __init__(self, config: AlgoTradingConfig):
        self.config = config

    def generate_signal(self, market_data: MarketData,
                       active_positions: List[Position]) -> SignalType:
        """Generate trading signal based on current market conditions"""
        basis_pct = market_data.basis_pct

        # Check for exit signals first (for active positions)
        for position in active_positions:
            if position.position_type == "SELL_BASIS":
                # Exit when premium falls to exit threshold
                if basis_pct <= self.config.premium_exit_threshold:
                    return SignalType.CLOSE_LONG
            else:  # BUY_BASIS
                # Exit when discount rises to exit threshold
                if basis_pct >= self.config.discount_exit_threshold:
                    return SignalType.CLOSE_SHORT

        # Check for entry signals (only if no position of that type exists)
        has_sell_basis = any(p.position_type == "SELL_BASIS" for p in active_positions)
        has_buy_basis = any(p.position_type == "BUY_BASIS" for p in active_positions)

        # Net profit after costs
        net_premium = basis_pct - self.config.round_trip_cost
        net_discount = basis_pct + self.config.round_trip_cost

        if not has_sell_basis and net_premium >= self.config.premium_entry_threshold:
            return SignalType.SELL_BASIS

        if not has_buy_basis and net_discount <= self.config.discount_entry_threshold:
            return SignalType.BUY_BASIS

        return SignalType.NO_SIGNAL

    def get_market_sentiment(self, basis_pct: float) -> str:
        """Get market sentiment based on basis"""
        if basis_pct > self.config.premium_entry_threshold:
            return "EXPENSIVE"
        elif basis_pct < self.config.discount_entry_threshold:
            return "CHEAP"
        else:
            return "FAIR"


# ==================== PERFORMANCE TRACKER ====================

class PerformanceTracker:
    """Track trading performance metrics"""

    def __init__(self):
        self.total_pnl = 0.0
        self.daily_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.max_drawdown = 0.0
        self.peak_pnl = 0.0
        self.daily_reset_date = datetime.now(timezone.utc).date()

    def reset_daily_if_needed(self):
        """Reset daily metrics if new day"""
        current_date = datetime.now(timezone.utc).date()
        if current_date != self.daily_reset_date:
            self.daily_pnl = 0.0
            self.daily_reset_date = current_date

    def update_with_closed_position(self, pnl: float):
        """Update metrics when position closes"""
        self.total_pnl += pnl
        self.daily_pnl += pnl
        self.total_trades += 1

        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        # Update drawdown
        if self.total_pnl > self.peak_pnl:
            self.peak_pnl = self.total_pnl

        current_drawdown = self.peak_pnl - self.total_pnl
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown

    def get_win_rate(self) -> float:
        """Get win rate percentage"""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    def get_metrics(self) -> Dict[str, Any]:
        """Get all performance metrics"""
        self.reset_daily_if_needed()
        return {
            'total_pnl': self.total_pnl,
            'daily_pnl': self.daily_pnl,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.get_win_rate(),
            'max_drawdown': self.max_drawdown
        }


# ==================== MAIN TRADING SYSTEM ====================

class AlgorithmicTradingSystem:
    """Main trading system orchestrator"""

    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.config = AlgoTradingConfig()
        self.client = None
        self.running = False

        # Initialize components
        self.db_logger = DataLogger()
        self.order_manager = None
        self.position_manager = PositionManager()
        self.risk_manager = RiskManager(self.config)
        self.signal_generator = SignalGenerator(self.config)
        self.performance = PerformanceTracker()

        # Market data
        self.market_data = MarketData()
        self.current_signal = SignalType.NO_SIGNAL

        # Display state
        self.loop_count = 0
        self.last_error = None
        self.connection_status = "DISCONNECTED"

    def initialize_okx(self) -> bool:
        """Initialize OKX connection"""
        try:
            from app.api.okx_client import OKXClient

            self.client = OKXClient(
                api_key=os.environ.get('OKX_API_KEY', ''),
                secret_key=os.environ.get('OKX_SECRET_KEY', ''),
                passphrase=os.environ.get('OKX_PASSPHRASE', ''),
                demo_trading=self.paper_mode
            )

            self.order_manager = OrderManager(self.client, self.paper_mode)

            # Test connection
            ticker = self.client.get_ticker('BTC-USDT')
            if ticker:
                self.connection_status = "CONNECTED"
                logger.info(f"OKX connection established. BTC price: ${ticker.last_price:,.2f}")
                return True
            else:
                self.connection_status = "ERROR"
                logger.error("Failed to get ticker data")
                return False

        except Exception as e:
            self.connection_status = "ERROR"
            logger.error(f"OKX initialization failed: {e}")
            return False

    def get_market_data(self) -> bool:
        """Fetch current market data"""
        try:
            # Get spot data
            spot_ticker = self.client.get_ticker(self.config.spot_symbol)
            if not spot_ticker:
                return False

            self.market_data.spot_price = spot_ticker.last_price
            self.market_data.spot_bid = spot_ticker.bid_price
            self.market_data.spot_ask = spot_ticker.ask_price

            # Get futures data (most liquid)
            futures_instruments = self.client.get_instruments('FUTURES', 'BTC-USDT')
            if futures_instruments:
                # Get tickers for all futures
                futures_tickers = []
                for inst in futures_instruments:
                    ticker = self.client.get_ticker(inst.inst_id)
                    if ticker and ticker.volume_24h:
                        futures_tickers.append({
                            'instrument': inst,
                            'ticker': ticker
                        })

                # Sort by volume and pick most liquid with >3 days to expiry
                futures_tickers.sort(key=lambda x: x['ticker'].volume_24h, reverse=True)

                now = datetime.now(timezone.utc)
                for ft in futures_tickers:
                    if ft['instrument'].expiry_time:
                        days_left = (ft['instrument'].expiry_time - now).days
                        if days_left >= 3:
                            self.market_data.futures_instrument = ft['instrument'].inst_id
                            self.market_data.futures_price = ft['ticker'].last_price
                            self.market_data.futures_bid = ft['ticker'].bid_price
                            self.market_data.futures_ask = ft['ticker'].ask_price
                            self.market_data.futures_expiry = ft['instrument'].expiry_time
                            self.market_data.days_to_expiry = days_left
                            self.config.futures_symbol = ft['instrument'].inst_id
                            break

            # Calculate basis
            if self.market_data.spot_price > 0 and self.market_data.futures_price > 0:
                self.market_data.basis_usd = self.market_data.futures_price - self.market_data.spot_price
                self.market_data.basis_pct = self.market_data.basis_usd / self.market_data.spot_price

                if self.market_data.days_to_expiry > 0:
                    self.market_data.annualized_basis_pct = (
                        self.market_data.basis_pct / self.market_data.days_to_expiry * 365
                    )

            # Get funding rate for perp
            funding = self.client.get_funding_rate(self.config.perp_symbol)
            if funding:
                self.market_data.funding_rate = funding.funding_rate

            self.market_data.timestamp = datetime.now(timezone.utc)
            return True

        except Exception as e:
            logger.error(f"Failed to get market data: {e}")
            return False

    def process_signals(self):
        """Process trading signals and execute trades"""
        active_positions = self.position_manager.get_active_positions()

        # Generate signal
        self.current_signal = self.signal_generator.generate_signal(
            self.market_data, active_positions
        )

        # Process exit signals
        if self.current_signal in [SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]:
            for position in active_positions:
                if ((self.current_signal == SignalType.CLOSE_LONG and position.position_type == "SELL_BASIS") or
                    (self.current_signal == SignalType.CLOSE_SHORT and position.position_type == "BUY_BASIS")):
                    self._close_position(position)
                    return

        # Check stop losses
        for position in active_positions:
            if self.risk_manager.check_stop_loss(position, self.market_data.basis_pct):
                logger.warning(f"Stop loss triggered for position {position.id}")
                self._close_position(position)
                return

        # Process entry signals
        if self.current_signal in [SignalType.SELL_BASIS, SignalType.BUY_BASIS]:
            valid, reason = self.risk_manager.validate_new_position(
                len(active_positions), self.current_signal
            )

            if valid:
                self._open_position(self.current_signal.value)
            else:
                logger.debug(f"Signal blocked: {reason}")

    def _open_position(self, position_type: str):
        """Open a new position"""
        trades = self.order_manager.execute_trade_pair(
            spot_symbol=self.config.spot_symbol,
            futures_symbol=self.config.futures_symbol,
            position_type=position_type,
            size_usd=self.config.max_position_usd,
            spot_price=self.market_data.spot_price,
            futures_price=self.market_data.futures_price
        )

        if trades:
            position = self.position_manager.create_position(
                position_type=position_type,
                spot_trade=trades['spot'],
                futures_trade=trades['futures'],
                entry_premium_pct=self.market_data.basis_pct
            )

            self.risk_manager.record_trade(SignalType[position_type])
            self.db_logger.log_trade(trades['spot'], position.id)
            self.db_logger.log_trade(trades['futures'], position.id)
            self.db_logger.log_position(position)

    def _close_position(self, position: Position):
        """Close an existing position"""
        # Execute reverse trades
        if position.position_type == "SELL_BASIS":
            spot_side = "sell"
            futures_side = "buy"
        else:
            spot_side = "buy"
            futures_side = "sell"

        spot_size_usd = position.spot_trade.size * self.market_data.spot_price
        futures_size_usd = position.futures_trade.size * self.market_data.futures_price

        spot_trade = self.order_manager.execute_market_order(
            self.config.spot_symbol, spot_side, spot_size_usd, self.market_data.spot_price
        )

        futures_trade = self.order_manager.execute_market_order(
            self.config.futures_symbol, futures_side, futures_size_usd, self.market_data.futures_price
        )

        if spot_trade and futures_trade:
            # Calculate realized P&L
            self.position_manager.update_position_pnl(
                position, self.market_data.spot_price, self.market_data.futures_price
            )
            realized_pnl = position.unrealized_pnl

            self.position_manager.close_position(position, realized_pnl)
            self.performance.update_with_closed_position(realized_pnl)
            self.risk_manager.record_pnl(realized_pnl)
            self.db_logger.log_position(position)

    def update_positions_pnl(self):
        """Update P&L for all active positions"""
        for position in self.position_manager.get_active_positions():
            self.position_manager.update_position_pnl(
                position,
                self.market_data.spot_price,
                self.market_data.futures_price
            )

    def get_current_session(self) -> TradingSession:
        """Determine current trading session"""
        now = datetime.now(timezone.utc)
        hour = now.hour

        if 0 <= hour < 6:
            return TradingSession.ASIAN
        elif 6 <= hour < 8:
            return TradingSession.EUROPEAN_PRE
        elif 8 <= hour < 13:
            return TradingSession.EUROPEAN
        elif 13 <= hour < 14:
            return TradingSession.US_PRE
        elif 14 <= hour < 21:
            return TradingSession.US_OPEN
        else:
            return TradingSession.AFTER_HOURS

    def print_display(self):
        """Print the trading dashboard to console"""
        # Clear screen periodically
        if self.loop_count % self.config.clear_screen_interval == 0:
            os.system('cls' if os.name == 'nt' else 'clear')

        session = self.get_current_session()
        mode = "PAPER" if self.paper_mode else "LIVE"
        sentiment = self.signal_generator.get_market_sentiment(self.market_data.basis_pct)
        metrics = self.performance.get_metrics()
        active_positions = self.position_manager.get_active_positions()
        risk_status = self.risk_manager.get_risk_status(len(active_positions))

        # Build display
        lines = []
        lines.append("=" * 80)
        lines.append(f"  ALGORITHMIC BASIS TRADING SYSTEM - OKX")
        lines.append(f"  Session: {session.value:<15} Mode: {mode:<10} Status: {self.connection_status}")
        lines.append("=" * 80)
        lines.append("")

        # Market Data Section
        lines.append(f"  BTC SPOT ({self.config.spot_symbol})")
        lines.append(f"  Price: ${self.market_data.spot_price:>12,.2f}    Bid: ${self.market_data.spot_bid:>12,.2f}    Ask: ${self.market_data.spot_ask:>12,.2f}")
        lines.append("")

        lines.append(f"  BTC FUTURES ({self.market_data.futures_instrument or 'N/A'})")
        lines.append(f"  Price: ${self.market_data.futures_price:>12,.2f}    Bid: ${self.market_data.futures_bid:>12,.2f}    Ask: ${self.market_data.futures_ask:>12,.2f}")
        lines.append(f"  Expiry: {self.market_data.futures_expiry.strftime('%Y-%m-%d') if self.market_data.futures_expiry else 'N/A'}    Days Left: {self.market_data.days_to_expiry}")
        lines.append("")

        # Basis Analysis
        lines.append("-" * 80)
        basis_color = "\033[92m" if self.market_data.basis_usd >= 0 else "\033[91m"
        reset = "\033[0m"

        lines.append(f"  BASIS ANALYSIS")
        lines.append(f"  Spread: {basis_color}${self.market_data.basis_usd:>+10,.2f}{reset}    Premium: {basis_color}{self.market_data.basis_pct:>+8.4%}{reset}    Annualized: {basis_color}{self.market_data.annualized_basis_pct:>+8.2%}{reset}")
        lines.append(f"  Sentiment: {sentiment:<12}    Funding Rate: {self.market_data.funding_rate:>+.4%}")
        lines.append("")

        # Signal
        signal_color = "\033[93m" if self.current_signal != SignalType.NO_SIGNAL else "\033[90m"
        lines.append(f"  SIGNAL: {signal_color}{self.current_signal.value}{reset}")
        lines.append("")

        # Thresholds
        lines.append("-" * 80)
        lines.append(f"  THRESHOLDS")
        lines.append(f"  Entry SELL_BASIS: {self.config.premium_entry_threshold:>+.3%}    Exit: {self.config.premium_exit_threshold:>+.3%}")
        lines.append(f"  Entry BUY_BASIS:  {self.config.discount_entry_threshold:>+.3%}    Exit: {self.config.discount_exit_threshold:>+.3%}")
        lines.append(f"  Round-trip Cost:  {self.config.round_trip_cost:.3%}")
        lines.append("")

        # Active Positions
        lines.append("-" * 80)
        lines.append(f"  ACTIVE POSITIONS ({len(active_positions)}/{self.config.max_positions})")
        if active_positions:
            lines.append(f"  {'ID':<20} {'Type':<12} {'Entry %':<12} {'Current %':<12} {'P&L':>12} {'Age':<10}")
            for pos in active_positions:
                age = datetime.now(timezone.utc) - pos.entry_time
                age_str = f"{age.seconds // 3600}h {(age.seconds % 3600) // 60}m"
                pnl_color = "\033[92m" if pos.unrealized_pnl >= 0 else "\033[91m"
                lines.append(f"  {pos.id:<20} {pos.position_type:<12} {pos.entry_premium_pct:>+.4%}      {self.market_data.basis_pct:>+.4%}      {pnl_color}${pos.unrealized_pnl:>+10.2f}{reset} {age_str:<10}")
        else:
            lines.append("  No active positions")
        lines.append("")

        # Performance
        lines.append("-" * 80)
        lines.append(f"  PERFORMANCE")
        total_color = "\033[92m" if metrics['total_pnl'] >= 0 else "\033[91m"
        daily_color = "\033[92m" if metrics['daily_pnl'] >= 0 else "\033[91m"
        lines.append(f"  Total P&L: {total_color}${metrics['total_pnl']:>+10.2f}{reset}    Daily P&L: {daily_color}${metrics['daily_pnl']:>+10.2f}{reset}")
        lines.append(f"  Trades: {metrics['total_trades']:<5}    Win Rate: {metrics['win_rate']:.1f}%    Max Drawdown: ${metrics['max_drawdown']:.2f}")
        lines.append(f"  Daily Trades: {self.risk_manager.daily_trades}/{self.config.max_daily_trades}    Risk Status: {risk_status}")
        lines.append("")

        lines.append("=" * 80)
        lines.append(f"  Last Update: {self.market_data.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        lines.append("  Press Ctrl+C to stop")
        lines.append("=" * 80)

        # Print
        print("\n".join(lines))

    def trading_loop(self):
        """Main trading loop"""
        self.running = True
        error_count = 0
        max_errors = 10

        logger.info("Starting trading loop...")

        while self.running:
            try:
                self.loop_count += 1

                # Get market data
                if not self.get_market_data():
                    error_count += 1
                    if error_count >= max_errors:
                        logger.error("Too many errors, attempting reconnection...")
                        self.initialize_okx()
                        error_count = 0
                    time.sleep(1)
                    continue

                error_count = 0  # Reset on success

                # Process signals and execute trades
                self.process_signals()

                # Update position P&L
                self.update_positions_pnl()

                # Log market data
                self.db_logger.log_market_data(self.market_data, self.current_signal)

                # Update display
                self.print_display()

                # Sleep
                time.sleep(self.config.refresh_interval)

            except KeyboardInterrupt:
                logger.info("Shutdown requested by user")
                self.running = False
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                import traceback
                traceback.print_exc()
                error_count += 1
                time.sleep(1)

        # Cleanup
        self.shutdown()

    def shutdown(self):
        """Shutdown the system"""
        logger.info("Shutting down trading system...")

        # Close all positions
        active_positions = self.position_manager.get_active_positions()
        if active_positions:
            logger.info(f"Closing {len(active_positions)} active positions...")
            for position in active_positions:
                self._close_position(position)

        # Print final stats
        metrics = self.performance.get_metrics()
        print("\n" + "=" * 50)
        print("FINAL SESSION SUMMARY")
        print("=" * 50)
        print(f"Total P&L:      ${metrics['total_pnl']:>+10.2f}")
        print(f"Total Trades:   {metrics['total_trades']}")
        print(f"Win Rate:       {metrics['win_rate']:.1f}%")
        print(f"Max Drawdown:   ${metrics['max_drawdown']:.2f}")
        print("=" * 50)


# ==================== MAIN ====================

def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("  ALGORITHMIC BASIS TRADING SYSTEM - OKX")
    print("=" * 60)

    # Select mode
    print("\nSelect trading mode:")
    print("  1. PAPER (simulated)")
    print("  2. LIVE (real orders)")

    choice = input("\nEnter choice (1/2): ").strip()
    paper_mode = choice != "2"

    if not paper_mode:
        print("\n" + "!" * 60)
        print("  WARNING: LIVE TRADING MODE")
        print("  Real orders will be executed on OKX!")
        print("!" * 60)
        confirm = input("\nType 'YES I UNDERSTAND' to confirm: ").strip()
        if confirm != "YES I UNDERSTAND":
            print("Live trading not confirmed. Starting in PAPER mode.")
            paper_mode = True

    # Initialize system
    system = AlgorithmicTradingSystem(paper_mode=paper_mode)

    print(f"\nInitializing OKX connection ({'PAPER' if paper_mode else 'LIVE'} mode)...")

    if not system.initialize_okx():
        print("Failed to initialize OKX. Please check your API credentials.")
        return

    print("Connection successful! Starting trading system...")
    time.sleep(2)

    # Run trading loop
    system.trading_loop()


if __name__ == "__main__":
    main()
