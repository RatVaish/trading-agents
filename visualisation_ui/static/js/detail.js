/* detail.js — DETAIL view: per-agent deep dive
   Updated for dynamic leverage + long/short:
   - Chart markers: SHORT entry = red down arrow, LONG entry = green up arrow
   - SHORT exit uses up arrow (covering); LONG exit uses down arrow (selling)
   - Price lines: SHORT entry dashed red, LONG entry dashed green
   - Trades table: shows Side, Leverage, Notional columns
   - Open position card: shows leverage + notional exposure
   - Monitor panel: shows open position details with leverage
*/

let _agent        = ACTIVE_AGENT;
let _data         = null;
let _subTab       = 'chart';
let _window       = '7d';
let _symbol       = null;
let _lwChart      = null;
let _candleSeries = null;
let _tradeFilter  = 'all';
let _showTrades   = true;
let _logFile      = 'monitor';
let _activeTrade  = -1;
let _tradesInView = [];
let _priceLines   = [];
let _deduped      = [];
const root = document.getElementById('detail-root');

// ── Agent switching ───────────────────────────────────────────────────────────
function switchAgent(key) {
  _agent = key;
  _symbol = null;
  _data   = null;
  history.replaceState(null, '', `/detail/${key}`);
  document.querySelectorAll('.agent-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.agent === key);
  });
  load();
}

// ── Sub-tab switching ─────────────────────────────────────────────────────────
function switchSub(tab) {
  _subTab = tab;
  document.querySelectorAll('.sub-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.querySelectorAll('.sub-panel').forEach(p => {
    p.style.display = p.dataset.panel === tab ? 'block' : 'none';
  });
  if (tab === 'chart' && _data) loadChart();
  if (tab === 'performance' && _data) renderPerf(_data);
  if (tab === 'logs') loadLogs();
}

// ── Main load ─────────────────────────────────────────────────────────────────
async function load() {
  try {
    _data = await fetchJSON(`/api/agent/${_agent}`);
    if (!_symbol) _symbol = (_data.meta.symbols || [])[0];
    render(_data);
    if (_subTab === 'chart') loadChart();
  } catch(e) {
    root.innerHTML = `<div class="empty">Failed to load agent data: ${e.message}</div>`;
  }
}

// ── Open position detail HTML ─────────────────────────────────────────────────
function openPosHtml(a) {
  // Normalise: forex/btc use a.position, stocks use a.positions dict
  const posRaw = a.positions || a.position;
  if (!posRaw) return '';

  const list = (typeof posRaw === 'object' && !posRaw.side)
    ? Object.entries(posRaw).filter(([, v]) => v && v.side).map(([sym, p]) => ({ sym, ...p }))
    : posRaw.side ? [{ sym: a.meta.pair, ...posRaw }] : [];

  if (!list.length) return '';

  return list.map(p => {
    const side     = (p.side || 'LONG').toUpperCase();
    const leverage = p.leverage || 1;
    const margin   = p.entry_value_usd != null ? fm(p.entry_value_usd) : '--';
    const notional = p.notional_usd != null
      ? fm(p.notional_usd)
      : p.entry_value_usd != null ? fm(p.entry_value_usd * leverage) : '--';
    const dp       = String(p.entry_price || '').includes('.') && Number(p.entry_price) < 100 ? 5 : 2;
    const sideCol  = side === 'SHORT' ? 'var(--red)' : 'var(--green)';
    return `<span class="bdg ${side==='LONG'?'bdg-g':'bdg-r'}">${p.sym||''} ${side}</span>` +
      (leverage > 1 ? ` <span class="bdg bdg-lev">${leverage}x</span>` : '') +
      ` <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--text2)">` +
      `@ ${Number(p.entry_price||0).toFixed(dp)} | margin ${margin} | notional ${notional}</span>`;
  }).join('<br>');
}

