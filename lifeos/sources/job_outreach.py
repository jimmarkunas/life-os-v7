"""LIFE-47 (D222): the native job-first Job Outreach table, a floating region beneath Active Opportunities on the Daily Report, written the way the Hiring Pipeline is
(lifeos/hiring/card.py): a heading_3 `🤝 Job Outreach (Updated 2:00 PM CT)` and the four-column table right under it, both pinned by id
(JOB_OUTREACH_CARD_BLOCK_ID, JOB_OUTREACH_TABLE_BLOCK_ID) and proven to sit on the same page as the Hiring Pipeline heading (HIRING_CARD_BLOCK_ID). Only the heading's
stamp and the table's data rows are written; the rest of the page is digested before and after and must be identical. Dormant until both ids are set: `run` then
returns NOT_CONFIGURED without reading or writing anything. Rows come from lifeos.recruiters.outreach.project (one per exact open Ledger job, D221). Fixed codes only."""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.hiring import card as hiring_card, decision as D
from lifeos.platform import report_region as R, router
from lifeos.platform.notion_client import Client, NotionError
from lifeos.recruiters import notion as store, outreach as O

TITLE = router.JOB_OUTREACH_REGION
MODULE = router.OWNERS[TITLE]
HEADERS = ("Job", "Outreach", "Next follow-up", "Notes")
EMPTY = "No job-specific outreach is being tracked."
SKIP = hiring_card.SKIP
CT = ZoneInfo("America/Chicago")


class JobOutreachError(NotionError):
    """Fixed failure codes only; never a name, company or id."""


def fail(code):
    return JobOutreachError(f"JOB_OUTREACH_{code}")


def heading_text(stamp):
    return f"{TITLE} ({stamp})" if stamp else TITLE


def _step(name, call):
    try:
        return call()
    except NotionError as error:
        if isinstance(error, JobOutreachError):
            raise
        raise fail(f"{name}:{error}") from None


def configured(environ):
    return bool(R.clean_id(environ.get("JOB_OUTREACH_CARD_BLOCK_ID")) and R.clean_id(environ.get("JOB_OUTREACH_TABLE_BLOCK_ID")))


def validate(client, environ):
    """-> (region, page digest). The exact heading -> table shape, the pinned table, the same page as the Hiring Pipeline heading, the exact header row, one owner."""
    for name in ("JOB_OUTREACH_CARD_BLOCK_ID", "JOB_OUTREACH_TABLE_BLOCK_ID", "HIRING_CARD_BLOCK_ID"):
        raw = R.clean_id(environ.get(name)).replace("-", "")
        if not raw:
            raise fail(f"NOT_CONFIGURED:{name}")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", raw):
            raise fail(f"ID_INVALID:{name}:len{len(raw)}")
    region = _step("ANCHOR", lambda: R.floating_region(client, R.clean_id(environ["JOB_OUTREACH_CARD_BLOCK_ID"]), TITLE, fail, with_status=False))
    if not R.same_id(region["table"]["id"], R.clean_id(environ["JOB_OUTREACH_TABLE_BLOCK_ID"])):
        raise fail("PINNED_BLOCK_MISMATCH")
    hiring = _step("HIRING_BLOCK", lambda: R._meta(client, R.clean_id(environ["HIRING_CARD_BLOCK_ID"])))
    if not isinstance(hiring, dict) or not R.same_id(_step("HIRING_PAGE", lambda: R.page_of(client, hiring, fail)), region["page_id"]):
        raise fail("WRONG_PAGE")
    if R.cells_of(region["rows"][0]) != list(HEADERS):
        raise fail("HEADERS_CHANGED")
    own = [region["heading"]["id"], region["table"]["id"]]
    digest = _step("DIGEST", lambda: R.page_digest(client, region["page_id"], own, fail, SKIP))
    if any(R.titled(text, TITLE) for text in digest["h3"]):
        raise fail("HEADING_NOT_UNIQUE")
    try:
        router.check_write(MODULE, [TITLE])
    except router.RouterError:
        raise fail("NOT_OWNED") from None
    return region, digest


def cells(rows):
    """project() rows -> table cells; the empty state is one explicit row, never a silent blank table."""
    if not rows:
        return [[(EMPTY, None), ("", None), ("", None), ("", None)]]
    return [[(r["job"], None), (r["outreach"], None), (r["next"], None), ("Job Ledger ↗", r["link"])] for r in rows]


