"""Write the Hiring Pipeline into the ONE existing floating region of the Daily Report (HIRE-1.2). V7 owns it exclusively (router.OWNERS; it is NOT a callout).

HIRING_CARD_BLOCK_ID and HIRING_TABLE_BLOCK_ID name the existing heading_3 and the table right under it (no status paragraph); both must match the live shape. The writer may change only the heading's
update stamp (D188: `📈 Hiring Pipeline (Updated 2:00 PM CT)`, the way Calendar and Jira carry theirs; the title, the block and its place never change) and the table rows under it. Before a live
write the whole page outside those two blocks is digested; after it the region is re-read and the digest must be identical. Any mismatch, any wrong shape, any ambiguity
raises: nothing is created, moved or guessed. Fixed codes only."""
import json
import re

from lifeos.platform import report_region as R, router
from lifeos.platform.notion_client import Client
from . import models as M
from lifeos.platform.notion_client import NotionError
from .snapshot import HiringError, fail

MODULE = router.OWNERS[router.HIRING_REGION]
SKIP = (router.DCC_REGION,)                                      # a region ChatGPT writes on its own schedule: outside V7's proof, never touched by V7


def writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _step(name, call):
    """Run one validation step; a Notion error is re-raised with the step's fixed name (never an id or content), so a failed run says where it stopped."""
    try:
        return call()
    except NotionError as error:
        if isinstance(error, HiringError):
            raise
        raise fail(f"{name}:{error}") from None


def validate(client, environ):
    """-> (region, page digest). The anchor must be the exact heading, on the same page as the Jira card, in the exact H3 -> table shape."""
    block_id = R.clean_id(environ.get("HIRING_CARD_BLOCK_ID"))
    if not block_id:
        raise fail("NOT_CONFIGURED")
    if M.TITLE != router.HIRING_REGION:
        raise fail("TITLE_DRIFT")
    for name in ("HIRING_CARD_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID"):
        raw = R.clean_id(environ.get(name)).replace("-", "")
        if raw and not re.fullmatch(r"[0-9a-fA-F]{32}", raw):             # a pasted link or stray text: say which secret, never its value
            raise fail(f"ID_INVALID:{name}:len{len(raw)}")
    table_id = R.clean_id(environ.get("HIRING_TABLE_BLOCK_ID"))
    if not table_id:                                             # both blocks are pinned by id: the heading and the table
        raise fail("NOT_CONFIGURED")
    region = _step("ANCHOR", lambda: R.floating_region(client, block_id, M.TITLE, fail, with_status=False))
    if not R.same_id(region["table"]["id"], table_id):
        raise fail("PINNED_BLOCK_MISMATCH")                      # the block under the heading is not the one Jim named: nothing is written
    anchor = R.clean_id(environ.get("JIRA_CARD_BLOCK_ID"))
    if not anchor:
        raise fail("PAGE_UNPROVEN")
    jira = _step("JIRA_BLOCK", lambda: R._meta(client, anchor))
    if not isinstance(jira, dict) or not R.same_id(_step("JIRA_PAGE", lambda: R.page_of(client, jira, fail)), region["page_id"]):
        raise fail("WRONG_PAGE")
    if R.cells_of(region["rows"][0]) not in (list(M.HEADERS), list(M.OLD_HEADERS)):      # LIFE-47: exactly the old or the new header; anything else writes nothing
        raise fail("HEADERS_CHANGED")
    own = [region["heading"]["id"], region["table"]["id"]]
    digest = _step("DIGEST", lambda: R.page_digest(client, region["page_id"], own, fail, SKIP))
    if any(R.titled(text, M.TITLE) for text in digest["h3"]):         # the owned heading is outside the digest: any other heading with the title is a second region
        raise fail("HEADING_NOT_UNIQUE")
    try:
        router.check_write(MODULE, [M.TITLE])
    except router.RouterError:
        raise fail("NOT_OWNED") from None
    return region, digest


def present(client, environ, text, cells, live):
    """Dry run: validate only. Live: write, read back, prove the rest of the page unchanged. -> {"rows_changed", "rows_added", "rows_removed", "verified"}."""
    region, before = validate(client, environ)
    counts = {"rows_changed": 0, "rows_added": 0, "rows_removed": 0, "verified": False, "header": "old" if R.cells_of(region["rows"][0]) == list(M.OLD_HEADERS) else "new"}
    if not live:
        return counts
    own = [region["heading"]["id"], region["table"]["id"]]
    stamp = M.split_status(text)[0]
    heading = M.heading_text(stamp)
    try:
        counts.update(R.write_floating(client, region, None, cells, fail))
        if counts["header"] == "old":                                # idempotent: only the exact old text, only the header row, inside the same fail-closed write
            client.call("PATCH", f"/blocks/{region['rows'][0]['id']}", {"table_row": {"cells": R.cell_blocks([(h, None) for h in M.HEADERS])}})
            counts["header"] = "migrated"
        if R.plain(region["heading"]).strip() != heading:
            client.call("PATCH", f"/blocks/{region['heading']['id']}", {"heading_3": {"rich_text": [{"type": "text", "text": {"content": heading}}]}})
    except Exception:
        try:                                                     # a half-written table must not look current: the heading says so, best effort
            client.call("PATCH", f"/blocks/{region['heading']['id']}", {"heading_3": {"rich_text": [{"type": "text", "text": {"content": M.heading_text("DEGRADED")}}]}})
        except Exception:
            pass
        raise fail("WRITE_FAILED") from None
    after_region = R.floating_region(client, region["heading"]["id"], M.TITLE, fail, with_status=False)                       # authoritative read-back
    after = R.page_digest(client, after_region["page_id"], own, fail, SKIP)
    if after_region["heading"]["id"] != region["heading"]["id"] or R.plain(after_region["heading"]).strip() != heading:
        raise fail("HEADING_CHANGED")                               # same block, same place, exactly the title and the stamp this run wrote
    if before["nodes"] != after["nodes"] or before["layout"] != after["layout"]:
        print("hiring: protected content changed", json.dumps(R.page_changed(before, after)))
        raise fail("PROTECTED_REGION_CHANGED")
    rows = after_region["rows"]
    if [R.cells_of(r) for r in rows[:1]] != [list(M.HEADERS)]:
        raise fail("READBACK_MISMATCH")
    if cells is not None:
        got = [(R.cells_of(r), [x[0] if x else None for x in R._links_of(r)]) for r in rows[1:]]
        if got != [([t for t, _ in row], [link for _, link in row]) for row in cells]:
            raise fail("READBACK_MISMATCH")
    counts["verified"] = True
    return counts
