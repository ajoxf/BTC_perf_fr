# BTC Basis & Funding Rate Trading System

## System Overview

A Python-based trading system for OKX exchange that implements two mean-reversion strategies:
1. **BTC Futures vs Spot Basis Trading** - Capture basis convergence as futures approach expiry
2. **BTC Perpetual Funding Rate Arbitrage** - Collect funding payments from extreme funding rates

---

## OKX API v5 - Key Endpoints

### Authentication
OKX uses HMAC SHA256 signature authentication with three components:
- **API Key**: Your public API key
- **Secret Key**: Used to generate signatures (never sent directly)
- **Passphrase**: Additional security layer you set when creating the API key

**Signature Generation:**
```python
import hmac
import base64
import hashlib
from datetime import datetime

def generate_signature(timestamp, method, request_path, body, secret_key):
    message = timestamp + method + request_path + body
    mac = hmac.new(
        bytes(secret_key, encoding='utf8'),
        bytes(message, encoding='utf8'),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode()
```

**Required Headers:**
```
OK-ACCESS-KEY: <api_key>
OK-ACCESS-SIGN: <signature>
OK-ACCESS-TIMESTAMP: <iso_timestamp>
OK-ACCESS-PASSPHRASE: <passphrase>
```

### REST API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v5/market/ticker` | GET | Get single ticker price |
| `/api/v5/market/tickers` | GET | Get all tickers by instrument type |
| `/api/v5/public/instruments` | GET | Get available instruments |
| `/api/v5/public/funding-rate` | GET | Get current funding rate |
| `/api/v5/public/funding-rate-history` | GET | Get historical funding rates |
| `/api/v5/trade/order` | POST | Place order |
| `/api/v5/trade/cancel-order` | POST | Cancel order |
| `/api/v5/account/balance` | GET | Get account balance |
| `/api/v5/account/positions` | GET | Get positions |

### Key Instrument IDs
```
BTC-USDT           # Spot
BTC-USDT-SWAP      # Perpetual Swap
BTC-USDT-240329    # Quarterly Futures (example: March 2024)
BTC-USDT-240628    # Next Quarter Futures (example: June 2024)
```

### WebSocket Channels
```
wss://ws.okx.com:8443/ws/v5/public
wss://ws.okx.com:8443/ws/v5/private
```

**Public Channels:**
- `tickers` - Real-time price updates
- `funding-rate` - Funding rate updates

**Private Channels:**
- `orders` - Order updates
- `positions` - Position updates
- `account` - Account updates

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Flask Web Interface                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │Dashboard │ │Positions │ │ Signals  │ │  Trades  │ │ Settings │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          Core Trading Engine                         │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐   │
│  │Signal Generator│  │Order Executor │  │  Position Manager     │   │
│  │  - Z-Score    │  │  - Paper Mode │  │  - Track positions    │   │
│  │  - Hurst Exp  │  │  - Live Mode  │  │  - Calculate P&L      │   │
│  │  - Thresholds │  │  - Order Queue│  │  - Funding tracking   │   │
│  └───────────────┘  └───────────────┘  └───────────────────────┘   │
│                                                                      │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐   │
│  │Risk Manager   │  │Basis Strategy │  │Funding Rate Strategy  │   │
│  │  - Max size   │  │  - Spot/Fut   │  │  - Spot/Perp          │   │
│  │  - Stop loss  │  │  - Roll mgmt  │  │  - 8h settlement      │   │
│  │  - Regime     │  │  - Expiry     │  │  - Rate prediction    │   │
│  └───────────────┘  └───────────────┘  └───────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          Data Layer                                  │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐   │
│  │ OKX Connector │  │  Data Store   │  │   SQLite Database     │   │
│  │  - REST API   │  │  - Prices     │  │   - Trades            │   │
│  │  - WebSocket  │  │  - Basis      │  │   - Positions         │   │
│  │  - Rate Limit │  │  - Funding    │  │   - Settings          │   │
│  └───────────────┘  └───────────────┘  └───────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Project Directory Structure

