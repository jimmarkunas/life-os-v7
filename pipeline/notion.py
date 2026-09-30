"""Publish READY jobs to the Notion Job Ledger (free official API, <= 2.5 req/s, counts-only logs).

Hostinger is the dedupe brain: each job is created once and its page id stored. The ONE cold-start read of the Ledger
(to avoid duplicating rows V2 already wrote) is remembered in v7_ledger_urls and never repeated.
Properties written (all exist in the Ledger): Job, Company, Apply URL, Source Provider, Source Types, Stable Job Key,
First Surfaced, Posting Date, Freshness Status, Liveness, Visible Lane, Admission Status. Fit fields are left blank.
The job description goes in the page BODY (contract v7.jd.1, docs/PLAN.md).
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from pipeline import limits, store

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"
SECTIONS = (("summary", "Summary"), ("responsibilities", "Responsibilities"), ("requirements", "Requirements"),
            ("qualifications", "Qualifications"))
PROVIDER = {"lensa": "Lensa", "jobright": "Jobright", "linkedin-alerts": "LinkedIn Jobs"}


class NotionError(RuntimeError):
    """Fixed codes only - never tokens, ids, or response bodies."""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def url_key(url):
    """Normalized Apply URL for dedupe: host + path, no query/fragment, lowercase, no trailing slash."""
    parts = urlsplit((url or "").strip())
    return hashlib.sha256(((parts.hostname or "") + parts.path.rstrip("/")).lower().encode()).hexdigest()


def _text(content):
    return [{"type": "text", "text": {"content": content[i:i + limits.NOTION_MAX_RICH_TEXT_CHARS]}}
            for i in range(0, max(len(content), 1), limits.NOTION_MAX_RICH_TEXT_CHARS)][:100]


def _block(kind, content):
    return {"object": "block", "type": kind, kind: {"rich_text": _text(content)}}


def body_blocks(key, description):
    """Contract v7.jd.1: marker paragraph, then heading_2 + bullets/paragraphs per non-empty section."""
    blocks = [_block("paragraph", f"v7-jd:1 | key={key}")]
    for field, heading in SECTIONS:
        text = (description.get(field) or "").strip()
        if not text:
            continue
        blocks.append(_block("heading_2", heading))
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("•"):
                blocks.append(_block("bulleted_list_item", line.lstrip("• ").strip() or "-"))
            else:
                blocks.append(_block("paragraph", line))
    return blocks


def properties(row):
    """row: dict with title, company, url, source, key, first_seen (date), posted (date|None)."""
    provider = PROVIDER.get(row["source"], row["source"])
    props = {
        "Job": {"title": _text(row["title"] or "Untitled")},
        "Company": {"rich_text": _text(row["company"] or "")},
        "Apply URL": {"url": row["url"]},
        "Source Provider": {"rich_text": _text(provider)},
        "Source Types": {"multi_select": [{"name": provider}]},
        "Stable Job Key": {"rich_text": _text(row["key"])},
        "First Surfaced": {"date": {"start": row["first_seen"].isoformat()}},
        "Freshness Status": {"select": {"name": "Fresh"}},
        "Liveness": {"select": {"name": "Live"}},
        "Visible Lane": {"select": {"name": "Newsletter"}},
        "Admission Status": {"select": {"name": "Passed / Review"}},
    }
    if row.get("posted"):
        props["Posting Date"] = {"date": {"start": row["posted"].isoformat()}}
    return props


class Client:
    def __init__(self, environ=os.environ, clock=time.monotonic, sleep=time.sleep):
        self.token = (environ.get("NOTION_API_TOKEN") or "").strip()
        self.source = (environ.get("NOTION_JOB_LEDGER_DATA_SOURCE_ID") or "").strip().replace("collection://", "")
        if not self.token or not self.source:
            raise NotionError("NOTION_CONFIG_MISSING")
        self._clock, self._sleep, self._last = clock, sleep, 0.0
        self.calls = 0

    def call(self, method, path, body=None):
        for attempt in range(4):
            wait = limits.NOTION_GAP_SECONDS - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last, self.calls = self._clock(), self.calls + 1
            request = urllib.request.Request(
                API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                headers={"Authorization": f"Bearer {self.token}", "Notion-Version": VERSION,
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 3:
                    self._sleep(min(float(error.headers.get("Retry-After") or 2), 30))
                    continue
                raise NotionError(f"NOTION_HTTP_{error.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                raise NotionError("NOTION_NETWORK") from None
        raise NotionError("NOTION_RATE_LIMITED")

    def ledger_urls(self):
        """Every Apply URL already in the Ledger (paged, 100 per request)."""
        urls, cursor = set(), None
        while True:
            body = {"page_size": limits.NOTION_PAGE_SIZE}
            if cursor:
                body["start_cursor"] = cursor
            data = self.call("POST", f"/data_sources/{self.source}/query", body)
            for page in data.get("results", []):
                value = ((page.get("properties") or {}).get("Apply URL") or {}).get("url")
                if value:
                    urls.add(url_key(value))
            if not data.get("has_more"):
                return urls
            cursor = data.get("next_cursor")

    def create(self, props, blocks):
        first, rest = blocks[:limits.NOTION_MAX_CHILD_BLOCKS], blocks[limits.NOTION_MAX_CHILD_BLOCKS:]
        page = self.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": self.source},
                                            "properties": props, "children": first})
        for start in range(0, len(rest), limits.NOTION_MAX_CHILD_BLOCKS):
            self.call("PATCH", f"/blocks/{page['id']}/children",
                      {"children": rest[start:start + limits.NOTION_MAX_CHILD_BLOCKS]})
        return page["id"]


def _seed(connection, client):
    """One-time cold start: remember every URL already in the Ledger."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_ledger_urls WHERE source='seed'")
        if cursor.fetchone()[0]:
            return 0
    urls = client.ledger_urls()
    with connection.cursor() as cursor:
        cursor.execute("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,'seed',%s)",
                       ("0" * 64, _now()))
        for key in urls:
            cursor.execute("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,'seed',%s)",
                           (key, _now()))
    return len(urls)


