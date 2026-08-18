"""
kraken_client.py
Single wrapper for all Kraken API interactions.
Uses python-kraken-sdk with sandbox=True for demo, False for live.
"""
import requests
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from config.config import (
    KRAKEN_API_KEY, KRAKEN_API_SECRET, KRAKEN_DEMO,
    SPOT_PAIR, LEVERAGE
)
from kraken.futures import User, Trade, Market

log = logging.getLogger(__name__)


class KrakenClient:

    def __init__(self):
        self.demo = KRAKEN_DEMO
        self._user  = User(key=KRAKEN_API_KEY,  secret=KRAKEN_API_SECRET,  sandbox=self.demo)
        self._trade = Trade(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET, sandbox=self.demo)
        self._market = Market(sandbox=self.demo)
        log.info(f"KrakenClient initialised — {'DEMO' if self.demo else 'LIVE'} mode")

    # ── Market data ────────────────────────────────────────────────────────────
    def get_ohlcv(self, pair=SPOT_PAIR, interval=15, since=None):
        """Fetch OHLCV via public Kraken REST (same endpoint for demo/live)."""
        params = {"pair": pair, "interval": interval}
        if since:
            params["since"] = since
        resp = requests.get("https://api.kraken.com/0/public/OHLC", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error") and data["error"]:
            raise ValueError(f"Kraken OHLC error: {data['error']}")
        key = [k for k in data["result"] if k != "last"][0]
        return data["result"][key]

    def get_ticker(self, pair=SPOT_PAIR):
        """Get current bid/ask/last price via public REST."""
        resp = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error") and data["error"]:
            raise ValueError(f"Kraken ticker error: {data['error']}")
        key = list(data["result"].keys())[0]
        t = data["result"][key]
        return {
            "bid":   float(t["b"][0]),
            "ask":   float(t["a"][0]),
            "last":  float(t["c"][0]),
            "vol24": float(t["v"][1]),
            "vwap":  float(t["p"][1]),
        }

    # ── Account ────────────────────────────────────────────────────────────────
    def get_balance(self):
        """Returns USD cash balance available for trading."""
        try:
            data = self._user.get_wallets()
            cash = data.get("accounts", {}).get("cash", {})
            balances = cash.get("balances", {})
            return float(balances.get("usd", 0))
        except Exception as e:
            log.warning(f"Balance fetch failed: {e}")
            return 0.0

    def get_open_positions(self):
        """Returns list of open positions."""
        try:
            data = self._trade.get_open_positions()
            return data.get("openPositions", [])
        except Exception as e:
            log.warning(f"Open positions fetch failed: {e}")
            return []

    # ── Order placement ────────────────────────────────────────────────────────
    def place_order(self, side, volume_usd, current_price):
        """
        Place a market order.
        side: "buy" or "sell"
        volume_usd: USD value to trade
        current_price: current XBT price
        """
        # Futures contracts are sized in USD for PI_XBTUSD
        size = int(volume_usd)  # PI_XBTUSD contract size is $1 per contract
        log.info(f"Placing {side} order: ${volume_usd:.2f} ({size} contracts) @ ~{current_price}")
        result = self._trade.create_order(
            orderType="mkt",
            side=side,
            size=size,
            symbol="PI_XBTUSD",
        )
        log.info(f"Order result: {result}")
        return result

    def close_position(self, position):
        """Close an open position by placing the opposing market order."""
        side = "sell" if position["side"] == "LONG" else "buy"
        size = int(position.get("entry_value_usd", 0))
        log.info(f"Closing position: {side} {size} contracts")
        result = self._trade.create_order(
            orderType="mkt",
            side=side,
            size=size,
            symbol="PI_XBTUSD",
            reduceOnly=True,
        )
        log.info(f"Close result: {result}")
        return result
