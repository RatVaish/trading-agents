"""
claude_brain.py — stocks agent with dynamic leverage + long/short support

Sonnet decides leverage per trade (1–MAX_LEVERAGE) based on:
  - Signal strength and confluence
  - VIX regime
  - Earnings risk on the symbol
  - Time of day (open/close more volatile)
  - Recent performance

Key fix: valid actions shown to Sonnet are conditional on current position state,
preventing BUY_TO_COVER being returned for a LONG position, etc.
"""
import json, logging, os, sys, re
from datetime import datetime, timezone
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import (
    ANTHROPIC_API_KEY, HAIKU_MODEL, SONNET_MODEL,
    STATE_FILE, VAULT_DIR, LOG_DIR,
    MAX_POSITION_PCT, STOP_LOSS_PCT, MAX_OPEN_POSITIONS,
    MIN_LEVERAGE, MAX_LEVERAGE, SHORT_ENABLED, WATCHLIST,
)

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "brain.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def parse_json_response(text):
    text = text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    log.error(f"Unparseable response: {text[:500]}")
    raise ValueError(f"Could not parse JSON: {text[:200]}")


def load_vault_summary(symbol=None):
    summary = {}
    trades_dir = os.path.join(VAULT_DIR, "trades")
    if os.path.exists(trades_dir):
        files = sorted(f for f in os.listdir(trades_dir) if f.endswith(".json"))
        if symbol:
            files = [f for f in files if f"_{symbol}_" in f]
        files = files[-10:]
        trades = []
        for fname in files:
            try:
                with open(os.path.join(trades_dir, fname)) as fp:
                    t = json.load(fp)
                    trades.append({
                        "ts":        t.get("timestamp"),
                        "symbol":    t.get("symbol"),
                        "action":    t.get("decision", {}).get("action"),
                        "leverage":  t.get("decision", {}).get("leverage"),
                        "reasoning": t.get("decision", {}).get("reasoning"),
                        "outcome":   t.get("outcome"),
                        "pnl_pct":   t.get("pnl_pct"),
                    })
            except Exception:
                pass
        summary["recent_trades"] = trades

    strat = os.path.join(VAULT_DIR, "strategy", "current.md")
    if os.path.exists(strat):
        with open(strat) as fp:
            summary["strategy_note"] = fp.read()[-800:]

    perf = os.path.join(VAULT_DIR, "reports", "performance.json")
    if os.path.exists(perf):
        with open(perf) as fp:
            summary["performance"] = json.load(fp)

    return summary


def write_vault_entry(symbol, decision, state, triggers):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    entry = {
        "timestamp":  ts,
        "symbol":     symbol,
        "triggers":   triggers,
        "indicators": state.get("last_indicators", {}).get(symbol, {}),
        "decision":   decision,
        "balance":    state.get("balance"),
        "position":   state.get("positions", {}).get(symbol),
        "outcome":    None,
        "pnl_pct":    None,
    }
    os.makedirs(os.path.join(VAULT_DIR, "trades"), exist_ok=True)
    path = os.path.join(VAULT_DIR, "trades", f"{ts}_{symbol}_decision.json")
    with open(path, "w") as fp:
        json.dump(entry, fp, indent=2)
    log.info(f"Vault entry: {path}")
    return path


def append_strategy_note(note):
    strat = os.path.join(VAULT_DIR, "strategy", "current.md")
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(strat, "a") as fp:
        fp.write(f"\n## {ts}\n{note}\n")


