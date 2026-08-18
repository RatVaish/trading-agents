"""
trade_executor.py
Receives a decision from claude_brain.py, validates it against hard guardrails,
places the order on Kraken, updates state.json, and sends a Telegram alert.

Balance is tracked internally — Kraken demo orders don't actually execute
so we simulate the balance ourselves based on trade P&L.
Fees: 0.26% taker on open + 0.26% taker on close = 0.52% round trip.
"""
import json
import logging
import os
import sys
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
    MAX_POSITION_PCT, STOP_LOSS_PCT, DRAWDOWN_PAUSE_PCT, LEVERAGE,
    DISPLAY_PAIR,
)
from kraken_client import KrakenClient
from telegram_bot import send_message

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "executor.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Kraken taker fee (market orders)
FEE_RATE = 0.0026

# Starting balance for internal paper trading simulation
PAPER_STARTING_BALANCE = 150.0


def load_state():
    with open(STATE_FILE) as fp:
        return json.load(fp)


def save_state(state):
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2, default=str)


def guardrail_check(decision, state, balance, ticker):
    """
    Hard safety checks that cannot be overridden by Claude.
    Returns (approved: bool, reason: str).
    """
    action = decision["action"]

    # Drawdown limit — pause everything
    peak = state.get("peak_balance")
    if peak and balance and (peak - balance) / peak >= DRAWDOWN_PAUSE_PCT:
        return False, f"DRAWDOWN_LIMIT: balance ${balance:.2f} vs peak ${peak:.2f}"

    # Don't open a new position if one is already open
    if action in ("OPEN_LONG", "OPEN_SHORT") and state.get("position"):
        return False, "Position already open — cannot open another"

    # Don't close if nothing is open
    if action == "CLOSE_POSITION" and not state.get("position"):
        return False, "No open position to close"

    # Position size — must be decimal 0.05 to MAX_POSITION_PCT
    # Guard against agent returning integer like 20 instead of 0.20
    size_pct = decision.get("position_size_pct", MAX_POSITION_PCT)
    if size_pct > 1:
        size_pct = size_pct / 100.0
        log.warning(f"position_size_pct looked like integer pct, converting: -> {size_pct}")
    if size_pct > MAX_POSITION_PCT:
        size_pct = MAX_POSITION_PCT
        log.warning(f"Position size capped at MAX_POSITION_PCT: {MAX_POSITION_PCT}")
    if size_pct < 0.05:
        size_pct = 0.05
        log.warning(f"Position size floored at 0.05")
    decision["position_size_pct"] = size_pct

    # Minimum confidence threshold
    if decision.get("confidence", 0) < 0.55:
        return False, f"Confidence too low: {decision['confidence']:.2f} < 0.55"

    # Minimum balance check
    if balance < 10:
        return False, f"Balance too low to trade: ${balance:.2f}"

    return True, "OK"


