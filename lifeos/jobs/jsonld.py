"""schema.org JobPosting extraction from a page: the most reliable public source of description + posting date."""
from datetime import date, datetime
import json
import re

SCRIPT = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.get("@graph", []) if isinstance(node.get("@graph"), list) else []:
            yield from _walk(value)


def job_posting(html):
    """First JobPosting object found in the page's JSON-LD, or None."""
    for raw in SCRIPT.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for node in _walk(data):
            kind = node.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return node
    return None


def posted_date(posting):
    """datePosted as a date (YYYY-MM-DD or ISO datetime), else None."""
    value = str((posting or {}).get("datePosted") or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None


NEXT_DATA = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I)
DESCRIPTION_KEYS = {"description", "jobdescription", "job_description", "descriptionhtml", "fulldescription",
                    "jobdescriptionhtml", "jobdetails", "jobcontent"}


def embedded_description(html, minimum=200):
    """Longest description-like string in the page's embedded Next.js data (the tier V2 uses between JSON-LD and plain
    text), or None. Server-rendered career sites often carry the whole posting here even when the visible page is a shell."""
    match = NEXT_DATA.search(html or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    best, stack = "", [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in DESCRIPTION_KEYS and isinstance(value, str):
                    best = value if len(value) > len(best) else best
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return best if len(best) >= minimum else None
