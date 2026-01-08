# Funding Rate System Conversion Guide

This guide marks all sections in `trading_portal.py` that need to be modified for funding rate trading.

---

## 1. DATABASE SCHEMA CHANGES (Lines ~50-130)

### price_history table → funding_history table
```python
# CHANGE FROM:
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    asset TEXT NOT NULL,
    spot_price REAL,
    futures_price REAL,
    spread REAL,
    swap_diff REAL
)

# CHANGE TO:
CREATE TABLE IF NOT EXISTS funding_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    asset TEXT NOT NULL,
    funding_rate REAL,
    funding_time TEXT,
    next_funding_rate REAL,
    swap_price REAL,
    annualized_rate REAL
)
```

### trading_config table changes
```python
# REMOVE these fields:
spot_symbol TEXT DEFAULT 'BTC-USDT',
futures_symbol TEXT DEFAULT '',
futures_expiry TEXT DEFAULT '',
points_per_minute INTEGER DEFAULT 1,

# ADD these fields:
swap_symbol TEXT DEFAULT 'BTC-USDT-SWAP',
lookback_unit TEXT DEFAULT 'periods',  -- 8-hour funding periods

# KEEP these fields (same logic):
lookback_period INTEGER DEFAULT 90,  -- 90 funding periods = 30 days
entry_std_dev REAL DEFAULT 2.0,
exit_std_dev REAL DEFAULT 0.2,
stop_loss_std_dev REAL DEFAULT 6.0,
```

### trades table changes
```python
# CHANGE FROM:
entry_spot_price REAL,
entry_futures_price REAL,
exit_spot_price REAL,
exit_futures_price REAL,
spot_pnl REAL DEFAULT 0,
futures_pnl REAL DEFAULT 0,

# CHANGE TO:
entry_swap_price REAL,
exit_swap_price REAL,
entry_funding_rate REAL,
exit_funding_rate REAL,
price_pnl REAL DEFAULT 0,
funding_pnl REAL DEFAULT 0,  -- Total funding received/paid
funding_periods_held INTEGER DEFAULT 0,
```

---

## 2. DATA CACHES (Lines ~427-430)

```python
# CHANGE FROM:
self.spread_cache = deque(maxlen=2000)

# CHANGE TO:
self.funding_cache = deque(maxlen=500)  # 500 funding periods = ~166 days
```

---

## 3. OKX CLIENT - Add Funding Rate Methods

Add to `app/api/okx_client.py`:
```python
def get_funding_rate(self, inst_id: str) -> Optional[Dict]:
    """Get current funding rate for a swap instrument"""
    result = self._request('GET', '/api/v5/public/funding-rate', {'instId': inst_id})
    if result.get('code') == '0' and result.get('data'):
        d = result['data'][0]
        return {
            'inst_id': d['instId'],
            'funding_rate': float(d['fundingRate']),
            'next_funding_rate': float(d.get('nextFundingRate', 0)),
            'funding_time': datetime.fromtimestamp(int(d['fundingTime']) / 1000, tz=timezone.utc),
            'next_funding_time': datetime.fromtimestamp(int(d.get('nextFundingTime', 0)) / 1000, tz=timezone.utc)
        }
    return None

def get_funding_rate_history(self, inst_id: str, limit: int = 100) -> List[Dict]:
    """Get historical funding rates"""
    result = self._request('GET', '/api/v5/public/funding-rate-history', {
        'instId': inst_id,
        'limit': str(min(limit, 100))
    })
    rates = []
    if result.get('code') == '0' and result.get('data'):
        for d in result['data']:
            rates.append({
                'funding_rate': float(d['fundingRate']),
                'funding_time': datetime.fromtimestamp(int(d['fundingTime']) / 1000, tz=timezone.utc),
                'realized_rate': float(d.get('realizedRate', d['fundingRate']))
            })
    return list(reversed(rates))  # Oldest first
```

---

## 4. GET MARKET DATA (Lines ~580-690)

### CHANGE FROM (basis calculation):
```python
def get_market_data(self) -> Dict:
    # Fetches spot_price, futures_price
    # Calculates spread = futures_price - spot_price
    # Returns spread, spot_price, futures_price
```

