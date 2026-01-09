"""
Streamlit Trading Portal for BTC Basis Trading
Alternative UI to the Flask version (trading_portal.py)

Supports both:
- Streamlit Cloud deployment (uses st.secrets)
- Local development (uses .env file)
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import os
import sys
import threading
from datetime import datetime, timezone
from collections import deque
from dotenv import load_dotenv

# Detect if running on Streamlit Cloud (read-only filesystem)
IS_STREAMLIT_CLOUD = os.environ.get('STREAMLIT_SHARING_MODE') or os.path.exists('/mount/src')

# Load environment variables from .env (for local dev)
load_dotenv()

# Override with Streamlit secrets if available (for cloud deployment)
if hasattr(st, 'secrets') and 'okx' in st.secrets:
    os.environ['OKX_API_KEY'] = st.secrets['okx']['api_key']
    os.environ['OKX_SECRET_KEY'] = st.secrets['okx']['secret_key']
    os.environ['OKX_PASSPHRASE'] = st.secrets['okx']['passphrase']
    os.environ['OKX_DEMO'] = str(st.secrets['okx'].get('demo', True)).lower()

# Configure logging for cloud (disable file logging)
if IS_STREAMLIT_CLOUD:
    from loguru import logger
    logger.remove()  # Remove default handler
    logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>", level="INFO")

# Import shared components from Flask app
from trading_portal import TradingDatabase, TradingMonitor

# Page config
st.set_page_config(
    page_title="BTC-USD Basis",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize database and monitor (cached to persist across reruns)
@st.cache_resource
def init_trading_system():
    """Initialize the trading system (runs once)"""
    # Use /tmp for database on Streamlit Cloud (read-only filesystem)
    db_path = "/tmp/trading_portal.db" if IS_STREAMLIT_CLOUD else "trading_portal.db"
    db = TradingDatabase(db_path)
    monitor = TradingMonitor(db)
    monitor._init_error = None

    # Try to initialize OKX
    try:
        if monitor.initialize_okx():
            monitor.start_background_updates()
    except Exception as e:
        monitor._init_error = str(e)

    return db, monitor

db, monitor = init_trading_system()

# Custom CSS
st.markdown("""
<style>
    .metric-card {
        background: white;
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .big-number {
        font-size: 2rem;
        font-weight: bold;
    }
    .green { color: #27ae60; }
    .red { color: #e74c3c; }
    .yellow { color: #f39c12; }
    .status-connected {
        background: #d4edda;
        color: #155724;
        padding: 5px 10px;
        border-radius: 15px;
        font-weight: bold;
    }
    .status-disconnected {
        background: #f8d7da;
        color: #721c24;
        padding: 5px 10px;
        border-radius: 15px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar - Settings & Controls
with st.sidebar:
    st.title("⚙️ Controls")

    # Connection status
    config = db.get_config()
    account_info = monitor.get_account_info()
    data = monitor.current_data

    connected = monitor.client is not None and data and data.get('spot_price') is not None
    authenticated = account_info and not account_info.get('error')

    if connected and authenticated:
        st.success("🟢 CONNECTED")
    elif connected:
        st.warning("🟡 NO AUTH")
    else:
        st.error("🔴 DISCONNECTED")
        # Debug info
        with st.expander("Debug Info"):
            has_secrets = hasattr(st, 'secrets') and 'okx' in st.secrets
            st.write(f"Secrets loaded: {has_secrets}")
            st.write(f"API Key set: {bool(os.environ.get('OKX_API_KEY'))}")
            st.write(f"Demo mode: {os.environ.get('OKX_DEMO', 'not set')}")
            st.write(f"Client created: {monitor.client is not None}")
            if hasattr(monitor, '_init_error') and monitor._init_error:
                st.error(f"Init error: {monitor._init_error}")

    st.divider()

    # Trading toggles
    col1, col2 = st.columns(2)
    with col1:
        algo_enabled = st.toggle("Algo Trading", value=config.get('algo_enabled', False), key="algo_toggle")
        if algo_enabled != config.get('algo_enabled', False):
            config['algo_enabled'] = algo_enabled
            db.save_config(config)
            monitor.config = config

    with col2:
        paper_mode = st.toggle("Paper Mode", value=config.get('paper_mode', True), key="paper_toggle")
        if paper_mode != config.get('paper_mode', True):
            config['paper_mode'] = paper_mode
            db.save_config(config)
            monitor.config = config
            if monitor.client:
                monitor.client.demo_trading = paper_mode

    st.divider()

    # Quick settings
    st.subheader("Quick Settings")

    futures_symbol = st.text_input("Futures Symbol", value=config.get('futures_symbol', 'BTC-USDT-SWAP'))
    if futures_symbol != config.get('futures_symbol', ''):
        config['futures_symbol'] = futures_symbol
        db.save_config(config)
        monitor.config = config

    lot_size = st.number_input("Lot Size", value=config.get('lot_size', 0.01), min_value=0.001, step=0.001, format="%.3f")
    if lot_size != config.get('lot_size', 0.01):
        config['lot_size'] = lot_size
        db.save_config(config)
        monitor.config = config

    st.divider()

    # Action buttons
    if st.button("🔄 Reset Statistics", use_container_width=True):
        monitor.spread_cache.clear()
        monitor.zscore_history.clear()
        monitor.price_history.clear()
        st.success("Statistics reset!")

    if st.button("🗑️ Clear Trades", use_container_width=True):
        db.clear_trades()
        monitor.positions.clear()
        st.success("Trades cleared!")

    # Auto-refresh (in milliseconds)
    refresh_options = {"300ms": 0.3, "500ms": 0.5, "1s": 1, "2s": 2, "5s": 5}
    refresh_choice = st.selectbox("Refresh Rate", list(refresh_options.keys()), index=0)
    refresh_rate = refresh_options[refresh_choice]

# Main content
st.title("📈 BTC-USD Basis")
st.caption(f"Last update: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

# Extract data
zscore = data.get('zscore') if data else None
signal = data.get('signal', {}) if data else {}
stats = data.get('stats', {}) if data else {}
hurst = data.get('hurst') if data else None
hurst_regime = data.get('hurst_regime', 'UNKNOWN') if data else 'UNKNOWN'

spot_price = data.get('spot_price', 0) if data else 0
spot_bid = data.get('spot_bid', 0) if data else 0
spot_ask = data.get('spot_ask', 0) if data else 0
spot_spread = data.get('spot_spread', 0) if data else 0

futures_price = data.get('futures_price', 0) if data else 0
futures_bid = data.get('futures_bid', 0) if data else 0
futures_ask = data.get('futures_ask', 0) if data else 0
futures_spread = data.get('futures_spread', 0) if data else 0

spread = data.get('spread', 0) if data else 0
futures_symbol = config.get('futures_symbol', 'BTC-USDT-SWAP')
days_to_expiry = data.get('days_to_expiry', '--') if data else '--'

# Main 3-column layout matching Flask version
col1, col2, col3 = st.columns([1, 1.5, 1])

# LEFT: BTC Price Card
with col1:
    st.markdown(f"""
    <div style="background: white; border-radius: 10px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
            <span style="font-size: 1.5rem; font-weight: bold;">{config.get('asset_name', 'BTC')}</span>
            <span style="color: #888;">N/A</span>
        </div>

        <div style="display: flex; justify-content: space-between; margin-bottom: 15px;">
            <div>
                <div style="color: #888; font-size: 0.8rem;">SPOT (MID)</div>
                <div style="font-size: 1.3rem; font-weight: bold;">{spot_price:,.2f}</div>
                <div style="font-size: 0.75rem; color: #888;">
                    <span style="color: #27ae60;">{spot_bid:,.2f}</span> /
                    <span style="color: #e74c3c;">{spot_ask:,.2f}</span>
                    <span>(Δ${spot_spread:.2f})</span>
                </div>
            </div>
            <div>
                <div style="color: #888; font-size: 0.8rem;">FUT ({futures_symbol})</div>
                <div style="font-size: 1.3rem; font-weight: bold;">{futures_price:,.2f}</div>
                <div style="font-size: 0.75rem; color: #888;">
                    <span style="color: #27ae60;">{futures_bid:,.2f}</span> /
                    <span style="color: #e74c3c;">{futures_ask:,.2f}</span>
                    <span>(Δ${futures_spread:.2f})</span>
                </div>
            </div>
        </div>

        <div style="border-top: 1px solid #eee; padding-top: 10px;">
            <div style="display: flex; justify-content: space-between;">
                <span style="color: #888;">Basis (F-S)</span>
                <span style="font-weight: bold; color: {'#e74c3c' if spread < 0 else '#27ae60'};">{spread:.2f}</span>
            </div>
            <div style="display: flex; justify-content: space-between; margin-top: 5px;">
                <span style="color: #888;">Contract:</span>
                <span style="color: #3498db;">{futures_symbol}</span>
            </div>
            <div style="display: flex; justify-content: space-between; margin-top: 5px;">
                <span style="color: #888;">Days to Expiry:</span>
                <span>{days_to_expiry}</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# CENTER: Z-Score Card
with col2:
    signal_type = signal.get('type', 'NO_SIGNAL')

    # Hurst badge color
    if hurst_regime == 'MEAN_REVERTING':
        hurst_bg = "#d4edda"
        hurst_color = "#155724"
    elif hurst_regime == 'TRENDING':
        hurst_bg = "#f8d7da"
        hurst_color = "#721c24"
    else:
        hurst_bg = "#e2e3e5"
        hurst_color = "#383d41"

    # Z-score color
    if zscore is not None:
        if abs(zscore) >= config.get('entry_std_dev', 2.0):
            zscore_color = "#e74c3c" if zscore > 0 else "#27ae60"
        else:
            zscore_color = "#333"
        zscore_display = f"{zscore:.2f}σ"
    else:
        zscore_color = "#888"
        zscore_display = "--"

    # Data progress
    count = stats.get('count', 0)
    required = stats.get('required', 2700)
    min_required = stats.get('min_required', 90)
    has_enough = stats.get('has_enough_data', False)
    pct = min(100, (count / required) * 100) if required > 0 else 0

    if has_enough:
        progress_text = f"✓ Ready ({count} pts) | Building: {pct:.0f}%"
        progress_color = "#3498db"
    else:
        progress_text = f"Collecting: {count}/{min_required} ({min(100, (count/min_required)*100):.0f}%)"
        progress_color = "#f39c12"

    st.markdown(f"""
    <div style="background: white; border: 2px solid {'#f8d7da' if signal_type != 'NO_SIGNAL' else '#ddd'}; border-radius: 10px; padding: 20px; text-align: center;">
        <div style="color: #888; margin-bottom: 5px;">{signal_type}</div>
        <div style="font-size: 3rem; font-weight: bold; color: {zscore_color};">{zscore_display}</div>

        <div style="display: inline-block; padding: 5px 15px; background: {hurst_bg}; color: {hurst_color}; border-radius: 20px; margin: 10px 0;">
            Hurst: {hurst:.3f if hurst else 0:.3f} | {hurst_regime}
        </div>

        <div style="font-size: 0.8rem; color: {progress_color}; margin: 10px 0;">
            {progress_text}
        </div>

        <div style="display: flex; justify-content: space-around; margin-top: 15px; padding-top: 15px; border-top: 1px solid #eee;">
            <div>
                <div style="color: #888; font-size: 0.8rem;">MEAN</div>
                <div style="font-weight: bold;">{stats.get('mean', 0):.2f}</div>
            </div>
            <div>
                <div style="color: #888; font-size: 0.8rem;">STD</div>
                <div style="font-weight: bold;">{stats.get('std', 0):.2f}</div>
            </div>
            <div>
                <div style="color: #888; font-size: 0.8rem;">SPREAD</div>
                <div style="font-weight: bold;">{spread:.2f}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# RIGHT: Entry/Exit Levels
with col3:
    entry_std = config.get('entry_std_dev', 2.0)
    exit_std = config.get('exit_std_dev', 0.2)
    mean = stats.get('mean', 0)
    std = stats.get('std', 1)

    short_entry = mean + (entry_std * std)
    short_exit = mean + (exit_std * std)
    long_entry = mean - (entry_std * std)
    long_exit = mean - (exit_std * std)

    st.markdown(f"""
    <div style="background: white; border-radius: 10px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <div style="background: #f8d7da; padding: 15px; border-radius: 8px; margin-bottom: 10px;">
            <div style="font-weight: bold; color: #721c24; margin-bottom: 10px;">Short Spread</div>
            <div style="display: flex; justify-content: space-between;">
                <span>Entry ↑</span>
                <span style="font-weight: bold;">{short_entry:.2f}</span>
            </div>
            <div style="display: flex; justify-content: space-between;">
                <span>Exit</span>
                <span style="font-weight: bold;">{short_exit:.2f}</span>
            </div>
        </div>

        <div style="background: #d4edda; padding: 15px; border-radius: 8px;">
            <div style="font-weight: bold; color: #155724; margin-bottom: 10px;">Long Spread</div>
            <div style="display: flex; justify-content: space-between;">
                <span>Entry ↓</span>
                <span style="font-weight: bold;">{long_entry:.2f}</span>
            </div>
            <div style="display: flex; justify-content: space-between;">
                <span>Exit</span>
                <span style="font-weight: bold;">{long_exit:.2f}</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# Account info row
st.divider()
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Account Balance", f"${account_info.get('balance', 0):,.2f}")
with col2:
    st.metric("Equity", f"${account_info.get('equity', 0):,.2f}")
with col3:
    st.metric("Free Margin", f"${account_info.get('free_margin', 0):,.2f}")
with col4:
    st.metric("Leverage", f"1:{account_info.get('leverage', 1)}")

st.divider()

# Charts
st.subheader("📊 Charts")

tab1, tab2 = st.tabs(["Z-Score History", "Price History"])

with tab1:
    zscore_history = list(monitor.zscore_history)
    if zscore_history:
        df_zscore = pd.DataFrame(zscore_history)

        fig = go.Figure()

        # Z-score line
        fig.add_trace(go.Scatter(
            x=df_zscore['time'],
            y=df_zscore['zscore'],
            mode='lines',
            name='Z-Score',
            line=dict(color='blue', width=2)
        ))

        # Threshold lines
        entry_std = config.get('entry_std_dev', 2.0)
        exit_std = config.get('exit_std_dev', 0.2)

        fig.add_hline(y=entry_std, line_dash="dash", line_color="red", annotation_text=f"Entry +{entry_std}σ")
        fig.add_hline(y=-entry_std, line_dash="dash", line_color="green", annotation_text=f"Entry -{entry_std}σ")
        fig.add_hline(y=exit_std, line_dash="dot", line_color="orange", annotation_text=f"Exit +{exit_std}σ")
        fig.add_hline(y=-exit_std, line_dash="dot", line_color="orange", annotation_text=f"Exit -{exit_std}σ")
        fig.add_hline(y=0, line_color="gray")

        fig.update_layout(
            height=400,
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis_title="Time",
            yaxis_title="Z-Score (σ)"
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No Z-score history yet. Waiting for data...")

with tab2:
    price_history = list(monitor.price_history)
    if price_history:
        df_price = pd.DataFrame(price_history)

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                           vertical_spacing=0.1,
                           row_heights=[0.7, 0.3])

        # Prices
        fig.add_trace(go.Scatter(x=df_price['time'], y=df_price['spot_price'], name='Spot', line=dict(color='blue')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_price['time'], y=df_price['futures_price'], name='Futures', line=dict(color='orange')), row=1, col=1)

        # Spread
        fig.add_trace(go.Scatter(x=df_price['time'], y=df_price['spread'], name='Spread', line=dict(color='purple'), fill='tozeroy'), row=2, col=1)

        fig.update_layout(
            height=500,
            margin=dict(l=0, r=0, t=30, b=0)
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No price history yet. Waiting for data...")

st.divider()

# Positions and Trade History
col1, col2 = st.columns(2)

with col1:
    st.subheader("📍 Open Positions")
    positions = monitor.get_enriched_positions()
    if positions:
        for pos in positions:
            with st.container():
                st.markdown(f"""
                **{pos.get('asset', 'BTC')}** - {pos.get('direction', '')}
                Entry: ${pos.get('entry_spot_price', 0):,.2f} / ${pos.get('entry_futures_price', 0):,.2f}
                P&L: ${pos.get('unrealized_pnl', 0):,.2f}
                """)
    else:
        st.info("No open positions")

with col2:
    st.subheader("📜 Recent Trades")
    trades = db.get_trades(limit=5)
    if trades:
        for trade in trades:
            status_icon = "✅" if trade.get('status') == 'CLOSED' else "🔄"
            pnl = trade.get('net_pnl', 0)
            pnl_color = "green" if pnl >= 0 else "red"

            st.markdown(f"""
            {status_icon} **{trade.get('direction', '')}** - {trade.get('entry_date', '')[:10]}
            <span style="color: {pnl_color};">P&L: ${pnl:,.2f}</span>
            """, unsafe_allow_html=True)
    else:
        st.info("No trade history")

# Trade summary
st.divider()
st.subheader("📊 Trade Summary")
summary = db.get_trade_summary()

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Trades", summary.get('total_trades', 0))
with col2:
    st.metric("Win Rate", f"{summary.get('win_rate', 0):.1f}%")
with col3:
    total_pnl = summary.get('total_pnl', 0)
    st.metric("Total P&L", f"${total_pnl:,.2f}", delta_color="normal" if total_pnl >= 0 else "inverse")
with col4:
    st.metric("Avg P&L", f"${summary.get('avg_pnl', 0):,.2f}")

# Auto-refresh
time.sleep(refresh_rate)
st.rerun()
