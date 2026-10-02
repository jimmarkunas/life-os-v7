"""Counts-only shape probes for sources whose real pages the build sandbox cannot reach. Run from Actions; prints facts (status, sizes,
key NAMES, counts) and never any posting text, URL or personal data. Result decides the next parser, nothing here writes anything."""
import json
import os
import re

from lifeos.platform.http import fetch
from lifeos.sources.web import registry

OPEN_JOBS = "https://backend.dehnbostele.workers.dev/data/"
JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)


def _shape(value, depth=0):
    if isinstance(value, dict):
        return {k: (_shape(v, depth + 1) if depth < 2 else type(v).__name__) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [len(value), _shape(value[0], depth + 1) if value and depth < 2 else None]
    return type(value).__name__


def open_jobs(fetcher=fetch):
    contact = os.environ.get("OPEN_JOBS_CONTACT", "")
    headers = {"User-Agent": f"life-os-v7 personal job feed reader ({contact})", "Accept": "application/json"}
    out = {}
    for name in ("manifest.json", "diffs/index.json"):
        page = fetcher(OPEN_JOBS + name, timeout=30, max_bytes=3_000_000, headers=headers)
        entry = {"status": page.status, "bytes": len(page.html)}
        try:
            entry["shape"] = _shape(json.loads(page.html))
        except ValueError:
            entry["shape"] = "not_json"
        out[name] = entry
    return out


def teamtailor(fetcher=fetch):
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["kind"] != "teamtailor":
            continue
        page = fetcher(source["url"], timeout=20, max_bytes=3_000_000)
        rss = fetcher(source["url"].rstrip("/") + ".rss", timeout=20, max_bytes=3_000_000)
        out[source["id"][3:15]] = {
            "status": page.status, "bytes": len(page.html), "job_links": len(set(re.findall(r'href="[^"]*/jobs/\d[^"]*"', page.html))),
            "jobposting_ld": sum("JobPosting" in b for b in JSONLD.findall(page.html)), "rss_status": rss.status, "rss_items": rss.html.count("<item>")}
    return out


DICE_SAMPLES = ("1dde497d-8758-4eed-8aac-816bed294b63",)             # a public job id from a Dice search the user shared


def dice(fetcher=fetch):
    search = fetcher("https://www.dice.com/jobs?q=technical%20program%20manager&filters.workplaceTypes=Remote", timeout=25, max_bytes=3_000_000)
    links = sorted(set(re.findall(r'href="(https://www\.dice\.com/job-detail/[0-9a-f-]{36})', search.html)))[:3]
    out = {"search_status": search.status, "detail_links": len(links), "pages": []}
    links += [f"https://www.dice.com/job-detail/{i}" for i in DICE_SAMPLES]
    for link in links:
        page = fetcher(link, timeout=25, max_bytes=3_000_000)
        blocks = [b for b in JSONLD.findall(page.html) if "JobPosting" in b]
        description = ""
        try:
            description = (json.loads(blocks[0]).get("description") or "") if blocks else ""
        except ValueError:
            pass
        out["pages"].append({"status": page.status, "bytes": len(page.html), "jobposting_ld": len(blocks), "ld_description_chars": len(description),
                             "easy_apply_marker": "easy apply" in page.html.lower(), "apply_link_marker": "applyurl" in page.html.lower(),
                             "next_data": "__NEXT_DATA__" in page.html, "title_in_html": "<title>" in page.html.lower()})
    return out


def hiring(environ=os.environ):
    """Can the runner read the Hiring Pipeline page, and what does the tree look like (counts only)?"""
    from lifeos.jobs import hiring_pipeline                                                  # noqa: PLC0415
    from lifeos.platform.notion_client import Client                                          # noqa: PLC0415
    page_id = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    if not page_id:
        return {"configured": False}
    opportunities, status = hiring_pipeline.snapshot(Client(environ), page_id)
    return {"configured": True, "status": status, "active": sum(o.section == hiring_pipeline.ACTIVE for o in opportunities),
            "retired": sum(o.section == hiring_pipeline.RETIRED for o in opportunities), "with_rounds": sum(bool(o.rounds) for o in opportunities),
            "unparsed_titles": sum(not o.role for o in opportunities)}


def interview_environ(environ=os.environ):
    """The environment for Interview's Notion client: ONLY the Interview token, never the Jobs token (no fallback), or None when it is missing.
    Notion's `Client` insists on a data-source id; Interview does not use one, so a fixed placeholder satisfies it."""
    token = (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip()
    return {"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "interview-unused"} if token else None


def interview_access(environ=os.environ, client_factory=None):
    """Can the Interview token read the Hiring Pipeline page? Counts and fixed codes only; writes nothing; never prints an id, a title or the token."""
    from lifeos.jobs import hiring_pipeline                                                  # noqa: PLC0415
    from lifeos.platform.notion_client import Client                                          # noqa: PLC0415
    page_id = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    mapped = interview_environ(environ)
    if not page_id or mapped is None:
        return {"status": "config_missing", "token": mapped is not None, "page": bool(page_id)}
    opportunities, status = hiring_pipeline.snapshot((client_factory or Client)(mapped), page_id)
    return {"status": status, "token": True, "page": True, "active": sum(o.section == hiring_pipeline.ACTIVE for o in opportunities),
            "retired": sum(o.section == hiring_pipeline.RETIRED for o in opportunities), "with_rounds": sum(bool(o.rounds) for o in opportunities),
            "unparsed_titles": sum(not o.role for o in opportunities)}


def ledger_target(environ=os.environ):
    """Does the configured data source pass the Job Ledger target check? Property names that fail are schema, not content."""
    from lifeos.jobs import ledger                                                           # noqa: PLC0415
    from lifeos.platform.notion_client import Client, NotionError                             # noqa: PLC0415
    client = Client(environ)
    status = ledger.verify(client)
    out = {"status": status}
    if status == ledger.MISMATCH:
        try:
            out["missing_or_wrong_type"] = ledger.missing(client)
        except NotionError:
            pass
    return out


ATS_PATTERNS = (                                   # (reader kind, regex over the result URL) -> slug; only kinds V7 can already list
    ("greenhouse", r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)"),
    ("lever", r"^https?://jobs(?:\.eu)?\.lever\.co/([a-z0-9_-]+)"),
    ("ashby", r"^https?://jobs\.ashbyhq\.com/([a-z0-9_.-]+)"),
    ("workable", r"^https?://apply\.workable\.com/([a-z0-9_-]+)"),
    ("teamtailor", r"^https?://([a-z0-9-]+)\.teamtailor\.com"),
    ("rippling_html", r"^https?://ats\.rippling\.com/([a-z0-9_-]+)"),
    ("join_html", r"^https?://join\.com/companies/([a-z0-9_-]+)"),
)


def discover(search=None, sources=None):
    """For each Scale-Up sponsor with no readable job board, ask web search (free TinyFish Search) where its jobs live. Prints, per sponsor id,
    only `kind:slug` for a board V7 can already list, or `own:<host>` for the sponsor's own site, or None. No posting text, no URLs."""
    from lifeos.platform import tinyfish_search                                                # noqa: PLC0415
    from lifeos.platform.tinyfish import TinyFishError                                         # noqa: PLC0415
    from lifeos.jobs.resolve import ats_match                                                  # noqa: PLC0415
    from urllib.parse import urlsplit                                                          # noqa: PLC0415
    search = search or tinyfish_search.search
    out = {}
    for source in sources if sources is not None else [r for r in registry.load(registry.PATHS["Scale-Up"]) if r["status"] == "fallback"]:
        tokens = [w for w in ats_match.norm(source["company"]).split() if w not in ats_match.SUFFIX and len(w) > 2][:2]
        found = None
        try:
            for result in search(f"{source['company']} careers jobs UK")[:8]:
                url = result.get("url") or ""
                hay = ats_match.norm(url + " " + (result.get("title") or "") + " " + (result.get("snippet") or ""))
                if not tokens or not all(t in hay for t in tokens):
                    continue
                for kind, pattern in ATS_PATTERNS:
                    match = re.match(pattern, url, re.I)
                    if match:
                        found = f"{kind}:{match.group(1)}"
                        break
                if found:
                    break
                host = (urlsplit(url).hostname or "").removeprefix("www.")
                if found is None and host and re.search(r"career|jobs|join|work-with|vacanc|opportunit", url, re.I) \
                        and not any(x in host for x in ("linkedin.", "indeed.", "glassdoor.", "reed.", "totaljobs.", "cv-library.", "jooble.", "adzuna.", "ziprecruiter.")):
                    found = f"own:{host}"
        except TinyFishError as error:
            found = str(error).lower()
        out[source["id"]] = found
    return out


def lane_funnel():
    """Where do Scale-Up jobs stop? Counts only: v7_jobs by lane and status, then the fit decision, admission and (digit-free) reason."""
    from lifeos.jobs import store                                                            # noqa: PLC0415
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT lane, status, COUNT(*) FROM v7_jobs GROUP BY lane, status")
        by_status = {f"{lane}/{status}": n for lane, status, n in cursor.fetchall()}
        cursor.execute("SELECT f.lane, f.admission, f.admission_reason, COUNT(*) FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id "
                       "WHERE j.lane = 'Scale-Up' GROUP BY f.lane, f.admission, f.admission_reason")
        reasons = {}
        for lane, admission, reason, n in cursor.fetchall():
            key = f"{lane}/{admission}/{re.sub(r'[0-9]+', '#', reason or '')[:48]}"
            reasons[key] = reasons.get(key, 0) + n
    return {"by_lane_status": by_status, "scale_up_fit": reasons}


def scale_up_listing(fetcher=fetch, only=None):
    """Per ready Scale-Up sponsor: listing status, failure reason and job count from the runner's own network (counts only, no text)."""
    from lifeos.sources.web import lister                                                    # noqa: PLC0415
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["status"] != "ready" or (only and source["id"] not in only):
            continue
        listing = lister.list_source(source, fetcher)
        out[source["id"]] = f"{listing.status}:{listing.reason or ''}:{len(listing.jobs)}"
    return out


def source_pages(ids=("su-futuristic-technologies-ltd", "su-otto-car-limited", "su-truvi-holdings-ltd"), plain=fetch):
    """Shape of a sponsor's page, plain versus Chrome-impersonated (counts and flags only): status, size, anchors, JobPosting JSON-LD,
    __NEXT_DATA__, and the hostnames of outbound links that look like a job board."""
    from lifeos.platform import impersonate                                                  # noqa: PLC0415
    from urllib.parse import urlsplit                                                        # noqa: PLC0415
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["id"] not in ids:
            continue
        shapes = {}
        for how, got in (("plain", plain(source["url"], timeout=20, max_hops=3, max_bytes=3_000_000)),
                         ("chrome", impersonate.fetch(source["url"], warm_url=source.get("warm_url"), rounds=1))):
            text = got.html or ""
            hosts = sorted({(urlsplit(h).hostname or "").removeprefix("www.") for h in re.findall(r'href=["\'](https?://[^"\']+)', text, re.I)
                            if re.search(r"greenhouse|lever|ashby|workable|teamtailor|bamboohr|recruitee|personio|breezy|join\.com|pinpoint|smartrecruiters|rippling|jobs", h, re.I)})[:6]
            shapes[how] = {"status": got.status, "bytes": len(text), "anchors": len(re.findall(r"<a\b", text, re.I)), "jsonld_job": len(re.findall(r"JobPosting", text)),
                           "next_data": "__NEXT_DATA__" in text, "job_hosts": hosts, "final": (urlsplit(got.final_url).path or "/")[:40]}
        out[source["id"]] = shapes
    return out


def _rows(html_readers, source, text):
    """How many rows the source's own reader finds in `text`, or the error class when it refuses the page."""
    if not text:
        return None
    try:
        return len(html_readers.READERS[source["kind"]](text, source, None))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return type(error).__name__


def browser_pages(ids=("su-futuristic-technologies-ltd", "su-otto-car-limited", "su-truvi-holdings-ltd"), fetcher=None):
    """Same shape as source_pages but through the real headless Chromium, plus (for pages with no job markup) the first link labels so a reader can be
    written: public navigation text only, capped. Counts and flags otherwise."""
    from lifeos.platform import browser                                                      # noqa: PLC0415
    from lifeos.sources.web import html_readers                                              # noqa: PLC0415
    from urllib.parse import urlsplit                                                        # noqa: PLC0415
    fetcher = fetcher or browser.fetch
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["id"] not in ids:
            continue
        got = fetcher(source["url"], warm_url=source.get("warm_url"))
        text = got.html or ""
        page = html_readers.parse_page(text) if text else None
        labels = [" ".join(label.split())[:40] for _, label in (page.links if page else []) if label.strip()][:25]
        out[source["id"]] = {"status": got.status, "error": got.error, "bytes": len(text), "anchors": len(re.findall(r"<a\b", text, re.I)),
                             "jsonld_job": len(re.findall(r"JobPosting", text)), "final": (urlsplit(got.final_url).path or "/")[:40],
                             "reader_rows": _rows(html_readers, source, text),
                             "labels": labels if source["kind"] == "static_complete_html" else []}
    return out


def run(limit, live):
    return {"browser_pages": browser_pages(), "source_pages": source_pages(), "scale_up_listing": scale_up_listing(), "lane_funnel": lane_funnel(), "discover_scale_up": discover(), "open_jobs": open_jobs(), "teamtailor": teamtailor(), "dice": dice(), "hiring_pipeline": hiring(), "ledger_target": ledger_target()}
