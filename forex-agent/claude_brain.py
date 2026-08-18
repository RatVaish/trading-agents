"""
claude_brain.py — forex agent with dynamic leverage + long/short support

Key fix: valid actions shown to Sonnet are conditional on current position state,
preventing wrong close actions (e.g. OPEN_SHORT when already LONG).
"""
import json, logging, os, sys, re
from datetime import datetime, timezone
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import (
    ANTHROPIC_API_KEY, HAIKU_MODEL, SONNET_MODEL,
    STATE_FILE, VAULT_DIR, LOG_DIR,
    MAX_POSITION_PCT, STOP_LOSS_PCT,
    MIN_LEVERAGE, MAX_LEVERAGE, SHORT_ENABLED,
    DISPLAY_PAIR,
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
    log.error(f"Full unparseable response: {text[:500]}")
    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


def load_vault_summary():
    summary = {}
    trades_dir = os.path.join(VAULT_DIR, "trades")
    if os.path.exists(trades_dir):
        files  = sorted(f for f in os.listdir(trades_dir) if f.endswith(".json"))[-10:]
        trades = []
        for fname in files:
            try:
                with open(os.path.join(trades_dir, fname)) as fp:
                    t = json.load(fp)
                    trades.append({
                        "ts":        t.get("timestamp"),
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
            summary["strategy_note"] = fp.read()[-600:]

    perf = os.path.join(VAULT_DIR, "reports", "performance.json")
    if os.path.exists(perf):
        with open(perf) as fp:
            summary["performance"] = json.load(fp)

    return summary


def write_vault_entry(decision, state, triggers):
    ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    entry = {
        "timestamp":  ts,
        "triggers":   triggers,
        "indicators": state.get("last_indicators", {}),
        "decision":   decision,
        "balance":    state.get("balance"),
        "position":   state.get("position"),
        "outcome":    None,
        "pnl_pct":    None,
    }
    path = os.path.join(VAULT_DIR, "trades", f"{ts}_decision.json")
    with open(path, "w") as fp:
        json.dump(entry, fp, indent=2)
    log.info(f"Vault entry: {path}")
    return path


def append_strategy_note(note):
    strat = os.path.join(VAULT_DIR, "strategy", "current.md")
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(strat, "a") as fp:
        fp.write(f"\n## {ts}\n{note}\n")


def haiku_filter(ind, triggers, state):
    position = state.get("position")
    pos_side = position.get("side") if position else None

    prompt = f"""You are a signal quality filter for a {DISPLAY_PAIR} forex trading system.
This system trades both LONG and SHORT with dynamic leverage ({MIN_LEVERAGE}x-{MAX_LEVERAGE}x).

Triggers: {triggers}
Key indicators: { {k: ind.get(k) for k in ('rsi','macd_hist','bb_pct','vol_ratio')} }
Position open: {bool(position)} (side: {pos_side})

Escalate ONLY when:
- BULLISH_CONFLUENCE or BEARISH_CONFLUENCE present
- STOP_LOSS_HIT or DRAWDOWN_LIMIT_HIT present
- LONG open AND bearish exit signals fire (RSI_OVERBOUGHT_VOL, BB_UPPER_TOUCH, MACD_CROSS_DOWN)
- SHORT open AND bullish exit signals fire (RSI_OVERSOLD_VOL, BB_LOWER_TOUCH, MACD_CROSS_UP)
- Bullish entry signals with no position (RSI_OVERSOLD_VOL, BB_LOWER_TOUCH, MACD_CROSS_UP)
- Bearish entry signals with no position (RSI_OVERBOUGHT_VOL, BB_UPPER_TOUCH, MACD_CROSS_DOWN)

Do NOT escalate: single weak signals, same combo already evaluated in last 30 min.

Respond ONLY with JSON: {{"escalate": true, "reason": "brief reason"}}"""

    try:
        resp = ai.messages.create(
            model=HAIKU_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result = parse_json_response(resp.content[0].text)
        log.info(f"Haiku: escalate={result['escalate']} — {result.get('reason', '')}")
        return bool(result.get("escalate", False))
    except Exception as e:
        log.warning(f"Haiku filter error ({e}) — escalating by default")
        return True


def _valid_actions_for_state(pos_side):
    """Return action options text conditioned on current position state."""
    if pos_side == "LONG":
        return (
            "- CLOSE_POSITION → exit your existing LONG position (only valid close action)\n"
            "- WAIT           → hold, conditions not convincing enough\n"
            "\n"
            "YOU MUST NOT return OPEN_LONG or OPEN_SHORT. You already have a LONG open. To exit it, use CLOSE_POSITION."
        )
    elif pos_side == "SHORT":
        return (
            "- CLOSE_POSITION → exit your existing SHORT position (only valid close action)\n"
            "- WAIT           → hold, conditions not convincing enough\n"
            "\n"
            "YOU MUST NOT return OPEN_LONG or OPEN_SHORT. You already have a SHORT open. To exit it, use CLOSE_POSITION."
        )
    else:
        short_open = "- OPEN_SHORT → sell EUR/USD expecting EUR to weaken (uses leverage)\n" if SHORT_ENABLED else ""
        return (
            "- OPEN_LONG  → buy EUR/USD expecting EUR to strengthen (uses leverage)\n"
            f"{short_open}"
            "- WAIT       → conditions not convincing enough\n"
            "\n"
            "YOU MUST NOT return CLOSE_POSITION — you have no open position to close."
        )


def _valid_action_enum(pos_side):
    if pos_side in ("LONG", "SHORT"):
        return "CLOSE_POSITION|WAIT"
    elif SHORT_ENABLED:
        return "OPEN_LONG|OPEN_SHORT|WAIT"
    else:
        return "OPEN_LONG|WAIT"


def sonnet_decision(ind, triggers, state, vault):
    position = state.get("position")
    pos_side = position.get("side") if position else None

    valid_actions = _valid_actions_for_state(pos_side)
    action_enum   = _valid_action_enum(pos_side)

    system = f"""You are an autonomous {DISPLAY_PAIR} forex trading agent with dynamic leverage control.

━━━ CURRENT POSITION STATE ━━━
Position side: {pos_side or 'NONE (flat)'}
Valid actions RIGHT NOW:
{valid_actions}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FOREX CONTEXT RULES:
- DXY up + risk_off + VIX high = bearish EUR/USD bias → favour OPEN_SHORT
- DXY down + risk_on + VIX low = bullish EUR/USD bias → favour OPEN_LONG
- High-impact events = reduce leverage or WAIT
- London/NY overlap (13:00-17:00 UTC) = highest reliability

LEVERAGE DECISION (only for OPEN_LONG / OPEN_SHORT):
Use {MAX_LEVERAGE}x when: BULLISH/BEARISH_CONFLUENCE, VIX < 15, London/NY overlap, confidence >= 0.80
Use 3x-5x when: good signal, moderate VIX 15-25, confidence 0.65-0.79
Use 1x-2x when: VIX > 25, high-impact event imminent, confidence 0.55-0.64
For CLOSE_POSITION / WAIT: set leverage=1 (ignored at close)

HARD CONSTRAINTS:
- Max position margin: {int(MAX_POSITION_PCT*100)}% of balance
- Stop-loss: {int(STOP_LOSS_PCT*100)}% on notional
- Leverage clamped to [{MIN_LEVERAGE}, {MAX_LEVERAGE}]
- One position at a time only

Respond with ONLY a valid JSON object:
{{
  "action": "{action_enum}",
  "leverage": 1,
  "confidence": 0.0,
  "reasoning": "2-3 sentences including macro context and leverage rationale",
  "position_size_pct": 0.20,
  "strategy_update": "one sentence or null"
}}

position_size_pct is a decimal between 0.05 and {MAX_POSITION_PCT}. Never use integers."""

    ctx  = state.get("market_context", {})
    news = [h.get("title", "") for h in ctx.get("news", {}).get("headlines", [])][:3]
    perf = vault.get("performance", {})
    slim_ind = {k: ind.get(k) for k in ('price','rsi','macd_hist','bb_pct','vol_ratio','macd_cross_up','macd_cross_down')}
    slim_ctx = {
        "dxy":    f"{ctx.get('dxy_price')} {ctx.get('usd_strength')}",
        "sp500":  f"{ctx.get('sp500_direction')} {ctx.get('sp500_change_pct')}% {ctx.get('risk_sentiment')}",
        "gold":   f"{ctx.get('gold_direction')} {ctx.get('gold_change_pct')}%",
        "vix":    f"{ctx.get('vix_level')} {ctx.get('vix_regime')}",
        "events": ctx.get("event_risk"),
        "news":   news,
    }
    slim_perf = {k: perf.get(k) for k in ('win_rate','profit_factor','total_closed_trades','total_pnl_usd') if perf.get(k) is not None}
    recent = vault.get('recent_trades', [])[-3:]

    pos_display = None
    if position:
        locked_lev = position.get("leverage", 1)
        pos_display = {
            "side":              pos_side,
            "entry_price":       position.get("entry_price"),
            "margin_usd":        position.get("entry_value_usd"),
            "locked_leverage":   locked_lev,
            "notional_exposure": round(position.get("entry_value_usd", 0) * locked_lev, 2) if isinstance(locked_lev, (int, float)) else "?",
            "stop_loss_price":   position.get("stop_loss_price"),
        }

    user = f"""Indicators: {json.dumps(slim_ind)}
Context: {json.dumps(slim_ctx)}
Triggers: {triggers}
Current position: {json.dumps(pos_display)}

REMINDER: valid action(s) right now = {action_enum}

Balance: ${state.get('balance', 0):.2f} | Peak: ${state.get('peak_balance', 0):.2f} | Trades: {state.get('total_trades', 0)}
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
            f"Sonnet: {decision['action']} leverage={decision['leverage']}x "
            f"confidence={decision['confidence']} — {decision['reasoning'][:80]}"
        )

        # Hard safety guards — last line of defence
        action = decision["action"]
        if action in ("OPEN_LONG", "OPEN_SHORT") and position:
            log.warning(f"Open action returned but position already open ({pos_side}) — WAIT")
            decision["action"] = "WAIT"
        elif action == "CLOSE_POSITION" and not position:
            log.warning(f"CLOSE_POSITION returned but no open position — WAIT")
            decision["action"] = "WAIT"
        elif action == "OPEN_SHORT" and not SHORT_ENABLED:
            log.warning(f"OPEN_SHORT returned but SHORT_ENABLED=False — WAIT")
            decision["action"] = "WAIT"

        return decision
    except Exception as e:
        log.error(f"Sonnet decision error: {e}")
        return {
            "action": "WAIT", "leverage": MIN_LEVERAGE, "confidence": 0.0,
            "reasoning": f"Decision error: {e}",
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

    triggers = state.get("triggers", [])
    ind      = state.get("last_indicators", {})

    if not triggers:
        log.info("No triggers in state — nothing to do")
        return

    state["triggers"] = []
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2, default=str)
    log.info(f"Triggers cleared: {triggers}")

    emergency = {"STOP_LOSS_HIT", "DRAWDOWN_LIMIT_HIT"}
    if emergency & set(triggers):
        log.warning(f"Emergency trigger: {triggers}")
        decision = {
            "action": "CLOSE_POSITION", "leverage": 1, "confidence": 1.0,
            "reasoning": f"Emergency guardrail: {list(emergency & set(triggers))}",
            "position_size_pct": 0, "strategy_update": None,
        }
        vault_path = write_vault_entry(decision, state, triggers)
        _invoke_executor(decision, vault_path)
        return

    if not haiku_filter(ind, triggers, state):
        log.info("Haiku filtered — signal too weak")
        return

    vault    = load_vault_summary()
    decision = sonnet_decision(ind, triggers, state, vault)

    if decision.get("strategy_update"):
        append_strategy_note(decision["strategy_update"])

    vault_path = write_vault_entry(decision, state, triggers)

    if decision["action"] in ("OPEN_LONG", "OPEN_SHORT", "CLOSE_POSITION"):
        _invoke_executor(decision, vault_path)
    else:
        log.info("Decision is WAIT — no trade")
        try:
            with open(STATE_FILE) as fp:
                fresh_state = json.load(fp)
            fresh_state["wait_until"]    = datetime.now(timezone.utc).timestamp() + 1800
            fresh_state["wait_triggers"] = sorted(triggers)
            with open(STATE_FILE, "w") as fp:
                json.dump(fresh_state, fp, indent=2, default=str)
            log.info(f"Wait cooldown set 30 mins on triggers {triggers}")
        except Exception as e:
            log.warning(f"Could not write wait_until: {e}")


def _invoke_executor(decision, vault_path):
    import subprocess
    executor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_executor.py")
    payload  = json.dumps({"decision": decision, "vault_path": vault_path})
    subprocess.Popen([sys.executable, executor, payload])
    log.info(f"Executor invoked: {decision['action']} leverage={decision.get('leverage')}x")


if __name__ == "__main__":
    main()
