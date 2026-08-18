"""
claude_brain.py
Called by market_monitor.py when a trigger fires.
Step 1: Haiku quickly filters noise (cheap).
Step 2: Sonnet makes the actual trade decision (capable).
All prompts are lean and structured — no conversational fluff.
"""
import json, logging, os, sys, re
from datetime import datetime, timezone
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import (
    ANTHROPIC_API_KEY, HAIKU_MODEL, SONNET_MODEL,
    STATE_FILE, VAULT_DIR, LOG_DIR,
    MAX_POSITION_PCT, STOP_LOSS_PCT, LEVERAGE,
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


# ── JSON parsing helper ────────────────────────────────────────────────────────

def parse_json_response(text):
    """
    Robustly parse JSON from a model response.
    Handles: raw JSON, markdown code blocks, leading/trailing text.
    """
    text = text.strip()

    # Strip markdown code fences if present
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object within the text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    log.error(f"Full unparseable response: {text[:500]}")
    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


# ── Vault helpers ──────────────────────────────────────────────────────────────

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


# ── Step 1: Haiku filter ───────────────────────────────────────────────────────

def haiku_filter(ind, triggers, state):
    key_ind = {k: ind.get(k) for k in ('rsi','macd_hist','bb_pct','vol_ratio','vol_spike')}
    has_pos = bool(state.get('position'))
    prompt = f"""Signal filter for {DISPLAY_PAIR}. Respond ONLY with JSON.

Triggers: {triggers}
Key indicators: {key_ind}
Position open: {has_pos}

Escalate ONLY when:
- BULLISH_CONFLUENCE or BEARISH_CONFLUENCE present
- STOP_LOSS_HIT or DRAWDOWN_LIMIT_HIT present
- Position open AND exit signal firing (RSI_OVERBOUGHT_VOL, BB_UPPER_TOUCH, MACD_CROSS_DOWN)
- Strong entry setup: RSI_OVERSOLD_VOL + BB_LOWER_TOUCH together with no position

Do NOT escalate: overbought signals with no open position, single weak signals.

{{"escalate": true/false, "reason": "one phrase"}}"""

    try:
        resp = ai.messages.create(
            model=HAIKU_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        raw    = resp.content[0].text
        result = parse_json_response(raw)
        log.info(f"Haiku: escalate={result['escalate']} — {result.get('reason', '')}")
        return bool(result.get("escalate", False))
    except Exception as e:
        log.warning(f"Haiku filter error ({e}) — escalating by default")
        return True


# ── Step 2: Sonnet decision ────────────────────────────────────────────────────

def sonnet_decision(ind, triggers, state, vault):
    system = f"""You are an autonomous {DISPLAY_PAIR} trading agent.
You make disciplined decisions based on technical indicators, external market context, and your own trade history.
You learn from past trades and update your strategy over time.

HARD CONSTRAINTS (enforced in code — you cannot override these):
- Max position: {int(MAX_POSITION_PCT * 100)}% of available balance
- Stop-loss: {int(STOP_LOSS_PCT * 100)}% per trade (executed automatically)
- Leverage: {LEVERAGE}:1 only — no margin
- One position at a time only

DECISION OPTIONS:
- OPEN_LONG  -> buy XBT expecting price rise
- CLOSE_POSITION -> exit current open position
- WAIT -> triggers present but conditions not convincing enough

You MUST respond with ONLY a valid JSON object. No preamble, no markdown, no backticks.
Use exactly this structure:
{{
  "action": "OPEN_LONG|CLOSE_POSITION|WAIT",
  "confidence": 0.0,
  "reasoning": "2-3 sentences explaining your decision",
  "position_size_pct": 0.20,
  "strategy_update": "one sentence or null"
}}

IMPORTANT: position_size_pct is a DECIMAL between 0.05 and {MAX_POSITION_PCT}.
0.20 means 20% of balance. NEVER use integers like 20 — that would mean 2000%."""

    ctx  = state.get("market_context", {})
    news = [h.get("title", "") for h in ctx.get("news", {}).get("headlines", [])][:3]
    perf = vault.get("performance", {})
    slim_ind = {k: ind.get(k) for k in ('price','rsi','macd_hist','bb_pct','vol_ratio','vol_spike','macd_cross_up','macd_cross_down')}
    slim_ctx = {
        "fear_greed": f"{ctx.get('fear_greed_value')} {ctx.get('fear_greed_label')} dir:{ctx.get('fear_greed_direction')}",
        "funding":    f"{ctx.get('funding_rate')} ({ctx.get('funding_sentiment')})",
        "sp500":      f"{ctx.get('sp500_direction')} {ctx.get('sp500_change_pct')}% {ctx.get('risk_sentiment')}",
        "dominance":  ctx.get("btc_dominance_pct"),
        "headlines":  news,
    }
    slim_perf = {k: perf.get(k) for k in ('win_rate','profit_factor','total_closed_trades','total_pnl_usd') if perf.get(k) is not None}
    recent = vault.get('recent_trades', [])[-3:]

    user = f"""Indicators: {json.dumps(slim_ind)}
Context: {json.dumps(slim_ctx)}
Triggers: {triggers}
Position: {json.dumps(state.get('position'))}
Balance: ${state.get('balance', 0):.2f} | Peak: ${state.get('peak_balance', 0):.2f} | Trades: {state.get('total_trades', 0)}
Performance: {json.dumps(slim_perf)}
Last 3 decisions: {json.dumps(recent)}
Strategy: {vault.get('strategy_note', 'None yet.')[:300]}"""

    try:
        resp = ai.messages.create(
            model=SONNET_MODEL,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw      = resp.content[0].text
        decision = parse_json_response(raw)
        log.info(
            f"Sonnet: {decision['action']} "
            f"confidence={decision['confidence']} — {decision['reasoning'][:80]}"
        )
        return decision
    except Exception as e:
        log.error(f"Sonnet decision error: {e}")
        return {
            "action":            "WAIT",
            "confidence":        0.0,
            "reasoning":         f"Decision error: {e}",
            "position_size_pct": 0,
            "strategy_update":   None,
        }


# ── Main ──────────────────────────────────────────────────────────────────────

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

    # Clear triggers immediately so monitor doesn't re-invoke brain
    # on the next cycle while this decision is still being processed
    state["triggers"] = []
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2, default=str)
    log.info(f"Triggers cleared: {triggers}")

    # Emergency triggers bypass Haiku and go straight to executor
    emergency = {"STOP_LOSS_HIT", "DRAWDOWN_LIMIT_HIT"}
    if emergency & set(triggers):
        log.warning(f"Emergency trigger detected: {triggers}")
        decision = {
            "action":            "CLOSE_POSITION",
            "confidence":        1.0,
            "reasoning":         f"Emergency guardrail: {list(emergency & set(triggers))}",
            "position_size_pct": 0,
            "strategy_update":   None,
        }
        vault_path = write_vault_entry(decision, state, triggers)
        _invoke_executor(decision, vault_path)
        return

    # Normal flow: Haiku filter → Sonnet decision
    if not haiku_filter(ind, triggers, state):
        log.info("Haiku filtered — signal too weak, skipping Sonnet call")
        return

    vault    = load_vault_summary()
    decision = sonnet_decision(ind, triggers, state, vault)

    if decision.get("strategy_update"):
        append_strategy_note(decision["strategy_update"])

    vault_path = write_vault_entry(decision, state, triggers)

    if decision["action"] in ("OPEN_LONG", "CLOSE_POSITION"):
        _invoke_executor(decision, vault_path)
    else:
        log.info(f"Decision is WAIT — no trade executed")


def _invoke_executor(decision, vault_path):
    import subprocess
    executor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_executor.py")
    payload  = json.dumps({"decision": decision, "vault_path": vault_path})
    subprocess.Popen([sys.executable, executor, payload])
    log.info(f"Executor invoked with action={decision['action']}")


if __name__ == "__main__":
    main()
