(() => {
  // Live device log (LOG-01): polls new syslog lines every 5 s, filters by severity and text.
  const root = document.querySelector('[data-log-root]');
  if (!root) return;
  const endpoint = root.dataset.endpoint;
  const body = root.querySelector('[data-log-body]');
  const severity = root.querySelector('[data-log-severity]');
  const search = root.querySelector('[data-log-q]');
  const csv = root.querySelector('[data-log-csv]');
  const pauseButton = root.querySelector('[data-log-pause]');
  const liveState = root.querySelector('[data-log-live-state]');
  const moreButton = root.querySelector('[data-log-more]');
  const countLabel = root.querySelector('[data-log-count]');
  const csvBase = csv ? csv.getAttribute('href') : '';
  let latestId = 0, oldestId = 0, rows = 0, paused = false, loading = false, timer = null;

  const fmtTime = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  };
  const sevClass = (value) => value <= 2 ? 'severity-critical' : value === 3 ? 'severity-high' : value === 4 ? 'severity-warning' : 'severity-info';
  const params = (extra) => {
    const query = new URLSearchParams({severity: severity.value, q: search.value.trim(), ...extra});
    return query.toString();
  };
  function rowFor(entry) {
    const tr = document.createElement('tr');
    tr.className = `log-row log-sev-${entry.severity}`;
    const cells = [fmtTime(entry.received_at), null, entry.topics || '', entry.message];
    cells.forEach((text, index) => {
      const td = document.createElement('td');
      if (index === 1) {
        const badge = document.createElement('span');
        badge.className = `severity-badge ${sevClass(entry.severity)}`;
        badge.textContent = entry.severity_label;
        td.appendChild(badge);
      } else {
        td.textContent = text;
      }
      if (index === 3) td.className = 'log-message';
      tr.appendChild(td);
    });
    return tr;
  }
  function setEmpty(text) {
    body.innerHTML = '';
    const tr = document.createElement('tr'); tr.className = 'log-empty';
    const td = document.createElement('td'); td.colSpan = 4; td.textContent = text;
    tr.appendChild(td); body.appendChild(tr);
  }
  function updateCount() { if (countLabel) countLabel.textContent = `${rows} righe`; if (moreButton) moreButton.hidden = rows === 0; }

  async function fetchEntries(extra) {
    const response = await fetch(`${endpoint}?${params(extra)}`, {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function reload() {
    latestId = 0; oldestId = 0; rows = 0;
    if (csv) csv.setAttribute('href', `${csvBase}?${params({})}`);
    try {
      const data = await fetchEntries({});
      body.innerHTML = '';
      data.entries.forEach((entry) => body.appendChild(rowFor(entry)));
      rows = data.entries.length;
      latestId = data.entries.length ? data.entries[0].id : 0;
      oldestId = data.entries.length ? data.entries[data.entries.length - 1].id : 0;
      if (!rows) setEmpty('Nessuna riga di log per questi filtri.');
    } catch (error) {
      setEmpty('Impossibile caricare i log.');
    }
    updateCount();
  }
  async function poll() {
    if (paused || loading || document.hidden) return;
    loading = true;
    try {
      const data = await fetchEntries({after_id: latestId});
      if (data.entries.length) {
        body.querySelector('.log-empty')?.remove();
        [...data.entries].reverse().forEach((entry) => { const tr = rowFor(entry); tr.classList.add('log-new'); body.insertBefore(tr, body.firstChild); });
        latestId = data.entries[0].id;
        if (!oldestId) oldestId = data.entries[data.entries.length - 1].id;
        rows += data.entries.length;
        updateCount();
      }
    } catch (error) { /* keep polling */ } finally { loading = false; }
  }
  moreButton?.addEventListener('click', async () => {
    if (!oldestId) return;
    const data = await fetchEntries({before_id: oldestId});
    data.entries.forEach((entry) => body.appendChild(rowFor(entry)));
    if (data.entries.length) oldestId = data.entries[data.entries.length - 1].id;
    rows += data.entries.length;
    updateCount();
    if (!data.entries.length) moreButton.hidden = true;
  });
  pauseButton?.addEventListener('click', () => {
    paused = !paused;
    pauseButton.textContent = paused ? 'Riprendi' : 'Pausa';
    liveState?.classList.toggle('paused', paused);
    if (liveState) liveState.textContent = paused ? '❚❚ in pausa' : '● live';
  });
  severity.addEventListener('change', reload);
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(reload, 300); });
  root.querySelector('[data-log-filters]')?.addEventListener('submit', (event) => { event.preventDefault(); reload(); });
  reload();
  setInterval(poll, 5000);
})();