// ── Full render ───────────────────────────────────────────────────────────────
function render(d) {
  const a    = d;
  const col  = AGENT_COLOR[_agent];
  const pnlC = a.pnl_pct > 0 ? 'var(--green)' : a.pnl_pct < 0 ? 'var(--red)' : 'var(--text3)';

  const symSelector = _agent === 'stocks' ? `
    <div class="sym-btns" id="sym-btns">
      ${(AGENT_SYMBOLS[_agent].symbols || []).map(s =>
        `<button class="sym-btn ${s === _symbol ? 'active' : ''}"
          onclick="setSym('${s}')">${s}</button>`
      ).join('')}
    </div>` : '';

  const winBtns = ['1d','3d','7d','30d','all'].map(w =>
    `<button class="win-btn ${w === _window ? 'active' : ''}"
      onclick="setWindow('${w}')">${w}</button>`
  ).join('');

  const posDetail = openPosHtml(a);

  root.innerHTML = `
    <div class="card" style="margin-bottom:16px;padding:14px 20px">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
        <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
          <span class="bal-num" style="font-size:22px">${fm(a.balance)}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:${pnlC}">${fp(a.pnl_pct)}</span>
          ${posLabel(a)}
          ${a.trading_paused ? '<span class="bdg bdg-r">PAUSED</span>' : '<span class="bdg bdg-g">LIVE</span>'}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">${trgHtml(a)}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--text3)">
          last cycle ${fmtTimeOnly(a.last_checked)}
        </div>
      </div>
      ${posDetail ? `<div style="margin-top:8px;font-size:11px">${posDetail}</div>` : ''}
      ${a.unrealised_pnl && a.unrealised_pnl !== 0 ? `
        <div style="margin-top:6px;font-family:'IBM Plex Mono',monospace;font-size:11px;color:${a.unrealised_pnl>0?'var(--green)':'var(--red)'}">
          Unrealised P&L: ${a.unrealised_pnl>0?'+':''}${fm(a.unrealised_pnl)}
        </div>` : ''}
    </div>

    <div class="sub-tabs">
      <div class="sub-tab ${_subTab==='chart'?'active':''}"       data-tab="chart"       onclick="switchSub('chart')">Chart</div>
      <div class="sub-tab ${_subTab==='monitor'?'active':''}"     data-tab="monitor"     onclick="switchSub('monitor')">Monitor</div>
      <div class="sub-tab ${_subTab==='performance'?'active':''}" data-tab="performance" onclick="switchSub('performance')">Performance</div>
      <div class="sub-tab ${_subTab==='trades'?'active':''}"      data-tab="trades"      onclick="switchSub('trades')">Trades</div>
      <div class="sub-tab ${_subTab==='strategy'?'active':''}"    data-tab="strategy"    onclick="switchSub('strategy')">Strategy</div>
      <div class="sub-tab ${_subTab==='logs'?'active':''}"        data-tab="logs"        onclick="switchSub('logs')">Logs</div>
    </div>

    <div class="sub-panel" data-panel="chart" style="display:${_subTab==='chart'?'block':'none'}">
      <div class="chart-wrap">
        <div class="chart-toolbar">
          <div style="display:flex;align-items:center;gap:12px">
            <span class="chart-title" id="chart-title">${_symbol || ''}</span>
            ${symSelector}
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <div id="trade-nav" style="display:none;align-items:center;gap:6px;font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--text2)">
              <button id="trade-prev" onclick="stepTrade(-1)" style="background:none;border:1px solid var(--border);color:var(--text2);border-radius:3px;padding:2px 7px;cursor:pointer;font-size:11px">&#8592;</button>
              <span id="trade-counter"></span>
              <button id="trade-next" onclick="stepTrade(1)"  style="background:none;border:1px solid var(--border);color:var(--text2);border-radius:3px;padding:2px 7px;cursor:pointer;font-size:11px">&#8594;</button>
            </div>
            <button id="trade-toggle" onclick="toggleTrades()"
              style="background:none;border:1px solid var(--border);color:var(--text2);border-radius:3px;padding:2px 9px;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.05em">
              TRADES &#10003;
            </button>
            <div class="window-btns">${winBtns}</div>
          </div>
        </div>
        <div id="chart-container"></div>
      </div>
      <div class="sp"></div>
    </div>

    <div class="sub-panel" data-panel="monitor" style="display:${_subTab==='monitor'?'block':'none'}">
      ${renderMonitor(d)}
    </div>

    <div class="sub-panel" data-panel="performance" style="display:${_subTab==='performance'?'block':'none'}">
      ${renderPerf(d)}
    </div>

    <div class="sub-panel" data-panel="trades" style="display:${_subTab==='trades'?'block':'none'}">
      ${renderTrades(d.trades || [])}
    </div>

    <div class="sub-panel" data-panel="strategy" style="display:${_subTab==='strategy'?'block':'none'}">
      <div class="card">
        <div class="card-title"><span class="cdot" style="background:${col}"></span>Strategy notes</div>
        <div class="strategy-body">${(d.strategy || 'No strategy notes yet.').trim()}</div>
      </div>
      <div class="sp"></div>
    </div>

    <div class="sub-panel" data-panel="logs" style="display:${_subTab==='logs'?'block':'none'}">
      <div class="card" style="padding:0">
        <div style="display:flex;align-items:center;gap:0;border-bottom:1px solid var(--border);padding:10px 16px">
          <button onclick="setLogFile('monitor')" id="logbtn-monitor"
            style="background:${_logFile==='monitor'?'var(--hover)':'none'};border:none;font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:4px 12px;cursor:pointer;border-radius:3px;color:${_logFile==='monitor'?'var(--text)':'var(--text3)'}">monitor.log</button>
          <button onclick="setLogFile('brain')" id="logbtn-brain"
            style="background:${_logFile==='brain'?'var(--hover)':'none'};border:none;font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:4px 12px;cursor:pointer;border-radius:3px;color:${_logFile==='brain'?'var(--text)':'var(--text3)'}">brain.log</button>
          <button onclick="setLogFile('executor')" id="logbtn-executor"
            style="background:${_logFile==='executor'?'var(--hover)':'none'};border:none;font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:4px 12px;cursor:pointer;border-radius:3px;color:${_logFile==='executor'?'var(--text)':'var(--text3)'}">executor.log</button>
          <div style="flex:1"></div>
          <button onclick="loadLogs()" style="background:none;border:1px solid var(--border);color:var(--text3);border-radius:3px;padding:3px 10px;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:10px">&#8635; refresh</button>
        </div>
        <div id="log-container" style="font-family:'IBM Plex Mono',monospace;font-size:11px;line-height:1.6;padding:14px 16px;max-height:520px;overflow-y:auto;color:var(--text2);white-space:pre-wrap;word-break:break-all">Loading...</div>
      </div>
      <div class="sp"></div>
    </div>
  `;
}

