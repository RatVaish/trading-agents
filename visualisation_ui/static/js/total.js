/* total.js — TOTAL view rendering */

const tRoot = document.getElementById('total-page');
let _data     = null;
let _eqWindow = 'all';

function renderTotal(data) {
  _data = data;
  const agents = data.agents;

  const cards = AGENT_KEYS.map(k => {
    const a = agents[k];
    const p = a.performance || {};
    const pnlC = a.pnl_pct > 0 ? 'var(--green)' : a.pnl_pct < 0 ? 'var(--red)' : 'var(--text3)';
    const wr   = p.win_rate != null ? (p.win_rate * 100).toFixed(0) + '%' : '--';
    const pf   = p.profit_factor != null ? f(p.profit_factor) : '--';
    const trgs = trgHtml(a);

    return `
    <div class="agent-card" onclick="location.href='/detail/${k}'">
      <div class="agent-card-accent" style="background:${AGENT_COLOR[k]}"></div>
      <div class="agent-hdr">
        <div>
          <div class="agent-lbl">${k.toUpperCase()}</div>
          <div class="agent-pair">${a.meta.pair}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:5px">
          ${a.trading_paused ? '<span class="bdg bdg-r">PAUSED</span>' : '<span class="bdg bdg-g">LIVE</span>'}
          ${posLabel(a)}
        </div>
      </div>
      <div class="bal-num">${fm(a.balance)}</div>
      ${a.unrealised_pnl && a.unrealised_pnl !== 0 ? `
        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:${a.unrealised_pnl>0?'var(--green)':'var(--red)'}">
          ${a.unrealised_pnl>0?'+':''}${fm(a.unrealised_pnl)} unrealised
        </div>` : ''}
      <div class="bal-pnl" style="color:${pnlC}">${fp(a.pnl_pct)} since start</div>
      ${trgs ? `<div class="trg-row">${trgs}</div>` : ''}
      <div style="display:flex;gap:4px;margin:6px 0 2px">
        ${['all','1m','1w'].map(w => `<button onclick="event.stopPropagation();setEqWindow('${w}')" id="eqbtn-${k}-${w}"
          style="background:${w===_eqWindow?'var(--hover)':'none'};border:1px solid var(--border);
                 color:${w===_eqWindow?'var(--text)':'var(--text3)'};border-radius:3px;
                 padding:1px 7px;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:9px;
                 letter-spacing:.05em">${w}</button>`).join('')}
      </div>
      <div class="mini-wrap"><canvas id="mini-${k}"></canvas></div>
      <div class="stat-g">
        <div><div class="slbl">Win rate</div><div class="sval">${wr}</div></div>
        <div><div class="slbl">Profit factor</div><div class="sval">${pf}</div></div>
        <div><div class="slbl">Trades</div><div class="sval">${a.total_trades}</div></div>
        <div><div class="slbl">Cycles</div><div class="sval">${a.cycle_count}</div></div>
      </div>
    </div>`;
  }).join('');

  const pnlVals    = AGENT_KEYS.map(k => Number((agents[k].pnl_pct || 0).toFixed(2)));
  const activeTrades = renderActiveTrades();
  const tradeFeed    = renderTradeFeed();

  tRoot.innerHTML = `
    <div class="sec-hdr">Agent Status <span style="color:var(--text3);font-size:9px">click card to open detail</span></div>
    <div class="g3">${cards}</div>

    <div class="sec-hdr">P&amp;L Comparison</div>
    <div class="card">
      <div style="position:relative;height:160px"><canvas id="pnl-chart"></canvas></div>
    </div>

    ${activeTrades}

    <div class="sec-hdr">Recent Activity</div>
    <div class="card" style="padding:0">
      <div class="tbl-wrap" style="padding:0 20px">${tradeFeed}</div>
    </div>
    <div class="sp"></div>
  `;

  // ── Sparklines ──────────────────────────────────────────────────────────────
  AGENT_KEYS.forEach(k => {
    const eq = agents[k].equity || [];
    Charts.kill('mini-' + k);
    const ctx = document.getElementById('mini-' + k);
    if (!ctx || !eq.length) return;
    const vals = eq.map(e => e.balance);
    const upnl = agents[k].unrealised_pnl || 0;
    if (upnl !== 0) vals.push((agents[k].balance || 150) + upnl);
    const isUp = vals[vals.length - 1] >= vals[0];
    const vMin = vals.reduce((a, b) => a < b ? a : b, vals[0]);
    const vMax = vals.reduce((a, b) => a > b ? a : b, vals[0]);
    const vPad = Math.max((vMax - vMin) * 0.3, 0.05);
    Charts.set('mini-' + k, new Chart(ctx.getContext('2d'), {
      type: 'line',
      data: { labels: vals.map((_, i) => i), datasets: [{
        data: vals, borderColor: isUp ? '#00d68f' : '#ff4d6a', borderWidth: 1.5,
        pointRadius: 0, fill: true,
        backgroundColor: isUp ? 'rgba(0,214,143,.07)' : 'rgba(255,77,106,.07)', tension: 0.4
      }]},
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: false },
          y: { display: false, min: vMin - vPad, max: vMax + vPad },
        },
        animation: false,
      }
    }));
  });

  // ── P&L bar chart ──────────────────────────────────────────────────────────
  Charts.kill('pnl-chart');
  const pnlCtx = document.getElementById('pnl-chart');
  if (pnlCtx) {
    const tCol = document.documentElement.getAttribute('data-theme') === 'dark' ? '#555c72' : '#9ba0b0';
    Charts.set('pnl-chart', new Chart(pnlCtx.getContext('2d'), {
      type: 'bar',
      data: {
        labels: AGENT_KEYS.map(k => k.toUpperCase()),
        datasets: [{
          data:            pnlVals,
          backgroundColor: pnlVals.map(v => v >= 0 ? 'rgba(0,214,143,.5)' : 'rgba(255,77,106,.5)'),
          borderColor:     pnlVals.map(v => v >= 0 ? '#00d68f' : '#ff4d6a'),
          borderWidth: 1, borderRadius: 3,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: c => (c.parsed.y >= 0 ? '+' : '') + c.parsed.y.toFixed(2) + '%' } } },
        scales: {
          x: { ticks: { color: tCol, font: { size: 10, family: 'IBM Plex Mono' } }, grid: { display: false }, border: { display: false } },
          y: { ticks: { color: tCol, font: { size: 9, family: 'IBM Plex Mono' }, callback: v => v + '%', maxTicksLimit: 5 },
               grid: { color: 'rgba(128,128,128,.07)' }, border: { display: false } },
        },
      },
    }));
  }
}

// ── Open positions table ──────────────────────────────────────────────────────
function renderActiveTrades() {
  if (!_data) return '';
  const agents = _data.agents;
  const rows = [];

  AGENT_KEYS.forEach(k => {
    const a = agents[k];
    // Support both single-position (btc/forex) and multi-position (stocks) formats
    const posRaw = a.positions || a.position;
    if (!posRaw) return;

    const positions = (typeof posRaw === 'object' && !posRaw.side)
      ? Object.entries(posRaw).filter(([, v]) => v && v.side).map(([sym, p]) => ({ sym, ...p }))
      : [{ sym: a.meta.pair, ...posRaw }];

    positions.forEach(p => {
      if (!p.entry_price || !p.side) return;
      const col      = AGENT_COLOR[k];
      const since    = p.opened_at ? fmtTs(p.opened_at) : '--';
      const side     = (p.side || 'LONG').toUpperCase();
      const leverage = p.leverage || 1;
      const notional = p.notional_usd != null
        ? fm(p.notional_usd)
        : p.entry_value_usd != null
          ? fm(p.entry_value_usd * leverage)
          : '--';
      const margin   = p.entry_value_usd != null ? fm(p.entry_value_usd) : '--';
      const dp       = p.sym && (p.sym.includes('USD') || p.sym.includes('/')) && !p.sym.startsWith('XBT') ? 5 : 2;

      rows.push(`
        <tr onclick="location.href='/detail/${k}'" style="cursor:pointer">
          <td><span class="cdot" style="background:${col}"></span>${k.toUpperCase()}</td>
          <td style="color:var(--text2)">${p.sym || '--'}</td>
          <td><span class="bdg ${side === 'LONG' ? 'bdg-g' : 'bdg-r'}">${side}</span></td>
          <td>${leverage > 1 ? `<span class="bdg bdg-lev">${leverage}x</span>` : '<span style="color:var(--text3)">1x</span>'}</td>
          <td style="font-family:'IBM Plex Mono',monospace">${Number(p.entry_price).toFixed(dp)}</td>
          <td style="color:var(--text3);font-size:10px">${since}</td>
          <td style="font-family:'IBM Plex Mono',monospace" title="Margin: ${margin}">${notional}</td>
          <td>${p.stop_loss_price != null ? Number(p.stop_loss_price).toFixed(dp) : '--'}</td>
        </tr>`);
    });
  });

  if (!rows.length) return '';
  return `
    <div class="sec-hdr">Open Positions</div>
    <div class="card" style="padding:0">
      <div class="tbl-wrap" style="padding:0 20px">
        <table class="t-tbl">
          <thead><tr>
            <th>Agent</th><th>Symbol</th><th>Side</th><th>Lev</th>
            <th>Entry</th><th>Opened</th><th>Notional</th><th>Stop</th>
          </tr></thead>
          <tbody>${rows.join('')}</tbody>
        </table>
      </div>
    </div>`;
}

// ── Recent trade feed ─────────────────────────────────────────────────────────
function renderTradeFeed() {
  if (!_data) return '<div class="empty">No data</div>';
  const agents = _data.agents;

  let all = [];
  AGENT_KEYS.forEach(k => {
    (agents[k].trades || []).forEach(t => all.push({ ...t, _agent: k }));
  });

  if (!all.length) return '<div class="empty" style="padding:20px 0;font-size:11px">No trades yet</div>';

  all.sort((a, b) => {
    const aOpen = !a.outcome && !a.closed_at;
    const bOpen = !b.outcome && !b.closed_at;
    if (aOpen && !bOpen) return -1;
    if (!aOpen && bOpen) return 1;
    return (b.ts || b.opened_at || '') > (a.ts || a.opened_at || '') ? 1 : -1;
  });

  const rows = all.slice(0, 30).map(t => {
    const col     = AGENT_COLOR[t._agent];
    const action  = t.action || '--';
    const side    = (t.side || '').toUpperCase();
    const leverage= t.leverage;
    const outcome = t.outcome;
    const isOpen  = !outcome && !t.closed_at;
    const pnl     = t.pnl_pct;
    const oC      = isOpen             ? 'style="color:var(--amber)"'
                  : outcome === 'WIN'  ? 'style="color:var(--green)"'
                  : outcome === 'LOSS' ? 'style="color:var(--red)"'
                  : 'style="color:var(--text3)"';
    const oL = isOpen ? 'OPEN' : (outcome || '—');
    const pC = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text3)';

    // Human-readable action label with direction colour
    const sideColor = side === 'SHORT' ? 'var(--red)' : 'var(--green)';
    const actionLabel = side
      ? `<span style="color:${sideColor}">${side}</span>`
      : action;
    const levLabel = leverage && leverage > 1
      ? `<span class="bdg bdg-lev" style="margin-left:3px">${leverage}x</span>`
      : '';

    return `<tr onclick="location.href='/detail/${t._agent}'" style="cursor:pointer">
      <td><span class="cdot" style="background:${col}"></span>${t._agent.toUpperCase()}</td>
      <td style="color:var(--text2)">${t.symbol || '--'}</td>
      <td>${actionLabel}${levLabel}</td>
      <td ${oC}>${oL}</td>
      <td style="color:${pC}">${pnl!=null?fp(pnl):'--'}</td>
      <td style="color:var(--text3);font-size:10px">${fmtTs(t.ts || t.opened_at)}</td>
    </tr>`;
  }).join('');

  return `<table class="t-tbl">
    <thead><tr>
      <th>Agent</th><th>Symbol</th><th>Side / Lev</th>
      <th>Outcome</th><th>P&L</th><th>Time</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function setEqWindow(w) {
  _eqWindow = w;
  load();
}

async function load() {
  try {
    const resp = await fetchJSON(`/api/overview?eq_window=${_eqWindow}`);
    renderTotal(resp);
    const el = document.getElementById('srv-time');
    if (el) el.textContent = fmtTimeOnly(resp.server_time);
  } catch(e) {
    tRoot.innerHTML = `<div class="empty">Failed to load: ${e.message}</div>`;
  }
}

const poller = new Poller(30)
  .setLabel(document.getElementById('refresh-lbl'))
  .onTick(load)
  .start();

function onThemeChange() { if (_data) renderTotal(_data); }
