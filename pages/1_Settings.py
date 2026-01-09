"""
Settings Page for Streamlit Trading Portal
"""

import streamlit as st
from trading_portal import TradingDatabase, TradingMonitor

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")

# Initialize (reuse from main app)
@st.cache_resource
def get_db():
    return TradingDatabase()

@st.cache_resource
def get_monitor(_db):
    monitor = TradingMonitor(_db)
    if monitor.initialize_okx():
        monitor.start_background_updates()
    return monitor

db = get_db()
monitor = get_monitor(db)
config = db.get_config()

st.title("⚙️ Trading Settings")

# Asset Configuration
st.header("Asset Configuration")
col1, col2 = st.columns(2)

with col1:
    asset_name = st.text_input("Asset Name", value=config.get('asset_name', 'BTC'))
    spot_symbol = st.text_input("Spot Symbol", value=config.get('spot_symbol', 'BTC-USDT'))

with col2:
    # Futures dropdown
    futures_options = ['BTC-USDT-SWAP']

    # Try to get more options from OKX
    if monitor.client:
        try:
            futures = monitor.client.get_instruments('FUTURES', 'BTC-USDT')
            if futures:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                for f in futures:
                    if f.expiry_time and f.expiry_time > now:
                        days = (f.expiry_time - now).days
                        futures_options.append(f.inst_id)
        except:
            pass

    current_futures = config.get('futures_symbol', 'BTC-USDT-SWAP')
    if current_futures and current_futures not in futures_options:
        futures_options.insert(0, current_futures)

    futures_symbol = st.selectbox("Futures Contract", futures_options,
                                  index=futures_options.index(current_futures) if current_futures in futures_options else 0)

    contract_size = st.number_input("Contract Size", value=float(config.get('contract_size', 1)), min_value=0.001, step=0.001)

st.divider()

# Statistical Parameters
st.header("Statistical Parameters")
col1, col2, col3 = st.columns(3)

with col1:
    lookback_period = st.number_input("Lookback Period", value=int(config.get('lookback_period', 90)), min_value=1)
    lookback_unit = st.selectbox("Lookback Unit", ["minutes", "days"],
                                 index=0 if config.get('lookback_unit', 'minutes') == 'minutes' else 1)

with col2:
    entry_std = st.number_input("Entry Std Dev (σ)", value=float(config.get('entry_std_dev', 2.0)), min_value=0.1, step=0.1)
    exit_std = st.number_input("Exit Std Dev (σ)", value=float(config.get('exit_std_dev', 0.2)), min_value=0.1, step=0.1)

with col3:
    stop_loss_std = st.number_input("Stop Loss Std Dev (σ)", value=float(config.get('stop_loss_std_dev', 6.0)), min_value=0.1, step=0.1)
    points_per_minute = st.selectbox("Points Per Minute", [1, 6, 12, 30, 60],
                                     index=[1, 6, 12, 30, 60].index(config.get('points_per_minute', 1)) if config.get('points_per_minute', 1) in [1, 6, 12, 30, 60] else 0)

st.divider()

# Risk Management
st.header("Risk Management")
col1, col2 = st.columns(2)

with col1:
    lot_size = st.number_input("Lot Size", value=float(config.get('lot_size', 0.01)), min_value=0.001, step=0.001, format="%.3f")
    max_positions = st.number_input("Max Positions", value=int(config.get('max_positions', 1)), min_value=1)
    min_balance = st.number_input("Min Balance to Trade ($)", value=float(config.get('min_balance_to_trade', 10)), min_value=0.0, step=1.0)

with col2:
    min_profit = st.number_input("Min Profit Per Lot ($)", value=float(config.get('min_profit_per_lot', 10)), min_value=0.0, step=1.0)
    max_loss = st.number_input("Max Loss Per Lot ($)", value=float(config.get('max_loss_per_lot', 50)), min_value=0.0, step=1.0)
    commission = st.number_input("Commission Per Lot ($)", value=float(config.get('commission_per_lot', 0)), min_value=0.0, step=0.01)

st.divider()

# Hurst Filter
st.header("Hurst Filter")
col1, col2, col3 = st.columns(3)

with col1:
    hurst_enabled = st.checkbox("Enable Hurst Filter", value=config.get('hurst_enabled', True))