// ── Trade toggle ──────────────────────────────────────────────────────────────
function toggleTrades() {
  _showTrades = !_showTrades;
  const btn = document.getElementById('trade-toggle');
  if (btn) btn.innerHTML = _showTrades ? 'TRADES &#10003;' : 'TRADES &#10005;';
  loadChart();
}

function stepTrade(dir) {
  if (!_tradesInView.length) return;
  _activeTrade = (_activeTrade + dir + _tradesInView.length) % _tradesInView.length;
  updateTradeCounter();
  scrollToActiveTrade();
  redrawTradeOverlays();
}

function updateTradeCounter() {
  const el  = document.getElementById('trade-counter');
  const nav = document.getElementById('trade-nav');
  if (!el || !nav) return;
  if (_tradesInView.length > 1) {
    nav.style.display = 'flex';
    el.textContent = `${_activeTrade + 1} / ${_tradesInView.length}`;
  } else {
    nav.style.display = 'none';
  }
}

function scrollToActiveTrade() {
  if (!_lwChart || !_tradesInView.length) return;
  const trade       = _tradesInView[_activeTrade];
  const entryRaw    = parseTs(trade.entry_ts);
  const entryTime   = entryRaw ? Math.floor(entryRaw.getTime() / 1000) : null;
  if (!entryTime || isNaN(entryTime)) return;
  try {
    _lwChart.timeScale().scrollToPosition(0, false);
    const coord = _lwChart.timeScale().timeToCoordinate(entryTime);
    if (coord !== null) {
      _lwChart.timeScale().scrollToPosition(
        -Math.round((_lwChart.timeScale().width() / 2 - coord) / 10), true
      );
    }
  } catch(e) {}
}

function clearPriceLines() {
  if (!_candleSeries) return;
  for (const line of _priceLines) {
    try { _candleSeries.removePriceLine(line); } catch(e) {}
  }
  _priceLines = [];
}

function redrawTradeOverlays() {
  if (!_candleSeries) return;
  clearPriceLines();
  if (!_showTrades || !_tradesInView.length) {
    _candleSeries.setMarkers([]);
    return;
  }
  drawTradeOverlays(_tradesInView, _activeTrade, _deduped);
}

