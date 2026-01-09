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
    page_title="BTC Basis Trading Portal",
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

    # Auto-refresh
    refresh_rate = st.selectbox("Refresh Rate", [1, 2, 5, 10], index=0)

# Main content
st.title("📈 BTC Basis Trading Portal")
st.caption(f"Last update: {datetime.now().strftime('%H:%M:%S')}")

# Top metrics row
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric(
        "Account Balance",
        f"${account_info.get('balance', 0):,.2f}",
        delta=None
    )

with col2:
    st.metric(
        "Free Margin",
        f"${account_info.get('free_margin', 0):,.2f}",
        delta=None
    )

with col3:
    spot_price = data.get('spot_price', 0) if data else 0
    st.metric("Spot Price", f"${spot_price:,.2f}")

with col4:
    futures_price = data.get('futures_price', 0) if data else 0
    st.metric("Futures Price", f"${futures_price:,.2f}")

with col5:
    spread = data.get('spread', 0) if data else 0
    spread_pct = data.get('spread_pct', 0) if data else 0
    st.metric("Basis", f"${spread:,.2f}", delta=f"{spread_pct:.3f}%")

st.divider()

# Z-Score and Signal section
col1, col2 = st.columns([2, 1])

with col1:
    zscore = data.get('zscore') if data else None
    signal = data.get('signal', {}) if data else {}
    stats = data.get('stats', {}) if data else {}

    if zscore is not None:
        # Color based on Z-score
        if abs(zscore) >= 2:
            zscore_color = "red" if zscore > 0 else "green"
        else:
            zscore_color = "gray"

        st.markdown(f"""
        <div style="text-align: center; padding: 20px; background: #f8f9fa; border-radius: 10px;">
            <div style="font-size: 3rem; font-weight: bold; color: {zscore_color};">{zscore:.2f}σ</div>
            <div style="font-size: 1.2rem; color: #666;">Z-Score</div>
            <div style="margin-top: 10px; font-size: 1rem; color: #888;">
                {signal.get('type', 'NO_SIGNAL')} - {signal.get('reason', '')}
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        count = stats.get('count', 0)
        required = stats.get('min_required', 90)
        pct = min(100, (count / required) * 100) if required > 0 else 0

        st.markdown(f"""
        <div style="text-align: center; padding: 20px; background: #fff3cd; border-radius: 10px;">
            <div style="font-size: 2rem; font-weight: bold; color: #856404;">Collecting Data</div>
            <div style="font-size: 1.2rem; color: #666;">{count} / {required} points ({pct:.0f}%)</div>
        </div>
        """, unsafe_allow_html=True)
        st.progress(pct / 100)

with col2:
    hurst = data.get('hurst') if data else None
    hurst_regime = data.get('hurst_regime', 'UNKNOWN') if data else 'UNKNOWN'

    if hurst is not None:
        if hurst_regime == 'MEAN_REVERTING':
            hurst_color = "#27ae60"
        elif hurst_regime == 'TRENDING':
            hurst_color = "#e74c3c"
        else:
            hurst_color = "#666"

        st.markdown(f"""
        <div style="text-align: center; padding: 20px; background: #f8f9fa; border-radius: 10px;">
            <div style="font-size: 2rem; font-weight: bold; color: {hurst_color};">{hurst:.3f}</div>
            <div style="font-size: 1rem; color: #666;">Hurst Exponent</div>
            <div style="margin-top: 5px; padding: 5px 10px; background: {hurst_color}; color: white; border-radius: 5px; display: inline-block;">
                {hurst_regime}
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Hurst: Calculating...")

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
        fig.add_trace(go.Scatter(x=df_price['time'], y=df_price['spot'], name='Spot', line=dict(color='blue')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_price['time'], y=df_price['futures'], name='Futures', line=dict(color='orange')), row=1, col=1)

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
