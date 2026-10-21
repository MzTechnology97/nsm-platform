/* Applies the theme before the stylesheet paints (loaded synchronously in <head>).
   The preference is light, dark or system (follows prefers-color-scheme); the page always gets light or dark. */
(function () {
  try {
    var root = document.documentElement;
    var serverTheme = root.getAttribute('data-server-theme') || '';
    var preference = serverTheme || localStorage.getItem('nsm-theme') || 'light';
    var dark = preference === 'dark' || (preference === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    if (serverTheme) localStorage.setItem('nsm-theme', serverTheme);
  } catch (e) {}
})();
