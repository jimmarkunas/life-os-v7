"""LIFE-47 P0 (BRANCH ONLY, never merged): trash the ONE orphaned status paragraph the 2026-10-15 incident left at the bottom of the Hiring Pipeline's column (D197).

Dry run reads and proves only. Live: the paragraph named by the retired HIRING_STATUS_BLOCK_ID must be a childless, untrashed paragraph that is the LAST child of the heading's own parent;
the region must already validate in its new heading -> table shape; it is trashed with one block-level call; the region validates again and the rest of the page is digested identically
before and after. Any surprise stops with a fixed code and leaves the page as found. Counts and booleans only: no id, company or text is printed."""
import json
import os

from lifeos.hiring import card
from lifeos.hiring.snapshot import fail
from lifeos.platform import report_region as R


def _rest(digest, drop):
    keep = lambda i: not R.same_id(i, drop)                                             # noqa: E731
    return {k: v for k, v in digest["nodes"].items() if keep(k)}, {p: [c for c in kids if keep(c)] for p, kids in digest["layout"].items()}


def run(limit, live, environ=os.environ, client=None):
    client = client or card.writer(environ)
    orphan, heading = R.clean_id(environ.get("HIRING_STATUS_BLOCK_ID")), R.clean_id(environ.get("HIRING_CARD_BLOCK_ID"))
    if not orphan or not heading:
        raise fail("ORPHAN_NOT_CONFIGURED")
    block, anchor = R._meta(client, orphan), R._meta(client, heading)
    parent = (anchor.get("parent") or {})
    parent_id = parent.get("block_id") or parent.get("page_id")
    kids = R.children(client, parent_id, fail)
    facts = {"paragraph": block.get("type") == "paragraph", "trashed": bool(block.get("in_trash") or block.get("archived")), "childless": not block.get("has_children"),
             "same_parent": block.get("parent") == anchor.get("parent"), "last_child": bool(kids) and R.same_id(kids[-1]["id"], orphan), "text_empty": not R.plain(block).strip()}
    region, before = card.validate(client, environ)                                      # heading -> table must already hold; the orphan is not adjacent to it
    facts["region_ok"] = True
    own = [region["heading"]["id"], region["table"]["id"], orphan]
    before = R.page_digest(client, region["page_id"], own, fail, card.SKIP)
    print("hiring-orphan:", json.dumps(facts, sort_keys=True))
    if facts["trashed"]:
        print("hiring-orphan: already gone")
        return {"trashed": True, "changed": 0}
    if not (facts["paragraph"] and facts["childless"] and facts["same_parent"] and facts["last_child"]):
        raise fail("ORPHAN_STATE_UNEXPECTED")
    if not live:
        return {"trashed": False, "changed": 0}
    client.call("PATCH", f"/blocks/{orphan}", {"in_trash": True})
    again = R._meta(client, orphan)
    left = [k["id"] for k in R.children(client, parent_id, fail)]
    region, _ = card.validate(client, environ)
    after = R.page_digest(client, region["page_id"], own, fail, card.SKIP)
    gone = bool(again.get("in_trash") or again.get("archived")) and not any(R.same_id(k, orphan) for k in left)
    same = _rest(before, orphan) == _rest(after, orphan)
    print("hiring-orphan:", json.dumps({"gone": gone, "rest_of_page_unchanged": same, "region_ok": True}))
    if not (gone and same):
        raise fail("ORPHAN_READBACK_MISMATCH")
    return {"trashed": True, "changed": 1}
