/* api.js — shared fetch helpers, polling, formatters */

const AGENT_COLOR  = { btc: '#f7931a', forex: '#4d9fff', stocks: '#c084fc' };
const AGENT_KEYS   = ['btc', 'forex', 'stocks'];
// AGENT_SYMBOLS is injected by the Flask template in detail.html — do not redeclare here

/* ── Timezone helper ── */
function tzOffsetMins() { return (window.TZ_OFFSET || 0) * 60; }
function toDisplayTime(d) {
  if (!d || isNaN(d)) return null;
  return new Date(d.getTime() + tzOffsetMins() * 60000);
}
function tzLabel() {
  const h = window.TZ_OFFSET || 0;
  return h === 0 ? 'GMT' : `UTC${h >= 0 ? '+' : ''}${h}`;
}

/* ── Formatters ── */
function f(n, dp=2)  { return n != null ? Number(n).toFixed(dp) : '--'; }
function fm(n)       { return n != null ? '$' + Number(n).toFixed(2) : '--'; }
function fp(n)       { if (n == null) return '--'; return (n >= 0 ? '+' : '') + Number(n).toFixed(2) + '%'; }

function normaliseTs(raw) {
  if (!raw) return null;
  let s = String(raw);
  s = s.replace(/(\.\d{3})\d+/, '$1');
  s = s.replace(' ', 'T');
  if (!s.endsWith('Z') && !s.includes('+') && s.includes('T')) s += 'Z';
  return s;
}

function parseTs(raw) {
  if (!raw) return null;
  try {
    const d = new Date(normaliseTs(raw));
    return isNaN(d) ? null : d;
  } catch { return null; }
}

function fmtTs(raw) {
  if (!raw) return '--';
  const m = String(raw).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/);
  if (m) {
    const d = toDisplayTime(new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`));
    if (!d) return raw.substring(0, 16);
    return `${String(d.getUTCDate()).padStart(2,'0')}/${String(d.getUTCMonth()+1).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} ${tzLabel()}`;
  }
  const d = parseTs(raw);
  if (!d) return String(raw).substring(0, 16);
  const dd = toDisplayTime(d);
  return `${String(dd.getUTCDate()).padStart(2,'0')}/${String(dd.getUTCMonth()+1).padStart(2,'0')} ` +
         `${String(dd.getUTCHours()).padStart(2,'0')}:${String(dd.getUTCMinutes()).padStart(2,'0')} ${tzLabel()}`;
}

function fmtTimeOnly(iso) {
  if (!iso) return '--';
  const d = parseTs(iso);
  if (!d) return '--';
  const dd = toDisplayTime(d);
  return `${String(dd.getUTCHours()).padStart(2,'0')}:${String(dd.getUTCMinutes()).padStart(2,'0')}:${String(dd.getUTCSeconds()).padStart(2,'0')} ${tzLabel()}`;
}

function fmtNewsDate(raw) {
  if (!raw) return '';
  const d = parseTs(raw);
  if (!d) return String(raw).substring(0, 16);
  const dd = toDisplayTime(d);
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${String(dd.getUTCDate()).padStart(2,'0')} ${months[dd.getUTCMonth()]} ` +
         `${String(dd.getUTCHours()).padStart(2,'0')}:${String(dd.getUTCMinutes()).padStart(2,'0')}`;
}

/* ── Candle snapping ── */
function snapToCandle(tradeTsSec, candles) {
  if (!candles || !candles.length) return tradeTsSec;
  let best = candles[0].time;
  for (const c of candles) {
    if (c.time <= tradeTsSec) best = c.time;
    else break;
  }
  return best;
}

/* ── Leverage badge helper ── */
function levBadge(leverage) {
  if (!leverage || leverage <= 1) return '';
  return `<span class="bdg bdg-lev">${leverage}x</span>`;
}

/* ── Position label ── */
function posLabel(a) {
  // Prefer explicit positions dict (stocks); fall back to single position (forex/btc)
  const pos = a.positions || a.position;
  if (!pos) return '<span class="bdg bdg-m">FLAT</span>';

  // Multi-position (stocks) — positions is a dict of {symbol: posObj|null}
  if (typeof pos === 'object' && !pos.side) {
    const open = Object.entries(pos).filter(([, v]) => v && v.side);
    if (!open.length) return '<span class="bdg bdg-m">FLAT</span>';
    return open.map(([sym, p]) => {
      const side = (p.side || 'LONG').toUpperCase();
      const lev  = p.leverage;
      return `<span class="bdg ${side === 'LONG' ? 'bdg-g' : 'bdg-r'}">${sym} ${side}${lev && lev > 1 ? ' ' + lev + 'x' : ''}</span>`;
    }).join(' ');
  }

  // Single position (forex / btc)
  const side = (pos.side || 'LONG').toUpperCase();
  const lev  = pos.leverage;
  return `<span class="bdg ${side === 'LONG' ? 'bdg-g' : 'bdg-r'}">${side}${lev && lev > 1 ? ' ' + lev + 'x' : ''}</span>`;
}

/* ── Trigger badges ── */
function trgHtml(a) {
  const t = a.triggers;
  if (!t) return '';
  const list = Array.isArray(t) ? t : Object.values(t).flat();
  return list.map(x => `<span class="trg">${x}</span>`).join('');
}

/* ── Fetch helpers ── */
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* ── Polling ── */
class Poller {
  constructor(intervalSec = 30) {
    this._interval  = intervalSec;
    this._countdown = intervalSec;
    this._callbacks = [];
    this._timer     = null;
    this._lblEl     = null;
  }
  setLabel(el) { this._lblEl = el; return this; }
  onTick(fn)   { this._callbacks.push(fn); return this; }
  start() {
    this._run();
    this._timer = setInterval(() => {
      this._countdown--;
      if (this._lblEl) this._lblEl.textContent = this._countdown <= 0 ? '...' : `${this._countdown}s`;
      if (this._countdown <= 0) { this._countdown = this._interval; this._run(); }
    }, 1000);
    return this;
  }
  _run() { this._callbacks.forEach(fn => fn()); }
  reset() { this._countdown = this._interval; }
}

/* ── Chart registry ── */
const Charts = {
  _c: {},
  kill(id)       { if (this._c[id]) { this._c[id].destroy(); delete this._c[id]; } },
  set(id, chart) { this._c[id] = chart; },
  get(id)        { return this._c[id]; },
};
