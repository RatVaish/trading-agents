"""
tools/condenser.py
Nightly OHLCV compression — run via cron at midnight daily.
Handles all three agents in one pass.

Tier structure:
  Tier 1 (raw)    — every candle, kept 7 days
  Tier 2 (hourly) — 1h OHLCV aggregates, kept 30 days
  Tier 3 (daily)  — 1d OHLCV aggregates, kept forever

Cron entry (add to crontab -e):
  0 0 * * * /usr/bin/python3 /home/ratul/projects/trading-agents/tools/condenser.py >> /home/ratul/projects/trading-agents/tools/condenser.log 2>&1
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from db import DB_PATH, _connect

# ── Config ─────────────────────────────────────────────────────────────────────
TIER1_KEEP_DAYS = 7
TIER2_KEEP_DAYS = 30

AGENTS_SYMBOLS = {
    "btc":    ["XBT/USD"],
    "forex":  ["EUR/USD"],
    "stocks": ["SPY", "AAPL", "MSFT", "NVDA", "TSLA"],
}

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}")


def aggregate_to_hourly(conn, agent_id, symbol):
    """
    Aggregate tier-1 candles older than TIER1_KEEP_DAYS into tier-2 hourly bars.
    Groups by truncating timestamp to the hour.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TIER1_KEEP_DAYS)).isoformat()

    rows = conn.execute("""
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE agent_id=? AND symbol=? AND tier=1 AND ts < ?
        ORDER BY ts ASC
    """, (agent_id, symbol, cutoff)).fetchall()

    if not rows:
        return 0

    # Group by hour bucket
    buckets = {}
    for ts, o, h, l, c, v in rows:
        # Truncate to hour: "2026-03-30T14:35:00+00:00" -> "2026-03-30T14:00:00+00:00"
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            bucket = dt.replace(minute=0, second=0, microsecond=0).isoformat()
        except Exception:
            bucket = ts[:13] + ":00:00+00:00"

        if bucket not in buckets:
            buckets[bucket] = {"open": o, "high": h, "low": l, "close": c, "volume": v or 0}
        else:
            buckets[bucket]["high"]   = max(buckets[bucket]["high"], h or 0)
            buckets[bucket]["low"]    = min(buckets[bucket]["low"],  l or 0)
            buckets[bucket]["close"]  = c
            buckets[bucket]["volume"] = (buckets[bucket]["volume"] or 0) + (v or 0)

    inserted = 0
    for bucket_ts, bar in buckets.items():
        conn.execute("""
            INSERT OR IGNORE INTO ohlcv
                (agent_id, symbol, ts, open, high, low, close, volume, interval_mins, tier)
            VALUES (?,?,?,?,?,?,?,?,60,2)
        """, (agent_id, symbol, bucket_ts,
              bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]))
        inserted += conn.execute("SELECT changes()").fetchone()[0]

    # Delete the tier-1 rows we just aggregated
    conn.execute("""
        DELETE FROM ohlcv
        WHERE agent_id=? AND symbol=? AND tier=1 AND ts < ?
    """, (agent_id, symbol, cutoff))

    deleted = conn.execute("SELECT changes()").fetchone()[0]
    return inserted, deleted


def aggregate_to_daily(conn, agent_id, symbol):
    """
    Aggregate tier-2 hourly bars older than TIER2_KEEP_DAYS into tier-3 daily bars.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TIER2_KEEP_DAYS)).isoformat()

    rows = conn.execute("""
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE agent_id=? AND symbol=? AND tier=2 AND ts < ?
        ORDER BY ts ASC
    """, (agent_id, symbol, cutoff)).fetchall()

    if not rows:
        return 0, 0

    # Group by day bucket
    buckets = {}
    for ts, o, h, l, c, v in rows:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            bucket = dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        except Exception:
            bucket = ts[:10] + "T00:00:00+00:00"

        if bucket not in buckets:
            buckets[bucket] = {"open": o, "high": h, "low": l, "close": c, "volume": v or 0}
        else:
            buckets[bucket]["high"]   = max(buckets[bucket]["high"], h or 0)
            buckets[bucket]["low"]    = min(buckets[bucket]["low"],  l or 0)
            buckets[bucket]["close"]  = c
            buckets[bucket]["volume"] = (buckets[bucket]["volume"] or 0) + (v or 0)

    inserted = 0
    for bucket_ts, bar in buckets.items():
        conn.execute("""
            INSERT OR IGNORE INTO ohlcv
                (agent_id, symbol, ts, open, high, low, close, volume, interval_mins, tier)
            VALUES (?,?,?,?,?,?,?,?,1440,3)
        """, (agent_id, symbol, bucket_ts,
              bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]))
        inserted += conn.execute("SELECT changes()").fetchone()[0]

    conn.execute("""
        DELETE FROM ohlcv
        WHERE agent_id=? AND symbol=? AND tier=2 AND ts < ?
    """, (agent_id, symbol, cutoff))

    deleted = conn.execute("SELECT changes()").fetchone()[0]
    return inserted, deleted


def prune_equity(conn):
    """Keep only the last 90 days of equity snapshots."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    conn.execute("DELETE FROM equity WHERE ts < ?", (cutoff,))
    deleted = conn.execute("SELECT changes()").fetchone()[0]
    return deleted


def main():
    log("── Condenser starting ──")

    if not os.path.exists(DB_PATH):
        log(f"DB not found at {DB_PATH} — nothing to do")
        return

    conn = _connect()

    try:
        total_t1_inserted = total_t1_deleted = 0
        total_t2_inserted = total_t2_deleted = 0

        for agent_id, symbols in AGENTS_SYMBOLS.items():
            for symbol in symbols:
                # Tier 1 → Tier 2
                result = aggregate_to_hourly(conn, agent_id, symbol)
                if result:
                    ins, dl = result
                    total_t1_inserted += ins
                    total_t1_deleted  += dl
                    if ins or dl:
                        log(f"  {agent_id}/{symbol} T1→T2: +{ins} hourly bars, -{dl} raw candles")

                # Tier 2 → Tier 3
                ins2, dl2 = aggregate_to_daily(conn, agent_id, symbol)
                total_t2_inserted += ins2
                total_t2_deleted  += dl2
                if ins2 or dl2:
                    log(f"  {agent_id}/{symbol} T2→T3: +{ins2} daily bars, -{dl2} hourly bars")

        # Prune equity
        eq_pruned = prune_equity(conn)
        if eq_pruned:
            log(f"  Equity: pruned {eq_pruned} old snapshots (>90 days)")

        conn.commit()

        # Summary
        log(f"── Done: T1→T2 +{total_t1_inserted}/-{total_t1_deleted} | "
            f"T2→T3 +{total_t2_inserted}/-{total_t2_deleted} | "
            f"Equity pruned {eq_pruned} ──")

        # VACUUM to reclaim space
        conn.execute("VACUUM")
        log("VACUUM complete")

    except Exception as e:
        log(f"ERROR: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
