(() => {
  const root = document.querySelector('[data-telemetry-root]');
  if (!root) return;

  const endpoint = root.dataset.endpoint;
  const ns = 'http://www.w3.org/2000/svg';
  let activeRange = '24h';
  let loading = false;

  const fmtTime = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
  };

  const fields = {cpu: ['cpu_load', 'CPU', 'a'], memory: ['memory_used_percent', 'Memoria', 'b']};

  function renderChart(container, points, type) {
    const [field, label, color] = fields[type];
    const series = [{label, color, kind: 'area', points: points.map((p) => [p.timestamp, Number.isFinite(p[field]) ? p[field] : null])}];
    window.NSMChart.render(container, {series, unit: '%', min: 0, max: 100, title: label, legend: root.querySelector(`[data-chart-legend="${type}"]`),
                                       emptyText: 'Nessun campione disponibile per questo intervallo.'});
    const st = window.NSMChart.stats(series[0].points.map((p) => [0, p[1]]));
    const current = root.querySelector(`[data-chart-current="${type}"]`);
    const range = root.querySelector(`[data-chart-range="${type}"]`);
    if (current) current.textContent = Number.isFinite(st.current) ? `${st.current.toFixed(1)}%` : '—';
    if (range) range.textContent = Number.isFinite(st.max) ? `media ${st.avg.toFixed(1)}% · max ${st.max.toFixed(1)}%` : '—';
  }

  let lastPoints = null, resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (lastPoints) { renderChart(root.querySelector('[data-chart="cpu"]'), lastPoints, 'cpu'); renderChart(root.querySelector('[data-chart="memory"]'), lastPoints, 'memory'); } }, 150); });

  async function load(range = activeRange) {
    if (loading) return;
    loading = true; root.classList.add('telemetry-loading');
    try {
      const response = await fetch(`${endpoint}?range=${encodeURIComponent(range)}`, {headers:{Accept:'application/json'}, credentials:'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const points = data.points || [];
      lastPoints = points;
      renderChart(root.querySelector('[data-chart="cpu"]'), points, 'cpu');
      renderChart(root.querySelector('[data-chart="memory"]'), points, 'memory');
      const samples = root.querySelector('[data-telemetry-samples]'); if (samples) samples.textContent = `${data.sample_count || 0} campioni`;
      const updated = root.querySelector('[data-telemetry-updated]'); if (updated) updated.textContent = points.length ? fmtTime(points[points.length-1].timestamp) : '—';
      if (points.length) {
        const last = points[points.length-1];
        const cpu = document.querySelector('[data-live-cpu]'); if (cpu && Number.isFinite(last.cpu_load)) cpu.textContent=`${Number(last.cpu_load).toFixed(1)}%`;
        const mem = document.querySelector('[data-live-memory]'); if (mem && Number.isFinite(last.memory_used_percent)) mem.textContent=`${Number(last.memory_used_percent).toFixed(1)}%`;
        const uptime = document.querySelector('[data-live-uptime]'); if (uptime && last.uptime) uptime.textContent=last.uptime;
      }
    } catch (error) {
      console.warn('NSM telemetry load failed', error);
      root.querySelectorAll('[data-chart]').forEach((node) => node.innerHTML='<div class="telemetry-empty">Impossibile caricare la telemetria.</div>');
    } finally { loading=false; root.classList.remove('telemetry-loading'); }
  }

  root.querySelectorAll('[data-range]').forEach((button) => button.addEventListener('click', () => {
    activeRange=button.dataset.range;
    root.querySelectorAll('[data-range]').forEach((b)=>b.classList.toggle('active',b===button));
    load(activeRange);
  }));
  root.querySelector('[data-telemetry-refresh]')?.addEventListener('click',()=>load(activeRange));
  load();
  setInterval(()=>{ if(!document.hidden) load(activeRange); }, 60000);
})();