```
BTC_perf_fr/
├── app/
│   ├── __init__.py              # Flask app factory
│   ├── config.py                # Configuration management
│   ├── models.py                # SQLAlchemy models
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── okx_client.py        # OKX REST API client
│   │   ├── okx_websocket.py     # OKX WebSocket client
│   │   └── rate_limiter.py      # API rate limiting
│   │
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base_strategy.py     # Abstract base strategy
│   │   ├── basis_strategy.py    # Spot-Futures basis trading
│   │   └── funding_strategy.py  # Perpetual funding arbitrage
│   │
│   ├── analytics/
│   │   ├── __init__.py
│   │   ├── statistics.py        # Rolling stats, z-scores
│   │   ├── hurst.py             # Hurst exponent calculation
│   │   └── signals.py           # Signal generation
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── order_manager.py     # Order placement/tracking
│   │   ├── position_manager.py  # Position tracking
│   │   └── paper_trading.py     # Paper trading simulation
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── risk_manager.py      # Risk checks and limits
│   │   └── stops.py             # Stop loss logic
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── collector.py         # Data collection service
│   │   ├── store.py             # In-memory data store
│   │   └── persistence.py       # Database operations
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py         # Dashboard routes
│   │   ├── api_routes.py        # REST API endpoints
│   │   └── settings.py          # Settings routes
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── positions.html
│   │   ├── trades.html
│   │   └── settings.html
│   │
│   └── static/
│       ├── css/
│       └── js/
│
├── tests/
│   ├── __init__.py
│   ├── test_okx_client.py
│   ├── test_strategies.py
│   └── test_analytics.py
│
├── migrations/                   # Database migrations
├── logs/                         # Application logs
├── instance/                     # Instance-specific config
│   └── trading.db               # SQLite database
│
├── requirements.txt
├── run.py                        # Application entry point
├── config.yaml                   # Default configuration
└── README.md
```

---

## Database Schema

```sql
-- Configuration settings
CREATE TABLE settings (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Price history for analysis
CREATE TABLE price_history (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    price REAL NOT NULL,
    volume REAL,
    UNIQUE(instrument_id, timestamp)
);

-- Basis spread history
CREATE TABLE basis_history (
    id INTEGER PRIMARY KEY,
    futures_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    spot_price REAL NOT NULL,
    futures_price REAL NOT NULL,
    basis REAL NOT NULL,
    basis_pct REAL NOT NULL,
    z_score REAL,
    UNIQUE(futures_id, timestamp)
);

-- Funding rate history
CREATE TABLE funding_history (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    funding_rate REAL NOT NULL,
    predicted_rate REAL,
    z_score REAL,
    UNIQUE(instrument_id, timestamp)
);

-- Trade journal
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    strategy TEXT NOT NULL,          -- 'basis' or 'funding'
    trade_type TEXT NOT NULL,        -- 'entry' or 'exit'
    timestamp TIMESTAMP NOT NULL,

    -- Position details
    spot_side TEXT,                  -- 'long' or 'short'
    spot_instrument TEXT,
    spot_size REAL,
    spot_price REAL,

    derivative_side TEXT,            -- 'long' or 'short'
    derivative_instrument TEXT,
    derivative_size REAL,
    derivative_price REAL,

    -- Signal info
    signal_value REAL,               -- z-score at entry/exit
    signal_type TEXT,                -- 'zscore', 'time_stop', 'loss_stop'

    -- P&L (populated on exit)
    realized_pnl REAL,
    funding_collected REAL,
    fees_paid REAL,

    notes TEXT
);

-- Active positions
CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    strategy TEXT NOT NULL,
    status TEXT DEFAULT 'open',      -- 'open', 'closed'
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,

    -- Entry details
    entry_spot_price REAL,
    entry_derivative_price REAL,
    entry_signal REAL,

    -- Position size
    spot_size REAL,
    derivative_size REAL,

    -- Current state
    unrealized_pnl REAL,
    total_funding REAL DEFAULT 0,

    -- Exit details
    exit_spot_price REAL,
    exit_derivative_price REAL,
    exit_signal REAL,
    exit_reason TEXT,

    -- Final P&L
    realized_pnl REAL,
    total_fees REAL
);

-- Funding payments received/paid
CREATE TABLE funding_payments (
    id INTEGER PRIMARY KEY,
    position_id INTEGER REFERENCES positions(id),
    timestamp TIMESTAMP NOT NULL,
    instrument_id TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    position_size REAL NOT NULL,
    payment_amount REAL NOT NULL,    -- positive = received, negative = paid
    mark_price REAL
);
```

