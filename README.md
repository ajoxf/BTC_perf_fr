# BTC Basis & Funding Rate Trading System

A Python-based trading system for OKX exchange implementing two mean-reversion arbitrage strategies:

1. **BTC Futures vs Spot Basis Trading** - Capture basis convergence as futures approach expiry
2. **BTC Perpetual Funding Rate Arbitrage** - Collect funding payments from extreme funding rates

## Features

- Real-time market data via OKX WebSocket
- Z-score based entry/exit signals
- Hurst exponent for regime detection (mean-reverting vs trending)
- Paper trading mode for testing
- Web dashboard for monitoring
- Risk management with position limits and stop losses
- SQLite database for trade persistence

## Quick Start

### 1. Clone and Install

```bash
cd BTC_perf_fr
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure API Credentials

```bash
cp .env.example .env
# Edit .env with your OKX API credentials
```

### 3. Initialize Database

```bash
python run.py init
```

### 4. Run the System

**Web Interface Only:**
```bash
python run.py web --port 5000
```

**Trading Engine Only:**
```bash
python run.py trade  # Paper trading mode
```

**Full System (Web + Trading):**
```bash
python run.py run --port 5000
```

Visit `http://localhost:5000` to access the dashboard.

## Configuration

Edit `config.yaml` to customize:

```yaml
strategies:
  basis:
    entry_zscore: 2.0      # Enter when basis z-score > 2
    exit_zscore: 0.5       # Exit when z-score < 0.5
    max_position_usd: 10000

  funding:
    min_rate_threshold: 0.0005  # 0.05% minimum funding
    entry_zscore: 2.0
    max_settlements: 6     # Hold for max 2 days

risk:
  max_total_exposure_usd: 25000
  max_daily_loss_usd: 1000
  hurst_threshold: 0.5     # Only trade in mean-reverting regime
```

## Strategies

### Basis Trading

- Monitors spread between BTC spot and quarterly futures
- Enters when basis deviates significantly from mean (z-score > 2)
- Exits when basis reverts (z-score < 0.5)
- Handles contract rolls approaching expiry

### Funding Rate Arbitrage

- Monitors BTC perpetual funding rates (settled every 8h)
- Enters when funding is extreme (z-score > 2)
- Collects funding by holding spot+perp hedge
- Exits when funding normalizes

## Project Structure

```
BTC_perf_fr/
├── app/
│   ├── api/           # OKX API client
│   ├── analytics/     # Z-score, Hurst exponent
│   ├── strategies/    # Basis & Funding strategies
│   ├── execution/     # Order management
│   ├── risk/          # Risk management
│   ├── data/          # Data collection
│   ├── routes/        # Flask routes
│   └── templates/     # Web UI templates
├── config.yaml        # Configuration
├── run.py            # Entry point
└── requirements.txt
```

## API Documentation

See [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md) for detailed API documentation and system architecture.

## Risk Warning

This software is for educational purposes. Cryptocurrency trading carries significant risk. Always start with paper trading and never risk more than you can afford to lose.

## License

MIT License
