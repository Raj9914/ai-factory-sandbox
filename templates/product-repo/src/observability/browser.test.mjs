// Front-end error tracking tests (Node's built-in test runner; no packages needed).
// Run from the repository root: node --test src/observability/
import { test } from "node:test";
import assert from "node:assert/strict";

import { REDACTED, initErrorTracking, httpTransport, scrub, scrubText } from "./browser.js";

function fakeWindow() {
  const listeners = {};
  return {
    location: { pathname: "/checkout", search: "?email=a@b.co" },
    listeners,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
    count() { return Object.values(listeners).reduce((n, l) => n + l.length, 0); },
  };
}

function guardNetwork() {
  const calls = [];
  const saved = { fetch: globalThis.fetch, XMLHttpRequest: globalThis.XMLHttpRequest, navigator: globalThis.navigator };
  globalThis.fetch = (...args) => { calls.push(["fetch", args]); throw new Error("network call attempted"); };
  globalThis.XMLHttpRequest = function XMLHttpRequest() { calls.push(["xhr"]); throw new Error("network call attempted"); };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true, value: { sendBeacon: (...args) => { calls.push(["beacon", args]); return false; } },
  });
  return {
    calls,
    restore() {
      globalThis.fetch = saved.fetch;
      globalThis.XMLHttpRequest = saved.XMLHttpRequest;
      Object.defineProperty(globalThis, "navigator", { configurable: true, value: saved.navigator });
    },
  };
}

test("no network calls and no listeners when DSN unset", () => {
  const guard = guardNetwork();
  try {
    for (const dsn of [undefined, null, "", "   "]) {
      const win = fakeWindow();
      const tracker = initErrorTracking({ dsn, target: win });
      assert.equal(tracker.enabled, false);
      assert.equal(tracker.captureException(new Error("boom")), null);
      assert.equal(tracker.captureMessage("hello"), null);
      tracker.close();
      assert.equal(win.count(), 0);
    }
    assert.equal(initErrorTracking().enabled, false);
  } finally {
    guard.restore();
  }
  assert.deepEqual(guard.calls, []);
});

test("custom transport is ignored without DSN", () => {
  const sent = [];
  const tracker = initErrorTracking({ transport: (e) => sent.push(e), target: fakeWindow() });
  tracker.captureMessage("x");
  assert.deepEqual(sent, []);
});

test("enabled tracker sends scrubbed events without query strings", () => {
  const sent = [];
  const win = fakeWindow();
  const tracker = initErrorTracking({
    dsn: "https://errors.example.invalid/ingest", environment: "beta", release: "1.0.0",
    transport: (e) => sent.push(e), target: win,
  });
  assert.equal(tracker.enabled, true);
  assert.equal(win.count(), 2);
  const event = tracker.captureException(new TypeError("no user jane@example.com"),
    { context: { route: "/checkout", password: "p", cart: { items: 2 } } });
  assert.deepEqual(sent, [event]);
  assert.equal(event.page, "/checkout");
  assert.equal(event.exception.type, "TypeError");
  assert.equal(event.exception.value, `no user ${REDACTED}`);
  assert.deepEqual(event.context, { route: "/checkout", password: REDACTED, cart: { items: 2 } });
  assert.equal(JSON.stringify(event).includes("jane@example.com"), false);
  assert.equal(JSON.stringify(event).includes("a@b.co"), false);
  tracker.close();
  assert.equal(win.count(), 0);
});

test("window error and unhandledrejection are captured", () => {
  const sent = [];
  const win = fakeWindow();
  initErrorTracking({ dsn: "https://errors.example.invalid/ingest", transport: (e) => sent.push(e), target: win });
  win.listeners.error[0]({ error: new Error("bad") });
  win.listeners.unhandledrejection[0]({ reason: new RangeError("worse") });
  assert.deepEqual(sent.map((e) => e.exception.type), ["Error", "RangeError"]);
});

test("transport failure never throws", () => {
  const tracker = initErrorTracking({
    dsn: "https://errors.example.invalid/ingest", transport: () => { throw new Error("down"); }, target: fakeWindow(),
  });
  assert.notEqual(tracker.captureMessage("still fine"), null);
});

test("default transport posts without credentials", async () => {
  const saved = globalThis.fetch;
  const calls = [];
  globalThis.fetch = (url, opts) => { calls.push([url, opts]); return Promise.resolve({ ok: true }); };
  try {
    await httpTransport("https://errors.example.invalid/ingest")({ level: "error" });
  } finally {
    globalThis.fetch = saved;
  }
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].credentials, "omit");
  assert.deepEqual(JSON.parse(calls[0][1].body), { level: "error" });
});

test("insecure DSN rejected", () => {
  for (const dsn of ["http://errors.example.invalid/x", "not a url"]) {
    assert.throws(() => initErrorTracking({ dsn, target: fakeWindow() }));
  }
  assert.equal(initErrorTracking({ dsn: "http://localhost:8080/x", transport: () => {}, target: fakeWindow() }).enabled, true);
});

test("scrub matches the Python rules", () => {
  assert.equal(scrubText("GET /reset?token=abc&uid=4"), `GET /reset?token=${REDACTED}&uid=${REDACTED}`);
  assert.equal(scrubText("Bearer abcdefghijklmnop"), `Bearer ${REDACTED}`);
  assert.equal(scrubText("client 198.51.100.7 and 2001:db8::1"), `client ${REDACTED} and ${REDACTED}`);
  assert.equal(scrubText("card 4111 1111 1111 1111"), `card ${REDACTED}`);
  assert.equal(scrubText("order 1234567890123 at 10:00:00"), "order 1234567890123 at 10:00:00");
  assert.deepEqual(scrub({ "Set-Cookie": "a", user: { id: 1 }, filename: "app.js", n: 3 }),
    { "Set-Cookie": REDACTED, user: REDACTED, filename: "app.js", n: 3 });
});
