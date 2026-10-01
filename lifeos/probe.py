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


def run(limit, live):
    return {"open_jobs": open_jobs(), "teamtailor": teamtailor(), "dice": dice(), "hiring_pipeline": hiring(), "ledger_target": ledger_target()}
