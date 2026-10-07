/* Applies the theme before the stylesheet paints (loaded synchronously in <head>). */
(function () {
  try {
    var root = document.documentElement;
    var serverTheme = root.getAttribute('data-server-theme') || '';
    var theme = serverTheme || localStorage.getItem('nsm-theme') || 'light';
    root.dataset.theme = theme;
    if (serverTheme) localStorage.setItem('nsm-theme', serverTheme);
  } catch (e) {}
})();
