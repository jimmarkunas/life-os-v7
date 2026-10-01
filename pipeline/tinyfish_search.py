"""TinyFish Search API client (free; 30 requests/minute). Key from TINYFISH_API_KEY. Fixed codes only in errors."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from pipeline import limits
from pipeline.tinyfish import TinyFishError, clean_key

ENDPOINT = "https://api.search.tinyfish.ai"
MIN_GAP_SECONDS = limits.TINYFISH_SEARCH_GAP_SECONDS
_last = [0.0]


def _key():
    key, parts = clean_key(os.environ.get("TINYFISH_API_KEY") or "")
    if not key or len(parts) > 2:
        raise TinyFishError("TINYFISH_KEY_MISSING")
    return key


def search(query, include_domains=None, timeout=30):
    """[{url,title,snippet,site_name}] (possibly empty). Raises TinyFishError('TINYFISH_SEARCH_HTTP_<code>')."""
    wait = MIN_GAP_SECONDS - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    params = {"query": query[:400]}
    if include_domains:
        params["include_domains"] = ",".join(include_domains)
    request = urllib.request.Request(ENDPOINT + "?" + urllib.parse.urlencode(params),
                                     headers={"X-API-Key": _key(), "Accept": "application/json"})
    _last[0] = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise TinyFishError(f"TINYFISH_SEARCH_HTTP_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TinyFishError("TINYFISH_SEARCH_NETWORK") from None
    return [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("url")]
