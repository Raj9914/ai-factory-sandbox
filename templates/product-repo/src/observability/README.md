# Error tracking (optional, opt-in)

`errors.js` captures runtime errors from beta users on the front end and the back end,
so you see more than health-check failures. It is **off by default**.

| Setting | Effect |
|---|---|
| DSN unset, empty or whitespace | No-op. No transport is created, no handler does anything, **no network request is made**. |
| DSN set | Errors are scrubbed (see below), then handed to the transport. |

Rollback: unset the DSN (or remove `src/observability/`).

## Choosing a provider

The provider is a human decision (account setup is out of scope for the template).
Keep the DSN in the deploy environment, never in the repository. A front-end DSN is
public by design; a back-end DSN still belongs in secrets.

- **Built-in transport:** with only `dsn`, each event is POSTed as JSON to the DSN URL
  (`credentials: 'omit'`). Use it with any endpoint that accepts JSON.
- **Provider SDK:** pass `transport: { send(event) { /* call the SDK */ } }`. Events reach
  `send` already scrubbed. Also call `scrubEvent` from the SDK's own `beforeSend` hook
  and turn off its PII and replay features, for example in Sentry:
  `sendDefaultPii: false`, `replaysSessionSampleRate: 0`, `replaysOnErrorSampleRate: 0`,
  no Replay integration, `beforeSend: (e) => scrubEvent(e)` (adapt to the SDK's event shape).

## Usage

```js
import { createErrorTracker } from './observability/errors.js';

// Front end
const errors = createErrorTracker({
  dsn: import.meta.env.PUBLIC_ERROR_DSN,  // unset = off
  environment: import.meta.env.MODE,
  release: import.meta.env.PUBLIC_RELEASE,
});
errors.installBrowserHandlers();          // window 'error' + 'unhandledrejection'

// Back end
const serverErrors = createErrorTracker({ dsn: process.env.ERROR_DSN, environment: process.env.NODE_ENV });
serverErrors.installNodeHandlers();       // observes uncaught exceptions; Node still crashes as usual

// Anywhere
try { risky(); } catch (err) { errors.captureException(err, { tags: { area: 'checkout' } }); }
```

Options: `dsn`, `environment`, `release`, `transport`, `sampleRate` (0–1, default 1),
`maxEvents` (per tracker/page load, default 50), `fetch` (test injection).

## PII scrubbing

Every event passes `scrubEvent` before any transport:

- Only these fields are kept: `type`, `message`, `stack`, `level`, `timestamp`,
  `environment`, `release`, `runtime`, `url`, `tags`, `extra`. There is no user object.
- Keys that look like personal data or secrets (`email`, `phone`, `name`, `address`, `ip`,
  `password`, `token`, `session`, `cookie`, `authorization`, `body`, ...) are replaced
  with `[redacted]` at any depth.
- In strings: email addresses, IPv4/IPv6 addresses, phone-like numbers, JWTs and
  `Bearer`/`Basic` credentials are masked; URL query strings and fragments are dropped;
  strings are truncated to 2000 characters.

Do not put user input in error messages, tags or `extra` anyway; scrubbing is a safety net.

## Not included, on purpose

No session replay, no breadcrumbs of clicks or input, no user identification, no IP
collection. Do not enable them in a provider SDK without an owned, reviewed task.

## Tests

`npm test -- observability` (or `make test`) runs `tests/observability.test.js`, which
includes a test that no network call is made when the DSN is unset.