def run(limit, live, environ=os.environ):
    counts = {"picked": 0, "created": 0, "duplicate": 0, "failed": 0, "seeded": 0, "calls": 0}
    client = Client(environ)
    with store.connect() as connection:
        store.ensure_schema(connection)
        if live:
            counts["seeded"] = _seed(connection, client)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT j.id, j.dedupe_key, j.title, j.company, j.final_apply_url, j.source, j.first_seen, j.posted_date,"
                " d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text"
                " FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id=j.id WHERE j.status='READY'"
                " AND j.notion_page_id IS NULL ORDER BY j.first_seen LIMIT %s", (min(limit, limits.NOTION_PER_RUN),))
            rows = cursor.fetchall()
        counts["picked"] = len(rows)
        for (job_id, key, title, company, url, source, first_seen, posted, summary, resp, req, qual, full) in rows:
            digest = url_key(url)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM v7_ledger_urls WHERE url_hash=%s", (digest,))
                known = cursor.fetchone() is not None
            if known:
                counts["duplicate"] += 1
                if live:
                    with connection.cursor() as cursor:
                        cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', unresolved_reason='in_ledger', "
                                       "updated_at=%s WHERE id=%s", (_now(), job_id))
                continue
            if not live:
                counts["created"] += 1
                continue
            desc = {"summary": summary, "responsibilities": resp, "requirements": req, "qualifications": qual}
            if not any((summary, resp, req, qual)):
                desc["summary"] = (full or "")[:6000]
            row = {"title": title, "company": company, "url": url, "source": source, "key": key,
                   "first_seen": first_seen.date() if hasattr(first_seen, "date") else first_seen, "posted": posted}
            try:
                page_id = client.create(properties(row), body_blocks(key, desc))
            except NotionError:
                counts["failed"] += 1
                if counts["failed"] >= 3:          # systemic (auth/schema/limit): stop instead of hammering
                    break
                continue
            with connection.cursor() as cursor:
                cursor.execute("UPDATE v7_jobs SET status='PUBLISHED', notion_page_id=%s, updated_at=%s WHERE id=%s",
                               (page_id, _now(), job_id))
                cursor.execute("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,'v7',%s)",
                               (digest, _now()))
            counts["created"] += 1
    counts["calls"] = client.calls
    return counts
