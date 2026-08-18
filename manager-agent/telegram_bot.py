"""
telegram_bot.py
Sends management reports via Telegram.
Uses plain text to avoid Markdown parse errors with $, +, - characters.
"""
import requests
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

MAX_MSG_LEN = 4000


def send_message(text: str) -> bool:
    """Send a Telegram message. Splits if too long."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured")
        return False

    # Split long messages
    chunks = []
    while len(text) > MAX_MSG_LEN:
        split_at = text.rfind("\n", 0, MAX_MSG_LEN)
        if split_at == -1:
            split_at = MAX_MSG_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    chunks.append(text)

    success = True
    for chunk in chunks:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Telegram send failed: {e}")
            success = False
    return success


def send_management_report(analyses: dict, strategy_results: dict, recommendations: str,
                           config_changes: list = None, config_results: list = None):
    """Format and send the full management report."""
    lines = ["MANAGER AGENT REPORT", "=" * 30, ""]

    for agent_id in ["btc", "forex", "stocks"]:
        a = analyses.get(agent_id, {})
        sr = strategy_results.get(agent_id, {})

        lines.append(f"--- {agent_id.upper()} ---")
        lines.append(
            f"Balance: ${a.get('balance', 0):.2f} "
            f"(return: {a.get('total_return_pct', 0):+.1f}%)"
        )
        lines.append(
            f"Trades: {a.get('total_closed', 0)} | "
            f"WR: {a.get('win_rate', 0):.1f}% | "
            f"PF: {a.get('profit_factor', 0):.2f}"
        )
        lines.append(f"P&L: ${a.get('total_pnl_usd', 0):+.4f}")

        by_side = a.get("by_side", {})
        for side, stats in by_side.items():
            lines.append(
                f"  {side}: {stats['trades']} trades, "
                f"WR {stats['win_rate']}%, avg {stats['avg_pnl']:+.3f}%"
            )

        if sr.get("success"):
            lines.append(
                f"Strategy: {sr['old_lines']} -> {sr['new_lines']} lines"
            )
        elif sr.get("error"):
            lines.append(f"Strategy rewrite failed: {sr['error']}")

        lines.append("")

    if config_results:
        lines.append("CONFIG CHANGES")
        lines.append("-" * 20)
        for cr in (config_results or []):
            if cr.get("success"):
                lines.append(
                    f"{cr['agent_id']}/{cr['param']}: "
                    f"{cr['old_value']} -> {cr['new_value']}"
                )
        lines.append("")

    if recommendations:
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 20)
        lines.append(recommendations)

    return send_message("\n".join(lines))

