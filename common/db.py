"""
common/db.py
Shared SQLite write module — imported by all three agents.
All writes are fire-and-forget with try/except so a DB failure
never crashes the agent's main loop.

DB location: trading-agents/data/trading.db
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

# Resolve paths relative to this file's location
_COMMON_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR   = os.path.dirname(_COMMON_DIR)
DB_PATH     = os.path.join(_ROOT_DIR, "data", "trading.db")
SCHEMA_PATH = os.path.join(_COMMON_DIR, "schema.sql")

# Write DB errors to a dedicated log so they're visible even from subprocesses
_LOG_PATH = os.path.join(_ROOT_DIR, "data", "db_errors.log")
os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)

logging.basicConfig(level=logging.WARNING)
_log = logging.getLogger("db")
_fh  = logging.FileHandler(_LOG_PATH)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_log.addHandler(_fh)


def _connect():
    """Open a connection with WAL mode for safe concurrent reads/writes."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema():
    """Apply schema.sql if tables don't exist yet. Safe to call every import."""
    with open(SCHEMA_PATH) as f:
        sql = f.read()
    conn = _connect()
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


try:
    _ensure_schema()
except Exception as e:
    _log.error(f"Schema init failed: {e}")


# ── Public write functions ─────────────────────────────────────────────────────

