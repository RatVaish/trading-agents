"""
trade_executor.py — forex agent with dynamic leverage + long/short P&L

Leverage is chosen by Sonnet at open and locked into state.json on the position.
At close, the executor reads the locked leverage from the position — never from
the decision (which may carry a stale or irrelevant value at close time).

Key maths:
  margin   = balance * position_size_pct
  notional = margin * leverage              (leverage locked at open)
  raw_move = (exit - entry) / entry
  dir_move = raw_move  (LONG) | -raw_move (SHORT)
  lev_pnl  = dir_move * leverage
  gross    = margin * lev_pnl
  net      = gross - spread_cost
"""
import json, logging, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
try:
    from db import write_trade, close_trade
    _DB_ENABLED = True
except Exception as _db_err:
    _DB_ENABLED = False
    print(f"[trade_executor] DB unavailable: {_db_err}")

try:
    from performance_tracker import compute_performance
    _PERF_ENABLED = True
except Exception:
    _PERF_ENABLED = False

from config.config import (
    STATE_FILE, VAULT_DIR, LOG_DIR,
    MAX_POSITION_PCT, STOP_LOSS_PCT, DRAWDOWN_PAUSE_PCT,
    DISPLAY_PAIR, INSTRUMENT,
    MIN_LEVERAGE, MAX_LEVERAGE, SHORT_ENABLED,
)
from oanda_client import OandaClient
from telegram_bot import send_message

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "executor.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

SPREAD_PIPS            = 1.5
PIP_SIZE               = 0.0001
PAPER_STARTING_BALANCE = 150.0


def load_state():
    with open(STATE_FILE) as fp:
        return json.load(fp)


def save_state(state):
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2, default=str)


def clamp_leverage(raw):
    """Clamp and validate leverage value to [MIN, MAX]."""
    try:
        lev = int(raw)
    except (TypeError, ValueError):
        lev = MIN_LEVERAGE
    return max(MIN_LEVERAGE, min(MAX_LEVERAGE, lev))


def guardrail_check(decision, state, balance):
    action = decision["action"]
    peak   = state.get("peak_balance")

    if peak and balance and (peak - balance) / peak >= DRAWDOWN_PAUSE_PCT:
        return False, f"DRAWDOWN_LIMIT: balance ${balance:.2f} vs peak ${peak:.2f}"
    if action in ("OPEN_LONG", "OPEN_SHORT") and state.get("position"):
        return False, "Position already open — cannot open another"
    if action == "CLOSE_POSITION" and not state.get("position"):
        return False, "No open position to close"
    if action == "OPEN_SHORT" and not SHORT_ENABLED:
        return False, "SHORT_ENABLED=False — OPEN_SHORT blocked"

    size_pct = decision.get("position_size_pct", MAX_POSITION_PCT)
    if size_pct > 1:
        size_pct = size_pct / 100.0
        log.warning(f"position_size_pct looks like integer, converting: {size_pct}")
    size_pct = max(0.05, min(size_pct, MAX_POSITION_PCT))
    decision["position_size_pct"] = size_pct

    if decision.get("confidence", 0) < 0.55:
        return False, f"Confidence too low: {decision['confidence']:.2f} < 0.55"
    if balance < 10:
        return False, f"Balance too low: ${balance:.2f}"

    return True, "OK"


