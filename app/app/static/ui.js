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
  function deviceMeta(item, query) {
    const values = [['Seriale', item.serial], ['MAC', item.mac], ['IP', item.ip]].filter((entry) => entry[1]);
    if (!values.length) return '';
    return '<span class="search-suggestion-meta">' + values.map(([label, value]) => `<span class="search-meta-chip"><b>${label}</b> ${highlightText(value, query)}</span>`).join('') + '</span>';
  }
  function renderGroup(type, rows, query) {
    if (!rows.length) return '';
    return `<div class="search-suggestions-header">${escapeHtml(type)}</div>` + rows.map((item) => {
      const match = item.match_field && item.match_value ? `<span class="search-match"><b>${escapeHtml(item.match_field)}</b> ${highlightText(item.match_value, query)}</span>` : '';
      const meta = item.type === 'Apparato' ? deviceMeta(item, query) : '';
      return `<a class="search-suggestion" href="${escapeHtml(item.url)}" data-completion="${escapeHtml(item.completion || item.title)}" role="option"><span class="result-icon">${resultIcon(item.type)}</span><span class="search-suggestion-copy"><strong>${highlightText(item.title, query)}</strong><small>${escapeHtml(item.subtitle)}</small>${match}${meta}</span><span class="chevron">›</span></a>`;
    }).join('');
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
    suggestions.innerHTML = order.map((type) => renderGroup(type, rows.filter((item) => item.type === type), query)).join('') + `<div class="search-suggestions-footer"><strong>Tab</strong> completa · ↑ ↓ naviga · Invio apre · Esc chiude</div>`;
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
    searchInput.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(fetchSuggestions, 140); });
    searchInput.addEventListener('focus', () => { if (searchInput.value.trim().length >= 2) fetchSuggestions(); });
    searchInput.addEventListener('keydown', (event) => {
      const items = suggestionItems();
      if (event.key === 'ArrowDown' && items.length) { event.preventDefault(); setActive(activeIndex + 1); }
      else if (event.key === 'ArrowUp' && items.length) { event.preventDefault(); setActive(activeIndex <= 0 ? items.length - 1 : activeIndex - 1); }
      else if (event.key === 'Enter' && activeIndex >= 0 && items[activeIndex]) { event.preventDefault(); window.location.href = items[activeIndex].href; }
      else if (event.key === 'Tab' && items.length && suggestions.classList.contains('open')) {
        const target = items[activeIndex >= 0 ? activeIndex : 0];
        const completion = target ? target.dataset.completion : '';
        if (completion && completion.toLowerCase() !== searchInput.value.trim().toLowerCase()) {
          event.preventDefault(); searchInput.value = completion; searchInput.setSelectionRange(completion.length, completion.length); clearTimeout(searchTimer); searchTimer = setTimeout(fetchSuggestions, 40);
        }
      } else if (event.key === 'Escape') closeSuggestions();
    });
    suggestions.addEventListener('mousemove', (event) => {
      const item = event.target.closest('.search-suggestion');
      if (!item) return;
      const items = suggestionItems();
      const index = items.indexOf(item);
      if (index >= 0 && index !== activeIndex) setActive(index);
    });
    document.addEventListener('click', (event) => { if (searchWrap && !searchWrap.contains(event.target)) closeSuggestions(); });
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