def execute_trade(decision, state, kraken, ticker):
    action = decision["action"]
    balance = state.get("balance", PAPER_STARTING_BALANCE)
    price = ticker["last"]
    ts = datetime.now(timezone.utc).isoformat()

    if action == "CLOSE_POSITION":
        position = state["position"]
        entry = position["entry_price"]
        size = position["entry_value_usd"]

        # Calculate raw P&L based on price movement
        if position["side"] == "LONG":
            pnl_pct = (price - entry) / entry
        else:
            pnl_pct = (entry - price) / entry

        # Deduct fees: 0.26% on open + 0.26% on close
        fee_open = size * FEE_RATE
        fee_close = size * FEE_RATE
        total_fees = fee_open + fee_close

        gross_pnl = size * pnl_pct
        net_pnl = gross_pnl - total_fees
        pnl_pct_show = pnl_pct * 100
        new_balance = balance + net_pnl

        log.info(
            f"Position closed. Gross: ${gross_pnl:.2f} | "
            f"Fees: ${total_fees:.2f} | "
            f"Net: ${net_pnl:.2f} ({pnl_pct_show:.2f}%) | "
            f"Balance: ${balance:.2f} -> ${new_balance:.2f}"
        )

        send_message(
            f"Position closed - {DISPLAY_PAIR}\n"
            f"Entry: ${entry:,.2f} -> Exit: ${price:,.2f}\n"
            f"Gross P&L: ${gross_pnl:+.2f} | Fees: -${total_fees:.2f}\n"
            f"Net P&L: ${net_pnl:+.2f} ({pnl_pct_show:+.2f}%)\n"
            f"Balance: ${balance:.2f} -> ${new_balance:.2f}\n"
            f"{decision['reasoning']}"
        )

        # Update vault entry with outcome
        vault_path = decision.get("_vault_path")
        if vault_path and os.path.exists(vault_path):
            with open(vault_path) as fp:
                entry_data = json.load(fp)
            entry_data["outcome"] = "WIN" if net_pnl > 0 else "LOSS"
            entry_data["pnl_pct"] = round(pnl_pct_show, 4)
            entry_data["pnl_usd"] = round(net_pnl, 4)
            entry_data["gross_pnl"] = round(gross_pnl, 4)
            entry_data["fees_usd"] = round(total_fees, 4)
            entry_data["exit_price"] = price
            with open(vault_path, "w") as fp:
                json.dump(entry_data, fp, indent=2)

        # Update internal balance
        state["balance"] = round(new_balance, 2)
        state["position"] = None
        state["total_trades"] = state.get("total_trades", 0) + 1

        # Update peak balance
        if new_balance > state.get("peak_balance", 0):
            state["peak_balance"] = round(new_balance, 2)

        # Write close to DB
        if _DB_ENABLED:
            close_trade(
                agent_id  = "btc",
                symbol    = "XBT/USD",
                opened_at = position.get("opened_at", ""),
                outcome   = "WIN" if net_pnl > 0 else "LOSS",
                pnl_pct   = round(pnl_pct_show, 4),
                pnl_usd   = round(net_pnl, 4),
                exit_price = price,
                fees_usd  = round(total_fees, 4),
            )

        return {"simulated": True, "net_pnl": net_pnl, "fees": total_fees}

    elif action in ("OPEN_LONG", "OPEN_SHORT"):
        size_pct = decision.get("position_size_pct", 0.20)
        trade_usd = balance * size_pct
        volume_xbt = trade_usd / price

        # Place on Kraken demo for logging (may not execute on exchange)
        try:
            side = "buy" if action == "OPEN_LONG" else "sell"
            kraken.place_order(
                side=side,
                volume_usd=trade_usd,
                current_price=price,
            )
        except Exception as e:
            log.warning(f"Kraken demo order failed (continuing anyway): {e}")

        side_label = "LONG" if action == "OPEN_LONG" else "SHORT"
        stop_price = (
            price * (1 - STOP_LOSS_PCT) if action == "OPEN_LONG"
            else price * (1 + STOP_LOSS_PCT)
        )

        log.info(
            f"Opened {action}: ${trade_usd:.2f} ({size_pct*100:.0f}%) "
            f"@ ${price:,.2f} ({volume_xbt:.6f} XBT) | Balance: ${balance:.2f}"
        )

        send_message(
            f"Position opened {side_label} - {DISPLAY_PAIR}\n"
            f"Size: ${trade_usd:.2f} ({int(size_pct*100)}% of ${balance:.2f})\n"
            f"Entry: ${price:,.2f} | Stop: ${stop_price:,.2f}\n"
            f"{decision['reasoning']}"
        )

        state["position"] = {
            "side": "LONG" if action == "OPEN_LONG" else "SHORT",
            "entry_price": price,
            "entry_value_usd": trade_usd,
            "volume_xbt": volume_xbt,
            "opened_at": ts,
            "stop_loss_price": stop_price,
        }

        # Write open to DB
        if _DB_ENABLED:
            write_trade(
                agent_id          = "btc",
                symbol            = "XBT/USD",
                ts                = ts,
                action            = action,
                side              = "LONG" if action == "OPEN_LONG" else "SHORT",
                confidence        = decision.get("confidence", 0),
                position_size_pct = size_pct,
                entry_price       = price,
                balance_at_trade  = balance,
                reasoning         = decision.get("reasoning", ""),
                triggers          = [],
                indicators        = {},
                strategy_update   = decision.get("strategy_update"),
            )

        return {"simulated": True, "trade_usd": trade_usd}


def main():
    if len(sys.argv) < 2:
        log.error("No payload passed to executor")
        return

    try:
        payload = json.loads(sys.argv[1])
        decision = payload["decision"]
        vault_path = payload.get("vault_path")
        decision["_vault_path"] = vault_path
    except Exception as e:
        log.error(f"Payload parse error: {e}")
        return

    log.info(f"── Executor: {decision['action']} ──")

    state = load_state()
    kraken = KrakenClient()

    # Get current market price
    try:
        ticker = kraken.get_ticker()
    except Exception as e:
        log.error(f"Ticker fetch failed: {e}")
        send_message(f"Executor could not fetch ticker: {e}")
        return

    # Use internal tracked balance — never fetch from Kraken demo
    balance = state.get("balance", PAPER_STARTING_BALANCE)
    if not balance:
        balance = PAPER_STARTING_BALANCE
        state["balance"] = balance
        state["peak_balance"] = balance
        log.info(f"Initialised paper trading balance: ${balance}")

    # Hard guardrail check
    approved, reason = guardrail_check(decision, state, balance, ticker)
    if not approved:
        log.warning(f"Guardrail blocked trade: {reason}")
        send_message(f"Trade blocked by guardrail\n{reason}")

        if "DRAWDOWN_LIMIT" in reason:
            state["trading_paused"] = True
            save_state(state)
            send_message(
                f"TRADING PAUSED - Drawdown limit hit\n"
                f"Balance: ${balance:.2f} | Peak: ${state['peak_balance']:.2f}\n"
                f"Resume by setting trading_paused=false in state.json"
            )
        return

    # Execute
    try:
        execute_trade(decision, state, kraken, ticker)
    except Exception as e:
        log.error(f"Order execution failed: {e}")
        send_message(f"Order failed: {e}")
        return

    save_state(state)

    # Update performance stats after every trade close
    if _PERF_ENABLED and decision.get("action") in ("CLOSE_POSITION", "SELL"):
        try:
            compute_performance()
            log.info("Performance stats updated")
        except Exception as _pe:
            log.warning(f"Performance update failed: {_pe}")

    log.info("Executor complete")


if __name__ == "__main__":
    main()