### CHANGE TO (funding rate):
```python
def get_market_data(self) -> Dict:
    """Get current swap price and funding rate"""
    swap_symbol = self.config.get('swap_symbol', 'BTC-USDT-SWAP')

    # Get swap ticker
    swap_ticker = self.client.get_ticker(swap_symbol)
    swap_price = swap_ticker.last_price if swap_ticker else 0

    # Get current funding rate
    funding_data = self.client.get_funding_rate(swap_symbol)
    funding_rate = funding_data['funding_rate'] if funding_data else 0
    next_funding_rate = funding_data.get('next_funding_rate', 0)
    next_funding_time = funding_data.get('next_funding_time')

    # Calculate time until next funding
    if next_funding_time:
        time_to_funding = next_funding_time - datetime.now(timezone.utc)
        hours_to_funding = time_to_funding.total_seconds() / 3600

    # Annualized rate = funding_rate * 3 * 365 (3 times per day)
    annualized_rate = funding_rate * 3 * 365 * 100  # As percentage

    # Calculate Z-score
    zscore, stats = self.calculate_zscore(funding_rate)

    return {
        'asset': asset_name,
        'swap_symbol': swap_symbol,
        'swap_price': swap_price,
        'funding_rate': funding_rate,
        'funding_rate_pct': funding_rate * 100,  # As percentage
        'next_funding_rate': next_funding_rate,
        'next_funding_time': next_funding_time.isoformat() if next_funding_time else None,
        'time_to_funding_hours': hours_to_funding,
        'annualized_rate': annualized_rate,
        'zscore': zscore,
        'stats': stats,
        ...
    }
```

---

## 5. CALCULATE Z-SCORE (Lines ~766-810)

```python
# CHANGE FROM:
def calculate_zscore(self, current_spread: float) -> Tuple[Optional[float], Dict]:
    spreads = list(self.spread_cache)

# CHANGE TO:
def calculate_zscore(self, current_funding_rate: float) -> Tuple[Optional[float], Dict]:
    """Calculate Z-score of funding rate"""
    lookback = self.config.get('lookback_period', 90)  # 90 funding periods

    # Each period is 8 hours, so 90 periods = 30 days
    required = lookback

    rates = list(self.funding_cache)
    count = len(rates)

    # ... same z-score math, just on funding rates instead of spreads
```

---

## 6. SIGNAL GENERATION (Lines ~820-910)

### CHANGE FROM (basis signals):
```python
# Basis expensive → Short spread (sell futures, buy spot)
if zscore >= entry_std:
    signal = {'type': 'SELL_BASIS', 'action': 'Short Spread'}
# Basis cheap → Long spread (buy futures, sell spot)
elif zscore <= -entry_std:
    signal = {'type': 'BUY_BASIS', 'action': 'Long Spread'}
```

### CHANGE TO (funding signals):
```python
# Funding HIGH (positive) → Go SHORT to receive funding
if zscore >= entry_std:
    signal = {
        'type': 'SHORT_SWAP',
        'reason': f'Funding rate HIGH: {funding_rate*100:.4f}% (z={zscore:.2f}σ)',
        'action': 'SHORT',
        'expected_income': 'Receive funding payments'
    }
# Funding LOW (negative) → Go LONG to receive funding
elif zscore <= -entry_std:
    signal = {
        'type': 'LONG_SWAP',
        'reason': f'Funding rate LOW: {funding_rate*100:.4f}% (z={zscore:.2f}σ)',
        'action': 'LONG',
        'expected_income': 'Receive funding payments'
    }
```

---

## 7. POSITION MANAGEMENT (Lines ~950-1050)

### CHANGE FROM (two-leg spread):
```python
def _open_position(self, signal, data):
    # Opens BOTH spot and futures positions
    # spot_order = buy/sell spot
    # futures_order = sell/buy futures
```

### CHANGE TO (single-leg swap):
```python
def _open_position(self, signal, data):
    """Open a single swap position"""
    swap_symbol = self.config.get('swap_symbol', 'BTC-USDT-SWAP')
    direction = signal['action']  # 'LONG' or 'SHORT'

    if not self.config.get('paper_mode'):
        # Real order - single swap position
        side = 'buy' if direction == 'LONG' else 'sell'
        order = self.client.place_order(
            inst_id=swap_symbol,
            side=side,
            order_type='market',
            size=lot_size,
            td_mode='cross'  # Cross margin for swaps
        )

    # Track position
    self.positions[asset_name] = {
        'direction': direction,
        'entry_swap_price': data['swap_price'],
        'entry_funding_rate': data['funding_rate'],
        'entry_zscore': data['zscore'],
        'funding_payments': 0,  # Track accumulated funding
        'funding_periods': 0,
        ...
    }
```

---

## 8. P&L CALCULATION

### CHANGE FROM:
```python
spot_pnl = (exit_spot - entry_spot) * lot_size
futures_pnl = (entry_futures - exit_futures) * lot_size  # Inverted for short
gross_pnl = spot_pnl + futures_pnl
```

### CHANGE TO:
```python
# Price P&L
if direction == 'LONG':
    price_pnl = (exit_price - entry_price) * lot_size * contract_value
else:  # SHORT
    price_pnl = (entry_price - exit_price) * lot_size * contract_value

# Funding P&L (accumulated during hold)
funding_pnl = position['funding_payments']

# Total P&L
gross_pnl = price_pnl + funding_pnl
```

---

## 9. BOOTSTRAP HISTORICAL DATA (Lines ~507-565)

### CHANGE FROM:
```python
def _bootstrap_from_okx(self, points_needed):
    # Fetches spot candles and futures candles
    # Calculates spread from close prices
```

