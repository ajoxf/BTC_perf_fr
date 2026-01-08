"""
Data collection service for market data
"""
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable
from loguru import logger

from app.api.okx_client import OKXClient, Ticker, FundingRate, Instrument
from app.api.okx_websocket import OKXWebSocket, WSMessage
from app.data.store import DataStore


class DataCollector:
    """Collect and aggregate market data from OKX"""

    def __init__(
        self,
        client: OKXClient,
        store: DataStore,
        use_websocket: bool = True
    ):
        """
        Initialize data collector.

        Args:
            client: OKX REST API client
            store: Data store for collected data
            use_websocket: Use WebSocket for real-time data
        """
        self.client = client
        self.store = store
        self.use_websocket = use_websocket

        self._websocket: Optional[OKXWebSocket] = None
        self._running = False
        self._collection_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[Callable]] = {}

        # Instruments to track
        self.spot_instrument = 'BTC-USDT'
        self.perp_instrument = 'BTC-USDT-SWAP'
        self._futures_instruments: List[str] = []

    def start(self) -> None:
        """Start data collection"""
        if self._running:
            return

        self._running = True

        # Discover futures contracts
        self._update_futures_contracts()

        if self.use_websocket:
            self._start_websocket()
        else:
            self._start_polling()

        logger.info("Data collector started")

    def stop(self) -> None:
        """Stop data collection"""
        self._running = False

        if self._websocket:
            self._websocket.disconnect()
            self._websocket = None

        if self._collection_thread:
            self._collection_thread.join(timeout=5)
            self._collection_thread = None

        logger.info("Data collector stopped")

    def _update_futures_contracts(self) -> None:
        """Update list of available futures contracts"""
        try:
            instruments = self.client.get_instruments('FUTURES', 'BTC-USDT')
            self._futures_instruments = [i.inst_id for i in instruments]
            logger.info(f"Found {len(self._futures_instruments)} futures contracts")
        except Exception as e:
            logger.error(f"Failed to get futures contracts: {e}")

    def _start_websocket(self) -> None:
        """Start WebSocket connection for real-time data"""
        self._websocket = OKXWebSocket(
            api_key=self.client.api_key,
            secret_key=self.client.secret_key,
            passphrase=self.client.passphrase,
            demo_trading=self.client.demo_trading
        )

        # Subscribe to tickers
        self._websocket.subscribe_ticker(self.spot_instrument, self._on_ticker)
        self._websocket.subscribe_ticker(self.perp_instrument, self._on_ticker)

        for fut_inst in self._futures_instruments:
            self._websocket.subscribe_ticker(fut_inst, self._on_ticker)

        # Subscribe to funding rate
        self._websocket.subscribe_funding_rate(self.perp_instrument, self._on_funding_rate)

        # Connect
        self._websocket.connect(public=True, private=False)

        # Start periodic tasks thread
        self._collection_thread = threading.Thread(target=self._periodic_tasks, daemon=True)
        self._collection_thread.start()

    def _start_polling(self) -> None:
        """Start polling mode for data collection"""
        self._collection_thread = threading.Thread(target=self._poll_data, daemon=True)
        self._collection_thread.start()

    def _on_ticker(self, msg: WSMessage) -> None:
        """Handle ticker update from WebSocket"""
        try:
            data = msg.data
            inst_id = msg.inst_id

            ticker = Ticker(
                inst_id=inst_id,
                last_price=float(data.get('last', 0)),
                bid_price=float(data.get('bidPx', 0)) if data.get('bidPx') else 0,
                ask_price=float(data.get('askPx', 0)) if data.get('askPx') else 0,
                volume_24h=float(data.get('vol24h', 0)) if data.get('vol24h') else 0,
                timestamp=datetime.now(timezone.utc)
            )

            self.store.update_ticker(ticker)
            self._trigger_callbacks('ticker', ticker)

        except Exception as e:
            logger.error(f"Error processing ticker: {e}")

    def _on_funding_rate(self, msg: WSMessage) -> None:
        """Handle funding rate update from WebSocket"""
        try:
            data = msg.data

            funding = FundingRate(
                inst_id=msg.inst_id,
                funding_rate=float(data.get('fundingRate', 0)),
                next_funding_rate=float(data.get('nextFundingRate')) if data.get('nextFundingRate') else None,
                funding_time=datetime.fromtimestamp(int(data.get('fundingTime', 0)) / 1000, tz=timezone.utc)
            )

            self.store.update_funding_rate(funding)
            self._trigger_callbacks('funding', funding)

        except Exception as e:
            logger.error(f"Error processing funding rate: {e}")

    def _poll_data(self) -> None:
        """Poll data from REST API"""
        while self._running:
            try:
                # Get spot ticker
                spot = self.client.get_ticker(self.spot_instrument)
                if spot:
                    self.store.update_ticker(spot)
                    self._trigger_callbacks('ticker', spot)

                # Get perp ticker
                perp = self.client.get_ticker(self.perp_instrument)
                if perp:
                    self.store.update_ticker(perp)
                    self._trigger_callbacks('ticker', perp)

                # Get futures tickers
                for fut_inst in self._futures_instruments:
                    fut = self.client.get_ticker(fut_inst)
                    if fut:
                        self.store.update_ticker(fut)
                        self._trigger_callbacks('ticker', fut)

                # Get funding rate
                funding = self.client.get_funding_rate(self.perp_instrument)
                if funding:
                    self.store.update_funding_rate(funding)
                    self._trigger_callbacks('funding', funding)

            except Exception as e:
                logger.error(f"Polling error: {e}")

            time.sleep(1)  # Poll every second

    def _periodic_tasks(self) -> None:
        """Run periodic tasks (futures discovery, cleanup)"""
        last_futures_update = 0

        while self._running:
            now = time.time()

            # Update futures list every hour
            if now - last_futures_update > 3600:
                self._update_futures_contracts()

                # Resubscribe to new contracts
                if self._websocket:
                    for fut_inst in self._futures_instruments:
                        self._websocket.subscribe_ticker(fut_inst, self._on_ticker)

                last_futures_update = now

            time.sleep(60)

    def register_callback(self, event_type: str, callback: Callable) -> None:
        """
        Register callback for data events.

        Args:
            event_type: 'ticker' or 'funding'
            callback: Function to call with data
        """
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)

    def _trigger_callbacks(self, event_type: str, data: Any) -> None:
        """Trigger registered callbacks"""
        for callback in self._callbacks.get(event_type, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def get_market_data(self) -> Dict[str, Any]:
        """
        Get current market data for strategies.

        Returns:
            Dictionary with spot, perp, and futures data
        """
        spot_price = self.store.get_price(self.spot_instrument)
        perp_price = self.store.get_price(self.perp_instrument)
        funding = self.store.get_funding_rate(self.perp_instrument)

        # Get futures data
        futures_data = []
        for fut_inst in self._futures_instruments:
            price = self.store.get_price(fut_inst)
            if price:
                # Parse expiry from instrument ID (e.g., BTC-USDT-240329)
                parts = fut_inst.split('-')
                if len(parts) >= 3:
                    try:
                        expiry_str = parts[2]
                        # Convert YYMMDD to datetime
                        expiry = datetime.strptime(expiry_str, '%y%m%d')
                        expiry = expiry.replace(hour=8, tzinfo=timezone.utc)  # OKX settles at 08:00 UTC

                        futures_data.append({
                            'inst_id': fut_inst,
                            'price': price,
                            'expiry': expiry
                        })
                    except ValueError:
                        pass

        return {
            'spot_price': spot_price,
            'perp_price': perp_price,
            'funding_rate': funding.funding_rate if funding else 0,
            'predicted_rate': funding.next_funding_rate if funding else None,
            'funding_time': funding.funding_time if funding else None,
            'futures': futures_data,
            'timestamp': datetime.now(timezone.utc)
        }

    @property
    def is_running(self) -> bool:
        return self._running
