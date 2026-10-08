(() => {
  // Interface traffic graph (MON-01): inbound as a filled area, outbound as a line, 95th percentile dashed (drawn by rrd_chart.js).
  const root = document.querySelector('[data-traffic-root]');
  if (!root) return;

  const endpoint = root.dataset.endpoint;
  const select = root.querySelector('[data-traffic-interface]');
  const wrap = root.querySelector('[data-traffic-chart]');
  let activeRange = '24h';
  let loading = false;

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
  function render(data) {
    const points = data.points || [];
    const p95 = Math.max(data.stats?.rx?.p95 || 0, data.stats?.tx?.p95 || 0);
    window.NSMChart.render(wrap, {
      title: 'Traffico interfaccia', unit: 'bps',
      series: [
        {label: 'In', color: 'b', kind: 'area', points: points.map((p) => [p.timestamp, Number.isFinite(p.rx_bps) ? p.rx_bps : null])},
        {label: 'Out', color: 'a', kind: 'line', points: points.map((p) => [p.timestamp, Number.isFinite(p.tx_bps) ? p.tx_bps : null])},
      ],
      thresholds: p95 > 0 ? [{value: p95, label: `95° ${fmtBps(p95)}`}] : [],
      emptyText: "Nessun campione per questo intervallo: il primo valore arriva al secondo heartbeat dopo l'attivazione.",
    });
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