### CHANGE TO:
```python
def _bootstrap_from_okx(self):
    """Fetch historical funding rates from OKX"""
    swap_symbol = self.config.get('swap_symbol', 'BTC-USDT-SWAP')
    lookback = self.config.get('lookback_period', 90)

    # OKX provides up to 100 funding rate history records per call
    # May need multiple calls for full lookback
    rates = self.client.get_funding_rate_history(swap_symbol, limit=100)

    for rate in rates:
        self.funding_cache.append(rate['funding_rate'])
        self.db.save_funding(
            asset_name,
            rate['funding_rate'],
            rate['funding_time'].isoformat()
        )

    logger.info(f"Bootstrapped {len(rates)} historical funding rates")
```

---

## 10. HTML TEMPLATE CHANGES

### Asset Panel - CHANGE FROM:
```html
<div class="price-grid">
    <div class="price-item">
        <label>SPOT</label>
        <div class="value" id="spot-price">0.00</div>
    </div>
    <div class="price-item">
        <label>FUTURES</label>
        <div class="value" id="futures-price">0.00</div>
    </div>
</div>
<div class="basis-section">
    <span>Basis (F-S)</span>
    <span id="basis-value">$0.00</span>
</div>
```

### CHANGE TO:
```html
<div class="price-grid">
    <div class="price-item">
        <label>SWAP PRICE</label>
        <div class="value" id="swap-price">0.00</div>
    </div>
    <div class="price-item">
        <label>FUNDING RATE</label>
        <div class="value" id="funding-rate">0.0000%</div>
    </div>
    <div class="price-item">
        <label>ANNUALIZED</label>
        <div class="value" id="annualized-rate">0.00%</div>
    </div>
    <div class="price-item">
        <label>NEXT FUNDING</label>
        <div class="value" id="next-funding-countdown">--:--:--</div>
    </div>
</div>
<div class="funding-section">
    <span>Funding Status</span>
    <span id="funding-status" class="funding-positive">POSITIVE</span>
</div>
```

### Entry Levels - CHANGE FROM:
```html
<div class="entry-box entry-short">
    <div class="entry-title">Short Spread</div>
    <span>Entry ↑</span>
    <span id="short-entry">0.00</span>
</div>
```

### CHANGE TO:
```html
<div class="entry-box entry-short">
    <div class="entry-title">SHORT (Receive Funding)</div>
    <div>Entry: Funding ≥ <span id="short-entry">0.0000%</span></div>
    <div>Z-Score ≥ +2.0σ</div>
</div>
<div class="entry-box entry-long">
    <div class="entry-title">LONG (Receive Funding)</div>
    <div>Entry: Funding ≤ <span id="long-entry">0.0000%</span></div>
    <div>Z-Score ≤ -2.0σ</div>
</div>
```

---

## 11. CHART CHANGES

### Z-Score Chart - Same structure, just rename:
- "BTC Z-Score" → "Funding Rate Z-Score"
- Y-axis still shows z-score values

### Price Chart - CHANGE FROM:
```javascript
datasets: [
    { label: 'Spot Price', data: [], ... },
    { label: 'Futures Price', data: [], ... },
    { label: 'Spread (F-S)', data: [], ... }
]
```

### CHANGE TO:
```javascript
datasets: [
    { label: 'Funding Rate %', data: [], borderColor: '#3498db', ... },
    { label: 'Mean', data: [], borderColor: '#888', borderDash: [5,5], ... },
    { label: '+2σ', data: [], borderColor: '#e74c3c', borderDash: [2,2], ... },
    { label: '-2σ', data: [], borderColor: '#27ae60', borderDash: [2,2], ... }
]
```

---

## 12. SETTINGS PAGE CHANGES

### Remove:
- Spot Symbol
- Futures Symbol
- Futures Expiry
- Points Per Minute
- Contract Size (use OKX default)

### Change:
- "Lookback Period" → "Lookback Periods (8hr each)"
- "Lookback Unit" → Remove or simplify (always 8hr periods)

### Add:
- Swap Symbol: BTC-USDT-SWAP (dropdown with common swaps)

---

## 13. TRADE JOURNAL COLUMNS

### CHANGE FROM:
| Direction | Entry | Exit | Days | Spot P&L | Fut P&L | Net P&L |

### CHANGE TO:
| Direction | Entry Rate | Exit Rate | Periods | Price P&L | Funding P&L | Net P&L |

---

## SUMMARY OF KEY CONCEPTUAL CHANGES

| Basis System | Funding Rate System |
|--------------|---------------------|
| Two instruments (Spot + Futures) | One instrument (Swap) |
| Spread = Futures - Spot | Signal = Funding Rate |
| Mean reversion of price spread | Mean reversion of funding rate |
| Profit from convergence | Profit from rate normalization + funding income |
| Lookback: minutes/days | Lookback: 8-hour periods |
| Bootstrap: price candles | Bootstrap: funding rate history |
| Position: both legs | Position: single swap |

