(function () {
  var FAVICON_ICON = '/public/favicon-icon.png';
  var lastFavicon = null;
  var NEW_CHAT_HINTS = ['new chat', 'new thread', 'new conversation'];

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

  function setCookie(name, value, maxAgeSeconds) {
    var cookie = name + '=' + value + '; path=/; SameSite=Lax';
    if (typeof maxAgeSeconds === 'number') {
      cookie += '; max-age=' + String(maxAgeSeconds);
    }
    document.cookie = cookie;
  }

  function maybeMarkNewChatClick(event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var el = target.closest('button, a, [role="button"]');
    if (!el) return;

    var text = [
      el.textContent || '',
      el.getAttribute('aria-label') || '',
      el.getAttribute('title') || '',
      el.getAttribute('data-testid') || '',
    ].join(' ').toLowerCase();

    var isNewChatControl = NEW_CHAT_HINTS.some(function (hint) {
      return text.indexOf(hint) !== -1;
    });

    if (!isNewChatControl) {
      var rect = typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect() : null;
      var hasVisibleText = (el.textContent || '').trim().length > 0;
      var hasIcon = !!el.querySelector('svg, img');
      if (rect && !hasVisibleText && hasIcon && rect.top <= 96 && rect.left <= 96) {
        isNewChatControl = true;
      }
    }

    if (isNewChatControl) {
      setCookie('suppress_draft_restore', '1', 30);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      applyBranding();
      document.addEventListener('click', maybeMarkNewChatClick, true);
    });
  } else {
    applyBranding();
    document.addEventListener('click', maybeMarkNewChatClick, true);
  }
})();
