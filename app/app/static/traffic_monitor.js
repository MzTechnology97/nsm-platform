(() => {
  // Interface traffic graph (MON-01): inbound as a filled area, outbound as a line, 95th percentile dashed.
  const root = document.querySelector('[data-traffic-root]');
  if (!root) return;

  const endpoint = root.dataset.endpoint;
  const ns = 'http://www.w3.org/2000/svg';
  const select = root.querySelector('[data-traffic-interface]');
  const wrap = root.querySelector('[data-traffic-chart]');
  let activeRange = '24h';
  let loading = false;

  const fmtTime = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
  };
  const fmtBps = (value) => {
    if (!Number.isFinite(value)) return '—';
    for (const [unit, size] of [['Gbit/s', 1e9], ['Mbit/s', 1e6], ['kbit/s', 1e3]]) if (value >= size) return `${(value / size).toFixed(2)} ${unit}`;
    return `${Math.round(value)} bit/s`;
  };
  const fmtBytes = (value) => {
    if (!Number.isFinite(value) || value <= 0) return '—';
    for (const [unit, size] of [['TB', 1e12], ['GB', 1e9], ['MB', 1e6], ['kB', 1e3]]) if (value >= size) return `${(value / size).toFixed(2)} ${unit}`;
    return `${value} B`;
  };
  const niceMax = (value) => {
    if (!(value > 0)) return 1000;
    const exp = Math.pow(10, Math.floor(Math.log10(value)));
    for (const step of [1, 2, 2.5, 5, 10]) if (value <= step * exp) return step * exp;
    return 10 * exp;
  };
  const el = (name, attrs, cls) => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    if (cls) node.classList.add(cls);
    return node;
  };

  function render(data) {
    wrap.innerHTML = '';
    const points = (data.points || []).filter((p) => Number.isFinite(p.rx_bps) || Number.isFinite(p.tx_bps));
    if (!points.length) {
      wrap.innerHTML = '<div class="telemetry-empty">Nessun campione per questo intervallo: il primo valore arriva al secondo heartbeat dopo l\'attivazione.</div>';
      return;
    }
    // Size the viewBox to the container so labels keep their real font size on phones too.
    const width = Math.max(320, Math.round(wrap.clientWidth - 20)), height = Math.max(180, Math.round(wrap.clientHeight - 20));
    const left = 74, right = 10, top = 12, bottom = 22;
    const plotW = width - left - right, plotH = height - top - bottom;
    const peak = Math.max(...points.map((p) => Math.max(p.rx_bps || 0, p.tx_bps || 0)));
    const max = niceMax(peak);
    const t0 = new Date(points[0].timestamp).getTime();
    const t1 = new Date(points[points.length - 1].timestamp).getTime();
    const xOf = (p, i) => left + (t1 > t0 ? (new Date(p.timestamp).getTime() - t0) / (t1 - t0) : (points.length === 1 ? 0.5 : i / (points.length - 1))) * plotW;
    const yOf = (v) => top + plotH - (Math.min(v, max) / max) * plotH;
    const svg = el('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Grafico traffico interfaccia'}, 'telemetry-svg');

    for (let i = 0; i <= 4; i += 1) {
      const value = max * i / 4, y = yOf(value);
      svg.appendChild(el('line', {x1: left, x2: width - right, y1: y, y2: y}, 'telemetry-gridline'));
      const label = el('text', {x: left - 6, y: y + 3, 'text-anchor': 'end'}, 'telemetry-axis-label');
      label.textContent = fmtBps(value);
      svg.appendChild(label);
    }

    // Split series at missing samples so outages show as gaps, not as interpolated traffic.
    const segments = (key) => {
      const result = [];
      let current = [];
      points.forEach((p, i) => {
        if (Number.isFinite(p[key])) current.push([xOf(p, i), yOf(p[key])]);
        else if (current.length) { result.push(current); current = []; }
      });
      if (current.length) result.push(current);
      return result;
    };
    for (const seg of segments('rx_bps')) {
      const line = seg.map(([x, y]) => `${x},${y}`).join(' ');
      svg.appendChild(el('polygon', {points: `${seg[0][0]},${top + plotH} ${line} ${seg[seg.length - 1][0]},${top + plotH}`}, 'traffic-in-area'));
    }
    for (const seg of segments('tx_bps')) svg.appendChild(el('polyline', {points: seg.map(([x, y]) => `${x},${y}`).join(' ')}, 'traffic-out-line'));

    const p95 = Math.max(data.stats?.rx?.p95 || 0, data.stats?.tx?.p95 || 0);
    if (p95 > 0) {
      const y = yOf(p95);
      svg.appendChild(el('line', {x1: left, x2: width - right, y1: y, y2: y}, 'traffic-p95'));
      const label = el('text', {x: width - right - 4, y: y - 4, 'text-anchor': 'end'}, 'traffic-p95-label');
      label.textContent = `95° ${fmtBps(p95)}`;
      svg.appendChild(label);
    }
    // Time axis: evenly spaced ticks (more on wide screens), first/last anchored to the edges.
    const ticks = t1 > t0 ? (width >= 720 ? 6 : 3) : 1;
    for (let i = 0; i < ticks; i += 1) {
      const fraction = ticks === 1 ? 0 : i / (ticks - 1);
      const x = left + fraction * plotW;
      if (i > 0 && i < ticks - 1) svg.appendChild(el('line', {x1: x, x2: x, y1: top, y2: top + plotH}, 'telemetry-gridline'));
      const anchor = i === 0 ? 'start' : (i === ticks - 1 ? 'end' : 'middle');
      const label = el('text', {x, y: height - 4, 'text-anchor': anchor}, 'telemetry-axis-label');
      label.textContent = fmtTime(new Date(t0 + fraction * (t1 - t0)).toISOString());
      svg.appendChild(label);
    }
    const cursor = el('line', {x1: 0, x2: 0, y1: top, y2: top + plotH, visibility: 'hidden'}, 'traffic-cursor');
    svg.appendChild(cursor);
    wrap.appendChild(svg);

    svg.addEventListener('mousemove', (event) => {
      const rect = svg.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width * width;
      let best = 0;
      points.forEach((p, i) => { if (Math.abs(xOf(p, i) - x) < Math.abs(xOf(points[best], best) - x)) best = i; });
      const p = points[best];
      cursor.setAttribute('x1', xOf(p, best)); cursor.setAttribute('x2', xOf(p, best)); cursor.setAttribute('visibility', 'visible');
      let tip = wrap.querySelector('.telemetry-tooltip');
      if (!tip) { tip = document.createElement('div'); tip.className = 'telemetry-tooltip'; wrap.appendChild(tip); }
      tip.textContent = `${fmtTime(p.timestamp)} · ↓ ${fmtBps(p.rx_bps)} · ↑ ${fmtBps(p.tx_bps)}`;
      tip.style.left = `${Math.max(12, Math.min(88, xOf(p, best) / width * 100))}%`;
      tip.style.top = '18%';
    });
    svg.addEventListener('mouseleave', () => { cursor.setAttribute('visibility', 'hidden'); wrap.querySelector('.telemetry-tooltip')?.remove(); });
  }

  let lastData = null, resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastData) render(lastData); }, 150); });

  function fillStats(stats) {
    root.querySelectorAll('[data-traffic-stat]').forEach((cell) => {
      const [dir, key] = cell.dataset.trafficStat.split('.');
      const value = stats?.[dir]?.[key];
      cell.textContent = key === 'bytes' ? fmtBytes(value) : fmtBps(value);
    });
  }

  async function load() {
    if (!select || !select.value) { wrap.innerHTML = '<div class="telemetry-empty">Nessuna interfaccia monitorata: selezionale qui sotto.</div>'; return; }
    if (loading) return;
    loading = true; root.classList.add('telemetry-loading');
    try {
      const url = `${endpoint}?range=${encodeURIComponent(activeRange)}&interface=${encodeURIComponent(select.value)}`;
      const response = await fetch(url, {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      lastData = data;
      render(data);
      fillStats(data.stats);
      const title = root.querySelector('[data-traffic-title]');
      if (title) title.textContent = `${data.interface || ''} · ${data.sample_count || 0} campioni`;
      const samples = root.querySelector('[data-traffic-samples]');
      if (samples) samples.textContent = `${data.sample_count || 0} campioni · ${data.points?.length || 0} punti`;
    } catch (error) {
      console.warn('NSM traffic load failed', error);
      wrap.innerHTML = '<div class="telemetry-empty">Impossibile caricare il traffico.</div>';
    } finally { loading = false; root.classList.remove('telemetry-loading'); }
  }

  root.querySelectorAll('[data-traffic-range]').forEach((button) => button.addEventListener('click', () => {
    activeRange = button.dataset.trafficRange;
    root.querySelectorAll('[data-traffic-range]').forEach((b) => b.classList.toggle('active', b === button));
    load();
  }));
  select?.addEventListener('change', load);
  root.querySelector('[data-traffic-refresh]')?.addEventListener('click', load);
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
})();
