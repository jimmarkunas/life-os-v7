"""Ledger page layout migration (Jim, Oct 27: the approved two-column page, and no marker line showing). Moves a flat v7.jd.1 Job Ledger page to v7.jd.2 (lifeos.platform.ledger_body). Dry unless live; counts only.

A page is touched only when its own blocks are EXACTLY what V7 wrote from the stored description (same types, same text): nothing Jim wrote can be moved, and a page that differs is counted and left alone.
Per page, in this order, each step proven before the next: (1) the column_list is added right after the marker (the old description is still there), (2) its toggle is filled and read back, (3) the marker is
replaced by the hidden one in place, (4) the old description blocks are deleted, (5) the top-level blocks are proven to be the marker, the column_list and exactly the blocks that followed the old description.
An interrupted page is finished by the next run (the column_list is already there). The page's network state is cleared so the next surface run moves its leads block into the right column.
Done pages are remembered in v7_ledger_layout (job key, page id, state) so a later run does not read them again."""
import json
import os
from datetime import datetime, timezone

from lifeos.platform import db, ledger_body as body
from lifeos.platform.notion_client import Client, NotionError

STATE = "v7_ledger_layout"
SCHEMA = f"""CREATE TABLE IF NOT EXISTS {STATE} (
    job_key CHAR(64) NOT NULL PRIMARY KEY,
    page_id VARCHAR(64) NOT NULL, state VARCHAR(16) NOT NULL, checked_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
DONE, LEFT = "DONE", "LEFT"                  # DONE: the page has the layout. LEFT: its blocks are not exactly V7's; not touched (re-checked only if the row is removed)
DEFAULT_PAGES = 20


class LayoutError(NotionError):
    """Fixed codes only."""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _text(block):
    return body._plain(block)


def _same(actual, expected):
    return len(actual) == len(expected) and all(a.get("type") == e["type"] and _text(a) == _text(e) for a, e in zip(actual, expected))


def read(connection, limit):
    """-> [(job_key, page_id, description dict)] of PUBLISHED jobs not yet DONE or LEFT, oldest first. A missing state table reads as nothing done."""
    with connection.cursor() as cursor:
        try:
            cursor.execute(f"SELECT j.dedupe_key, j.notion_page_id, d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text FROM v7_jobs j "
                           f"JOIN v7_job_descriptions d ON d.job_id = j.id LEFT JOIN {STATE} s ON s.job_key = j.dedupe_key "
                           f"WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL AND s.job_key IS NULL ORDER BY j.id LIMIT %s", (limit,))
        except Exception:                                              # noqa: BLE001 - the state table may not exist before the first live run
            cursor.execute("SELECT j.dedupe_key, j.notion_page_id, d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text FROM v7_jobs j "
                           "JOIN v7_job_descriptions d ON d.job_id = j.id WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL ORDER BY j.id LIMIT %s", (limit,))
        rows = cursor.fetchall()
    out = []
    for key, page, summary, resp, req, qual, full in rows:
        desc = {"summary": summary, "responsibilities": resp, "requirements": req, "qualifications": qual}
        if not any(desc.values()):
            desc["summary"] = (full or "")[:6000]                      # the fallback publish applies
        out.append((key, page, desc))
    return out


def migrate(client, page_id, key, desc, live):
    """-> "done" (already the layout) | "migrated" | "would_migrate" | "left" (blocks are not exactly V7's). Raises LayoutError on any read-back that does not match."""
    top = body._kids(client, page_id)
    old = body.legacy_blocks(key, desc)[1:]                            # what V7 wrote after the marker
    if not top or not body.is_marker(top[0], key):
        return "left"
    layout = len(top) > 1 and top[1].get("type") == "column_list"
    start = 2 if layout else 1
    old_there = _same(top[start:start + len(old)], old)
    if layout and not old_there:
        found = body.find(client, page_id, top)
        if found and found["toggle"] and _text(top[0]) == "\u2060":    # finished: hidden marker, layout, nothing of the old description left
            return "done"
    if not layout and not old_there:
        return "left"
    if not live:
        return "would_migrate"
    if not layout:                                                      # (1) the column_list, right after the marker
        client.call("PATCH", f"/blocks/{page_id}/children", {"children": [body.column_list(desc)], "after": top[0]["id"]})
    body.fill(client, page_id, desc, strict=True)                       # (2) the toggle (a replay writes nothing)
    top = body._kids(client, page_id)
    found = body.find(client, page_id, top)
    if not found or not found["toggle"] or len([b for b in top if b.get("type") == "column_list"]) != 1:
        raise LayoutError("LAYOUT_COLUMNS_MISSING")
    want = [_text(b) for b in body.full_blocks(desc)]
    if [_text(b) for b in body._kids(client, found["toggle"])] != want:
        raise LayoutError("LAYOUT_TOGGLE_MISMATCH")
    shown = [_text(b) for b in body._kids(client, found["left"]) if b.get("type") not in ("toggle",)]
    if shown != [_text(b) for b in body.column_list(desc)["column_list"]["children"][0]["column"]["children"] if b["type"] != "toggle"]:
        raise LayoutError("LAYOUT_OVERVIEW_MISMATCH")
    after = [b for b in top[2:]]
    if not _same(after[:len(old)], old):
        raise LayoutError("LAYOUT_OLD_BLOCKS_CHANGED")                    # the page changed under us: nothing is deleted
    client.call("PATCH", f"/blocks/{top[0]['id']}", {"paragraph": body.marker_block(key)["paragraph"]})      # (3) the hidden marker, in place
    for block in after[:len(old)]:                                      # (4) the old description
        client.call("DELETE", f"/blocks/{block['id']}")
    final = body._kids(client, page_id)                                 # (5) proof
    if [b["id"] for b in final] != [top[0]["id"], found["list"]] + [b["id"] for b in after[len(old):]] or not body.is_marker(final[0], key) or _text(final[0]) != "\u2060":
        raise LayoutError("LAYOUT_READBACK_MISMATCH")
    return "migrated"


def run(limit, live, environ=os.environ, connection=None, client=None):
    if connection is not None:
        return _run(limit, live, environ, connection, client)
    with db.connect() as opened:
        return _run(limit, live, environ, opened, client)


def _run(limit, live, environ, connection, client):
    budget = limit if isinstance(limit, int) and limit > 0 else DEFAULT_PAGES
    counts = {"live": bool(live), "read": 0, "done": 0, "migrated": 0, "would_migrate": 0, "left": 0, "failed": 0, "why": {}}
    if live:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA)
    client = client or Client(environ)
    for key, page, desc in read(connection, budget):
        counts["read"] += 1
        try:
            outcome = migrate(client, page, key, desc, live)
        except NotionError as error:
            counts["failed"] += 1
            counts["why"][str(error)[:40]] = counts["why"].get(str(error)[:40], 0) + 1
            continue
        counts[outcome] += 1
        if live and outcome in ("done", "migrated", "left"):
            with connection.cursor() as cursor:
                cursor.execute(f"REPLACE INTO {STATE} (job_key, page_id, state, checked_at) VALUES (%s,%s,%s,%s)", (key, page, LEFT if outcome == "left" else DONE, _now()))
                if outcome == "migrated":
                    try:
                        cursor.execute("DELETE FROM v7_network_page_state WHERE job_key=%s", (key,))      # the surface reads the page again and moves its leads block into the right column
                    except Exception:                                  # noqa: BLE001 - that table exists only after the first live surface run
                        pass
            connection.commit()
    print("ledger-layout:", json.dumps(counts, sort_keys=True))          # counts only
    if counts["failed"]:
        raise LayoutError("LAYOUT_PAGES_FAILED")
    return counts
