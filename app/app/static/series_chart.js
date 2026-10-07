(() => {
  // Generic time-series charts (UBNT-08): one SVG per chart from {charts: [{title, unit, series: [{label, points: [[iso, value]]}]}]}.
  const root = document.querySelector('[data-series-root]');
  if (!root) return;
  const endpoint = root.dataset.endpoint;
  const grid = root.querySelector('[data-series-grid]');
  const ns = 'http://www.w3.org/2000/svg';
  const COLORS = ['series-a', 'series-b', 'series-c'];
  let range = root.querySelector('[data-series-range].active')?.dataset.seriesRange || '24h';
  let lastData = null;

  const fmtTime = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
  };
  const fmt = (value, unit) => {
    if (!Number.isFinite(value)) return '—';
    if (unit === 'bps') {
      for (const [u, size] of [['Gbps', 1e9], ['Mbps', 1e6], ['kbps', 1e3]]) if (Math.abs(value) >= size) return `${(value / size).toFixed(1)} ${u}`;
      return `${Math.round(value)} bps`;
    }
    const digits = Math.abs(value) >= 100 ? 0 : 1;
    return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
  };
  const el = (name, attrs, cls) => {
    const node = document.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (cls) node.classList.add(...cls.split(' '));
    return node;
  };
  const bounds = (values, unit) => {
    let lo = Math.min(...values), hi = Math.max(...values);
    if (unit === '%') { lo = 0; hi = Math.max(100, hi); }
    else if (unit === 'bps' || unit === '') { lo = 0; }
    if (hi === lo) { hi += 1; lo -= unit === 'dBm' ? 1 : 0; }
    const pad = (hi - lo) * 0.08;
    return unit === 'dBm' ? [Math.floor(lo - pad), Math.ceil(hi + pad)] : [lo, hi + pad];
  };

  function draw(card, chart) {
    const wrap = card.querySelector('.telemetry-svg-wrap');
    wrap.innerHTML = '';
    const values = chart.series.flatMap((s) => s.points.map((p) => p[1]).filter(Number.isFinite));
    if (!values.length) { wrap.innerHTML = '<div class="telemetry-empty">Nessun campione nel periodo.</div>'; return; }
    const width = Math.max(300, Math.round(wrap.clientWidth - 20)), height = Math.max(160, Math.round(wrap.clientHeight - 20));
    const left = 70, right = 10, top = 10, bottom = 22, plotW = width - left - right, plotH = height - top - bottom;
    const times = chart.series.flatMap((s) => s.points.map((p) => new Date(p[0]).getTime()));
    const t0 = Math.min(...times), t1 = Math.max(...times);
    const [lo, hi] = bounds(values, chart.unit);
    const x = (iso) => left + (t1 > t0 ? (new Date(iso).getTime() - t0) / (t1 - t0) : 0.5) * plotW;
    const y = (v) => top + plotH - ((v - lo) / (hi - lo)) * plotH;
    const svg = el('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': chart.title}, 'telemetry-svg');
    for (let i = 0; i <= 4; i += 1) {
      const v = lo + (hi - lo) * i / 4, yy = y(v);
      svg.appendChild(el('line', {x1: left, x2: width - right, y1: yy, y2: yy}, 'telemetry-gridline'));
      const label = el('text', {x: left - 6, y: yy + 3, 'text-anchor': 'end'}, 'telemetry-axis-label');
      label.textContent = fmt(v, chart.unit);
      svg.appendChild(label);
    }
    chart.series.forEach((series, index) => {
      let segment = [];
      const flush = () => { if (segment.length) svg.appendChild(el('polyline', {points: segment.join(' ')}, `series-line ${COLORS[index % COLORS.length]}`)); segment = []; };
      series.points.forEach(([t, v]) => { if (Number.isFinite(v)) segment.push(`${x(t)},${y(v)}`); else flush(); });
      flush();
    });
    for (const [iso, anchor, xx] of [[new Date(t0).toISOString(), 'start', left], [new Date(t1).toISOString(), 'end', width - right]]) {
      const label = el('text', {x: xx, y: height - 4, 'text-anchor': anchor}, 'telemetry-axis-label');
      label.textContent = fmtTime(iso);
      svg.appendChild(label);
    }
    wrap.appendChild(svg);
    const first = chart.series[0].points;
    svg.addEventListener('mousemove', (event) => {
      const rect = svg.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width * width;
      let best = 0;
      first.forEach((p, i) => { if (Math.abs(x(p[0]) - px) < Math.abs(x(first[best][0]) - px)) best = i; });
      let tip = wrap.querySelector('.telemetry-tooltip');
      if (!tip) { tip = document.createElement('div'); tip.className = 'telemetry-tooltip'; wrap.appendChild(tip); }
      tip.textContent = `${fmtTime(first[best][0])} · ` + chart.series.map((s) => `${s.label} ${fmt(s.points[best]?.[1], chart.unit)}`).join(' · ');
      tip.style.left = `${Math.max(14, Math.min(86, x(first[best][0]) / width * 100))}%`;
      tip.style.top = '14%';
    });
    svg.addEventListener('mouseleave', () => wrap.querySelector('.telemetry-tooltip')?.remove());
  }

  function render(data) {
    grid.innerHTML = '';
    if (!data.charts.length) { grid.innerHTML = '<div class="telemetry-empty">Nessun campione UISP nel periodo selezionato.</div>'; return; }
    data.charts.forEach((chart) => {
      const card = document.createElement('article');
      card.className = 'telemetry-chart';
      const head = document.createElement('div');
      head.className = 'telemetry-chart-head';
      const title = document.createElement('div');
      const name = document.createElement('span'); name.textContent = chart.title.toUpperCase();
      const current = document.createElement('strong');
      const last = (s) => [...s.points].reverse().find((p) => Number.isFinite(p[1]))?.[1];
      current.textContent = chart.series.map((s) => fmt(last(s), chart.unit)).join(' / ');
      title.append(name, current);
      const legend = document.createElement('small');
      legend.className = 'series-legend';
      chart.series.forEach((s, i) => { const key = document.createElement('span'); key.className = `series-key ${COLORS[i % COLORS.length]}`; key.textContent = s.label; legend.appendChild(key); });
      head.append(title, legend);
      const wrap = document.createElement('div');
      wrap.className = 'telemetry-svg-wrap series-svg-wrap';
      card.append(head, wrap);
      grid.appendChild(card);
      draw(card, chart);
    });
    const count = root.querySelector('[data-series-count]');
    if (count) count.textContent = `${data.sample_count} campioni`;
  }

  async function load() {
    root.classList.add('telemetry-loading');
    try {
      const response = await fetch(`${endpoint}?range=${encodeURIComponent(range)}`, {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      lastData = await response.json();
      render(lastData);
    } catch (error) {
      grid.innerHTML = '<div class="telemetry-empty">Impossibile caricare i grafici.</div>';
    } finally { root.classList.remove('telemetry-loading'); }
  }
  root.querySelectorAll('[data-series-range]').forEach((button) => button.addEventListener('click', () => {
    range = button.dataset.seriesRange;
    root.querySelectorAll('[data-series-range]').forEach((b) => b.classList.toggle('active', b === button));
    load();
  }));
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastData) render(lastData); }, 150); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 300000);
})();
