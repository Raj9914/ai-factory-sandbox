"""PII scrubbing for error events (stdlib only).

Every event passes through :func:`scrub` before it leaves the process. The rules are
deliberately aggressive: a redacted field costs a little debugging context, a leaked
email address or token costs a privacy incident.

Keep ``SENSITIVE_KEY_PARTS`` and ``SENSITIVE_KEYS`` in sync with ``browser.js``
(``test_error_tracking.py`` checks this).
"""
import re

REDACTED = "[redacted]"
MAX_STRING = 2000
MAX_DEPTH = 8
MAX_ITEMS = 100

# A key is sensitive when its normalised form (lowercase, without "-", "_", "." or
# spaces) contains one of these parts ...
SENSITIVE_KEY_PARTS = (
    "password", "passwd", "secret", "token", "apikey", "auth", "cookie", "session",
    "csrf", "xsrf", "email", "phone", "mobile", "ssn", "creditcard", "cardnumber",
    "cvv", "cvc", "iban", "birth", "postcode", "zipcode", "address", "forwardedfor",
    "privatekey", "signature",
)
# ... or equals one of these (too short or too generic to match as a substring).
SENSITIVE_KEYS = (
    "ip", "ipaddr", "name", "firstname", "lastname", "fullname", "username", "user",
    "login", "dob", "card", "pan", "pin", "otp", "street", "city", "zip", "body",
    "payload", "headers", "query", "querystring", "params", "formdata",
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+(?::[^/\s@]*)?@")
_QUERY_VALUE = re.compile(r"([?&][^=&#\s]+=)[^&#\s]*")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Full eight-group form, or a compressed "::" form (so clock times like 10:00:00 survive).
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"
                   r"|\b(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?")
_PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d")
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def is_sensitive_key(key) -> bool:
    norm = re.sub(r"[-_.\s]", "", str(key).lower())
    return norm in SENSITIVE_KEYS or any(part in norm for part in SENSITIVE_KEY_PARTS)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card(match):
    digits = re.sub(r"\D", "", match.group(0))
    return REDACTED if _luhn(digits) else match.group(0)


def scrub_text(text: str) -> str:
    """Redact PII-looking substrings from free text and cap its length."""
    text = str(text)
    text = _URL_USERINFO.sub(r"\1" + REDACTED + "@", text)
    text = _QUERY_VALUE.sub(r"\1" + REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(r"\1 " + REDACTED, text)
    text = _EMAIL.sub(REDACTED, text)
    text = _PHONE.sub(REDACTED, text)
    text = _CARD.sub(_card, text)
    text = _IPV4.sub(REDACTED, text)
    text = _IPV6.sub(REDACTED, text)
    if len(text) > MAX_STRING:
        text = text[:MAX_STRING] + "...[truncated]"
    return text


def scrub(value, _depth: int = 0):
    """Return a scrubbed, JSON-safe copy of ``value``. The input is never modified."""
    if _depth > MAX_DEPTH:
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        out = {}
        for i, (key, item) in enumerate(value.items()):
            if i >= MAX_ITEMS:
                out["..."] = "[truncated]"
                break
            key = str(key)
            out[key] = REDACTED if is_sensitive_key(key) else scrub(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [scrub(item, _depth + 1) for item in list(value)[:MAX_ITEMS]]
    # Unknown objects are stringified (never pickled or introspected) and then scrubbed.
    return scrub_text(repr(value))
