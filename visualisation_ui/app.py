"""
app.py
Flask dashboard server. Reads exclusively from data/trading.db.
Serves TOTAL and DETAIL views plus JSON API endpoints.

Updated for dynamic leverage + shorts:
  - unrealised_pnl() applies locked leverage and handles SHORT direction
  - /api/ohlcv includes side, leverage, pnl_usd on trade objects
"""
import json
import os
import sys
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))
from db import (
    get_agent_state, get_equity, get_ohlcv,
    get_trades, get_performance,
)

app = Flask(__name__)

def unrealised_pnl(state):
    """Compute total unrealised P&L from open positions.
    Applies leverage locked at open and handles SHORT direction.
    """
    pos        = state.get('position') or state.get('positions') or {}
    ind        = state.get('last_indicators') or {}
    total_upnl = 0.0

    def _calc(p, price):
        if not p or not price:
            return 0.0
        entry    = p.get('entry_price', 0)
        size     = p.get('entry_value_usd', 0)
        side     = (p.get('side') or 'LONG').upper()
        leverage = p.get('leverage', 1)
        try:
            leverage = max(1, int(leverage))
        except (TypeError, ValueError):
            leverage = 1
        if not entry or not size:
            return 0.0
        raw_move = (price - entry) / entry if side == 'LONG' else (entry - price) / entry
        return size * raw_move * leverage

    # Single position (btc / forex)
    if isinstance(pos, dict) and pos.get('side'):
        price = None
        if isinstance(ind, dict):
            price = ind.get('price')
            if price is None and ind:
                first = next(iter(ind.values()), {})
                price = first.get('price') if isinstance(first, dict) else None
        total_upnl += _calc(pos, price)

    # Multi positions (stocks)
    elif isinstance(pos, dict):
        for sym, p in pos.items():
            if not p or not isinstance(p, dict):
                continue
            price = None
            if isinstance(ind, dict):
                sym_ind = ind.get(sym, {})
                price   = sym_ind.get('price') if isinstance(sym_ind, dict) else None
            total_upnl += _calc(p, price)

    return round(total_upnl, 4)


def normalise_ts(ts):
    """Normalise timestamp to ISO format JS can parse."""
    if not ts:
        return ts
    import re
    s = str(ts)
    s = re.sub(r'(\.\d{3})\d+', r'\1', s)
    return s

AGENTS = ['btc', 'forex', 'stocks']

AGENT_META = {
    'btc':    {'label': 'BTC',    'pair': 'XBT/USD',   'symbols': ['XBT/USD']},
    'forex':  {'label': 'Forex',  'pair': 'EUR/USD',   'symbols': ['EUR/USD']},
    'stocks': {'label': 'Stocks', 'pair': 'Watchlist', 'symbols': ['SPY','AAPL','MSFT','NVDA','TSLA']},
}

def read_strategy(agent_id):
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', f'{agent_id}-agent', 'vault', 'strategy')
    for name in ['strategy.md', 'current.md']:
        p = os.path.join(base, name)
        if os.path.exists(p):
            try:
                return open(p).read()
            except Exception:
                pass
    return ''


@app.route('/api/overview')
def api_overview():
    data = {}
    for agent_id in AGENTS:
        state      = get_agent_state(agent_id) or {}
        perf       = get_performance(agent_id) or {}
        eq_window  = request.args.get('eq_window', 'all')
        eq_since   = None
        if eq_window != 'all':
            from datetime import timedelta
            days     = {'1w': 7, '1m': 30}.get(eq_window, 7)
            eq_since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        equity = get_equity(agent_id, limit=300, since_iso=eq_since)
        trades = get_trades(agent_id, limit=20)
        meta   = AGENT_META[agent_id]
        balance = state.get('balance', 150.0) or 150.0
        start   = state.get('starting_balance', 150.0) or 150.0
        data[agent_id] = {
            'meta':            meta,
            'balance':         round(balance, 2),
            'peak_balance':    round(state.get('peak_balance', balance) or balance, 2),
            'starting':        start,
            'pnl_pct':         round((balance - start) / start * 100, 2) if start else 0,
            'position':        state.get('position'),
            'positions':       state.get('positions'),
            'trading_paused':  bool(state.get('trading_paused')),
            'total_trades':    state.get('total_trades', 0),
            'cycle_count':     state.get('cycle_count', 0),
            'triggers':        state.get('triggers') or [],
            'last_checked':    state.get('last_checked'),
            'last_brain_call': state.get('last_brain_call'),
            'last_indicators': state.get('last_indicators') or {},
            'performance':     perf,
            'equity':          equity,
            'trades':          trades,
            'unrealised_pnl':  unrealised_pnl(state),
        }
    return jsonify({'agents': data, 'server_time': datetime.now(timezone.utc).isoformat()})