// ── Draw trade overlays ───────────────────────────────────────────────────────
function drawTradeOverlays(trades, activeIdx, deduped) {
  if (!_candleSeries || !trades.length) return;
  clearPriceLines();

  const seriesMarkers = [];

  trades.forEach((trade, i) => {
    const isActive  = i === activeIdx;
    const entryRaw  = parseTs(trade.entry_ts);
    if (!entryRaw) return;
    const entryTimeSec = Math.floor(entryRaw.getTime() / 1000);
    const entryTime    = snapToCandle(entryTimeSec, deduped);
    if (!entryTime || isNaN(entryTime)) return;

    const side   = (trade.side || 'LONG').toUpperCase();
    const isLong = side === 'LONG';
    const lev    = trade.leverage;
    const levStr = lev && lev > 1 ? ` ${lev}x` : '';

    // Outcome colours
    const isWin  = (trade.outcome || '').toUpperCase() === 'WIN';
    const isLoss = (trade.outcome || '').toUpperCase() === 'LOSS';

    // Entry colour: green for LONG, red for SHORT
    const entryColor = isLong ? '#00d68f' : '#ff4d6a';
    const dimAlpha   = isActive ? 1 : 0.3;
    const lineAlpha  = isActive ? 0.55 : 0.15;

    // Exit colour: based on outcome
    const exitColor = isWin ? '#00d68f' : isLoss ? '#ff4d6a' : '#f0b429';

    // Entry marker:
    //   LONG  → green up arrow below bar (buying low)
    //   SHORT → red down arrow above bar (selling high)
    const entryLabel = isActive && trade.entry_price
      ? `${isLong ? 'LONG' : 'SHORT'}${levStr} @ ${Number(trade.entry_price).toFixed(2)}`
      : '';

    seriesMarkers.push({
      time:     entryTime,
      position: isLong ? 'belowBar' : 'aboveBar',
      color:    isActive ? entryColor : `rgba(${isLong?'0,214,143':'255,77,106'}, ${dimAlpha * 0.5})`,
      shape:    isLong ? 'arrowUp' : 'arrowDown',
      text:     entryLabel,
    });

    // Exit marker:
    //   LONG close  → down arrow above bar (selling)
    //   SHORT cover → up arrow below bar (buying to cover)
    if (trade.exit_ts) {
      const exitRaw     = parseTs(trade.exit_ts);
      const exitTimeSec = exitRaw ? Math.floor(exitRaw.getTime() / 1000) : null;
      const exitTime    = exitTimeSec ? snapToCandle(exitTimeSec, deduped) : null;
      if (exitTime && !isNaN(exitTime)) {
        const exitLabel = isActive && trade.exit_price
          ? `${trade.outcome || 'EXIT'} @ ${Number(trade.exit_price).toFixed(2)}`
          : '';
        seriesMarkers.push({
          time:     exitTime,
          position: isLong ? 'aboveBar' : 'belowBar',
          color:    isActive ? exitColor : `rgba(${isWin?'0,214,143':isLoss?'255,77,106':'240,180,41'}, ${dimAlpha * 0.5})`,
          shape:    isLong ? 'arrowDown' : 'arrowUp',
          text:     exitLabel,
        });
      }
    }

    // Price lines
    const entryLineColor = `rgba(${isLong?'0,214,143':'255,77,106'}, ${lineAlpha})`;

    if (trade.exit_ts) {
      // Closed trade: dashed entry + dotted exit
      if (trade.entry_price) {
        const line = _candleSeries.createPriceLine({
          price:            Number(trade.entry_price),
          color:            entryLineColor,
          lineWidth:        1,
          lineStyle:        LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: isActive,
          title:            isActive ? `${isLong?'Long':'Short'} entry ${Number(trade.entry_price).toFixed(2)}` : '',
        });
        _priceLines.push(line);
      }
      if (trade.exit_price) {
        const exitLineColor = isWin
          ? `rgba(0,214,143,${lineAlpha})`
          : isLoss
            ? `rgba(255,77,106,${lineAlpha})`
            : `rgba(240,180,41,${lineAlpha})`;
        const pnlStr = trade.pnl_pct != null
          ? ` (${trade.pnl_pct >= 0 ? '+' : ''}${Number(trade.pnl_pct).toFixed(2)}%)`
          : '';
        const line = _candleSeries.createPriceLine({
          price:            Number(trade.exit_price),
          color:            exitLineColor,
          lineWidth:        1,
          lineStyle:        LightweightCharts.LineStyle.Dotted,
          axisLabelVisible: isActive,
          title:            isActive ? `${trade.outcome || 'Exit'} ${Number(trade.exit_price).toFixed(2)}${pnlStr}` : '',
        });
        _priceLines.push(line);
      }
    } else {
      // Open trade: solid line
      if (trade.entry_price) {
        const line = _candleSeries.createPriceLine({
          price:            Number(trade.entry_price),
          color:            `rgba(${isLong?'0,214,143':'255,77,106'}, ${isActive ? 0.7 : 0.2})`,
          lineWidth:        1,
          lineStyle:        LightweightCharts.LineStyle.Solid,
          axisLabelVisible: isActive,
          title:            isActive ? `${isLong?'Long':'Short'}${levStr} open @ ${Number(trade.entry_price).toFixed(2)}` : '',
        });
        _priceLines.push(line);
      }
    }
  });

  seriesMarkers.sort((a, b) => a.time - b.time);
  _candleSeries.setMarkers(seriesMarkers);
}