---

## Answers to Your Questions

### 1. What's the best way to structure OKX API authentication?

**Recommended Approach: Singleton Client with Environment Variables**

```python
# app/api/okx_client.py
import os
import hmac
import base64
import hashlib
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any

class OKXClient:
    """OKX API v5 Client with proper authentication"""

    BASE_URL = "https://www.okx.com"
    DEMO_URL = "https://www.okx.com"  # Same URL, different flag

    def __init__(self,
                 api_key: Optional[str] = None,
                 secret_key: Optional[str] = None,
                 passphrase: Optional[str] = None,
                 demo_trading: bool = True):

        self.api_key = api_key or os.environ.get('OKX_API_KEY')
        self.secret_key = secret_key or os.environ.get('OKX_SECRET_KEY')
        self.passphrase = passphrase or os.environ.get('OKX_PASSPHRASE')
        self.demo_trading = demo_trading

        if not all([self.api_key, self.secret_key, self.passphrase]):
            raise ValueError("API credentials required")

    def _get_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def _sign(self, timestamp: str, method: str, path: str, body: str = '') -> str:
        message = timestamp + method + path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf8'),
            bytes(message, encoding='utf8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method: str, path: str, body: str = '') -> Dict[str, str]:
        timestamp = self._get_timestamp()
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': self._sign(timestamp, method, path, body),
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'x-simulated-trading': '1' if self.demo_trading else '0',
            'Content-Type': 'application/json'
        }

    def get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Make authenticated GET request"""
        url = self.BASE_URL + path
        if params:
            query = '&'.join(f"{k}={v}" for k, v in params.items())
            path = f"{path}?{query}"
            url = self.BASE_URL + path

        headers = self._headers('GET', path)
        response = requests.get(url, headers=headers)
        return response.json()

    def post(self, path: str, data: Dict) -> Dict[str, Any]:
        """Make authenticated POST request"""
        import json
        body = json.dumps(data)
        headers = self._headers('POST', path, body)
        response = requests.post(self.BASE_URL + path, headers=headers, data=body)
        return response.json()
```

**Key Points:**
- Use `x-simulated-trading: 1` header for paper trading (uses OKX demo environment)
- Store credentials in environment variables or encrypted config
- Implement retry logic with exponential backoff for rate limits

---

### 2. How should we handle the 8-hour funding settlement timing?

**Funding Settlement Schedule:**
- Settlements occur at **00:00, 08:00, 16:00 UTC**
- Funding rate snapshot taken **1 minute before** settlement (e.g., 07:59 for 08:00 settlement)
- Position must be held **through** settlement to receive/pay funding

**Recommended Implementation:**

```python
# app/strategies/funding_strategy.py
from datetime import datetime, timezone, timedelta
from enum import Enum

class FundingWindow(Enum):
    BEFORE_SETTLEMENT = "before"    # Good time to enter
    AT_SETTLEMENT = "at"            # Settlement happening
    AFTER_SETTLEMENT = "after"      # Just settled, evaluate exit

class FundingTimer:
    """Manage funding settlement timing"""

    SETTLEMENT_HOURS = [0, 8, 16]  # UTC hours
    ENTRY_WINDOW_MINUTES = 30      # Enter up to 30 min before
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
```

