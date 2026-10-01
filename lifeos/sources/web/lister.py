"""Status-aware board listing for the public ATS APIs. A listing is COMPLETE only when the provider answered with a valid,
fully-read inventory; anything else is FAILED with a fixed reason. FAILED is never "zero jobs" and never implies a removal.
(`resolve.ats_match.board` returns [] for both, which is fine for matching a title but not for acquisition.)"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import html as htmllib
import json
import re
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit

from lifeos.jobs import jd
from lifeos.sources.web import html_readers
from lifeos.platform import impersonate, limits
from lifeos.platform.http import fetch

COMPLETE, FAILED = "COMPLETE", "FAILED"
MAX_BYTES = 25_000_000                      # a large Greenhouse board with full content is several MB
SMARTRECRUITERS_PAGES = 30                  # 3,000 postings; a board that is still not finished is FAILED, not COMPLETE


@dataclass
class Listing:
    status: str
    jobs: list = field(default_factory=list)       # dicts: id, title, location, url, posted (date|None), content (text|None)
    reason: str | None = None


def _json(url, fetcher, body=None):
    extra = {"data": json.dumps(body).encode(), "headers": {"Content-Type": "application/json", "Accept": "application/json"}} if body is not None else {}
    page = fetcher(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2, max_bytes=MAX_BYTES, **extra)
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


def _pinpoint(source, fetcher):
    url = source.get("url") or f"https://{source['slug']}.pinpointhq.com/postings.json"
    data, err = _json(url, fetcher)
    if err or not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return Listing(FAILED, reason=err or "bad_shape")
    return Listing(COMPLETE, [_job(j.get("id"), j.get("title"), (j.get("location") or {}).get("name"), j.get("url"),
                                   j.get("published_at") or j.get("created_at"), _text(j.get("description")))
                              for j in data["data"]])


WORKDAY_PAGE, WORKDAY_MAX = 20, 2000            # Workday's page size is 20; a board past the bound is FAILED, not COMPLETE


def workday_urls(source):
    """(CXS list url, public job base) from either a CXS url or a public site url."""
    url = source["url"]
    if "/wday/cxs/" in url:
        return url, (source.get("public_job_base_url") or "").rstrip("/")
    parts = urlsplit(url)
    site = [p for p in parts.path.split("/") if p][-1]
    tenant = parts.hostname.split(".")[0]
    return f"{parts.scheme}://{parts.hostname}/wday/cxs/{tenant}/{site}/jobs", url.rstrip("/")


def _posted_on(text, today):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' -> a date; '30+ Days Ago' and anything else -> None."""
    low = (text or "").lower()
    if "today" in low:
        return today
    if "yesterday" in low:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\s*days?\s*ago", low)
    return today - timedelta(days=int(m.group(1))) if m and "+" not in low else None


def _workday(source, fetcher, today=None):
    today = today or date.today()
    list_url, base = workday_urls(source)
    jobs, offset, total = [], 0, None
    while offset < WORKDAY_MAX:
        data, err = _json(list_url, fetcher, {"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": offset, "searchText": ""})
        if err or not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
            return Listing(FAILED, reason=err or "bad_shape")
        batch = data["jobPostings"]
        if total is None:
            total = data.get("total")                       # only the first page carries a real total
            if not isinstance(total, int) or total < 0:
                return Listing(FAILED, reason="bad_shape")
            if total > WORKDAY_MAX:
                return Listing(FAILED, reason="too_many")
        for j in batch:
            path = j.get("externalPath")
            if path:
                jobs.append(_job(path, j.get("title"), j.get("locationsText"), f"{base}{path}", None))
                jobs[-1]["posted"] = _posted_on(j.get("postedOn"), today)
        offset += len(batch)
        if not batch or offset >= total:
            break
    if total is None or len(jobs) < total and offset < total:
        return Listing(FAILED, reason="incomplete")
    return Listing(COMPLETE, jobs)


def _jibe(source, fetcher):
    base, job_base = (source.get("url") or "").rstrip("?"), (source.get("job_base_url") or "").rstrip("/")
    if not base or not job_base:
        return Listing(FAILED, reason="bad_config")
    jobs, page, total, seen = [], 1, None, set()
    while total is None or len(seen) < total:
        data, err = _json(f"{base}?page={page}&sortBy=relevance&descending=false&internal=false", fetcher)
        if err or not isinstance(data, dict) or not isinstance(data.get("jobs"), list) or not isinstance(data.get("totalCount"), int):
            return Listing(FAILED, reason=err or "bad_shape")
        if total is None:
            total = data["totalCount"]
            if total > WORKDAY_MAX:
                return Listing(FAILED, reason="too_many")
        elif data["totalCount"] != total:
            return Listing(FAILED, reason="inconsistent")      # the inventory moved while we read it
        if not data["jobs"]:
            if len(seen) < total:
                return Listing(FAILED, reason="incomplete")
            break
        for item in data["jobs"]:
            d = item.get("data", item) if isinstance(item, dict) else {}
            ident = str(d.get("slug") or d.get("req_id") or "").strip()
            if not ident or ident in seen:
                return Listing(FAILED, reason="bad_shape")
            seen.add(ident)
            where = " ".join(" ".join(str(d.get(k) or "").split()) for k in ("full_location", "short_location", "location_name", "country") if d.get(k))
            body = "\n\n".join(t for t in (_text(d.get(k)) for k in ("description", "responsibilities", "qualifications")) if t) or None
            jobs.append(_job(ident, " ".join(str(d.get("title") or "").split()), where,
                             f"{job_base}/{quote(ident, safe='')}?lang=en-us", d.get("posted_date") or d.get("create_date"), body))
        page += 1
    return Listing(COMPLETE, jobs)


