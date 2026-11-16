"""Observability tests (stdlib unittest; no third-party packages needed).

Run from the repository root:
    python3 -B -m unittest discover -s src/observability -t src -v
"""
import json
import re
import socket
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from observability import error_tracking, scrub as scrub_module
from observability.error_tracking import ErrorTracker, HttpTransport, init
from observability.scrub import REDACTED, scrub, scrub_text

HERE = Path(__file__).resolve().parent


def _no_network(*_args, **_kwargs):
    raise AssertionError("network call attempted")


class NetworkGuard:
    """Patch every stdlib path to the network so any attempt fails the test."""

    def __enter__(self):
        self._patches = [
            mock.patch.object(socket, "socket", side_effect=_no_network),
            mock.patch.object(socket, "create_connection", side_effect=_no_network),
            mock.patch.object(socket, "getaddrinfo", side_effect=_no_network),
            mock.patch.object(urllib.request, "urlopen", side_effect=_no_network),
        ]
        self.mocks = [p.start() for p in self._patches]
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()

    @property
    def calls(self) -> int:
        return sum(m.call_count for m in self.mocks)


def _raise(exc):
    try:
        raise exc
    except Exception as caught:
        return caught


class DisabledByDefaultTest(unittest.TestCase):
    def test_no_network_calls_when_dsn_unset(self):
        with NetworkGuard() as guard:
            for env in ({}, {"ERROR_TRACKING_DSN": ""}, {"ERROR_TRACKING_DSN": "   "}):
                tracker = init(env=env)
                self.assertFalse(tracker.enabled)
                self.assertIsNone(tracker.capture_exception(_raise(RuntimeError("boom"))))
                self.assertIsNone(tracker.capture_message("hello", context={"a": 1}))
                tracker.install_excepthook()
        self.assertEqual(guard.calls, 0)

    def test_disabled_never_builds_transport_or_hook(self):
        hook = sys.excepthook
        with mock.patch.object(error_tracking, "HttpTransport", side_effect=_no_network) as transport:
            tracker = init(env={})
            tracker.install_excepthook()
        self.assertEqual(transport.call_count, 0)
        self.assertIs(sys.excepthook, hook)

    def test_custom_transport_ignored_without_dsn(self):
        sent = []
        tracker = init(env={}, transport=sent.append)
        self.assertFalse(tracker.enabled)
        tracker.capture_message("x")
        self.assertEqual(sent, [])

    def test_reads_environment_variable(self):
        sent = []
        tracker = init(env={"ERROR_TRACKING_DSN": "https://errors.example.invalid/ingest",
                            "ERROR_TRACKING_ENVIRONMENT": "beta", "ERROR_TRACKING_RELEASE": "1.2.0"},
                       transport=sent.append)
        self.assertTrue(tracker.enabled)
        tracker.capture_message("started")
        self.assertEqual(sent[0]["environment"], "beta")
        self.assertEqual(sent[0]["release"], "1.2.0")


class EnabledTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.tracker = ErrorTracker("https://errors.example.invalid/ingest", transport=self.sent.append)

    def test_exception_event_is_scrubbed_and_has_no_locals(self):
        secret_local = "local-value-never-sent"  # noqa: F841  (must not appear in the event)
        exc = _raise(ValueError("user jane.doe@example.com failed from 203.0.113.9"))
        event = self.tracker.capture_exception(exc, context={"route": "/checkout", "email": "a@b.co",
                                                             "cart": {"password": "x", "items": 2}})
        self.assertEqual(self.sent, [event])
        self.assertEqual(event["exception"]["type"], "ValueError")
        self.assertEqual(event["exception"]["value"], f"user {REDACTED} failed from {REDACTED}")
        self.assertEqual(event["context"], {"route": "/checkout", "email": REDACTED,
                                            "cart": {"password": REDACTED, "items": 2}})
        frame = event["exception"]["frames"][-1]
        self.assertEqual(set(frame), {"file", "function", "line"})
        self.assertEqual(frame["file"], "test_error_tracking.py")
        self.assertNotIn("local-value-never-sent", json.dumps(event))
        self.assertNotIn(str(HERE), json.dumps(event))

    def test_transport_failure_never_raises(self):
        def broken(_event):
            raise OSError("down")
        tracker = ErrorTracker("https://errors.example.invalid/ingest", transport=broken)
        self.assertIsNotNone(tracker.capture_message("still fine"))

    def test_excepthook_reports_then_chains(self):
        previous = mock.Mock()
        with mock.patch.object(sys, "excepthook", previous):
            self.tracker.install_excepthook()
            exc = _raise(KeyError("k"))
            sys.excepthook(KeyError, exc, exc.__traceback__)
        self.assertEqual(self.sent[0]["level"], "fatal")
        previous.assert_called_once()

    def test_http_transport_posts_json_with_timeout(self):
        response = mock.MagicMock(status=202)
        response.__enter__.return_value = response
        with mock.patch.object(urllib.request, "urlopen", return_value=response) as urlopen:
            ok = HttpTransport("https://errors.example.invalid/ingest", timeout=1.5)({"level": "error"})
        self.assertTrue(ok)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://errors.example.invalid/ingest")
        self.assertEqual(json.loads(request.data), {"level": "error"})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 1.5)

    def test_http_transport_swallows_errors(self):
        with mock.patch.object(urllib.request, "urlopen", side_effect=OSError("refused")):
            self.assertFalse(HttpTransport("https://errors.example.invalid/x")({}))

    def test_rejects_insecure_dsn(self):
        for dsn in ("http://errors.example.invalid/x", "ftp://errors.example.invalid/x", "not a url"):
            with self.assertRaises(ValueError):
                ErrorTracker(dsn)
        self.assertTrue(ErrorTracker("http://localhost:8080/x").enabled)


class ScrubTest(unittest.TestCase):
    def test_text_patterns(self):
        cases = {
            "mail jane.doe+x@example.co.uk now": f"mail {REDACTED} now",
            "Authorization: Bearer abcdefghijklmnop": f"Authorization: Bearer {REDACTED}",
            "GET /reset?token=abc123&uid=42#top": f"GET /reset?token={REDACTED}&uid={REDACTED}#top",
            "https://alice:pw@db.example.invalid/x": f"https://{REDACTED}@db.example.invalid/x",
            "client 198.51.100.7": f"client {REDACTED}",
            "client 2001:db8::1": f"client {REDACTED}",
            "call +44 20 7946 0958": f"call {REDACTED}",
            "card 4111 1111 1111 1111": f"card {REDACTED}",
            "order 1234567890123 shipped": "order 1234567890123 shipped",  # fails Luhn: kept
            "at 10:00:00 retry 3": "at 10:00:00 retry 3",
        }
        for raw, expected in cases.items():
            self.assertEqual(scrub_text(raw), expected, raw)

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOi.eyJzdWIiOiIx.c2lnbmF0dXJl"
        self.assertEqual(scrub_text(f"jwt {jwt}"), f"jwt {REDACTED}")

    def test_sensitive_keys_redacted_recursively(self):
        data = {"Set-Cookie": "a", "X-Api-Key": "b", "user": {"id": 1}, "items": [{"phone_number": "1"}],
                "first_name": "Ada", "filename": "app.py", "count": 3}
        self.assertEqual(scrub(data), {"Set-Cookie": REDACTED, "X-Api-Key": REDACTED, "user": REDACTED,
                                       "items": [{"phone_number": REDACTED}], "first_name": REDACTED,
                                       "filename": "app.py", "count": 3})

    def test_input_not_mutated_and_limits_applied(self):
        data = {"password": "p", "nested": {"email": "x"}}
        scrub(data)
        self.assertEqual(data, {"password": "p", "nested": {"email": "x"}})
        self.assertTrue(scrub_text("a" * 5000).endswith("...[truncated]"))
        deep = current = {}
        for _ in range(20):
            current["n"] = {}
            current = current["n"]
        self.assertIn(REDACTED, json.dumps(scrub(deep)))

    def test_key_lists_match_browser_module(self):
        source = (HERE / "browser.js").read_text(encoding="utf-8")
        for name in ("SENSITIVE_KEY_PARTS", "SENSITIVE_KEYS"):
            match = re.search(rf"export const {name} = \[(.*?)\];", source, re.S)
            self.assertIsNotNone(match, name)
            self.assertEqual(tuple(re.findall(r'"([^"]+)"', match.group(1))), getattr(scrub_module, name))


if __name__ == "__main__":
    unittest.main()
