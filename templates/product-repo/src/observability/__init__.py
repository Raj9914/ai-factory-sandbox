"""Optional observability helpers. Error tracking is off unless ERROR_TRACKING_DSN is set.

PII scrubbing helpers live in ``observability.scrub``.
"""
from .error_tracking import ErrorTracker, HttpTransport, init

__all__ = ["ErrorTracker", "HttpTransport", "init"]