// ── Candlestick chart ─────────────────────────────────────────────────────────
async function loadChart() {
  const container = document.getElementById('chart-container');
  if (!container || !_symbol) return;

  if (_lwChart) { _lwChart.remove(); _lwChart = null; _candleSeries = null; _priceLines = []; }
  container.innerHTML = '<div class="empty" style="padding:20px 0">Loading chart...</div>';

  try {
    const resp        = await fetchJSON(`/api/ohlcv/${_agent}/${_symbol}?window=${_window}`);
    const candles     = resp.candles       || [];
    const trades      = resp.trades        || [];
    const intervalSec = (resp.interval_mins || 15) * 60;

    if (!candles.length) {
      container.innerHTML = '<div class="empty" style="padding:40px 0">No OHLCV data yet</div>';
      return;
    }

    container.innerHTML = '';
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

    _lwChart = LightweightCharts.createChart(container, {
      width:  container.clientWidth,
      height: 360,
      layout: {
        background: { color: isDark ? '#141720' : '#ffffff' },
        textColor:  isDark ? '#9196a8' : '#5a6075',
        fontSize: 11,
        fontFamily: "'IBM Plex Mono', monospace",
      },
      grid: {
        vertLines: { color: isDark ? '#1a1f2e' : '#f0f2f7' },
        horzLines: { color: isDark ? '#1a1f2e' : '#f0f2f7' },
      },
      crosshair:       { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: isDark ? '#232840' : '#d8dce8' },
      timeScale:       { borderColor: isDark ? '#232840' : '#d8dce8', timeVisible: true,
                         lockVisibleTimeRangeOnResize: true },
      handleScroll:    { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
      handleScale:     { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: {
        timeFormatter: (ts) => {
          const d = new Date((ts + (window.TZ_OFFSET || 0) * 3600) * 1000);
          return String(d.getUTCHours()).padStart(2,'0') + ':' + String(d.getUTCMinutes()).padStart(2,'0');
        },
        dateFormatter: (ts) => {
          const d = new Date((ts + (window.TZ_OFFSET || 0) * 3600) * 1000);
          return String(d.getUTCDate()).padStart(2,'0') + '/' + String(d.getUTCMonth()+1).padStart(2,'0');
        },
      },
    });

    _candleSeries = _lwChart.addCandlestickSeries({
      upColor:         '#00d68f',
      downColor:       '#ff4d6a',
      borderUpColor:   '#00d68f',
      borderDownColor: '#ff4d6a',
      wickUpColor:     '#00d68f',
      wickDownColor:   '#ff4d6a',
    });

    const seen = new Set();
    const deduped = candles.map(c => {
      let time;
      try { time = Math.floor(new Date(c.ts).getTime() / 1000); } catch { return null; }
      if (!time || isNaN(time) || seen.has(time)) return null;
      seen.add(time);
      return { time, open: c.open, high: c.high, low: c.low, close: c.close };
    }).filter(Boolean).sort((a, b) => a.time - b.time);

    _deduped = deduped;
    _candleSeries.setData(deduped);

    if (deduped.length) {
      const windowDays = { '1d': 1, '3d': 3, '7d': 7, '30d': 30, 'all': 9999 }[_window] || 7;
      const nowSec  = Math.floor(Date.now() / 1000);
      const fromSec = windowDays >= 9999 ? deduped[0].time : nowSec - windowDays * 86400;
      const toSec   = nowSec + 900;
      _lwChart.timeScale().setVisibleRange({ from: fromSec, to: toSec });
    } else {
      _lwChart.timeScale().fitContent();
    }

    if (deduped.length) {
      const minTime = deduped[0].time;
      const maxTime = deduped[deduped.length - 1].time;

      _tradesInView = trades.filter(t => {
        if (!t.entry_ts) return false;
        const et = parseTs(t.entry_ts);
        const xt = t.exit_ts ? parseTs(t.exit_ts) : null;
        const entryS = et ? Math.floor(et.getTime() / 1000) : null;
        const exitS  = xt ? Math.floor(xt.getTime() / 1000) : null;
        if (!t.exit_ts) return true;
        const entryInRange = entryS && entryS >= minTime && entryS <= maxTime;
        const exitInRange  = exitS  && exitS  >= minTime && exitS  <= maxTime;
        const straddles    = entryS && exitS && entryS <= minTime && exitS >= minTime;
        return entryInRange || exitInRange || straddles;
      });
    } else {
      _tradesInView = [];
    }

    _tradesInView.sort((a, b) => {
      const aOpen = !a.exit_ts;
      const bOpen = !b.exit_ts;
      if (aOpen && !bOpen) return 1;
      if (!aOpen && bOpen) return -1;
      return (a.entry_ts || '') < (b.entry_ts || '') ? -1 : 1;
    });

    if (_activeTrade < 0 || _activeTrade >= _tradesInView.length) {
      _activeTrade = Math.max(0, _tradesInView.length - 1);
    }

    updateTradeCounter();

    if (_showTrades && _tradesInView.length) {
      drawTradeOverlays(_tradesInView, _activeTrade, deduped);
    }

    const btn = document.getElementById('trade-toggle');
    if (btn) btn.innerHTML = _showTrades ? 'TRADES &#10003;' : 'TRADES &#10005;';

    const ro = new ResizeObserver(() => {
      if (_lwChart) _lwChart.resize(container.clientWidth, 360);
    });
    ro.observe(container);

  } catch(e) {
    container.innerHTML = `<div class="empty" style="padding:40px 0">Chart error: ${e.message}</div>`;
  }
}

function setWindow(w) {
  _window = w;
  _activeTrade = -1;
  document.querySelectorAll('.win-btn').forEach(b => b.classList.toggle('active', b.textContent === w));
  loadChart();
}

function setSym(s) {
  _symbol = s;
  _activeTrade = -1;
  document.querySelectorAll('.sym-btn').forEach(b => b.classList.toggle('active', b.textContent === s));
  const titleEl = document.getElementById('chart-title');
  if (titleEl) titleEl.textContent = s;
  loadChart();
}

// ── Monitor panel ─────────────────────────────────────────────────────────────
function renderMonitor(d) {
  const ind = d.last_indicators || {};
  const ctx = d.market_context  || {};
  const dp  = _agent === 'forex' ? 5 : 2;

  let indHtml = '';
  if (_agent === 'stocks') {
    indHtml = Object.entries(ind).map(([sym, i]) => {
      const rc = i.rsi < 35 ? 'neg' : i.rsi > 65 ? 'hot' : '';
      return `
        <tr><td class="k" colspan="2" style="color:var(--text2);padding-top:8px;font-weight:600">${sym}</td></tr>
        <tr><td class="k">Price</td><td class="v">$${f(i.price,2)}</td></tr>
        <tr><td class="k">RSI</td><td class="v ${rc}">${f(i.rsi,1)}</td></tr>
        <tr><td class="k">MACD hist</td><td class="v ${i.macd_hist>0?'pos':'neg'}">${f(i.macd_hist,4)}</td></tr>
        <tr><td class="k">BB%</td><td class="v">${f(i.bb_pct,3)}</td></tr>
        <tr><td class="k">Vol ratio</td><td class="v ${i.vol_spike?'hot':''}">${f(i.vol_ratio,2)}x</td></tr>`;
    }).join('');
  } else {
    const rc = ind.rsi < 35 ? 'neg' : ind.rsi > 65 ? 'hot' : '';
    indHtml = `
      <tr><td class="k">Price</td><td class="v">${f(ind.price,dp)}</td></tr>
      <tr><td class="k">Change</td><td class="v ${ind.price_change_pct>0?'pos':'neg'}">${fp(ind.price_change_pct)}</td></tr>
      <tr><td class="k">RSI(14)</td><td class="v ${rc}">${f(ind.rsi,1)}</td></tr>
      <tr><td class="k">MACD hist</td><td class="v ${ind.macd_hist>0?'pos':'neg'}">${f(ind.macd_hist,4)}</td></tr>
      <tr><td class="k">BB upper</td><td class="v">${f(ind.bb_upper,dp)}</td></tr>
      <tr><td class="k">BB mid</td><td class="v">${f(ind.bb_mid,dp)}</td></tr>
      <tr><td class="k">BB lower</td><td class="v">${f(ind.bb_lower,dp)}</td></tr>
      <tr><td class="k">BB%</td><td class="v">${f(ind.bb_pct,3)}</td></tr>
      <tr><td class="k">Vol ratio</td><td class="v ${ind.vol_spike?'hot':''}">${f(ind.vol_ratio,2)}x</td></tr>`;
  }

  let macroHtml = '';
  if (_agent === 'btc') {
    macroHtml = [
      ['Fear & Greed', ctx.fear_greed_value != null ? `${ctx.fear_greed_value} (${ctx.fear_greed_label||'--'})` : '--'],
      ['SP500', ctx.sp500_direction ? `${ctx.sp500_direction} ${ctx.sp500_change_pct!=null?fp(ctx.sp500_change_pct):''}` : '--'],
      ['Funding rate', ctx.funding_rate != null ? ctx.funding_rate : '--'],
      ['Open interest', ctx.open_interest != null ? ctx.open_interest : '--'],
      ['BTC dominance', ctx.btc_dominance != null ? ctx.btc_dominance + '%' : '--'],
    ].map(([k,v]) => `<div class="mac-row"><span class="mac-k">${k}</span><span class="mac-v">${v}</span></div>`).join('');
  } else if (_agent === 'forex') {
    macroHtml = [
      ['DXY', ctx.dxy_price != null ? `${f(ctx.dxy_price,2)} (${ctx.usd_strength||'--'})` : '--'],
      ['SP500', ctx.sp500_direction ? `${ctx.sp500_direction} ${ctx.sp500_change_pct!=null?fp(ctx.sp500_change_pct):''}` : '--'],
      ['Gold', ctx.gold_price != null ? `$${f(ctx.gold_price,2)}` : '--'],
      ['US 10Y', ctx.us10y_yield != null ? f(ctx.us10y_yield,3) + '%' : '--'],
      ['VIX', ctx.vix_level != null ? `${f(ctx.vix_level,1)} (${ctx.vix_regime||'--'})` : '--'],
      ['Risk', ctx.risk_sentiment || '--'],
    ].map(([k,v]) => `<div class="mac-row"><span class="mac-k">${k}</span><span class="mac-v">${v}</span></div>`).join('');
  } else {
    macroHtml = [
      ['VIX', ctx.vix_level != null ? `${f(ctx.vix_level,1)} (${ctx.vix_regime||'--'})` : '--'],
      ['SPY trend', ctx.spy_trend || '--'],
      ['Risk', ctx.risk_sentiment || '--'],
    ].map(([k,v]) => `<div class="mac-row"><span class="mac-k">${k}</span><span class="mac-v">${v}</span></div>`).join('');
  }

  const news = ctx.news;
  let newsHtml = '<div class="empty" style="padding:12px 0">No headlines</div>';
  if (news) {
    const hls = Array.isArray(news) ? news : (news.headlines || []);
    if (hls.length) newsHtml = hls.slice(0,6).map(h =>
      `<div class="hl">${h.title||h}<div class="hl-time">${fmtNewsDate(h.published||h.date||'')}</div></div>`
    ).join('');
  }

  return `
    <div class="g3" style="margin-bottom:16px">
      <div class="card">
        <div class="card-title">Indicators</div>
        <table class="ind-tbl">${indHtml}</table>
      </div>
      <div class="card">
        <div class="card-title">Market context</div>
        ${macroHtml || '<div class="empty" style="padding:12px 0">No context</div>'}
      </div>
      <div class="card">
        <div class="card-title">Headlines</div>
        ${newsHtml}
      </div>
    </div>
    <div class="sp"></div>`;
}

// ── Performance panel ─────────────────────────────────────────────────────────
function renderPerf(d) {
  const p   = d.performance || {};
  const eq  = d.equity      || [];
  const wr  = p.win_rate != null ? p.win_rate : null;
  const wrPct = wr != null ? (wr * 100).toFixed(1) + '%' : '--';
  const wrW   = wr != null ? (wr * 100).toFixed(1) : 0;
  const col   = AGENT_COLOR[_agent];

  let symRows = '';
  if (_agent === 'stocks' && p.per_symbol && Object.keys(p.per_symbol).length) {
    symRows = `<div style="margin-top:16px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--text3);margin-bottom:8px">Per symbol</div>
      <table class="sym-tbl"><thead><tr><th>Sym</th><th>Trades</th><th>W%</th><th>Avg P&L</th></tr></thead><tbody>
      ${Object.entries(p.per_symbol).map(([sym,s]) => `<tr>
        <td style="color:var(--stocks)">${sym}</td>
        <td>${s.trades}</td>
        <td>${(s.win_rate*100).toFixed(0)}%</td>
        <td style="color:${s.avg_pnl_pct>=0?'var(--green)':'var(--red)'}">${s.avg_pnl_pct>=0?'+':''}${f(s.avg_pnl_pct,2)}%</td>
      </tr>`).join('')}
      </tbody></table></div>`;
  }

  const perfCard = `
    <div class="card">
      <div class="card-title"><span class="cdot" style="background:${col}"></span>Stats</div>
      <div class="wl-labels">
        <span style="color:var(--green);font-family:'IBM Plex Mono',monospace;font-size:10px">WIN ${p.wins||0}</span>
        <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--text)">${wrPct}</span>
        <span style="color:var(--red);font-family:'IBM Plex Mono',monospace;font-size:10px">LOSS ${p.losses||0}</span>
      </div>
      <div class="wl-bar"><div class="wl-fill" style="width:${wrW}%"></div></div>
      <div class="pstat"><span class="pk">Total trades</span><span class="pv">${p.total_closed_trades??'--'}</span></div>
      <div class="pstat"><span class="pk">Avg win</span><span class="pv g">${p.avg_win_pct!=null?'+'+f(p.avg_win_pct,2)+'%':'--'}</span></div>
      <div class="pstat"><span class="pk">Avg loss</span><span class="pv r">${p.avg_loss_pct!=null?f(p.avg_loss_pct,2)+'%':'--'}</span></div>
      <div class="pstat"><span class="pk">Profit factor</span><span class="pv ${p.profit_factor>=1.5?'g':p.profit_factor!=null?'a':''}">${p.profit_factor!=null?f(p.profit_factor):'--'}</span></div>
      <div class="pstat"><span class="pk">Sharpe</span><span class="pv ${p.sharpe_approx>=1?'g':p.sharpe_approx!=null?'a':''}">${p.sharpe_approx!=null?f(p.sharpe_approx):'--'}</span></div>
      <div class="pstat"><span class="pk">Total P&L</span><span class="pv ${p.total_pnl_usd>0?'g':p.total_pnl_usd<0?'r':''}">${p.total_pnl_usd!=null?fm(p.total_pnl_usd):'--'}</span></div>
      <div class="pstat"><span class="pk">Balance</span><span class="pv">${fm(d.balance)}</span></div>
      <div class="pstat"><span class="pk">Peak balance</span><span class="pv">${fm(d.peak_balance)}</span></div>
      ${symRows}
    </div>`;

  const equityCard = `
    <div class="card">
      <div class="card-title"><span class="cdot" style="background:${col}"></span>Equity curve</div>
      <div class="eq-wrap"><canvas id="eq-chart"></canvas></div>
    </div>`;

  setTimeout(() => {
    Charts.kill('eq-chart');
    const ctx = document.getElementById('eq-chart');
    if (!ctx || !eq.length) return;
    const vals = eq.map(e => e.balance);
    const isUp = vals[vals.length-1] >= vals[0];
    const tCol = document.documentElement.getAttribute('data-theme') === 'dark' ? '#555c72' : '#9ba0b0';
    Charts.set('eq-chart', new Chart(ctx.getContext('2d'), {
      type: 'line',
      data: { labels: eq.map(e => fmtTs(e.ts)), datasets: [{
        data: vals, borderColor: isUp ? '#00d68f' : '#ff4d6a', borderWidth:1.5,
        pointRadius:0, fill:true, backgroundColor: isUp ? 'rgba(0,214,143,.06)' : 'rgba(255,77,106,.06)', tension:0.4
      }]},
      options: { responsive:true, maintainAspectRatio:false,
        plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'$'+c.parsed.y.toFixed(2)}}},
        scales:{
          x:{display:true,ticks:{color:tCol,font:{size:9,family:'IBM Plex Mono'},maxTicksLimit:8,maxRotation:0},grid:{color:'rgba(128,128,128,.07)'},border:{display:false}},
          y:{display:true,ticks:{color:tCol,font:{size:9,family:'IBM Plex Mono'},callback:v=>'$'+v.toFixed(0),maxTicksLimit:5},grid:{color:'rgba(128,128,128,.07)'},border:{display:false}}
        }
      }
    }));
  }, 50);

  return `<div class="g2">${perfCard}${equityCard}</div><div class="sp"></div>`;
}