**Strategy Timing Logic:**
1. **Monitor** funding rate continuously
2. **Enter** when rate is extreme AND within 30 min of settlement
3. **Hold** through settlement to collect funding
4. **Evaluate exit** after settlement - continue if rate still favorable

---

### 3. What's a reasonable minimum funding rate threshold for entry?

**Analysis of BTC Perpetual Funding:**
- Neutral funding ≈ 0.01% per 8 hours (0.03% daily, ~11% APY)
- Normal range: -0.01% to 0.03% per 8 hours
- Elevated: 0.05% to 0.10% per 8 hours
- Extreme: > 0.10% per 8 hours

**Recommended Thresholds:**

```python
# app/config.py
class FundingConfig:
    # Entry thresholds (absolute value)
    MIN_FUNDING_RATE = 0.0005      # 0.05% minimum to consider (18% APY)
    STRONG_SIGNAL_RATE = 0.001    # 0.10% strong signal (36% APY)
    EXTREME_RATE = 0.002          # 0.20% extreme (73% APY)

    # Z-score based thresholds (preferred)
    ENTRY_ZSCORE = 2.0            # Enter when funding z-score > 2
    EXIT_ZSCORE = 0.5             # Exit when funding z-score < 0.5

    # Cost considerations
    SPOT_TAKER_FEE = 0.001        # 0.10% taker fee
    PERP_TAKER_FEE = 0.0005       # 0.05% taker fee
    TOTAL_ROUND_TRIP = 0.003      # ~0.30% total trading costs

    # Minimum funding to cover costs
    # Need at least 0.30% funding to break even on round trip
    # Conservative: 2x costs = 0.60% = 0.075% per 8h over 1 day
    MIN_EXPECTED_FUNDING = 0.00075  # 0.075% per settlement minimum
```

**Recommended Entry Criteria:**
1. **Z-score > 2.0** (funding rate is 2 std devs from mean)
2. **Absolute rate > 0.05%** per 8 hours
3. **Predicted rate** confirms direction (OKX provides next predicted rate)
4. **Hurst exponent < 0.5** (mean-reverting regime)

---

### 4. How to handle futures contract rolls approaching expiry?

**OKX Futures Expiry Schedule:**
- Quarterly contracts: Last Friday of March, June, September, December
- Weekly contracts: Every Friday
- Contracts settle at 08:00 UTC on expiry day

**Roll Management Strategy:**

```python
# app/strategies/basis_strategy.py
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class FuturesContract:
    inst_id: str          # e.g., "BTC-USDT-240329"
    expiry: datetime
    days_to_expiry: int
    basis: float
    basis_pct: float

class ContractRollManager:
    """Manage futures contract rolls"""

    # Roll parameters
    ROLL_WARNING_DAYS = 7     # Start considering roll
    FORCE_ROLL_DAYS = 3       # Must roll by this time
    MIN_LIQUIDITY_DAYS = 5    # Minimum days for new positions

    def __init__(self, okx_client):
        self.client = okx_client

    def get_active_futures(self) -> List[FuturesContract]:
        """Get all active BTC-USDT futures contracts"""
        response = self.client.get('/api/v5/public/instruments', {
            'instType': 'FUTURES',
            'uly': 'BTC-USDT'
        })

        contracts = []
        now = datetime.now()

        for inst in response.get('data', []):
            expiry = datetime.fromtimestamp(int(inst['expTime']) / 1000)
            days = (expiry - now).days

            contracts.append(FuturesContract(
                inst_id=inst['instId'],
                expiry=expiry,
                days_to_expiry=days,
                basis=0,  # Populate from ticker
                basis_pct=0
            ))

        return sorted(contracts, key=lambda x: x.expiry)

    def select_contract(self, contracts: List[FuturesContract]) -> Optional[FuturesContract]:
        """Select best contract for new position"""
        eligible = [c for c in contracts if c.days_to_expiry >= self.MIN_LIQUIDITY_DAYS]

        if not eligible:
            return None

        # Prefer front-month if > 7 days to expiry, else next contract
        front = eligible[0]
        if front.days_to_expiry >= self.ROLL_WARNING_DAYS:
            return front
        elif len(eligible) > 1:
            return eligible[1]  # Next contract

        return front

    def needs_roll(self, contract: FuturesContract) -> bool:
        """Check if position needs to be rolled"""
        return contract.days_to_expiry <= self.FORCE_ROLL_DAYS

    def should_warn_roll(self, contract: FuturesContract) -> bool:
        """Check if roll warning should be displayed"""
        return contract.days_to_expiry <= self.ROLL_WARNING_DAYS

    def execute_roll(self, old_contract: FuturesContract,
                     new_contract: FuturesContract,
                     position_size: float) -> dict:
        """Execute a contract roll"""
        # 1. Close position in old contract
        # 2. Open equivalent position in new contract
        # 3. Record roll in database

        return {
            'old_contract': old_contract.inst_id,
            'new_contract': new_contract.inst_id,
            'size': position_size,
            'roll_cost': 0  # Calculate from execution
        }
```