def write_agent_state(agent_id: str, state: dict):
    """
    Upsert the current agent state snapshot.
    Stores positions dict (stocks) or single position (btc/forex) in the
    position column as JSON. Called at the end of every market_monitor cycle.
    """
    try:
        # Support both single position (btc/forex) and multi-position (stocks)
        pos_data = state.get("positions") or state.get("position")
        conn = _connect()
        conn.execute("""
            INSERT INTO agent_state (
                agent_id, balance, peak_balance, starting_balance,
                position, trading_paused, total_trades, cycle_count,
                last_indicators, market_context, triggers,
                last_checked, last_brain_call, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                balance         = excluded.balance,
                peak_balance    = excluded.peak_balance,
                position        = excluded.position,
                trading_paused  = excluded.trading_paused,
                total_trades    = excluded.total_trades,
                cycle_count     = excluded.cycle_count,
                last_indicators = excluded.last_indicators,
                market_context  = excluded.market_context,
                triggers        = excluded.triggers,
                last_checked    = excluded.last_checked,
                last_brain_call = excluded.last_brain_call,
                updated_at      = excluded.updated_at
        """, (
            agent_id,
            state.get("balance"),
            state.get("peak_balance"),
            state.get("starting_balance", 150.0),
            json.dumps(pos_data),
            1 if state.get("trading_paused") else 0,
            state.get("total_trades", 0),
            state.get("cycle_count", 0),
            json.dumps(state.get("last_indicators")),
            json.dumps(state.get("market_context")),
            json.dumps(state.get("triggers")),
            state.get("last_checked"),
            state.get("last_brain_call"),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
    except Exception as e:
        _log.error(f"write_agent_state failed ({agent_id}): {e}")
    finally:
        conn.close()


def write_ohlcv(agent_id: str, symbol: str, candle: dict, interval_mins: int):
    """
    Insert a single OHLCV candle (tier 1 = raw).
    Uses INSERT OR IGNORE so duplicate candle timestamps are silently skipped.
    """
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR IGNORE INTO ohlcv
                (agent_id, symbol, ts, open, high, low, close, volume, interval_mins, tier)
            VALUES (?,?,?,?,?,?,?,?,?,1)
        """, (
            agent_id,
            symbol,
            candle["ts"],
            candle.get("open"),
            candle.get("high"),
            candle.get("low"),
            candle.get("close"),
            candle.get("volume"),
            interval_mins,
        ))
        conn.commit()
    except Exception as e:
        _log.error(f"write_ohlcv failed ({agent_id}/{symbol}): {e}")
    finally:
        conn.close()


def write_equity(agent_id: str, balance: float):
    """
    Append a balance snapshot. Truncated to minute precision.
    Uses INSERT OR REPLACE so balance always reflects latest value.
    """
    try:
        now = datetime.now(timezone.utc)
        ts  = now.strftime('%Y-%m-%dT%H:%M:00+00:00')
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO equity (agent_id, ts, balance)
            VALUES (?,?,?)
        """, (agent_id, ts, round(balance, 4)))
        conn.commit()
    except Exception as e:
        _log.error(f"write_equity failed ({agent_id}): {e}")
    finally:
        conn.close()


def write_trade(
    agent_id: str,
    symbol: str,
    ts: str,
    action: str,
    side: str,
    confidence: float,
    position_size_pct: float,
    entry_price: float,
    balance_at_trade: float,
    reasoning: str,
    triggers: list,
    indicators: dict,
    strategy_update: str = None,
):
    """
    Insert a new trade record when a position is opened.
    opened_at is set to ts so close_trade can match on it later.
    """
    try:
        conn = _connect()
        cur = conn.execute("""
            INSERT INTO trades (
                agent_id, symbol, ts, action, side, confidence,
                position_size_pct, entry_price, balance_at_trade,
                reasoning, triggers, indicators, strategy_update, opened_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            agent_id,
            symbol,
            ts,
            action,
            side,
            confidence,
            position_size_pct,
            entry_price,
            balance_at_trade,
            reasoning,
            json.dumps(triggers or []),
            json.dumps(indicators or {}),
            strategy_update,
            ts,  # opened_at = ts so close_trade WHERE clause matches
        ))
        rowid = cur.lastrowid
        conn.commit()
        _log.info(f"write_trade OK ({agent_id}/{symbol} id={rowid})")
        return rowid
    except Exception as e:
        _log.error(f"write_trade FAILED ({agent_id}/{symbol}): {e}")
        return None
    finally:
        conn.close()


def close_trade(
    agent_id: str,
    symbol: str,
    opened_at: str,
    outcome: str,
    pnl_pct: float,
    pnl_usd: float,
    exit_price: float,
    fees_usd: float = 0.0,
):
    """
    Update an existing open trade with close data.
    Matches on agent_id + symbol + opened_at.
    Falls back to matching on agent_id + symbol + closed_at IS NULL
    if exact opened_at match returns 0 rows (handles timestamp drift).
    """
    try:
        conn = _connect()
        # Try exact match first
        cur = conn.execute("""
            UPDATE trades
            SET outcome    = ?,
                pnl_pct    = ?,
                pnl_usd    = ?,
                exit_price = ?,
                fees_usd   = ?,
                closed_at  = ?
            WHERE agent_id = ? AND symbol = ? AND opened_at = ? AND closed_at IS NULL
        """, (
            outcome, pnl_pct, pnl_usd, exit_price, fees_usd,
            datetime.now(timezone.utc).isoformat(),
            agent_id, symbol, opened_at,
        ))
        conn.commit()

        if cur.rowcount == 0:
            # Fallback: match the most recent open trade for this symbol
            _log.warning(
                f"close_trade exact match found 0 rows for {agent_id}/{symbol} "
                f"opened_at={opened_at} — trying fallback (most recent open trade)"
            )
            cur2 = conn.execute("""
                UPDATE trades
                SET outcome    = ?,
                    pnl_pct    = ?,
                    pnl_usd    = ?,
                    exit_price = ?,
                    fees_usd   = ?,
                    closed_at  = ?
                WHERE agent_id = ? AND symbol = ? AND closed_at IS NULL
                AND id = (
                    SELECT id FROM trades
                    WHERE agent_id = ? AND symbol = ? AND closed_at IS NULL
                    ORDER BY ts DESC LIMIT 1
                )
            """, (
                outcome, pnl_pct, pnl_usd, exit_price, fees_usd,
                datetime.now(timezone.utc).isoformat(),
                agent_id, symbol,
                agent_id, symbol,
            ))
            conn.commit()
            if cur2.rowcount > 0:
                _log.warning(f"close_trade fallback succeeded for {agent_id}/{symbol}")
            else:
                _log.error(f"close_trade fallback also found 0 rows for {agent_id}/{symbol}")

    except Exception as e:
        _log.error(f"close_trade FAILED ({agent_id}/{symbol}): {e}")
    finally:
        conn.close()


def write_performance(agent_id: str, perf: dict):
    """Upsert computed performance stats."""
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO performance (
                agent_id, total_closed_trades, wins, losses, win_rate,
                avg_win_pct, avg_loss_pct, profit_factor, total_pnl_usd,
                sharpe_approx, per_symbol, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                total_closed_trades = excluded.total_closed_trades,
                wins                = excluded.wins,
                losses              = excluded.losses,
                win_rate            = excluded.win_rate,
                avg_win_pct         = excluded.avg_win_pct,
                avg_loss_pct        = excluded.avg_loss_pct,
                profit_factor       = excluded.profit_factor,
                total_pnl_usd       = excluded.total_pnl_usd,
                sharpe_approx       = excluded.sharpe_approx,
                per_symbol          = excluded.per_symbol,
                updated_at          = excluded.updated_at
        """, (
            agent_id,
            perf.get("total_closed_trades", 0),
            perf.get("wins", 0),
            perf.get("losses", 0),
            perf.get("win_rate", 0),
            perf.get("avg_win_pct", 0),
            perf.get("avg_loss_pct", 0),
            perf.get("profit_factor", 0),
            perf.get("total_pnl_usd", 0),
            perf.get("sharpe_approx", 0),
            json.dumps(perf.get("per_symbol")),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
    except Exception as e:
        _log.error(f"write_performance failed ({agent_id}): {e}")
    finally:
        conn.close()


# ── Read helpers ───────────────────────────────────────────────────────────────

def get_ohlcv(agent_id: str, symbol: str, tier: int = 1, limit: int = 500):
    """Fetch OHLCV rows ordered oldest-first."""
    try:
        conn = _connect()
        rows = conn.execute("""
            SELECT ts, open, high, low, close, volume
            FROM ohlcv
            WHERE agent_id=? AND symbol=? AND tier=?
            ORDER BY ts ASC
            LIMIT ?
        """, (agent_id, symbol, tier, limit)).fetchall()
        return [{"ts":r[0],"open":r[1],"high":r[2],"low":r[3],"close":r[4],"volume":r[5]} for r in rows]
    except Exception as e:
        _log.error(f"get_ohlcv failed: {e}")
        return []
    finally:
        conn.close()


def get_equity(agent_id: str, limit: int = 1000, since_iso: str = None):
    """Fetch equity snapshots in chronological order, optionally windowed."""
    try:
        conn = _connect()
        if since_iso:
            anchor = conn.execute("""
                SELECT ts, balance FROM equity
                WHERE agent_id=? AND ts < ?
                ORDER BY ts DESC LIMIT 1
            """, (agent_id, since_iso)).fetchall()
            rows = conn.execute("""
                SELECT ts, balance FROM equity
                WHERE agent_id=? AND ts >= ?
                ORDER BY ts ASC
            """, (agent_id, since_iso)).fetchall()
            rows = list(anchor) + list(rows)
        else:
            rows = conn.execute("""
                SELECT ts, balance FROM equity
                WHERE agent_id=?
                ORDER BY ts ASC
            """, (agent_id,)).fetchall()

        if not rows:
            return []
        if len(rows) > limit:
            step = len(rows) / limit
            rows = [rows[int(i * step)] for i in range(limit)]
        return [{"ts": r[0], "balance": r[1]} for r in rows]
    except Exception as e:
        _log.error(f"get_equity failed: {e}")
        return []
    finally:
        conn.close()


def get_trades(agent_id: str = None, limit: int = 200):
    """Fetch trades, optionally filtered by agent, newest first."""
    try:
        conn = _connect()
        if agent_id:
            rows = conn.execute("""
                SELECT agent_id, symbol, ts, action, side, outcome,
                       confidence, position_size_pct, entry_price, exit_price,
                       pnl_pct, pnl_usd, fees_usd, balance_at_trade,
                       reasoning, triggers, indicators, strategy_update,
                       opened_at, closed_at
                FROM trades WHERE agent_id=?
                ORDER BY ts DESC LIMIT ?
            """, (agent_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT agent_id, symbol, ts, action, side, outcome,
                       confidence, position_size_pct, entry_price, exit_price,
                       pnl_pct, pnl_usd, fees_usd, balance_at_trade,
                       reasoning, triggers, indicators, strategy_update,
                       opened_at, closed_at
                FROM trades ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
        keys = ["agent_id","symbol","ts","action","side","outcome",
                "confidence","position_size_pct","entry_price","exit_price",
                "pnl_pct","pnl_usd","fees_usd","balance_at_trade",
                "reasoning","triggers","indicators","strategy_update",
                "opened_at","closed_at"]
        return [dict(zip(keys, r)) for r in rows]
    except Exception as e:
        _log.error(f"get_trades failed: {e}")
        return []
    finally:
        conn.close()


def get_agent_state(agent_id: str):
    """
    Fetch single agent state row.
    Returns position data under BOTH 'position' and 'positions' keys
    so the dashboard works for both single-position (btc/forex) and
    multi-position (stocks) agents.
    """
    try:
        conn = _connect()
        row = conn.execute("""
            SELECT agent_id, balance, peak_balance, starting_balance,
                   position, trading_paused, total_trades, cycle_count,
                   last_indicators, market_context, triggers,
                   last_checked, last_brain_call, updated_at
            FROM agent_state WHERE agent_id=?
        """, (agent_id,)).fetchone()
        if not row:
            return None
        keys = ["agent_id","balance","peak_balance","starting_balance",
                "position","trading_paused","total_trades","cycle_count",
                "last_indicators","market_context","triggers",
                "last_checked","last_brain_call","updated_at"]
        d = dict(zip(keys, row))
        # Parse JSON blobs
        for field in ["last_indicators","market_context","triggers"]:
            try:
                d[field] = json.loads(d[field]) if d[field] else None
            except Exception:
                pass
        # Parse position blob — expose as both 'position' and 'positions'
        # so btc/forex (single) and stocks (multi) both work in the dashboard
        try:
            pos = json.loads(d["position"]) if d["position"] else None
        except Exception:
            pos = None
        d["position"]  = pos
        d["positions"] = pos  # dashboard reads whichever key is populated
        return d
    except Exception as e:
        _log.error(f"get_agent_state failed: {e}")
        return None
    finally:
        conn.close()


def get_performance(agent_id: str):
    """Fetch performance row for one agent."""
    try:
        conn = _connect()
        row = conn.execute("""
            SELECT agent_id, total_closed_trades, wins, losses, win_rate,
                   avg_win_pct, avg_loss_pct, profit_factor, total_pnl_usd,
                   sharpe_approx, per_symbol, updated_at
            FROM performance WHERE agent_id=?
        """, (agent_id,)).fetchone()
        if not row:
            return None
        keys = ["agent_id","total_closed_trades","wins","losses","win_rate",
                "avg_win_pct","avg_loss_pct","profit_factor","total_pnl_usd",
                "sharpe_approx","per_symbol","updated_at"]
        d = dict(zip(keys, row))
        try:
            d["per_symbol"] = json.loads(d["per_symbol"]) if d["per_symbol"] else {}
        except Exception:
            d["per_symbol"] = {}
        return d
    except Exception as e:
        _log.error(f"get_performance failed: {e}")
        return None
    finally:
        conn.close()
