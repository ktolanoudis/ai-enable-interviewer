(function () {
  function setTitle() {
    document.title = 'AI-Enable Interviewer';
  }

  function setFavicon() {
    var href = '/public/avatar-logo.png?v=' + Date.now();
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
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTitle();
      setFavicon();
    });
  } else {
    setTitle();
    setFavicon();
  }
})();
