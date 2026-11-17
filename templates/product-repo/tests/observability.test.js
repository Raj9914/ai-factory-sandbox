import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import {
  createErrorTracker, scrub, scrubEvent, scrubString, isPiiLikeKey, REDACTED,
} from '../src/observability/errors.js';

function fakeTarget() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
    dispatch(type, event) { for (const fn of listeners[type] || []) fn(event); },
  };
}

test('no network calls when DSN is unset, empty or whitespace', async () => {
  const originalFetch = globalThis.fetch;
  let globalCalls = 0;
  globalThis.fetch = () => { globalCalls += 1; return Promise.resolve({ ok: true }); };
  try {
    for (const dsn of [undefined, null, '', '   ']) {
      let injectedCalls = 0;
      let transportCalls = 0;
      const tracker = createErrorTracker({
        dsn,
        fetch: () => { injectedCalls += 1; return Promise.resolve({ ok: true }); },
        transport: { send: () => { transportCalls += 1; } },
      });
      assert.equal(tracker.enabled, false);
      assert.equal(tracker.captureException(new Error('boom')), false);
      assert.equal(tracker.captureMessage('hello'), false);

      const win = fakeTarget();
      tracker.installBrowserHandlers(win)();
      assert.deepEqual(win.listeners, {});
      const proc = new EventEmitter();
      tracker.installNodeHandlers(proc)();
      assert.equal(proc.listenerCount('uncaughtExceptionMonitor'), 0);

      await new Promise((resolve) => setTimeout(resolve, 10));
      assert.equal(injectedCalls, 0);
      assert.equal(transportCalls, 0);
    }
    assert.equal(createErrorTracker().enabled, false);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(globalCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('with a DSN, the built-in transport POSTs a scrubbed event to the DSN', async () => {
  const calls = [];
  const tracker = createErrorTracker({
    dsn: ' https://errors.example.test/ingest ',
    environment: 'production',
    release: '1.2.3.4',
    fetch: (url, init) => { calls.push({ url, init }); return Promise.resolve({ ok: true }); },
  });
  assert.equal(tracker.enabled, true);
  const err = new Error('Login failed for jane@example.com from 203.0.113.9');
  assert.equal(tracker.captureException(err, { tags: { area: 'auth' }, extra: { email: 'jane@example.com', count: 2 } }), true);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://errors.example.test/ingest');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.credentials, 'omit');
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.message, `Login failed for ${REDACTED} from ${REDACTED}`);
  assert.equal(body.extra.email, REDACTED);
  assert.equal(body.extra.count, 2);
  assert.equal(body.tags.area, 'auth');
  assert.equal(body.release, '1.2.3.4');
  assert.match(body.timestamp, /^\d{4}-\d{2}-\d{2}T/);
  assert.ok(!JSON.stringify(body).includes('jane@example.com'));
  assert.ok(!JSON.stringify(body).includes('203.0.113.9'));
});

test('a custom transport receives scrubbed events only', () => {
  const events = [];
  const tracker = createErrorTracker({ dsn: 'provider-dsn', transport: { send: (e) => events.push(e) } });
  tracker.captureMessage('call +44 20 7946 0958', { extra: { user: { id: 1 }, password: 'x', note: 'Bearer abc.def' } });
  assert.equal(events.length, 1);
  assert.equal(events[0].message, `call ${REDACTED}`);
  assert.equal(events[0].extra.user, REDACTED);
  assert.equal(events[0].extra.password, REDACTED);
  assert.equal(events[0].extra.note, `Bearer ${REDACTED}`);
});

test('scrubbing: PII-like keys, URLs, tokens, cycles and unknown fields', () => {
  for (const key of ['email', 'User_Email', 'phoneNumber', 'firstName', 'ip', 'authorization', 'sessionId', 'api_key', 'cookie']) {
    assert.equal(isPiiLikeKey(key), true, key);
  }
  for (const key of ['count', 'area', 'status', 'route']) assert.equal(isPiiLikeKey(key), false, key);

  assert.equal(scrubString('GET https://app.test/reset?token=abc#x failed'), 'GET https://app.test/reset failed');
  assert.equal(scrubString('jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl'), `jwt ${REDACTED}`);
  assert.equal(scrubString('x'.repeat(5000)).length, 2001);

  const cyclic = { a: 1 };
  cyclic.self = cyclic;
  assert.deepEqual(scrub(cyclic), { a: 1, self: '[truncated]' });

  const clean = scrubEvent({ message: 'm', user: { email: 'a@b.co' }, replay: [1], request: { headers: {} } });
  assert.deepEqual(Object.keys(clean), ['message']);
  assert.equal(scrubEvent(null), null);
});

test('browser and node handlers capture errors and uninstall cleanly', () => {
  const events = [];
  const tracker = createErrorTracker({ dsn: 'x', transport: { send: (e) => events.push(e) } });

  const win = fakeTarget();
  const uninstallBrowser = tracker.installBrowserHandlers(win);
  win.dispatch('error', { error: new TypeError('bad') });
  win.dispatch('unhandledrejection', { reason: new Error('rejected') });
  uninstallBrowser();
  win.dispatch('error', { error: new Error('after uninstall') });
  assert.deepEqual(events.map((e) => [e.type, e.message]), [['TypeError', 'bad'], ['Error', 'rejected']]);

  const proc = new EventEmitter();
  const uninstallNode = tracker.installNodeHandlers(proc);
  proc.emit('uncaughtExceptionMonitor', new RangeError('crash'));
  uninstallNode();
  assert.equal(proc.listenerCount('uncaughtExceptionMonitor'), 0);
  assert.equal(events[2].level, 'fatal');
  assert.equal(events[2].message, 'crash');
});

test('maxEvents, sampleRate and a throwing transport never break the app', () => {
  let sent = 0;
  const capped = createErrorTracker({ dsn: 'x', maxEvents: 2, transport: { send: () => { sent += 1; } } });
  for (let i = 0; i < 5; i += 1) capped.captureException(new Error(String(i)));
  assert.equal(sent, 2);

  const sampled = createErrorTracker({ dsn: 'x', sampleRate: 0.5, random: () => 0.9, transport: { send: () => { sent += 1; } } });
  assert.equal(sampled.captureException(new Error('dropped')), false);

  const broken = createErrorTracker({ dsn: 'x', transport: { send: () => { throw new Error('down'); } } });
  assert.equal(broken.captureException(new Error('e')), false);
});
