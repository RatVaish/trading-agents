"""
data_collector.py
Fetches all external context signals used to enrich Claude's forex decisions.
All calls are non-blocking with timeouts and graceful fallbacks —
a failed data source never stops the monitor cycle.

Forex-specific signals replace crypto signals:
  HIGH VALUE (free, no API key needed):
  - Economic calendar events        (ForexFactory RSS)
  - USD strength index (DXY)        (Stooq)
  - S&P 500 direction               (Stooq) — risk-on/off proxy
  - EUR/USD news headlines          (ForexLive RSS)

  MEDIUM VALUE (free, no API key needed):
  - Gold price direction            (Stooq) — risk-off proxy
  - US 10Y Treasury yield direction (Stooq) — USD strength proxy
  - VIX index level                 (Stooq) — volatility/risk proxy
"""
import requests
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TIMEOUT = 6  # seconds per request


# ── DXY (US Dollar Index) ──────────────────────────────────────────────────────

def fetch_dxy():
    """
    Fetch DXY (Dollar Index) via Stooq.
    DXY up = USD strong = EUR/USD likely bearish.
    DXY down = USD weak = EUR/USD likely bullish.
    """
    try:
        resp = requests.get(
            "https://stooq.com/q/l/?s=dxy&f=sd2t2ohlcv&h&e=csv",
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return _empty_dxy()
        headers = lines[0].split(",")
        values  = lines[1].split(",")
        row = dict(zip(headers, values))
        curr  = float(row.get("Close", 0))
        open_ = float(row.get("Open", curr))
        change_pct = round((curr - open_) / open_ * 100, 3) if open_ else 0
        return {
            "dxy_price":      round(curr, 3),
            "dxy_change_pct": change_pct,
            "dxy_direction":  "up" if change_pct > 0 else "down",
            "usd_strength":   "strong" if change_pct > 0.2
                              else "weak" if change_pct < -0.2
                              else "neutral",
        }
    except Exception as e:
        log.warning(f"DXY fetch failed: {e}")
        return _empty_dxy()

def _empty_dxy():
    return {
        "dxy_price":      None,
        "dxy_change_pct": None,
        "dxy_direction":  None,
        "usd_strength":   "unavailable",
    }


# ── S&P 500 ────────────────────────────────────────────────────────────────────

def fetch_sp500():
    """
    Fetch SPY via Stooq.
    SP500 up = risk-on = EUR/USD typically supported.
    SP500 down = risk-off = USD safe haven bid = EUR/USD bearish.
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
            "risk_sentiment":   "risk_on"  if change_pct > 0.3
                                else "risk_off" if change_pct < -0.3
                                else "neutral",
        }
    except Exception as e:
        log.warning(f"SP500 fetch failed: {e}")
        return _empty_sp500()

def _empty_sp500():
    return {
        "sp500_price":      None,
        "sp500_change_pct": None,
        "sp500_direction":  None,
        "risk_sentiment":   "unavailable",
    }


# ── Gold ───────────────────────────────────────────────────────────────────────

def fetch_gold():
    """
    Fetch Gold (GC.F) via Stooq.
    Gold up = risk-off/inflation fears = mixed for EUR/USD.
    Gold down = risk-on/USD strength = EUR/USD typically bearish.
    """
    try:
        resp = requests.get(
            "https://stooq.com/q/l/?s=xauusd&f=sd2t2ohlcv&h&e=csv",
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return _empty_gold()
        headers = lines[0].split(",")
        values  = lines[1].split(",")
        row = dict(zip(headers, values))
        curr  = float(row.get("Close", 0))
        open_ = float(row.get("Open", curr))
        change_pct = round((curr - open_) / open_ * 100, 3) if open_ else 0
        return {
            "gold_price":      round(curr, 2),
            "gold_change_pct": change_pct,
            "gold_direction":  "up" if change_pct > 0 else "down",
        }
    except Exception as e:
        log.warning(f"Gold fetch failed: {e}")
        return _empty_gold()

def _empty_gold():
    return {
        "gold_price":      None,
        "gold_change_pct": None,
        "gold_direction":  None,
    }


# ── US 10Y Treasury Yield ──────────────────────────────────────────────────────

def fetch_us10y():
    """
    Fetch US 10Y Treasury yield via Stooq.
    Yield up = USD typically strengthens = EUR/USD bearish.
    Yield down = USD weakens = EUR/USD bullish.
    """
    try:
        resp = requests.get(
            "https://stooq.com/q/l/?s=10us.b&f=sd2t2ohlcv&h&e=csv",
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return _empty_us10y()
        headers = lines[0].split(",")
        values  = lines[1].split(",")
        row = dict(zip(headers, values))
        curr  = float(row.get("Close", 0))
        open_ = float(row.get("Open", curr))
        change_bps = round((curr - open_) * 100, 2)  # basis points
        return {
            "us10y_yield":      round(curr, 3),
            "us10y_change_bps": change_bps,
            "us10y_direction":  "up" if change_bps > 0 else "down",
        }
    except Exception as e:
        log.warning(f"US10Y fetch failed: {e}")
        return _empty_us10y()

def _empty_us10y():
    return {
        "us10y_yield":      None,
        "us10y_change_bps": None,
        "us10y_direction":  None,
    }


# ── VIX ────────────────────────────────────────────────────────────────────────

def fetch_vix():
    """
    Fetch VIX (volatility index) via Stooq.
    VIX > 20 = elevated fear = risk-off = USD safe haven bid.
    VIX < 15 = complacency = risk-on = EUR/USD bullish.
    """
    try:
        resp = requests.get(
            "https://stooq.com/q/l/?s=^vix&f=sd2t2ohlcv&h&e=csv",
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return _empty_vix()
        headers = lines[0].split(",")
        values  = lines[1].split(",")
        row = dict(zip(headers, values))
        curr = float(row.get("Close", 0))
        return {
            "vix_level":     round(curr, 2),
            "vix_regime":    "high_fear"  if curr > 25
                             else "elevated" if curr > 18
                             else "normal"   if curr > 12
                             else "low",
        }
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")
        return _empty_vix()

def _empty_vix():
    return {
        "vix_level":  None,
        "vix_regime": "unavailable",
    }


# ── Forex news headlines ───────────────────────────────────────────────────────

def fetch_news_headlines(max_headlines=6):
    """
    Fetches recent EUR/USD and forex news from ForexLive RSS.
    Falls back to FXStreet RSS if ForexLive fails.
    """
    feeds = [
        ("https://www.forexlive.com/feed/news", "forexlive"),
        ("https://www.fxstreet.com/rss/news",   "fxstreet"),
    ]
    for url, source in feeds:
        try:
            resp = requests.get(
                url,
                timeout=TIMEOUT,
                headers={"User-Agent": "forex-agent/1.0"}
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
            if headlines:
                return {"headlines": headlines, "source": source}
        except Exception as e:
            log.warning(f"News fetch failed ({source}): {e}")

    return {"headlines": [], "source": "unavailable"}


# ── Economic calendar (ForexFactory RSS) ──────────────────────────────────────

def fetch_economic_calendar():
    """
    Fetches upcoming high-impact economic events from ForexFactory.
    High-impact events (NFP, CPI, rate decisions) cause large moves.
    We check if any major events are due today or tomorrow.
    """
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=TIMEOUT,
            headers={"User-Agent": "forex-agent/1.0"}
        )
        resp.raise_for_status()
        events = resp.json()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        high_impact = []
        for event in events:
            if event.get("impact") == "High":
                event_date = event.get("date", "")[:10]
                if event_date >= today:
                    high_impact.append({
                        "title":    event.get("title", ""),
                        "date":     event_date,
                        "currency": event.get("currency", ""),
                        "impact":   event.get("impact", ""),
                    })
        # Only show next 3 upcoming high-impact events
        high_impact = high_impact[:3]
        return {
            "upcoming_high_impact": high_impact,
            "event_risk": "high" if high_impact else "low",
        }
    except Exception as e:
        log.warning(f"Economic calendar fetch failed: {e}")
        return {
            "upcoming_high_impact": [],
            "event_risk": "unknown",
        }


# ── Master collector ───────────────────────────────────────────────────────────

def collect_all():
    """
    Runs all data collectors and returns a single enriched context dict.
    Each source is independent — failures don't cascade.
    """
    log.info("Collecting external forex context data...")

    context = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    context.update(fetch_dxy())
    context.update(fetch_sp500())
    context.update(fetch_gold())
    context.update(fetch_us10y())
    context.update(fetch_vix())
    context.update(fetch_economic_calendar())
    context["news"] = fetch_news_headlines()

    log.info(
        f"Context collected — "
        f"DXY={context.get('dxy_price')} ({context.get('usd_strength')}) | "
        f"SP500={context.get('sp500_direction')} {context.get('sp500_change_pct')}% | "
        f"Gold={context.get('gold_direction')} | "
        f"VIX={context.get('vix_level')} ({context.get('vix_regime')}) | "
        f"Event risk={context.get('event_risk')}"
    )

    return context


if __name__ == "__main__":
    import json, logging
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(collect_all(), indent=2))
