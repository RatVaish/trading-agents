"""
oanda_client.py
Single wrapper for all OANDA API interactions.
Uses OANDA's REST v20 API.
Demo (practice) vs live is controlled by OANDA_DEMO in .env.

OANDA practice account: https://www.oanda.com/register/#/sign-up/demo
API docs: https://developer.oanda.com/rest-live-v20/introduction/
"""
import requests
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from config.config import (
    OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_DEMO,
    INSTRUMENT, CANDLE_INTERVAL, CANDLE_LIMIT,
)

log = logging.getLogger(__name__)

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if OANDA_DEMO else
    "https://api-fxtrade.oanda.com"
)


class OandaClient:

    def __init__(self):
        self.demo = OANDA_DEMO
        self.account_id = OANDA_ACCOUNT_ID
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {OANDA_API_KEY}",
            "Content-Type":  "application/json",
        })
        log.info(f"OandaClient initialised — {'DEMO/PRACTICE' if self.demo else 'LIVE'} mode")

    def _get(self, path, params=None):
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body):
        url = f"{BASE_URL}{path}"
        resp = self.session.post(url, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_ohlcv(self, instrument=INSTRUMENT, granularity=CANDLE_INTERVAL, count=CANDLE_LIMIT):
        data = self._get(
            f"/v3/instruments/{instrument}/candles",
            params={"granularity": granularity, "count": count, "price": "M"}
        )
        candles = []
        for c in data.get("candles", []):
            if not c.get("complete", True):
                continue
            mid = c.get("mid", {})
            candles.append({
                "time":   c["time"],
                "open":   float(mid.get("o", 0)),
                "high":   float(mid.get("h", 0)),
                "low":    float(mid.get("l", 0)),
                "close":  float(mid.get("c", 0)),
                "volume": int(c.get("volume", 0)),
            })
        return candles

    def get_ticker(self, instrument=INSTRUMENT):
        data = self._get(
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instrument}
        )
        prices = data.get("prices", [])
        if not prices:
            raise ValueError(f"No price data for {instrument}")
        p = prices[0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        mid = round((bid + ask) / 2, 5)
        return {"bid": bid, "ask": ask, "last": mid, "spread": round(ask - bid, 5)}

    def get_balance(self):
        try:
            data = self._get(f"/v3/accounts/{self.account_id}/summary")
            return float(data["account"]["balance"])
        except Exception as e:
            log.warning(f"Balance fetch failed: {e}")
            return 0.0

    def get_open_positions(self):
        try:
            data = self._get(f"/v3/accounts/{self.account_id}/openPositions")
            return data.get("positions", [])
        except Exception as e:
            log.warning(f"Open positions fetch failed: {e}")
            return []

    def get_open_trades(self):
        try:
            data = self._get(f"/v3/accounts/{self.account_id}/openTrades")
            return data.get("trades", [])
        except Exception as e:
            log.warning(f"Open trades fetch failed: {e}")
            return []

    def place_order(self, side, units, instrument=INSTRUMENT):
        if side == "sell":
            units = -abs(units)
        else:
            units = abs(units)
        body = {
            "order": {
                "type":       "MARKET",
                "instrument": instrument,
                "units":      str(units),
            }
        }
        log.info(f"Placing {side} order: {units} units of {instrument}")
        result = self._post(f"/v3/accounts/{self.account_id}/orders", body)
        log.info(f"Order result: {result}")
        return result

    def close_trade(self, trade_id):
        log.info(f"Closing trade {trade_id}")
        result = self._post(f"/v3/accounts/{self.account_id}/trades/{trade_id}/close", {})
        log.info(f"Close result: {result}")
        return result

    def close_all_positions(self, instrument=INSTRUMENT):
        try:
            trades = self.get_open_trades()
            results = []
            for trade in trades:
                if trade.get("instrument") == instrument:
                    results.append(self.close_trade(trade["id"]))
            return results
        except Exception as e:
            log.error(f"Close all positions failed: {e}")
            return []


if __name__ == "__main__":
    import json, logging
    logging.basicConfig(level=logging.INFO)
    client = OandaClient()
    print("Ticker:", json.dumps(client.get_ticker(), indent=2))
    print("Balance:", client.get_balance())