def _teamtailor(source, fetcher):
    """Teamtailor career sites publish every open job as an RSS feed at <jobs url>.rss (found by the shape probe; the HTML list has no JSON-LD)."""
    page = fetcher(source["url"].rstrip("/") + ".rss", timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2, max_bytes=MAX_BYTES)
    if page.status in (403, 429):
        return Listing(FAILED, reason="rate_limited")
    if page.status == 404:
        return Listing(FAILED, reason="not_found")
    if page.status != 200:
        return Listing(FAILED, reason=f"http_{page.status}" if page.status else "network")
    if "<!DOCTYPE" in page.html[:2000].upper() or "<!ENTITY" in page.html.upper():
        return Listing(FAILED, reason="bad_shape")                   # never expand entities from a third-party feed
    try:
        channel = ET.fromstring(page.html).find("channel")
    except ET.ParseError:
        return Listing(FAILED, reason="bad_xml")
    if channel is None:
        return Listing(FAILED, reason="bad_shape")                   # a valid empty board still has a channel; anything else is not an inventory
    jobs, seen = [], set()
    for item in channel.findall("item"):
        def text(name):
            node = item.find(name)
            return (node.text or "").strip() if node is not None and node.text else ""
        link = text("link")
        ident = text("guid") or link
        if not ident or ident in seen:
            return Listing(FAILED, reason="bad_shape")
        seen.add(ident)
        places = []
        for node in item.iter():
            name = node.tag.rsplit("}", 1)[-1].lower()
            if name in ("city", "country", "location") and node.text and node.text.strip() and not len(node):
                if node.text.strip() not in places:
                    places.append(node.text.strip())
            elif name == "remotestatus" and (node.text or "").strip().lower() in ("fully", "remote", "full"):
                places.append("Remote")
        try:
            posted = parsedate_to_datetime(text("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            posted = None
        jobs.append(_job(ident, text("title"), ", ".join(places), link, posted, _text(text("description"))))
    return Listing(COMPLETE, jobs)


def _first_party_html(source, fetcher):
    """A sponsor's own careers page (no public ATS API). COMPLETE only when the reader proves the inventory, or the page carries the
    registry's `zero_marker` phrase; an ambiguous page is FAILED (bad_shape), never zero jobs."""
    def fetch_text(url):
        got = fetcher(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2, max_bytes=MAX_BYTES)
        if got.status != 200:
            raise ValueError(f"http_{got.status}")
        return got.html
    if source.get("impersonate"):                       # a site that refuses plain requests (Revolut): Chrome TLS fingerprint, warm-up, retries
        page = impersonate.fetch(source["url"], warm_url=source.get("warm_url"), alt_urls=source.get("alt_urls", ()), must_contain=source.get("must_contain", ""))
    else:
        page = fetcher(source["url"], timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=3, max_bytes=MAX_BYTES)
    if page.status in (403, 429):
        return Listing(FAILED, reason="rate_limited")
    if page.status == 404:
        return Listing(FAILED, reason="not_found")
    if page.status != 200:
        return Listing(FAILED, reason=f"http_{page.status}" if page.status else "network")
    try:
        rows = html_readers.READERS[source["kind"]](page.html, source, fetch_text)
    except (ValueError, KeyError, TypeError, AttributeError):
        return Listing(FAILED, reason="bad_shape")
    if rows:
        return Listing(COMPLETE, rows)
    marker = source.get("zero_marker")
    if source["kind"] in html_readers.SELF_PROVING or (marker and marker.casefold() in html_readers.strip_html(page.html).casefold()):
        return Listing(COMPLETE, [])
    return Listing(FAILED, reason="bad_shape")


def _by_slug(reader):
    return lambda source, fetcher: reader(source["slug"], fetcher)


READERS = {"greenhouse": _by_slug(_greenhouse), "ashby": _by_slug(_ashby), "lever": _by_slug(_lever),
           "smartrecruiters": _by_slug(_smartrecruiters), "workable": _by_slug(_workable),
           "pinpoint": _pinpoint, "workday": _workday, "jibe": _jibe, "teamtailor": _teamtailor,
           **{kind: _first_party_html for kind in html_readers.READERS}}


def list_source(source, fetcher=fetch):
    """The listing for one registry source (any reader above)."""
    reader = READERS.get(source["kind"])
    if reader is None:
        return Listing(FAILED, reason="no_reader")
    try:
        listing = reader(source, fetcher)
    except (OSError, ValueError, KeyError):
        return Listing(FAILED, reason="network")
    if listing.status == COMPLETE:
        listing.jobs = [j for j in listing.jobs if j["id"] and j["title"] and j["url"]]     # a row without identity is not a job
    return listing


def list_board(kind, slug, fetcher=fetch):
    return list_source({"kind": kind, "slug": slug}, fetcher)
