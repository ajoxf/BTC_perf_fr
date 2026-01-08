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


def run_trading_engine(paper_mode: bool = True):
    """Run the trading engine"""
    from app import create_app, db
    from app.config import TradingConfig
    from app.api.okx_client import OKXClient
    from app.data.collector import DataCollector
    from app.data.store import DataStore
    from app.strategies.basis_strategy import BasisStrategy
    from app.strategies.funding_strategy import FundingStrategy
    from app.execution.paper_trading import PaperTradingEngine
    from app.execution.position_manager import PositionManager
    from app.risk.risk_manager import RiskManager

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

        # Execution
        if paper_mode:
            paper_engine = PaperTradingEngine()
            logger.info("Running in PAPER TRADING mode")
        else:
            logger.warning("Running in LIVE TRADING mode")

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

        # Start data collection
        collector.start()

        # Main trading loop
        running = True

        def signal_handler(sig, frame):
            nonlocal running
            logger.info("Shutdown signal received")
            running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("Trading engine started")

        import time
        while running:
            try:
                # Get current market data
                market_data = collector.get_market_data()

                if market_data['spot_price']:
                    # Update basis strategy
                    if basis_strategy.enabled:
                        basis_signal = basis_strategy.update({
                            'spot_price': market_data['spot_price'],
                            'futures': market_data['futures']
                        })

                        if basis_signal.is_entry():
                            logger.info(f"Basis entry signal: {basis_signal.reason}")
                        elif basis_signal.is_exit():
                            logger.info(f"Basis exit signal: {basis_signal.reason}")

                    # Update funding strategy
                    if funding_strategy.enabled:
                        funding_signal = funding_strategy.update({
                            'spot_price': market_data['spot_price'],
                            'perp_price': market_data['perp_price'],
                            'funding_rate': market_data['funding_rate'],
                            'predicted_rate': market_data['predicted_rate']
                        })

                        if funding_signal.is_entry():
                            logger.info(f"Funding entry signal: {funding_signal.reason}")
                        elif funding_signal.is_exit():
                            logger.info(f"Funding exit signal: {funding_signal.reason}")

                time.sleep(1)

            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                time.sleep(5)

        # Cleanup
        collector.stop()
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
        run_trading_engine(paper_mode=not args.live)

    elif args.command == 'run':
        # Run both web server and trading engine
        web_thread = threading.Thread(
            target=run_web_server,
            args=(args.host, args.port),
            daemon=True
        )
        web_thread.start()

        run_trading_engine(paper_mode=not args.live)

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
