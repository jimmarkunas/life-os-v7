"""LIFE-47 P0: one-shot repair of the floating Hiring Pipeline region after a hand edit (HIRING_FLOATING_SHAPE_INVALID). Branch tool, never scheduled.

It reads the three pinned blocks, then (live only) un-trashes the ORIGINAL status paragraph by its pinned id, trashes the ONE stray three-column table and corrects the ONE Notes cell, and proves the
result with the publisher's own validation. It creates no block, never touches the pinned heading or table, and on any surprise stops with a fixed code and leaves the page as it found it. Counts and
booleans only: no id, company or text is printed."""
import json
import os
import re

from lifeos.hiring import card, models as M
from lifeos.hiring.snapshot import fail
from lifeos.platform import report_region as R
from lifeos.platform.notion_client import NotionError

STRAY_HEADER = ["Company/Role", "Stage", "Next Step"]
LINK = re.compile(r"^https://www\.notion\.so/[0-9a-f]{32}$")


def _gone(block):
    return bool(block.get("in_trash") or block.get("archived"))


def _shaped(sibs, ids, stray_id):
    """heading -> status -> table, once the stray table is out of the way."""
    kept = [b["id"] for b in sibs if not R.same_id(b["id"], stray_id)]
    at = [i for i, b in enumerate(kept) if R.same_id(b, ids["CARD"])]
    return len(at) == 1 and len(kept) > at[0] + 2 and R.same_id(kept[at[0] + 1], ids["STATUS"]) and R.same_id(kept[at[0] + 2], ids["TABLE"])


def _rest(digest, own):
    """The page outside the region: every block's content hash and every parent's child order, with the region's own blocks (which this repair legitimately moves in and out of their column) set aside."""
    ours = {i.replace("-", "").lower() for i in own}
    keep = lambda i: i.replace("-", "").lower() not in ours                                  # noqa: E731
    return {k: v for k, v in digest["nodes"].items() if keep(k)}, {p: [c for c in kids if keep(c)] for p, kids in digest["layout"].items()}


def plan(client, environ):
    """-> (facts, objects). Raises a fixed code if the pinned heading is not the live one or the table is not the pinned four-column table."""
    ids = {k: R.clean_id(environ.get(f"HIRING_{k}_BLOCK_ID")) for k in ("CARD", "STATUS", "TABLE")}
    heading = R._meta(client, ids["CARD"])
    if heading.get("type") != "heading_3" or _gone(heading) or not R.titled(R.plain(heading), M.TITLE):
        raise fail("REPAIR_HEADING_UNEXPECTED")
    parent = heading["parent"]
    parent_id = parent.get("block_id") or parent.get("page_id")
    sibs = R.children(client, parent_id, fail)
    at = [i for i, b in enumerate(sibs) if R.same_id(b["id"], ids["CARD"])]
    if len(at) != 1:
        raise fail("REPAIR_HEADING_UNEXPECTED")
    table = next((b for b in sibs[at[0] + 1:at[0] + 3] if R.same_id(b["id"], ids["TABLE"])), None)
    rows = R.children(client, table["id"], fail) if table and table.get("type") == "table" and (table.get("table") or {}).get("table_width") == 4 else []
    if not rows:
        raise fail("REPAIR_TABLE_UNEXPECTED")
    near = sibs[at[0] + 1:at[0] + 3]
    stray = [b for b in near if b.get("type") == "table" and b is not table]
    stray_rows = R.children(client, stray[0]["id"], fail) if len(stray) == 1 else []
    try:
        status = R._meta(client, ids["STATUS"])
    except NotionError:
        status = None
    sick = [r for r in rows[1:] if "•" in "".join(R.cells_of(r)[3:4])]
    facts = {"heading_ok": True, "table_ok": True, "stray_tables": len(stray), "stray_header_exact": bool(stray_rows) and (stray[0].get("table") or {}).get("table_width") == 3 and R.cells_of(stray_rows[0]) == STRAY_HEADER,
             "stray_is_first": bool(stray) and stray[0] is near[0], "status_found": status is not None and status.get("type") == "paragraph", "status_in_trash": bool(status) and _gone(status),
             "status_in_place": bool(status) and not _gone(status) and bool(near) and R.same_id(near[0]["id"], ids["STATUS"]), "cells_to_fix": len(sick)}
    return facts, {"heading": heading, "ids": ids, "parent_id": parent_id, "stray": stray[0] if len(stray) == 1 else None, "status": status, "sick": sick}


