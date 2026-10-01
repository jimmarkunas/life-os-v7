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


def dice(fetcher=fetch):
    search = fetcher("https://www.dice.com/jobs?q=technical%20program%20manager&filters.workplaceTypes=Remote", timeout=25, max_bytes=3_000_000)
    links = sorted(set(re.findall(r'href="(https://www\.dice\.com/job-detail/[0-9a-f-]{36})', search.html)))[:3]
    out = {"search_status": search.status, "detail_links": len(links), "pages": []}
    for link in links:
        page = fetcher(link, timeout=25, max_bytes=3_000_000)
        blocks = [b for b in JSONLD.findall(page.html) if "JobPosting" in b]
        description = ""
        try:
            description = (json.loads(blocks[0]).get("description") or "") if blocks else ""
        except ValueError:
            pass
        out["pages"].append({"status": page.status, "bytes": len(page.html), "jobposting_ld": len(blocks), "ld_description_chars": len(description),
                             "easy_apply_marker": "easy apply" in page.html.lower(), "apply_link_marker": "applyurl" in page.html.lower()})
    return out


def run(limit, live):
    return {"open_jobs": open_jobs(), "teamtailor": teamtailor(), "dice": dice()}
