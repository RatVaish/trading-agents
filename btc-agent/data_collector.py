"""
data_collector.py
Fetches all external context signals used to enrich Claude's decision making.
All calls are non-blocking with timeouts and graceful fallbacks —
a failed data source never stops the monitor cycle.

Sources:
  HIGH VALUE (free, no API key needed):
  - Fear & Greed Index       (alternative.me)
  - BTC Funding Rate         (Kraken futures ticker)
  - Long/Short Ratio         (Kraken futures)
  - News headlines           (CryptoPanic public RSS)
  - S&P 500 daily direction  (Yahoo Finance)

  MEDIUM VALUE (free, no API key needed):
  - On-chain: exchange netflow  (Blockchain.com public stats)
  - BTC dominance            (CoinGecko public API)
  - Open interest            (Kraken futures)
"""
import requests
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TIMEOUT = 6  # seconds per request — never block the monitor cycle


# ── Fear & Greed Index ─────────────────────────────────────────────────────────

def fetch_fear_greed():
    """
    Returns current Fear & Greed value (0=extreme fear, 100=extreme greed)
    and the previous day's value for direction context.
    """
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=2",
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return {
            "fear_greed_value":     int(data[0]["value"]),
            "fear_greed_label":     data[0]["value_classification"],
            "fear_greed_yesterday": int(data[1]["value"]),
            "fear_greed_direction": "up" if int(data[0]["value"]) > int(data[1]["value"]) else "down",
        }
    except Exception as e:
        log.warning(f"Fear & Greed fetch failed: {e}")
        return {
            "fear_greed_value":     None,
            "fear_greed_label":     "unavailable",
            "fear_greed_yesterday": None,
            "fear_greed_direction": None,
        }


# ── Kraken Futures market data ─────────────────────────────────────────────────

