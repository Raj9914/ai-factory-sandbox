// Ship48 opt-in error tracking (front end and back end). See ./README.md.
//
// Rules:
//   - Off by default. No DSN (empty, whitespace or missing): every function is a
//     no-op and no network request is ever made. Rollback = unset the DSN.
//   - Provider-agnostic. The human picks the provider; pass its SDK-backed
//     `transport` ({ send(event) }) or let the built-in transport POST JSON to the DSN URL.
//   - Every event is PII-scrubbed before it reaches any transport (scrubEvent):
//     PII-like keys are redacted, emails / IPs / phone numbers / tokens in strings
//     are masked, URL query strings and fragments are dropped.
//   - Errors only: no session replay, no breadcrumbs of user input, no user object.

export const REDACTED = '[redacted]';
export const MAX_STRING_LENGTH = 2000;
export const DEFAULT_MAX_EVENTS = 50;

// Key names that look like personal data or secrets. Compared after lower-casing and
// removing separators, so `Email`, `user_email` and `e-mail` are all caught.
const PII_EXACT = new Set([
  'name', 'firstname', 'lastname', 'fullname', 'username', 'displayname',
  'address', 'streetaddress', 'postcode', 'zipcode', 'zip', 'ip', 'ipaddress',
  'dob', 'dateofbirth', 'birthday', 'ssn', 'latitude', 'longitude', 'lat', 'lng', 'lon',
  'user', 'cookie', 'cookies', 'setcookie', 'authorization', 'auth', 'body', 'requestbody',
]);
const PII_SUBSTRINGS = [
  'email', 'mail', 'phone', 'password', 'passwd', 'secret', 'token', 'apikey',
  'session', 'passport', 'creditcard', 'cardnumber', 'iban', 'cvv',
];

