"""TinyFish Fetch API client (real-browser fetch with anti-bot). Key comes from the TINYFISH_API_KEY secret.

Fetch (free on Jim's plan) returns final_url, page text/html and every <a href> when links=true - so the apply button's
destination is visible without metered Agent steps. Only counts/categories are ever logged (public repo).
"""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://api.fetch.tinyfish.ai"
MAX_URLS = 10


class TinyFishError(RuntimeError):
    """Fixed codes only - never key material or response bodies."""


def fetch_many(urls, fmt="html", links=True, ttl=0, per_url_timeout_ms=45000, timeout=150):
    """Fetch up to 10 URLs in one call. Returns (results_by_requested_url, errors_list)."""
    key = os.environ.get("TINYFISH_API_KEY")
    if not key:
        raise TinyFishError("TINYFISH_KEY_MISSING")
    if not urls or len(urls) > MAX_URLS:
        raise TinyFishError("TINYFISH_BAD_BATCH")
    body = json.dumps({"urls": list(urls), "format": fmt, "links": links, "ttl": ttl,
                       "per_url_timeout_ms": per_url_timeout_ms}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise TinyFishError(f"TINYFISH_HTTP_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TinyFishError("TINYFISH_NETWORK") from None
    results = {item.get("url"): item for item in data.get("results", []) if isinstance(item, dict)}
    return results, [e for e in data.get("errors", []) if isinstance(e, dict)]