// ── Trades panel ──────────────────────────────────────────────────────────────
function renderTrades(trades) {
  const rows = trades.slice(0, 100).map(t => {
    const action   = t.action || '--';
    const side     = (t.side || '').toUpperCase();
    const leverage = t.leverage;
    const outcome  = t.outcome;
    const pnl      = t.pnl_pct;
    const pnlUsd   = t.pnl_usd;
    const conf     = t.confidence != null ? (t.confidence * 100).toFixed(0) + '%' : '--';
    const margin   = t.position_size_pct != null && t.balance_at_trade != null
      ? '$' + (Number(t.balance_at_trade) * Number(t.position_size_pct)).toFixed(2)
        + ' (' + (Number(t.position_size_pct)*100).toFixed(0) + '%)'
      : '--';
    const sym      = t.symbol || _agent.toUpperCase();
    const rsn      = (t.reasoning || '').substring(0, 140);
    const oC       = outcome === 'WIN' ? 'style="color:var(--green)"' : outcome === 'LOSS' ? 'style="color:var(--red)"' : action !== 'WAIT' ? 'style="color:var(--amber)"' : 'style="color:var(--text3)"';
    const oL       = outcome || (action !== 'WAIT' ? 'OPEN' : '—');
    const pC       = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text3)';
    const sideCol  = side === 'SHORT' ? 'var(--red)' : side === 'LONG' ? 'var(--green)' : 'var(--text2)';
    const levLabel = leverage && leverage > 1 ? ` <span class="bdg bdg-lev">${leverage}x</span>` : '';
    const pnlDisp  = pnl != null
      ? `${fp(pnl)}${pnlUsd != null ? ' / ' + (pnlUsd >= 0 ? '+' : '') + fm(pnlUsd) : ''}`
      : '--';
    return `<tr>
      <td>${fmtTs(t.ts || t.opened_at)}</td>
      <td style="color:var(--text2)">${sym}</td>
      <td style="color:${sideCol};font-weight:600">${side || action}${levLabel}</td>
      <td ${oC}>${oL}</td>
      <td style="color:${pC}">${pnlDisp}</td>
      <td style="color:var(--text3)">${margin}</td>
      <td style="color:var(--text3)">${conf}</td>
      <td class="rsn" title="${rsn.replace(/"/g,'&quot;')}">${rsn||'—'}</td>
    </tr>`;
  }).join('');

  if (!rows) return `<div class="empty">No trades recorded yet</div><div class="sp"></div>`;

  return `
    <div class="card" style="padding:0">
      <div class="tbl-wrap" style="padding:0 20px">
        <table class="t-tbl">
          <thead><tr>
            <th>Time</th><th>Symbol</th><th>Side / Lev</th>
            <th>Outcome</th><th>P&L % / $</th><th>Margin</th><th>Conf</th><th>Reasoning</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
    <div class="sp"></div>`;
}

