"""
market_monitor.py
Runs every 60s via cron. Fetches XBT/USD candles from Kraken, calculates
technical indicators, checks trigger conditions, writes state.json.
Never calls the Claude API — that's claude_brain.py's job.
"""
import json, logging, os, sys, subprocess
from datetime import datetime, timezone
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
try:
    from db import write_agent_state, write_ohlcv, write_equity
    _DB_ENABLED = True
except Exception as _db_err:
    _DB_ENABLED = False
    print(f"[market_monitor] DB unavailable: {_db_err}")

from config.config import (
    SPOT_PAIR, STATE_FILE, LOG_DIR,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, VOL_SPIKE_MULT,
    CANDLE_INTERVAL, CANDLE_LIMIT,
    STOP_LOSS_PCT, DRAWDOWN_PAUSE_PCT,
)
from kraken_client import KrakenClient
from data_collector import collect_all

os.makedirs(LOG_DIR, exist_ok=True)

CONTEXT_REFRESH_CYCLES = {
    "fear_greed":   20,
    "futures":       2,
    "sp500":        40,
    "dominance":    40,
    "onchain":      60,
    "news":         10,
}
_cycle_counter = 0
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "monitor.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ── Indicator calculations ─────────────────────────────────────────────────────

def calc_rsi(series, period=RSI_PERIOD):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    ema_f  = series.ewm(span=fast, adjust=False).mean()
    ema_s  = series.ewm(span=slow, adjust=False).mean()
    line   = ema_f - ema_s
    sig    = line.ewm(span=signal, adjust=False).mean()
    hist   = line - sig
    return line, sig, hist


def calc_bollinger(series, period=BB_PERIOD, std=BB_STD):
    sma   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return sma + std * sigma, sma, sma - std * sigma


def compute_indicators(df):
    close  = df["close"]
    volume = df["volume"]

    rsi               = calc_rsi(close)
    macd, sig, hist   = calc_macd(close)
    bb_upper, bb_mid, bb_lower = calc_bollinger(close)

    vol_avg_24  = volume.rolling(96).mean().iloc[-1]
    vol_now     = volume.iloc[-1]
    price       = close.iloc[-1]
    prev_price  = close.iloc[-2]

    bb_u = float(bb_upper.iloc[-1])
    bb_l = float(bb_lower.iloc[-1])
    bb_range = bb_u - bb_l if bb_u != bb_l else 1e-9

    return {
        "price":             round(float(price), 2),
        "price_change_pct":  round(float((price - prev_price) / prev_price * 100), 4),
        "rsi":               round(float(rsi.iloc[-1]), 2),
        "macd":              round(float(macd.iloc[-1]), 4),
        "macd_signal":       round(float(sig.iloc[-1]), 4),
        "macd_hist":         round(float(hist.iloc[-1]), 4),
        "macd_cross_up":     bool(hist.iloc[-1] > 0 and hist.iloc[-2] <= 0),
        "macd_cross_down":   bool(hist.iloc[-1] < 0 and hist.iloc[-2] >= 0),
        "bb_upper":          round(bb_u, 2),
        "bb_mid":            round(float(bb_mid.iloc[-1]), 2),
        "bb_lower":          round(bb_l, 2),
        "bb_pct":            round(float((price - bb_l) / bb_range), 4),
        "vol_spike":         bool(vol_now > vol_avg_24 * VOL_SPIKE_MULT),
        "vol_ratio":         round(float(vol_now / (vol_avg_24 + 1e-9)), 2),
    }


# ── Trigger logic ──────────────────────────────────────────────────────────────

