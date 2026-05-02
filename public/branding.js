(function () {
  var FAVICON_ICON = '/public/favicon-icon.png';
  var lastFavicon = null;
  var NEW_CHAT_HINTS = ['new chat', 'new thread', 'new conversation'];
  var SURVEY_LINK_HINT = 'continue to the experience survey';
  var SURVEY_REDIRECT_KEY = 'ai_enable_last_survey_redirect_href';
  var progressBarFill = null;
  var progressBarLabel = null;
  var progressObserver = null;
  var maxInterviewProgress = 0;
  var surveyRedirectTimer = null;

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
    maybeAutoOpenSurveyTab();
  }

  function ensureInterviewProgressBar() {
    var existing = document.getElementById('interview-progress-shell');
    var shouldShow = shouldShowInterviewProgressBar();
    var estimatedProgress = estimateInterviewProgress(document.body ? document.body.innerText || '' : '');
    var progress = Math.max(maxInterviewProgress, estimatedProgress);
    var mountInfo = progress >= 0.95 ? null : findHeaderMountInfo();
    if (existing) {
      existing.style.display = shouldShow ? 'flex' : 'none';
      existing.setAttribute('data-mounted-in-header', mountInfo ? 'true' : 'false');
      progressBarFill = existing.querySelector('.interview-progress-fill');
      progressBarLabel = existing.querySelector('.interview-progress-label');
      if (shouldShow) {
        positionInterviewProgressBar(existing, mountInfo);
      }
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
    shell.style.display = shouldShow ? 'flex' : 'none';
    if (shouldShow) {
      positionInterviewProgressBar(shell, mountInfo);
    }
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

  function isLikelyNewChatControl(el) {
    if (!el) return false;
    var text = normalizeText(
      [
        el.textContent || '',
        el.getAttribute('aria-label') || '',
        el.getAttribute('title') || '',
        el.getAttribute('data-testid') || '',
      ].join(' ')
    );

    var hasHint = NEW_CHAT_HINTS.some(function (hint) {
      return text.indexOf(hint) !== -1;
    });
    if (hasHint) return true;

    var rect = typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect() : null;
    var hasVisibleText = (el.textContent || '').trim().length > 0;
    var hasIcon = !!el.querySelector('svg, img');
    return !!(rect && !hasVisibleText && hasIcon && rect.top <= 96 && rect.left <= 96);
  }

  function isExplicitNewChatControl(el) {
    if (!el) return false;
    var text = normalizeText(
      [
        el.textContent || '',
        el.getAttribute('aria-label') || '',
        el.getAttribute('title') || '',
        el.getAttribute('data-testid') || '',
      ].join(' ')
    );
    return NEW_CHAT_HINTS.some(function (hint) {
      return text.indexOf(hint) !== -1;
    });
  }

  function findNewChatControl() {
    var labeled = findControlElement('new chat');
    if (labeled) return labeled;

    var controls = Array.prototype.slice.call(
      document.querySelectorAll('button, a, [role="button"], [data-testid]')
    );
    for (var i = 0; i < controls.length; i += 1) {
      if (isLikelyNewChatControl(controls[i])) {
        return controls[i];
      }
    }
    return null;
  }

  function findHeaderMountInfo() {
    var newChatControl = findNewChatControl();
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
        var leftBound = newRect ? newRect.right + 20 : 120;
        var rightBound = readmeRect ? readmeRect.left - 20 : window.innerWidth - 120;
        var top = topRect.top + topRect.height / 2;
        if (
          !Number.isFinite(leftBound)
          || !Number.isFinite(rightBound)
          || !Number.isFinite(top)
          || rightBound - leftBound < 120
          || top < 16
          || top > 120
        ) {
          return null;
        }
        return {
          top: top,
          leftBound: leftBound,
          rightBound: rightBound,
          newChatRight: newRect ? newRect.right : null,
          readmeLeft: readmeRect ? readmeRect.left : null
        };
      }
    }
    return null;
  }

  function positionInterviewProgressBar(shell, mountInfo) {
    if (!shell) return;
    var width = 320;
    var top = 52;
    var mobileInlineLayout = false;
    if (mountInfo) {
      var available = mountInfo.rightBound - mountInfo.leftBound;
      width = clamp(available, 160, 360);
      top = mountInfo.top;
      if (window.innerWidth <= 620 && mountInfo.newChatRight) {
        var mobileLeft = mountInfo.newChatRight + 12;
        var mobileRight = mountInfo.readmeLeft ? mountInfo.readmeLeft - 12 : window.innerWidth - 16;
        var mobileAvailable = mobileRight - mobileLeft;
        if (mobileAvailable > 72) {
          width = Math.min(220, mobileAvailable);
          shell.style.left = String(Math.round(mobileLeft)) + 'px';
          shell.style.top = String(Math.round(top)) + 'px';
          shell.style.transform = 'translate(0, -50%)';
          shell.style.width = String(Math.round(width)) + 'px';
          mobileInlineLayout = true;
        }
      }
    } else {
      width = Math.min(320, window.innerWidth - 180);
    }

    shell.style.position = 'fixed';
    if (!mobileInlineLayout) {
      shell.style.left = '50%';
      shell.style.top = String(Math.round(top)) + 'px';
      shell.style.transform = 'translate(-50%, -50%)';
      shell.style.width = String(Math.round(width)) + 'px';
      shell.style.right = 'auto';
    }
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function countUserMessages() {
    var text = document.body ? document.body.innerText || '' : '';
    var matches = text.match(/Avatar for User/gi);
    return matches ? matches.length : 0;
  }

  function shouldShowInterviewProgressBar() {
    var readmeControl = findControlElement('readme');
    if (!readmeControl) return true;

    var locationText = normalizeText(
      [
        window.location.pathname || '',
        window.location.hash || '',
        window.location.search || '',
      ].join(' ')
    );
    if (locationText.indexOf('readme') !== -1) {
      return false;
    }

    var activeAttrs = [
      readmeControl.getAttribute('aria-current'),
      readmeControl.getAttribute('aria-selected'),
      readmeControl.getAttribute('aria-pressed'),
      readmeControl.getAttribute('data-state'),
    ].map(normalizeText);

    if (activeAttrs.indexOf('page') !== -1 || activeAttrs.indexOf('true') !== -1 || activeAttrs.indexOf('active') !== -1 || activeAttrs.indexOf('open') !== -1) {
      return false;
    }

    var classText = normalizeText(readmeControl.className || '');
    if (classText.indexOf('active') !== -1 || classText.indexOf('selected') !== -1 || classText.indexOf('current') !== -1) {
      return false;
    }

    return true;
  }

  function estimateInterviewProgress(text) {
    if (!text) return 0.06;
    if (text.indexOf('Thank you for your time. I’m finalizing your report now.') !== -1) return 1;
    if (text.indexOf("Thank you for your time. I'm finalizing your report now.") !== -1) return 1;
    if (text.indexOf('finalizing your report') !== -1) return 1;
    if (text.indexOf('This interview is now complete.') !== -1) return 1;
    if (text.indexOf('Your report is ready.') !== -1) return 1;
    if (text.indexOf('Continue to the experience survey in a new tab') !== -1) return 1;

    var progressNodes = document.querySelectorAll('[data-ai-enable-progress]');
    var latestProgressNode = progressNodes.length ? progressNodes[progressNodes.length - 1] : null;
    if (latestProgressNode) {
      var attr = latestProgressNode.getAttribute('data-ai-enable-progress');
      var parsed = parseFloat(attr);
      if (!Number.isNaN(parsed)) {
        return clamp(parsed, 0, 1);
      }
    }
    var explicitMatch = text.match(/ai_enable_progress:([0-9.]+)/);
    if (explicitMatch) {
      var explicit = parseFloat(explicitMatch[1]);
      if (!Number.isNaN(explicit)) {
        return clamp(explicit, 0, 1);
      }
    }

    var useCaseMatch = text.match(/\((\d+)\/(\d+)\)/);
    if (useCaseMatch) {
      var current = parseInt(useCaseMatch[1], 10);
      var total = parseInt(useCaseMatch[2], 10);
      if (total > 0 && current > 0) {
        return clamp(0.82 + ((current - 1) / total) * 0.14, 0.82, 0.96);
      }
    }

    if (text.indexOf('Next, we will review the suggested AI use cases one by one.') !== -1) return 0.8;
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

  function shouldResetProgressForFreshInterview(text) {
    if (!text) return false;
    var hasWelcome = text.indexOf("What's your name?") !== -1 || text.indexOf('This interview follows a research-based framework') !== -1;
    if (!hasWelcome) return false;
    var hasCompletion = (
      text.indexOf('This interview is now complete.') !== -1
      || text.indexOf('Your report is ready.') !== -1
      || text.indexOf('finalizing your report') !== -1
      || text.indexOf('Continue to the experience survey in a new tab') !== -1
    );
    return !hasCompletion;
  }

  function updateInterviewProgressBar() {
    ensureInterviewProgressBar();
    var text = document.body ? document.body.innerText || '' : '';
    if (shouldResetProgressForFreshInterview(text)) {
      maxInterviewProgress = 0;
    }
    var estimatedProgress = estimateInterviewProgress(text);
    maxInterviewProgress = Math.max(maxInterviewProgress, estimatedProgress);
    var progress = maxInterviewProgress;
    if (progressBarFill) {
      progressBarFill.style.width = progress >= 0.995 ? '100%' : String(Math.round(progress * 100)) + '%';
    }
    if (progressBarLabel) {
      progressBarLabel.textContent = 'Interview progress';
    }
    maybeAutoOpenSurveyTab();
  }

  function scheduleProgressUpdate() {
    window.requestAnimationFrame(updateInterviewProgressBar);
  }

  function getStoredSurveyRedirectHref() {
    try {
      return window.sessionStorage ? window.sessionStorage.getItem(SURVEY_REDIRECT_KEY) || '' : '';
    } catch (error) {
      return '';
    }
  }

  function setStoredSurveyRedirectHref(href) {
    try {
      if (!window.sessionStorage) return;
      if (href) {
        window.sessionStorage.setItem(SURVEY_REDIRECT_KEY, href);
      } else {
        window.sessionStorage.removeItem(SURVEY_REDIRECT_KEY);
      }
    } catch (error) {
      return;
    }
  }

  function findSurveyLink() {
    var links = Array.prototype.slice.call(document.querySelectorAll('a[href]'));
    for (var i = 0; i < links.length; i += 1) {
      var link = links[i];
      var text = normalizeText(link.textContent || '');
      if (text.indexOf(SURVEY_LINK_HINT) !== -1) {
        return link;
      }
    }
    return null;
  }

  function prepareSurveyLink(link) {
    if (!link) return;
    link.setAttribute('target', '_blank');
    link.setAttribute('rel', 'noopener noreferrer');
  }

  function maybeAutoOpenSurveyTab() {
    var link = findSurveyLink();
    if (!link || !link.href) {
      if (surveyRedirectTimer) {
        window.clearTimeout(surveyRedirectTimer);
        surveyRedirectTimer = null;
      }
      setStoredSurveyRedirectHref('');
      return;
    }

    prepareSurveyLink(link);
    setStoredSurveyRedirectHref(link.href);
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

  function markDraftRestoreSuppressed() {
    var nonce = String(Date.now()) + '_' + Math.random().toString(36).slice(2);
    maxInterviewProgress = 0;
    setCookie('suppress_draft_restore', nonce, 8);
    window.requestAnimationFrame(updateInterviewProgressBar);
  }

  function maybeMarkNewChatActivation(event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var el = target.closest('button, a, [role="button"]');
    if (!el) return;

    var detectedNewChatControl = findNewChatControl();
    if (isExplicitNewChatControl(el) || el === detectedNewChatControl || (detectedNewChatControl && detectedNewChatControl.contains(el))) {
      markDraftRestoreSuppressed();
    }
  }

  function maybeMarkNewChatKey(event) {
    var key = event.key || '';
    if (key !== 'Enter' && key !== ' ') return;
    maybeMarkNewChatActivation(event);
  }

  function installNewChatSuppressionHandlers() {
    document.addEventListener('pointerdown', maybeMarkNewChatActivation, true);
    document.addEventListener('click', maybeMarkNewChatActivation, true);
    document.addEventListener('keydown', maybeMarkNewChatKey, true);
  }

  function boot() {
    applyBranding();
    installNewChatSuppressionHandlers();
    observeProgressTriggers();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      boot();
    });
  } else {
    boot();
  }
})();
