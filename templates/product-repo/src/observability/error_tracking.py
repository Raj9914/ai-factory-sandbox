"""Opt-in back-end error tracking (stdlib only, provider-agnostic).

Disabled by default. Nothing is sent, no transport is created and no network call is
made unless a DSN is configured (``ERROR_TRACKING_DSN`` or ``init(dsn=...)``).

    from observability import error_tracking

    tracker = error_tracking.init()          # reads ERROR_TRACKING_DSN; no-op when unset
    try:
        handle_request()
    except Exception as exc:
        tracker.capture_exception(exc, context={"route": "/checkout"})
        raise

Events contain only the exception type, a scrubbed message, a stack of
(file, function, line) frames, environment, release and scrubbed caller context.
Never local variables, source lines, request bodies, headers, cookies or user records.
See ``README.md`` in this folder.
"""
import json
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request

from .scrub import scrub, scrub_text

DSN_ENV = "ERROR_TRACKING_DSN"
ENVIRONMENT_ENV = "ERROR_TRACKING_ENVIRONMENT"
RELEASE_ENV = "ERROR_TRACKING_RELEASE"
LEVELS = ("debug", "info", "warning", "error", "fatal")
MAX_FRAMES = 50


def validate_dsn(dsn: str) -> str:
    """Return the DSN if it is an https URL (http only for localhost), else raise ValueError."""
    parsed = urllib.parse.urlsplit(dsn)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if not parsed.hostname or parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not local):
        raise ValueError(f"{DSN_ENV} must be an https:// URL (http:// only for localhost)")
    return dsn


class HttpTransport:
    """POST each event as JSON to the DSN. Short timeout; failures never raise."""

    def __init__(self, dsn: str, timeout: float = 2.0):
        self.dsn = validate_dsn(dsn)
        self.timeout = timeout

    def __call__(self, event: dict) -> bool:
        request = urllib.request.Request(
            self.dsn, data=json.dumps(event).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 (validated scheme)
                return 200 <= response.status < 300
        except Exception:  # error reporting must never take the app down
            return False


def _frames(tb) -> list:
    frames = []
    for frame in traceback.extract_tb(tb)[-MAX_FRAMES:]:
        # Basename only: absolute paths can contain the machine user's name.
        frames.append({"file": os.path.basename(frame.filename), "function": frame.name, "line": frame.lineno})
    return frames


class ErrorTracker:
    """Error tracker. ``enabled`` is False (and every method a no-op) without a DSN."""

    def __init__(self, dsn=None, *, environment=None, release=None, transport=None):
        self.dsn = dsn or None
        self.environment = environment
        self.release = release
        self._transport = None
        if self.dsn:
            self._transport = transport if transport is not None else HttpTransport(self.dsn)

    @property
    def enabled(self) -> bool:
        return self._transport is not None

    def _base(self, level: str, context) -> dict:
        if level not in LEVELS:
            level = "error"
        return {
            "timestamp": round(time.time(), 3),
            "level": level,
            "platform": "backend",
            "environment": self.environment,
            "release": self.release,
            "context": scrub(context or {}),
        }

    def _send(self, event: dict):
        try:
            self._transport(event)
        except Exception:
            pass
        return event

    def capture_exception(self, exc: BaseException, *, context=None, level: str = "error"):
        """Send a scrubbed exception event. Returns the event sent, or None when disabled."""
        if not self.enabled or exc is None:
            return None
        event = self._base(level, context)
        event["exception"] = {
            "type": type(exc).__name__,
            "value": scrub_text(str(exc)),
            "frames": _frames(exc.__traceback__),
        }
        return self._send(event)

    def capture_message(self, message: str, *, context=None, level: str = "info"):
        """Send a scrubbed message event. Returns the event sent, or None when disabled."""
        if not self.enabled:
            return None
        event = self._base(level, context)
        event["message"] = scrub_text(message)
        return self._send(event)

    def install_excepthook(self) -> None:
        """Report uncaught exceptions, then defer to the previous hook. No-op when disabled."""
        if not self.enabled:
            return
        previous = sys.excepthook

        def hook(exc_type, exc, tb):
            if not issubclass(exc_type, KeyboardInterrupt):
                self.capture_exception(exc, level="fatal")
            previous(exc_type, exc, tb)

        sys.excepthook = hook


def init(dsn=None, *, environment=None, release=None, transport=None, env=None) -> ErrorTracker:
    """Build a tracker from arguments, falling back to environment variables.

    Returns a disabled no-op tracker when no DSN is configured.
    """
    env = os.environ if env is None else env
    return ErrorTracker(
        (dsn or env.get(DSN_ENV, "")).strip() or None,
        environment=environment or env.get(ENVIRONMENT_ENV) or None,
        release=release or env.get(RELEASE_ENV) or None,
        transport=transport,
    )
