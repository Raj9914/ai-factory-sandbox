// Opt-in front-end error tracking (no dependencies, provider-agnostic).
//
// Disabled by default: without a DSN no listener is registered, no transport is created
// and no network request is made.
//
//   import { initErrorTracking } from "./observability/browser.js";
//   const tracker = initErrorTracking({ dsn: import.meta.env.VITE_ERROR_TRACKING_DSN });
//
// Events hold only the error type, a scrubbed message and stack, page path (no query
// string), environment and release. Never DOM snapshots, session replay, input values,
// cookies, storage or user records. See README.md in this folder.
//
// Keep SENSITIVE_KEY_PARTS and SENSITIVE_KEYS in sync with scrub.py.

export const REDACTED = "[redacted]";
const MAX_STRING = 2000;
const MAX_DEPTH = 8;
const MAX_ITEMS = 100;

export const SENSITIVE_KEY_PARTS = [
  "password", "passwd", "secret", "token", "apikey", "auth", "cookie", "session",
  "csrf", "xsrf", "email", "phone", "mobile", "ssn", "creditcard", "cardnumber",
  "cvv", "cvc", "iban", "birth", "postcode", "zipcode", "address", "forwardedfor",
  "privatekey", "signature",
];
export const SENSITIVE_KEYS = [
  "ip", "ipaddr", "name", "firstname", "lastname", "fullname", "username", "user",
  "login", "dob", "card", "pan", "pin", "otp", "street", "city", "zip", "body",
  "payload", "headers", "query", "querystring", "params", "formdata",
];

const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const BEARER = /\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}/gi;
const JWT = /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}/g;
const URL_USERINFO = /\b([a-z][a-z0-9+.-]*:\/\/)[^/\s:@]+(?::[^/\s@]*)?@/gi;
const QUERY_VALUE = /([?&][^=&#\s]+=)[^&#\s]*/g;
const IPV4 = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
const IPV6 = /\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b|\b(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?/g;
const PHONE = /\+\d[\d\s().-]{7,}\d/g;
const CARD = /\b\d(?:[ -]?\d){12,18}\b/g;

export function isSensitiveKey(key) {
  const norm = String(key).toLowerCase().replace(/[-_.\s]/g, "");
  return SENSITIVE_KEYS.includes(norm) || SENSITIVE_KEY_PARTS.some((part) => norm.includes(part));
}

function luhn(digits) {
  let total = 0;
  [...digits].reverse().forEach((ch, i) => {
    let n = Number(ch);
    if (i % 2) n = n > 4 ? n * 2 - 9 : n * 2;
    total += n;
  });
  return total % 10 === 0;
}

export function scrubText(text) {
  let out = String(text)
    .replace(URL_USERINFO, `$1${REDACTED}@`)
    .replace(QUERY_VALUE, `$1${REDACTED}`)
    .replace(JWT, REDACTED)
    .replace(BEARER, `$1 ${REDACTED}`)
    .replace(EMAIL, REDACTED)
    .replace(PHONE, REDACTED)
    .replace(CARD, (m) => (luhn(m.replace(/\D/g, "")) ? REDACTED : m))
    .replace(IPV4, REDACTED)
    .replace(IPV6, REDACTED);
  if (out.length > MAX_STRING) out = `${out.slice(0, MAX_STRING)}...[truncated]`;
  return out;
}

export function scrub(value, depth = 0) {
  if (depth > MAX_DEPTH) return REDACTED;
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean" || typeof value === "number") return value;
  if (typeof value === "string") return scrubText(value);
  if (Array.isArray(value)) return value.slice(0, MAX_ITEMS).map((item) => scrub(item, depth + 1));
  if (typeof value === "object") {
    const out = {};
    Object.keys(value).slice(0, MAX_ITEMS).forEach((key) => {
      out[key] = isSensitiveKey(key) ? REDACTED : scrub(value[key], depth + 1);
    });
    return out;
  }
  return scrubText(String(value));
}

function validateDsn(dsn) {
  let url;
  try {
    url = new URL(dsn);
  } catch {
    throw new Error("error tracking DSN must be an https:// URL");
  }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (!(url.protocol === "https:" || (url.protocol === "http:" && local))) {
    throw new Error("error tracking DSN must be an https:// URL (http:// only for localhost)");
  }
  return dsn;
}

// Default transport: fetch without cookies or credentials. Failures are swallowed.
export function httpTransport(dsn) {
  validateDsn(dsn);
  return (event) => {
    try {
      return globalThis
        .fetch(dsn, {
          method: "POST",
          body: JSON.stringify(event),
          headers: { "Content-Type": "application/json" },
          credentials: "omit",
          keepalive: true,
        })
        .catch(() => undefined);
    } catch {
      return undefined;
    }
  };
}

function pagePath(target) {
  try {
    return target.location ? target.location.pathname : null;
  } catch {
    return null;
  }
}

const DISABLED = Object.freeze({
  enabled: false,
  captureException: () => null,
  captureMessage: () => null,
  close: () => undefined,
});

export function initErrorTracking({ dsn, environment = null, release = null, transport, target = globalThis } = {}) {
  const trimmed = typeof dsn === "string" ? dsn.trim() : "";
  if (!trimmed) return DISABLED;
  const send = transport || httpTransport(trimmed);

  const base = (level, context) => ({
    timestamp: Date.now() / 1000,
    level,
    platform: "frontend",
    environment,
    release,
    page: pagePath(target),
    context: scrub(context || {}),
  });
  const dispatch = (event) => {
    try {
      send(event);
    } catch {
      // error reporting must never break the page
    }
    return event;
  };

  const captureException = (error, { context, level = "error" } = {}) => {
    if (error === null || error === undefined) return null;
    const event = base(level, context);
    event.exception = {
      type: (error && error.name) || typeof error,
      value: scrubText(error && error.message !== undefined ? error.message : error),
      stack: error && error.stack ? scrubText(error.stack) : null,
    };
    return dispatch(event);
  };
  const captureMessage = (message, { context, level = "info" } = {}) => {
    const event = base(level, context);
    event.message = scrubText(message);
    return dispatch(event);
  };

  const onError = (e) => captureException(e && e.error ? e.error : new Error(e && e.message ? e.message : "error"));
  const onRejection = (e) => captureException(e ? e.reason : null);
  const listening = target && typeof target.addEventListener === "function";
  if (listening) {
    target.addEventListener("error", onError);
    target.addEventListener("unhandledrejection", onRejection);
  }

  return {
    enabled: true,
    captureException,
    captureMessage,
    close() {
      if (listening) {
        target.removeEventListener("error", onError);
        target.removeEventListener("unhandledrejection", onRejection);
      }
    },
  };
}
