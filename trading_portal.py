#!/usr/bin/env python3
"""
Algorithmic Trading Portal for OKX
Based on MT5 Statistical Arbitrage System

A Flask-based web application for real-time monitoring and automated trading
of spot-futures basis spreads using mean-reversion strategy with Z-score signals.
"""

import os
import sys
import time
import json
import sqlite3
import threading
import uuid
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, Response
from dotenv import load_dotenv
from loguru import logger

# Load environment
load_dotenv()

# Configure logging
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>", level="INFO")
logger.add("logs/trading_portal_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days", level="DEBUG")

# ==================== DATABASE MANAGER ====================

class DatabaseManager:
    """SQLite database for persistent storage"""

    def __init__(self, db_path: str = "trading_portal.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_database()

    def init_database(self):
        """Initialize database tables"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Price history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    spot_price REAL,
                    futures_price REAL,
                    spread REAL,
                    swap_diff REAL
                )
            ''')

            # Trading configuration
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trading_config (
                    id INTEGER PRIMARY KEY,
                    asset_name TEXT DEFAULT 'BTC',
                    spot_symbol TEXT DEFAULT 'BTC-USDT',
                    futures_symbol TEXT DEFAULT '',
                    futures_expiry TEXT DEFAULT '',
                    contract_size REAL DEFAULT 1,
                    swap_charge REAL DEFAULT 0,
                    lookback_period INTEGER DEFAULT 90,
                    lookback_unit TEXT DEFAULT 'minutes',
                    entry_std_dev REAL DEFAULT 2.0,
                    exit_std_dev REAL DEFAULT 0.2,
                    stop_loss_std_dev REAL DEFAULT 6.0,
                    time_stop_loss_days REAL DEFAULT 0,
                    max_positions INTEGER DEFAULT 1,
                    lot_size REAL DEFAULT 0.01,
                    algo_enabled INTEGER DEFAULT 0,
                    paper_mode INTEGER DEFAULT 1,
                    commission_per_lot REAL DEFAULT 0,
                    hurst_threshold REAL DEFAULT 0.5,
                    trending_duration_minutes INTEGER DEFAULT 20,
                    hurst_enabled INTEGER DEFAULT 1,
                    close_before_overnight INTEGER DEFAULT 0,
                    overnight_close_hour INTEGER DEFAULT 16,
                    overnight_close_minute INTEGER DEFAULT 40,
                    min_profit_per_lot REAL DEFAULT 10,
                    max_loss_per_lot REAL DEFAULT 50,
                    updated_at TEXT
                )
            ''')

            # Trades table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    asset TEXT,
                    direction TEXT,
                    entry_date TEXT,
                    exit_date TEXT,
                    days_held INTEGER DEFAULT 0,
                    entry_zscore REAL,
                    exit_zscore REAL,
                    entry_spot_price REAL,
                    entry_futures_price REAL,
                    exit_spot_price REAL,
                    exit_futures_price REAL,
                    spot_pnl REAL DEFAULT 0,
                    futures_pnl REAL DEFAULT 0,
                    gross_pnl REAL DEFAULT 0,
                    swap_cost REAL DEFAULT 0,
                    commission REAL DEFAULT 0,
                    spread_cost REAL DEFAULT 0,
                    net_pnl REAL DEFAULT 0,
                    return_pct REAL DEFAULT 0,
                    lot_size REAL DEFAULT 0.01,
                    okx_spot_order_id TEXT,
                    okx_futures_order_id TEXT,
                    order_status TEXT DEFAULT 'PENDING',
                    status TEXT DEFAULT 'OPEN'
                )
            ''')

            # Insert default config if not exists
            cursor.execute('SELECT COUNT(*) FROM trading_config')
            if cursor.fetchone()[0] == 0:
                cursor.execute('INSERT INTO trading_config (id) VALUES (1)')

            conn.commit()
            conn.close()

    def get_config(self) -> Dict[str, Any]:
        """Get trading configuration"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT asset_name, spot_symbol, futures_symbol, futures_expiry, contract_size,
                       swap_charge, lookback_period, lookback_unit, entry_std_dev, exit_std_dev,
                       stop_loss_std_dev, time_stop_loss_days, max_positions, lot_size,
                       algo_enabled, paper_mode, commission_per_lot, hurst_threshold,
                       trending_duration_minutes, hurst_enabled, close_before_overnight,
                       overnight_close_hour, overnight_close_minute, min_profit_per_lot,
                       max_loss_per_lot
                FROM trading_config WHERE id = 1
            ''')
            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    'asset_name': row[0] or 'BTC',
                    'spot_symbol': row[1] or 'BTC-USDT',
                    'futures_symbol': row[2] or '',
                    'futures_expiry': row[3] or '',
                    'contract_size': row[4] or 1,
                    'swap_charge': row[5] or 0,
                    'lookback_period': row[6] or 90,
                    'lookback_unit': row[7] or 'minutes',
                    'entry_std_dev': row[8] or 2.0,
                    'exit_std_dev': row[9] or 0.2,
                    'stop_loss_std_dev': row[10] or 6.0,
                    'time_stop_loss_days': row[11] or 0,
                    'max_positions': row[12] or 1,
                    'lot_size': row[13] or 0.01,
                    'algo_enabled': bool(row[14]),
                    'paper_mode': bool(row[15]) if row[15] is not None else True,
                    'commission_per_lot': row[16] or 0,
                    'hurst_threshold': row[17] or 0.5,
                    'trending_duration_minutes': row[18] or 20,
                    'hurst_enabled': bool(row[19]) if row[19] is not None else True,
                    'close_before_overnight': bool(row[20]),
                    'overnight_close_hour': row[21] or 16,
                    'overnight_close_minute': row[22] or 40,
                    'min_profit_per_lot': row[23] or 10,
                    'max_loss_per_lot': row[24] or 50
                }
            return {}

    def save_config(self, config: Dict[str, Any]):
        """Save trading configuration"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trading_config SET
                    asset_name = ?, spot_symbol = ?, futures_symbol = ?, futures_expiry = ?,
                    contract_size = ?, swap_charge = ?, lookback_period = ?, lookback_unit = ?,
                    entry_std_dev = ?, exit_std_dev = ?, stop_loss_std_dev = ?, time_stop_loss_days = ?,
                    max_positions = ?, lot_size = ?, algo_enabled = ?, paper_mode = ?,
                    commission_per_lot = ?, hurst_threshold = ?, trending_duration_minutes = ?,
                    hurst_enabled = ?, close_before_overnight = ?, overnight_close_hour = ?,
                    overnight_close_minute = ?, min_profit_per_lot = ?, max_loss_per_lot = ?,
                    updated_at = ?
                WHERE id = 1
            ''', (
                config.get('asset_name', 'BTC'),
                config.get('spot_symbol', 'BTC-USDT'),
                config.get('futures_symbol', ''),
                config.get('futures_expiry', ''),
                config.get('contract_size', 1),
                config.get('swap_charge', 0),
                config.get('lookback_period', 90),
                config.get('lookback_unit', 'minutes'),
                config.get('entry_std_dev', 2.0),
                config.get('exit_std_dev', 0.2),
                config.get('stop_loss_std_dev', 6.0),
                config.get('time_stop_loss_days', 0),
                config.get('max_positions', 1),
                config.get('lot_size', 0.01),
                1 if config.get('algo_enabled') else 0,
                1 if config.get('paper_mode', True) else 0,
                config.get('commission_per_lot', 0),
                config.get('hurst_threshold', 0.5),
                config.get('trending_duration_minutes', 20),
                1 if config.get('hurst_enabled', True) else 0,
                1 if config.get('close_before_overnight') else 0,
                config.get('overnight_close_hour', 16),
                config.get('overnight_close_minute', 40),
                config.get('min_profit_per_lot', 10),
                config.get('max_loss_per_lot', 50),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
            conn.close()

    def save_price(self, asset: str, spot_price: float, futures_price: float, spread: float, swap_diff: float = 0):
        """Save price data point"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO price_history (timestamp, asset, spot_price, futures_price, spread, swap_diff)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (datetime.now(timezone.utc).isoformat(), asset, spot_price, futures_price, spread, swap_diff))
            conn.commit()
            conn.close()

    def get_price_history(self, asset: str, limit: int = 2000) -> List[Dict]:
        """Get price history"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT timestamp, spot_price, futures_price, spread, swap_diff
                FROM price_history
                WHERE asset = ?
                ORDER BY id DESC
                LIMIT ?
            ''', (asset, limit))
            rows = cursor.fetchall()
            conn.close()

            return [
                {'timestamp': r[0], 'spot_price': r[1], 'futures_price': r[2], 'spread': r[3], 'swap_diff': r[4]}
                for r in reversed(rows)
            ]

    def clear_price_history(self, asset: str = None):
        """Clear price history"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            if asset:
                cursor.execute('DELETE FROM price_history WHERE asset = ?', (asset,))
            else:
                cursor.execute('DELETE FROM price_history')
            conn.commit()
            conn.close()

    def save_trade(self, trade: Dict):
        """Save or update trade"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO trades (
                    trade_id, asset, direction, entry_date, exit_date, days_held,
                    entry_zscore, exit_zscore, entry_spot_price, entry_futures_price,
                    exit_spot_price, exit_futures_price, spot_pnl, futures_pnl,
                    gross_pnl, swap_cost, commission, spread_cost, net_pnl,
                    return_pct, lot_size, okx_spot_order_id, okx_futures_order_id,
                    order_status, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade.get('trade_id'),
                trade.get('asset'),
                trade.get('direction'),
                trade.get('entry_date'),
                trade.get('exit_date'),
                trade.get('days_held', 0),
                trade.get('entry_zscore'),
                trade.get('exit_zscore'),
                trade.get('entry_spot_price'),
                trade.get('entry_futures_price'),
                trade.get('exit_spot_price'),
                trade.get('exit_futures_price'),
                trade.get('spot_pnl', 0),
                trade.get('futures_pnl', 0),
                trade.get('gross_pnl', 0),
                trade.get('swap_cost', 0),
                trade.get('commission', 0),
                trade.get('spread_cost', 0),
                trade.get('net_pnl', 0),
                trade.get('return_pct', 0),
                trade.get('lot_size', 0.01),
                trade.get('okx_spot_order_id'),
                trade.get('okx_futures_order_id'),
                trade.get('order_status', 'PENDING'),
                trade.get('status', 'OPEN')
            ))
            conn.commit()
            conn.close()

    def get_trades(self, limit: int = 500, status: str = None) -> List[Dict]:
        """Get trades"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            if status:
                cursor.execute('''
                    SELECT trade_id, asset, direction, entry_date, exit_date, days_held,
                           entry_zscore, exit_zscore, entry_spot_price, entry_futures_price,
                           exit_spot_price, exit_futures_price, spot_pnl, futures_pnl,
                           gross_pnl, swap_cost, commission, spread_cost, net_pnl,
                           return_pct, lot_size, okx_spot_order_id, okx_futures_order_id,
                           order_status, status
                    FROM trades WHERE status = ? ORDER BY entry_date DESC LIMIT ?
                ''', (status, limit))
            else:
                cursor.execute('''
                    SELECT trade_id, asset, direction, entry_date, exit_date, days_held,
                           entry_zscore, exit_zscore, entry_spot_price, entry_futures_price,
                           exit_spot_price, exit_futures_price, spot_pnl, futures_pnl,
                           gross_pnl, swap_cost, commission, spread_cost, net_pnl,
                           return_pct, lot_size, okx_spot_order_id, okx_futures_order_id,
                           order_status, status
                    FROM trades ORDER BY entry_date DESC LIMIT ?
                ''', (limit,))

            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    'trade_id': r[0], 'asset': r[1], 'direction': r[2], 'entry_date': r[3],
                    'exit_date': r[4], 'days_held': r[5], 'entry_zscore': r[6], 'exit_zscore': r[7],
                    'entry_spot_price': r[8], 'entry_futures_price': r[9], 'exit_spot_price': r[10],
                    'exit_futures_price': r[11], 'spot_pnl': r[12], 'futures_pnl': r[13],
                    'gross_pnl': r[14], 'swap_cost': r[15], 'commission': r[16], 'spread_cost': r[17],
                    'net_pnl': r[18], 'return_pct': r[19], 'lot_size': r[20],
                    'okx_spot_order_id': r[21], 'okx_futures_order_id': r[22],
                    'order_status': r[23], 'status': r[24]
                }
                for r in rows
            ]

    def get_trade_summary(self) -> Dict:
        """Get trade summary statistics"""
        trades = self.get_trades(status='CLOSED')

        if not trades:
            return {
                'total_pnl': 0, 'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0, 'sharpe_ratio': 0, 'max_drawdown': 0, 'current_drawdown': 0,
                'cumulative_return': 0
            }

        pnls = [t['net_pnl'] for t in trades if t['net_pnl'] is not None]

        total_pnl = sum(pnls)
        winning = len([p for p in pnls if p > 0])
        losing = len([p for p in pnls if p <= 0])
        win_rate = (winning / len(pnls) * 100) if pnls else 0

        # Sharpe ratio
        if len(pnls) > 1 and np.std(pnls) > 0:
            sharpe = np.mean(pnls) / np.std(pnls)
        else:
            sharpe = 0

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0
        current_drawdown = drawdown[-1] if len(drawdown) > 0 else 0

        return {
            'total_pnl': total_pnl,
            'total_trades': len(trades),
            'winning_trades': winning,
            'losing_trades': losing,
            'win_rate': win_rate,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'current_drawdown': current_drawdown,
            'cumulative_return': total_pnl
        }

    def clear_trades(self):
        """Clear all trades"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trades')
            conn.commit()
            conn.close()


# ==================== TRADING MONITOR ====================

class TradingMonitor:
    """Core trading logic and OKX integration"""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.client = None
        self.config = {}

        # Data caches
        self.spread_cache = deque(maxlen=2000)
        self.zscore_history = deque(maxlen=200)
        self.price_history = deque(maxlen=200)

        # Positions
        self.positions: Dict[str, Dict] = {}

        # State
        self.running = False
        self.initialized = False
        self.last_save_time = 0
        self.consecutive_stops = 0
        self.hurst_block_until = None

        # Market data
        self.current_data = {}

        self._thread = None
        self._lock = threading.Lock()

    def initialize_okx(self) -> bool:
        """Initialize OKX connection"""
        try:
            from app.api.okx_client import OKXClient

            self.config = self.db.get_config()

            self.client = OKXClient(
                api_key=os.environ.get('OKX_API_KEY', ''),
                secret_key=os.environ.get('OKX_SECRET_KEY', ''),
                passphrase=os.environ.get('OKX_PASSPHRASE', ''),
                demo_trading=self.config.get('paper_mode', True)
            )

            # Test connection
            ticker = self.client.get_ticker(self.config.get('spot_symbol', 'BTC-USDT'))
            if ticker:
                logger.info(f"OKX connected. {self.config.get('spot_symbol')} price: ${ticker.last_price:,.2f}")
                self.initialized = True

                # Load open positions
                self._load_open_positions()

                # Load historical spreads
                self._load_spread_history()

                return True
            else:
                logger.error("Failed to get ticker data")
                return False

        except Exception as e:
            logger.error(f"OKX initialization failed: {e}")
            return False

    def _load_open_positions(self):
        """Load open positions from database"""
        trades = self.db.get_trades(status='OPEN')
        for trade in trades:
            self.positions[trade['asset']] = trade
            logger.info(f"Loaded open position: {trade['trade_id']} ({trade['direction']})")

    def _load_spread_history(self):
        """Load historical spread data"""
        history = self.db.get_price_history(self.config.get('asset_name', 'BTC'), limit=2000)
        for h in history:
            if h['spread'] is not None:
                self.spread_cache.append(h['spread'])
        logger.info(f"Loaded {len(self.spread_cache)} historical spread points")

    def start_background_updates(self):
        """Start background update thread"""
        if self._thread and self._thread.is_alive():
            return

        self.running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()
        logger.info("Background update thread started")

    def stop(self):
        """Stop background thread"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _background_loop(self):
        """Background update loop"""
        save_interval = 60  # Save every 60 seconds for minutes mode

        while self.running:
            try:
                # Get market data
                data = self.get_market_data()

                if data and data.get('spot_price'):
                    with self._lock:
                        self.current_data = data

                    # Update caches
                    spread = data.get('spread', 0)
                    self.spread_cache.append(spread)

                    # Update chart history
                    now = datetime.now(timezone.utc)
                    self.price_history.append({
                        'time': now.strftime('%H:%M:%S'),
                        'spot_price': data.get('spot_price', 0),
                        'futures_price': data.get('futures_price', 0),
                        'spread': spread
                    })

                    if data.get('zscore') is not None:
                        stats = data.get('stats', {})
                        # Store Z-score thresholds (constant values), not spread values
                        entry_std = self.config.get('entry_std_dev', 2.0)
                        exit_std = self.config.get('exit_std_dev', 0.2)
                        self.zscore_history.append({
                            'time': now.strftime('%H:%M:%S'),
                            'zscore': data['zscore'],
                            'entry_upper': entry_std,      # e.g., +2
                            'entry_lower': -entry_std,     # e.g., -2
                            'exit_upper': exit_std,        # e.g., +0.2
                            'exit_lower': -exit_std        # e.g., -0.2
                        })

                    # Save to database periodically
                    current_time = time.time()
                    if current_time - self.last_save_time >= save_interval:
                        self.db.save_price(
                            self.config.get('asset_name', 'BTC'),
                            data.get('spot_price', 0),
                            data.get('futures_price', 0),
                            spread,
                            data.get('swap_diff', 0)
                        )
                        self.last_save_time = current_time

                    # Process algo trading
                    if self.config.get('algo_enabled'):
                        self._process_algo_trading(data)

                time.sleep(0.3)  # 300ms update interval

            except Exception as e:
                logger.error(f"Background loop error: {e}")
                time.sleep(1)

    def get_market_data(self) -> Dict:
        """Get current market data"""
        if not self.client:
            return {}

        try:
            asset_name = self.config.get('asset_name', 'BTC')
            spot_symbol = self.config.get('spot_symbol', 'BTC-USDT')
            futures_symbol = self.config.get('futures_symbol', '')

            # Get spot data
            spot_ticker = self.client.get_ticker(spot_symbol)
            if not spot_ticker:
                return {}

            spot_price = spot_ticker.last_price
            spot_bid = spot_ticker.bid_price
            spot_ask = spot_ticker.ask_price
            spot_spread = spot_ask - spot_bid

            # Get futures data
            futures_price = 0
            futures_bid = 0
            futures_ask = 0
            futures_spread = 0
            days_to_expiry = 0

            if futures_symbol:
                futures_ticker = self.client.get_ticker(futures_symbol)
                if futures_ticker:
                    futures_price = futures_ticker.last_price
                    futures_bid = futures_ticker.bid_price
                    futures_ask = futures_ticker.ask_price
                    futures_spread = futures_ask - futures_bid
            else:
                # Auto-select most liquid futures
                futures_instruments = self.client.get_instruments('FUTURES', 'BTC-USDT')
                if futures_instruments:
                    now = datetime.now(timezone.utc)
                    best_futures = None
                    best_volume = 0

                    for inst in futures_instruments:
                        if inst.expiry_time:
                            days_left = (inst.expiry_time - now).days
                            if days_left >= 3:
                                ticker = self.client.get_ticker(inst.inst_id)
                                if ticker and ticker.volume_24h and ticker.volume_24h > best_volume:
                                    best_volume = ticker.volume_24h
                                    best_futures = (inst, ticker)

                    if best_futures:
                        inst, ticker = best_futures
                        futures_symbol = inst.inst_id
                        futures_price = ticker.last_price
                        futures_bid = ticker.bid_price
                        futures_ask = ticker.ask_price
                        futures_spread = futures_ask - futures_bid
                        days_to_expiry = (inst.expiry_time - now).days

                        # Update config with selected futures
                        self.config['futures_symbol'] = futures_symbol
                        self.config['futures_expiry'] = inst.expiry_time.strftime('%Y-%m-%d')

            # Calculate basis
            spread = futures_price - spot_price if futures_price else 0
            spread_pct = (spread / spot_price * 100) if spot_price else 0

            # Calculate statistics
            zscore, stats = self.calculate_zscore(spread)
            hurst_value, hurst_regime = self.calculate_hurst()

            # Generate signal
            signal = self._generate_signal(zscore, stats, hurst_value, hurst_regime, spot_price, futures_price)

            # Market session
            session = self._get_market_session()

            # Sentiment
            if stats.get('complete') and zscore is not None:
                if zscore >= self.config.get('entry_std_dev', 2.0):
                    sentiment = 'EXPENSIVE'
                elif zscore <= -self.config.get('entry_std_dev', 2.0):
                    sentiment = 'CHEAP'
                else:
                    sentiment = 'FAIR'
            else:
                sentiment = 'N/A'

            return {
                'asset': asset_name,
                'spot_symbol': spot_symbol,
                'futures_symbol': futures_symbol,
                'spot_price': spot_price,
                'spot_bid': spot_bid,
                'spot_ask': spot_ask,
                'spot_spread': spot_spread,
                'futures_price': futures_price,
                'futures_bid': futures_bid,
                'futures_ask': futures_ask,
                'futures_spread': futures_spread,
                'spread': spread,
                'spread_pct': spread_pct,
                'days_to_expiry': days_to_expiry,
                'zscore': zscore,
                'stats': stats,
                'hurst': hurst_value,
                'hurst_regime': hurst_regime,
                'signal': signal,
                'session': session,
                'sentiment': sentiment,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"Error getting market data: {e}")
            return {}

    def calculate_zscore(self, current_spread: float) -> Tuple[Optional[float], Dict]:
        """Calculate Z-score"""
        lookback = self.config.get('lookback_period', 90)
        unit = self.config.get('lookback_unit', 'minutes')

        # Required points based on unit
        if unit == 'days':
            required = lookback * 24  # hourly points for days
        else:
            required = lookback  # minute points

        spreads = list(self.spread_cache)
        count = len(spreads)

        stats = {
            'mean': 0,
            'std': 0,
            'count': count,
            'required': required,
            'complete': count >= required,
            'upper_entry': 0,
            'lower_entry': 0,
            'upper_exit': 0,
            'lower_exit': 0,
            'upper_stop': 0,
            'lower_stop': 0
        }

        if count < 20:  # Minimum points for any calculation
            return None, stats

        # Calculate mean and std for display purposes
        mean = np.mean(spreads)
        std = np.std(spreads)

        stats['mean'] = mean
        stats['std'] = std

        if std > 0:
            entry_std = self.config.get('entry_std_dev', 2.0)
            exit_std = self.config.get('exit_std_dev', 0.2)
            stop_std = self.config.get('stop_loss_std_dev', 6.0)

            stats['upper_entry'] = mean + (entry_std * std)
            stats['lower_entry'] = mean - (entry_std * std)
            stats['upper_exit'] = mean + (exit_std * std)
            stats['lower_exit'] = mean - (exit_std * std)
            stats['upper_stop'] = mean + (stop_std * std)
            stats['lower_stop'] = mean - (stop_std * std)

            # ONLY return Z-score if we have enough data for reliable statistics
            if count >= required:
                zscore = (current_spread - mean) / std
                return zscore, stats
            else:
                # Not enough data yet - don't return z-score
                return None, stats

        return None, stats

    def calculate_hurst(self, min_points: int = 20) -> Tuple[Optional[float], str]:
        """Calculate Hurst exponent using R/S method"""
        spreads = list(self.spread_cache)

        if len(spreads) < min_points:
            return None, 'UNKNOWN'

        # Use last 100 points
        data = np.array(spreads[-100:])

        if len(data) < min_points:
            return None, 'UNKNOWN'

        try:
            # R/S Analysis
            n = len(data)
            max_k = n // 2
            min_k = 4

            if max_k <= min_k:
                return None, 'UNKNOWN'

            rs_list = []
            n_list = []

            for k in range(min_k, max_k + 1):
                num_chunks = n // k
                if num_chunks < 1:
                    continue

                rs_values = []
                for i in range(num_chunks):
                    chunk = data[i * k:(i + 1) * k]
                    if len(chunk) < 2:
                        continue

                    mean = np.mean(chunk)
                    deviations = chunk - mean
                    cumsum = np.cumsum(deviations)
                    R = np.max(cumsum) - np.min(cumsum)
                    S = np.std(chunk)

                    if S > 0:
                        rs_values.append(R / S)

                if rs_values:
                    rs_list.append(np.mean(rs_values))
                    n_list.append(k)

            if len(rs_list) < 3:
                return None, 'UNKNOWN'

            # Linear regression on log-log scale
            log_n = np.log(n_list)
            log_rs = np.log(rs_list)

            slope, _ = np.polyfit(log_n, log_rs, 1)
            hurst = max(0, min(1, slope))  # Clamp to [0, 1]

            # Determine regime
            if hurst < 0.4:
                regime = 'MEAN_REVERTING'
            elif hurst < 0.6:
                regime = 'RANDOM_WALK'
            else:
                regime = 'TRENDING'

            return hurst, regime

        except Exception as e:
            logger.error(f"Hurst calculation error: {e}")
            return None, 'UNKNOWN'

    def _generate_signal(self, zscore: Optional[float], stats: Dict, hurst: Optional[float],
                        hurst_regime: str, spot_price: float, futures_price: float) -> Dict:
        """Generate trading signal"""
        signal = {'type': 'NO_SIGNAL', 'reason': '', 'action': ''}

        if zscore is None:
            signal['reason'] = f"Collecting data: {stats['count']}/{stats['required']}"
            return signal

        asset_name = self.config.get('asset_name', 'BTC')
        entry_std = self.config.get('entry_std_dev', 2.0)
        exit_std = self.config.get('exit_std_dev', 0.2)
        stop_std = self.config.get('stop_loss_std_dev', 6.0)
        hurst_enabled = self.config.get('hurst_enabled', True)
        hurst_threshold = self.config.get('hurst_threshold', 0.5)

        # Check for existing position
        position = self.positions.get(asset_name)

        if position:
            # Exit signals
            direction = position['direction']

            # Time stop
            if self.config.get('time_stop_loss_days', 0) > 0:
                entry_date = datetime.fromisoformat(position['entry_date'])
                days_held = (datetime.now(timezone.utc) - entry_date).days
                if days_held >= self.config['time_stop_loss_days']:
                    signal = {'type': 'TIME_STOP', 'reason': f'Max holding period ({days_held} days)', 'action': 'CLOSE'}
                    return signal

            # Overnight close
            if self.config.get('close_before_overnight'):
                now = datetime.now(timezone.utc)
                close_hour = self.config.get('overnight_close_hour', 16)
                close_minute = self.config.get('overnight_close_minute', 40)
                if now.hour == close_hour and now.minute >= close_minute:
                    signal = {'type': 'OVERNIGHT_CLOSE', 'reason': f'Close before overnight (time: {now.hour}:{now.minute:02d} >= {close_hour}:{close_minute:02d})', 'action': 'CLOSE'}
                    return signal

            # Max loss
            if self.config.get('max_loss_per_lot', 0) > 0:
                unrealized_pnl = self._calculate_unrealized_pnl(position, spot_price, futures_price)
                max_loss = self.config['max_loss_per_lot'] * position.get('lot_size', 0.01)
                if unrealized_pnl < -max_loss:
                    signal = {'type': 'MAX_LOSS', 'reason': f'Max loss reached (${unrealized_pnl:.2f})', 'action': 'CLOSE'}
                    return signal

            # Stop loss
            if direction == 'Short Spread' and zscore >= stop_std:
                signal = {'type': 'STOP_LOSS', 'reason': f'Z-score {zscore:.2f} >= {stop_std}', 'action': 'CLOSE'}
            elif direction == 'Long Spread' and zscore <= -stop_std:
                signal = {'type': 'STOP_LOSS', 'reason': f'Z-score {zscore:.2f} <= {-stop_std}', 'action': 'CLOSE'}

            # Exit at mean reversion
            elif direction == 'Short Spread' and zscore <= exit_std:
                signal = {'type': 'CLOSE', 'reason': f'Mean reversion (z={zscore:.2f})', 'action': 'CLOSE'}
            elif direction == 'Long Spread' and zscore >= -exit_std:
                signal = {'type': 'CLOSE', 'reason': f'Mean reversion (z={zscore:.2f})', 'action': 'CLOSE'}

        else:
            # Entry signals

            # Check Hurst filter
            if hurst_enabled and hurst is not None and hurst >= hurst_threshold:
                if self.hurst_block_until and datetime.now(timezone.utc) < self.hurst_block_until:
                    signal['reason'] = f'Hurst filter active ({hurst:.3f} >= {hurst_threshold})'
                    return signal
                elif hurst_regime == 'TRENDING':
                    # Start new block period
                    duration = self.config.get('trending_duration_minutes', 20)
                    self.hurst_block_until = datetime.now(timezone.utc) + timedelta(minutes=duration)
                    signal['reason'] = f'Trending market blocked for {duration}min (H={hurst:.3f})'
                    return signal

            # Entry signals
            if zscore >= entry_std:
                signal = {
                    'type': 'SELL_BASIS',
                    'reason': f'Z-score {zscore:.2f} >= {entry_std} (expensive)',
                    'action': 'Short Spread'
                }
            elif zscore <= -entry_std:
                signal = {
                    'type': 'BUY_BASIS',
                    'reason': f'Z-score {zscore:.2f} <= {-entry_std} (cheap)',
                    'action': 'Long Spread'
                }

        return signal

    def _calculate_unrealized_pnl(self, position: Dict, current_spot: float, current_futures: float) -> float:
        """Calculate unrealized P&L for a position"""
        entry_spot = position.get('entry_spot_price', 0)
        entry_futures = position.get('entry_futures_price', 0)
        lot_size = position.get('lot_size', 0.01)
        contract_size = self.config.get('contract_size', 1)
        direction = position.get('direction', '')

        spot_diff = current_spot - entry_spot
        futures_diff = current_futures - entry_futures

        if direction == 'Short Spread':
            # Long spot, Short futures
            spot_pnl = spot_diff * lot_size * contract_size
            futures_pnl = -futures_diff * lot_size * contract_size
        else:  # Long Spread
            # Short spot, Long futures
            spot_pnl = -spot_diff * lot_size * contract_size
            futures_pnl = futures_diff * lot_size * contract_size

        return spot_pnl + futures_pnl

    def _process_algo_trading(self, data: Dict):
        """Process algorithmic trading signals"""
        signal = data.get('signal', {})
        signal_type = signal.get('type', 'NO_SIGNAL')
        asset_name = self.config.get('asset_name', 'BTC')

        if signal_type in ['SELL_BASIS', 'BUY_BASIS']:
            if asset_name not in self.positions:
                if len(self.positions) < self.config.get('max_positions', 1):
                    self._open_position(signal, data)

        elif signal_type in ['CLOSE', 'STOP_LOSS', 'TIME_STOP', 'OVERNIGHT_CLOSE', 'MAX_LOSS']:
            if asset_name in self.positions:
                self._close_position(signal, data)

    def _open_position(self, signal: Dict, data: Dict):
        """Open a new position"""
        asset_name = self.config.get('asset_name', 'BTC')
        direction = signal.get('action', '')
        lot_size = self.config.get('lot_size', 0.01)

        trade_id = str(uuid.uuid4())[:8]

        # Calculate spread cost
        spot_spread_cost = data.get('spot_spread', 0) * lot_size * self.config.get('contract_size', 1)
        futures_spread_cost = data.get('futures_spread', 0) * lot_size * self.config.get('contract_size', 1)
        total_spread_cost = spot_spread_cost + futures_spread_cost

        trade = {
            'trade_id': trade_id,
            'asset': asset_name,
            'direction': direction,
            'entry_date': datetime.now(timezone.utc).isoformat(),
            'entry_zscore': data.get('zscore'),
            'entry_spot_price': data.get('spot_price'),
            'entry_futures_price': data.get('futures_price'),
            'lot_size': lot_size,
            'spread_cost': total_spread_cost,
            'status': 'OPEN',
            'order_status': 'FILLED' if self.config.get('paper_mode') else 'PENDING'
        }

        if not self.config.get('paper_mode'):
            # Execute real orders on OKX
            success = self._execute_okx_orders(trade, 'OPEN')
            if not success:
                logger.error("Failed to execute OKX orders")
                return

        self.positions[asset_name] = trade
        self.db.save_trade(trade)

        logger.info(f"Position opened: {trade_id} ({direction}) @ spot={data.get('spot_price'):.2f}, futures={data.get('futures_price'):.2f}")

    def _close_position(self, signal: Dict, data: Dict):
        """Close an existing position"""
        asset_name = self.config.get('asset_name', 'BTC')
        position = self.positions.get(asset_name)

        if not position:
            return

        # Calculate P&L
        entry_spot = position.get('entry_spot_price', 0)
        entry_futures = position.get('entry_futures_price', 0)
        exit_spot = data.get('spot_price', 0)
        exit_futures = data.get('futures_price', 0)
        lot_size = position.get('lot_size', 0.01)
        contract_size = self.config.get('contract_size', 1)
        direction = position.get('direction', '')

        spot_diff = exit_spot - entry_spot
        futures_diff = exit_futures - entry_futures

        if direction == 'Short Spread':
            spot_pnl = spot_diff * lot_size * contract_size
            futures_pnl = -futures_diff * lot_size * contract_size
        else:
            spot_pnl = -spot_diff * lot_size * contract_size
            futures_pnl = futures_diff * lot_size * contract_size

        gross_pnl = spot_pnl + futures_pnl
        commission = self.config.get('commission_per_lot', 0) * lot_size * 4  # 4 trades total
        spread_cost = position.get('spread_cost', 0) * 2  # Round trip
        net_pnl = gross_pnl - commission - spread_cost

        # Calculate days held
        entry_date = datetime.fromisoformat(position['entry_date'])
        days_held = (datetime.now(timezone.utc) - entry_date).days

        # Update trade
        position['exit_date'] = datetime.now(timezone.utc).isoformat()
        position['exit_zscore'] = data.get('zscore')
        position['exit_spot_price'] = exit_spot
        position['exit_futures_price'] = exit_futures
        position['spot_pnl'] = spot_pnl
        position['futures_pnl'] = futures_pnl
        position['gross_pnl'] = gross_pnl
        position['commission'] = commission
        position['net_pnl'] = net_pnl
        position['days_held'] = days_held
        position['status'] = 'CLOSED'

        if not self.config.get('paper_mode'):
            # Execute close orders on OKX
            self._execute_okx_orders(position, 'CLOSE')

        self.db.save_trade(position)
        del self.positions[asset_name]

        # Track consecutive stops
        if signal.get('type') == 'STOP_LOSS':
            self.consecutive_stops += 1
            if self.consecutive_stops >= 3:
                # Auto-protection
                self.config['hurst_enabled'] = True
                self.config['hurst_threshold'] = 0.5
                self.config['trending_duration_minutes'] = 20
                self.config['lot_size'] = 0.01
                self.db.save_config(self.config)
                logger.warning("Auto-protection activated after 3 consecutive stops")
        else:
            self.consecutive_stops = 0

        logger.info(f"Position closed: {position['trade_id']} ({signal.get('type')}) P&L: ${net_pnl:.2f}")

    def _execute_okx_orders(self, trade: Dict, action: str) -> bool:
        """Execute orders on OKX"""
        # TODO: Implement real OKX order execution
        # For now, simulate in paper mode
        return True

    def _get_market_session(self) -> str:
        """Get current market session"""
        now = datetime.now(timezone.utc)
        hour = now.hour

        if 0 <= hour < 7:
            return "Asia/Sydney"
        elif 7 <= hour < 8:
            return "London Pre"
        elif 8 <= hour < 13:
            return "London"
        elif 13 <= hour < 14:
            return "US Pre"
        elif 14 <= hour < 21:
            return "US New York"
        else:
            return "After Hours"

    def get_account_info(self) -> Dict:
        """Get OKX account info"""
        if not self.client:
            return {'server': 'Not Connected', 'error': 'Client not initialized'}

        # Check if credentials are configured
        api_key = os.environ.get('OKX_API_KEY', '')
        if not api_key or api_key == 'your_api_key_here':
            return {
                'server': 'OKX Demo' if self.config.get('paper_mode') else 'OKX Live',
                'balance': 0,
                'equity': 0,
                'margin': 0,
                'free_margin': 0,
                'leverage': 1,
                'error': 'API credentials not configured. Create .env file from .env.example'
            }

        try:
            balance_response = self.client.get_balance('USDT')

            if balance_response.get('code') == '0' and balance_response.get('data'):
                for bal in balance_response['data']:
                    details = bal.get('details', [])
                    for detail in details:
                        if detail.get('ccy') == 'USDT':
                            return {
                                'balance': float(detail.get('cashBal', 0)),
                                'equity': float(detail.get('eq', 0)),
                                'margin': float(detail.get('frozenBal', 0)),
                                'free_margin': float(detail.get('availBal', 0)),
                                'leverage': 1,  # OKX reports per-position
                                'server': 'OKX Demo' if self.config.get('paper_mode') else 'OKX Live'
                            }
            # API returned error - check code
            error_code = balance_response.get('code', '')
            if error_code == '50111':
                return {'server': 'Auth Error', 'error': 'Invalid API key or signature'}
            elif error_code == '50113':
                return {'server': 'Auth Error', 'error': 'Invalid passphrase'}
            return {'server': 'API Error', 'error': f"Code: {error_code}"}
        except Exception as e:
            # Only log occasionally to avoid spam
            if not hasattr(self, '_last_account_error_time') or time.time() - self._last_account_error_time > 60:
                logger.warning(f"Account info error: {e}")
                self._last_account_error_time = time.time()
            return {'server': 'Error', 'error': str(e)[:50]}

    def get_okx_positions(self) -> List[Dict]:
        """Get OKX positions"""
        if not self.client:
            return []

        # Check if credentials are configured
        api_key = os.environ.get('OKX_API_KEY', '')
        if not api_key or api_key == 'your_api_key_here':
            return []  # No positions without credentials

        try:
            positions_response = self.client.get_positions()

            positions = []
            if positions_response.get('code') == '0' and positions_response.get('data'):
                for pos in positions_response['data']:
                    if float(pos.get('pos', 0)) != 0:
                        positions.append({
                            'symbol': pos.get('instId'),
                            'type': 'BUY' if float(pos.get('pos', 0)) > 0 else 'SELL',
                            'volume': abs(float(pos.get('pos', 0))),
                            'open_price': float(pos.get('avgPx', 0)),
                            'current_price': float(pos.get('markPx', 0)),
                            'pnl': float(pos.get('upl', 0)),
                            'time': pos.get('cTime', '')
                        })
            return positions
        except Exception as e:
            # Only log occasionally to avoid spam
            if not hasattr(self, '_last_pos_error_time') or time.time() - self._last_pos_error_time > 60:
                logger.warning(f"Positions error: {e}")
                self._last_pos_error_time = time.time()
            return []

    def get_enriched_positions(self) -> List[Dict]:
        """Get positions with enriched data"""
        enriched = []

        for asset, pos in self.positions.items():
            data = self.current_data
            if data:
                unrealized_pnl = self._calculate_unrealized_pnl(pos, data.get('spot_price', 0), data.get('futures_price', 0))
                pos['unrealized_pnl'] = unrealized_pnl
                pos['current_spot'] = data.get('spot_price', 0)
                pos['current_futures'] = data.get('futures_price', 0)
                pos['current_spread'] = data.get('spread', 0)
            enriched.append(pos)

        return enriched

    def manual_close_position(self, asset_name: str) -> bool:
        """Manually close a position"""
        if asset_name not in self.positions:
            return False

        signal = {'type': 'MANUAL', 'reason': 'Manual close', 'action': 'CLOSE'}
        self._close_position(signal, self.current_data)
        return True


# ==================== FLASK APP ====================

app = Flask(__name__)
db = DatabaseManager()
monitor = TradingMonitor(db)


# ==================== HTML TEMPLATES ====================

MONITOR_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Algorithmic Trading Portal</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; }
        .container { max-width: 1400px; margin: 0 auto; padding: 15px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; background: white; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .header h1 { font-size: 1.5rem; font-weight: 600; }
        .header-time { font-size: 1.2rem; color: #666; }
        .controls { display: flex; gap: 15px; align-items: center; padding: 15px 20px; background: white; border-radius: 8px; margin-bottom: 15px; flex-wrap: wrap; }
        .toggle-group { display: flex; align-items: center; gap: 8px; }
        .toggle-label { font-weight: 500; }
        .toggle { position: relative; width: 50px; height: 26px; }
        .toggle input { opacity: 0; width: 0; height: 0; }
        .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #ccc; border-radius: 26px; transition: 0.3s; }
        .toggle-slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.3s; }
        input:checked + .toggle-slider { background: #e74c3c; }
        input:checked + .toggle-slider:before { transform: translateX(24px); }
        .toggle-status { font-weight: 600; padding: 3px 10px; border-radius: 4px; font-size: 0.85rem; }
        .status-on { background: #e74c3c; color: white; }
        .status-off { background: #eee; color: #666; }
        .mode-badge { padding: 5px 12px; border-radius: 4px; font-weight: 600; font-size: 0.85rem; }
        .mode-paper { background: #f0f0f0; color: #666; border: 1px solid #ddd; }
        .mode-live { background: #e74c3c; color: white; }
        .thresholds { color: #666; font-size: 0.9rem; }
        .session { font-weight: 500; }
        .session-icon { margin-right: 5px; }
        .btn { padding: 8px 16px; border: 1px solid #ddd; background: white; border-radius: 6px; cursor: pointer; font-size: 0.9rem; transition: all 0.2s; }
        .btn:hover { background: #f5f5f5; }
        .btn-danger { color: #e74c3c; border-color: #e74c3c; }
        .btn-danger:hover { background: #fdf2f2; }
        .card { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 15px; overflow: hidden; }
        .card-header { padding: 12px 15px; border-bottom: 1px solid #eee; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
        .card-body { padding: 15px; }
        .row { display: flex; gap: 15px; flex-wrap: wrap; }
        .col { flex: 1; min-width: 200px; }
        .col-2 { flex: 2; }
        .col-3 { flex: 3; }
        .account-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 20px; text-align: center; }
        .account-item label { display: block; color: #888; font-size: 0.8rem; margin-bottom: 5px; text-transform: uppercase; }
        .account-item .value { font-size: 1.3rem; font-weight: 600; }
        .account-item .value.negative { color: #e74c3c; }
        .account-item .value.positive { color: #27ae60; }
        .chart-container { height: 300px; position: relative; width: 100%; }
        .asset-panel { padding: 15px; }
        .asset-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid #333; }
        .asset-name { font-size: 24px; font-weight: 700; }
        .sentiment { padding: 5px 15px; border-radius: 4px; font-weight: 600; font-size: 16px; }
        .sentiment-cheap { background: #d4edda; color: #155724; }
        .sentiment-expensive { background: #f8d7da; color: #721c24; }
        .sentiment-fair { background: #e2e3e5; color: #383d41; }
        .price-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 15px; }
        .price-item { padding: 10px; background: #f8f9fa; border-radius: 6px; }
        .price-item label { display: block; color: #888; font-size: 12px; text-transform: uppercase; margin-bottom: 3px; }
        .price-item .value { font-size: 20px; font-weight: 600; }
        .basis-section { background: #f8f9fa; border-radius: 6px; padding: 15px; margin-bottom: 15px; }
        .basis-header { display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 16px; }
        .basis-value { font-size: 24px; font-weight: 700; }
        .zscore-display { text-align: center; padding: 20px; background: linear-gradient(135deg, #fff5f5, #fff); border: 2px solid #e74c3c; border-radius: 8px; margin-bottom: 15px; }
        .zscore-value { font-size: 48px !important; font-weight: 700; }
        .account-item .value { font-size: 20px; font-weight: 600; }
        .stat-item .value { font-size: 20px; font-weight: 600; }
        .entry-value { font-size: 20px; font-weight: 600; }
        .info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 15px; }
        .info-card { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 15px; }
        .zscore-note { color: #888; font-size: 14px; margin-top: 5px; }
        .top-info-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 15px; }
        .hurst-badge { display: inline-block; padding: 5px 15px; border-radius: 20px; font-weight: 600; margin-top: 10px; }
        .hurst-mean { background: #d4edda; color: #155724; }
        .hurst-trending { background: #f8d7da; color: #721c24; }
        .hurst-random { background: #fff3cd; color: #856404; }
        .stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 15px; }
        .stat-item { text-align: center; padding: 10px; background: #f8f9fa; border-radius: 6px; }
        .stat-item label { display: block; color: #888; font-size: 0.75rem; text-transform: uppercase; }
        .stat-item .value { font-size: 1.3rem; font-weight: 600; }
        .entry-levels { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .entry-box { padding: 15px; border-radius: 6px; }
        .entry-short { background: #ffeaea; border-left: 4px solid #e74c3c; }
        .entry-long { background: #eafff0; border-left: 4px solid #27ae60; }
        .entry-title { font-weight: 600; margin-bottom: 10px; }
        .entry-short .entry-title { color: #e74c3c; }
        .entry-long .entry-title { color: #27ae60; }
        .entry-row { display: flex; justify-content: space-between; margin-bottom: 5px; }
        .entry-label { color: #666; }
        .entry-value { font-weight: 600; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; color: #666; }
        .type-buy { color: #27ae60; font-weight: 600; }
        .type-sell { color: #e74c3c; font-weight: 600; }
        .pnl-positive { color: #27ae60; font-weight: 600; }
        .pnl-negative { color: #e74c3c; font-weight: 600; }
        @media (max-width: 768px) {
            .account-grid { grid-template-columns: repeat(3, 1fr); }
            .price-grid { grid-template-columns: repeat(2, 1fr); }
            .stats-grid { grid-template-columns: 1fr; }
            .entry-levels { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Algorithmic Trading Portal</h1>
            <span class="header-time" id="current-time">--:--:--</span>
        </div>

        <div class="controls">
            <div class="toggle-group">
                <span class="toggle-label">Algo Trading:</span>
                <label class="toggle">
                    <input type="checkbox" id="algo-toggle" onchange="toggleAlgo(this.checked)">
                    <span class="toggle-slider"></span>
                </label>
                <span class="toggle-status" id="algo-status">OFF</span>
            </div>

            <div class="toggle-group">
                <span class="toggle-label">Mode:</span>
                <label class="toggle">
                    <input type="checkbox" id="mode-toggle" onchange="toggleMode(this.checked)">
                    <span class="toggle-slider"></span>
                </label>
                <span class="mode-badge" id="mode-badge">PAPER</span>
            </div>

            <div class="thresholds" id="thresholds">
                <strong>Thresholds:</strong> Entry: ±2σ | Exit: ±0.2σ | Stop: ±6σ | Hurst: 0.5 (20min)
            </div>

            <div class="session" id="session">
                <span class="session-icon">🇺🇸</span>
                <span>Session: <span id="session-name">New York</span></span>
            </div>

            <div style="margin-left: auto; display: flex; gap: 10px;">
                <a href="/settings" class="btn">⚙ Settings</a>
                <button class="btn btn-danger" onclick="resetStats()">↻ Reset Stats</button>
                <button class="btn btn-danger" onclick="clearTrades()">🗑 Clear Trades</button>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span>OKX Account</span>
                <span id="account-server">--</span>
            </div>
            <div class="card-body">
                <div class="account-grid">
                    <div class="account-item">
                        <label>Balance</label>
                        <div class="value" id="account-balance">$0.00</div>
                    </div>
                    <div class="account-item">
                        <label>Equity</label>
                        <div class="value" id="account-equity">$0.00</div>
                    </div>
                    <div class="account-item">
                        <label>Margin</label>
                        <div class="value" id="account-margin">$0.00</div>
                    </div>
                    <div class="account-item">
                        <label>Free Margin</label>
                        <div class="value" id="account-free">$0.00</div>
                    </div>
                    <div class="account-item">
                        <label>Open P&L</label>
                        <div class="value" id="account-pnl">$0.00</div>
                    </div>
                    <div class="account-item">
                        <label>Leverage</label>
                        <div class="value" id="account-leverage">1:1</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Info Panel Row - Above Charts -->
        <div class="top-info-row">
            <!-- Left: Prices & Basis -->
            <div class="card">
                <div class="asset-panel">
                    <div class="asset-header">
                        <span class="asset-name" id="asset-name">BTC</span>
                        <span class="sentiment" id="sentiment">FAIR</span>
                    </div>
                    <div class="price-grid">
                        <div class="price-item">
                            <label>SPOT</label>
                            <div class="value" id="spot-price">0.00</div>
                        </div>
                        <div class="price-item">
                            <label id="futures-label">FUTURES</label>
                            <div class="value" id="futures-price">0.00</div>
                        </div>
                        <div class="price-item">
                            <label>SPOT SPREAD</label>
                            <div class="value" id="spot-spread">$0.00</div>
                        </div>
                        <div class="price-item">
                            <label>FUT SPREAD</label>
                            <div class="value" id="futures-spread">$0.00</div>
                        </div>
                    </div>
                    <div class="basis-section">
                        <div class="basis-header">
                            <span>Basis (F-S)</span>
                            <span class="basis-value" id="basis-value">$0.00</span>
                        </div>
                        <div style="font-size: 14px; margin-bottom: 5px;">
                            <strong>Contract:</strong> <span id="futures-contract" style="color: #3498db; font-weight: 600;">--</span>
                        </div>
                        <div style="font-size: 14px;">Days to Expiry: <span id="days-expiry">--</span></div>
                    </div>
                </div>
            </div>

            <!-- Center: Z-Score Display -->
            <div class="card">
                <div class="asset-panel">
                    <div class="zscore-display">
                        <div class="zscore-note" id="zscore-note">COLLECTING DATA</div>
                        <div class="zscore-value" id="zscore-value">--</div>
                        <div class="hurst-badge" id="hurst-badge">Hurst: 0.500 | RANDOM</div>
                    </div>
                    <div class="stats-grid">
                        <div class="stat-item">
                            <label>MEAN</label>
                            <div class="value" id="stat-mean">0.00</div>
                        </div>
                        <div class="stat-item">
                            <label>STD</label>
                            <div class="value" id="stat-std">0.00</div>
                        </div>
                        <div class="stat-item">
                            <label>SPREAD</label>
                            <div class="value" id="stat-spread">0.00</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right: Entry Levels -->
            <div class="card">
                <div class="asset-panel">
                    <div class="entry-levels">
                        <div class="entry-box entry-short">
                            <div class="entry-title">Short Spread</div>
                            <div class="entry-row">
                                <span class="entry-label">Entry ↑</span>
                                <span class="entry-value" id="short-entry">0.00</span>
                            </div>
                            <div class="entry-row">
                                <span class="entry-label">Exit</span>
                                <span class="entry-value" id="short-exit">0.00</span>
                            </div>
                        </div>
                        <div class="entry-box entry-long">
                            <div class="entry-title">Long Spread</div>
                            <div class="entry-row">
                                <span class="entry-label">Entry ↓</span>
                                <span class="entry-value" id="long-entry">0.00</span>
                            </div>
                            <div class="entry-row">
                                <span class="entry-label">Exit</span>
                                <span class="entry-value" id="long-exit">0.00</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Full Width Charts -->
        <div class="card">
            <div class="card-header">BTC Z-Score</div>
            <div class="card-body">
                <div class="chart-container">
                    <canvas id="zscore-chart"></canvas>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">BTC Price</div>
            <div class="card-body">
                <div class="chart-container">
                    <canvas id="price-chart"></canvas>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">OKX Open Positions</div>
            <div class="card-body" style="padding: 0;">
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Type</th>
                            <th>Volume</th>
                            <th>Open Price</th>
                            <th>Current</th>
                            <th>P&L</th>
                            <th>Time</th>
                        </tr>
                    </thead>
                    <tbody id="positions-table">
                        <tr><td colspan="7" style="text-align: center; color: #888;">No open positions</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span>Portal Algo Positions</span>
                <span id="algo-position-summary" style="font-size: 12px;"></span>
            </div>
            <div class="card-body" style="padding: 0;">
                <table>
                    <thead>
                        <tr>
                            <th>Asset</th>
                            <th>Direction</th>
                            <th>Lots</th>
                            <th>Entry Z</th>
                            <th>Entry Spot</th>
                            <th>Entry Fut</th>
                            <th>Current Spot</th>
                            <th>Current Fut</th>
                            <th>Unrealized P&L</th>
                            <th>Days</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="algo-positions-table">
                        <tr><td colspan="11" style="text-align: center; color: #888;">No algorithm positions</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <div class="card-header" style="display: flex; justify-content: space-between; align-items: center;">
                <span>Trade Journal</span>
                <div>
                    <span id="trade-summary" style="margin-right: 15px;">Total P&L: $0.00 | Win Rate: 0% | Sharpe: 0.00</span>
                    <a href="/api/trades/csv" class="btn" style="background: #3498db; color: white; padding: 4px 12px; text-decoration: none; border-radius: 4px; font-size: 12px;">📥 Download CSV</a>
                </div>
            </div>
            <div class="card-body" style="padding: 0;">
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Direction</th>
                            <th>Entry</th>
                            <th>Exit</th>
                            <th>Days</th>
                            <th>Gross P&L</th>
                            <th>Net P&L</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="trades-table">
                        <tr><td colspan="8" style="text-align: center; color: #888;">No trades</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let zscoreChart, priceChart;

        function initCharts() {
            const zscoreCtx = document.getElementById('zscore-chart').getContext('2d');
            zscoreChart = new Chart(zscoreCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label: 'Z-Score', data: [], borderColor: '#3498db', borderWidth: 2, fill: false, pointRadius: 0 },
                        { label: 'Entry Upper (+)', data: [], borderColor: '#e74c3c', borderWidth: 1, borderDash: [5,5], fill: false, pointRadius: 0 },
                        { label: 'Entry Lower (-)', data: [], borderColor: '#e74c3c', borderWidth: 1, borderDash: [5,5], fill: false, pointRadius: 0 },
                        { label: 'Exit Upper', data: [], borderColor: '#27ae60', borderWidth: 1, borderDash: [2,2], fill: false, pointRadius: 0 },
                        { label: 'Exit Lower', data: [], borderColor: '#27ae60', borderWidth: 1, borderDash: [2,2], fill: false, pointRadius: 0 },
                        { label: 'Zero Line', data: [], borderColor: '#bbb', borderWidth: 1, fill: false, pointRadius: 0 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: { y: { suggestedMin: -3, suggestedMax: 3 } },
                    plugins: { legend: { position: 'top', labels: { boxWidth: 12, font: { size: 10 } } } }
                }
            });

            const priceCtx = document.getElementById('price-chart').getContext('2d');
            priceChart = new Chart(priceCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label: 'Spot Price', data: [], borderColor: '#3498db', borderWidth: 2, fill: false, pointRadius: 0, yAxisID: 'y' },
                        { label: 'Futures Price', data: [], borderColor: '#f39c12', borderWidth: 2, fill: false, pointRadius: 0, yAxisID: 'y' },
                        { label: 'Spread (F-S)', data: [], borderColor: '#27ae60', borderWidth: 2, borderDash: [3,3], fill: false, pointRadius: 0, yAxisID: 'y1' }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { type: 'linear', position: 'left', title: { display: true, text: 'Price ($)' } },
                        y1: { type: 'linear', position: 'right', title: { display: true, text: 'Spread' }, grid: { drawOnChartArea: false } }
                    },
                    plugins: { legend: { position: 'top', labels: { boxWidth: 12, font: { size: 10 } } } }
                }
            });
        }

        function updateData() {
            fetch('/api/data')
                .then(r => r.json())
                .then(data => {
                    // Update time
                    document.getElementById('current-time').textContent = new Date().toLocaleTimeString();

                    // Update config/toggles
                    if (data.config) {
                        document.getElementById('algo-toggle').checked = data.config.algo_enabled;
                        document.getElementById('algo-status').textContent = data.config.algo_enabled ? 'ON' : 'OFF';
                        document.getElementById('algo-status').className = 'toggle-status ' + (data.config.algo_enabled ? 'status-on' : 'status-off');

                        document.getElementById('mode-toggle').checked = !data.config.paper_mode;
                        document.getElementById('mode-badge').textContent = data.config.paper_mode ? 'PAPER' : 'LIVE';
                        document.getElementById('mode-badge').className = 'mode-badge ' + (data.config.paper_mode ? 'mode-paper' : 'mode-live');

                        document.getElementById('thresholds').innerHTML = `<strong>Thresholds:</strong> Entry: ±${data.config.entry_std_dev}σ | Exit: ±${data.config.exit_std_dev}σ | Stop: ±${data.config.stop_loss_std_dev}σ | Hurst: ${data.config.hurst_threshold} (${data.config.trending_duration_minutes}min)`;
                    }

                    // Update session
                    if (data.market_session) {
                        document.getElementById('session-name').textContent = data.market_session;
                    }

                    // Update account
                    if (data.account) {
                        const serverEl = document.getElementById('account-server');
                        serverEl.textContent = data.account.server || '--';
                        // Show error hint if present
                        if (data.account.error) {
                            serverEl.title = data.account.error;
                            serverEl.style.color = '#e74c3c';
                        } else {
                            serverEl.title = '';
                            serverEl.style.color = '';
                        }
                        document.getElementById('account-balance').textContent = '$' + (data.account.balance || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
                        document.getElementById('account-equity').textContent = '$' + (data.account.equity || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
                        document.getElementById('account-margin').textContent = '$' + (data.account.margin || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
                        document.getElementById('account-free').textContent = '$' + (data.account.free_margin || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
                        document.getElementById('account-leverage').textContent = '1:' + (data.account.leverage || 1);
                    }

                    // Update market data
                    if (data.data) {
                        const d = data.data;
                        document.getElementById('asset-name').textContent = d.asset || 'BTC';
                        document.getElementById('spot-price').textContent = (d.spot_price || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
                        document.getElementById('futures-price').textContent = (d.futures_price || 0).toLocaleString('en-US', {minimumFractionDigits: 2});
                        document.getElementById('spot-spread').textContent = '$' + (d.spot_spread || 0).toFixed(2);
                        document.getElementById('futures-spread').textContent = '$' + (d.futures_spread || 0).toFixed(2);
                        document.getElementById('basis-value').textContent = (d.spread || 0).toFixed(2);

                        // Show contract name in futures label and contract section
                        const futSymbol = d.futures_symbol || '';
                        document.getElementById('futures-contract').textContent = futSymbol || '--';
                        document.getElementById('futures-label').textContent = futSymbol ? `FUT (${futSymbol})` : 'FUTURES';
                        document.getElementById('days-expiry').textContent = d.days_to_expiry || '--';

                        // Z-Score
                        const zscore = d.zscore;
                        if (zscore !== null && zscore !== undefined) {
                            document.getElementById('zscore-value').textContent = zscore.toFixed(2) + 'σ';
                        } else {
                            document.getElementById('zscore-value').textContent = '--';
                        }

                        // Signal note with data collection status
                        if (d.signal) {
                            const noteEl = document.getElementById('zscore-note');
                            let noteText = d.signal.type || '';
                            // Show reason for better context
                            if (d.signal.reason && d.signal.reason.includes('Collecting data')) {
                                noteText = d.signal.reason;
                                noteEl.style.color = '#f39c12';  // Orange for collecting
                            } else if (d.signal.type === 'SELL_BASIS' || d.signal.type === 'BUY_BASIS') {
                                noteEl.style.color = '#27ae60';  // Green for entry signal
                            } else if (d.signal.type === 'STOP_LOSS') {
                                noteEl.style.color = '#e74c3c';  // Red for stop loss
                            } else {
                                noteEl.style.color = '#888';
                            }
                            noteEl.textContent = noteText;
                        }

                        // Data collection progress bar
                        if (d.stats) {
                            const count = d.stats.count || 0;
                            const required = d.stats.required || 90;
                            const complete = d.stats.complete || false;
                            const pct = Math.min(100, (count / required) * 100);

                            let progressEl = document.getElementById('data-progress');
                            if (!progressEl) {
                                // Create progress element if not exists
                                const container = document.querySelector('.zscore-display');
                                if (container) {
                                    progressEl = document.createElement('div');
                                    progressEl.id = 'data-progress';
                                    progressEl.style.cssText = 'font-size: 10px; color: #888; margin-top: 5px;';
                                    container.appendChild(progressEl);
                                }
                            }
                            if (progressEl) {
                                if (!complete) {
                                    progressEl.innerHTML = `<span style="color:#f39c12">Data: ${count}/${required} (${pct.toFixed(0)}%)</span> <progress value="${count}" max="${required}" style="width:80px;height:8px;"></progress>`;
                                } else {
                                    progressEl.innerHTML = `<span style="color:#27ae60">✓ Data ready (${count} points)</span>`;
                                }
                            }
                        }

                        // Hurst
                        const hurst = d.hurst;
                        const regime = d.hurst_regime || 'UNKNOWN';
                        if (hurst !== null && hurst !== undefined) {
                            const badge = document.getElementById('hurst-badge');
                            badge.textContent = `Hurst: ${hurst.toFixed(3)} | ${regime}`;
                            badge.className = 'hurst-badge hurst-' + regime.toLowerCase().replace('_', '-');
                        }

                        // Stats
                        if (d.stats) {
                            document.getElementById('stat-mean').textContent = (d.stats.mean || 0).toFixed(2);
                            document.getElementById('stat-std').textContent = (d.stats.std || 0).toFixed(2);
                            document.getElementById('stat-spread').textContent = (d.spread || 0).toFixed(2);

                            document.getElementById('short-entry').textContent = (d.stats.upper_entry || 0).toFixed(2);
                            document.getElementById('short-exit').textContent = (d.stats.upper_exit || 0).toFixed(2);
                            document.getElementById('long-entry').textContent = (d.stats.lower_entry || 0).toFixed(2);
                            document.getElementById('long-exit').textContent = (d.stats.lower_exit || 0).toFixed(2);
                        }

                        // Sentiment
                        const sentiment = d.sentiment || 'FAIR';
                        const sentimentEl = document.getElementById('sentiment');
                        sentimentEl.textContent = sentiment;
                        sentimentEl.className = 'sentiment sentiment-' + sentiment.toLowerCase();
                    }

                    // Update Z-score chart
                    if (data.zscore_history && data.zscore_history.length > 0) {
                        const labels = data.zscore_history.map(h => h.time);
                        zscoreChart.data.labels = labels;
                        zscoreChart.data.datasets[0].data = data.zscore_history.map(h => h.zscore);
                        zscoreChart.data.datasets[1].data = data.zscore_history.map(h => h.entry_upper);
                        zscoreChart.data.datasets[2].data = data.zscore_history.map(h => h.entry_lower);
                        zscoreChart.data.datasets[3].data = data.zscore_history.map(h => h.exit_upper);
                        zscoreChart.data.datasets[4].data = data.zscore_history.map(h => h.exit_lower);
                        zscoreChart.data.datasets[5].data = labels.map(() => 0);
                        zscoreChart.update('none');
                    }

                    // Update price chart
                    if (data.price_history && data.price_history.length > 0) {
                        const labels = data.price_history.map(h => h.time);
                        priceChart.data.labels = labels;
                        priceChart.data.datasets[0].data = data.price_history.map(h => h.spot_price);
                        priceChart.data.datasets[1].data = data.price_history.map(h => h.futures_price);
                        priceChart.data.datasets[2].data = data.price_history.map(h => h.spread);
                        priceChart.update('none');
                    }

                    // Update positions
                    const posTable = document.getElementById('positions-table');
                    if (data.okx_positions && data.okx_positions.length > 0) {
                        posTable.innerHTML = data.okx_positions.map(p => `
                            <tr>
                                <td>${p.symbol}</td>
                                <td class="${p.type === 'BUY' ? 'type-buy' : 'type-sell'}">${p.type}</td>
                                <td>${p.volume}</td>
                                <td>${(p.open_price || 0).toFixed(2)}</td>
                                <td>${(p.current_price || 0).toFixed(2)}</td>
                                <td class="${p.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">$${(p.pnl || 0).toFixed(2)}</td>
                                <td>${p.time || '--'}</td>
                            </tr>
                        `).join('');

                        // Update account P&L
                        const totalPnl = data.okx_positions.reduce((sum, p) => sum + (p.pnl || 0), 0);
                        const pnlEl = document.getElementById('account-pnl');
                        pnlEl.textContent = '$' + totalPnl.toFixed(2);
                        pnlEl.className = 'value ' + (totalPnl >= 0 ? 'positive' : 'negative');
                    } else {
                        posTable.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #888;">No open positions</td></tr>';
                    }

                    // Update trades
                    const tradesTable = document.getElementById('trades-table');
                    if (data.trade_history && data.trade_history.length > 0) {
                        tradesTable.innerHTML = data.trade_history.slice(0, 10).map(t => `
                            <tr>
                                <td>${t.trade_id}</td>
                                <td>${t.direction}</td>
                                <td>${t.entry_date ? new Date(t.entry_date).toLocaleString() : '--'}</td>
                                <td>${t.exit_date ? new Date(t.exit_date).toLocaleString() : '--'}</td>
                                <td>${t.days_held || 0}</td>
                                <td class="${(t.gross_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}">$${(t.gross_pnl || 0).toFixed(2)}</td>
                                <td class="${(t.net_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}">$${(t.net_pnl || 0).toFixed(2)}</td>
                                <td>${t.status}</td>
                            </tr>
                        `).join('');
                    } else {
                        tradesTable.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #888;">No trades</td></tr>';
                    }

                    // Update algo positions table
                    const algoTable = document.getElementById('algo-positions-table');
                    if (data.algo_positions && data.algo_positions.length > 0) {
                        algoTable.innerHTML = data.algo_positions.map(p => {
                            const entryDate = p.entry_date ? new Date(p.entry_date) : null;
                            const days = entryDate ? Math.floor((new Date() - entryDate) / (1000 * 60 * 60 * 24)) : 0;
                            const unrealizedPnl = p.unrealized_pnl || 0;
                            return `
                            <tr>
                                <td>${p.asset || '--'}</td>
                                <td class="${p.direction === 'Short Spread' ? 'type-sell' : 'type-buy'}">${p.direction || '--'}</td>
                                <td>${p.lot_size || 0.01}</td>
                                <td>${(p.entry_zscore || 0).toFixed(2)}σ</td>
                                <td>${(p.entry_spot_price || 0).toFixed(2)}</td>
                                <td>${(p.entry_futures_price || 0).toFixed(2)}</td>
                                <td>${(p.current_spot || 0).toFixed(2)}</td>
                                <td>${(p.current_futures || 0).toFixed(2)}</td>
                                <td class="${unrealizedPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">$${unrealizedPnl.toFixed(2)}</td>
                                <td>${days}</td>
                                <td><button onclick="closeAlgoPosition('${p.asset}')" style="background: #e74c3c; color: white; border: none; padding: 2px 8px; border-radius: 3px; cursor: pointer;">Close</button></td>
                            </tr>
                        `}).join('');

                        // Update summary
                        const totalUnrealized = data.algo_positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0);
                        document.getElementById('algo-position-summary').textContent = `${data.algo_positions.length} position(s) | Unrealized: $${totalUnrealized.toFixed(2)}`;
                    } else {
                        algoTable.innerHTML = '<tr><td colspan="11" style="text-align: center; color: #888;">No algorithm positions</td></tr>';
                        document.getElementById('algo-position-summary').textContent = '';
                    }

                    // Update trade summary
                    if (data.trade_summary) {
                        const s = data.trade_summary;
                        document.getElementById('trade-summary').textContent =
                            `Total P&L: $${(s.total_pnl || 0).toFixed(2)} | Win Rate: ${(s.win_rate || 0).toFixed(1)}% | Sharpe: ${(s.sharpe_ratio || 0).toFixed(2)} | Max DD: $${(s.max_drawdown || 0).toFixed(2)}`;
                    }
                })
                .catch(console.error);
        }

        function toggleAlgo(enabled) {
            fetch('/api/toggle_algo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled })
            });
        }

        function toggleMode(live) {
            if (live && !confirm('Switch to LIVE mode? Real orders will be executed!')) {
                document.getElementById('mode-toggle').checked = false;
                return;
            }
            fetch('/api/toggle_paper', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paper: !live })
            });
        }

        function resetStats() {
            if (confirm('Reset all statistics? This will clear price history.')) {
                fetch('/api/reset_statistics', { method: 'POST' })
                    .then(() => location.reload());
            }
        }

        function clearTrades() {
            if (confirm('Clear all trades? This cannot be undone.')) {
                fetch('/api/clear_trades', { method: 'POST' })
                    .then(() => location.reload());
            }
        }

        function closeAlgoPosition(assetKey) {
            if (confirm(`Close position for ${assetKey}?`)) {
                fetch('/api/close_position', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ asset_key: assetKey })
                }).then(r => r.json())
                  .then(data => {
                      if (data.success) {
                          alert('Position closed');
                      } else {
                          alert('Failed to close position');
                      }
                  });
            }
        }

        initCharts();
        updateData();
        setInterval(updateData, 300);  // Refresh every 300ms
    </script>
</body>
</html>
'''

SETTINGS_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Settings - Trading Portal</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { margin-bottom: 20px; }
        .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .card h2 { margin-bottom: 15px; font-size: 1.2rem; border-bottom: 1px solid #eee; padding-bottom: 10px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: 500; }
        input, select { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 1rem; }
        input:focus, select:focus { outline: none; border-color: #3498db; }
        .row { display: flex; gap: 15px; }
        .row .form-group { flex: 1; }
        .btn { padding: 12px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; }
        .btn-primary { background: #3498db; color: white; }
        .btn-secondary { background: #eee; color: #333; }
        .btn:hover { opacity: 0.9; }
        .actions { display: flex; gap: 10px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Trading Settings</h1>

        <form method="POST">
            <div class="card">
                <h2>Asset Configuration</h2>
                <div class="row">
                    <div class="form-group">
                        <label>Asset Name</label>
                        <input type="text" name="asset_name" value="{{ config.asset_name or 'BTC' }}">
                    </div>
                    <div class="form-group">
                        <label>Spot Symbol</label>
                        <input type="text" name="spot_symbol" value="{{ config.spot_symbol or 'BTC-USDT' }}">
                    </div>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Futures Symbol (leave empty for auto)</label>
                        <input type="text" name="futures_symbol" value="{{ config.futures_symbol or '' }}">
                    </div>
                    <div class="form-group">
                        <label>Contract Size</label>
                        <input type="number" step="0.001" name="contract_size" value="{{ config.contract_size or 1 }}">
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Statistical Parameters</h2>
                <div class="row">
                    <div class="form-group">
                        <label>Lookback Period</label>
                        <input type="number" name="lookback_period" value="{{ config.lookback_period or 90 }}">
                    </div>
                    <div class="form-group">
                        <label>Lookback Unit</label>
                        <select name="lookback_unit">
                            <option value="minutes" {{ 'selected' if config.lookback_unit == 'minutes' }}>Minutes</option>
                            <option value="days" {{ 'selected' if config.lookback_unit == 'days' }}>Days</option>
                        </select>
                    </div>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Entry Std Dev (σ)</label>
                        <input type="number" step="0.1" name="entry_std_dev" value="{{ config.entry_std_dev or 2.0 }}">
                    </div>
                    <div class="form-group">
                        <label>Exit Std Dev (σ)</label>
                        <input type="number" step="0.1" name="exit_std_dev" value="{{ config.exit_std_dev or 0.2 }}">
                    </div>
                    <div class="form-group">
                        <label>Stop Loss Std Dev (σ)</label>
                        <input type="number" step="0.1" name="stop_loss_std_dev" value="{{ config.stop_loss_std_dev or 6.0 }}">
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Risk Management</h2>
                <div class="row">
                    <div class="form-group">
                        <label>Lot Size</label>
                        <input type="number" step="0.001" name="lot_size" value="{{ config.lot_size or 0.01 }}">
                    </div>
                    <div class="form-group">
                        <label>Max Positions</label>
                        <input type="number" name="max_positions" value="{{ config.max_positions or 1 }}">
                    </div>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Min Profit Per Lot ($)</label>
                        <input type="number" step="1" name="min_profit_per_lot" value="{{ config.min_profit_per_lot or 10 }}">
                    </div>
                    <div class="form-group">
                        <label>Max Loss Per Lot ($)</label>
                        <input type="number" step="1" name="max_loss_per_lot" value="{{ config.max_loss_per_lot or 50 }}">
                    </div>
                </div>
                <div class="row">
                    <div class="form-group">
                        <label>Time Stop (Days, 0=disabled)</label>
                        <input type="number" step="0.5" name="time_stop_loss_days" value="{{ config.time_stop_loss_days or 0 }}">
                    </div>
                    <div class="form-group">
                        <label>Commission Per Lot ($)</label>
                        <input type="number" step="0.01" name="commission_per_lot" value="{{ config.commission_per_lot or 0 }}">
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Hurst Filter</h2>
                <div class="row">
                    <div class="form-group">
                        <label>Hurst Enabled</label>
                        <select name="hurst_enabled">
                            <option value="1" {{ 'selected' if config.hurst_enabled }}>Yes</option>
                            <option value="0" {{ 'selected' if not config.hurst_enabled }}>No</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Hurst Threshold</label>
                        <input type="number" step="0.05" name="hurst_threshold" value="{{ config.hurst_threshold or 0.5 }}">
                    </div>
                    <div class="form-group">
                        <label>Trending Duration (min)</label>
                        <input type="number" name="trending_duration_minutes" value="{{ config.trending_duration_minutes or 20 }}">
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>Overnight Close</h2>
                <div class="row">
                    <div class="form-group">
                        <label>Close Before Overnight</label>
                        <select name="close_before_overnight">
                            <option value="0" {{ 'selected' if not config.close_before_overnight }}>No</option>
                            <option value="1" {{ 'selected' if config.close_before_overnight }}>Yes</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Close Hour (UTC)</label>
                        <input type="number" name="overnight_close_hour" value="{{ config.overnight_close_hour or 16 }}">
                    </div>
                    <div class="form-group">
                        <label>Close Minute</label>
                        <input type="number" name="overnight_close_minute" value="{{ config.overnight_close_minute or 40 }}">
                    </div>
                </div>
            </div>

            <div class="actions">
                <button type="submit" class="btn btn-primary">Save Settings</button>
                <a href="/" class="btn btn-secondary">Cancel</a>
            </div>
        </form>
    </div>
</body>
</html>
'''


# ==================== ROUTES ====================

@app.route('/')
def index():
    """Main monitoring page"""
    if not monitor.initialized:
        return redirect(url_for('setup'))
    return render_template_string(MONITOR_TEMPLATE)


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Setup page"""
    if request.method == 'POST':
        config = db.get_config()
        config['asset_name'] = request.form.get('asset_name', 'BTC')
        config['spot_symbol'] = request.form.get('spot_symbol', 'BTC-USDT')
        config['futures_symbol'] = request.form.get('futures_symbol', '')
        config['contract_size'] = float(request.form.get('contract_size', 1))
        db.save_config(config)

        if monitor.initialize_okx():
            monitor.start_background_updates()
            return redirect(url_for('index'))
        else:
            return "Failed to connect to OKX. Check your API credentials.", 500

    config = db.get_config()
    return render_template_string(SETTINGS_TEMPLATE, config=config)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Settings page"""
    if request.method == 'POST':
        config = db.get_config()

        # Update all settings
        for key in ['asset_name', 'spot_symbol', 'futures_symbol', 'lookback_unit']:
            if key in request.form:
                config[key] = request.form[key]

        for key in ['contract_size', 'lookback_period', 'entry_std_dev', 'exit_std_dev',
                    'stop_loss_std_dev', 'time_stop_loss_days', 'max_positions', 'lot_size',
                    'commission_per_lot', 'hurst_threshold', 'trending_duration_minutes',
                    'overnight_close_hour', 'overnight_close_minute', 'min_profit_per_lot',
                    'max_loss_per_lot']:
            if key in request.form:
                config[key] = float(request.form[key])

        for key in ['hurst_enabled', 'close_before_overnight']:
            config[key] = request.form.get(key) == '1'

        db.save_config(config)
        monitor.config = config

        return redirect(url_for('index'))

    config = db.get_config()
    return render_template_string(SETTINGS_TEMPLATE, config=config)


@app.route('/api/data')
def get_data():
    """Get all data for dashboard"""
    data = monitor.current_data
    config = db.get_config()

    return jsonify({
        'data': data,
        'account': monitor.get_account_info(),
        'okx_positions': monitor.get_okx_positions(),
        'positions': monitor.get_enriched_positions(),
        'algo_positions': monitor.get_enriched_positions(),  # Algorithm-managed positions
        'zscore_history': list(monitor.zscore_history),
        'price_history': list(monitor.price_history),
        'trade_history': db.get_trades(limit=100),
        'trade_summary': db.get_trade_summary(),
        'config': config,
        'market_session': data.get('session', 'Unknown') if data else 'Unknown'
    })


@app.route('/api/toggle_algo', methods=['POST'])
def toggle_algo():
    """Toggle algorithmic trading"""
    data = request.json
    enabled = data.get('enabled', False)

    config = db.get_config()
    config['algo_enabled'] = enabled
    db.save_config(config)
    monitor.config = config

    return jsonify({'success': True, 'enabled': enabled})


@app.route('/api/toggle_paper', methods=['POST'])
def toggle_paper():
    """Toggle paper/live mode"""
    data = request.json
    paper = data.get('paper', True)

    config = db.get_config()
    config['paper_mode'] = paper
    db.save_config(config)
    monitor.config = config

    # Reinitialize client with new mode
    if monitor.client:
        monitor.client.demo_trading = paper

    return jsonify({'success': True, 'paper': paper})


@app.route('/api/reset_statistics', methods=['POST'])
def reset_statistics():
    """Reset statistics"""
    asset = monitor.config.get('asset_name', 'BTC')
    db.clear_price_history(asset)
    monitor.spread_cache.clear()
    monitor.zscore_history.clear()
    monitor.price_history.clear()

    return jsonify({'success': True})


@app.route('/api/clear_trades', methods=['POST'])
def api_clear_trades():
    """Clear all trades"""
    db.clear_trades()
    monitor.positions.clear()

    return jsonify({'success': True})


@app.route('/api/close_position', methods=['POST'])
def api_close_position():
    """Manually close a position"""
    data = request.json
    asset = data.get('asset_key', monitor.config.get('asset_name', 'BTC'))

    success = monitor.manual_close_position(asset)
    return jsonify({'success': success})


@app.route('/api/trades/csv')
def api_trades_csv():
    """Download trade history as CSV"""
    import csv
    import io

    trades = db.get_trades(limit=10000)

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row matching MT5 version
    writer.writerow([
        '#', 'Direction', 'Lots', 'Entry Date', 'Exit Date', 'Days',
        'Entry Z', 'Exit Z', 'Entry Spot', 'Entry Fut', 'Exit Spot', 'Exit Fut',
        'Spot P&L', 'Futures P&L', 'Gross P&L', 'Swap', 'Comm', 'Spread', 'Net P&L', 'Return %', 'Status'
    ])

    # Data rows
    for i, t in enumerate(trades, 1):
        writer.writerow([
            i,
            t.get('direction', ''),
            t.get('lot_size', 0.01),
            t.get('entry_date', ''),
            t.get('exit_date', ''),
            t.get('days_held', 0),
            f"{t.get('entry_zscore', 0):.2f}" if t.get('entry_zscore') else '',
            f"{t.get('exit_zscore', 0):.2f}" if t.get('exit_zscore') else '',
            f"{t.get('entry_spot_price', 0):.2f}" if t.get('entry_spot_price') else '',
            f"{t.get('entry_futures_price', 0):.2f}" if t.get('entry_futures_price') else '',
            f"{t.get('exit_spot_price', 0):.2f}" if t.get('exit_spot_price') else '',
            f"{t.get('exit_futures_price', 0):.2f}" if t.get('exit_futures_price') else '',
            f"{t.get('spot_pnl', 0):.2f}",
            f"{t.get('futures_pnl', 0):.2f}",
            f"{t.get('gross_pnl', 0):.2f}",
            f"{t.get('swap_cost', 0):.2f}",
            f"{t.get('commission', 0):.2f}",
            f"{t.get('spread_cost', 0):.2f}",
            f"{t.get('net_pnl', 0):.2f}",
            f"{t.get('return_pct', 0):.2f}%",
            t.get('status', '')
        ])

    # Generate response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=trade_history_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )


@app.route('/api/algo_positions')
def api_algo_positions():
    """Get algorithm-managed positions"""
    return jsonify({
        'positions': monitor.get_enriched_positions()
    })


# ==================== MAIN ====================

def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("  ALGORITHMIC TRADING PORTAL - OKX")
    print("=" * 60)
    print("\nStarting server on http://localhost:8080")
    print("Press Ctrl+C to stop\n")

    # Try to auto-initialize
    config = db.get_config()
    if config.get('spot_symbol'):
        if monitor.initialize_okx():
            monitor.start_background_updates()

    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)


if __name__ == '__main__':
    main()
