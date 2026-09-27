(() => {
  const root = document.querySelector('[data-dashboard-live]');
  if (!root) return;

  const intervalMs = Number(root.dataset.refreshMs || 30000);
  const endpoint = root.dataset.endpoint || '/api/v1/dashboard/summary';
  const updated = root.querySelector('[data-dashboard-updated]');
  const refreshButton = root.querySelector('[data-dashboard-refresh]');
  const progress = root.querySelector('[data-dashboard-progress]');
  let timer = null;
  let busy = false;

  const formatValue = (value) => value == null ? '—' : String(value);

  function restartProgress() {
    if (!progress) return;
    progress.style.animation = 'none';
    void progress.offsetWidth;
    progress.style.animation = `uiLiveCountdown ${intervalMs}ms linear infinite`;
  }

  function updateStats(payload) {
    Object.entries(payload).forEach(([key, value]) => {
      root.querySelectorAll(`[data-dashboard-stat="${CSS.escape(key)}"]`).forEach((node) => {
        node.textContent = formatValue(value);
      });
    });
    if (updated && payload.updated_at) {
      const date = new Date(payload.updated_at);
      updated.textContent = Number.isNaN(date.getTime()) ? payload.updated_at : date.toLocaleTimeString();
    }
  }

  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    root.classList.add('is-refreshing');
    try {
      const response = await fetch(endpoint, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      updateStats(await response.json());
      root.classList.remove('is-offline');
    } catch (error) {
      root.classList.add('is-offline');
      console.warn('NSM dashboard refresh failed', error);
    } finally {
      busy = false;
      root.classList.remove('is-refreshing');
      restartProgress();
    }
  }

  function schedule() {
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, intervalMs);
    restartProgress();
  }

  refreshButton?.addEventListener('click', refresh);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  schedule();
})();
