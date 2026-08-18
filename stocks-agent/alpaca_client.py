"""
alpaca_client.py
Single wrapper for all Alpaca Paper Trading API interactions.
Uses v2 REST API directly via requests — no third-party SDK dependency.
All market data uses the free IEX feed (sufficient for paper trading).
"""
import requests
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from config.config import (
    ALPACA_API_KEY, ALPACA_API_SECRET,
    ALPACA_BASE_URL, ALPACA_DATA_URL,
    CANDLE_INTERVAL, CANDLE_LIMIT,
)

log = logging.getLogger(__name__)

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_API_SECRET,
    "Content-Type":        "application/json",
}
TIMEOUT = 10


class AlpacaClient:

    # ── Account ────────────────────────────────────────────────────────────────
    def get_account(self):
        """Returns account dict with cash, portfolio_value, buying_power."""
        resp = requests.get(
            f"{ALPACA_BASE_URL}/v2/account",
            headers=HEADERS, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()

    def get_cash(self):
        """Returns available cash as float."""
        acc = self.get_account()
        return float(acc.get("cash", 0))

    def get_positions(self):
        """Returns list of open position dicts."""
        resp = requests.get(
            f"{ALPACA_BASE_URL}/v2/positions",
            headers=HEADERS, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()

    def get_position(self, symbol):
        """Returns a single position dict or None if not held."""
        try:
            resp = requests.get(
                f"{ALPACA_BASE_URL}/v2/positions/{symbol}",
                headers=HEADERS, timeout=TIMEOUT
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    # ── Market data ────────────────────────────────────────────────────────────
    def get_bars(self, symbol, timeframe=CANDLE_INTERVAL, limit=CANDLE_LIMIT):
        """
        Fetch OHLCV bars for a symbol.
        Returns list of dicts: {t, o, h, l, c, v} sorted oldest-first.
        Uses free IEX feed — sufficient for paper trading signals.

        Fetches with sort=desc so Alpaca returns the NEWEST bars first,
        then we reverse to get chronological order for indicator calculations.
        This ensures we always get today's intraday bars rather than bars
        from days ago (which happens with sort=asc + limit on a 5-day window).
        """
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=5)

        params = {
            "timeframe": timeframe,
            "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit":     limit,
            "feed":      "iex",
            "sort":      "desc",   # newest first → we get today's bars
        }
        resp = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
            headers=HEADERS, params=params, timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        bars = data.get("bars", [])
        if not bars:
            raise ValueError(f"No bars returned for {symbol}")
        # Reverse to chronological order (oldest first) for indicator calculations
        return list(reversed(bars))

    def get_latest_quote(self, symbol):
        """Returns latest bid/ask/last price for a symbol."""
        resp = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/quotes/latest",
            headers=HEADERS,
            params={"feed": "iex"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        q = resp.json().get("quote", {})
        return {
            "bid":  float(q.get("bp", 0)),
            "ask":  float(q.get("ap", 0)),
            "last": float(q.get("ap", q.get("bp", 0))),
        }

    def get_latest_trade(self, symbol):
        """Returns latest trade price."""
        resp = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/trades/latest",
            headers=HEADERS,
            params={"feed": "iex"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        t = resp.json().get("trade", {})
        return {"price": float(t.get("p", 0))}

    def is_market_open(self):
        """Returns True if NYSE is currently open."""
        resp = requests.get(
            f"{ALPACA_BASE_URL}/v2/clock",
            headers=HEADERS, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("is_open", False)

    # ── Order placement ────────────────────────────────────────────────────────
    def place_market_order(self, symbol, side, notional=None, qty=None):
        """
        Place a market order.
        Use notional (USD amount) for fractional shares, or qty for whole shares.
        side: "buy" or "sell"
        """
        order = {
            "symbol":        symbol,
            "side":          side,
            "type":          "market",
            "time_in_force": "day",
        }
        if notional is not None:
            order["notional"] = str(round(notional, 2))
        elif qty is not None:
            order["qty"] = str(qty)
        else:
            raise ValueError("Must specify notional or qty")

        log.info(f"Placing {side} order: {symbol} notional=${notional}")
        resp = requests.post(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS, json=order, timeout=TIMEOUT
        )
        resp.raise_for_status()
        result = resp.json()
        log.info(f"Order placed: {result.get('id')} status={result.get('status')}")
        return result

    def close_position(self, symbol):
        """Close entire position in a symbol via Alpaca's close endpoint."""
        resp = requests.delete(
            f"{ALPACA_BASE_URL}/v2/positions/{symbol}",
            headers=HEADERS, timeout=TIMEOUT
        )
        if resp.status_code == 404:
            log.warning(f"No position to close for {symbol}")
            return None
        resp.raise_for_status()
        return resp.json()

    def get_order(self, order_id):
        """Get order status by ID."""
        resp = requests.get(
            f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
            headers=HEADERS, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    client = AlpacaClient()

    print("\n=== Account ===")
    try:
        acc = client.get_account()
        print(f"Cash: ${float(acc['cash']):.2f}")
        print(f"Portfolio value: ${float(acc['portfolio_value']):.2f}")
        print(f"Status: {acc['status']}")
    except Exception as e:
        print(f"Account fetch FAILED: {e}")

    print("\n=== Market clock ===")
    try:
        print(f"Market open: {client.is_market_open()}")
    except Exception as e:
        print(f"Clock fetch FAILED: {e}")

    print("\n=== Bars (SPY) ===")
    try:
        bars = client.get_bars("SPY", limit=5)
        print(f"Got {len(bars)} bars. Latest: {bars[-1]}")
    except Exception as e:
        print(f"Bars fetch FAILED: {e}")

    print("\n=== Latest trade (AAPL) ===")
    try:
        t = client.get_latest_trade("AAPL")
        print(f"AAPL last price: ${t['price']:.2f}")
    except Exception as e:
        print(f"Trade fetch FAILED: {e}")