**Roll Handling Workflow:**
1. **7 days before expiry**: Display warning, start monitoring next contract
2. **3 days before expiry**: Force roll - close current, open in next contract
3. **On roll**: Record the roll basis, calculate any slippage
4. **Expiry day**: Never hold through expiry (settlement risk)

---

## Implementation Priority

### Phase 1: Foundation (Week 1)
1. OKX API client with authentication
2. WebSocket for real-time data
3. SQLite database setup
4. Basic Flask app with dashboard

### Phase 2: Data & Analytics (Week 2)
1. Data collector service
2. Rolling statistics calculation
3. Z-score computation
4. Hurst exponent implementation

### Phase 3: Strategies (Week 3)
1. Funding rate strategy
2. Basis trading strategy
3. Paper trading mode
4. Signal visualization

### Phase 4: Execution & Risk (Week 4)
1. Order execution (paper mode)
2. Position tracking
3. Risk management
4. P&L calculation with funding

### Phase 5: Production Ready (Week 5)
1. Live trading mode
2. Error handling & logging
3. Testing
4. Documentation

---

## Configuration Example

```yaml
# config.yaml
okx:
  demo_trading: true
  rate_limit_per_second: 10

strategies:
  basis:
    enabled: true
    lookback_period: 168        # 7 days in hours
    entry_zscore: 2.0
    exit_zscore: 0.5
    max_position_usd: 10000
    stop_loss_pct: 0.02         # 2% stop loss
    time_stop_hours: 168        # 7 day max hold

  funding:
    enabled: true
    lookback_periods: 21        # 21 funding periods (7 days)
    min_rate_threshold: 0.0005  # 0.05%
    entry_zscore: 2.0
    exit_zscore: 0.5
    max_position_usd: 10000
    min_settlements: 1          # Hold for at least 1 settlement
    max_settlements: 6          # Max 2 days

risk:
  max_total_exposure_usd: 25000
  max_loss_per_trade_usd: 500
  max_daily_loss_usd: 1000
  hurst_threshold: 0.5          # Only trade when Hurst < 0.5

database:
  path: instance/trading.db

logging:
  level: INFO
  file: logs/trading.log
```

---

## Next Steps

1. **Confirm architecture** - Does this structure meet your needs?
2. **API credentials** - Set up OKX API keys (demo mode first)
3. **Begin implementation** - Start with Phase 1 foundation

---

## Sources

- [OKX API v5 Documentation](https://www.okx.com/docs-v5/en/)
- [OKX API Guide](https://www.okx.com/en-us/learn/complete-guide-to-okex-api-v5-upgrade)
- [Funding Fee Mechanism](https://www.okx.com/en-us/help/iv-introduction-to-perpetual-swap-funding-fee)
- [OKX Funding Rate Optimization](https://www.okx.com/help/okx-to-optimize-funding-rate-calculation)
- [OKX Perpetual Futures Guide](https://www.okx.com/en-us/help/i-perpetual-swaps)
