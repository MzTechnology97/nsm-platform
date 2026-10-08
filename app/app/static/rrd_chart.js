(() => {
  // Shared Cacti/Zabbix-style time-series renderer (MON-01): real time axis with round ticks,
  // gaps where samples are missing, readable labels, crosshair tooltip and a
  // Current / Min / Avg / Max legend under the graph.
  //
  // NSMChart.render(wrap, {
  //   series: [{label, color: 'a'|'b'|'c'|'d', kind: 'area'|'line', points: [[timeMs|iso, value|null], ...]}],
  //   unit: 'bps' | '%' | 'dBm' | 'ms' | '' , min, max (optional fixed scale),
  //   thresholds: [{value, label}], legend: element for the legend table (optional),
  //   emptyText,
  // })
  const ns = 'http://www.w3.org/2000/svg';
  const MINUTE = 60000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
  const STEPS = [5 * MINUTE, 10 * MINUTE, 15 * MINUTE, 30 * MINUTE, HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY];

  const el = (name, attrs, cls) => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    if (cls) node.classList.add(...cls.split(' '));
    return node;
  };
  const toMs = (t) => (typeof t === 'number' ? t : new Date(t).getTime());
  const pad = (n) => String(n).padStart(2, '0');

  function fmtValue(value, unit) {
    if (!Number.isFinite(value)) return '—';
    if (unit === 'bps') {
      for (const [u, size] of [['Gbit/s', 1e9], ['Mbit/s', 1e6], ['kbit/s', 1e3]]) if (Math.abs(value) >= size) return `${(value / size).toFixed(2)} ${u}`;
      return `${Math.round(value)} bit/s`;
    }
    if (unit === 'bytes') {
      for (const [u, size] of [['TB', 1e12], ['GB', 1e9], ['MB', 1e6], ['kB', 1e3]]) if (Math.abs(value) >= size) return `${(value / size).toFixed(2)} ${u}`;
      return `${Math.round(value)} B`;
    }
    if (unit === '%') return `${value.toFixed(1)}%`;
    if (unit === 'count') return `${Math.round(value)}`;
    const digits = Math.abs(value) >= 100 ? 0 : 1;
    return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
  }

  function fmtTick(ms, step) {
    const d = new Date(ms);
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const dm = `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
    if (step >= DAY) return dm;
    if (d.getHours() === 0 && d.getMinutes() === 0) return dm;
    return hm;
  }
  const fmtFull = (ms) => {
    const d = new Date(ms);
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  function niceScale(lo, hi, count) {
    const span = hi - lo || Math.abs(hi) || 1;
    const raw = span / count;
    const exp = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * exp).find((s) => s >= raw) || 10 * exp;
    return {lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step};
  }

  // Split a series where the time between two samples is much larger than usual.
  function segments(points) {
    const valid = points.filter((p) => p[0] !== null);
    const deltas = [];
    for (let i = 1; i < valid.length; i += 1) deltas.push(valid[i][0] - valid[i - 1][0]);
    const sorted = deltas.filter((d) => d > 0).sort((a, b) => a - b);
    const typical = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
    const out = [];
    let current = [];
    valid.forEach((p, i) => {
      const gap = i > 0 && typical > 0 && p[0] - valid[i - 1][0] > Math.max(typical * 3, 15 * MINUTE);
      if (!Number.isFinite(p[1]) || gap) { if (current.length) out.push(current); current = []; }
      if (Number.isFinite(p[1])) current.push(p);
    });
    if (current.length) out.push(current);
    return out;
  }

  function stats(points) {
    const values = points.map((p) => p[1]).filter(Number.isFinite);
    if (!values.length) return {current: null, min: null, avg: null, max: null};
    const last = [...points].reverse().find((p) => Number.isFinite(p[1]));
    return {current: last ? last[1] : null, min: Math.min(...values), avg: values.reduce((a, b) => a + b, 0) / values.length, max: Math.max(...values)};
  }

  function renderLegend(target, series, unit) {
    if (!target) return;
    target.innerHTML = '';
    const table = document.createElement('table');
    table.className = 'rrd-legend-table';
    const head = document.createElement('tr');
    for (const label of ['', 'Attuale', 'Min', 'Media', 'Max']) { const th = document.createElement('th'); th.textContent = label; head.appendChild(th); }
    table.appendChild(head);
    series.forEach((s) => {
      const st = stats(s.points);
      const row = document.createElement('tr');
      const name = document.createElement('td');
      const key = document.createElement('span');
      key.className = `rrd-key rrd-${s.color || 'a'}`;
      key.textContent = s.label;
      name.appendChild(key);
      row.appendChild(name);
      for (const value of [st.current, st.min, st.avg, st.max]) { const td = document.createElement('td'); td.textContent = fmtValue(value, unit); row.appendChild(td); }
      table.appendChild(row);
    });
    target.appendChild(table);
  }

  function render(wrap, opts) {
    const unit = opts.unit || '';
    const series = (opts.series || []).map((s, i) => ({...s, color: s.color || 'abcd'[i % 4], points: (s.points || []).map((p) => [toMs(p[0]), p[1] === null || p[1] === undefined ? null : Number(p[1])]).filter((p) => Number.isFinite(p[0])).sort((a, b) => a[0] - b[0])}));
    wrap.innerHTML = '';
    const values = series.flatMap((s) => s.points.map((p) => p[1]).filter(Number.isFinite));
    renderLegend(opts.legend, series, unit);
    if (!values.length) {
      const empty = document.createElement('div');
      empty.className = 'telemetry-empty';
      empty.textContent = opts.emptyText || 'Nessun campione per questo intervallo.';
      wrap.appendChild(empty);
      return;
    }
    const width = Math.max(300, Math.round(wrap.clientWidth - 16));
    const height = Math.max(170, Math.round(wrap.clientHeight - 16));
    const times = series.flatMap((s) => s.points.map((p) => p[0]));
    let t0 = Math.min(...times), t1 = Math.max(...times);
    if (opts.from && opts.to) { t0 = Math.min(t0, toMs(opts.from)); t1 = Math.max(t1, toMs(opts.to)); }
    if (t1 === t0) { t0 -= 30 * MINUTE; t1 += 30 * MINUTE; }

    let lo = Number.isFinite(opts.min) ? opts.min : Math.min(...values);
    let hi = Number.isFinite(opts.max) ? Math.max(opts.max, Math.max(...values)) : Math.max(...values);
    for (const t of opts.thresholds || []) if (Number.isFinite(t.value)) hi = Math.max(hi, t.value);
    if (!Number.isFinite(opts.min) && (unit === 'bps' || unit === 'bytes' || unit === '%' || unit === 'ms' || unit === 'count' || lo >= 0)) lo = 0;
    const scale = niceScale(lo, hi === lo ? lo + 1 : hi, 4);
    if (unit === 'count' && scale.step < 1) { scale.step = 1; scale.hi = Math.max(scale.hi, Math.ceil(hi)); }
    if (Number.isFinite(opts.max) && unit === '%') scale.hi = Math.max(100, scale.hi);
    lo = scale.lo; hi = scale.hi;

    const labelWidth = Math.max(...[lo, hi, (lo + hi) / 2].map((v) => fmtValue(v, unit).length)) * 6.6 + 12;
    const left = Math.max(44, Math.round(labelWidth)), right = 12, top = 10, bottom = 24;
    const plotW = width - left - right, plotH = height - top - bottom;
    const x = (t) => left + (t - t0) / (t1 - t0) * plotW;
    const y = (v) => top + plotH - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * plotH;
    const svg = el('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': opts.title || 'Grafico'}, 'telemetry-svg rrd-svg');
    svg.appendChild(el('rect', {x: left, y: top, width: plotW, height: plotH}, 'rrd-plot'));

    for (let v = lo; v <= hi + scale.step / 2; v += scale.step) {
      const yy = y(v);
      svg.appendChild(el('line', {x1: left, x2: left + plotW, y1: yy, y2: yy}, 'rrd-grid'));
      const label = el('text', {x: left - 6, y: yy + 3.5, 'text-anchor': 'end'}, 'rrd-label');
      label.textContent = fmtValue(v, unit);
      svg.appendChild(label);
    }
    const maxTicks = Math.max(3, Math.floor(plotW / 90));
    const step = STEPS.find((s) => (t1 - t0) / s <= maxTicks) || STEPS[STEPS.length - 1];
    const offset = new Date().getTimezoneOffset() * MINUTE;
    let tick = Math.ceil((t0 - offset) / step) * step + offset;
    for (; tick <= t1; tick += step) {
      const xx = x(tick);
      svg.appendChild(el('line', {x1: xx, x2: xx, y1: top, y2: top + plotH}, 'rrd-grid rrd-grid-v'));
      const label = el('text', {x: xx, y: height - 6, 'text-anchor': 'middle'}, 'rrd-label');
      label.textContent = fmtTick(tick, step);
      svg.appendChild(label);
    }

    series.forEach((s) => {
      for (const seg of segments(s.points)) {
        const line = seg.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ');
        if (s.kind === 'area') {
          const base = lo < 0 && hi > 0 ? y(0) : y(lo);  // all-negative series (dBm) fill from the bottom
          const first = x(seg[0][0]).toFixed(1), last = x(seg[seg.length - 1][0]).toFixed(1);
          svg.appendChild(el('polygon', {points: `${first},${base} ${line} ${last},${base}`}, `rrd-area rrd-${s.color}`));
        }
        if (seg.length === 1) svg.appendChild(el('circle', {cx: x(seg[0][0]), cy: y(seg[0][1]), r: 2}, `rrd-dot rrd-${s.color}`));
        else svg.appendChild(el('polyline', {points: line}, `rrd-line rrd-${s.color}`));
      }
    });
    for (const t of opts.thresholds || []) {
      if (!Number.isFinite(t.value) || t.value < lo || t.value > hi) continue;
      const yy = y(t.value);
      svg.appendChild(el('line', {x1: left, x2: left + plotW, y1: yy, y2: yy}, 'rrd-threshold'));
      const label = el('text', {x: left + plotW - 4, y: yy - 4, 'text-anchor': 'end'}, 'rrd-threshold-label');
      label.textContent = t.label || fmtValue(t.value, unit);
      svg.appendChild(label);
    }
    svg.appendChild(el('rect', {x: left, y: top, width: plotW, height: plotH}, 'rrd-frame'));
    const cursor = el('line', {x1: 0, x2: 0, y1: top, y2: top + plotH, visibility: 'hidden'}, 'rrd-cursor');
    svg.appendChild(cursor);
    wrap.appendChild(svg);

    const nearest = (pts, t) => {
      let best = null;
      for (const p of pts) if (best === null || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      return best;
    };
    svg.addEventListener('mousemove', (event) => {
      const rect = svg.getBoundingClientRect();
      const px = (event.clientX - rect.left) / rect.width * width;
      if (px < left || px > left + plotW) return;
      const t = t0 + (px - left) / plotW * (t1 - t0);
      const anchor = nearest(series.flatMap((s) => s.points), t);
      if (!anchor) return;
      cursor.setAttribute('x1', x(anchor[0])); cursor.setAttribute('x2', x(anchor[0])); cursor.setAttribute('visibility', 'visible');
      let tip = wrap.querySelector('.telemetry-tooltip');
      if (!tip) { tip = document.createElement('div'); tip.className = 'telemetry-tooltip'; wrap.appendChild(tip); }
      const parts = series.map((s) => { const p = nearest(s.points, anchor[0]); return `${s.label} ${fmtValue(p && Math.abs(p[0] - anchor[0]) < 15 * MINUTE ? p[1] : null, unit)}`; });
      tip.textContent = `${fmtFull(anchor[0])} · ${parts.join(' · ')}`;
      tip.style.left = `${Math.max(14, Math.min(86, x(anchor[0]) / width * 100))}%`;
      tip.style.top = '16%';
    });
    svg.addEventListener('mouseleave', () => { cursor.setAttribute('visibility', 'hidden'); wrap.querySelector('.telemetry-tooltip')?.remove(); });
  }

  window.NSMChart = {render, fmtValue, stats};
})();
