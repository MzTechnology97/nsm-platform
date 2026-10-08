(() => {
  // MikroTik wireless signal (MON-01) with the shared Cacti-style renderer.
  const root = document.querySelector('[data-wireless-root]');
  if (!root) return;
  const endpoint = root.dataset.endpoint;
  const select = root.querySelector('[data-wireless-interface]');
  const signalWrap = root.querySelector('[data-wireless-signal]');
  const clientsWrap = root.querySelector('[data-wireless-clients]');
  const summary = root.querySelector('[data-wireless-summary]');
  let activeRange = '24h';
  let lastData = null;
  let resizeTimer = null;

  function render(data) {
    if (!window.NSMChart) return;
    const points = data.points || [];
    const field = (name) => points.map((p) => [p.timestamp, Number.isFinite(p[name]) ? p[name] : null]);
    const single = points.every((p) => !Number.isFinite(p.clients) || p.clients <= 1);
    window.NSMChart.render(signalWrap, {
      title: 'Segnale', unit: 'dBm', legend: root.querySelector('[data-wireless-legend]'),
      series: single
        ? [{label: 'Segnale', color: 'b', kind: 'area', points: field('signal_avg')}]
        : [
          {label: 'Segnale medio', color: 'b', kind: 'area', points: field('signal_avg')},
          {label: 'Client migliore', color: 'a', kind: 'line', points: field('signal_max')},
          {label: 'Client peggiore', color: 'c', kind: 'line', points: field('signal_min')},
        ],
      emptyText: 'Nessun campione per questo intervallo.',
    });
    const series = [{label: 'Client collegati', color: 'a', kind: 'line', points: field('clients')}];
    if (points.some((p) => Number.isFinite(p.ccq_avg))) series.push({label: 'CCQ medio %', color: 'd', kind: 'line', points: field('ccq_avg')});
    window.NSMChart.render(clientsWrap, {title: 'Client e CCQ', unit: 'count', legend: root.querySelector('[data-wireless-clients-legend]'), series, emptyText: 'Nessun campione per questo intervallo.'});
    const c = data.current || {};
    if (summary) summary.textContent = Number.isFinite(c.signal_avg) ? `${data.interface} · ora ${c.signal_avg} dBm · ${c.clients} ${c.clients === 1 ? 'peer' : 'client'}` : (data.interface || '');
  }

  async function load() {
    try {
      const url = `${endpoint}?range=${encodeURIComponent(activeRange)}&interface=${encodeURIComponent(select ? select.value : '')}`;
      const response = await fetch(url, {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      lastData = await response.json();
      render(lastData);
    } catch (error) {
      console.warn('NSM wireless load failed', error);
      signalWrap.innerHTML = '<div class="telemetry-empty">Impossibile caricare il segnale.</div>';
    }
  }

  root.querySelectorAll('[data-wireless-range]').forEach((button) => button.addEventListener('click', () => {
    activeRange = button.dataset.wirelessRange;
    root.querySelectorAll('[data-wireless-range]').forEach((b) => b.classList.toggle('active', b === button));
    load();
  }));
  select?.addEventListener('change', load);
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastData) render(lastData); }, 150); });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
})();