def check_triggers(ind, state):
    triggers = []

    if ind["rsi"] < RSI_OVERSOLD and ind["vol_spike"]:
        triggers.append("RSI_OVERSOLD_VOL")

    if ind["rsi"] > RSI_OVERBOUGHT and ind["vol_spike"]:
        triggers.append("RSI_OVERBOUGHT_VOL")

    if ind["macd_cross_up"]:
        triggers.append("MACD_CROSS_UP")

    if ind["macd_cross_down"]:
        triggers.append("MACD_CROSS_DOWN")

    if ind["bb_pct"] < 0.05:
        triggers.append("BB_LOWER_TOUCH")

    if ind["bb_pct"] > 0.95:
        triggers.append("BB_UPPER_TOUCH")

    if ind["rsi"] < 40 and ind["macd_cross_up"] and ind["bb_pct"] < 0.25:
        triggers.append("BULLISH_CONFLUENCE")

    if ind["rsi"] > 60 and ind["macd_cross_down"] and ind["bb_pct"] > 0.75:
        triggers.append("BEARISH_CONFLUENCE")

    pos = state.get("position")
    if pos:
        entry   = pos["entry_price"]
        current = ind["price"]
        pnl_pct = ((current - entry) / entry) if pos["side"] == "LONG" \
                  else ((entry - current) / entry)
        if pnl_pct <= -STOP_LOSS_PCT:
            triggers.append("STOP_LOSS_HIT")

    peak = state.get("peak_balance")
    bal  = state.get("balance")
    if peak and bal and (peak - bal) / peak >= DRAWDOWN_PAUSE_PCT:
        triggers.append("DRAWDOWN_LIMIT_HIT")

    return triggers


# ── State helpers ──────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "balance":        None,
        "peak_balance":   None,
        "position":       None,
        "trading_paused": False,
        "total_trades":   0,
        "triggers":       [],
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ── Helpers ────────────────────────────────────────────────────────────────────

def should_refresh(state, key, every_n_cycles):
    last    = state.get("context_refresh_cycles", {}).get(key, -999)
    current = state.get("cycle_count", 0)
    return (current - last) >= every_n_cycles


