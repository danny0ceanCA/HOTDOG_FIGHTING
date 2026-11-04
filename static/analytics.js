(function () {
  'use strict';
  var measurementId = 'G-4BSML4TG35';
  var storageKey = 'hotdog_analytics_opt_out';
  var pageUrl = new URL(window.location.href);
  var preference = pageUrl.searchParams.get('analytics');
  var excluded = preference === 'off';
  var saved = false;

  // Decide before loading Google Analytics, including on the exclusion visit.
  try {
    if (preference === 'off') localStorage.setItem(storageKey, '1');
    if (preference === 'on') localStorage.removeItem(storageKey);
    excluded = excluded || localStorage.getItem(storageKey) === '1';
    saved = localStorage.getItem(storageKey) === '1';
  } catch (error) { /* A first-party cookie also supports blocked local storage. */ }
  if (preference === 'off' || preference === 'on') {
    document.cookie = storageKey + '=' + (preference === 'off' ? '1' : '') +
      '; Path=/; Max-Age=' + (preference === 'off' ? '31536000' : '0') +
      '; SameSite=Lax' + (pageUrl.protocol === 'https:' ? '; Secure' : '');
    pageUrl.searchParams.delete('analytics');
    window.history.replaceState(null, '', pageUrl.pathname + pageUrl.search + pageUrl.hash);
  }
  var cookieSaved = document.cookie.split(';').some(function (value) {
    return value.trim() === storageKey + '=1';
  });
  excluded = excluded || cookieSaved;
  saved = saved || cookieSaved;
  window['ga-disable-' + measurementId] = excluded;

  document.addEventListener('DOMContentLoaded', function () {
    var status = document.getElementById('analytics-status');
    if (status && excluded) {
      status.hidden = false;
      status.textContent = saved
        ? 'Your visits in this browser are excluded from Google Analytics. Repeat this setup after clearing site data.'
        : 'Analytics is off for this page, but this browser blocked saving your preference. Allow site storage and reopen the exclusion link.';
    } else if (status && preference === 'on') {
      status.hidden = false;
      status.textContent = 'Google Analytics is enabled again in this browser.';
    }
  });
  if (excluded) return;

  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  window.gtag('js', new Date());
  window.gtag('config', measurementId);
  var tag = document.createElement('script');
  tag.async = true;
  tag.src = 'https://www.googletagmanager.com/gtag/js?id=' + measurementId;
  document.head.appendChild(tag);

  function trackArticle(event) {
    if (event.type === 'auxclick' && event.button !== 1) return;
    var article = event.target.closest('a[data-article-category]');
    if (!article) return;
    var destination = new URL(article.href, window.location.href);
    if (destination.protocol !== 'https:' && destination.protocol !== 'http:') return;
    var parameters = {
      send_to: measurementId,
      article_title: article.textContent.trim().slice(0, 100),
      article_category: article.dataset.articleCategory,
      link_url: destination.href,
      link_domain: destination.hostname
    };
    // Let normal navigation proceed after dispatch, even when Analytics is blocked.
    if (event.type === 'click' && event.button === 0 && !event.ctrlKey &&
        !event.metaKey && !event.shiftKey && !event.altKey &&
        (!article.target || article.target === '_self') && !event.defaultPrevented) {
      event.preventDefault();
      var navigated = false;
      function navigate() {
        if (navigated) return;
        navigated = true;
        window.location.assign(destination.href);
      }
      parameters.event_callback = navigate;
      parameters.event_timeout = 300;
      window.setTimeout(navigate, 350);
    }
    window.gtag('event', 'article_click', parameters);
  }
  document.addEventListener('click', trackArticle);
  document.addEventListener('auxclick', trackArticle);
}());
