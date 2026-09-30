"""LinkedIn apply-type and employer link WITHOUT logging in (ported from V2's proven logic, D12/D14).

The public guest posting page exposes (a) which apply path a job uses (native Easy Apply vs external) and (b) for
external jobs, the employer URL - either in JSON-ish keys or wrapped in LinkedIn redirect links
(/safety/go/?url=..., /redir/redirect?url=...). Plain HTTP only; LinkedIn is never logged in to.
"""
import html as htmllib
import re
from urllib.parse import parse_qs, unquote, urlsplit

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
KEYS = ("offsiteApplyUrl", "offsiteApplyTrackingUrl", "companyApplyUrl", "externalApplyUrl", "applyRedirectUrl", "applyUrl")
WRAPPER_PATHS = ("/safety/go/", "/safety/go", "/redir/redirect", "/redir/redirect/")
MARKERS = ("/safety/go", "/redir/redirect", "externalApply")


def _host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_linkedin(url):
    host = _host(url)
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def job_id(url):
    match = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})", url or "") or re.search(r"(\d{6,})/?$", urlsplit(url or "").path)
    return match.group(1) if match else None


def unwrap(value):
    """External URL from a LinkedIn redirect wrapper; other URLs pass through; LinkedIn-internal -> None."""
    value = unquote(htmllib.unescape(value or "")).strip()
    parts = urlsplit(value)
    if is_linkedin(value):
        if parts.path in WRAPPER_PATHS:
            inner = unquote(parse_qs(parts.query).get("url", [""])[0])
            return inner if inner.startswith("http") and not is_linkedin(inner) else None
        return None
    return value if value.startswith("http") else None


def _decode(body):
    text = htmllib.unescape(body or "")
    for escaped, plain in (("\\u002F", "/"), ("\\u003A", ":"), ("\\u0026", "&"), ("\\u003d", "="), ("\\/", "/")):
        text = text.replace(escaped, plain)
    return text


def external_urls(body):
    """Up to 3 distinct non-LinkedIn apply URLs found in JSON-ish keys or wrapped redirect anchors."""
    decoded, found = _decode(body), []
    for key in KEYS:
        for match in re.finditer(r"[\"']" + re.escape(key) + r"[\"']\s*:\s*[\"']([^\"']+)", decoded, re.I):
            url = unwrap(match.group(1))
            if url and url not in found:
                found.append(url)
    for href in re.findall(r"href=[\"']([^\"']+)[\"']", decoded):
        if any(marker in href for marker in MARKERS):
            url = unwrap(href)
            if url and url not in found:
                found.append(url)
    return found[:3]


def quick_apply_signal(body):
    """True = native Easy/Quick Apply; False = external or unavailable; never inferred from absence."""
    raw = htmllib.unescape(body or "").casefold()
    if "public_jobs_apply-link-offsite" in raw or "easy apply is not available" in raw or "quick apply is not available" in raw:
        return False
    if "public_jobs_apply-link-onsite" in raw:
        return True
    text = re.sub(r"<[^>]+>", " ", raw)
    return "easy apply" in text or "quick apply" in text


def read(body):
    """('external', url) | ('easy_apply', None) | ('external_unlinked', None) | ('unknown', None)."""
    urls = external_urls(body)
    if urls:
        return "external", urls[0]
    if quick_apply_signal(body):
        return "easy_apply", None
    if "public_jobs_apply-link-offsite" in htmllib.unescape(body or "").casefold():
        return "external_unlinked", None
    return "unknown", None
