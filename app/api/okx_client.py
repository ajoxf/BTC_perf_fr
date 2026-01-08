"""
OKX REST API Client with authentication
"""
import os
import hmac
import base64
import hashlib
import json
import requests
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from loguru import logger

from app.api.rate_limiter import RateLimiter


@dataclass
class Ticker:
    """Ticker data structure"""
    inst_id: str
    last_price: float
    bid_price: float
    ask_price: float
    volume_24h: float
    timestamp: datetime


@dataclass
class FundingRate:
    """Funding rate data structure"""
    inst_id: str
    funding_rate: float
    next_funding_rate: Optional[float]
    funding_time: datetime


@dataclass
class Instrument:
    """Instrument info structure"""
    inst_id: str
    inst_type: str  # SPOT, FUTURES, SWAP
    underlying: str
    base_currency: str
    quote_currency: str
    contract_value: Optional[float]
    expiry_time: Optional[datetime]


class OKXClient:
    """OKX API v5 REST Client"""

    BASE_URL = "https://www.okx.com"

    def __init__(self,
                 api_key: Optional[str] = None,
                 secret_key: Optional[str] = None,
                 passphrase: Optional[str] = None,
                 demo_trading: bool = True):
        """
        Initialize OKX client.

        Args:
            api_key: OKX API key (or set OKX_API_KEY env var)
            secret_key: OKX secret key (or set OKX_SECRET_KEY env var)
            passphrase: OKX passphrase (or set OKX_PASSPHRASE env var)
            demo_trading: Use demo/paper trading mode
        """
        self.api_key = api_key or os.environ.get('OKX_API_KEY', '')
        self.secret_key = secret_key or os.environ.get('OKX_SECRET_KEY', '')
        self.passphrase = passphrase or os.environ.get('OKX_PASSPHRASE', '')
        self.demo_trading = demo_trading
        self.rate_limiter = RateLimiter(requests_per_second=10)

        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'x-simulated-trading': '1' if demo_trading else '0'
        })

    def _get_timestamp(self) -> str:
        """Get ISO timestamp for request signing"""
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def _sign(self, timestamp: str, method: str, path: str, body: str = '') -> str:
        """Generate HMAC SHA256 signature"""
        message = timestamp + method.upper() + path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf8'),
            bytes(message, encoding='utf8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    def _auth_headers(self, method: str, path: str, body: str = '') -> Dict[str, str]:
        """Get authenticated request headers"""
        timestamp = self._get_timestamp()
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': self._sign(timestamp, method, path, body),
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
        }

    def _request(self, method: str, path: str,
                 params: Optional[Dict] = None,
                 data: Optional[Dict] = None,
                 auth: bool = False) -> Dict[str, Any]:
        """Make HTTP request with optional authentication"""
        self.rate_limiter.wait()

        url = self.BASE_URL + path

        if params:
            query = '&'.join(f"{k}={v}" for k, v in params.items() if v is not None)
            if query:
                path = f"{path}?{query}"
                url = self.BASE_URL + path

        body = ''
        if data:
            body = json.dumps(data)

        headers = {}
        if auth:
            headers = self._auth_headers(method, path, body)

        try:
            if method.upper() == 'GET':
                response = self._session.get(url, headers=headers)
            elif method.upper() == 'POST':
                response = self._session.post(url, headers=headers, data=body)
            else:
                raise ValueError(f"Unsupported method: {method}")

            response.raise_for_status()
            result = response.json()

            if result.get('code') != '0':
                logger.error(f"OKX API error: {result.get('msg')} (code: {result.get('code')})")

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise

    # ==================== Public Endpoints ====================

    def get_ticker(self, inst_id: str) -> Optional[Ticker]:
        """Get ticker for a single instrument"""
        result = self._request('GET', '/api/v5/market/ticker', {'instId': inst_id})

        if result.get('code') == '0' and result.get('data'):
            d = result['data'][0]
            return Ticker(
                inst_id=d['instId'],
                last_price=float(d['last']),
                bid_price=float(d['bidPx']) if d.get('bidPx') else 0,
                ask_price=float(d['askPx']) if d.get('askPx') else 0,
                volume_24h=float(d['vol24h']) if d.get('vol24h') else 0,
                timestamp=datetime.fromtimestamp(int(d['ts']) / 1000, tz=timezone.utc)
            )
        return None

    def get_tickers(self, inst_type: str = 'SPOT') -> List[Ticker]:
        """Get all tickers for an instrument type (SPOT, FUTURES, SWAP)"""
        result = self._request('GET', '/api/v5/market/tickers', {'instType': inst_type})

        tickers = []
        if result.get('code') == '0' and result.get('data'):
            for d in result['data']:
                tickers.append(Ticker(
                    inst_id=d['instId'],
                    last_price=float(d['last']) if d.get('last') else 0,
                    bid_price=float(d['bidPx']) if d.get('bidPx') else 0,
                    ask_price=float(d['askPx']) if d.get('askPx') else 0,
                    volume_24h=float(d['vol24h']) if d.get('vol24h') else 0,
                    timestamp=datetime.fromtimestamp(int(d['ts']) / 1000, tz=timezone.utc)
                ))
        return tickers

    def get_candles(self, inst_id: str, bar: str = '1m', limit: int = 100) -> List[Dict]:
        """
        Get historical candlestick data.

        Args:
            inst_id: Instrument ID (e.g., 'BTC-USDT', 'BTC-USDT-250117')
            bar: Bar size - 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 1D, etc.
            limit: Number of candles to fetch (max 300)

        Returns:
            List of candle dicts with: timestamp, open, high, low, close, volume
        """
        result = self._request('GET', '/api/v5/market/candles', {
            'instId': inst_id,
            'bar': bar,
            'limit': str(min(limit, 300))
        })

        candles = []
        if result.get('code') == '0' and result.get('data'):
            # OKX returns: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            for d in result['data']:
                candles.append({
                    'timestamp': datetime.fromtimestamp(int(d[0]) / 1000, tz=timezone.utc),
                    'open': float(d[1]),
                    'high': float(d[2]),
                    'low': float(d[3]),
                    'close': float(d[4]),
                    'volume': float(d[5])
                })
        # Reverse to get chronological order (oldest first)
        return list(reversed(candles))

    def get_instruments(self, inst_type: str, underlying: str = None) -> List[Instrument]:
        """Get available instruments"""
        params = {'instType': inst_type}
        if underlying:
            params['uly'] = underlying

        result = self._request('GET', '/api/v5/public/instruments', params)

        instruments = []
        if result.get('code') == '0' and result.get('data'):
            for d in result['data']:
                expiry = None
                if d.get('expTime'):
                    expiry = datetime.fromtimestamp(int(d['expTime']) / 1000, tz=timezone.utc)

                instruments.append(Instrument(
                    inst_id=d['instId'],
                    inst_type=d['instType'],
                    underlying=d.get('uly', ''),
                    base_currency=d.get('baseCcy', ''),
                    quote_currency=d.get('quoteCcy', d.get('settleCcy', '')),
                    contract_value=float(d['ctVal']) if d.get('ctVal') else None,
                    expiry_time=expiry
                ))
        return instruments

    def get_funding_rate(self, inst_id: str) -> Optional[FundingRate]:
        """Get current funding rate for perpetual swap"""
        result = self._request('GET', '/api/v5/public/funding-rate', {'instId': inst_id})

        if result.get('code') == '0' and result.get('data'):
            d = result['data'][0]
            return FundingRate(
                inst_id=d['instId'],
                funding_rate=float(d['fundingRate']),
                next_funding_rate=float(d['nextFundingRate']) if d.get('nextFundingRate') else None,
                funding_time=datetime.fromtimestamp(int(d['fundingTime']) / 1000, tz=timezone.utc)
            )
        return None

    def get_funding_rate_history(self, inst_id: str, limit: int = 100) -> List[FundingRate]:
        """Get historical funding rates"""
        result = self._request('GET', '/api/v5/public/funding-rate-history', {
            'instId': inst_id,
            'limit': str(limit)
        })

        rates = []
        if result.get('code') == '0' and result.get('data'):
            for d in result['data']:
                rates.append(FundingRate(
                    inst_id=d['instId'],
                    funding_rate=float(d['fundingRate']),
                    next_funding_rate=None,
                    funding_time=datetime.fromtimestamp(int(d['fundingTime']) / 1000, tz=timezone.utc)
                ))
        return rates

    # ==================== Private Endpoints ====================

    def get_balance(self, currency: str = None) -> Dict[str, Any]:
        """Get account balance"""
        params = {}
        if currency:
            params['ccy'] = currency

        return self._request('GET', '/api/v5/account/balance', params, auth=True)

    def get_positions(self, inst_type: str = None, inst_id: str = None) -> Dict[str, Any]:
        """Get current positions"""
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id

        return self._request('GET', '/api/v5/account/positions', params, auth=True)

    def place_order(self,
                    inst_id: str,
                    side: str,  # 'buy' or 'sell'
                    order_type: str,  # 'market', 'limit', 'post_only', etc.
                    size: float,
                    price: float = None,
                    trade_mode: str = 'cross',  # 'cross', 'isolated', 'cash'
                    client_order_id: str = None) -> Dict[str, Any]:
        """
        Place an order.

        Args:
            inst_id: Instrument ID (e.g., 'BTC-USDT', 'BTC-USDT-SWAP')
            side: 'buy' or 'sell'
            order_type: 'market', 'limit', 'post_only', 'fok', 'ioc'
            size: Order size
            price: Price (required for limit orders)
            trade_mode: 'cross', 'isolated', or 'cash' (for spot)
            client_order_id: Optional client-defined order ID
        """
        data = {
            'instId': inst_id,
            'tdMode': trade_mode,
            'side': side,
            'ordType': order_type,
            'sz': str(size)
        }

        if price is not None:
            data['px'] = str(price)

        if client_order_id:
            data['clOrdId'] = client_order_id

        return self._request('POST', '/api/v5/trade/order', data=data, auth=True)

    def cancel_order(self, inst_id: str, order_id: str = None, client_order_id: str = None) -> Dict[str, Any]:
        """Cancel an order"""
        data = {'instId': inst_id}

        if order_id:
            data['ordId'] = order_id
        elif client_order_id:
            data['clOrdId'] = client_order_id
        else:
            raise ValueError("Either order_id or client_order_id required")

        return self._request('POST', '/api/v5/trade/cancel-order', data=data, auth=True)

    def get_order(self, inst_id: str, order_id: str = None, client_order_id: str = None) -> Dict[str, Any]:
        """Get order details"""
        params = {'instId': inst_id}

        if order_id:
            params['ordId'] = order_id
        elif client_order_id:
            params['clOrdId'] = client_order_id

        return self._request('GET', '/api/v5/trade/order', params, auth=True)

    def get_pending_orders(self, inst_type: str = None, inst_id: str = None) -> Dict[str, Any]:
        """Get pending orders"""
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id

        return self._request('GET', '/api/v5/trade/orders-pending', params, auth=True)

    # ==================== Helper Methods ====================

    def get_btc_spot_price(self) -> Optional[float]:
        """Get current BTC-USDT spot price"""
        ticker = self.get_ticker('BTC-USDT')
        return ticker.last_price if ticker else None

    def get_btc_perp_price(self) -> Optional[float]:
        """Get current BTC-USDT perpetual swap price"""
        ticker = self.get_ticker('BTC-USDT-SWAP')
        return ticker.last_price if ticker else None

    def get_btc_futures(self) -> List[Instrument]:
        """Get all BTC-USDT futures contracts"""
        return self.get_instruments('FUTURES', 'BTC-USDT')

    def get_btc_funding_rate(self) -> Optional[FundingRate]:
        """Get BTC perpetual funding rate"""
        return self.get_funding_rate('BTC-USDT-SWAP')
