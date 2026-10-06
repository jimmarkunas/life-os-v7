"""The MegIBOW block writer (D134). Writes only inside the one machine-owned callout whose id is MEGIBOW_BLOCK_ID: the callout's own text (status line, warnings) and the cells of the
one table it holds. Every edit is in place (a PATCH of an existing block): nothing is inserted, deleted, moved or reordered, so nothing around the block can change.
A block that is not the expected shape is DEGRADED and nothing is written; a write is read back and compared exactly."""
from urllib.parse import quote
import time

from lifeos.platform import report_region
from lifeos.platform.notion_client import NotionError

ROWS, COLS = 7, 10


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("MEGIBOW_" + code)


def _cells(row):
    return [report_region.plain({"type": "x", "x": {"rich_text": cell}}) for cell in (row.get("table_row") or {}).get("cells") or []]


def _plain_callout(meta):
    return report_region.plain({"type": "callout", "callout": (meta or {}).get("callout") or {}})


def find(client, page_id):
    """The one top-level callout on the page that holds exactly one 10-column table with 7 rows (the status text changes, the shape does not). Not exactly one -> DEGRADED, nothing written."""
    found = []
    for block in report_region.children(client, page_id, _fail):
        if block.get("type") != "callout":
            continue
        try:
            locate(client, block["id"])
        except NotionError:
            continue
        found.append(block["id"])
    if len(found) != 1:
        raise _fail("BLOCK_NOT_FOUND")
    return found[0]


def locate(client, block_id):
    """-> (callout meta, table block, table rows). Anything unexpected raises: the last good block stays."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout":
        raise _fail("BLOCK_NOT_FOUND")
    tables = [b for b in report_region.children(client, block_id, _fail) if b.get("type") == "table"]
    if len(tables) != 1 or (tables[0].get("table") or {}).get("table_width") != COLS:
        raise _fail("BLOCK_SHAPE")
    rows = report_region.children(client, tables[0]["id"], _fail)
    if len(rows) != ROWS or any(len(_cells(r)) != COLS for r in rows):
        raise _fail("BLOCK_SHAPE")
    return meta, tables[0], rows


def write(client, block_id, text, table_rows, live):
    """text: the callout's own text; table_rows: ROWS x COLS strings, or None to leave the table alone (DEGRADED). -> {"rows_written", "text_written", "verified"}."""
    meta, _, rows = locate(client, block_id)
    changed = []
    if table_rows is not None:
        if len(table_rows) != ROWS or any(len(r) != COLS for r in table_rows):
            raise _fail("RENDER_SHAPE")
        changed = [(r["id"], want) for r, want in zip(rows, table_rows) if _cells(r) != want]
    text_changed = _plain_callout(meta) != text
    result = {"rows_written": 0, "text_written": False, "verified": True, "rows_changed": len(changed), "text_changed": text_changed}
    if not live:
        return result
    last_id = rows[-1]["id"] if rows else None
    for row_id, want in changed:
        bold = {"annotations": {"bold": True}} if row_id == last_id else {}               # the totals row is the last row and is bold
        client.call("PATCH", f"/blocks/{quote(row_id, safe='')}", {"table_row": {"cells": [[{"type": "text", "text": {"content": c}, **bold}] for c in want]}})
        result["rows_written"] += 1
    if text_changed:
        client.call("PATCH", f"/blocks/{quote(block_id, safe='')}", {"callout": {"rich_text": [{"type": "text", "text": {"content": text}}]}})
        result["text_written"] = True
    if not (changed or text_changed):
        return result
    for attempt in range(4):                                                          # authoritative read-back; Notion may briefly show the old state
        if attempt:
            time.sleep(3)
        after_meta, _, after_rows = locate(client, block_id)
        table_ok = table_rows is None or [_cells(r) for r in after_rows] == table_rows
        if table_ok and _plain_callout(after_meta) == text:
            return result
    raise _fail("VERIFY_FAILED")


def read_legacy(client, block_id):
    """The one-time seed for Cumulative: a paragraph inside the block reading "Legacy totals: Outreach 20, Scheduled 13, ..." -> counts, or None when there is none."""
    from lifeos.megibow import classify as C                                   # noqa: PLC0415
    for block in report_region.children(client, block_id, _fail):
        text = report_region.plain(block)
        if block.get("type") == "paragraph" and text.lower().startswith("legacy totals:"):
            found = {}
            for part in text.split(":", 1)[1].split(","):
                name, _, number = part.strip().rpartition(" ")
                if name in C.ACTIVITIES and number.isdigit():
                    found[name] = int(number)
            return found if set(found) == set(C.ACTIVITIES) else None
    return None


MANUAL = "phone calls:"


def parse_manual(text, today):
    """Jim's own count line, "Phone calls: Oct 5 Recruiter Calls 1; Oct 7 Company Calls 2" -> ({monday: {activity: n}}, unreadable entry count). A date is "Mon D" in the current year
    (the previous year when that would be more than a week ahead); an entry that does not parse is counted and ignored, never guessed."""
    from datetime import date, datetime as dt, timedelta                         # noqa: PLC0415
    from lifeos.megibow import classify as C                                     # noqa: PLC0415
    from lifeos.megibow.windows import monday                                    # noqa: PLC0415
    out, bad = {}, 0
    body = text.split(":", 1)[1] if ":" in text else ""
    for entry in body.replace("\n", ";").split(";"):
        entry = " ".join(entry.split())
        if not entry or entry.lower() in ("none", "-"):
            continue
        parts = entry.split(" ")
        try:
            day = dt.strptime(" ".join(parts[:2]) + f" {today.year}", "%b %d %Y").date()
            if day > today + timedelta(days=7):
                day = day.replace(year=today.year - 1)
            activity, number = " ".join(parts[2:-1]), parts[-1]
            if activity not in C.ACTIVITIES or not number.isdigit():
                raise ValueError
        except (ValueError, IndexError):
            bad += 1
            continue
        week = out.setdefault(monday(day), {})
        week[activity] = week.get(activity, 0) + int(number)
    return out, bad


def read_manual(client, block_id, today):
    """-> ({monday: {activity: n}}, unreadable entries, line present). Reads the paragraph inside the block that starts with "Phone calls:"; absent means no entries."""
    for block in report_region.children(client, block_id, _fail):
        text = report_region.plain(block)
        if block.get("type") == "paragraph" and text.lower().startswith(MANUAL):
            found, bad = parse_manual(text, today)
            return found, bad, True
    return {}, 0, False


def ensure_manual_line(client, block_id):
    """Add the empty "Phone calls: none" paragraph at the end of the block once, so Jim has a line to edit. Never touches an existing one."""
    client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Phone calls: none"}}]}}]})