with col2:
    hurst_threshold = st.number_input("Hurst Threshold", value=float(config.get('hurst_threshold', 0.5)), min_value=0.0, max_value=1.0, step=0.05)

with col3:
    trending_duration = st.number_input("Trending Duration (min)", value=int(config.get('trending_duration_minutes', 20)), min_value=1)

st.divider()

# Save button
if st.button("💾 Save Settings", type="primary", use_container_width=True):
    # Update config
    config['asset_name'] = asset_name
    config['spot_symbol'] = spot_symbol
    config['futures_symbol'] = futures_symbol
    config['contract_size'] = contract_size
    config['lookback_period'] = lookback_period
    config['lookback_unit'] = lookback_unit
    config['entry_std_dev'] = entry_std
    config['exit_std_dev'] = exit_std
    config['stop_loss_std_dev'] = stop_loss_std
    config['points_per_minute'] = points_per_minute
    config['lot_size'] = lot_size
    config['max_positions'] = max_positions
    config['min_balance_to_trade'] = min_balance
    config['min_profit_per_lot'] = min_profit
    config['max_loss_per_lot'] = max_loss
    config['commission_per_lot'] = commission
    config['hurst_enabled'] = hurst_enabled
    config['hurst_threshold'] = hurst_threshold
    config['trending_duration_minutes'] = trending_duration

    db.save_config(config)
    monitor.config = config

    st.success("✅ Settings saved successfully!")
    st.balloons()

st.divider()

# Test Orders section
st.header("🧪 Test Exchange Connection")
st.caption("Test that orders can be placed and cancelled on OKX")

if st.button("🔌 Test Spot & Futures Orders", use_container_width=True):
    with st.spinner("Testing orders..."):
        import time as t

        results = {'spot': None, 'futures': None}

        if not monitor.client:
            st.error("OKX client not initialized")
        else:
            # Get current price
            try:
                spot_ticker = monitor.client.get_ticker(spot_symbol)
                spot_price = spot_ticker.last_price if spot_ticker else 50000
            except:
                spot_price = 50000

            test_price = round(spot_price * 0.5, 1)  # 50% below market

            # Test spot order
            try:
                place_response = monitor.client.place_order(
                    inst_id=spot_symbol,
                    side='buy',
                    order_type='limit',
                    size=0.0001,
                    price=test_price,
                    trade_mode='cash'
                )

                if place_response.get('code') == '0' and place_response.get('data'):
                    order_id = place_response['data'][0].get('ordId')
                    t.sleep(0.3)
                    cancel_response = monitor.client.cancel_order(inst_id=spot_symbol, order_id=order_id)

                    if cancel_response.get('code') == '0':
                        st.success(f"✅ SPOT: Order placed and cancelled successfully")
                    else:
                        st.warning(f"⚠️ SPOT: Order placed but cancel failed")
                else:
                    st.error(f"❌ SPOT: Order failed - {place_response.get('msg', 'Unknown error')}")
            except Exception as e:
                st.error(f"❌ SPOT: {str(e)}")

            # Test futures order
            if futures_symbol:
                try:
                    fut_ticker = monitor.client.get_ticker(futures_symbol)
                    fut_price = fut_ticker.last_price if fut_ticker else spot_price
                    fut_test_price = round(fut_price * 0.5, 1)

                    place_response = monitor.client.place_order(
                        inst_id=futures_symbol,
                        side='buy',
                        order_type='limit',
                        size=1,
                        price=fut_test_price,
                        trade_mode='cross'
                    )

                    if place_response.get('code') == '0' and place_response.get('data'):
                        order_id = place_response['data'][0].get('ordId')
                        t.sleep(0.3)
                        cancel_response = monitor.client.cancel_order(inst_id=futures_symbol, order_id=order_id)

                        if cancel_response.get('code') == '0':
                            st.success(f"✅ FUTURES: Order placed and cancelled successfully")
                        else:
                            st.warning(f"⚠️ FUTURES: Order placed but cancel failed")
                    else:
                        st.error(f"❌ FUTURES: Order failed - {place_response.get('msg', 'Unknown error')}")
                except Exception as e:
                    st.error(f"❌ FUTURES: {str(e)}")
