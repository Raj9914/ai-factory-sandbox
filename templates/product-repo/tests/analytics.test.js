// Tests for src/analytics.js and analytics/events.json. Run: npm test -- analytics
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  AnalyticsSpecError,
  POSTHOG_INIT_OPTIONS,
  createAnalytics,
  isPiiLikePropertyName,
  validateSpec,
} from '../src/analytics.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const spec = JSON.parse(readFileSync(join(root, 'analytics/events.json'), 'utf8'));
const KEY = 'phc_test_public_key';

function fetchMock() {
  const calls = [];
  const fn = async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200 }; };
  fn.calls = calls;
  return fn;
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
const memoryStorage = () => {
  const data = new Map();
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)) };
};
const base = (overrides = {}) => ({ spec, key: KEY, navigator: {}, window: {}, storage: memoryStorage(), ...overrides });

let realFetch;
let globalFetch;
beforeEach(() => {
  realFetch = globalThis.fetch;
  globalFetch = fetchMock();
  globalThis.fetch = globalFetch;
});
afterEach(() => { globalThis.fetch = realFetch; });

// Event names written in backticks in the first column of the "Event catalogue"
// table and anywhere in the "Metric definitions" table (placeholders like
// `<activation event>` are skipped).
function eventsInSpecMarkdown(markdown) {
  const names = new Set();
  let section = '';
  for (const line of markdown.split('\n')) {
    const heading = line.match(/^##\s+(.*)/);
    if (heading) { section = heading[1].trim().toLowerCase(); continue; }
    if (!line.startsWith('|') || /^\|\s*-/.test(line)) continue;
    const cells = line.split('|').slice(1, -1);
    const scope = section === 'event catalogue' ? cells.slice(0, 1) : section === 'metric definitions' ? cells : [];
    for (const cell of scope) {
      for (const m of cell.matchAll(/`([a-z][a-z0-9_]*)`/g)) names.add(m[1]);
    }
  }
  return names;
}

test('analytics: events.json is a valid spec with activation and core_action_completed examples', () => {
  assert.doesNotThrow(() => validateSpec(spec));
  for (const name of ['app_opened', 'activation', 'core_action_completed', 'error_unhandled']) {
    assert.ok(spec.events[name], `missing ${name}`);
  }
  for (const [name, event] of Object.entries(spec.events)) {
    for (const [prop, def] of Object.entries(event.properties || {})) {
      assert.equal(typeof def.required, 'boolean', `${name}.${prop}.required must be boolean`);
    }
  }
});

test('analytics: every event in the spec markdown exists in events.json', () => {
  const docs = ['docs/templates/ANALYTICS_SPEC_TEMPLATE.md', 'docs/ANALYTICS_SPEC.md']
    .map((p) => join(root, p))
    .filter((p) => existsSync(p));
  assert.ok(docs.length > 0, 'no analytics spec markdown found');
  for (const doc of docs) {
    const names = eventsInSpecMarkdown(readFileSync(doc, 'utf8'));
    assert.ok(names.size > 0, `${doc}: no events found`);
    const missing = [...names].filter((n) => !spec.events[n]);
    assert.deepEqual(missing, [], `${doc}: events missing from analytics/events.json`);
  }
});

test('analytics: markdown event parser reads the catalogue and metric tables only', () => {
  const md = [
    '## Principles', '| `not_an_event` |', '',
    '## Event catalogue', '| Event | When |', '|---|---|', '| `signup_done` | `ignored_cell` |', '',
    '## Metric definitions', '| Metric | Numerator |', '|---|---|', '| x | users firing `<activation event>` or `paid` |',
  ].join('\n');
  assert.deepEqual([...eventsInSpecMarkdown(md)].sort(), ['paid', 'signup_done']);
});

test('analytics: known event with valid properties is sent once to PostHog with an anonymous id', async () => {
  const fetch = fetchMock();
  const analytics = createAnalytics(base({ fetch, mode: 'development', host: 'https://ph.example.test/' }));
  assert.equal(analytics.enabled, true);
  assert.equal(analytics.track('app_opened', { source: 'direct' }), true);
  assert.equal(analytics.track('core_action_completed', { screen_id: 'S-02', duration_ms: 1200, internal: true }), true);
  await flush();
  assert.equal(fetch.calls.length, 2);
  assert.equal(fetch.calls[0].url, 'https://ph.example.test/i/v0/e/');
  assert.equal(fetch.calls[0].init.credentials, 'omit');
  const first = JSON.parse(fetch.calls[0].init.body);
  const second = JSON.parse(fetch.calls[1].init.body);
  assert.equal(first.event, 'app_opened');
  assert.equal(first.api_key, KEY);
  assert.deepEqual(first.properties, { source: 'direct', $process_person_profile: false, $geoip_disable: true });
  assert.match(first.distinct_id, /^[0-9a-f-]{32,36}$/);
  assert.equal(first.distinct_id, second.distinct_id);
});

test('analytics: unknown event throws in development and is dropped in production', async () => {
  const fetch = fetchMock();
  const dev = createAnalytics(base({ fetch, mode: 'development' }));
  assert.throws(() => dev.track('button_clicked', {}), AnalyticsSpecError);
  const dropped = [];
  const prod = createAnalytics(base({ fetch, mode: 'production', onDrop: (m) => dropped.push(m) }));
  assert.equal(prod.track('button_clicked', {}), false);
  assert.equal(prod.track('toString', {}), false);
  await flush();
  assert.equal(fetch.calls.length, 0);
  assert.equal(dropped.length, 2);
  assert.match(dropped[0], /unknown event "button_clicked"/);
});

test('analytics: unknown, missing or wrongly typed properties are rejected', async () => {
  const fetch = fetchMock();
  const dev = createAnalytics(base({ fetch, mode: 'development' }));
  assert.throws(() => dev.track('app_opened', { source: 'direct', plan: 'pro' }), /unknown property "plan"/);
  assert.throws(() => dev.track('app_opened', {}), /missing required property "source"/);
  assert.throws(() => dev.track('app_opened', { source: 42 }), /"source" must be string/);
  assert.throws(() => dev.track('core_action_completed', { screen_id: 'S-02', duration_ms: 1.5 }), /must be integer/);
  const prod = createAnalytics(base({ fetch, mode: 'production' }));
  assert.equal(prod.track('app_opened', { source: 'direct', plan: 'pro' }), false);
  await flush();
  assert.equal(fetch.calls.length, 0);
});

test('analytics: PII-like property names (email, phone, name) and email values are rejected', async () => {
  for (const prop of ['email', 'phone', 'name', 'user_email', 'phoneNumber', 'first_name', 'Full-Name']) {
    assert.equal(isPiiLikePropertyName(prop), true, prop);
  }
  for (const prop of ['source', 'screen_id', 'duration_ms', 'error_type', 'internal']) {
    assert.equal(isPiiLikePropertyName(prop), false, prop);
  }
  const fetch = fetchMock();
  const dev = createAnalytics(base({ fetch, mode: 'development' }));
  for (const prop of ['email', 'phone', 'name']) {
    assert.throws(() => dev.track('app_opened', { source: 'direct', [prop]: 'x' }), new RegExp(`"${prop}" looks like PII`));
  }
  assert.throws(() => dev.track('app_opened', { source: 'jane@example.com' }), /looks like an email address/);
  const prod = createAnalytics(base({ fetch, mode: 'production' }));
  assert.equal(prod.track('app_opened', { source: 'direct', email: 'jane@example.com' }), false);
  await flush();
  assert.equal(fetch.calls.length, 0);
  // A spec that declares a PII-like property is itself invalid.
  const bad = { events: { signup: { properties: { email: { type: 'string', required: true } } } } };
  assert.throws(() => validateSpec(bad), /looks like PII/);
});

test('analytics: zero network requests when PUBLIC_POSTHOG_KEY is unset', async () => {
  const fetch = fetchMock();
  for (const key of [undefined, '', '   ']) {
    const analytics = createAnalytics(base({ key, fetch, mode: 'development' }));
    assert.equal(analytics.enabled, false);
    assert.equal(analytics.track('app_opened', { source: 'direct' }), false);
    assert.equal(analytics.track('activation', { screen_id: 'S-02' }), false);
  }
  // Default transport (global fetch) is not touched either.
  const viaGlobal = createAnalytics({ spec, navigator: {}, window: {}, storage: memoryStorage(), mode: 'production' });
  assert.equal(viaGlobal.track('app_opened', { source: 'direct' }), false);
  await flush();
  assert.equal(fetch.calls.length, 0);
  assert.equal(globalFetch.calls.length, 0);
});

test('analytics: Do-Not-Track and Global Privacy Control turn tracking off', async () => {
  const fetch = fetchMock();
  const cases = [
    { navigator: { doNotTrack: '1' }, window: {} },
    { navigator: {}, window: { doNotTrack: '1' } },
    { navigator: { globalPrivacyControl: true }, window: {} },
  ];
  for (const env of cases) {
    const analytics = createAnalytics(base({ ...env, fetch, mode: 'production' }));
    assert.equal(analytics.enabled, false);
    assert.equal(analytics.track('app_opened', { source: 'direct' }), false);
  }
  await flush();
  assert.equal(fetch.calls.length, 0);
});

test('analytics: autocapture and session recording are off for posthog-js clients', () => {
  assert.equal(POSTHOG_INIT_OPTIONS.autocapture, false);
  assert.equal(POSTHOG_INIT_OPTIONS.disable_session_recording, true);
  assert.equal(POSTHOG_INIT_OPTIONS.capture_pageview, false);
  assert.equal(POSTHOG_INIT_OPTIONS.person_profiles, 'never');
  assert.ok(Object.isFrozen(POSTHOG_INIT_OPTIONS));
  const captured = [];
  const client = { capture: (event, props) => captured.push({ event, props }) };
  const fetch = fetchMock();
  const analytics = createAnalytics(base({ client, fetch, mode: 'development' }));
  assert.equal(analytics.track('activation', { screen_id: 'S-02' }), true);
  assert.throws(() => analytics.track('pageview', {}), AnalyticsSpecError);
  assert.deepEqual(captured, [{ event: 'activation', props: { screen_id: 'S-02', $process_person_profile: false, $geoip_disable: true } }]);
  assert.equal(fetch.calls.length, 0);
});

test('analytics: anonymous id persists in storage and survives blocked storage', async () => {
  const storage = memoryStorage();
  const fetch = fetchMock();
  createAnalytics(base({ storage, fetch })).track('app_opened', { source: 'direct' });
  createAnalytics(base({ storage, fetch })).track('app_opened', { source: 'direct' });
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  createAnalytics(base({ storage: blocked, fetch })).track('app_opened', { source: 'direct' });
  await flush();
  const ids = fetch.calls.map((c) => JSON.parse(c.init.body).distinct_id);
  assert.equal(ids.length, 3);
  assert.equal(ids[0], ids[1]);
  assert.equal(storage.getItem('ship48_anon_id'), ids[0]);
  assert.ok(ids[2]);
});

test('analytics: transport failures never throw into the product', async () => {
  const failing = async () => { throw new Error('offline'); };
  const analytics = createAnalytics(base({ fetch: failing, mode: 'production' }));
  assert.equal(analytics.track('error_unhandled', { error_type: 'TypeError' }), true);
  await flush();
});