@app.route('/api/agent/<agent_id>')
def api_agent(agent_id):
    if agent_id not in AGENTS:
        return jsonify({'error': 'Unknown agent'}), 404
    state    = get_agent_state(agent_id) or {}
    perf     = get_performance(agent_id) or {}
    equity   = get_equity(agent_id, limit=1000)
    trades   = get_trades(agent_id, limit=100)
    strategy = read_strategy(agent_id)
    meta     = AGENT_META[agent_id]
    balance  = state.get('balance', 150.0) or 150.0
    start    = state.get('starting_balance', 150.0) or 150.0
    return jsonify({
        'agent_id':        agent_id,
        'meta':            meta,
        'balance':         round(balance, 2),
        'peak_balance':    round(state.get('peak_balance', balance) or balance, 2),
        'starting':        start,
        'pnl_pct':         round((balance - start) / start * 100, 2) if start else 0,
        'position':        state.get('position'),
        'positions':       state.get('positions'),
        'trading_paused':  bool(state.get('trading_paused')),
        'total_trades':    state.get('total_trades', 0),
        'cycle_count':     state.get('cycle_count', 0),
        'triggers':        state.get('triggers') or [],
        'last_checked':    state.get('last_checked'),
        'last_brain_call': state.get('last_brain_call'),
        'last_indicators': state.get('last_indicators') or {},
        'market_context':  state.get('market_context') or {},
        'performance':     perf,
        'equity':          equity,
        'trades':          trades,
        'strategy':        strategy,
        'unrealised_pnl':  unrealised_pnl(state),
        'server_time':     datetime.now(timezone.utc).isoformat(),
    })


@app.route('/api/ohlcv/<agent_id>/<path:symbol>')
def api_ohlcv(agent_id, symbol):
    if agent_id not in AGENTS:
        return jsonify({'error': 'Unknown agent'}), 404
    window     = request.args.get('window', '7d')
    window_map = {'1d': 1, '3d': 3, '7d': 7, '30d': 30, 'all': 9999}
    days       = window_map.get(window, 7)
    if days <= 7:
        candles = get_ohlcv(agent_id, symbol, tier=1, limit=2000)
    elif days <= 30:
        candles = sorted(
            get_ohlcv(agent_id, symbol, tier=2, limit=2000) +
            get_ohlcv(agent_id, symbol, tier=1, limit=2000),
            key=lambda x: x['ts']
        )
    else:
        candles = sorted(
            get_ohlcv(agent_id, symbol, tier=3, limit=2000) +
            get_ohlcv(agent_id, symbol, tier=2, limit=2000) +
            get_ohlcv(agent_id, symbol, tier=1, limit=2000),
            key=lambda x: x['ts']
        )

    all_trades = get_trades(agent_id, limit=500)
    trades_out = []
    for idx, t in enumerate(all_trades):
        sym = t.get('symbol') or symbol
        if sym != symbol and agent_id in ('btc', 'forex'):
            sym = symbol
        if sym != symbol:
            continue
        action = t.get('action', '')
        # Include all opening actions — OPEN_LONG, OPEN_SHORT, BUY, SELL_SHORT
        opening_actions = ('OPEN_LONG', 'OPEN_SHORT', 'BUY', 'SELL_SHORT')
        if action not in opening_actions:
            continue
        if not t.get('entry_price'):
            continue
        trade = {
            'trade_id':    idx,
            'action':      action,
            'side':        t.get('side', 'LONG'),       # LONG or SHORT
            'leverage':    t.get('leverage'),            # may be None for old trades
            'entry_ts':    t.get('opened_at') or t.get('ts'),
            'entry_price': t.get('entry_price'),
            'exit_ts':     t.get('closed_at'),
            'exit_price':  t.get('exit_price'),
            'outcome':     t.get('outcome'),
            'pnl_pct':     t.get('pnl_pct'),
            'pnl_usd':     t.get('pnl_usd'),
        }
        trades_out.append(trade)

    trades_out.sort(key=lambda x: x['entry_ts'] or '')

    interval_map = {'btc': 15, 'forex': 15, 'stocks': 5}

    for c in candles:
        c['ts'] = normalise_ts(c.get('ts'))

    return jsonify({
        'candles':       candles,
        'trades':        trades_out,
        'symbol':        symbol,
        'window':        window,
        'interval_mins': interval_map.get(agent_id, 15),
    })


@app.route('/api/trades')
def api_trades():
    agent_id = request.args.get('agent')
    limit    = int(request.args.get('limit', 200))
    trades   = get_trades(agent_id if agent_id in AGENTS else None, limit=limit)
    return jsonify({'trades': trades})


@app.route('/api/logs/<agent_id>')
def api_logs(agent_id):
    if agent_id not in AGENTS:
        return jsonify({'error': 'Unknown agent'}), 404
    log_file = request.args.get('file', 'monitor')
    allowed  = {'monitor', 'brain', 'executor', 'cron'}
    if log_file not in allowed:
        return jsonify({'error': 'Unknown log file'}), 400
    lines    = int(request.args.get('lines', 200))
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', f'{agent_id}-agent', 'logs', f'{log_file}.log'
    )
    if not os.path.exists(log_path):
        return jsonify({'lines': [], 'file': log_file, 'exists': False})
    try:
        with open(log_path, 'r', errors='replace') as f:
            all_lines = f.readlines()
        tail = [l.rstrip('\n') for l in all_lines[-lines:]]
        return jsonify({'lines': tail, 'file': log_file, 'exists': True,
                        'total_lines': len(all_lines)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
def total_view():
    return render_template('total.html')


@app.route('/detail')
@app.route('/detail/<agent_id>')
def detail_view(agent_id='btc'):
    if agent_id not in AGENTS:
        agent_id = 'btc'
    return render_template('detail.html', active_agent=agent_id, agents=AGENTS, meta=AGENT_META)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=False)
