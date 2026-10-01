"""Public-safe redaction (ported, trimmed, from V2 core/security.py), with meeting credentials added.

The primary log control in V7 is counts-only output with fixed error codes. This is defence in depth for the places free text can leak in: exception messages
and any structured diagnostic. Private meeting credentials (passcode, meeting id, join token) are sensitive evidence: they may live only on the bounded
operating surface where a person needs to join, never in logs, summaries or derived context."""
from collections.abc import Iterable, Mapping
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"
_SENSITIVE_KEY_FRAGMENTS = ("authorization", "cookie", "token", "secret", "password", "passcode", "passwd", "pwd", "apikey", "privatekey", "email", "sender",
                            "recipient", "messageid", "body", "payload", "content", "trackingurl", "accountid", "notionid", "pageid", "calendarid",
                            "meetingid", "joinurl", "joinlink", "meetinglink", "transcript")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_URL = re.compile(r"https?://[^\s]+", re.I)
_AUTH = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.I)
_PASSCODE = re.compile(r"(?i)\b(pass(?:code|word)|pwd|meeting id|meeting number|access code)\b\s*[:=]?\s*[\w-]+(?:[ -]\d{2,}){0,3}")
_NOTION_ID = re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.I)


def _key(key):
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _sensitive_key(key):
    folded = _key(key)
    return any(fragment in folded for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _url(match):
    try:
        parts = urlsplit(match.group(0))
        query = urlencode([(k, REDACTED if _sensitive_key(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        host = parts.hostname or ""
        host = f"[{host}]" if ":" in host else host
        host = f"{host}:{parts.port}" if parts.port is not None else host
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return REDACTED


def _text(value, secrets):
    for secret in secrets:
        if secret:
            value = value.replace(secret, REDACTED)
    value = _AUTH.sub(REDACTED, value)
    value = _PASSCODE.sub(lambda m: f"{m.group(1)}: {REDACTED}", value)
    value = _URL.sub(_url, value)
    value = _EMAIL.sub(REDACTED, value)
    return _NOTION_ID.sub(REDACTED, value)


def redact(value, secrets: Iterable[str] = ()):
    """A JSON-friendly redacted copy; the input is never mutated. Unknown objects are reduced to their type name, never repr()'d."""
    return _walk(value, tuple(str(s) for s in secrets if s))


def _walk(value, secrets):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, secrets)
    if isinstance(value, bytes):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): REDACTED if _sensitive_key(k) else _walk(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_walk(v, secrets) for v in value]
    return type(value).__name__


def safe_error(error, secrets: Iterable[str] = ()):
    """A redacted one-line description of an exception for a log line: type name plus scrubbed message."""
    return f"{type(error).__name__}: {_text(str(error), tuple(str(s) for s in secrets if s))}"[:300]