def append_equity_snapshot(balance):
    equity_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault", "reports", "equity.jsonl")
    os.makedirs(os.path.dirname(equity_path), exist_ok=True)
    with open(equity_path, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "balance": balance}) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _cycle_counter
    log.info("── Monitor cycle ──")
    state = load_state()

    if state.get("trading_paused"):
        log.info("Trading paused (drawdown limit reached). Skipping cycle.")
        return

    state["cycle_count"] = state.get("cycle_count", 0) + 1
    cycle = state["cycle_count"]
    if "context_refresh_cycles" not in state:
        state["context_refresh_cycles"] = {}

    kraken = KrakenClient()

    try:
        raw = kraken.get_ohlcv(pair=SPOT_PAIR, interval=CANDLE_INTERVAL)
        df  = pd.DataFrame(raw, columns=[
            "time", "open", "high", "low", "close", "vwap", "volume", "count"
        ])
        for col in ["open", "high", "low", "close", "vwap", "volume"]:
            df[col] = df[col].astype(float)
        df = df.tail(CANDLE_LIMIT).reset_index(drop=True)
    except Exception as e:
        log.error(f"OHLCV fetch failed: {e}")
        return

    try:
        ind = compute_indicators(df)
    except Exception as e:
        log.error(f"Indicator calculation failed: {e}")
        return

    if not state.get("balance"):
        state["balance"]      = 150.0
        state["peak_balance"] = 150.0
        log.info("Initialised paper trading balance: $150.00")

    existing = state.get("market_context", {})

    if should_refresh(state, "futures", CONTEXT_REFRESH_CYCLES["futures"]):
        from data_collector import fetch_kraken_futures_context
        existing.update(fetch_kraken_futures_context())
        state["context_refresh_cycles"]["futures"] = cycle

    if should_refresh(state, "fear_greed", CONTEXT_REFRESH_CYCLES["fear_greed"]):
        from data_collector import fetch_fear_greed
        existing.update(fetch_fear_greed())
        state["context_refresh_cycles"]["fear_greed"] = cycle

    if should_refresh(state, "sp500", CONTEXT_REFRESH_CYCLES["sp500"]):
        from data_collector import fetch_sp500
        existing.update(fetch_sp500())
        state["context_refresh_cycles"]["sp500"] = cycle

    if should_refresh(state, "dominance", CONTEXT_REFRESH_CYCLES["dominance"]):
        from data_collector import fetch_btc_dominance
        existing.update(fetch_btc_dominance())
        state["context_refresh_cycles"]["dominance"] = cycle

    if should_refresh(state, "onchain", CONTEXT_REFRESH_CYCLES["onchain"]):
        from data_collector import fetch_onchain
        existing.update(fetch_onchain())
        state["context_refresh_cycles"]["onchain"] = cycle

    if should_refresh(state, "news", CONTEXT_REFRESH_CYCLES["news"]):
        from data_collector import fetch_news_headlines
        existing["news"] = fetch_news_headlines()
        state["context_refresh_cycles"]["news"] = cycle

    existing["context_updated_at"] = datetime.now(timezone.utc).isoformat()
    state["market_context"] = existing

    triggers = check_triggers(ind, state)

    state["last_indicators"] = ind
    state["last_checked"]    = datetime.now(timezone.utc).isoformat()
    state["triggers"]        = triggers
    save_state(state)
    append_equity_snapshot(state["balance"])

    # ── DB writes ─────────────────────────────────────────────────────────────
    if _DB_ENABLED:
        write_agent_state("btc", state)
        # Write equity with unrealised P&L so sparkline reflects open positions
        _eq_balance = state["balance"]
        _pos = state.get("position")
        if _pos and ind:
            _entry = _pos.get("entry_price", 0)
            _size  = _pos.get("entry_value_usd", 0)
            _side  = _pos.get("side", "LONG")
            _price = ind.get("price", 0)
            if _entry and _price and _size:
                _upnl = _size * ((_price - _entry) / _entry if _side == "LONG" else (_entry - _price) / _entry)
                _eq_balance = round(state["balance"] + _upnl, 4)
        write_equity("btc", _eq_balance)
        # Write last CLOSED candle (iloc[-2]) — iloc[-1] is still forming
        try:
            latest = df.iloc[-2]
            write_ohlcv("btc", "XBT/USD", {
                "ts":     pd.Timestamp(latest["time"], unit="s", tz="UTC").isoformat(),
                "open":   float(latest["open"]),
                "high":   float(latest["high"]),
                "low":    float(latest["low"]),
                "close":  float(latest["close"]),
                "volume": float(latest["volume"]),
            }, interval_mins=CANDLE_INTERVAL)
        except Exception as _e:
            log.warning(f"OHLCV write failed: {_e}")

    no_trigger_log = (
        f"No triggers. "
        f"Price={ind['price']} RSI={ind['rsi']} "
        f"MACD_hist={ind['macd_hist']} BB%={ind['bb_pct']} | "
        f"F&G={existing.get('fear_greed_value')} "
        f"Funding={existing.get('funding_rate')} "
        f"SP500={existing.get('sp500_direction')}"
    )

    if triggers:
        now        = datetime.now(timezone.utc)
        last_brain = state.get("last_brain_call")
        in_cooldown = False

        # Priority triggers ALWAYS bypass cooldown — never suppress these
        priority     = {'BULLISH_CONFLUENCE', 'BEARISH_CONFLUENCE', 'STOP_LOSS_HIT', 'DRAWDOWN_LIMIT_HIT'}
        has_priority = bool(priority & set(triggers))

        if not has_priority and last_brain:
            try:
                elapsed = (now - datetime.fromisoformat(last_brain)).total_seconds()
                if elapsed < 120:
                    log.info(f"Triggers {triggers} but cooldown active ({int(elapsed)}s ago) — skipping brain")
                    in_cooldown = True
            except Exception:
                pass

        if not in_cooldown:
            state["last_brain_call"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            log.info(f"Triggers fired: {triggers} → invoking claude_brain.py")
            brain = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_brain.py")
            subprocess.Popen([sys.executable, brain])
        else:
            log.info(no_trigger_log)
    else:
        log.info(no_trigger_log)


if __name__ == "__main__":
    main()
