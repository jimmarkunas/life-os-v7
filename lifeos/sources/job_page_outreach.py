"""LIFE-10 §2 / LIFE-47: the outreach table on each Job Ledger job page, in the Recruiter Outreach style (Jim accepted the synthetic preview on Oct 20, 2026).

One V7-owned region per job page: a heading_3 `🤝 Outreach (Updated 10/20 @ 9:40 PM CT)` directly followed by a native four-column table (Person — Employer · Channel · Role ·
Initial outreach · Conversation · Next step · Due), one row per conversation recorded against that exact job by Mark as Sent (D221). Cells are a bold primary line over a
gray detail line, and only recorded facts are shown: a reply that cannot be verified reads "reply unknown", never "no reply". A job with no recorded conversation gets no region.

Writes are bounded to the region. The page must be an open Job Ledger page (the Ledger data source, not archived). A new region is appended right after the Network Leads
block (else at the end of the page) with one non-retried call and read back. An existing region is edited row by row in place, and the heading stamp changes only when a row
changes. Every other block on the page is digested before and after and must be identical. Live writes need JOB_PAGE_OUTREACH=on as well as --live; otherwise the stage
reports what it would do. The table stays on the job page until an interview; moving the job to the Hiring Pipeline is LIFE-42 / LIFE-46, not this stage. Fixed codes and
counts only."""
import os
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.network import surface as N
from lifeos.platform import report_region as R
from lifeos.platform.notion_client import Client, NotionError
from lifeos.recruiters import notion as store, outreach as O

TITLE = "🤝 Outreach"
HEADERS = ("Person — Employer", "Channel · Role", "Initial outreach · Conversation", "Next step · Due")
CT = ZoneInfo("America/Chicago")


class JobPageOutreachError(NotionError):
    """Fixed failure codes only; never a name, company or id."""


def fail(code):
    return JobPageOutreachError(f"JOB_PAGE_OUTREACH_{code}")


def stamp(now):
    return f"Updated {now:%m/%d} @ {now:%-I:%M %p} CT"


def heading_text(now):
    return f"{TITLE} ({stamp(now)})"


def _day(iso):
    return f"{date.fromisoformat(iso[:10]):%b} {int(iso[8:10])}" if iso else ""


def _two(primary, detail="", link=None):
    """The Recruiter Outreach cell: a bold primary line, then a gray detail line."""
    return [(primary, link, "bold")] + ([("\n", None, None), (detail, None, "gray")] if detail else [])


def _closed(r):
    return r["done"] or not r["active"] or r["action"] in store.HISTORY


def _next(r, today):
    if _closed(r):
        return _two("No follow-up", r["action"] or "Closed")
    if r["action"] == store.NEEDS_ACTION:
        due = r["follow_up"]
        return _two("Needs action: follow up", f"Overdue since {_day(due)}" if due and due < today.isoformat() else "Due now")
    if r["action"] == store.WAITING and r["follow_up"]:
        return _two("Follow up", f"Due {_day(r['follow_up'])}")
    return _two("No follow-up scheduled")


def _conversation(r):
    if r["action"] == store.WAITING:
        return "Waiting · reply unknown (LinkedIn inbox not connected)"
    return r["action"] or "No status recorded"


def _sort_key(r):
    rank = 2 if _closed(r) else 0 if r["action"] == store.NEEDS_ACTION else 1
    return (rank, r["follow_up"] or "9999-99-99", r["person"].lower())


def cells(rows, today):
    """Recruiters rows for ONE job -> table rows, most urgent first: Needs Action, then by the soonest follow-up, closed conversations last."""
    return [[_two(r["person"], r["company"]), _two(r["medium"] or "Channel not recorded", r["role"]),
             _two(f"Initial outreach: {_day(r['last_contact'])}" if r["last_contact"] else "Initial outreach: date not recorded", _conversation(r)), _next(r, today)]
            for r in sorted(rows, key=_sort_key)]


def outreach_rows(notion):
    """Every Recruiters row that carries a Job, following every page; an incomplete read raises (never a trustworthy empty table)."""
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


def by_job(rows):
    jobs = {}
    for r in rows:
        for jid in {O._id(j) for j in r["jobs"]}:
            jobs.setdefault(jid, []).append(r)
    return jobs


def _owned(top):
    return [b for b in top if b.get("type") == "heading_3" and R.titled(R.plain(b), TITLE)]


def _is_leads(client, block_id):
    """The Network Leads block (NET-2.3) is a toggle whose first child paragraph carries its marker."""
    inner = R.children(client, block_id, fail)
    return bool(inner) and inner[0].get("type") == "paragraph" and R.plain(inner[0]).startswith(N.MARK)


def _check_page(page, ledger_source):
    if not isinstance(page, dict) or page.get("archived") or page.get("in_trash"):
        raise fail("PAGE_GONE")
    if O._id((page.get("parent") or {}).get("data_source_id")) != O._id(ledger_source):
        raise fail("NOT_A_LEDGER_PAGE")


