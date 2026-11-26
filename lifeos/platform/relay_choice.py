"""BUZZ_RELAY=hosted|pilot: whether the governed MCP service also serves the private relay pilot. hosted (the default,
also when empty) serves only the community relays; anything else is refused, so a typo never falls back to a different
relay.
"""

# tailnet only; the relay does not accept loopback
PILOT_URL = "https://lifeos-edge-01.tail96b07f.ts.net:3100"
MODES = ("hosted", "pilot")


def mode(environ):
    """-> "hosted" or "pilot"; ValueError for any other value."""
    value = (environ.get("BUZZ_RELAY") or "").strip().lower() or "hosted"
    if value not in MODES:
        raise ValueError("BUZZ_RELAY")
    return value


def pilot_url_ok(url):
    return url.rstrip("/") == PILOT_URL
