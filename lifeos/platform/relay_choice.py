"""LIFE-870: BUZZ_RELAY=hosted|pilot says whether the governed MCP service also serves the private relay pilot.

Jim, comment 13867. Rollback is setting it back to hosted.

hosted (the default, also when the setting is empty or absent) serves only the community relays, as today. pilot adds the private relay pilot on the Oracle host (loopback only, never a public
address) as one more community. Anything else is refused, so a typo never falls back to a different relay. The pilot address is fixed here, not a setting a caller can aim."""

PILOT_URL = "http://127.0.0.1:3100"
MODES = ("hosted", "pilot")


def mode(environ):
    """-> "hosted" or "pilot"; ValueError for any other value."""
    value = (environ.get("BUZZ_RELAY") or "").strip().lower() or "hosted"
    if value not in MODES:
        raise ValueError("BUZZ_RELAY")
    return value


def pilot_url_ok(url):
    return url.rstrip("/") == PILOT_URL
