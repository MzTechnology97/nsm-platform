(function () {
  if (!document.querySelector('link[data-runtime-branding]')) {
    const brandingCss = document.createElement('link');
    brandingCss.rel = 'stylesheet';
    brandingCss.href = '/branding/theme.css?v=' + Date.now();
    brandingCss.dataset.runtimeBranding = '1';
    document.head.appendChild(brandingCss);
  }

  function dismissFlash(element) {
    if (!element || element.classList.contains('is-leaving')) return;
    element.classList.add('is-leaving');
    window.setTimeout(() => element.remove(), 190);
  }
  document.querySelectorAll('[data-flash-message]').forEach((message) => {
    const close = message.querySelector('[data-flash-dismiss]');
    if (close) close.addEventListener('click', () => dismissFlash(message));
    if (message.dataset.flashAutoclose === '1') {
      window.setTimeout(() => dismissFlash(message), 6500);
    }
  });

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
  const searchForm = searchWrap ? searchWrap.querySelector('form') : null;
  const RECENT_KEY = 'nsm.search.recent';
  let searchTimer = null, searchAbort = null, activeIndex = -1;

  function escapeHtml(value) { return String(value ?? '').replace(/[&<>'\"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','\"':'&quot;'}[char])); }
  function highlightText(value, query) {
    const text = String(value ?? '');
    const needle = String(query ?? '').trim();
    if (!needle) return escapeHtml(text);
    const index = text.toLowerCase().indexOf(needle.toLowerCase());
    if (index < 0) return escapeHtml(text);
    return escapeHtml(text.slice(0, index)) + '<mark>' + escapeHtml(text.slice(index, index + needle.length)) + '</mark>' + escapeHtml(text.slice(index + needle.length));
  }
  function readRecent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]').filter((v) => typeof v === 'string').slice(0, 6); } catch (e) { return []; } }
  function saveRecent(value) {
    const term = String(value || '').trim();
    if (term.length < 2) return;
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([term, ...readRecent().filter((v) => v.toLowerCase() !== term.toLowerCase())].slice(0, 6))); } catch (e) {}
  }
  function clearRecent() { try { localStorage.removeItem(RECENT_KEY); } catch (e) {} }
  const TYPE_META = {
    'Pagina': {label: 'Pagine', icon: '↗', cls: 'page'},
    'Vulnerabilità': {label: 'Vulnerabilità', icon: '!', cls: 'cve'},
    'Apparato': {label: 'Apparati', icon: '▣', cls: 'device'},
    'Cliente': {label: 'Clienti', icon: '◎', cls: 'customer'},
    'Sede': {label: 'Sedi', icon: '⌖', cls: 'site'},
  };
  function iconFor(item) {
    if (item.type === 'Apparato' && item.icon) {
      const fill = item.icon.color ? ` fill="${escapeHtml(item.icon.color)}"` : '';
      const kind = item.icon.brand ? 'brand' : 'generic';
      return `<span class="sg-icon sg-device brand-icon brand-icon-${kind}" title="${escapeHtml(item.icon.label)}"><svg width="18" height="18" viewBox="0 0 24 24"${fill} aria-hidden="true"><use href="/static/brand-icons.svg#${escapeHtml(item.icon.symbol)}"></use></svg></span>`;
    }
    const meta = TYPE_META[item.type] || TYPE_META['Apparato'];
    return `<span class="sg-icon sg-${meta.cls}" aria-hidden="true">${meta.icon}</span>`;
  }
  function closeSuggestions() { if (!suggestions) return; suggestions.classList.remove('open'); suggestions.innerHTML = ''; activeIndex = -1; if (searchInput) searchInput.setAttribute('aria-expanded', 'false'); }
  function openSuggestions(html) { suggestions.innerHTML = html; suggestions.classList.add('open'); activeIndex = -1; if (searchInput) searchInput.setAttribute('aria-expanded', 'true'); }
  function suggestionItems() { return suggestions ? Array.from(suggestions.querySelectorAll('.search-suggestion')) : []; }
  function setActive(index) {
    const items = suggestionItems();
    if (!items.length) { activeIndex = -1; return; }
    activeIndex = Math.max(0, Math.min(index, items.length - 1));
    items.forEach((item, i) => { item.classList.toggle('active', i === activeIndex); item.setAttribute('aria-selected', i === activeIndex ? 'true' : 'false'); });
    items[activeIndex].scrollIntoView({block: 'nearest'});
  }
  function row(item, query) {
    const status = item.type === 'Apparato' ? `<i class="sg-dot status-${escapeHtml(item.status || 'unknown')}" title="${escapeHtml(item.status || '')}"></i>` : '';
    const severity = item.type === 'Vulnerabilità' ? `<span class="severity-badge severity-${escapeHtml(item.severity || 'unknown')}">${escapeHtml(item.severity || 'n.d.')}</span>` : '';
    const showMatch = item.match_field && item.match_value && !['Alias', 'Identity', 'Apparato', 'Cliente', 'Sede'].includes(item.match_field);
    const side = showMatch ? `<span class="sg-match"><b>${escapeHtml(item.match_field)}</b>${highlightText(item.match_value, query)}</span>` : severity;
    return `<a class="search-suggestion sg-row" href="${escapeHtml(item.url)}" data-completion="${escapeHtml(item.completion || item.title)}" data-title="${escapeHtml(item.title)}" role="option" aria-selected="false">${iconFor(item)}<span class="sg-main"><span class="sg-title"><span class="sg-text">${highlightText(item.title, query)}</span>${status}</span><span class="sg-sub">${escapeHtml(item.subtitle || '')}</span></span>${side}</a>`;
  }
  function renderGroup(type, rows, query, total) {
    if (!rows.length) return '';
    const meta = TYPE_META[type] || {label: type};
    const more = total && total > rows.length ? `<a class="sg-more" href="/search?q=${encodeURIComponent(query)}">vedi tutti (${total})</a>` : `<span class="sg-count">${rows.length}</span>`;
    return `<div class="sg-group"><div class="search-suggestions-header sg-header"><span>${escapeHtml(meta.label)}</span>${more}</div>${rows.map((item) => row(item, query)).join('')}</div>`;
  }
  const FOOTER = '<div class="search-suggestions-footer sg-footer"><span><kbd>↑</kbd><kbd>↓</kbd> naviga</span><span><kbd>Invio</kbd> apri</span><span><kbd>Tab</kbd> completa</span><span><kbd>Esc</kbd> chiudi</span></div>';
  function renderSuggestions(payload, query) {
    if (!suggestions) return;
    const rows = payload.results || [];
    const counts = payload.counts || {};
    const all = `<a class="search-suggestion sg-row sg-all" href="/search?q=${encodeURIComponent(query)}" data-completion="${escapeHtml(query)}" role="option" aria-selected="false"><span class="sg-icon sg-page" aria-hidden="true">⌕</span><span class="sg-main"><span class="sg-title">Cerca «${escapeHtml(query)}» in tutto il portale</span></span></a>`;
    if (!rows.length) {
      openSuggestions(`${all}<div class="search-suggestions-empty">Nessun suggerimento per <strong>${escapeHtml(query)}</strong>: prova con MAC, IP, seriale, nome cliente o sede.</div>${FOOTER}`);
      return;
    }
    const order = ['Pagina', 'Vulnerabilità', 'Apparato', 'Cliente', 'Sede'];
    openSuggestions(all + order.map((type) => renderGroup(type, rows.filter((item) => item.type === type), query, counts[type])).join('') + FOOTER);
  }
  function renderRecent() {
    const recent = readRecent();
    if (!recent.length) { closeSuggestions(); return; }
    openSuggestions(`<div class="sg-group"><div class="search-suggestions-header sg-header"><span>Ricerche recenti</span><button type="button" class="sg-clear" data-search-clear-recent>cancella</button></div>${recent.map((term) => `<a class="search-suggestion sg-row" href="/search?q=${encodeURIComponent(term)}" data-completion="${escapeHtml(term)}" role="option" aria-selected="false"><span class="sg-icon sg-recent" aria-hidden="true">↺</span><span class="sg-main"><span class="sg-title">${escapeHtml(term)}</span></span></a>`).join('')}</div>${FOOTER}`);
  }
  async function fetchSuggestions() {
    if (!searchInput || !suggestions) return;
    const query = searchInput.value.trim();
    if (query.length < 2) { if (!query) renderRecent(); else closeSuggestions(); return; }
    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();
    if (!suggestions.classList.contains('open')) openSuggestions('<div class="search-suggestions-empty">Ricerca in corso…</div>');
    suggestions.classList.add('loading');
    try {
      const response = await fetch('/api/v1/search/suggest?q=' + encodeURIComponent(query), {signal: searchAbort.signal, headers: {'Accept': 'application/json'}});
      if (!response.ok) throw new Error('search failed');
      renderSuggestions(await response.json(), query);
    } catch (error) {
      if (error.name === 'AbortError') return;
      openSuggestions('<div class="search-suggestions-empty">Ricerca temporaneamente non disponibile.</div>');
    } finally {
      suggestions.classList.remove('loading');
    }
  }
  if (searchInput && suggestions) {
    searchInput.setAttribute('autocomplete', 'off');
    searchInput.setAttribute('role', 'combobox');
    searchInput.setAttribute('aria-expanded', 'false');
    searchInput.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(fetchSuggestions, 140); });
    searchInput.addEventListener('focus', () => { if (searchInput.value.trim().length >= 2) fetchSuggestions(); else if (!searchInput.value.trim()) renderRecent(); });
    searchInput.addEventListener('keydown', (event) => {
      const items = suggestionItems();
      if (event.key === 'ArrowDown' && items.length) { event.preventDefault(); setActive(activeIndex + 1); }
      else if (event.key === 'ArrowUp' && items.length) { event.preventDefault(); setActive(activeIndex <= 0 ? items.length - 1 : activeIndex - 1); }
      else if (event.key === 'Enter' && activeIndex >= 0 && items[activeIndex]) { event.preventDefault(); saveRecent(searchInput.value); window.location.href = items[activeIndex].href; }
      else if (event.key === 'Tab' && items.length && suggestions.classList.contains('open')) {
        const target = items[activeIndex >= 0 ? activeIndex : 0];
        const completion = target ? target.dataset.completion : '';
        if (completion && completion.toLowerCase() !== searchInput.value.trim().toLowerCase()) {
          event.preventDefault(); searchInput.value = completion; searchInput.setSelectionRange(completion.length, completion.length); clearTimeout(searchTimer); searchTimer = setTimeout(fetchSuggestions, 40);
        }
      } else if (event.key === 'Escape') { closeSuggestions(); searchInput.blur(); }
    });
    if (searchForm) searchForm.addEventListener('submit', () => saveRecent(searchInput.value));
    suggestions.addEventListener('click', (event) => {
      if (event.target.closest('[data-search-clear-recent]')) { event.preventDefault(); clearRecent(); closeSuggestions(); searchInput.focus(); return; }
      if (event.target.closest('.search-suggestion')) saveRecent(searchInput.value);
    });
    suggestions.addEventListener('mousemove', (event) => {
      const item = event.target.closest('.search-suggestion');
      if (!item) return;
      const items = suggestionItems();
      const index = items.indexOf(item);
      if (index >= 0 && index !== activeIndex) setActive(index);
    });
    document.addEventListener('click', (event) => { if (searchWrap && !searchWrap.contains(event.target)) closeSuggestions(); });
    document.addEventListener('keydown', (event) => {
      // "/" or Ctrl+K focuses the global search from anywhere (not while typing in a field).
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement && document.activeElement.tagName);
      if ((event.key === '/' && !typing) || (event.key.toLowerCase() === 'k' && (event.ctrlKey || event.metaKey))) { event.preventDefault(); searchInput.focus(); searchInput.select(); }
    });
  }

  const enrollmentCommand = document.getElementById('enrollment-command');
  const enrollmentCopyButton = enrollmentCommand ? enrollmentCommand.closest('.command-box')?.querySelector('button') : null;
  if (enrollmentCommand && enrollmentCopyButton) {
    enrollmentCopyButton.onclick = null;
    const originalLabel = enrollmentCopyButton.textContent.trim() || 'Copia comando';
    let feedbackTimer = null;
    function setCopyFeedback(label) {
      enrollmentCopyButton.textContent = label;
      clearTimeout(feedbackTimer);
      feedbackTimer = setTimeout(() => { enrollmentCopyButton.textContent = originalLabel; }, 1800);
    }
    async function copyEnrollmentCommand() {
      const text = enrollmentCommand.textContent || '';
      if (!text) { setCopyFeedback('Comando vuoto'); return; }
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0'; document.body.appendChild(area); area.select();
          const copied = document.execCommand('copy'); area.remove();
          if (!copied) throw new Error('copy fallback failed');
        }
        setCopyFeedback('Copiato');
      } catch (error) { setCopyFeedback('Copia non riuscita'); }
    }
    enrollmentCopyButton.addEventListener('click', copyEnrollmentCommand);
  }

  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeMobileSidebar(); closeSuggestions(); } });
  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { closeMobileSidebar(); applyDesktopPreference(); }, 80); });
  applyDesktopPreference();
})();