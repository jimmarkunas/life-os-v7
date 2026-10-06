"""Write the Hiring Pipeline into the ONE existing floating region of the Daily Report (HIRE-1.2). V7 owns it exclusively (router.OWNERS; it is NOT a callout).

HIRING_CARD_BLOCK_ID, HIRING_STATUS_BLOCK_ID and HIRING_TABLE_BLOCK_ID name the existing heading_3, status paragraph and table; all three must match the live shape. The writer may change only the status paragraph and the table rows under it; the heading is immutable. Before a live
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
    """-> (region, page digest). The anchor must be the exact heading, on the same page as the Jira card, in the exact H3 -> paragraph -> table shape."""
    block_id = R.clean_id(environ.get("HIRING_CARD_BLOCK_ID"))
    if not block_id:
        raise fail("NOT_CONFIGURED")
    if M.TITLE != router.HIRING_REGION:
        raise fail("TITLE_DRIFT")
    for name in ("HIRING_CARD_BLOCK_ID", "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID"):
        raw = R.clean_id(environ.get(name)).replace("-", "")
        if raw and not re.fullmatch(r"[0-9a-fA-F]{32}", raw):             # a pasted link or stray text: say which secret, never its value
            raise fail(f"ID_INVALID:{name}:len{len(raw)}")
    status_id, table_id = R.clean_id(environ.get("HIRING_STATUS_BLOCK_ID")), R.clean_id(environ.get("HIRING_TABLE_BLOCK_ID"))
    if not status_id or not table_id:                            # all three blocks are pinned by id: the heading, the status paragraph and the table
        raise fail("NOT_CONFIGURED")
    region = _step("ANCHOR", lambda: R.floating_region(client, block_id, M.TITLE, fail))
    if not R.same_id(region["status"]["id"], status_id) or not R.same_id(region["table"]["id"], table_id):
        raise fail("PINNED_BLOCK_MISMATCH")                      # the blocks under the heading are not the ones Jim named: nothing is written
    anchor = R.clean_id(environ.get("JIRA_CARD_BLOCK_ID"))
    if not anchor:
        raise fail("PAGE_UNPROVEN")
    jira = _step("JIRA_BLOCK", lambda: R._meta(client, anchor))
    if not isinstance(jira, dict) or not R.same_id(_step("JIRA_PAGE", lambda: R.page_of(client, jira, fail)), region["page_id"]):
        raise fail("WRONG_PAGE")
    if [R.cells_of(region["rows"][0])] != [list(M.HEADERS)]:
        raise fail("HEADERS_CHANGED")
    own = [region["status"]["id"], region["table"]["id"]]
    digest = _step("DIGEST", lambda: R.page_digest(client, region["page_id"], own, fail, SKIP))
    if digest["h3"].get(M.TITLE) != 1:
        raise fail("HEADING_NOT_UNIQUE")
    try:
        router.check_write(MODULE, [M.TITLE])
    except router.RouterError:
        raise fail("NOT_OWNED") from None
    return region, digest


def present(client, environ, text, cells, live):
    """Dry run: validate only. Live: write, read back, prove the rest of the page unchanged. -> {"rows_changed", "rows_added", "rows_removed", "verified"}."""
    region, before = validate(client, environ)
    counts = {"rows_changed": 0, "rows_added": 0, "rows_removed": 0, "verified": False}
    if not live:
        return counts
    own = [region["status"]["id"], region["table"]["id"]]
    try:
        counts.update(R.write_floating(client, region, text, cells, fail))
    except Exception:
        try:                                                     # a half-written table must not look current: say so, best effort
            client.call("PATCH", f"/blocks/{region['status']['id']}", {"paragraph": {"rich_text": [{"type": "text", "text": {"content": "DEGRADED · Hiring Pipeline update failed · the table may be incomplete"}}]}})
        except Exception:
            pass
        raise fail("WRITE_FAILED") from None
    after_region = R.floating_region(client, region["heading"]["id"], M.TITLE, fail)                       # authoritative read-back
    after = R.page_digest(client, after_region["page_id"], own, fail, SKIP)
    if before["nodes"].get(region["heading"]["id"]) != after["nodes"].get(region["heading"]["id"]):
        raise fail("HEADING_CHANGED")
    if before["nodes"] != after["nodes"] or before["layout"] != after["layout"]:
        print("hiring: protected content changed", json.dumps(R.page_changed(before, after)))
        raise fail("PROTECTED_REGION_CHANGED")
    rows = after_region["rows"]
    if R.plain(after_region["status"]) != text or [R.cells_of(r) for r in rows[:1]] != [list(M.HEADERS)]:
        raise fail("READBACK_MISMATCH")
    if cells is not None:
        got = [(R.cells_of(r), [x[0] if x else None for x in R._links_of(r)]) for r in rows[1:]]
        if got != [([t for t, _ in row], [link for _, link in row]) for row in cells]:
            raise fail("READBACK_MISMATCH")
    counts["verified"] = True
    return counts
