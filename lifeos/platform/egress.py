"""Other ways to reach a page a plain request from the GitHub runner cannot (counts and flags only; URLs never logged).
Each returns lifeos.platform.http.Fetched facts. Used by probes first; a registry source opts in only after a probe proves the route."""
import json
from urllib.parse import quote

from lifeos.platform.http import Fetched, fetch

HONEST_UA = "Mozilla/5.0 (compatible; LIFE-OS-Scale-up/1.0; +https://github.com/jimmarkunas/life-os-automation)"   # what V1 sent: some sites prefer an announced bot


def honest(url, **kwargs):
    """Plain fetch with the announced-bot user agent (V1's)."""
    return fetch(url, headers={"User-Agent": HONEST_UA, "Accept": "*/*"}, **kwargs)


def reader_proxy(url, timeout=40):
    """Jina Reader (r.jina.ai): a free public service that fetches the page from its own network and returns it. Rate limited; no key."""
    return fetch("https://r.jina.ai/" + url, timeout=timeout, headers={"Accept": "text/plain", "X-Return-Format": "html"})


def wayback(url, timeout=30):
    """Newest Wayback Machine snapshot of the page (the snapshot's freshness, not the live page's)."""
    meta = fetch("https://archive.org/wayback/available?url=" + quote(url, safe=""), timeout=timeout)
    try:
        snap = json.loads(meta.html)["archived_snapshots"]["closest"]["url"].replace("http://", "https://", 1)
    except (KeyError, ValueError, TypeError):
        return Fetched(url, 404, "", 0, "no_snapshot")
    return fetch(snap, timeout=timeout)