def _safe(parts):
    return [{"type": "text", "text": {"content": (p.get("text") or {}).get("content") or p.get("plain_text") or "", **({"link": p["text"]["link"]} if (p.get("text") or {}).get("link") else {})},
             **({"annotations": p["annotations"]} if p.get("annotations") else {})} for p in parts]


def run(limit, live, environ=os.environ, client=None):
    client = client or card.writer(environ)
    facts, o = plan(client, environ)
    ok = facts["stray_tables"] <= 1 and (facts["stray_tables"] == 0 or (facts["stray_header_exact"] and facts["stray_is_first"])) and facts["status_found"] and facts["cells_to_fix"] <= 1
    if facts["cells_to_fix"]:
        cells = o["sick"][0]["table_row"]["cells"]
        links = [p["text"]["link"]["url"] for p in cells[3] if (p.get("text") or {}).get("link")]
        ok = ok and "SH/FT" in R.cells_of(o["sick"][0])[0] and len(links) == 1 and bool(LINK.match(links[0]))
    try:
        card.validate(client, environ)
        facts["validate"] = "OK"
    except NotionError as error:
        facts["validate"] = str(error)
    facts["plan_ok"] = bool(ok)
    print("hiring-repair:", json.dumps(facts, sort_keys=True))
    if not ok:
        raise fail("REPAIR_STATE_UNEXPECTED")
    if not live or facts["validate"] == "OK":
        return facts
    status_id = o["status"]["id"]
    own = [o["ids"]["CARD"], o["ids"]["STATUS"], o["ids"]["TABLE"]] + ([o["stray"]["id"]] if o["stray"] else [])
    page = R.page_of(client, o["heading"], fail)
    before = R.page_digest(client, page, own, fail, card.SKIP)                              # everything outside the region, before the first write
    if facts["status_in_trash"]:
        client.call("PATCH", f"/blocks/{status_id}", {"in_trash": False})                  # the original paragraph, by its pinned id: no replacement, nothing repinned
        stray_id = o["stray"]["id"] if o["stray"] else ""
        if not _shaped(R.children(client, o["parent_id"], fail), o["ids"], stray_id):
            client.call("PATCH", f"/blocks/{status_id}", {"in_trash": True})               # restored somewhere else: put it back exactly as it was and stop
            raise fail("REPAIR_STATUS_MISPLACED")
    if o["stray"]:
        client.call("PATCH", f"/blocks/{o['stray']['id']}", {"in_trash": True})
    if facts["cells_to_fix"]:
        row = o["sick"][0]
        cells = row["table_row"]["cells"]
        url = next(p["text"]["link"]["url"] for p in cells[3] if (p.get("text") or {}).get("link"))
        client.call("PATCH", f"/blocks/{row['id']}", {"table_row": {"cells": [_safe(c) for c in cells[:3]] + [R.cell_blocks([(M.NOTES_LABEL, url)])[0]]}})
    card.validate(client, environ)                                                          # the publisher's own strict shape, pinned-id and whole-page proof
    after = R.page_digest(client, page, own, fail, card.SKIP)
    facts["validate"], facts["rest_of_page_unchanged"] = "OK", _rest(before, own) == _rest(after, own)
    print("hiring-repair: repaired", json.dumps({k: facts[k] for k in ("stray_tables", "status_in_trash", "cells_to_fix", "rest_of_page_unchanged")}, sort_keys=True))
    return facts
