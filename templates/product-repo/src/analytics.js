// Ship48 analytics wrapper (PostHog), driven by analytics/events.json.
//
// Rules (ADR 0012):
//   - Only events and properties defined in analytics/events.json are sent.
//     Unknown or invalid ones throw in development and are dropped in production.
//   - No PII: PII-like property names (email, phone, name, ...) and email-like
//     values are always rejected. The distinct ID is a random anonymous UUID.
//   - Autocapture, pageview capture and session recording are off.
//   - No PUBLIC_POSTHOG_KEY, or Do-Not-Track / Global Privacy Control on: no-op,
//     zero network requests. Rollback = unset the key.
//   - Only the public project key belongs in the frontend; never a personal API key.
//
// Usage:
//   import spec from '../analytics/events.json';
//   import { createAnalytics } from './analytics.js';
//   const analytics = createAnalytics({ spec, key: import.meta.env.PUBLIC_POSTHOG_KEY,
//                                       mode: import.meta.env.MODE });
//   analytics.track('app_opened', { source: 'direct' });
//
// By default events go straight to PostHog's capture endpoint with fetch (no SDK, so
// nothing can autocapture or record). To use posthog-js instead, initialise it with
// POSTHOG_INIT_OPTIONS and pass it as `client`; track() still enforces the spec.

export const DEFAULT_POSTHOG_HOST = 'https://eu.i.posthog.com';
export const ANON_ID_STORAGE_KEY = 'ship48_anon_id';

export const POSTHOG_INIT_OPTIONS = Object.freeze({
  autocapture: false,
  capture_pageview: false,
  capture_pageleave: false,
  disable_session_recording: true,
  disable_surveys: true,
  enable_heatmaps: false,
  person_profiles: 'never',
  respect_dnt: true,
  ip: false,
});

// Property names that look like personal data. Compared after lower-casing and
// removing separators, so `Email`, `user_email` and `e-mail` are all caught.
const PII_EXACT = new Set([
  'name', 'firstname', 'lastname', 'fullname', 'username', 'displayname',
  'address', 'streetaddress', 'postcode', 'zipcode', 'zip', 'ip', 'ipaddress',
  'dob', 'dateofbirth', 'birthday', 'ssn', 'password', 'latitude', 'longitude',
  'lat', 'lng', 'lon',
]);
const PII_SUBSTRINGS = ['email', 'mail', 'phone','password', 'passport', 'creditcard', 'iban'];
const EMAIL_VALUE = /[^\s@]+@[^\s@]+\.[^\s@]+/;
const TYPES = new Set(['string', 'number', 'integer', 'boolean']);

export class AnalyticsSpecError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AnalyticsSpecError';
  }
}

export function isPiiLikePropertyName(propName) {
  const normalised = String(propName).toLowerCase().replace(/[^a-z0-9]/g, '');
  if (PII_EXACT.has(normalised)) return true;
  if (normalised.endsWith('name') && normalised !== 'name' && /^(first|last|full|user|display|real|given|family|sur)/.test(normalised)) return true;
  return PII_SUBSTRINGS.some((part) => normalised.includes(part));
}

function matchesType(value, type) {
  switch (type) {
    case 'string': return typeof value === 'string';
    case 'number': return typeof value === 'number' && Number.isFinite(value);
    case 'integer': return Number.isInteger(value);
    case 'boolean': return typeof value === 'boolean';
    default: return false;
  }
}

// Throws AnalyticsSpecError if the spec itself is malformed or declares PII-like properties.
export function validateSpec(spec) {
  if (!spec || typeof spec !== 'object' || !spec.events || typeof spec.events !== 'object') {
    throw new AnalyticsSpecError('spec must be an object with an "events" map');
  }
  const checkProps = (where, props) => {
    for (const [propName, def] of Object.entries(props || {})) {
      if (isPiiLikePropertyName(propName)) throw new AnalyticsSpecError(`${where}: property "${propName}" looks like PII`);
      if (!def || !TYPES.has(def.type)) throw new AnalyticsSpecError(`${where}: property "${propName}" has unknown type`);
      if (def.values !== undefined && !Array.isArray(def.values)) throw new AnalyticsSpecError(`${where}: "${propName}.values" must be an array`);
    }
  };
  checkProps('common_properties', spec.common_properties);
  for (const [eventName, event] of Object.entries(spec.events)) {
    if (!/^[a-z][a-z0-9_]*$/.test(eventName)) throw new AnalyticsSpecError(`event "${eventName}" must be snake_case`);
    checkProps(`event "${eventName}"`, event && event.properties);
  }
  return spec;
}

