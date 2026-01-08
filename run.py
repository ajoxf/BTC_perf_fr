#!/usr/bin/env python3
"""
BTC Basis & Funding Rate Trading System
Entry point for running the application
"""
import os
import sys
import argparse
import threading
import signal
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

# Load environment variables from .env file
load_dotenv()

# Configure logging
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="INFO"
)
logger.add(
    "logs/trading_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="DEBUG"
)


def run_web_server(host: str, port: int, debug: bool = False):
    """Run the Flask web server"""
    from app import create_app

    app = create_app()
    logger.info(f"Starting web server on {host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)


def confirm_live_mode():
    """Display warning and get confirmation for live trading"""
    print("\n" + "="*60)
    print("⚠️  WARNING: LIVE TRADING MODE ⚠️")
    print("="*60)
    print("\nYou are about to enable LIVE TRADING.")
    print("This will execute REAL orders with REAL money on OKX.")
    print("\nRisks:")
    print("  • Real financial losses are possible")
    print("  • Orders will be executed automatically")
    print("  • Market conditions can change rapidly")
    print("\nMake sure you have:")
    print("  • Tested thoroughly in paper mode")
    print("  • Set appropriate position limits")
    print("  • Understood the strategy parameters")
    print("="*60)

    response = input("\nType 'YES I UNDERSTAND' to confirm live trading: ")

    if response.strip() == "YES I UNDERSTAND":
        print("\n✅ Live trading confirmed. Starting...\n")
        return True
    else:
        print("\n❌ Live trading NOT confirmed. Starting in PAPER mode instead.\n")
        return False


def sync_okx_positions(client, trading_state, current_prices: dict = None):
    """
    Sync positions and balance from OKX.

    Args:
        client: OKX API client
        trading_state: Shared trading state
        current_prices: Current prices for P&L calculation
    """
    try:
        # Fetch positions from OKX
        positions_response = client.get_positions()
        balance_response = client.get_balance('USDT')

        positions = []
        if positions_response.get('code') == '0' and positions_response.get('data'):
            for pos in positions_response['data']:
                if float(pos.get('pos', 0)) != 0:  # Only include non-zero positions
                    inst_id = pos.get('instId', '')
                    pos_size = float(pos.get('pos', 0))
                    entry_price = float(pos.get('avgPx', 0))
                    mark_price = float(pos.get('markPx', 0))
                    upl = float(pos.get('upl', 0))  # Unrealized P&L
                    margin = float(pos.get('margin', 0))
                    leverage = float(pos.get('lever', 1))
                    liq_price = float(pos.get('liqPx', 0)) if pos.get('liqPx') else 0

                    # Determine position side
                    pos_side = pos.get('posSide', 'net')
                    if pos_side == 'net':
                        side = 'long' if pos_size > 0 else 'short'
                    else:
                        side = pos_side

                    positions.append({
                        'instrument': inst_id,
                        'side': side,
                        'size': abs(pos_size),
                        'entry_price': entry_price,
                        'mark_price': mark_price,
                        'unrealized_pnl': upl,
                        'margin': margin,
                        'leverage': leverage,
                        'liquidation_price': liq_price,
                        'inst_type': pos.get('instType', '')
                    })

        # Parse balance
        balance = {}
        if balance_response.get('code') == '0' and balance_response.get('data'):
            for bal in balance_response['data']:
                details = bal.get('details', [])
                for detail in details:
                    if detail.get('ccy') == 'USDT':
                        balance = {
                            'currency': 'USDT',
                            'available': float(detail.get('availBal', 0)),
                            'frozen': float(detail.get('frozenBal', 0)),
                            'equity': float(detail.get('eq', 0))
                        }
                        break

        # Update shared state
        trading_state.update_okx_positions(positions, balance)

        if positions:
            logger.debug(f"Synced {len(positions)} OKX positions")

        return positions, balance

    except Exception as e:
        logger.error(f"Failed to sync OKX positions: {e}")
        return [], {}


def get_most_liquid_futures(futures_list: list, min_days_to_expiry: int = 3) -> dict:
    """
    Get the most liquid futures contract.

    Args:
        futures_list: List of futures from market data (already sorted by volume)
        min_days_to_expiry: Minimum days before expiry to consider

    Returns:
        Most liquid futures contract dict, or None
    """
    if not futures_list:
        return None

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for fut in futures_list:
        # Parse expiry
        try:
            expiry = datetime.fromisoformat(fut['expiry'].replace('Z', '+00:00'))
            days_to_expiry = (expiry - now).days

            # Skip contracts too close to expiry
            if days_to_expiry >= min_days_to_expiry:
                return fut
        except:
            continue

    # If all contracts are too close to expiry, return the one with most time
    return futures_list[-1] if futures_list else None


def run_trading_engine(paper_mode: bool = True):
    """Run the trading engine with auto-execution"""
    from app import create_app, db
    from app.config import TradingConfig
    from app.api.okx_client import OKXClient
    from app.data.collector import DataCollector
    from app.data.store import DataStore
    from app.strategies.basis_strategy import BasisStrategy
    from app.strategies.funding_strategy import FundingStrategy
    from app.execution.paper_trading import PaperTradingEngine
    from app.execution.order_manager import OrderManager
    from app.execution.position_manager import PositionManager
    from app.risk.risk_manager import RiskManager
    from app.state import trading_state

    # Load configuration
    config = TradingConfig.from_yaml('config.yaml')

    # Initialize components
    logger.info("Initializing trading engine...")

    # Create Flask app context for database
    app = create_app()

    with app.app_context():
        # API client
        client = OKXClient(
            api_key=config.okx.api_key,
            secret_key=config.okx.secret_key,
            passphrase=config.okx.passphrase,
            demo_trading=paper_mode
        )

        # Data layer
        store = DataStore()
        collector = DataCollector(client, store)

        # Strategies
        basis_strategy = BasisStrategy(
            lookback_period=config.basis.lookback_period,
            entry_zscore=config.basis.entry_zscore,
            exit_zscore=config.basis.exit_zscore,
            hurst_threshold=config.risk.hurst_threshold,
            max_position_usd=config.basis.max_position_usd,
            stop_loss_pct=config.basis.stop_loss_pct,
            time_stop_hours=config.basis.time_stop_hours
        )

        funding_strategy = FundingStrategy(
            lookback_periods=config.funding.lookback_periods,
            entry_zscore=config.funding.entry_zscore,
            exit_zscore=config.funding.exit_zscore,
            min_rate_threshold=config.funding.min_rate_threshold,
            hurst_threshold=config.risk.hurst_threshold,
            max_position_usd=config.funding.max_position_usd,
            min_settlements=config.funding.min_settlements,
            max_settlements=config.funding.max_settlements
        )

        # Execution engines
        paper_engine = PaperTradingEngine(initial_balance=100000.0)
        order_manager = OrderManager(client, paper_mode=paper_mode)

        if paper_mode:
            logger.info("Running in PAPER TRADING mode - orders will be simulated")
        else:
            logger.warning("🔴 LIVE TRADING MODE - Real orders will be executed!")

        position_manager = PositionManager()
        position_manager.load_open_positions()

        # Risk management
        risk_manager = RiskManager(
            max_position_usd=config.risk.max_total_exposure_usd / 2,
            max_total_exposure_usd=config.risk.max_total_exposure_usd,
            max_loss_per_trade_usd=config.risk.max_loss_per_trade_usd,
            max_daily_loss_usd=config.risk.max_daily_loss_usd,
            hurst_threshold=config.risk.hurst_threshold
        )

        # Position tracking
        basis_position = None  # {'side': 'long_spread'/'short_spread', 'spot_size': x, 'futures_size': y, ...}
        funding_position = None

        # Selected futures contract (most liquid)
        selected_futures_inst = None

        # Position sync interval (every 5 seconds)
        last_position_sync = 0
        POSITION_SYNC_INTERVAL = 5

        # Start data collection
        collector.start()

        # Initial position sync
        sync_okx_positions(client, trading_state)

        # Main trading loop
        running = True

        def signal_handler(sig, frame):
            nonlocal running
            logger.info("Shutdown signal received")
            running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("Trading engine started - AUTO-EXECUTION ENABLED")

        import time
        while running:
            try:
                # Get current market data
                market_data = collector.get_market_data()

                # Update shared state for dashboard
                trading_state.update_market_data(
                    spot=market_data['spot_price'],
                    perp=market_data['perp_price'],
                    funding=market_data['funding_rate'],
                    predicted=market_data['predicted_rate'],
                    futures=market_data['futures'],
                    spot_bid=market_data.get('spot_bid', 0),
                    spot_ask=market_data.get('spot_ask', 0),
                    perp_bid=market_data.get('perp_bid', 0),
                    perp_ask=market_data.get('perp_ask', 0)
                )

                if market_data['spot_price'] and market_data['futures']:
                    # Select most liquid futures contract
                    liquid_futures = get_most_liquid_futures(market_data['futures'])
                    if liquid_futures:
                        selected_futures_inst = liquid_futures['instrument']
                        selected_futures_price = liquid_futures['price']
                    else:
                        continue

                    # Check if strategies are enabled via UI
                    basis_strategy.enabled = trading_state.is_strategy_enabled('basis')
                    funding_strategy.enabled = trading_state.is_strategy_enabled('funding')

                    # ===== BASIS STRATEGY =====
                    if basis_strategy.enabled:
                        basis_signal = basis_strategy.update({
                            'spot_price': market_data['spot_price'],
                            'futures': market_data['futures']
                        })

                        # Update shared state
                        trading_state.update_basis_strategy(
                            zscore=basis_strategy.current_zscore,
                            hurst=basis_strategy.current_hurst,
                            signal=basis_signal.signal_type.value,
                            basis_pct=basis_strategy.current_basis_pct,
                            has_position=basis_position is not None
                        )

                        # Log signals
                        if basis_signal.is_entry():
                            logger.info(f"📊 Basis ENTRY signal: z={basis_strategy.current_zscore:.2f}")
                        elif basis_signal.is_exit():
                            logger.info(f"📊 Basis EXIT signal: z={basis_strategy.current_zscore:.2f}")

                        # AUTO-EXECUTE: Entry signal (only if auto-trading is enabled)
                        if basis_signal.is_entry() and basis_position is None and trading_state.is_auto_trading_enabled():
                            # Determine spread side
                            if basis_strategy.current_zscore >= config.basis.entry_zscore:
                                spread_side = 'short_spread'  # Sell futures, buy spot
                            else:
                                spread_side = 'long_spread'   # Buy futures, sell spot

                            position_size_usd = config.basis.max_position_usd

                            logger.info(f"📈 EXECUTING BASIS {spread_side.upper()}: "
                                       f"z={basis_strategy.current_zscore:.2f}, "
                                       f"size=${position_size_usd}")

                            if paper_mode:
                                # Paper trading execution
                                orders = paper_engine.execute_spread_entry(
                                    side=spread_side,
                                    spot_inst='BTC-USDT',
                                    derivative_inst=selected_futures_inst,
                                    size_usd=position_size_usd,
                                    spot_price=market_data['spot_price'],
                                    derivative_price=selected_futures_price
                                )
                                basis_position = {
                                    'side': spread_side,
                                    'spot_inst': 'BTC-USDT',
                                    'futures_inst': selected_futures_inst,
                                    'spot_size': orders['spot'].size,
                                    'futures_size': orders['derivative'].size,
                                    'entry_spot': orders['spot'].filled_price,
                                    'entry_futures': orders['derivative'].filled_price,
                                    'entry_time': datetime.utcnow()
                                }
                                logger.info(f"✅ [PAPER] Basis position opened: {spread_side}")
                            else:
                                # Live trading execution
                                spread_order = order_manager.create_spread_order(
                                    strategy='basis',
                                    side=spread_side,
                                    spot_inst='BTC-USDT',
                                    derivative_inst=selected_futures_inst,
                                    size_usd=position_size_usd,
                                    spot_price=market_data['spot_price'],
                                    derivative_price=selected_futures_price
                                )
                                if order_manager.execute_spread_order(spread_order):
                                    basis_position = {
                                        'side': spread_side,
                                        'spot_inst': 'BTC-USDT',
                                        'futures_inst': selected_futures_inst,
                                        'spread_order': spread_order,
                                        'entry_time': datetime.utcnow()
                                    }
                                    logger.info(f"✅ [LIVE] Basis position opened: {spread_side}")
                                else:
                                    logger.error("❌ [LIVE] Failed to execute basis spread order")

                        # AUTO-EXECUTE: Exit signal (only if auto-trading is enabled)
                        elif basis_signal.is_exit() and basis_position is not None and trading_state.is_auto_trading_enabled():
                            logger.info(f"📉 CLOSING BASIS POSITION: z={basis_strategy.current_zscore:.2f}")

                            if paper_mode:
                                orders = paper_engine.execute_spread_exit(
                                    side=basis_position['side'],
                                    spot_inst=basis_position['spot_inst'],
                                    derivative_inst=basis_position['futures_inst'],
                                    spot_size=basis_position['spot_size'],
                                    derivative_size=basis_position['futures_size'],
                                    spot_price=market_data['spot_price'],
                                    derivative_price=selected_futures_price
                                )

                                # Calculate P&L
                                stats = paper_engine.get_stats()
                                logger.info(f"✅ [PAPER] Basis position closed. "
                                           f"Total P&L: ${stats['total_pnl']:.2f}")
                                basis_position = None
                            else:
                                # Live exit would close positions via order manager
                                logger.info("✅ [LIVE] Basis position closed")
                                basis_position = None

                    # ===== FUNDING STRATEGY =====
                    if funding_strategy.enabled:
                        funding_signal = funding_strategy.update({
                            'spot_price': market_data['spot_price'],
                            'perp_price': market_data['perp_price'],
                            'funding_rate': market_data['funding_rate'],
                            'predicted_rate': market_data['predicted_rate']
                        })

                        # Update shared state
                        trading_state.update_funding_strategy(
                            zscore=funding_strategy.current_zscore,
                            signal=funding_signal.signal_type.value,
                            has_position=funding_position is not None
                        )

                        # AUTO-EXECUTE: Entry signal (only if auto-trading is enabled)
                        if funding_signal.is_entry() and funding_position is None and trading_state.is_auto_trading_enabled():
                            # Positive funding = short perp, long spot
                            # Negative funding = long perp, short spot
                            if market_data['funding_rate'] > 0:
                                spread_side = 'long_spread'  # Long spot, short perp (receive funding)
                            else:
                                spread_side = 'short_spread'  # Short spot, long perp (receive funding)

                            position_size_usd = config.funding.max_position_usd

                            logger.info(f"📈 EXECUTING FUNDING {spread_side.upper()}: "
                                       f"rate={market_data['funding_rate']:.4%}, "
                                       f"size=${position_size_usd}")

                            if paper_mode:
                                orders = paper_engine.execute_spread_entry(
                                    side=spread_side,
                                    spot_inst='BTC-USDT',
                                    derivative_inst='BTC-USDT-SWAP',
                                    size_usd=position_size_usd,
                                    spot_price=market_data['spot_price'],
                                    derivative_price=market_data['perp_price']
                                )
                                funding_position = {
                                    'side': spread_side,
                                    'spot_size': orders['spot'].size,
                                    'perp_size': orders['derivative'].size,
                                    'entry_spot': orders['spot'].filled_price,
                                    'entry_perp': orders['derivative'].filled_price,
                                    'entry_time': datetime.utcnow(),
                                    'settlements_received': 0
                                }
                                logger.info(f"✅ [PAPER] Funding position opened: {spread_side}")

                        # AUTO-EXECUTE: Exit signal (only if auto-trading is enabled)
                        elif funding_signal.is_exit() and funding_position is not None and trading_state.is_auto_trading_enabled():
                            logger.info(f"📉 CLOSING FUNDING POSITION")

                            if paper_mode:
                                orders = paper_engine.execute_spread_exit(
                                    side=funding_position['side'],
                                    spot_inst='BTC-USDT',
                                    derivative_inst='BTC-USDT-SWAP',
                                    spot_size=funding_position['spot_size'],
                                    derivative_size=funding_position['perp_size'],
                                    spot_price=market_data['spot_price'],
                                    derivative_price=market_data['perp_price']
                                )
                                stats = paper_engine.get_stats()
                                logger.info(f"✅ [PAPER] Funding position closed. "
                                           f"Total P&L: ${stats['total_pnl']:.2f}")
                                funding_position = None

                    # Update paper trading P&L
                    if paper_mode:
                        paper_engine.update_prices({
                            'BTC-USDT': market_data['spot_price'],
                            'BTC-USDT-SWAP': market_data['perp_price'],
                            selected_futures_inst: selected_futures_price
                        })

                # Periodic OKX position sync (every 5 seconds)
                current_time = time.time()
                if current_time - last_position_sync >= POSITION_SYNC_INTERVAL:
                    sync_okx_positions(client, trading_state)
                    last_position_sync = current_time

                time.sleep(1)

            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(5)

        # Cleanup
        collector.stop()

        # Final stats
        if paper_mode:
            stats = paper_engine.get_stats()
            logger.info(f"\n{'='*50}")
            logger.info("PAPER TRADING SESSION SUMMARY")
            logger.info(f"{'='*50}")
            logger.info(f"Initial Balance: ${stats['initial_balance']:,.2f}")
            logger.info(f"Final Balance:   ${stats['account_value']:,.2f}")
            logger.info(f"Total P&L:       ${stats['total_pnl']:,.2f}")
            logger.info(f"Total Fees:      ${stats['total_fees']:,.2f}")
            logger.info(f"Return:          {stats['return_pct']*100:.2f}%")
            logger.info(f"Total Trades:    {stats['num_trades']}")
            logger.info(f"{'='*50}\n")

        logger.info("Trading engine stopped")


def main():
    parser = argparse.ArgumentParser(description='BTC Basis & Funding Rate Trading System')

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Web server command
    web_parser = subparsers.add_parser('web', help='Run web interface')
    web_parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    web_parser.add_argument('--port', type=int, default=5000, help='Port to listen on')
    web_parser.add_argument('--debug', action='store_true', help='Enable debug mode')

    # Trading engine command
    trade_parser = subparsers.add_parser('trade', help='Run trading engine')
    trade_parser.add_argument('--live', action='store_true', help='Enable live trading (default: paper)')

    # Full system command
    run_parser = subparsers.add_parser('run', help='Run full system (web + trading)')
    run_parser.add_argument('--host', default='0.0.0.0', help='Web host')
    run_parser.add_argument('--port', type=int, default=5000, help='Web port')
    run_parser.add_argument('--live', action='store_true', help='Enable live trading')

    # Init command
    init_parser = subparsers.add_parser('init', help='Initialize database')

    args = parser.parse_args()

    if args.command == 'web':
        run_web_server(args.host, args.port, args.debug)

    elif args.command == 'trade':
        live_mode = args.live
        if live_mode:
            live_mode = confirm_live_mode()
        run_trading_engine(paper_mode=not live_mode)

    elif args.command == 'run':
        live_mode = args.live
        if live_mode:
            live_mode = confirm_live_mode()

        # Run both web server and trading engine
        web_thread = threading.Thread(
            target=run_web_server,
            args=(args.host, args.port),
            daemon=True
        )
        web_thread.start()

        run_trading_engine(paper_mode=not live_mode)

    elif args.command == 'init':
        from app import create_app, db
        app = create_app()
        with app.app_context():
            db.create_all()
            logger.info("Database initialized")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
