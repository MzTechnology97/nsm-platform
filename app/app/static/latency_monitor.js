(() => {
  // ICMP latency and loss from NSM (MON-01), drawn with the shared Cacti-style renderer.
  const root = document.querySelector('[data-latency-root]');
  if (!root) return;
  const endpoint = root.dataset.endpoint;
  const rttWrap = root.querySelector('[data-latency-chart]');
  const lossWrap = root.querySelector('[data-latency-loss]');
  const summary = root.querySelector('[data-latency-summary]');
  let activeRange = '24h';
  let lastData = null;
  let resizeTimer = null;
  const fmt = (value, unit) => (Number.isFinite(value) ? `${value} ${unit}` : '—');

  function render(data) {
    if (!window.NSMChart) return;
    const points = data.points || [];
    const field = (name) => points.map((p) => [p.timestamp, Number.isFinite(p[name]) ? p[name] : null]);
    const empty = 'Nessun campione per questo intervallo: il primo arriva entro 2 minuti dall\'attivazione.';
    window.NSMChart.render(rttWrap, {
      title: 'Round-trip time', unit: 'ms', legend: root.querySelector('[data-latency-legend]'),
      series: [
        {label: 'RTT medio', color: 'b', kind: 'area', points: field('rtt_avg')},
        {label: 'RTT massimo', color: 'a', kind: 'line', points: field('rtt_max')},
        {label: 'RTT minimo', color: 'c', kind: 'line', points: field('rtt_min')},
      ],
      emptyText: empty,
    });
    window.NSMChart.render(lossWrap, {
      title: 'Perdita pacchetti', unit: '%', min: 0, max: 100,
      series: [{label: 'Perdita', color: 'd', kind: 'area', points: field('loss')}],
      emptyText: empty,
    });
    const s = data.stats || {};
    if (summary) summary.textContent = `${data.target || ''} · perdita ${fmt(s.loss, '%')} · RTT medio ${fmt(s.rtt_avg, 'ms')} · max ${fmt(s.rtt_max, 'ms')} · ${data.sample_count || 0} campioni`;
  }

  async function load() {
    try {
      const response = await fetch(`${endpoint}?range=${encodeURIComponent(activeRange)}`, {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      lastData = await response.json();
      render(lastData);
    } catch (error) {
      console.warn('NSM latency load failed', error);
      rttWrap.innerHTML = '<div class="telemetry-empty">Impossibile caricare la latenza.</div>';
    }
  }

  root.querySelectorAll('[data-latency-range]').forEach((button) => button.addEventListener('click', () => {
    activeRange = button.dataset.latencyRange;
    root.querySelectorAll('[data-latency-range]').forEach((b) => b.classList.toggle('active', b === button));
    load();
  }));
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastData) render(lastData); }, 150); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
})();
