"""Machine ids that ride in a page without showing on it: a hidden ref is a zero-width character linked to a fixed address, read back from the link.

The ref is the link's last path segment and nothing is drawn (Jim, Oct 27 2026: no marker, hash or id may show). Pure; the callers own what a ref means."""
from urllib.parse import quote, unquote

BASE = "https://example.com/lifeos/"
ZW = "\u2060"


def run(ref):
    return {"type": "text", "text": {"content": ZW, "link": {"url": BASE + quote(ref, safe="")}}}


def refs(block):
    """The hidden refs carried by a block's own text, in order."""
    inner = (block.get(block.get("type")) if block.get("type") else next((v for v in block.values() if isinstance(v, dict) and "rich_text" in v), None)) or {}
    out = []
    for part in inner.get("rich_text") or []:
        if not isinstance(part, dict):
            continue
        url = part.get("href") or ((part.get("text") or {}).get("link") or {}).get("url") or ""
        if url.startswith(BASE):
            out.append(unquote(url[len(BASE):]))
    return out
