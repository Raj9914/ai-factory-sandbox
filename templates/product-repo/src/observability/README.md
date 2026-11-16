# Optional error tracking

Opt-in error reporting for beta users, so runtime errors are seen and not only health-check
failures. **Off by default.** With no DSN configured, nothing is sent, no transport is
created, no browser listener is registered and no network call is made (tested).

| File | Purpose |
|---|---|
| `error_tracking.py` | Back-end tracker (Python stdlib only) |
| `scrub.py` | PII scrubbing rules applied to every event |
| `browser.js` | Front-end tracker (dependency-free ES module) |
| `test_error_tracking.py`, `browser.test.mjs` | Tests, including "no network calls when DSN unset" |

Delete this folder if the product does not need it (rollback is removing the module).

## Choosing a provider
A human chooses the provider and creates the account. The module is provider-agnostic:

- **Generic HTTPS endpoint:** set the DSN to the ingest URL; events are POSTed as JSON
  (2 s timeout, failures ignored, browser requests sent without cookies).
- **Provider SDK (Sentry, Rollbar, Bugsnag, self-hosted, ...):** pass a `transport`
  function that forwards the already-scrubbed event to the SDK. Configure the SDK
  itself with PII off (for example `send_default_pii=False`) and session replay off.

## Configuration
Configure through environment / build variables only. Never commit a DSN: store it as a
deployment secret. DSNs must be `https://` (`http://` only for `localhost`).

| Variable | Meaning |
|---|---|
| `ERROR_TRACKING_DSN` | Enables tracking when set and non-empty |
| `ERROR_TRACKING_ENVIRONMENT` | Optional, for example `preview`, `beta` |
| `ERROR_TRACKING_RELEASE` | Optional, for example the git SHA |

Back end:
```python
from observability import error_tracking

tracker = error_tracking.init()   # disabled no-op tracker when ERROR_TRACKING_DSN is unset
tracker.install_excepthook()      # optional: report uncaught exceptions
try:
    handle_request()
except Exception as exc:
    tracker.capture_exception(exc, context={"route": "/checkout"})
    raise
```

Front end (pass the DSN from your bundler's public env, for example Vite):
```js
import { initErrorTracking } from "./observability/browser.js";
const tracker = initErrorTracking({ dsn: import.meta.env.VITE_ERROR_TRACKING_DSN });
// Registers window "error" and "unhandledrejection" listeners only when enabled.
tracker.captureMessage("checkout loaded", { context: { step: 2 } });
```

## What is sent, and what is never sent
Sent: exception type, scrubbed message, stack (back end: file basename, function and line
only; front end: scrubbed stack text), page path without query string, level, timestamp,
environment, release and caller-supplied context after scrubbing.

Never sent: local variables, source lines, request or response bodies, headers, cookies,
local/session storage, form or input values, DOM snapshots, user records. **No session
replay**, and do not add one.

## PII scrubbing
`scrub.py` and `browser.js` share the same rules (a test keeps the key lists in sync):

- Values of sensitive keys are replaced with `[redacted]`: anything containing
  `password`, `secret`, `token`, `auth`, `cookie`, `session`, `email`, `phone`, `address`,
  `card` numbers, `iban`, `birth`, ..., plus exact keys such as `ip`, `name`, `user`,
  `username`, `body`, `headers`, `query`, `params`.
- Free text has emails, bearer/basic tokens, JWTs, URL credentials, URL query values,
  IPv4/IPv6 addresses, international phone numbers and Luhn-valid card numbers redacted.
- Strings are capped at 2000 characters; nesting depth and collection sizes are capped.

Add product-specific fields to both key lists when the product handles other personal data.

## Tests
From the product repository root:
```bash
python3 -B -m unittest discover -s src/observability -t src -v
node --test src/observability/
```
Add both to `make test` once the product adopts the module.
