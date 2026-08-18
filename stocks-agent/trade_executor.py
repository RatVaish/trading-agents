"""
trade_executor.py — stocks agent with dynamic leverage + long/short P&L

Leverage is chosen by Sonnet at open and locked into state.json on the position.
At close, executor reads leverage from the position record — never from the
incoming decision (which carries leverage=1 at close time by convention).

Key maths:
  margin   = balance * position_size_pct
  notional = margin * leverage              (leverage locked at open)
  raw_move = (exit - entry) / entry
  dir_move = raw_move  (LONG) | -raw_move (SHORT)
  lev_pnl  = dir_move * leverage
  pnl_usd  = margin * lev_pnl
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
    MAX_OPEN_POSITIONS, PAPER_STARTING_BALANCE,
    MIN_LEVERAGE, MAX_LEVERAGE, SHORT_ENABLED,
)
from alpaca_client import AlpacaClient
from telegram_bot import send_message

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "executor.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def load_state():
    with open(STATE_FILE) as fp:
        return json.load(fp)

def save_state(state):
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2, default=str)


def clamp_leverage(raw):
    try:
        lev = int(raw)
    except (TypeError, ValueError):
        lev = MIN_LEVERAGE
    return max(MIN_LEVERAGE, min(MAX_LEVERAGE, lev))


def guardrail_check(decision, state):
    action  = decision["action"]
    symbol  = decision["symbol"]
    balance = state.get("balance", PAPER_STARTING_BALANCE)

    peak = state.get("peak_balance")
    if peak and balance and (peak - balance) / peak >= DRAWDOWN_PAUSE_PCT:
        return False, f"DRAWDOWN_LIMIT: ${balance:.2f} vs peak ${peak:.2f}"

    positions = state.get("positions", {})
    pos       = positions.get(symbol)
    pos_side  = pos.get("side") if pos else None
    n_open    = len([p for p in positions.values() if p is not None])

    if action == "BUY":
        if pos and pos_side == "LONG":
            return False, f"Already LONG {symbol}"
        if n_open >= MAX_OPEN_POSITIONS:
            return False, f"Max positions reached ({n_open}/{MAX_OPEN_POSITIONS})"
    if action == "SELL_SHORT":
        if not SHORT_ENABLED:
            return False, "SHORT_ENABLED=False — SELL_SHORT blocked"
        if pos and pos_side == "SHORT":
            return False, f"Already SHORT {symbol}"
        if n_open >= MAX_OPEN_POSITIONS:
            return False, f"Max positions reached ({n_open}/{MAX_OPEN_POSITIONS})"
    if action == "SELL":
        if not pos or pos_side != "LONG":
            return False, f"No LONG position in {symbol} to close"
    if action == "BUY_TO_COVER":
        if not pos or pos_side != "SHORT":
            return False, f"No SHORT position in {symbol} to cover"

    size_pct = decision.get("position_size_pct", MAX_POSITION_PCT)
    if size_pct > 1:
        size_pct = size_pct / 100.0
        log.warning(f"position_size_pct looked like integer, converting: {size_pct}")
    size_pct = max(0.05, min(size_pct, MAX_POSITION_PCT))
    decision["position_size_pct"] = size_pct

    if decision.get("confidence", 0) < 0.55:
        return False, f"Confidence too low: {decision['confidence']:.2f}"
    if balance < 10:
        return False, f"Balance too low: ${balance:.2f}"

    return True, "OK"


def get_current_price(alpaca, symbol, state):
    try:
        trade = alpaca.get_latest_trade(symbol)
        return trade["price"]
    except Exception as e:
        log.warning(f"Could not get latest trade for {symbol}: {e}")
        return state.get("last_indicators", {}).get(symbol, {}).get("price", 0)


def execute_trade(decision, state, alpaca):
    action  = decision["action"]
    symbol  = decision["symbol"]
    balance = state.get("balance", PAPER_STARTING_BALANCE)
    ts      = datetime.now(timezone.utc).isoformat()

    # ── CLOSE actions ──────────────────────────────────────────────────────────
    if action in ("SELL", "BUY_TO_COVER"):
        pos         = state["positions"][symbol]
        entry_price = pos["entry_price"]
        margin      = pos["entry_value_usd"]
        side        = pos["side"]

        # Always use leverage locked at open — ignore decision leverage at close
        leverage = clamp_leverage(pos.get("leverage", MIN_LEVERAGE))

        current_price     = get_current_price(alpaca, symbol, state)
        raw_move          = (current_price - entry_price) / entry_price
        directional_move  = raw_move if side == "LONG" else -raw_move
        leveraged_pnl_pct = directional_move * leverage
        pnl_usd           = margin * leveraged_pnl_pct
        new_balance       = balance + pnl_usd

        try:
            alpaca.close_position(symbol)
            log.info(f"Alpaca position closed: {symbol} ({side})")
        except Exception as e:
            log.warning(f"Alpaca close failed for {symbol} (tracking internally): {e}")

        log.info(
            f"CLOSE {side} {symbol} | leverage={leverage}x | "
            f"entry=${entry_price:.2f} exit=${current_price:.2f} | "
            f"raw={raw_move*100:.2f}% lev_pnl={leveraged_pnl_pct*100:.2f}% | "
            f"P&L=${pnl_usd:+.2f} | balance ${balance:.2f} -> ${new_balance:.2f}"
        )
        send_message(
            f"{'SELL' if side=='LONG' else 'COVER'} — {symbol}\n"
            f"Entry: ${entry_price:.2f} → Exit: ${current_price:.2f}\n"
            f"Leverage: {leverage}x | Raw: {raw_move*100:+.2f}% → P&L: {leveraged_pnl_pct*100:+.2f}%\n"
            f"P&L: ${pnl_usd:+.2f}\n"
            f"Balance: ${balance:.2f} → ${new_balance:.2f}\n"
            f"{decision['reasoning']}"
        )

        vault_path = decision.get("_vault_path")
        if vault_path and os.path.exists(vault_path):
            with open(vault_path) as fp:
                entry_data = json.load(fp)
            entry_data["outcome"]    = "WIN" if pnl_usd > 0 else "LOSS"
            entry_data["pnl_pct"]   = round(leveraged_pnl_pct * 100, 4)
            entry_data["pnl_usd"]   = round(pnl_usd, 4)
            entry_data["exit_price"] = current_price
            entry_data["closed_at"]  = ts
            entry_data["leverage"]   = leverage
            with open(vault_path, "w") as fp:
                json.dump(entry_data, fp, indent=2)

        state["balance"]           = round(new_balance, 2)
        state["positions"][symbol] = None
        state["total_trades"]      = state.get("total_trades", 0) + 1
        if new_balance > state.get("peak_balance", 0):
            state["peak_balance"] = round(new_balance, 2)

        if _DB_ENABLED:
            close_trade(
                agent_id   = "stocks",
                symbol     = symbol,
                opened_at  = pos.get("opened_at", ""),
                outcome    = "WIN" if pnl_usd > 0 else "LOSS",
                pnl_pct    = round(leveraged_pnl_pct * 100, 4),
                pnl_usd    = round(pnl_usd, 4),
                exit_price = current_price,
                fees_usd   = 0.0,
            )

        return {"pnl_usd": pnl_usd, "leverage": leverage}

    # ── OPEN actions ───────────────────────────────────────────────────────────
    elif action in ("BUY", "SELL_SHORT"):
        leverage      = clamp_leverage(decision.get("leverage", MIN_LEVERAGE))
        size_pct      = decision.get("position_size_pct", 0.20)
        margin        = balance * size_pct
        notional      = margin * leverage
        current_price = get_current_price(alpaca, symbol, state)
        side_label    = "LONG" if action == "BUY" else "SHORT"
        alpaca_side   = "buy"  if action == "BUY" else "sell"

        if action == "BUY":
            stop_price = current_price * (1 - STOP_LOSS_PCT)
        else:
            stop_price = current_price * (1 + STOP_LOSS_PCT)

        try:
            order    = alpaca.place_market_order(symbol, alpaca_side, notional=notional)
            order_id = order.get("id", "unknown")
            log.info(f"Alpaca {alpaca_side} order: {symbol} notional=${notional:.2f} id={order_id}")
        except Exception as e:
            log.warning(f"Alpaca {alpaca_side} failed for {symbol} (tracking locally): {e}")
            order_id = "local_only"

        log.info(
            f"OPEN {action} {symbol} | leverage={leverage}x | "
            f"margin=${margin:.2f} ({size_pct*100:.0f}%) notional=${notional:.2f} @ ~${current_price:.2f}"
        )
        send_message(
            f"{'BUY' if action=='BUY' else 'SHORT'} — {symbol}\n"
            f"Margin: ${margin:.2f} ({int(size_pct*100)}% of ${balance:.2f})\n"
            f"Leverage: {leverage}x | Notional: ${notional:.2f}\n"
            f"Price: ~${current_price:.2f} | Stop: ${stop_price:.2f}\n"
            f"{decision['reasoning']}"
        )

        # Leverage locked into position
        state["positions"][symbol] = {
            "symbol":          symbol,
            "side":            side_label,
            "entry_price":     current_price,
            "entry_value_usd": margin,
            "notional_usd":    round(notional, 2),
            "leverage":        leverage,
            "size_pct":        size_pct,
            "opened_at":       ts,
            "stop_loss_price": stop_price,
            "alpaca_order_id": order_id,
        }

        if _DB_ENABLED:
            write_trade(
                agent_id          = "stocks",
                symbol            = symbol,
                ts                = ts,
                action            = action,
                side              = side_label,
                confidence        = decision.get("confidence", 0),
                position_size_pct = size_pct,
                entry_price       = current_price,
                balance_at_trade  = balance,
                reasoning         = decision.get("reasoning", ""),
                triggers          = [],
                indicators        = {},
                strategy_update   = decision.get("strategy_update"),
            )

        return {"margin": margin, "notional": notional, "leverage": leverage}


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

    symbol = decision.get("symbol", "UNKNOWN")
    log.info(f"── Executor: {decision['action']} {symbol} leverage={decision.get('leverage')}x ──")

    state  = load_state()
    alpaca = AlpacaClient()

    approved, reason = guardrail_check(decision, state)
    if not approved:
        log.warning(f"Guardrail blocked {symbol}: {reason}")
        send_message(f"Trade blocked ({symbol})\n{reason}")
        if "DRAWDOWN_LIMIT" in reason:
            state["trading_paused"] = True
            save_state(state)
            send_message(
                f"TRADING PAUSED — Drawdown limit hit\n"
                f"Balance: ${state.get('balance', 0):.2f} | "
                f"Peak: ${state.get('peak_balance', 0):.2f}\n"
                f"Set trading_paused=false in state.json to resume"
            )
        return

    try:
        execute_trade(decision, state, alpaca)
    except Exception as e:
        log.error(f"Trade execution failed {symbol}: {e}")
        send_message(f"Order failed ({symbol}): {e}")
        return

    save_state(state)

    close_actions = ("SELL", "BUY_TO_COVER")
    if _PERF_ENABLED and decision.get("action") in close_actions:
        try:
            compute_performance()
            log.info("Performance stats updated")
        except Exception as _pe:
            log.warning(f"Performance update failed: {_pe}")

    log.info("Executor complete")


if __name__ == "__main__":
    main()
