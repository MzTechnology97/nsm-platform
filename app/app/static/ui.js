(function () {
  const shell = document.querySelector('[data-app-shell]');
  if (!shell) return;

  const storageKey = 'nsm-sidebar-collapsed';
  const desktop = () => window.matchMedia('(min-width: 901px)').matches;

  function applyDesktopPreference() {
    if (!desktop()) {
      shell.classList.remove('sidebar-collapsed');
      return;
    }
    let collapsed = false;
    try {
      collapsed = localStorage.getItem(storageKey) === '1';
    } catch (e) {}
    shell.classList.toggle('sidebar-collapsed', collapsed);
  }

  function closeMobileSidebar() {
    shell.classList.remove('sidebar-open');
    document.body.classList.remove('sidebar-lock');
  }

  function openMobileSidebar() {
    shell.classList.add('sidebar-open');
    document.body.classList.add('sidebar-lock');
  }

  document.querySelectorAll('[data-sidebar-open]').forEach((button) => {
    button.addEventListener('click', openMobileSidebar);
  });

  document.querySelectorAll('[data-sidebar-close]').forEach((button) => {
    button.addEventListener('click', closeMobileSidebar);
  });

  document.querySelectorAll('[data-sidebar-collapse]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!desktop()) return;
      const collapsed = !shell.classList.contains('sidebar-collapsed');
      shell.classList.toggle('sidebar-collapsed', collapsed);
      try {
        localStorage.setItem(storageKey, collapsed ? '1' : '0');
      } catch (e) {}
    });
  });

  document.querySelectorAll('.sidebar-nav a').forEach((link) => {
    link.addEventListener('click', () => {
      if (!desktop()) closeMobileSidebar();
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMobileSidebar();
  });

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      closeMobileSidebar();
      applyDesktopPreference();
    }, 80);
  });

  applyDesktopPreference();
})();
