(function () {
  if (!document.querySelector('link[data-runtime-branding]')) {
    const brandingCss = document.createElement('link');
    brandingCss.rel = 'stylesheet';
    brandingCss.href = '/branding/theme.css?v=' + Date.now();
    brandingCss.dataset.runtimeBranding = '1';
    document.head.appendChild(brandingCss);
  }

  const shell = document.querySelector('[data-app-shell]');
  if (!shell) return;

  const storageKey = 'nsm-sidebar-collapsed';
  const desktop = () => window.matchMedia('(min-width: 901px)').matches;
  function applyDesktopPreference() {
    if (!desktop()) { shell.classList.remove('sidebar-collapsed'); return; }
    let collapsed = false;
    try { collapsed = localStorage.getItem(storageKey) === '1'; } catch (e) {}
    shell.classList.toggle('sidebar-collapsed', collapsed);
  }
  function closeMobileSidebar() { shell.classList.remove('sidebar-open'); document.body.classList.remove('sidebar-lock'); }
  function openMobileSidebar() { shell.classList.add('sidebar-open'); document.body.classList.add('sidebar-lock'); }
  document.querySelectorAll('[data-sidebar-open]').forEach((button) => button.addEventListener('click', openMobileSidebar));
  document.querySelectorAll('[data-sidebar-close]').forEach((button) => button.addEventListener('click', closeMobileSidebar));
  document.querySelectorAll('[data-sidebar-collapse]').forEach((button) => button.addEventListener('click', () => {
    if (!desktop()) return;
    const collapsed = !shell.classList.contains('sidebar-collapsed');
    shell.classList.toggle('sidebar-collapsed', collapsed);
    try { localStorage.setItem(storageKey, collapsed ? '1' : '0'); } catch (e) {}
  }));
  document.querySelectorAll('.sidebar-nav a').forEach((link) => link.addEventListener('click', () => { if (!desktop()) closeMobileSidebar(); }));

  const searchWrap = document.querySelector('[data-global-search-wrap]');
  const searchInput = document.querySelector('[data-global-search-input]');
  const suggestions = document.querySelector('[data-search-suggestions]');
  let searchTimer = null, searchAbort = null, activeIndex = -1;

  function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char])); }
  function resultIcon(type) { if (type === 'Cliente') return '◎'; if (type === 'Sede') return '⌖'; return '▣'; }
  function closeSuggestions() { if (!suggestions) return; suggestions.classList.remove('open'); suggestions.innerHTML = ''; activeIndex = -1; }
  function suggestionItems() { return suggestions ? Array.from(suggestions.querySelectorAll('.search-suggestion')) : []; }
  function setActive(index) {
    const items = suggestionItems();
    if (!items.length) { activeIndex = -1; return; }
    activeIndex = Math.max(0, Math.min(index, items.length - 1));
    items.forEach((item, i) => item.classList.toggle('active', i === activeIndex));
    items[activeIndex].scrollIntoView({block: 'nearest'});
  }
  function renderGroup(type, rows) {
    if (!rows.length) return '';
    return `<div class="search-suggestions-header">${escapeHtml(type)}</div>` + rows.map((item) => `
      <a class="search-suggestion" href="${escapeHtml(item.url)}" role="option">
        <span class="result-icon">${resultIcon(item.type)}</span>
        <span class="search-suggestion-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.subtitle)}</small></span>
        <span class="chevron">›</span>
      </a>`).join('');
  }
  function renderSuggestions(payload, query) {
    if (!suggestions) return;
    const rows = payload.results || [];
    if (!rows.length) {
      suggestions.innerHTML = `<div class="search-suggestions-empty">Nessun risultato per <strong>${escapeHtml(query)}</strong></div><div class="search-suggestions-footer">Invio per la ricerca completa</div>`;
      suggestions.classList.add('open');
      return;
    }
    const order = ['Apparato', 'Cliente', 'Sede'];
    suggestions.innerHTML = order.map((type) => renderGroup(type, rows.filter((item) => item.type === type))).join('') + `<div class="search-suggestions-footer">↑ ↓ naviga · Invio apre · Esc chiude</div>`;
    suggestions.classList.add('open');
    activeIndex = -1;
  }
  async function fetchSuggestions() {
    if (!searchInput || !suggestions) return;
    const query = searchInput.value.trim();
    if (query.length < 2) { closeSuggestions(); return; }
    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();
    suggestions.innerHTML = '<div class="search-suggestions-empty">Ricerca in corso…</div>';
    suggestions.classList.add('open');
    try {
      const response = await fetch('/api/v1/search/suggest?q=' + encodeURIComponent(query), {signal: searchAbort.signal, headers: {'Accept': 'application/json'}});
      if (!response.ok) throw new Error('search failed');
      renderSuggestions(await response.json(), query);
    } catch (error) {
      if (error.name === 'AbortError') return;
      suggestions.innerHTML = '<div class="search-suggestions-empty">Ricerca temporaneamente non disponibile.</div>';
      suggestions.classList.add('open');
    }
  }
  if (searchInput && suggestions) {
    searchInput.setAttribute('autocomplete', 'off');
    searchInput.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(fetchSuggestions, 160); });
    searchInput.addEventListener('focus', () => { if (searchInput.value.trim().length >= 2) fetchSuggestions(); });
    searchInput.addEventListener('keydown', (event) => {
      const items = suggestionItems();
      if (event.key === 'ArrowDown' && items.length) { event.preventDefault(); setActive(activeIndex + 1); }
      else if (event.key === 'ArrowUp' && items.length) { event.preventDefault(); setActive(activeIndex <= 0 ? items.length - 1 : activeIndex - 1); }
      else if (event.key === 'Enter' && activeIndex >= 0 && items[activeIndex]) { event.preventDefault(); window.location.href = items[activeIndex].href; }
      else if (event.key === 'Escape') closeSuggestions();
    });
    document.addEventListener('click', (event) => { if (searchWrap && !searchWrap.contains(event.target)) closeSuggestions(); });
  }

  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeMobileSidebar(); closeSuggestions(); } });
  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { closeMobileSidebar(); applyDesktopPreference(); }, 80); });
  applyDesktopPreference();
})();