def execute_trade(decision, state, oanda, ticker):
    action  = decision["action"]
    balance = state.get("balance", PAPER_STARTING_BALANCE)
    price   = ticker["last"]
    ts      = datetime.now(timezone.utc).isoformat()

    # ── CLOSE ──────────────────────────────────────────────────────────────────
    if action == "CLOSE_POSITION":
        position = state["position"]
        entry    = position["entry_price"]
        side     = position["side"]
        margin   = position["entry_value_usd"]

        # Leverage locked at open — always read from position, never from decision
        leverage = clamp_leverage(position.get("leverage", MIN_LEVERAGE))

        raw_move          = (price - entry) / entry
        directional_move  = raw_move if side == "LONG" else -raw_move
        leveraged_pnl_pct = directional_move * leverage

        spread_cost_pct = (SPREAD_PIPS * PIP_SIZE) / price * 2
        gross_pnl       = margin * leveraged_pnl_pct
        spread_cost_usd = margin * spread_cost_pct
        net_pnl         = gross_pnl - spread_cost_usd
        new_balance     = balance + net_pnl

        log.info(
            f"CLOSE {side} | leverage={leverage}x | raw={raw_move*100:.4f}% "
            f"leveraged={leveraged_pnl_pct*100:.4f}% | "
            f"gross=${gross_pnl:.4f} spread=${spread_cost_usd:.4f} net=${net_pnl:.4f} | "
            f"balance ${balance:.2f} -> ${new_balance:.2f}"
        )
        send_message(
            f"CLOSED {side} — {DISPLAY_PAIR}\n"
            f"Entry: {entry:.5f} → Exit: {price:.5f}\n"
            f"Leverage: {leverage}x | Raw: {raw_move*100:+.4f}% → P&L: {leveraged_pnl_pct*100:+.4f}%\n"
            f"Gross: ${gross_pnl:+.4f} | Spread: -${spread_cost_usd:.4f}\n"
            f"Net P&L: ${net_pnl:+.4f}\n"
            f"Balance: ${balance:.2f} → ${new_balance:.2f}\n"
            f"{decision['reasoning']}"
        )

        vault_path = decision.get("_vault_path")
        if vault_path and os.path.exists(vault_path):
            with open(vault_path) as fp:
                entry_data = json.load(fp)
            entry_data["outcome"]    = "WIN" if net_pnl > 0 else "LOSS"
            entry_data["pnl_pct"]   = round(leveraged_pnl_pct * 100, 6)
            entry_data["pnl_usd"]   = round(net_pnl, 6)
            entry_data["gross_pnl"] = round(gross_pnl, 6)
            entry_data["spread_usd"]= round(spread_cost_usd, 6)
            entry_data["exit_price"]= price
            entry_data["leverage"]  = leverage
            with open(vault_path, "w") as fp:
                json.dump(entry_data, fp, indent=2)

        state["balance"]      = round(new_balance, 2)
        state["position"]     = None
        state["total_trades"] = state.get("total_trades", 0) + 1
        if new_balance > state.get("peak_balance", 0):
            state["peak_balance"] = round(new_balance, 2)

        try:
            oanda.close_all_positions(INSTRUMENT)
        except Exception as e:
            log.warning(f"OANDA close call failed (P&L tracked internally): {e}")

        if _DB_ENABLED:
            close_trade(
                agent_id   = "forex",
                symbol     = "EUR/USD",
                opened_at  = position.get("opened_at", ""),
                outcome    = "WIN" if net_pnl > 0 else "LOSS",
                pnl_pct    = round(leveraged_pnl_pct * 100, 6),
                pnl_usd    = round(net_pnl, 6),
                exit_price = price,
                fees_usd   = round(spread_cost_usd, 6),
            )

        return {"simulated": True, "net_pnl": net_pnl}

    # ── OPEN ───────────────────────────────────────────────────────────────────
    elif action in ("OPEN_LONG", "OPEN_SHORT"):
        leverage  = clamp_leverage(decision.get("leverage", MIN_LEVERAGE))
        size_pct  = decision.get("position_size_pct", 0.20)
        margin    = balance * size_pct
        notional  = margin * leverage
        units     = int(notional / price)
        if units < 1:
            units = 1

        side_label = "LONG" if action == "OPEN_LONG" else "SHORT"
        oanda_side = "buy"  if action == "OPEN_LONG" else "sell"

        if action == "OPEN_LONG":
            stop_price = price * (1 - STOP_LOSS_PCT)
        else:
            stop_price = price * (1 + STOP_LOSS_PCT)

        try:
            oanda.place_order(side=oanda_side, units=units)
        except Exception as e:
            log.warning(f"OANDA order call failed (tracking internally): {e}")

        log.info(
            f"OPEN {action} | leverage={leverage}x | margin=${margin:.2f} ({size_pct*100:.0f}%) "
            f"notional=${notional:.2f} @ {price:.5f}"
        )
        send_message(
            f"OPENED {side_label} — {DISPLAY_PAIR}\n"
            f"Margin: ${margin:.2f} ({int(size_pct*100)}% of ${balance:.2f})\n"
            f"Leverage: {leverage}x | Notional: ${notional:.2f}\n"
            f"Entry: {price:.5f} | Stop: {stop_price:.5f}\n"
            f"{decision['reasoning']}"
        )

        # Leverage locked into position — this is what close will read
        state["position"] = {
            "side":            side_label,
            "entry_price":     price,
            "entry_value_usd": margin,
            "notional_usd":    round(notional, 2),
            "leverage":        leverage,
            "units":           units,
            "opened_at":       ts,
            "stop_loss_price": stop_price,
        }

        if _DB_ENABLED:
            write_trade(
                agent_id          = "forex",
                symbol            = "EUR/USD",
                ts                = ts,
                action            = action,
                side              = side_label,
                confidence        = decision.get("confidence", 0),
                position_size_pct = size_pct,
                entry_price       = price,
                balance_at_trade  = balance,
                reasoning         = decision.get("reasoning", ""),
                triggers          = [],
                indicators        = {},
                strategy_update   = decision.get("strategy_update"),
            )

        return {"simulated": True, "margin": margin, "notional": notional, "leverage": leverage}


def main():
    if len(sys.argv) < 2:
        log.error("No payload passed to executor")
        return
    try:
        payload    = json.loads(sys.argv[1])
        decision   = payload["decision"]
        vault_path = payload.get("vault_path")
        decision["_vault_path"] = vault_path
    except Exception as e:
        log.error(f"Payload parse error: {e}")
        return

    log.info(f"── Executor: {decision['action']} ──")
    state = load_state()
    oanda = OandaClient()

    try:
        ticker = oanda.get_ticker()
    except Exception as e:
        log.error(f"Ticker fetch failed: {e}")
        send_message(f"Executor could not fetch ticker: {e}")
        return

    balance = state.get("balance", PAPER_STARTING_BALANCE)
    if not balance:
        balance = PAPER_STARTING_BALANCE
        state["balance"]      = balance
        state["peak_balance"] = balance

    approved, reason = guardrail_check(decision, state, balance)
    if not approved:
        log.warning(f"Guardrail blocked trade: {reason}")
        send_message(f"Trade blocked by guardrail\n{reason}")
        if "DRAWDOWN_LIMIT" in reason:
            state["trading_paused"] = True
            save_state(state)
            send_message(
                f"TRADING PAUSED — Drawdown limit hit\n"
                f"Balance: ${balance:.2f} | Peak: ${state['peak_balance']:.2f}\n"
                f"Set trading_paused=false in state.json to resume"
            )
        return

    try:
        execute_trade(decision, state, oanda, ticker)
    except Exception as e:
        log.error(f"Order execution failed: {e}")
        send_message(f"Order failed: {e}")
        return

    save_state(state)

    if _PERF_ENABLED and decision.get("action") == "CLOSE_POSITION":
        try:
            compute_performance()
            log.info("Performance stats updated")
        except Exception as _pe:
            log.warning(f"Performance update failed: {_pe}")

    log.info("Executor complete")


if __name__ == "__main__":
    main()
