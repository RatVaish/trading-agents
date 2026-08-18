"""
analyzer.py
Reads the shared trading.db and computes deep performance analytics
for each agent. Pure data -- no AI calls. Returns structured dicts
that the manager passes into Sonnet prompts.
"""
import os
import sys
import statistics
from datetime import datetime, timezone, timedelta

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"),
)
from db import get_trades, get_agent_state, get_performance, get_equity


def analyze_agent(agent_id: str) -> dict:
    """Full performance analysis for one agent."""
    state = get_agent_state(agent_id) or {}
    trades = get_trades(agent_id, limit=10000)
    equity = get_equity(agent_id, limit=5000)

    closed = [
        t for t in trades
        if t.get("closed_at") is not None
        and t.get("pnl_pct") is not None
    ]

    if not closed:
        return _empty_analysis(agent_id, state)

    pnls = [t["pnl_pct"] for t in closed]
    usds = [t.get("pnl_usd", 0) or 0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # -- Side breakdown ------------------------------------------------------
    by_side = {}
    for t in closed:
        side = t.get("side") or "LONG"
        by_side.setdefault(side, []).append(t["pnl_pct"])
    side_stats = {
        s: {
            "trades": len(v),
            "win_rate": round(sum(1 for p in v if p > 0) / len(v) * 100, 1),
            "avg_pnl": round(sum(v) / len(v), 3),
            "total_pnl": round(sum(v), 3),
        }
        for s, v in by_side.items()
    }

    # -- Symbol breakdown ----------------------------------------------------
    by_symbol = {}
    for t in closed:
        sym = t.get("symbol") or "N/A"
        by_symbol.setdefault(sym, []).append(t["pnl_pct"])
    symbol_stats = {
        s: {
            "trades": len(v),
            "win_rate": round(sum(1 for p in v if p > 0) / len(v) * 100, 1),
            "avg_pnl": round(sum(v) / len(v), 3),
        }
        for s, v in by_symbol.items()
    }

    # -- Streak analysis -----------------------------------------------------
    current_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    streak_type = None
    sorted_closed = sorted(closed, key=lambda t: t.get("closed_at", ""))
    for t in sorted_closed:
        won = t["pnl_pct"] > 0
        if streak_type == won:
            current_streak += 1
        else:
            current_streak = 1
            streak_type = won
        if won:
            max_win_streak = max(max_win_streak, current_streak)
        else:
            max_loss_streak = max(max_loss_streak, current_streak)

    # -- Recent trend (last 20 trades vs prior 20) ---------------------------
    recent_20 = pnls[-20:] if len(pnls) >= 20 else pnls
    prior_20 = pnls[-40:-20] if len(pnls) >= 40 else []
    recent_wr = (
        round(sum(1 for p in recent_20 if p > 0) / len(recent_20) * 100, 1)
        if recent_20 else 0
    )
    prior_wr = (
        round(sum(1 for p in prior_20 if p > 0) / len(prior_20) * 100, 1)
        if prior_20 else None
    )

    # -- Confidence vs outcome -----------------------------------------------
    conf_analysis = {}
    for t in closed:
        conf = t.get("confidence")
        if conf is None:
            continue
        bucket = "high" if conf >= 0.70 else "medium" if conf >= 0.60 else "low"
        conf_analysis.setdefault(bucket, []).append(t["pnl_pct"])
    confidence_stats = {
        b: {
            "trades": len(v),
            "win_rate": round(sum(1 for p in v if p > 0) / len(v) * 100, 1),
            "avg_pnl": round(sum(v) / len(v), 3),
        }
        for b, v in conf_analysis.items()
    }

    # -- Position sizing effectiveness ---------------------------------------
    size_analysis = {}
    for t in closed:
        size = t.get("position_size_pct")
        if size is None:
            continue
        bucket = (
            "small" if size <= 0.12
            else "medium" if size <= 0.22
            else "large"
        )
        size_analysis.setdefault(bucket, []).append(t["pnl_pct"])
    sizing_stats = {
        b: {
            "trades": len(v),
            "avg_pnl": round(sum(v) / len(v), 3),
        }
        for b, v in size_analysis.items()
    }

    # -- Equity drawdown -----------------------------------------------------
    peak_bal = state.get("peak_balance", 150.0)
    current_bal = state.get("balance", 150.0)
    starting_bal = state.get("starting_balance", 150.0)
    total_return = round((current_bal - starting_bal) / starting_bal * 100, 2)
    max_drawdown = round((peak_bal - current_bal) / peak_bal * 100, 2) if peak_bal else 0

    # -- Sharpe approximation ------------------------------------------------
    sharpe = 0.0
    if len(pnls) > 1:
        mean = statistics.mean(pnls)
        std = statistics.stdev(pnls)
        sharpe = round(mean / std, 3) if std > 0 else 0.0

    # -- Profit factor -------------------------------------------------------
    gross_wins = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p <= 0))
    profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else float("inf")

    # -- Last 5 trades for context -------------------------------------------
    last_5 = [
        {
            "symbol": t.get("symbol"),
            "side": t.get("side"),
            "pnl_pct": round(t["pnl_pct"], 3),
            "pnl_usd": round(t.get("pnl_usd", 0) or 0, 4),
            "confidence": t.get("confidence"),
            "size": t.get("position_size_pct"),
            "closed_at": (t.get("closed_at") or "")[:10],
        }
        for t in sorted_closed[-5:]
    ]

    return {
        "agent_id": agent_id,
        "balance": round(current_bal, 2),
        "peak_balance": round(peak_bal, 2),
        "starting_balance": starting_bal,
        "total_return_pct": total_return,
        "max_drawdown_pct": max_drawdown,
        "total_closed": len(closed),
        "open_positions": len([t for t in trades if t.get("closed_at") is None]),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "avg_win_pct": round(sum(wins) / len(wins), 3) if wins else 0,
        "avg_loss_pct": round(sum(losses) / len(losses), 3) if losses else 0,
        "total_pnl_usd": round(sum(usds), 4),
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "best_trade_pct": round(max(pnls), 3),
        "worst_trade_pct": round(min(pnls), 3),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "by_side": side_stats,
        "by_symbol": symbol_stats,
        "by_confidence": confidence_stats,
        "by_sizing": sizing_stats,
        "recent_wr": recent_wr,
        "prior_wr": prior_wr,
        "last_5": last_5,
        "cycle_count": state.get("cycle_count", 0),
        "last_checked": state.get("last_checked"),
    }


def _empty_analysis(agent_id, state):
    return {
        "agent_id": agent_id,
        "balance": state.get("balance", 150.0),
        "total_closed": 0,
        "win_rate": 0,
        "total_pnl_usd": 0,
        "note": "No closed trades to analyze",
    }


def analyze_all() -> dict:
    """Run analysis for all three agents."""
    return {
        agent_id: analyze_agent(agent_id)
        for agent_id in ["btc", "forex", "stocks"]
    }


if __name__ == "__main__":
    import json
    results = analyze_all()
    print(json.dumps(results, indent=2, default=str))