def fetch_kraken_futures_context():
    """
    Fetches funding rate, open interest, and mark price from Kraken futures.
    Funding rate: positive = longs pay shorts (market overleveraged long)
                  negative = shorts pay longs (market overleveraged short)
    """
    try:
        resp = requests.get(
            "https://futures.kraken.com/derivatives/api/v3/tickers",
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        tickers = resp.json().get("tickers", [])

        # Find PI_XBTUSD (perpetual inverse BTC/USD)
        xbt = next((t for t in tickers if t.get("symbol") == "PI_XBTUSD"), None)
        if not xbt:
            return _empty_futures_context()

        funding_rate  = xbt.get("fundingRate", 0)
        funding_rate_prediction = xbt.get("fundingRatePrediction", 0)
        open_interest = xbt.get("openInterest", 0)
        mark_price    = xbt.get("markPrice", 0)

        # Interpret funding rate
        if funding_rate > 0.001:
            funding_sentiment = "overleveraged_long"
        elif funding_rate < -0.001:
            funding_sentiment = "overleveraged_short"
        else:
            funding_sentiment = "neutral"

        return {
            "funding_rate":             round(float(funding_rate), 6),
            "funding_rate_prediction":  round(float(funding_rate_prediction), 6),
            "funding_sentiment":        funding_sentiment,
            "open_interest_btc":        round(float(open_interest), 2),
            "mark_price":               round(float(mark_price), 2),
        }
    except Exception as e:
        log.warning(f"Kraken futures context fetch failed: {e}")
        return _empty_futures_context()


def _empty_futures_context():
    return {
        "funding_rate":            None,
        "funding_rate_prediction": None,
        "funding_sentiment":       "unavailable",
        "open_interest_btc":       None,
        "mark_price":              None,
    }


# ── News headlines ─────────────────────────────────────────────────────────────

def fetch_news_headlines(max_headlines=6):
    """
    Fetches recent BTC/crypto news headlines from CryptoPanic public RSS.
    Returns lean list of titles only — enough for Claude to spot major events
    without burning tokens on full article text.
    """
    try:
        resp = requests.get(
	    "https://feeds.feedburner.com/CoinDesk",
            timeout=TIMEOUT,
            headers={"User-Agent": "btc-agent/1.0"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        headlines = []
        for item in items[:max_headlines]:
            title = item.findtext("title", "").strip()
            pub   = item.findtext("pubDate", "").strip()
            if title:
                headlines.append({"title": title, "published": pub})
        return {"headlines": headlines, "source": "cryptopanic"}
    except Exception as e:
        log.warning(f"News fetch failed: {e}")
        # Fallback: try CoinDesk RSS
        try:
            resp = requests.get(
                "https://feeds.feedburner.com/CoinDesk",
                timeout=TIMEOUT,
                headers={"User-Agent": "btc-agent/1.0"}
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            headlines = []
            for item in items[:max_headlines]:
                title = item.findtext("title", "").strip()
                pub   = item.findtext("pubDate", "").strip()
                if title:
                    headlines.append({"title": title, "published": pub})
            return {"headlines": headlines, "source": "coindesk"}
        except Exception as e2:
            log.warning(f"News fallback fetch failed: {e2}")
            return {"headlines": [], "source": "unavailable"}


# ── S&P 500 daily direction ────────────────────────────────────────────────────

def fetch_sp500():
    """
    Fetches SPY via Stooq — more reliable than Yahoo Finance for free use.
    """
    try:
        resp = requests.get(
            "https://stooq.com/q/l/?s=spy.us&f=sd2t2ohlcv&h&e=csv",
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return _empty_sp500()
        headers = lines[0].split(",")
        values  = lines[1].split(",")
        row = dict(zip(headers, values))
        curr  = float(row.get("Close", 0))
        open_ = float(row.get("Open", curr))
        change_pct = round((curr - open_) / open_ * 100, 3) if open_ else 0
        return {
            "sp500_price":      round(curr, 2),
            "sp500_change_pct": change_pct,
            "sp500_direction":  "up" if change_pct > 0 else "down",
            "risk_sentiment":   "risk_on" if change_pct > 0.3
                                else "risk_off" if change_pct < -0.3
                                else "neutral",
        }
    except Exception as e:
        log.warning(f"S&P 500 fetch failed: {e}")
        return _empty_sp500()

def _empty_sp500():
    return {
        "sp500_price":      None,
        "sp500_change_pct": None,
        "sp500_direction":  None,
        "risk_sentiment":   "unavailable",
    }


# ── BTC Dominance ──────────────────────────────────────────────────────────────

def fetch_btc_dominance():
    """Placeholder — dominance APIs not reachable from this server."""
    return {
        "btc_dominance_pct":     None,
        "total_crypto_mcap_usd": None,
        "total_mcap_change_24h": None,
    }

# ── On-chain: BTC exchange netflow ─────────────────────────────────────────────

def fetch_onchain():
    """
    Approximates exchange netflow using Blockchain.com public stats.
    - n_transactions: activity proxy
    - trade_volume_usd: on-chain USD volume
    High exchange inflow = selling pressure. High outflow = accumulation.
    Note: this is an approximation — proper netflow needs Glassnode paid tier.
    """
    try:
        resp = requests.get(
            "https://api.blockchain.info/stats",
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "onchain_txn_count_24h":    data.get("n_tx", None),
            "onchain_volume_usd_24h":   round(float(data.get("trade_volume_usd", 0)), 2),
            "onchain_difficulty":       data.get("difficulty", None),
            "onchain_hash_rate":        round(float(data.get("hash_rate", 0)), 2),
        }
    except Exception as e:
        log.warning(f"On-chain fetch failed: {e}")
        return {
            "onchain_txn_count_24h":  None,
            "onchain_volume_usd_24h": None,
            "onchain_difficulty":     None,
            "onchain_hash_rate":      None,
        }


# ── Master collector ───────────────────────────────────────────────────────────

def collect_all():
    """
    Runs all data collectors and returns a single enriched context dict.
    Each source is independent — failures don't cascade.
    Called once per monitor cycle (every 60s) but some sources
    are cached to avoid hammering free APIs.
    """
    log.info("Collecting external context data...")

    context = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    context.update(fetch_fear_greed())
    context.update(fetch_kraken_futures_context())
    context.update(fetch_sp500())
    context.update(fetch_btc_dominance())
    context.update(fetch_onchain())
    context["news"] = fetch_news_headlines()

    log.info(
        f"Context collected — "
        f"F&G={context.get('fear_greed_value')} "
        f"({context.get('fear_greed_label')}) | "
        f"Funding={context.get('funding_rate')} "
        f"({context.get('funding_sentiment')}) | "
        f"SP500={context.get('sp500_direction')} "
        f"{context.get('sp500_change_pct')}% | "
        f"BTC dom={context.get('btc_dominance_pct')}%"
    )

    return context


if __name__ == "__main__":
    import json, logging
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(collect_all(), indent=2))
