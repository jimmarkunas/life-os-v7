"""Job identity (Jobs OS): the same keys for every producer, so one opening is one row however it arrives."""
from hashlib import sha256
import re
from urllib.parse import urlsplit


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def url_hash(url):
    """Exact-URL key (dedupe_key)."""
    return sha256(url.encode()).hexdigest()


def fuzzy_key(company, title, location):
    """Same opening seen under another URL: normalized company + title + location."""
    return sha256("|".join(norm(x) for x in (company, title, location)).encode()).hexdigest()


def url_key(url):
    """Normalized Apply URL for the ledger: host + path, no query/fragment, lowercase, no trailing slash."""
    parts = urlsplit((url or "").strip())
    return sha256(((parts.hostname or "") + parts.path.rstrip("/")).lower().encode()).hexdigest()
