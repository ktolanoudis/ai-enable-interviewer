(function () {
  var FAVICON_ICON = '/public/favicon-icon.png';
  var lastFavicon = null;
  var NEW_CHAT_HINTS = ['new chat', 'new thread', 'new conversation'];
  var progressBarFill = null;
  var progressBarLabel = null;
  var progressObserver = null;

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
    ensureInterviewProgressBar();
    updateInterviewProgressBar();
  }

  function ensureInterviewProgressBar() {
    var existing = document.getElementById('interview-progress-shell');
    var mountInfo = findHeaderMountInfo();
    if (existing) {
      existing.style.display = 'flex';
      existing.setAttribute('data-mounted-in-header', mountInfo ? 'true' : 'false');
      progressBarFill = existing.querySelector('.interview-progress-fill');
      progressBarLabel = existing.querySelector('.interview-progress-label');
      positionInterviewProgressBar(existing, mountInfo);
      return;
    }

    var shell = document.createElement('div');
    shell.id = 'interview-progress-shell';
    shell.setAttribute('data-mounted-in-header', mountInfo ? 'true' : 'false');
    shell.innerHTML = [
      '<div class="interview-progress-track" aria-hidden="true">',
      '<div class="interview-progress-fill"></div>',
      '</div>',
      '<div class="interview-progress-label">Interview progress</div>'
    ].join('');

    document.body.appendChild(shell);
    progressBarFill = shell.querySelector('.interview-progress-fill');
    progressBarLabel = shell.querySelector('.interview-progress-label');
    positionInterviewProgressBar(shell, mountInfo);
  }

  function normalizeText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function findControlElement(label) {
    var controls = Array.prototype.slice.call(
      document.querySelectorAll('button, a, [role="button"], [data-testid]')
    );
    for (var i = 0; i < controls.length; i += 1) {
      var el = controls[i];
      var text = normalizeText(
        [el.textContent, el.getAttribute('aria-label'), el.getAttribute('title')].join(' ')
      );
      if (text.indexOf(label) !== -1) {
        return el;
      }
    }
    return null;
  }

  function findCommonAncestor(a, b) {
    if (!a || !b) return null;
    var seen = [];
    var current = a;
    while (current) {
      seen.push(current);
      current = current.parentElement;
    }
    current = b;
    while (current) {
      if (seen.indexOf(current) !== -1) return current;
      current = current.parentElement;
    }
    return null;
  }

  function findHeaderMountInfo() {
    var newChatControl = findControlElement('new chat');
    var readmeControl = findControlElement('readme');
    if (newChatControl || readmeControl) {
      var newRect = newChatControl && typeof newChatControl.getBoundingClientRect === 'function'
        ? newChatControl.getBoundingClientRect()
        : null;
      var readmeRect = readmeControl && typeof readmeControl.getBoundingClientRect === 'function'
        ? readmeControl.getBoundingClientRect()
        : null;
      var topRect = newRect || readmeRect;
      if (topRect) {
        return {
          top: topRect.top + topRect.height / 2,
          leftBound: newRect ? newRect.right + 20 : 120,
          rightBound: readmeRect ? readmeRect.left - 20 : window.innerWidth - 120
        };
      }
    }
    return null;
  }

  function positionInterviewProgressBar(shell, mountInfo) {
    if (!shell) return;
    var width = 320;
    var top = 52;
    if (mountInfo) {
      var available = mountInfo.rightBound - mountInfo.leftBound;
      width = clamp(available, 160, 360);
      top = mountInfo.top;
    } else {
      width = Math.min(320, window.innerWidth - 180);
    }

    shell.style.position = 'fixed';
    shell.style.left = '50%';
    shell.style.top = String(Math.round(top)) + 'px';
    shell.style.transform = 'translate(-50%, -50%)';
    shell.style.width = String(Math.round(width)) + 'px';
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function countUserMessages() {
    var text = document.body ? document.body.innerText || '' : '';
    var matches = text.match(/Avatar for User/gi);
    return matches ? matches.length : 0;
  }

  function estimateInterviewProgress(text) {
    var progressNodes = document.querySelectorAll('[data-interview-progress]');
    var latestProgressNode = progressNodes.length ? progressNodes[progressNodes.length - 1] : null;
    if (latestProgressNode) {
      var attr = latestProgressNode.getAttribute('data-interview-progress');
      var parsed = parseFloat(attr);
      if (!isNaN(parsed)) {
        return clamp(parsed, 0, 1);
      }
    }

    if (!text) return 0.06;
    if (text.indexOf('This interview is now complete.') !== -1) return 1;
    if (text.indexOf('Your report is ready.') !== -1) return 1;

    var useCaseMatch = text.match(/\((\d+)\/(\d+)\)/);
    if (useCaseMatch) {
      var current = parseInt(useCaseMatch[1], 10);
      var total = parseInt(useCaseMatch[2], 10);
      if (total > 0 && current > 0) {
        return clamp(0.82 + ((current - 1) / total) * 0.14, 0.82, 0.96);
      }
    }

    if (text.indexOf('Would you like to review them now?') !== -1) return 0.8;
    if (text.indexOf('Before the final review step') !== -1) return 0.76;
    if (text.indexOf('How would you rate it from 1 to 5') !== -1) return 0.86;
    if (text.indexOf('One short feasibility check before we move on.') !== -1) return 0.9;
    if (text.indexOf('What are your main day-to-day tasks?') !== -1) return 0.22;
    if (text.indexOf('What company do you work for?') !== -1) return 0.1;
    if (text.indexOf('What is your company website URL?') !== -1) return 0.14;
    if (text.indexOf('What\'s your work email?') !== -1) return 0.18;
    if (text.indexOf('What department do you work in?') !== -1) return 0.2;
    if (text.indexOf('What\'s your position/role?') !== -1) return 0.22;

    var userMessages = countUserMessages();
    if (userMessages > 0) {
      return clamp(0.12 + userMessages * 0.045, 0.12, 0.74);
    }

    return 0.06;
  }

  function updateInterviewProgressBar() {
    ensureInterviewProgressBar();
    var text = document.body ? document.body.innerText || '' : '';
    var progress = estimateInterviewProgress(text);
    if (progressBarFill) {
      progressBarFill.style.width = String(Math.round(progress * 100)) + '%';
    }
    if (progressBarLabel) {
      progressBarLabel.textContent = 'Interview progress';
    }
  }

  function scheduleProgressUpdate() {
    window.requestAnimationFrame(updateInterviewProgressBar);
  }

  function observeProgressTriggers() {
    if (progressObserver || !document.body) return;
    progressObserver = new MutationObserver(function () {
      scheduleProgressUpdate();
    });
    progressObserver.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    window.addEventListener('resize', scheduleProgressUpdate);
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
      observeProgressTriggers();
    });
  } else {
    applyBranding();
    document.addEventListener('click', maybeMarkNewChatClick, true);
    observeProgressTriggers();
  }
})();
