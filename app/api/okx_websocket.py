"""
OKX WebSocket Client for real-time data
"""
import json
import threading
import time
import hmac
import base64
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass
import websocket
from loguru import logger


@dataclass
class WSMessage:
    """WebSocket message structure"""
    channel: str
    inst_id: str
    data: Dict[str, Any]
    timestamp: datetime


class OKXWebSocket:
    """OKX WebSocket client for real-time data"""

    PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"
    PRIVATE_URL = "wss://ws.okx.com:8443/ws/v5/private"

    # Demo trading URLs
    PUBLIC_DEMO_URL = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
    PRIVATE_DEMO_URL = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"

    def __init__(self,
                 api_key: str = None,
                 secret_key: str = None,
                 passphrase: str = None,
                 demo_trading: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.demo_trading = demo_trading

        self._public_ws: Optional[websocket.WebSocketApp] = None
        self._private_ws: Optional[websocket.WebSocketApp] = None
        self._public_thread: Optional[threading.Thread] = None
        self._private_thread: Optional[threading.Thread] = None

        self._running = False
        self._callbacks: Dict[str, List[Callable]] = {}
        self._subscriptions: List[Dict] = []

    def _get_public_url(self) -> str:
        return self.PUBLIC_DEMO_URL if self.demo_trading else self.PUBLIC_URL

    def _get_private_url(self) -> str:
        return self.PRIVATE_DEMO_URL if self.demo_trading else self.PRIVATE_URL

    def _generate_signature(self, timestamp: str) -> str:
        """Generate signature for WebSocket authentication"""
        message = timestamp + 'GET' + '/users/self/verify'
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf8'),
            bytes(message, encoding='utf8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    def _on_open_public(self, ws):
        """Handle public WebSocket open"""
        logger.info("Public WebSocket connected")
        # Resubscribe to channels
        for sub in self._subscriptions:
            if sub.get('channel') in ['tickers', 'funding-rate']:
                self._send_public({"op": "subscribe", "args": [sub]})

    def _on_open_private(self, ws):
        """Handle private WebSocket open"""
        logger.info("Private WebSocket connected, authenticating...")
        timestamp = str(int(time.time()))
        sign = self._generate_signature(timestamp)

        auth_msg = {
            "op": "login",
            "args": [{
                "apiKey": self.api_key,
                "passphrase": self.passphrase,
                "timestamp": timestamp,
                "sign": sign
            }]
        }
        ws.send(json.dumps(auth_msg))

    def _on_message_public(self, ws, message):
        """Handle public WebSocket message"""
        self._handle_message(message)

    def _on_message_private(self, ws, message):
        """Handle private WebSocket message"""
        data = json.loads(message)

        # Check for login response
        if data.get('event') == 'login':
            if data.get('code') == '0':
                logger.info("Private WebSocket authenticated")
                # Subscribe to private channels
                for sub in self._subscriptions:
                    if sub.get('channel') in ['orders', 'positions', 'account']:
                        self._send_private({"op": "subscribe", "args": [sub]})
            else:
                logger.error(f"Authentication failed: {data.get('msg')}")
        else:
            self._handle_message(message)

    def _handle_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)

            # Skip ping/pong and subscription confirmations
            if data.get('event') in ['subscribe', 'unsubscribe', 'error']:
                if data.get('event') == 'error':
                    logger.error(f"WebSocket error: {data}")
                return

            # Process data messages
            if 'data' in data and 'arg' in data:
                channel = data['arg'].get('channel', '')
                inst_id = data['arg'].get('instId', '')

                for item in data['data']:
                    msg = WSMessage(
                        channel=channel,
                        inst_id=inst_id,
                        data=item,
                        timestamp=datetime.now(timezone.utc)
                    )

                    # Call registered callbacks
                    for callback in self._callbacks.get(channel, []):
                        try:
                            callback(msg)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")

    def _on_error(self, ws, error):
        """Handle WebSocket error"""
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close"""
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        if self._running:
            logger.info("Attempting to reconnect...")
            time.sleep(5)
            self._reconnect()

    def _reconnect(self):
        """Reconnect WebSocket connections"""
        if self._public_ws:
            self._start_public()
        if self._private_ws:
            self._start_private()

    def _send_public(self, data: Dict):
        """Send message on public WebSocket"""
        if self._public_ws:
            self._public_ws.send(json.dumps(data))

    def _send_private(self, data: Dict):
        """Send message on private WebSocket"""
        if self._private_ws:
            self._private_ws.send(json.dumps(data))

    def _start_public(self):
        """Start public WebSocket connection"""
        self._public_ws = websocket.WebSocketApp(
            self._get_public_url(),
            on_open=self._on_open_public,
            on_message=self._on_message_public,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self._public_thread = threading.Thread(
            target=self._public_ws.run_forever,
            daemon=True
        )
        self._public_thread.start()

    def _start_private(self):
        """Start private WebSocket connection"""
        if not all([self.api_key, self.secret_key, self.passphrase]):
            logger.warning("Private WebSocket requires API credentials")
            return

        self._private_ws = websocket.WebSocketApp(
            self._get_private_url(),
            on_open=self._on_open_private,
            on_message=self._on_message_private,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self._private_thread = threading.Thread(
            target=self._private_ws.run_forever,
            daemon=True
        )
        self._private_thread.start()

    def connect(self, public: bool = True, private: bool = False):
        """Connect to WebSocket servers"""
        self._running = True

        if public:
            self._start_public()

        if private:
            self._start_private()

    def disconnect(self):
        """Disconnect from WebSocket servers"""
        self._running = False

        if self._public_ws:
            self._public_ws.close()
        if self._private_ws:
            self._private_ws.close()

    def subscribe_ticker(self, inst_id: str, callback: Callable[[WSMessage], None]):
        """Subscribe to ticker updates"""
        sub = {'channel': 'tickers', 'instId': inst_id}
        self._subscriptions.append(sub)
        self._callbacks.setdefault('tickers', []).append(callback)

        if self._public_ws:
            self._send_public({"op": "subscribe", "args": [sub]})

    def subscribe_funding_rate(self, inst_id: str, callback: Callable[[WSMessage], None]):
        """Subscribe to funding rate updates"""
        sub = {'channel': 'funding-rate', 'instId': inst_id}
        self._subscriptions.append(sub)
        self._callbacks.setdefault('funding-rate', []).append(callback)

        if self._public_ws:
            self._send_public({"op": "subscribe", "args": [sub]})

    def subscribe_orders(self, inst_type: str, callback: Callable[[WSMessage], None]):
        """Subscribe to order updates (requires auth)"""
        sub = {'channel': 'orders', 'instType': inst_type}
        self._subscriptions.append(sub)
        self._callbacks.setdefault('orders', []).append(callback)

        if self._private_ws:
            self._send_private({"op": "subscribe", "args": [sub]})

    def subscribe_positions(self, inst_type: str, callback: Callable[[WSMessage], None]):
        """Subscribe to position updates (requires auth)"""
        sub = {'channel': 'positions', 'instType': inst_type}
        self._subscriptions.append(sub)
        self._callbacks.setdefault('positions', []).append(callback)

        if self._private_ws:
            self._send_private({"op": "subscribe", "args": [sub]})

    def unsubscribe(self, channel: str, inst_id: str = None):
        """Unsubscribe from a channel"""
        sub = {'channel': channel}
        if inst_id:
            sub['instId'] = inst_id

        self._subscriptions = [s for s in self._subscriptions
                               if not (s.get('channel') == channel and
                                       (inst_id is None or s.get('instId') == inst_id))]

        if channel in ['tickers', 'funding-rate']:
            self._send_public({"op": "unsubscribe", "args": [sub]})
        else:
            self._send_private({"op": "unsubscribe", "args": [sub]})
