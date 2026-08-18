"""
daily_review.py
Called once per day by cron (recommended: 7am).
Sonnet reflects on the past day's trades, updates strategy, sends report.
This is the agent's main learning loop — it reads its own history
and decides how to trade differently tomorrow.
"""
import json, logging, os, sys
from datetime import datetime, timezone
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.config import (
    ANTHROPIC_API_KEY, SONNET_MODEL,
    STATE_FILE, VAULT_DIR, LOG_DIR, DISPLAY_PAIR,
)
from performance_tracker import compute_performance
from telegram_bot import send_daily_report

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, "daily.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_recent_trades(n=20):
    trades_dir = os.path.join(VAULT_DIR, "trades")
    if not os.path.exists(trades_dir):
        return []
    files  = sorted(f for f in os.listdir(trades_dir) if f.endswith(".json"))[-n:]
    trades = []
    for fname in files:
        try:
            with open(os.path.join(trades_dir, fname)) as fp:
                trades.append(json.load(fp))
        except Exception:
            pass
    return trades


def load_strategy_note():
    path = os.path.join(VAULT_DIR, "strategy", "current.md")
    if os.path.exists(path):
        with open(path) as fp:
            return fp.read()[-1200:]   # last 1200 chars = most recent notes
    return "No strategy note yet."


def run_daily_review(state, trades, performance, strategy_note):
    system = f"""You are an autonomous {DISPLAY_PAIR} trading agent doing your daily review.
Be honest. Analyse what worked, what didn't, and give yourself a concrete update.
Your goal is to improve your edge over time through disciplined self-reflection.

Respond ONLY with valid JSON — no preamble, no markdown fences:
{{
  "summary": "2-3 sentence summary of today's performance and conditions",
  "what_worked": "one concrete observation about signals or timing that worked",
  "what_didnt": "one honest assessment of what went wrong or missed",
  "strategy_update": "one specific, actionable change for tomorrow's trading",
  "confidence_in_strategy": 0.0-1.0
}}"""

    user = f"""Today's date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

Performance stats:
{json.dumps(performance, indent=2)}

Balance: ${state.get('balance', 0):.2f} | Peak: ${state.get('peak_balance', 0):.2f}
Total trades: {state.get('total_trades', 0)}

Recent trades (last 20):
{json.dumps(trades, indent=2)}

Current strategy note:
{strategy_note}"""

    try:
        resp = ai.messages.create(
            model=SONNET_MODEL,
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        review = json.loads(resp.content[0].text.strip())
        log.info(f"Daily review: {review['summary'][:100]}")
        return review
    except Exception as e:
        log.error(f"Daily review failed: {e}")
        return None


def save_review(review):
    if not review:
        return
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = os.path.join(VAULT_DIR, "reports", f"{ts}_daily.json")
    with open(path, "w") as fp:
        json.dump(review, fp, indent=2)

    # Append strategy update to strategy note
    if review.get("strategy_update"):
        strat_path = os.path.join(VAULT_DIR, "strategy", "current.md")
        dt_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(strat_path, "a") as fp:
            fp.write(f"\n## Daily review {dt_str}\n{review['strategy_update']}\n")
        log.info("Strategy note updated from daily review")


def main():
    log.info("── Daily review ──")

    if not os.path.exists(STATE_FILE):
        log.error("No state.json — skipping daily review")
        return

    with open(STATE_FILE) as fp:
        state = json.load(fp)

    trades      = load_recent_trades(20)
    strategy    = load_strategy_note()
    performance = compute_performance()

    review = run_daily_review(state, trades, performance, strategy)
    save_review(review)

    # Send Telegram report regardless of whether review succeeded
    send_daily_report(state, performance, review)
    log.info("Daily review complete")


if __name__ == "__main__":
    main()
