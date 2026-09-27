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

  const metricValues = (points, type) => points.map((p) => type === 'cpu' ? p.cpu_load : p.memory_used_percent);

  function renderChart(container, points, type) {
    container.innerHTML = '';
    const values = metricValues(points, type);
    const usable = points.map((point, i) => ({point, value: values[i]})).filter((x) => Number.isFinite(x.value));
    if (!usable.length) {
      container.innerHTML = '<div class="telemetry-empty">Nessun campione disponibile per questo intervallo.</div>';
      return;
    }

    const width = 1000, height = 260, left = 36, right = 10, top = 12, bottom = 26;
    const plotW = width - left - right, plotH = height - top - bottom;
    const min = 0, max = 100;
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.classList.add('telemetry-svg');

    for (const yValue of [0,25,50,75,100]) {
      const y = top + plotH - (yValue-min)/(max-min)*plotH;
      const line = document.createElementNS(ns, 'line');
      line.setAttribute('x1', left); line.setAttribute('x2', width-right); line.setAttribute('y1', y); line.setAttribute('y2', y); line.classList.add('telemetry-gridline'); svg.appendChild(line);
      const text = document.createElementNS(ns, 'text'); text.setAttribute('x', 2); text.setAttribute('y', y+3); text.classList.add('telemetry-axis-label'); text.textContent = `${yValue}%`; svg.appendChild(text);
    }

    const coords = usable.map((item, index) => {
      const x = left + (usable.length === 1 ? plotW/2 : index/(usable.length-1)*plotW);
      const y = top + plotH - (item.value-min)/(max-min)*plotH;
      return {x,y,item};
    });
    const linePoints = coords.map((c) => `${c.x},${c.y}`).join(' ');
    const area = document.createElementNS(ns, 'polygon');
    area.setAttribute('points', `${left},${top+plotH} ${linePoints} ${width-right},${top+plotH}`);
    area.classList.add('telemetry-area'); if (type === 'memory') area.classList.add('memory'); svg.appendChild(area);
    const poly = document.createElementNS(ns, 'polyline'); poly.setAttribute('points', linePoints); poly.classList.add('telemetry-line'); if (type === 'memory') poly.classList.add('memory'); svg.appendChild(poly);

    const first = usable[0].point.timestamp, last = usable[usable.length-1].point.timestamp;
    for (const [x, label, anchor] of [[left,fmtTime(first),'start'],[width-right,fmtTime(last),'end']]) {
      const text = document.createElementNS(ns,'text'); text.setAttribute('x',x); text.setAttribute('y',height-4); text.setAttribute('text-anchor',anchor); text.classList.add('telemetry-axis-label'); text.textContent=label; svg.appendChild(text);
    }

    container.appendChild(svg);
    const latest = usable[usable.length-1].value;
    const valuesOnly = usable.map((u) => u.value);
    const current = root.querySelector(`[data-chart-current="${type}"]`);
    const range = root.querySelector(`[data-chart-range="${type}"]`);
    if (current) current.textContent = `${latest.toFixed(1)}%`;
    if (range) range.textContent = `min ${Math.min(...valuesOnly).toFixed(1)}% · max ${Math.max(...valuesOnly).toFixed(1)}%`;

    svg.addEventListener('mousemove', (event) => {
      const rect = svg.getBoundingClientRect();
      const pos = (event.clientX-rect.left)/rect.width;
      const idx = Math.max(0,Math.min(usable.length-1,Math.round(pos*(usable.length-1))));
      const item = usable[idx];
      let tip = container.querySelector('.telemetry-tooltip');
      if (!tip) { tip = document.createElement('div'); tip.className='telemetry-tooltip'; container.appendChild(tip); }
      tip.textContent = `${fmtTime(item.point.timestamp)} · ${item.value.toFixed(1)}%`;
      tip.style.left = `${Math.max(8,Math.min(92,pos*100))}%`; tip.style.top='52%';
    });
    svg.addEventListener('mouseleave', () => container.querySelector('.telemetry-tooltip')?.remove());
  }

  async function load(range = activeRange) {
    if (loading) return;
    loading = true; root.classList.add('telemetry-loading');
    try {
      const response = await fetch(`${endpoint}?range=${encodeURIComponent(range)}`, {headers:{Accept:'application/json'}, credentials:'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const points = data.points || [];
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
