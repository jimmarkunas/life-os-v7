"""LIFE-47 / D222 TEMPORARY one-shot installer (Jim-approved, remove after the install): creates the Job Outreach region once on the Daily Report, right after the
Hiring Pipeline table and before Recruiter Outreach. Manual only (domains.yml input `job_outreach_install`, never a tick, a schedule or "Run every domain job"; the
dispatch must leave 'Actually write' and 'Run every domain job' unticked, and the job's normal Hiring and Job Outreach steps are skipped).

`validate` reads only. `install` performs exactly ONE non-retried PATCH /blocks/{column}/children with `after` = the Hiring Pipeline table, inserting the heading_3,
the status paragraph and a header-only four-column table, then reads the column and the whole page back: the three new blocks must sit exactly there, the ongoing
job-outreach stage must accept them, and every other block on the page (Hiring Pipeline, Recruiter Outreach and its rows, JIRA Execution, every neighbour) must be
unchanged, the Command Center subtree included. The page must be the one the JIRA Execution anchor is on. An unclear reply is never retried: the destination is read first and the same proof decides. Fixed codes and counts only, plus the two new block ids."""
import json
import os
from urllib.parse import quote

from lifeos.hiring import card as hiring_card
from lifeos.platform import report_region as R, router
from lifeos.platform.notion_client import NotionError
from . import job_outreach as J

RECRUITER_TITLE = "🤝 Recruiter Outreach"
MODES = ("validate", "install")


class InstallError(NotionError):
    """Fixed failure codes only; never a name, text or id."""


def fail(code):
    return InstallError(f"JOB_OUTREACH_INSTALL_{code}")


def _text(content):
    return [{"type": "text", "text": {"content": content}}]


def blocks():
    """The three blocks, exactly as approved."""
    header = {"object": "block", "type": "table_row", "table_row": {"cells": [_text(h) for h in J.HEADERS]}}
    return [{"object": "block", "type": "heading_3", "heading_3": {"rich_text": _text(J.TITLE)}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(J.EMPTY)}},
            {"object": "block", "type": "table", "table": {"table_width": 4, "has_column_header": True, "has_row_header": False, "children": [header]}}]


def preflight(client, environ):
    """-> the facts the write depends on. Raises on anything but: the Hiring Pipeline heading -> table in a column, that table immediately followed by the
    Recruiter Outreach heading and its table, and no Job Outreach heading anywhere on the page."""
    for name in ("HIRING_CARD_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID"):
        if not R.clean_id(environ.get(name)):
            raise fail(f"NOT_CONFIGURED:{name}")
    try:                                                                       # the Hiring writer's own check: pinned heading and table, on the page the JIRA card is on
        hiring_card.validate(client, environ)
    except NotionError as error:
        raise fail(f"HIRING_PAGE_CHECK:{str(error)[:60]}") from None
    hiring = R.floating_region(client, R.clean_id(environ["HIRING_CARD_BLOCK_ID"]), router.HIRING_REGION, fail, with_status=False)
    table = hiring["table"]
    if not R.same_id(table["id"], R.clean_id(environ["HIRING_TABLE_BLOCK_ID"])):
        raise fail("HIRING_TABLE_MISMATCH")
    jira = R._meta(client, R.clean_id(environ["JIRA_CARD_BLOCK_ID"]))           # and independently here: the JIRA Execution anchor is on this same page
    if not isinstance(jira, dict) or not R.same_id(R.page_of(client, jira, fail), hiring["page_id"]):
        raise fail("WRONG_PAGE")
    column_id = hiring["parent_id"]
    column = R._meta(client, column_id)
    if not isinstance(column, dict) or column.get("type") != "column":
        raise fail("NOT_IN_A_COLUMN")
    siblings = R.children(client, column_id, fail)
    at = [i for i, b in enumerate(siblings) if R.same_id(b.get("id"), table["id"])]
    if len(at) != 1:
        raise fail("HIRING_TABLE_AMBIGUOUS")
    if any(b.get("type") == "heading_3" and R.titled(R.plain(b), J.TITLE) for b in siblings):
        raise fail("ALREADY_PRESENT")
    nxt = siblings[at[0] + 1:at[0] + 3]
    if (len(nxt) != 2 or nxt[0].get("type") != "heading_3" or not R.titled(R.plain(nxt[0]), RECRUITER_TITLE) or nxt[1].get("type") != "table"
            or sum(1 for b in siblings if b.get("type") == "heading_3" and R.titled(R.plain(b), RECRUITER_TITLE)) != 1):
        raise fail("RECRUITER_OUTREACH_NOT_NEXT")
    recruiter_rows = R.children(client, nxt[1]["id"], fail)
    digest = R.page_digest(client, hiring["page_id"], [], fail)                 # every block, the Command Center subtree included (no skip)
    if any(R.titled(text, J.TITLE) for text in digest["h3"]):
        raise fail("ALREADY_PRESENT")
    return {"page_id": hiring["page_id"], "column_id": column_id, "table_id": table["id"], "siblings": siblings, "index": at[0], "recruiter_heading": nxt[0]["id"],
            "recruiter_table": nxt[1]["id"], "recruiter_rows": len(recruiter_rows) - 1, "digest": digest}


