"""Status-aware board listing for the public ATS APIs. A listing is COMPLETE only when the provider answered with a valid,
fully-read inventory; anything else is FAILED with a fixed reason. FAILED is never "zero jobs" and never implies a removal.
(`resolve.ats_match.board` returns [] for both, which is fine for matching a title but not for acquisition.)"""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import html as htmllib
import json

from lifeos.jobs import jd
from lifeos.platform import limits
from lifeos.platform.http import fetch

COMPLETE, FAILED = "COMPLETE", "FAILED"
MAX_BYTES = 25_000_000                      # a large Greenhouse board with full content is several MB
SMARTRECRUITERS_PAGES = 30                  # 3,000 postings; a board that is still not finished is FAILED, not COMPLETE


@dataclass
class Listing:
    status: str
    jobs: list = field(default_factory=list)       # dicts: id, title, location, url, posted (date|None), content (text|None)
    reason: str | None = None


def _json(url, fetcher):
    page = fetcher(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2, max_bytes=MAX_BYTES)
    if page.status in (403, 429):
        return None, "rate_limited"
    if page.status == 404:
        return None, "not_found"
    if page.status != 200:
        return None, f"http_{page.status}" if page.status else "network"
    try:
        return json.loads(page.html), None
    except ValueError:
        return None, "bad_json"                  # includes a truncated body


def _day(value):
    """date from an ISO string or epoch milliseconds, else None."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, timezone.utc).date()
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _text(markup):
    return jd.html_to_text(htmllib.unescape(markup or "")) or None


def _job(ident, title, location, url, posted=None, content=None):
    return {"id": str(ident), "title": (title or "").strip(), "location": (location or "").strip(), "url": url or "",
            "posted": _day(posted), "content": content}


def _greenhouse(slug, fetcher):
    data, err = _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", fetcher)
    if err or not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return Listing(FAILED, reason=err or "bad_shape")
    return Listing(COMPLETE, [_job(j.get("id"), j.get("title"), (j.get("location") or {}).get("name"), j.get("absolute_url"),
                                   j.get("first_published"), _text(j.get("content"))) for j in data["jobs"]])


def _ashby(slug, fetcher):
    data, err = _json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", fetcher)
    if err or not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return Listing(FAILED, reason=err or "bad_shape")
    return Listing(COMPLETE, [_job(j.get("id"), j.get("title"), j.get("location"), j.get("jobUrl"), j.get("publishedAt"),
                                   j.get("descriptionPlain") or _text(j.get("descriptionHtml")))
                              for j in data["jobs"] if j.get("isListed", True)])


def _lever(slug, fetcher):
    data, err = _json(f"https://api.lever.co/v0/postings/{slug}?mode=json", fetcher)
    if err or not isinstance(data, list):
        return Listing(FAILED, reason=err or "bad_shape")
    jobs = []
    for j in data:
        extra = "\n".join(f"{x.get('text', '')}\n{_text(x.get('content')) or ''}" for x in (j.get("lists") or []))
        body = "\n".join(part for part in (j.get("descriptionPlain"), extra, j.get("additionalPlain")) if part) or None
        jobs.append(_job(j.get("id"), j.get("text"), (j.get("categories") or {}).get("location"), j.get("hostedUrl"),
                         j.get("createdAt"), body))
    return Listing(COMPLETE, jobs)


def _smartrecruiters(slug, fetcher):
    jobs = []
    for page in range(SMARTRECRUITERS_PAGES):
        data, err = _json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={page * 100}", fetcher)
        if err or not isinstance(data, dict) or not isinstance(data.get("content"), list):
            return Listing(FAILED, reason=err or "bad_shape")
        for j in data["content"]:
            loc = j.get("location") or {}
            where = " ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            if loc.get("remote"):
                where = ("Remote " + where).strip()
            jobs.append(_job(j.get("id"), j.get("name"), where, f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                             j.get("releasedDate")))                                  # no content in the list: enrich reads the page
        if len(data["content"]) < 100:
            return Listing(COMPLETE, jobs)
    return Listing(FAILED, reason="too_many_pages")


def _workable(slug, fetcher):
    data, err = _json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", fetcher)
    if err or not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return Listing(FAILED, reason=err or "bad_shape")
    jobs = []
    for j in data["jobs"]:
        where = " ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
        if j.get("telecommuting"):
            where = ("Remote " + where).strip()
        url = f"https://apply.workable.com/{slug}/j/{j['shortcode']}/" if j.get("shortcode") else j.get("url")
        jobs.append(_job(j.get("shortcode"), j.get("title"), where, url, j.get("published_on"), _text(j.get("description"))))
    return Listing(COMPLETE, jobs)


READERS = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever, "smartrecruiters": _smartrecruiters, "workable": _workable}


def list_board(kind, slug, fetcher=fetch):
    reader = READERS.get(kind)
    if reader is None:
        return Listing(FAILED, reason="no_reader")
    try:
        listing = reader(slug, fetcher)
    except (OSError, ValueError):
        return Listing(FAILED, reason="network")
    if listing.status == COMPLETE:
        listing.jobs = [j for j in listing.jobs if j["id"] and j["title"] and j["url"]]     # a row without identity is not a job
    return listing
