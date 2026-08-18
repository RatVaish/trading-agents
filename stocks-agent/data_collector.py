"""
data_collector.py
Fetches all external context signals used to enrich Claude's decision making.
All calls are non-blocking with timeouts and graceful fallbacks.

Sources (all free, no extra API keys):
  - VIX level                  (Yahoo Finance fallback chain)
  - SPY trend                  (stooq.com)
  - Sector ETF direction       (stooq.com — XLK, XLF, XLE, XLV)
  - Earnings calendar          (WSJ RSS)
  - Financial news headlines   (MarketWatch, Reuters, NYTimes RSS)
"""
import requests
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

log = logging.getLogger(__name__)
TIMEOUT = 6


# ── VIX level ──────────────────────────────────────────────────────────────────

def fetch_vix():
    """
    Fetches VIX with a multi-source fallback chain.
    VIX interpretation:
      < 15  = low fear, complacency, risk-on
      15-20 = normal range
      20-30 = elevated fear
      > 30  = high fear, potential capitulation / mean reversion
    """
    # Try stooq with multiple ticker formats
    for ticker in ["vix.us", "^vix"]:
        try:
            resp = requests.get(
                f"https://stooq.com/q/l/?s={ticker}&f=sd2t2ohlcv&h&e=csv",
                timeout=TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                continue
            headers = lines[0].split(",")
            values  = lines[1].split(",")
            row = dict(zip(headers, values))
            close = row.get("Close", "")
            if not close or close.strip() in ("N/D", "", "null"):
                continue
            vix = float(close)
            if vix <= 0:
                continue
            return {"vix": round(vix, 2), "vix_regime": _vix_regime(vix)}
        except Exception as e:
            log.warning(f"VIX stooq ({ticker}) failed: {e}")
            continue

    # Fallback: Yahoo Finance
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d",
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        data   = resp.json()
        result = data["chart"]["result"][0]
        vix    = float(result["meta"]["regularMarketPrice"])
        log.info(f"VIX from Yahoo Finance: {vix}")
        return {"vix": round(vix, 2), "vix_regime": _vix_regime(vix)}
    except Exception as e:
        log.warning(f"VIX Yahoo fallback failed: {e}")

    return _empty_vix()


def _vix_regime(vix):
    if vix < 15:
        return "low_volatility"
    elif vix < 20:
        return "normal"
    elif vix < 30:
        return "elevated_fear"
    else:
        return "high_fear"


def _empty_vix():
    return {"vix": None, "vix_regime": "unavailable"}


# ── SPY trend ──────────────────────────────────────────────────────────────────

def fetch_spy_trend():
    """
    Fetches SPY daily bar from stooq for macro trend direction.
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
            return _empty_spy()
        headers = lines[0].split(",")
        values  = lines[1].split(",")
        row = dict(zip(headers, values))
        close = float(row.get("Close", 0))
        open_ = float(row.get("Open", close))
        high  = float(row.get("High", close))
        low   = float(row.get("Low", close))

        if close == 0:
            return _empty_spy()

        change_pct = round((close - open_) / open_ * 100, 3) if open_ else 0

        if change_pct > 0.5:
            trend = "strong_up"
        elif change_pct > 0:
            trend = "up"
        elif change_pct > -0.5:
            trend = "down"
        else:
            trend = "strong_down"

        risk_sentiment = (
            "risk_on"  if change_pct >  0.3 else
            "risk_off" if change_pct < -0.3 else
            "neutral"
        )

        return {
            "spy_price":          round(close, 2),
            "spy_open":           round(open_, 2),
            "spy_change_pct":     change_pct,
            "spy_trend":          trend,
            "spy_intraday_range": round(high - low, 2),
            "risk_sentiment":     risk_sentiment,
        }
    except Exception as e:
        log.warning(f"SPY trend fetch failed: {e}")
        return _empty_spy()


def _empty_spy():
    return {
        "spy_price": None, "spy_open": None,
        "spy_change_pct": None, "spy_trend": None,
        "spy_intraday_range": None, "risk_sentiment": "unavailable",
    }


# ── Sector ETF direction ───────────────────────────────────────────────────────

def fetch_sector_etfs():
    """
    Fetches key sector ETFs to understand rotation context.
    XLK = Tech  | XLF = Financials  | XLE = Energy  | XLV = Healthcare
    """
    etfs = {
        "XLK": "tech",
        "XLF": "financials",
        "XLE": "energy",
        "XLV": "healthcare",
    }
    results = {}
    for ticker, sector in etfs.items():
        try:
            resp = requests.get(
                f"https://stooq.com/q/l/?s={ticker.lower()}.us&f=sd2t2ohlcv&h&e=csv",
                timeout=TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                results[f"sector_{sector}"] = None
                continue
            hdrs = lines[0].split(",")
            vals = lines[1].split(",")
            row  = dict(zip(hdrs, vals))
            close = float(row.get("Close", 0))
            open_ = float(row.get("Open", close))
            if close == 0:
                results[f"sector_{sector}"] = None
                continue
            chg = round((close - open_) / open_ * 100, 3) if open_ else 0
            results[f"sector_{sector}"] = {
                "ticker":     ticker,
                "price":      round(close, 2),
                "change_pct": chg,
                "direction":  "up" if chg > 0 else "down",
            }
        except Exception as e:
            log.warning(f"Sector ETF {ticker} fetch failed: {e}")
            results[f"sector_{sector}"] = None
    return results


# ── Earnings calendar ──────────────────────────────────────────────────────────

def fetch_earnings_calendar():
    """
    Checks WSJ RSS for earnings mentions of watchlist symbols.
    Claude will be extra cautious around earnings announcements.
    """
    from config.config import WATCHLIST
    try:
        resp = requests.get(
            "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
            timeout=TIMEOUT,
            headers={"User-Agent": "stocks-agent/1.0"}
        )
        resp.raise_for_status()
        root  = ET.fromstring(resp.content)
        items = root.findall(".//item")
        earnings_mentions = []
        for item in items[:15]:
            title = item.findtext("title", "").strip()
            for sym in WATCHLIST:
                if sym in title.upper() and any(
                    word in title.lower()
                    for word in ["earn", "result", "quarter", "profit", "revenue", "guidance"]
                ):
                    earnings_mentions.append({
                        "symbol":   sym,
                        "headline": title,
                        "source":   "wsj_rss",
                    })
        return {"earnings_mentions": earnings_mentions, "source": "wsj_rss"}
    except Exception as e:
        log.warning(f"Earnings calendar fetch failed: {e}")
        return {"earnings_mentions": [], "source": "unavailable"}


# ── Financial news ─────────────────────────────────────────────────────────────

def fetch_financial_news(max_headlines=8):
    """
    Fetches financial news from multiple RSS sources, newest first.
    """
    from config.config import WATCHLIST
    feeds = [
        ("https://feeds.reuters.com/reuters/businessNews",          "reuters_business"),
        ("https://feeds.reuters.com/reuters/technologyNews",         "reuters_tech"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "nytimes"),
        ("https://www.ft.com/rss/home/us",                          "ft"),
        ("https://feeds.a.dj.com/rss/RSSMarketsMain.xml",           "wsj_markets"),
    ]
    all_headlines = []
    for url, source in feeds:
        try:
            resp = requests.get(
                url, timeout=TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (compatible; stocks-agent/1.0)"}
            )
            resp.raise_for_status()
            root  = ET.fromstring(resp.content)
            items = root.findall(".//item")
            for item in items[:max_headlines]:
                title = item.findtext("title", "").strip()
                pub   = item.findtext("pubDate", "").strip()
                if not title:
                    continue
                mentioned = [s for s in WATCHLIST if s in title.upper()]
                all_headlines.append({
                    "title":              title,
                    "published":          pub,
                    "source":             source,
                    "watchlist_mentions": mentioned,
                })
        except Exception as e:
            log.warning(f"News feed {source} failed: {e}")
            continue

    if not all_headlines:
        return {"headlines": [], "source": "unavailable"}

    # Sort by published date descending, best-effort
    def parse_pub(h):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(h["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    all_headlines.sort(key=parse_pub, reverse=True)
    top = all_headlines[:max_headlines]
    sources_used = list(dict.fromkeys(h["source"] for h in top))
    log.info(f"News: {len(top)} headlines from {sources_used}")
    return {"headlines": top, "source": ", ".join(sources_used)}

# ── Master collector ───────────────────────────────────────────────────────────

def collect_all():
    """
    Runs all data collectors and returns a single enriched context dict.
    Each source is independent — failures don't cascade.
    """
    log.info("Collecting external context data...")

    context = {"collected_at": datetime.now(timezone.utc).isoformat()}

    context.update(fetch_vix())
    context.update(fetch_spy_trend())
    context.update(fetch_sector_etfs())
    context["earnings"] = fetch_earnings_calendar()
    context["news"]     = fetch_financial_news()

    log.info(
        f"Context collected — "
        f"VIX={context.get('vix')} ({context.get('vix_regime')}) | "
        f"SPY={context.get('spy_trend')} {context.get('spy_change_pct')}% | "
        f"Risk={context.get('risk_sentiment')} | "
        f"Tech={context.get('sector_tech', {}) and context.get('sector_tech', {}).get('change_pct')}%"
    )

    return context


if __name__ == "__main__":
    import json, logging
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(collect_all(), indent=2))