// Returns a list of problems (empty when the event and properties match the spec).
export function checkEvent(spec, eventName, props = {}) {
  const event = Object.prototype.hasOwnProperty.call(spec.events, eventName) ? spec.events[eventName] : undefined;
  if (!event) return [`unknown event "${eventName}"`];
  if (props === null || typeof props !== 'object' || Array.isArray(props)) return ['properties must be a plain object'];
  const defs = { ...(spec.common_properties || {}), ...(event.properties || {}) };
  const problems = [];
  for (const [propName, value] of Object.entries(props)) {
    if (isPiiLikePropertyName(propName)) { problems.push(`property "${propName}" looks like PII`); continue; }
    const def = Object.prototype.hasOwnProperty.call(defs, propName) ? defs[propName] : undefined;
    if (!def) { problems.push(`unknown property "${propName}" for event "${eventName}"`); continue; }
    if (!matchesType(value, def.type)) { problems.push(`property "${propName}" must be ${def.type}`); continue; }
    if (def.values && !def.values.includes(value)) problems.push(`property "${propName}" has a value outside the allowed list`);
    if (typeof value === 'string' && EMAIL_VALUE.test(value)) problems.push(`property "${propName}" value looks like an email address`);
  }
  for (const [propName, def] of Object.entries(defs)) {
    if (def.required && !Object.prototype.hasOwnProperty.call(props, propName)) {
      problems.push(`missing required property "${propName}" for event "${eventName}"`);
    }
  }
  return problems;
}

export function isDoNotTrack(nav, win) {
  const flags = [nav && nav.doNotTrack, nav && nav.msDoNotTrack, win && win.doNotTrack];
  return flags.some((flag) => flag === '1' || flag === 'yes') || Boolean(nav && nav.globalPrivacyControl === true);
}

function randomId(cryptoImpl) {
  if (cryptoImpl && typeof cryptoImpl.randomUUID === 'function') return cryptoImpl.randomUUID();
  const bytes = new Uint8Array(16);
  if (cryptoImpl && typeof cryptoImpl.getRandomValues === 'function') cryptoImpl.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

// Random anonymous ID, kept in localStorage when available so return visits can be
// counted; never derived from user data.
function anonymousId(storage, cryptoImpl) {
  try {
    const existing = storage && storage.getItem(ANON_ID_STORAGE_KEY);
    if (existing) return existing;
  } catch { /* storage blocked: fall through to an in-memory ID */ }
  const id = randomId(cryptoImpl);
  try { if (storage) storage.setItem(ANON_ID_STORAGE_KEY, id); } catch { /* in-memory only */ }
  return id;
}

function defaultMode() {
  const env = typeof process !== 'undefined' && process.env ? process.env.NODE_ENV : undefined;
  return env === 'production' ? 'production' : 'development';
}

export function createAnalytics(options = {}) {
  const g = typeof globalThis !== 'undefined' ? globalThis : {};
  const {
    spec,
    key,
    host = DEFAULT_POSTHOG_HOST,
    mode = defaultMode(),
    client,
    fetch: fetchImpl = g.fetch,
    navigator: nav = g.navigator,
    window: win = g.window,
    storage = g.localStorage,
    crypto: cryptoImpl = g.crypto,
    onDrop,
  } = options;
  validateSpec(spec);
  const strict = mode !== 'production';
  const enabled = typeof key === 'string' && key.trim() !== '' && !isDoNotTrack(nav, win);
  let distinctId;

  function reject(problems) {
    const message = `analytics: ${problems.join('; ')}`;
    if (strict) throw new AnalyticsSpecError(message);
    if (typeof onDrop === 'function') onDrop(message);
    return false;
  }

  // Returns true when the event was handed to the transport, false when dropped or disabled.
  function track(eventName, props = {}) {
    const problems = checkEvent(spec, eventName, props);
    if (problems.length) return reject(problems);
    if (!enabled) return false;
    if (!distinctId) distinctId = anonymousId(storage, cryptoImpl);
    const properties = { ...props, $process_person_profile: false, $geoip_disable: true };
    if (client && typeof client.capture === 'function') {
      client.capture(eventName, properties);
      return true;
    }
    if (typeof fetchImpl !== 'function') return false;
    const body = JSON.stringify({ api_key: key, event: eventName, distinct_id: distinctId, properties });
    Promise.resolve()
      .then(() => fetchImpl(`${host.replace(/\/+$/, '')}/i/v0/e/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
        credentials: 'omit',
      }))
      .catch(() => { /* analytics must never break the product */ });
    return true;
  }

  return Object.freeze({ track, enabled });
}
