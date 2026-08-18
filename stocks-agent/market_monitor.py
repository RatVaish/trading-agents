"""
market_monitor.py
Runs every 5 minutes via cron during NYSE hours (Mon-Fri 14:30-21:00 UTC).
Fetches bars for all watchlist symbols, calculates technical indicators,
checks trigger conditions, writes state.json.
Never calls the Claude API — that's claude_brain.py's job.

Updated for dynamic leverage + short positions:
  - Stop-loss check handles both LONG and SHORT directions per symbol
  - Unrealised P&L equity write applies locked leverage and handles shorts
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
    WATCHLIST, STATE_FILE, LOG_DIR,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, VOL_SPIKE_MULT,
    CANDLE_INTERVAL, CANDLE_LIMIT,
    STOP_LOSS_PCT, DRAWDOWN_PAUSE_PCT,
    MIN_LEVERAGE, MAX_LEVERAGE,
    MARKET_OPEN_UTC, MARKET_CLOSE_UTC,
    PAPER_STARTING_BALANCE,
)
from alpaca_client import AlpacaClient
from data_collector import (
    fetch_vix, fetch_spy_trend, fetch_sector_etfs,
    fetch_financial_news, fetch_earnings_calendar,
)

os.makedirs(LOG_DIR, exist_ok=True)

CONTEXT_REFRESH_CYCLES = {
    "vix":      6,
    "spy":      2,
    "sectors":  6,
    "earnings": 60,
    "news":     4,
}

logging.basicConfig(
    filename=os.path.join(LOG_DIR, "monitor.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def is_market_hours():
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    open_h, open_m   = MARKET_OPEN_UTC
    close_h, close_m = MARKET_CLOSE_UTC
    open_mins  = open_h  * 60 + open_m
    close_mins = close_h * 60 + close_m
    now_mins   = now.hour * 60 + now.minute
    return open_mins <= now_mins < close_mins


def calc_rsi(series, period=RSI_PERIOD):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    line  = ema_f - ema_s
    sig   = line.ewm(span=signal, adjust=False).mean()
    hist  = line - sig
    return line, sig, hist


def calc_bollinger(series, period=BB_PERIOD, std=BB_STD):
    sma   = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return sma + std * sigma, sma, sma - std * sigma


def compute_indicators(bars):
    df = pd.DataFrame(bars)
    df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "t": "time"}, inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close", "volume"]).reset_index(drop=True)
    if len(df) < 30:
        raise ValueError(f"Not enough bars: {len(df)}")

    close  = df["close"]
    volume = df["volume"]
    rsi               = calc_rsi(close)
    macd, sig, hist   = calc_macd(close)
    bb_upper, bb_mid, bb_lower = calc_bollinger(close)
    vol_avg = volume.rolling(20).mean().iloc[-1]
    vol_now = volume.iloc[-1]
    price      = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    bb_u = float(bb_upper.iloc[-1])
    bb_l = float(bb_lower.iloc[-1])
    bb_range = bb_u - bb_l if bb_u != bb_l else 1e-9

    return {
        "price":            round(price, 4),
        "price_change_pct": round((price - prev_price) / prev_price * 100, 4),
        "rsi":              round(float(rsi.iloc[-1]), 2),
        "macd":             round(float(macd.iloc[-1]), 6),
        "macd_signal":      round(float(sig.iloc[-1]), 6),
        "macd_hist":        round(float(hist.iloc[-1]), 6),
        "macd_cross_up":    bool(hist.iloc[-1] > 0 and hist.iloc[-2] <= 0),
        "macd_cross_down":  bool(hist.iloc[-1] < 0 and hist.iloc[-2] >= 0),
        "bb_upper":         round(bb_u, 4),
        "bb_mid":           round(float(bb_mid.iloc[-1]), 4),
        "bb_lower":         round(bb_l, 4),
        "bb_pct":           round((price - bb_l) / bb_range, 4),
        "vol_spike":        bool(vol_now > vol_avg * VOL_SPIKE_MULT),
        "vol_ratio":        round(float(vol_now / (vol_avg + 1e-9)), 2),
    }


def check_triggers(symbol, ind, state):
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

    # Stop-loss: handles both LONG and SHORT directions
    positions = state.get("positions", {})
    pos = positions.get(symbol)
    if pos:
        entry   = pos["entry_price"]
        current = ind["price"]
        side    = pos.get("side", "LONG")
        # Raw notional move — STOP_LOSS_PCT is defined on notional
        raw_move = (current - entry) / entry if side == "LONG" else (entry - current) / entry
        if raw_move <= -STOP_LOSS_PCT:
            triggers.append("STOP_LOSS_HIT")

    # Portfolio-level drawdown
    peak = state.get("peak_balance")
    bal  = state.get("balance")
    if peak and bal and (peak - bal) / peak >= DRAWDOWN_PAUSE_PCT:
        triggers.append("DRAWDOWN_LIMIT_HIT")

    return triggers


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "balance":        None,
        "peak_balance":   None,
        "positions":      {sym: None for sym in WATCHLIST},
        "trading_paused": False,
        "total_trades":   0,
        "triggers":       {sym: [] for sym in WATCHLIST},
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def should_refresh(state, key, every_n_cycles):
    last    = state.get("context_refresh_cycles", {}).get(key, 0)
    current = state.get("cycle_count", 0)
    return (current - last) >= every_n_cycles


def append_equity_snapshot(balance):
    equity_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault", "reports", "equity.jsonl")
    os.makedirs(os.path.dirname(equity_path), exist_ok=True)
    with open(equity_path, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "balance": balance}) + "\n")


def main():
    log.info("── Monitor cycle ──")

    if not is_market_hours():
        log.info("Outside market hours — skipping cycle")
        return

    state = load_state()

    if state.get("trading_paused"):
        log.info("Trading paused (drawdown limit). Skipping cycle.")
        return

    state["cycle_count"] = state.get("cycle_count", 0) + 1
    cycle = state["cycle_count"]
    if "context_refresh_cycles" not in state:
        state["context_refresh_cycles"] = {}

    alpaca = AlpacaClient()

    if not state.get("balance"):
        state["balance"]      = PAPER_STARTING_BALANCE
        state["peak_balance"] = PAPER_STARTING_BALANCE
        log.info(f"Initialised paper trading balance: ${PAPER_STARTING_BALANCE:.2f}")

    ctx = state.get("market_context", {})

    if should_refresh(state, "vix", CONTEXT_REFRESH_CYCLES["vix"]):
        ctx.update(fetch_vix())
        state["context_refresh_cycles"]["vix"] = cycle
    if should_refresh(state, "spy", CONTEXT_REFRESH_CYCLES["spy"]):
        ctx.update(fetch_spy_trend())
        state["context_refresh_cycles"]["spy"] = cycle
    if should_refresh(state, "sectors", CONTEXT_REFRESH_CYCLES["sectors"]):
        ctx.update(fetch_sector_etfs())
        state["context_refresh_cycles"]["sectors"] = cycle
    if should_refresh(state, "earnings", CONTEXT_REFRESH_CYCLES["earnings"]):
        ctx["earnings"] = fetch_earnings_calendar()
        state["context_refresh_cycles"]["earnings"] = cycle
    if should_refresh(state, "news", CONTEXT_REFRESH_CYCLES["news"]):
        ctx["news"] = fetch_financial_news()
        state["context_refresh_cycles"]["news"] = cycle

    ctx["context_updated_at"] = datetime.now(timezone.utc).isoformat()
    state["market_context"] = ctx

    # ── Process each symbol ────────────────────────────────────────────────────
    all_triggers   = {}
    all_indicators = {}
    brain_needed   = []
    brain_needed_triggers = {}

    for symbol in WATCHLIST:
        try:
            bars = alpaca.get_bars(symbol)
            ind  = compute_indicators(bars)
            all_indicators[symbol] = ind
            triggers = check_triggers(symbol, ind, state)
            all_triggers[symbol] = triggers

            if triggers:
                log.info(f"{symbol}: triggers={triggers} RSI={ind['rsi']} price={ind['price']}")
                brain_needed.append(symbol)
                brain_needed_triggers[symbol] = triggers
            else:
                log.info(
                    f"{symbol}: no trigger | price={ind['price']} "
                    f"RSI={ind['rsi']} BB%={ind['bb_pct']} "
                    f"MACD_hist={ind['macd_hist']}"
                )

            # Write latest candle to DB
            if _DB_ENABLED and bars:
                try:
                    latest = bars[-2] if len(bars) >= 2 else bars[-1]
                    write_ohlcv("stocks", symbol, {
                        "ts":     str(latest.get("t", latest.get("ts", ""))),
                        "open":   float(latest.get("o", latest.get("open", 0))),
                        "high":   float(latest.get("h", latest.get("high", 0))),
                        "low":    float(latest.get("l", latest.get("low", 0))),
                        "close":  float(latest.get("c", latest.get("close", 0))),
                        "volume": float(latest.get("v", latest.get("volume", 0))),
                    }, interval_mins=5)
                except Exception as _e:
                    log.warning(f"{symbol}: OHLCV write failed: {_e}")

        except Exception as e:
            log.error(f"{symbol}: indicator error — {e}")

    state["last_indicators"] = all_indicators
    state["last_checked"]    = datetime.now(timezone.utc).isoformat()
    state["triggers"]        = all_triggers
    save_state(state)
    append_equity_snapshot(state["balance"])

    # ── DB writes ──────────────────────────────────────────────────────────────
    if _DB_ENABLED:
        write_agent_state("stocks", state)

        # Unrealised P&L — apply leverage locked at open and handle SHORT direction
        _eq_balance = state["balance"]
        _positions  = state.get("positions", {})
        for _sym, _pos in _positions.items():
            if not _pos:
                continue
            _entry    = _pos.get("entry_price", 0)
            _size     = _pos.get("entry_value_usd", 0)
            _side     = _pos.get("side", "LONG")
            _leverage = _pos.get("leverage", 1)
            try:
                _leverage = max(1, min(MAX_LEVERAGE, int(_leverage)))
            except (TypeError, ValueError):
                _leverage = 1
            _ind   = all_indicators.get(_sym, {})
            _price = _ind.get("price", 0)
            if _entry and _price and _size:
                _raw_move = (_price - _entry) / _entry if _side == "LONG" else (_entry - _price) / _entry
                _upnl = _size * _raw_move * _leverage
                _eq_balance = round(_eq_balance + _upnl, 4)
        write_equity("stocks", _eq_balance)

    # ── Invoke brain for symbols with triggers (with cooldown) ─────────────────
    if brain_needed:
        import datetime as dt
        now        = dt.datetime.now(dt.timezone.utc)
        last_brain = state.get("last_brain_call")
        in_cooldown = False

        priority = {'BULLISH_CONFLUENCE', 'BEARISH_CONFLUENCE', 'STOP_LOSS_HIT', 'DRAWDOWN_LIMIT_HIT'}
        all_triggers_flat = [t for ts in brain_needed_triggers.values() for t in ts]
        has_priority = bool(priority & set(all_triggers_flat))

        if not has_priority and last_brain:
            try:
                elapsed = (now - dt.datetime.fromisoformat(last_brain)).total_seconds()
                if elapsed < 180:
                    log.info(f"Brain cooldown active ({int(elapsed)}s) — symbols with triggers: {brain_needed}")
                    in_cooldown = True
            except Exception:
                pass

        if not in_cooldown:
            state["last_brain_call"] = dt.datetime.now(dt.timezone.utc).isoformat()
            save_state(state)
            log.info(f"Invoking claude_brain.py for: {brain_needed}")
            brain = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_brain.py")
            subprocess.Popen([sys.executable, brain])


if __name__ == "__main__":
    main()