def _region(client, page_id, heading_id):
    """The owned region on a job page: the top-level heading and the table directly after it (4 columns, a header row). Unlike the Daily Report's floating regions,
    Jim's own notes may follow the table. -> {"heading", "table", "rows", "status": None}."""
    top = R.children(client, page_id, fail)
    at = [i for i, b in enumerate(top) if R.same_id(b.get("id"), heading_id)]
    if len(at) != 1 or at[0] + 1 >= len(top):
        raise fail("REGION_SHAPE_INVALID")
    heading, table = top[at[0]], top[at[0] + 1]
    spec = table.get("table") or {}
    if table.get("type") != "table" or spec.get("table_width") != 4 or spec.get("has_column_header") is not True:
        raise fail("REGION_SHAPE_INVALID")
    rows = R.children(client, table["id"], fail)
    if not rows or any(r.get("type") != "table_row" for r in rows):
        raise fail("REGION_SHAPE_INVALID")
    return {"heading": heading, "table": table, "rows": rows, "status": None}


def _shell(want, now):
    header = {"object": "block", "type": "table_row", "table_row": {"cells": [[{"type": "text", "text": {"content": h}}] for h in HEADERS]}}
    rows = [{"object": "block", "type": "table_row", "table_row": {"cells": R.cell_blocks(r)}} for r in want]
    return [{"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": heading_text(now)}}]}},
            {"object": "block", "type": "table", "table": {"table_width": 4, "has_column_header": True, "has_row_header": False, "children": [header] + rows}}]


def _verify(client, page_id, heading_id, want, before):
    region = _region(client, page_id, heading_id)
    if not R.titled(R.plain(region["heading"]), TITLE) or R.cells_of(region["rows"][0]) != list(HEADERS) or [R.live_view(r) for r in region["rows"][1:]] != [R.want_view(w) for w in want]:
        raise fail("READBACK_MISMATCH")
    after = R.page_digest(client, page_id, [region["heading"]["id"], region["table"]["id"]], fail)
    unowned = lambda d: ({k: v for k, v in d["nodes"].items() if v != "owned"}, {k: v for k, v in d["layout"].items() if k != page_id})
    if unowned(after) != unowned(before):
        raise fail("PROTECTED_CONTENT_CHANGED")
    return region


def sync_page(client, ledger, ledger_source, page_id, rows, now, live):
    """-> "created" | "updated" | "unchanged" (prefixed "would_" when not writing). Raises a fixed code on anything unsafe; nothing outside the region is ever written."""
    _check_page(ledger.get_page(page_id), ledger_source)
    top = R.children(client, page_id, fail)
    owned = _owned(top)
    if len(owned) > 1:
        raise fail("REGION_NOT_UNIQUE")
    want = cells(rows, now.date())
    if not owned:
        if not live:
            return "would_create"
        before = R.page_digest(client, page_id, [], fail)
        leads = [b["id"] for b in top if b.get("type") == "toggle" and b.get("has_children", True) and _is_leads(client, b["id"])]
        body = {"children": _shell(want, now), **({"after": leads[-1]} if leads else {})}
        try:
            client.call_once("PATCH", f"/blocks/{quote(page_id, safe='')}/children", body)
        except NotionError:
            pass                                                            # never retried: the read-back below decides what happened
        made = _owned(R.children(client, page_id, fail))
        if len(made) != 1:
            raise fail("CREATE_NOT_VERIFIED")
        _verify(client, page_id, made[0]["id"], want, before)
        return "created"
    region = _region(client, page_id, owned[0]["id"])
    if R.cells_of(region["rows"][0]) != list(HEADERS):
        raise fail("HEADERS_CHANGED")
    if [R.live_view(r) for r in region["rows"][1:]] == [R.want_view(w) for w in want]:
        return "unchanged"
    if not live:
        return "would_update"
    before = R.page_digest(client, page_id, [region["heading"]["id"], region["table"]["id"]], fail)
    R.write_floating(client, region, None, want, fail)
    client.call("PATCH", f"/blocks/{quote(region['heading']['id'], safe='')}", {"heading_3": {"rich_text": [{"type": "text", "text": {"content": heading_text(now)}}]}})
    _verify(client, page_id, region["heading"]["id"], want, before)
    return "updated"


def run(limit, live, environ=os.environ, recruiters=None, ledger=None, client=None, now=None):
    """The job-page-outreach stage. Live writes need JOB_PAGE_OUTREACH=on as well as --live. A page that fails is counted under its fixed code and the run ends failed;
    the other pages still sync."""
    now = (now or datetime.now(CT)).astimezone(CT)
    writing = live and (environ.get("JOB_PAGE_OUTREACH") or "").strip().lower() == "on"
    recruiters = recruiters or Client(environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")
    ledger = ledger or Client(environ)
    client = client or ledger
    counts = {"job_page_outreach": "live" if writing else "dry_run", "jobs": 0, "conversations": 0, "failed": 0, "codes": {}}
    jobs = by_job(outreach_rows(recruiters))
    counts["jobs"], counts["conversations"] = len(jobs), sum(len(v) for v in jobs.values())
    for jid in sorted(jobs)[:limit or None]:
        try:
            result = sync_page(client, ledger, ledger.source, jid, jobs[jid], now, writing)
        except NotionError as error:
            code = str(error)[:60]
            result, counts["failed"] = code if code.startswith("JOB_PAGE_OUTREACH_") else "JOB_PAGE_OUTREACH_NOTION_ERROR", counts["failed"] + 1
        counts["codes"][result] = counts["codes"].get(result, 0) + 1
    return counts
