"""
telegram_bot.py
Lightweight Telegram notification helper.
Uses plain text to avoid Markdown parse errors with $, %, +, - characters.
"""
import requests, logging, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — message suppressed")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def send_daily_report(state, performance, daily_review):
    """Format and send the end-of-day P&L report."""
    bal   = state.get("balance", 0)
    peak  = state.get("peak_balance", 0)
    total = state.get("total_trades", 0)

    drawdown = ((peak - bal) / peak * 100) if peak else 0

    # Open positions summary
    positions = state.get("positions", {})
    open_pos  = {sym: p for sym, p in positions.items() if p}
    if open_pos:
        pos_lines = []
        for sym, p in open_pos.items():
            pos_lines.append(
                f"  {sym}: entry=${p['entry_price']:.2f} "
                f"size=${p['entry_value_usd']:.2f} "
                f"stop=${p['stop_loss_price']:.2f}"
            )
        pos_text = "Open positions:\n" + "\n".join(pos_lines)
    else:
        pos_text = "No open positions"

    wins     = performance.get("wins", 0)
    losses   = performance.get("losses", 0)
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    review_text = ""
    if daily_review:
        review_text = (
            f"\n\nAgent daily note:\n"
            f"{daily_review.get('summary', '')}\n"
            f"Worked: {daily_review.get('what_worked', 'N/A')}\n"
            f"Didnt:  {daily_review.get('what_didnt', 'N/A')}\n"
            f"Tomorrow: {daily_review.get('strategy_update', 'N/A')}"
        )

    msg = (
        f"Stocks Agent - Daily Report\n\n"
        f"Balance: ${bal:,.2f}\n"
        f"Peak: ${peak:,.2f} | Drawdown: {drawdown:.1f}%\n"
        f"Total trades: {total} | Win rate: {win_rate:.0f}%\n"
        f"All-time P&L: ${performance.get('total_pnl_usd', 0):+,.2f}\n\n"
        f"{pos_text}"
        f"{review_text}"
    )
    return send_message(msg)