const EMAIL = /[^\s@"'<>()]+@[^\s@"'<>()]+\.[a-z]{2,}/gi;
const IPV4 = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
const IPV6 = /\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b/gi;
const BEARER = /\b(bearer|basic)\s+[a-z0-9._~+/=-]+/gi;
const JWT = /\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/g;
const PHONE = /\+?\d[\d\s().-]{7,}\d/g;
const URL_QUERY = /(\bhttps?:\/\/[^\s?#"']+)[?#][^\s"']*/gi;

export function isPiiLikeKey(key) {
  const normalised = String(key).toLowerCase().replace(/[^a-z0-9]/g, '');
  if (PII_EXACT.has(normalised)) return true;
  if (normalised.endsWith('name') && /^(first|last|full|user|display|real|given|family|sur)/.test(normalised)) return true;
  return PII_SUBSTRINGS.some((part) => normalised.includes(part));
}

export function scrubString(value) {
  let out = String(value);
  if (out.length > MAX_STRING_LENGTH) out = `${out.slice(0, MAX_STRING_LENGTH)}…`;
  return out
    .replace(URL_QUERY, '$1')
    .replace(BEARER, `$1 ${REDACTED}`)
    .replace(JWT, REDACTED)
    .replace(EMAIL, REDACTED)
    .replace(IPV6, REDACTED)
    .replace(IPV4, REDACTED)
    .replace(PHONE, REDACTED);
}

// Deep-copies `value`, redacting PII-like keys and masking PII in strings.
// Cycles are cut and depth is bounded so a hostile object cannot hang the scrubber.
export function scrub(value, depth = 0, seen = new WeakSet()) {
  if (value === null || value === undefined) return value;
  if (typeof value === 'string') return scrubString(value);
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  if (typeof value === 'bigint') return value.toString();
  if (typeof value !== 'object') return undefined; // functions, symbols
  if (depth >= 6 || seen.has(value)) return '[truncated]';
  seen.add(value);
  if (Array.isArray(value)) return value.slice(0, 50).map((item) => scrub(item, depth + 1, seen));
  const out = {};
  for (const [key, item] of Object.entries(value).slice(0, 100)) {
    out[key] = isPiiLikeKey(key) ? REDACTED : scrub(item, depth + 1, seen);
  }
  return out;
}

// Final gate before any transport: whitelist the event shape, then scrub it.
// Provider SDKs should call this from their `beforeSend` hook as well.
export function scrubEvent(event) {
  if (!event || typeof event !== 'object') return null;
  const scrubbed = ['type', 'message', 'stack', 'url', 'tags', 'extra'];
  // Set by this module or the deploy config, never by users; kept verbatim so a
  // timestamp or version string is not mistaken for a phone number or IP address.
  const verbatim = ['level', 'timestamp', 'environment', 'release', 'runtime'];
  const out = {};
  for (const key of scrubbed) {
    if (event[key] !== undefined) out[key] = scrub(event[key]);
  }
  for (const key of verbatim) {
    if (typeof event[key] === 'string') out[key] = event[key].slice(0, 200);
  }
  return out;
}

export function hasDsn(dsn) {
  return typeof dsn === 'string' && dsn.trim() !== '';
}

function toEvent(error, context, base) {
  const isError = error instanceof Error;
  const g = typeof globalThis !== 'undefined' ? globalThis : {};
  const href = g.location && typeof g.location.href === 'string' ? g.location.href : undefined;
  return {
    type: isError ? error.name : typeof error,
    message: isError ? error.message : String(error),
    stack: isError && typeof error.stack === 'string' ? error.stack : undefined,
    level: (context && context.level) || 'error',
    timestamp: new Date().toISOString(),
    environment: base.environment,
    release: base.release,
    runtime: typeof g.window !== 'undefined' ? 'browser' : 'server',
    url: href,
    tags: context && context.tags,
    extra: context && context.extra,
  };
}

function httpTransport(dsn, fetchImpl) {
  return {
    send(event) {
      if (typeof fetchImpl !== 'function') return;
      Promise.resolve()
        .then(() => fetchImpl(dsn, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(event),
          keepalive: true,
          credentials: 'omit',
        }))
        .catch(() => { /* error reporting must never break the product */ });
    },
  };
}

const NOOP_TRACKER = Object.freeze({
  enabled: false,
  captureException: () => false,
  captureMessage: () => false,
  installBrowserHandlers: () => () => {},
  installNodeHandlers: () => () => {},
});

export function createErrorTracker(options = {}) {
  const { dsn, environment, release, transport, sampleRate = 1, maxEvents = DEFAULT_MAX_EVENTS } = options;
  // Disabled: return the shared no-op before touching fetch, globals or the transport.
  if (!hasDsn(dsn)) return NOOP_TRACKER;
  const fetchImpl = Object.prototype.hasOwnProperty.call(options, 'fetch') ? options.fetch : globalThis.fetch;
  const sink = transport && typeof transport.send === 'function' ? transport : httpTransport(dsn.trim(), fetchImpl);
  const random = typeof options.random === 'function' ? options.random : Math.random;
  const base = { environment, release };
  let sent = 0;

  function send(event) {
    if (sent >= maxEvents) return false;
    if (sampleRate < 1 && random() >= sampleRate) return false;
    const clean = scrubEvent(event);
    if (!clean) return false;
    sent += 1;
    try { sink.send(clean); } catch { return false; }
    return true;
  }

  const captureException = (error, context) => send(toEvent(error, context, base));
  const captureMessage = (message, context) => send({ ...toEvent(String(message), context, base), type: 'message', level: (context && context.level) || 'info' });

  // Browser: window 'error' and 'unhandledrejection'. Returns an uninstall function.
  function installBrowserHandlers(target = globalThis.window) {
    if (!target || typeof target.addEventListener !== 'function') return () => {};
    const onError = (e) => captureException(e && e.error ? e.error : (e && e.message) || 'Unknown error', { tags: { handler: 'window.error' } });
    const onRejection = (e) => captureException(e ? e.reason : 'Unhandled rejection', { tags: { handler: 'unhandledrejection' } });
    target.addEventListener('error', onError);
    target.addEventListener('unhandledrejection', onRejection);
    return () => {
      target.removeEventListener('error', onError);
      target.removeEventListener('unhandledrejection', onRejection);
    };
  }

  // Node: 'uncaughtExceptionMonitor' observes crashes without changing Node's default
  // crash behaviour. Unhandled rejections are left to the app's own handler.
  function installNodeHandlers(target = globalThis.process) {
    if (!target || typeof target.on !== 'function') return () => {};
    const onCrash = (error) => captureException(error, { level: 'fatal', tags: { handler: 'uncaughtException' } });
    target.on('uncaughtExceptionMonitor', onCrash);
    return () => target.removeListener('uncaughtExceptionMonitor', onCrash);
  }

  return Object.freeze({ enabled: true, captureException, captureMessage, installBrowserHandlers, installNodeHandlers });
}
