(function () {
  var FAVICON_ICON = '/public/favicon-icon.png';
  var lastFavicon = null;

  function setTitle() {
    document.title = 'AI-Enable Interviewer';
  }

  function setFavicon() {
    var href = FAVICON_ICON;
    if (href === lastFavicon) return;
    var rels = ['icon', 'shortcut icon', 'apple-touch-icon'];
    rels.forEach(function (rel) {
      var link = document.querySelector('link[rel="' + rel + '"]');
      if (!link) {
        link = document.createElement('link');
        link.rel = rel;
        document.head.appendChild(link);
      }
      link.type = 'image/png';
      link.href = href;
    });
    lastFavicon = href;
  }

  function applyBranding() {
    setTitle();
    setFavicon();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      applyBranding();
    });
  } else {
    applyBranding();
  }
})();
