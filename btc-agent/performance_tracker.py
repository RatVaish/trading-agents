"""
performance_tracker.py
Computes performance statistics from the DB (source of truth).
Called after every trade close and by daily_review.py.
No AI calls — pure arithmetic.
"""
import os, sys, statistics
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
try:
    from db import get_trades, write_performance as db_write_performance
    _DB_ENABLED = True
except Exception as _db_err:
    _DB_ENABLED = False

from config.config import VAULT_DIR


def compute_performance():
    if not _DB_ENABLED:
        return _empty_performance()

    try:
        all_trades = get_trades("btc", limit=10000)
    except Exception:
        return _empty_performance()

    # Only closed trades (have exit_price and outcome)
    closed = [
        t for t in all_trades
        if t.get("outcome") in ("WIN", "LOSS")
        and t.get("pnl_pct") is not None
        and t.get("closed_at") is not None
    ]

    if not closed:
        return _empty_performance()

    pnl_pcts  = [t["pnl_pct"] for t in closed]
    wins      = [p for p in pnl_pcts if p > 0]
    losses    = [p for p in pnl_pcts if p <= 0]
    win_rate  = len(wins) / len(pnl_pcts) if pnl_pcts else 0
    avg_win   = sum(wins)   / len(wins)   if wins   else 0
    avg_loss  = sum(losses) / len(losses) if losses else 0

    gross_profit  = sum(wins)
    gross_loss    = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Use pnl_usd from DB directly where available
    total_pnl_usd = sum(
        t.get("pnl_usd") or 0 for t in closed
    )

    sharpe = 0.0
    if len(pnl_pcts) > 1:
        mean   = statistics.mean(pnl_pcts)
        std    = statistics.stdev(pnl_pcts)
        sharpe = round(mean / std, 3) if std > 0 else 0.0


    perf = {
        "total_closed_trades": len(closed),
        "wins":                len(wins),
        "losses":              len(losses),
        "win_rate":            round(win_rate, 3),
        "avg_win_pct":         round(avg_win, 3),
        "avg_loss_pct":        round(avg_loss, 3),
        "profit_factor":       round(profit_factor, 3),
        "total_pnl_usd":       round(total_pnl_usd, 4),
        "sharpe_approx":       sharpe,
        "last_updated":        datetime.now(timezone.utc).isoformat(),
    }

    # Also write to vault reports for backwards compatibility
    try:
        import json
        perf_path = os.path.join(VAULT_DIR, "reports", "performance.json")
        os.makedirs(os.path.dirname(perf_path), exist_ok=True)
        with open(perf_path, "w") as fp:
            json.dump(perf, fp, indent=2)
    except Exception:
        pass

    if _DB_ENABLED:
        try:
            db_write_performance("btc", perf)
        except Exception:
            pass

    return perf


def _empty_performance():
    return {
        "total_closed_trades": 0,
        "wins": 0, "losses": 0, "win_rate": 0.0,
        "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "profit_factor": 0.0, "total_pnl_usd": 0.0,
        "sharpe_approx": 0.0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(compute_performance())