def verify(client, environ, pre):
    """The authoritative read-back after the one write (or an unclear reply). -> (heading id, status id, table id)."""
    after = R.children(client, pre["column_id"], fail)
    old = [b["id"] for b in pre["siblings"]]
    ids = [b["id"] for b in after]
    i = pre["index"] + 1
    new = after[i:i + 3]
    if len(after) != len(old) + 3 or ids[:i] != old[:i] or ids[i + 3:] != old[i:] or [b.get("type") for b in new] != ["heading_3", "paragraph", "table"]:
        raise fail("READBACK_PLACEMENT")
    heading, status, table = new
    if R.plain(heading) != J.TITLE or R.plain(status) != J.EMPTY:
        raise fail("READBACK_TEXT")
    spec = table.get("table") or {}
    rows = R.children(client, table["id"], fail)
    if (spec.get("table_width"), spec.get("has_column_header"), spec.get("has_row_header")) != (4, True, False) or [R.cells_of(r) for r in rows] != [list(J.HEADERS)]:
        raise fail("READBACK_TABLE")
    J.validate(client, {**environ, "JOB_OUTREACH_CARD_BLOCK_ID": heading["id"], "JOB_OUTREACH_TABLE_BLOCK_ID": table["id"]})   # the ongoing stage accepts it as is
    digest = R.page_digest(client, pre["page_id"], [b["id"] for b in new], fail)
    mine = {b["id"] for b in new}
    nodes = {k: v for k, v in digest["nodes"].items() if k not in mine}
    layout = {k: v for k, v in digest["layout"].items() if k != pre["column_id"]}
    before_layout = {k: v for k, v in pre["digest"]["layout"].items() if k != pre["column_id"]}
    if nodes != pre["digest"]["nodes"] or layout != before_layout or digest["layout"].get(pre["column_id"]) != ids:
        print("job-outreach-install: protected content changed", json.dumps(R.page_changed({"nodes": pre["digest"]["nodes"], "layout": before_layout}, {"nodes": nodes, "layout": layout})))
        raise fail("PROTECTED_REGION_CHANGED")
    if len(R.children(client, pre["recruiter_table"], fail)) - 1 != pre["recruiter_rows"]:
        raise fail("RECRUITER_ROWS_CHANGED")
    return heading["id"], status["id"], table["id"]


def run(limit, live, environ=os.environ, client=None):
    """The `job-outreach-install` stage. JOB_OUTREACH_INSTALL picks the mode; nothing happens without it, outside a manual dispatch, or once Job Outreach is configured."""
    mode = (environ.get("JOB_OUTREACH_INSTALL") or "").strip()
    if mode not in MODES:
        return {"job_outreach_install": "NOT_REQUESTED"}
    if environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":                 # missing or any other event: refused
        raise fail("MANUAL_ONLY")
    if environ.get("INPUT_LIVE") != "false" or environ.get("INPUT_ALL") != "false":   # 'Actually write' and 'Run every domain job' must be explicitly unticked
        raise fail("CONFLICTING_INPUTS")
    if J.configured(environ):
        raise fail("ALREADY_CONFIGURED")
    client = client or hiring_card.writer(environ)                           # NOTION_CONFIG_MISSING without the token
    pre = preflight(client, environ)
    facts = {"column_blocks": len(pre["siblings"]), "hiring_table_position": pre["index"], "recruiter_outreach_next": True, "recruiter_rows": pre["recruiter_rows"],
             "page_blocks": len(pre["digest"]["nodes"]), "job_outreach_present": False}
    if mode == "validate":
        return {"job_outreach_install": "validated", "writes": 0, **facts}
    unclear = None
    try:
        result = client.call_once("PATCH", f"/blocks/{quote(pre['column_id'], safe='')}/children", {"children": blocks(), "after": pre["table_id"]})
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            unclear = "REPLY_MALFORMED"
    except NotionError as error:
        unclear = str(error)[:40]                                              # never retried: the read-back below decides what happened
    try:
        heading, status, table = verify(client, environ, pre)
    except NotionError as error:
        raise fail(f"NOT_VERIFIED:{'unclear reply ' + unclear + ' · ' if unclear else ''}{str(error)[:60]}") from None
    return {"job_outreach_install": "installed", "writes": 1, "reply": unclear or "ok", **facts,
            "JOB_OUTREACH_CARD_BLOCK_ID": heading, "JOB_OUTREACH_STATUS_BLOCK_ID": status, "JOB_OUTREACH_TABLE_BLOCK_ID": table}