def present(client, environ, stamp, rows, live):
    """Dry run: validate only. Live: write, read back, prove the rest of the page unchanged. rows=None (DEGRADED) changes only the heading.
    -> {"rows_changed", "rows_added", "rows_removed", "verified"}"""
    region, before = validate(client, environ)
    counts = {"rows_changed": 0, "rows_added": 0, "rows_removed": 0, "verified": False}
    if not live:
        return counts
    own = [region["heading"]["id"], region["table"]["id"]]
    heading = heading_text(stamp)
    try:
        counts.update(R.write_floating(client, region, None, rows, fail))
        if R.plain(region["heading"]).strip() != heading:
            client.call("PATCH", f"/blocks/{region['heading']['id']}", {"heading_3": {"rich_text": [{"type": "text", "text": {"content": heading}}]}})
    except Exception:
        try:
            client.call("PATCH", f"/blocks/{region['heading']['id']}", {"heading_3": {"rich_text": [{"type": "text", "text": {"content": heading_text("DEGRADED")}}]}})
        except Exception:
            pass
        raise fail("WRITE_FAILED") from None
    after_region = R.floating_region(client, region["heading"]["id"], TITLE, fail, with_status=False)
    after = R.page_digest(client, after_region["page_id"], own, fail, SKIP)
    if after_region["heading"]["id"] != region["heading"]["id"] or R.plain(after_region["heading"]).strip() != heading:
        raise fail("HEADING_CHANGED")
    if before["nodes"] != after["nodes"] or before["layout"] != after["layout"]:
        print("job-outreach: protected content changed", json.dumps(R.page_changed(before, after)))
        raise fail("PROTECTED_REGION_CHANGED")
    if [R.cells_of(r) for r in after_region["rows"][:1]] != [list(HEADERS)]:
        raise fail("READBACK_MISMATCH")
    if rows is not None and [R.live_view(r) for r in after_region["rows"][1:]] != [R.want_view(row) for row in rows]:
        raise fail("READBACK_MISMATCH")
    counts["verified"] = True
    return counts


def outreach_rows(notion):
    """Every Recruiters row that carries a Job, following every page; an incomplete read raises (DEGRADED, never a trustworthy zero)."""
    rows, cursor, seen = [], None, set()
    for _ in range(store.MAX_PAGES):
        body = {"page_size": 100, "filter": {"property": O.JOB, "relation": {"is_not_empty": True}}, **({"start_cursor": cursor} if cursor else {})}
        page = notion.query_data_source(None, body)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise fail("QUERY_INCOMPLETE")
        rows += [r for r in map(store.read, page["results"]) if r["jobs"]]
        if not page["has_more"]:
            return rows
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise fail("PAGINATION_INVALID")
        seen.add(cursor)
    raise fail("PAGINATION_INCOMPLETE")


def open_jobs(ledger, rows, ledger_source):
    """{Ledger page id: {company, role, url}} for every linked job that is still open by the Close rules (D221); a closed or foreign page is left out."""
    jobs = {}
    for jid in {O._id(j) for r in rows for j in r["jobs"]}:
        try:
            page = ledger.get_page(jid)
        except NotionError as error:
            if "404" in str(error):
                continue
            raise
        props = page.get("properties") or {}
        decision = props.get(D.DECISION)
        if (page.get("archived") or page.get("in_trash") or O._id((page.get("parent") or {}).get("data_source_id")) != O._id(ledger_source)
                or not isinstance(decision, dict) or (decision.get("select") or {}).get("name")):
            continue
        jobs[jid] = {"company": D._text(props.get("Company"), "rich_text"), "role": D._text(props.get("Job"), "title"), "url": page.get("url") or f"https://www.notion.so/{jid}"}
    return {k: v for k, v in jobs.items() if v["company"] and v["role"]}


def run(limit, live, environ=os.environ, recruiters=None, ledger=None, client=None, now=None):
    """The job-outreach stage. Dormant unless both region ids are set. A failed source read writes only the DEGRADED heading and fails the step."""
    if not configured(environ):
        return {"job_outreach": "NOT_CONFIGURED"}
    now = (now or datetime.now(CT)).astimezone(CT)
    stamp = f"Updated {now:%-I:%M %p} CT"
    writer = client or hiring_card.writer(environ)
    try:
        recruiters = recruiters or Client(environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")
        ledger = ledger or Client(environ)
        found = outreach_rows(recruiters)
        shown = O.project(found, open_jobs(ledger, found, ledger.source), now.date())
    except NotionError as error:
        present(writer, environ, "DEGRADED", None, live)
        raise fail(f"SOURCE_UNREADABLE:{str(error)[:40]}") from None
    done = present(writer, environ, stamp, cells(shown), live)
    return {"job_outreach": "written" if live else "dry_run", "jobs": len(shown), "conversations": sum(len(r["jobs"]) for r in found), **done}
