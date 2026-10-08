(() => {
  // Generic time-series charts (UBNT-08): one SVG per chart from {charts: [{title, unit, series: [{label, points: [[iso, value]]}]}]}.
  const root = document.querySelector('[data-series-root]');
  if (!root) return;
  const endpoint = root.dataset.endpoint;
  const grid = root.querySelector('[data-series-grid]');
  let range = root.querySelector('[data-series-range].active')?.dataset.seriesRange || '24h';
  let lastData = null;

  const fmt = (value, unit) => {
    if (!Number.isFinite(value)) return '—';
    if (unit === 'bps') {
      for (const [u, size] of [['Gbps', 1e9], ['Mbps', 1e6], ['kbps', 1e3]]) if (Math.abs(value) >= size) return `${(value / size).toFixed(1)} ${u}`;
      return `${Math.round(value)} bps`;
    }
    const digits = Math.abs(value) >= 100 ? 0 : 1;
    return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
  };
  function draw(card, chart) {
    window.NSMChart.render(card.querySelector('.telemetry-svg-wrap'), {
      title: chart.title, unit: chart.unit, min: chart.unit === '%' ? 0 : undefined, max: chart.unit === '%' ? 100 : undefined,
      series: chart.series.map((s, i) => ({label: s.label, color: 'abcd'[i % 4], kind: chart.series.length === 1 && chart.unit !== 'dBm' ? 'area' : 'line', points: s.points})),
      legend: card.querySelector('.rrd-legend'), emptyText: 'Nessun campione nel periodo.',
    });
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
      head.append(title);
      const wrap = document.createElement('div');
      wrap.className = 'telemetry-svg-wrap series-svg-wrap';
      const legend = document.createElement('div');
      legend.className = 'rrd-legend';
      card.append(head, wrap, legend);
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
