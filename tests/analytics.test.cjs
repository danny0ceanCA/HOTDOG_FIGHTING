const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/analytics.js'), 'utf8');

function browser({query = '', stored = false, cookie = '', blocked = false} = {}) {
  const values = new Map(stored ? [['hotdog_analytics_opt_out', '1']] : []);
  const listeners = {}, scripts = [], timers = [], navigations = [];
  const status = {hidden: true, textContent: ''};
  let cookieText = cookie;
  const document = {
    head: {appendChild: script => scripts.push(script)},
    createElement: () => ({}),
    addEventListener: (name, fn) => {listeners[name] = fn;},
    getElementById: () => status,
    get cookie() {return cookieText;},
    set cookie(value) {cookieText = value.includes('Max-Age=0') ? '' : value.split(';')[0];}
  };
  const window = {
    location: {href: 'https://hotdogfighting.com/' + query, assign: url => navigations.push(url)},
    history: {replaceState: (_, __, url) => {window.cleanedUrl = url;}},
    setTimeout: fn => timers.push(fn)
  };
  const localStorage = {
    getItem(key) {if (blocked) throw Error('blocked'); return values.get(key) || null;},
    setItem(key, value) {if (blocked) throw Error('blocked'); values.set(key, value);},
    removeItem(key) {if (blocked) throw Error('blocked'); values.delete(key);}
  };
  vm.runInNewContext(source, {window, document, localStorage, URL});
  listeners.DOMContentLoaded();
  return {window, document, values, listeners, scripts, timers, navigations, status};
}

test('exclusion link prevents even the initial GA load and persists across visits', () => {
  const b = browser({query: '?analytics=off&utm_source=test'});
  assert.equal(b.scripts.length, 0);
  assert.equal(b.window['ga-disable-G-4BSML4TG35'], true);
  assert.equal(b.values.get('hotdog_analytics_opt_out'), '1');
  assert.equal(b.window.cleanedUrl, '/?utm_source=test');
  assert.match(b.status.textContent, /excluded/);
  assert.equal(browser({stored: true}).scripts.length, 0);
  assert.equal(browser({cookie: 'hotdog_analytics_opt_out=1'}).scripts.length, 0);
});

test('cookie fallback works when local storage is blocked and opt-in reverses exclusion', () => {
  const b = browser({query: '?analytics=off', blocked: true});
  assert.equal(b.scripts.length, 0);
  assert.match(b.document.cookie, /hotdog_analytics_opt_out=1/);
  const enabled = browser({query: '?analytics=on', stored: true, cookie: 'hotdog_analytics_opt_out=1'});
  assert.equal(enabled.scripts.length, 1);
  assert.equal(enabled.values.size, 0);
  assert.equal(enabled.document.cookie, '');
});

function articleEvent(overrides = {}) {
  return {type: 'click', button: 0, target: {closest: () => ({
    href: 'https://publisher.example/news/fight', textContent: ' Fight headline ',
    dataset: {articleCategory: 'MMA'}, target: ''
  })}, preventDefault() {this.prevented = true;}, ...overrides};
}

test('article event includes title, section and destination and navigation always completes once', () => {
  const b = browser();
  const e = articleEvent();
  b.listeners.click(e);
  const call = Array.from(b.window.dataLayer.at(-1));
  assert.equal(call[0], 'event');
  assert.equal(call[1], 'article_click');
  assert.equal(call[2].article_title, 'Fight headline');
  assert.equal(call[2].article_category, 'MMA');
  assert.equal(call[2].link_url, 'https://publisher.example/news/fight');
  assert.equal(call[2].link_domain, 'publisher.example');
  assert.equal(e.prevented, true);
  call[2].event_callback(); b.timers[0]();
  assert.equal(b.navigations.length, 1);
  const blocked = browser(); blocked.listeners.click(articleEvent()); blocked.timers[0]();
  assert.equal(blocked.navigations.length, 1);
});

test('modified and middle clicks preserve browser behavior; unrelated clicks are ignored', () => {
  const b = browser();
  const e = articleEvent({ctrlKey: true}); b.listeners.click(e);
  assert.equal(e.prevented, undefined);
  const middle = articleEvent({type: 'auxclick', button: 1}); b.listeners.auxclick(middle);
  assert.equal(middle.prevented, undefined);
  assert.equal(b.timers.length, 0);
  const count = b.window.dataLayer.length;
  b.listeners.click(articleEvent({target: {closest: () => null}}));
  b.listeners.auxclick(articleEvent({type: 'auxclick', button: 2}));
  assert.equal(b.window.dataLayer.length, count);
});