def haiku_filter(symbol, ind, triggers, state):
    pos      = state.get("positions", {}).get(symbol)
    pos_side = pos.get("side") if pos else None

    short_ctx = (
        f"SHORT positions allowed. Current {symbol} side: {pos_side or 'none'}."
        if SHORT_ENABLED else "Long-only."
    )

    prompt = f"""You are a signal quality filter for a US stocks system with dynamic leverage ({MIN_LEVERAGE}x-{MAX_LEVERAGE}x).
{short_ctx}

Symbol: {symbol} | Triggers: {triggers}
RSI:{ind.get('rsi','')} BB%:{ind.get('bb_pct','')} MACD:{ind.get('macd_hist','')} VolSpike:{ind.get('vol_spike','')}
Position: {bool(pos)} (side: {pos_side}) | VIX: {state.get('market_context', {}).get('vix')}

Escalate ONLY when:
- BULLISH_CONFLUENCE or BEARISH_CONFLUENCE present
- STOP_LOSS_HIT or DRAWDOWN_LIMIT_HIT present
- LONG open AND bearish exit fires (RSI_OVERBOUGHT_VOL, BB_UPPER_TOUCH, MACD_CROSS_DOWN)
- SHORT open AND bullish exit fires (RSI_OVERSOLD_VOL, BB_LOWER_TOUCH, MACD_CROSS_UP)
- RSI_OVERSOLD_VOL + BB_LOWER_TOUCH with no position (capitulation → long entry)
- RSI_OVERBOUGHT_VOL + BB_UPPER_TOUCH with no position (exhaustion → short entry)

Do NOT escalate: single signals alone.

Respond ONLY with JSON: {{"escalate": true, "reason": "brief reason"}}"""

    try:
        resp = ai.messages.create(
            model=HAIKU_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result = parse_json_response(resp.content[0].text)
        log.info(f"Haiku [{symbol}]: escalate={result['escalate']} — {result.get('reason', '')}")
        return bool(result.get("escalate", False))
    except Exception as e:
        log.warning(f"Haiku filter error for {symbol} ({e}) — escalating by default")
        return True


def _valid_actions_for_state(pos_side, n_open):
    """
    Return the exact set of valid actions given the current position state.
    This is injected directly into the prompt so Sonnet only sees actions it can legally take.
    No ambiguity, no wrong action possible.
    """
    if pos_side == "LONG":
        return (
            "- SELL         → CLOSE your existing LONG position\n"
            "- SELL_SHORT   → if you want to go short: this will first close your LONG, then the next cycle opens the SHORT\n"
            "- WAIT         → hold, conditions not convincing enough to act\n"
            "\n"
            "If bearish signals are strong enough to short, return SELL_SHORT and the system will close your LONG first."
        )
    elif pos_side == "SHORT":
        return (
            "- BUY_TO_COVER → CLOSE your existing SHORT position\n"
            "- BUY          → if you want to go long: this will first close your SHORT, then the next cycle opens the LONG\n"
            "- WAIT         → hold, conditions not convincing enough to act\n"
            "\n"
            "If bullish signals are strong enough to go long, return BUY and the system will close your SHORT first."
        )
    else:
        # No position — can open long, open short (if enabled), or wait
        short_open = "- SELL_SHORT   → open a new SHORT position (profit if price falls)\n" if SHORT_ENABLED else ""
        can_open   = n_open < MAX_OPEN_POSITIONS
        if can_open:
            return (
                "- BUY          → open a new LONG position (profit if price rises)\n"
                f"{short_open}"
                "- WAIT         → conditions not convincing enough\n"
                "\n"
                "YOU MUST NOT return SELL or BUY_TO_COVER — you have no open position to close."
            )
        else:
            return (
                "- WAIT         → max positions already open, cannot open more\n"
                "\n"
                f"YOU MUST return WAIT. You already have {n_open}/{MAX_OPEN_POSITIONS} positions open."
            )


def _valid_action_enum(pos_side, n_open):
    """Return the action enum string for the JSON schema line."""
    if pos_side == "LONG":
        return "SELL|SELL_SHORT|WAIT"   # SELL_SHORT triggers close-then-short
    elif pos_side == "SHORT":
        return "BUY_TO_COVER|BUY|WAIT"  # BUY triggers close-then-long
    elif n_open >= MAX_OPEN_POSITIONS:
        return "WAIT"
    elif SHORT_ENABLED:
        return "BUY|SELL_SHORT|WAIT"
    else:
        return "BUY|WAIT"


def sonnet_decision(symbol, ind, triggers, state, vault):
    open_positions = state.get("positions", {})
    n_open   = len([p for p in open_positions.values() if p is not None])
    pos      = open_positions.get(symbol)
    pos_side = pos.get("side") if pos else None

    # Build action options that are ONLY valid for the current state
    valid_actions = _valid_actions_for_state(pos_side, n_open)
    action_enum   = _valid_action_enum(pos_side, n_open)

    system = f"""You are an autonomous US stock trading agent with dynamic leverage control.
Watchlist: {', '.join(WATCHLIST)}.

━━━ CURRENT POSITION STATE FOR {symbol} ━━━
Position side: {pos_side or 'NONE (flat)'}
Valid actions RIGHT NOW:
{valid_actions}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LEVERAGE DECISION (only for opening actions BUY or SELL_SHORT):
Use {MAX_LEVERAGE}x when: BULLISH/BEARISH_CONFLUENCE, VIX < 15, confidence >= 0.80
Use 2x-3x when: good signal, moderate VIX 15-25, confidence 0.65-0.79
Use 1x when: earnings risk, VIX > 25, confidence 0.55-0.64, or uncertain
For SELL / BUY_TO_COVER / WAIT: set leverage=1 (ignored)

HARD CONSTRAINTS:
- Max position margin: {int(MAX_POSITION_PCT*100)}% of balance
- Stop-loss: {int(STOP_LOSS_PCT*100)}% on notional
- Leverage clamped to [{MIN_LEVERAGE}, {MAX_LEVERAGE}]
- Max open positions: {MAX_OPEN_POSITIONS}

Respond with ONLY a valid JSON object. No preamble, no markdown:
{{
  "action": "{action_enum}",
  "symbol": "{symbol}",
  "leverage": 1,
  "confidence": 0.0,
  "reasoning": "2-3 sentences explaining decision",
  "position_size_pct": 0.20,
  "strategy_update": "one sentence or null"
}}

position_size_pct is a decimal between 0.05 and {MAX_POSITION_PCT}. Never use integers."""

    ctx      = state.get("market_context", {})
    news     = [h.get("title", "") for h in ctx.get("news", {}).get("headlines", [])][:3]
    earnings = ctx.get("earnings", {}).get("earnings_mentions", [])
    perf     = vault.get("performance", {})
    slim_ind = {k: ind.get(k) for k in ('price','rsi','macd_hist','bb_pct','vol_ratio','vol_spike','macd_cross_up','macd_cross_down')}
    slim_ctx = {
        "vix":           f"{ctx.get('vix')} {ctx.get('vix_regime')}",
        "spy":           f"{ctx.get('spy_trend')} {ctx.get('spy_change_pct')}%",
        "sentiment":     ctx.get("risk_sentiment"),
        "earnings_risk": any(e["symbol"] == symbol for e in earnings),
        "news":          news,
    }
    slim_perf = {k: perf.get(k) for k in ('win_rate','profit_factor','total_closed_trades','total_pnl_usd') if perf.get(k) is not None}
    recent = vault.get('recent_trades', [])[-3:]

    pos_display = None
    if pos:
        locked_lev = pos.get("leverage", 1)
        pos_display = {
            "side":            pos_side,
            "entry_price":     pos.get("entry_price"),
            "margin_usd":      pos.get("entry_value_usd"),
            "locked_leverage": locked_lev,
            "notional":        round(pos.get("entry_value_usd", 0) * locked_lev, 2) if isinstance(locked_lev, (int, float)) else "?",
            "stop_loss_price": pos.get("stop_loss_price"),
        }

    user = f"""Symbol: {symbol}
Triggers: {triggers}
Positions open: {n_open}/{MAX_OPEN_POSITIONS}
Current {symbol} position: {json.dumps(pos_display)}

REMINDER: valid action(s) for {symbol} right now = {action_enum}

Indicators: {json.dumps(slim_ind)}
Context: {json.dumps(slim_ctx)}
Balance: ${state.get('balance', 0):.2f} | Peak: ${state.get('peak_balance', 0):.2f}
Leverage range: {MIN_LEVERAGE}x – {MAX_LEVERAGE}x (for opens only)
Performance: {json.dumps(slim_perf)}
Last 3 decisions: {json.dumps(recent)}
Strategy: {vault.get('strategy_note', 'None yet.')[:300]}"""

    try:
        resp = ai.messages.create(
            model=SONNET_MODEL,
            max_tokens=450,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        decision = parse_json_response(resp.content[0].text)

        # Clamp leverage
        raw_lev = decision.get("leverage", MIN_LEVERAGE)
        try:
            raw_lev = int(raw_lev)
        except (TypeError, ValueError):
            raw_lev = MIN_LEVERAGE
        decision["leverage"] = max(MIN_LEVERAGE, min(MAX_LEVERAGE, raw_lev))

        log.info(
            f"Sonnet [{symbol}]: {decision['action']} leverage={decision['leverage']}x "
            f"conf={decision['confidence']} — {decision.get('reasoning','')[:80]}"
        )

        # Hard safety guards — direction conflict handling
        action = decision["action"]
        if not SHORT_ENABLED and action in ("SELL_SHORT", "BUY_TO_COVER"):
            log.warning(f"[{symbol}] Short action but SHORT_ENABLED=False — WAIT")
            decision["action"] = "WAIT"

        # Direction flip: wants to go short but holds long → close the long first
        elif action == "SELL_SHORT" and pos_side == "LONG":
            log.info(f"[{symbol}] SELL_SHORT requested while LONG — closing LONG first, short opens next cycle")
            decision["action"] = "SELL"
            decision["reasoning"] = "(Closing LONG to flip short) " + decision.get("reasoning", "")

        # Direction flip: wants to go long but holds short → close the short first
        elif action == "BUY" and pos_side == "SHORT":
            log.info(f"[{symbol}] BUY requested while SHORT — closing SHORT first, long opens next cycle")
            decision["action"] = "BUY_TO_COVER"
            decision["reasoning"] = "(Closing SHORT to flip long) " + decision.get("reasoning", "")

        # True conflicts that should never happen with the conditional prompt — convert to WAIT
        elif action == "SELL" and pos_side != "LONG":
            log.warning(f"[{symbol}] SELL but no LONG position — WAIT")
            decision["action"] = "WAIT"
        elif action == "BUY_TO_COVER" and pos_side != "SHORT":
            log.warning(f"[{symbol}] BUY_TO_COVER but no SHORT position — WAIT")
            decision["action"] = "WAIT"
        elif action == "BUY" and pos_side == "LONG":
            log.warning(f"[{symbol}] BUY but already LONG — WAIT")
            decision["action"] = "WAIT"
        elif action == "SELL_SHORT" and pos_side == "SHORT":
            log.warning(f"[{symbol}] SELL_SHORT but already SHORT — WAIT")
            decision["action"] = "WAIT"

        return decision
    except Exception as e:
        log.error(f"Sonnet decision error for {symbol}: {e}")
        return {
            "action": "WAIT", "symbol": symbol, "leverage": MIN_LEVERAGE,
            "confidence": 0.0, "reasoning": f"Decision error: {e}",
            "position_size_pct": 0, "strategy_update": None,
        }


def main():
    log.info("── Brain invoked ──")

    if not os.path.exists(STATE_FILE):
        log.error("state.json missing — run market_monitor.py first")
        return

    with open(STATE_FILE) as fp:
        state = json.load(fp)

    if state.get("trading_paused"):
        log.info("Trading paused — brain skipping")
        return

    all_triggers = state.get("triggers", {})
    if not any(all_triggers.values()):
        log.info("No triggers in state — nothing to do")
        return

    symbols_with_triggers = {
        sym: triggers
        for sym, triggers in all_triggers.items()
        if triggers
    }

    state["triggers"] = {sym: [] for sym in WATCHLIST}
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2, default=str)
    log.info(f"Triggers cleared: {symbols_with_triggers}")

    for symbol, triggers in symbols_with_triggers.items():
        ind = state.get("last_indicators", {}).get(symbol, {})
        if not ind:
            log.warning(f"No indicators for {symbol} — skipping")
            continue

        emergency = {"STOP_LOSS_HIT", "DRAWDOWN_LIMIT_HIT"}
        if emergency & set(triggers):
            log.warning(f"Emergency trigger for {symbol}: {triggers}")
            pos      = state.get("positions", {}).get(symbol)
            pos_side = pos.get("side") if pos else "LONG"
            close_action = "SELL" if pos_side == "LONG" else "BUY_TO_COVER"
            decision = {
                "action": close_action, "symbol": symbol,
                "leverage": 1, "confidence": 1.0,
                "reasoning": f"Emergency guardrail: {list(emergency & set(triggers))}",
                "position_size_pct": 0, "strategy_update": None,
            }
            vault_path = write_vault_entry(symbol, decision, state, triggers)
            _invoke_executor(decision, vault_path)
            continue

        if not haiku_filter(symbol, ind, triggers, state):
            log.info(f"Haiku filtered {symbol} — signal too weak")
            continue

        vault    = load_vault_summary(symbol)
        decision = sonnet_decision(symbol, ind, triggers, state, vault)

        if decision.get("strategy_update"):
            append_strategy_note(f"[{symbol}] {decision['strategy_update']}")

        vault_path = write_vault_entry(symbol, decision, state, triggers)

        executable_actions = ("BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER")
        if decision["action"] in executable_actions:
            _invoke_executor(decision, vault_path)
            positions = state.setdefault("positions", {})
            if decision["action"] == "BUY":
                positions[symbol] = {
                    "symbol": symbol, "side": "LONG",
                    "entry_price": ind.get("price", 0),
                    "leverage": decision["leverage"],
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
            elif decision["action"] == "SELL_SHORT":
                positions[symbol] = {
                    "symbol": symbol, "side": "SHORT",
                    "entry_price": ind.get("price", 0),
                    "leverage": decision["leverage"],
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
            elif decision["action"] in ("SELL", "BUY_TO_COVER"):
                positions[symbol] = None
        else:
            log.info(f"[{symbol}] Decision is WAIT — no trade")


def _invoke_executor(decision, vault_path):
    import subprocess
    executor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_executor.py")
    payload  = json.dumps({"decision": decision, "vault_path": vault_path})
    subprocess.Popen([sys.executable, executor, payload])
    log.info(f"Executor invoked: {decision['symbol']} {decision['action']} leverage={decision.get('leverage')}x")


if __name__ == "__main__":
    main()