// ── Log viewer ─────────────────────────────────────────────────────────────────
function setLogFile(file) {
  _logFile = file;
  ['monitor','brain','executor'].forEach(f => {
    const btn = document.getElementById(`logbtn-${f}`);
    if (!btn) return;
    btn.style.color      = f === file ? 'var(--text)'  : 'var(--text3)';
    btn.style.background = f === file ? 'var(--hover)' : 'none';
  });
  loadLogs();
}

async function loadLogs() {
  const container = document.getElementById('log-container');
  if (!container) return;
  try {
    const resp = await fetchJSON(`/api/logs/${_agent}?file=${_logFile}&lines=200`);
    if (!resp.exists) {
      container.textContent = `${_logFile}.log not found`;
      return;
    }
    if (!resp.lines.length) { container.textContent = '(empty)'; return; }
    container.innerHTML = resp.lines.map(line => {
      let color = 'var(--text2)';
      if (/\bERROR\b/.test(line))   color = 'var(--red)';
      else if (/\bWARNING\b/.test(line)) color = '#f0b429';
      else if (/leverage=\d+x|OPEN_LONG|OPEN_SHORT|SELL_SHORT|BUY_TO_COVER|BUY|SELL|CLOSE|Executor invoked/.test(line)) color = 'var(--green)';
      else if (/WAIT|cooldown|suppressed|filtered/.test(line)) color = 'var(--text3)';
      return `<span style="color:${color}">${escHtml(line)}</span>`;
    }).join('\n');
    container.scrollTop = container.scrollHeight;
  } catch(e) {
    if (container) container.textContent = `Error loading logs: ${e.message}`;
  }
}

function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Poll ──────────────────────────────────────────────────────────────────────
const poller = new Poller(30)
  .setLabel(document.getElementById('refresh-lbl'))
  .onTick(async () => {
    try {
      _data = await fetchJSON(`/api/agent/${_agent}`);
      if (!_symbol) _symbol = (_data.meta.symbols || [])[0];
      render(_data);
      if (_subTab === 'chart') loadChart();
      if (_subTab === 'logs') loadLogs();
      const el = document.getElementById('srv-time');
      if (el) el.textContent = fmtTimeOnly(_data.server_time);
    } catch(e) { console.error('Detail fetch failed:', e); }
  })
  .start();

function onThemeChange() {
  if (_data) {
    render(_data);
    if (_subTab === 'chart') loadChart();
    if (_subTab === 'logs') loadLogs();
  }
}
