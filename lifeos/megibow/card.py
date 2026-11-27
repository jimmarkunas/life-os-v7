"""The MegIBOW block writer: edits only the machine-owned callout (MEGIBOW_BLOCK_ID) and its one table, in place.

Every write is read back. Nothing around the block changes; a block of unexpected shape is DEGRADED and nothing is
written.
"""

from urllib.parse import quote

from lifeos.platform import readback, report_region
from lifeos.platform.notion_client import NotionError

ROWS, COLS = 7, 10


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("MEGIBOW_" + code)


def _cells(row):
    return [
        report_region.plain({"type": "x", "x": {"rich_text": cell}})
        for cell in (row.get("table_row") or {}).get("cells") or []
    ]


def _plain_callout(meta):
    return report_region.plain({"type": "callout", "callout": (meta or {}).get("callout") or {}})


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
    """text: the callout's own text; table_rows: ROWS x COLS strings, or None to leave the table alone (DEGRADED)."""
    meta, _, rows = locate(client, block_id)
    changed = []
    if table_rows is not None:
        if len(table_rows) != ROWS or any(len(r) != COLS for r in table_rows):
            raise _fail("RENDER_SHAPE")
        changed = [(r["id"], want) for r, want in zip(rows, table_rows) if _cells(r) != want]
    text_changed = _plain_callout(meta) != text
    result = {
        "rows_written": 0,
        "text_written": False,
        "verified": True,
        "rows_changed": len(changed),
        "text_changed": text_changed,
    }
    if not live:
        return result
    last_id = rows[-1]["id"] if rows else None
    for row_id, want in changed:
        bold = {"annotations": {"bold": True}} if row_id == last_id else {}
        client.call(
            "PATCH",
            f"/blocks/{quote(row_id, safe='')}",
            {"table_row": {"cells": [[{"type": "text", "text": {"content": c}, **bold}] for c in want]}},
        )
        result["rows_written"] += 1
    if text_changed:
        client.call(
            "PATCH",
            f"/blocks/{quote(block_id, safe='')}",
            {"callout": {"rich_text": [{"type": "text", "text": {"content": text}}]}},
        )
        result["text_written"] = True
    if not (changed or text_changed):
        return result
    ok, _ = readback.confirmed(
        lambda: locate(client, block_id),
        lambda after: (
            (table_rows is None or [_cells(r) for r in after[2]] == table_rows) and _plain_callout(after[0]) == text
        ),
    )
    if ok:
        return result
    raise _fail("VERIFY_FAILED")


def read_legacy(client, block_id):
    """The one-time Cumulative seed: a "Legacy totals: Outreach 20, ..." paragraph -> counts, or None."""
    from lifeos.megibow import classify as C  # noqa: PLC0415

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
    """ "Phone calls: Oct 5 Recruiter Calls 1; ..." -> ({monday: {activity: n}}, unreadable entry count)."""
    from datetime import date, datetime as dt, timedelta  # noqa: PLC0415
    from lifeos.megibow import classify as C  # noqa: PLC0415
    from lifeos.megibow.windows import monday  # noqa: PLC0415

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
    for block in report_region.children(client, block_id, _fail):
        text = report_region.plain(block)
        if block.get("type") == "paragraph" and text.lower().startswith(MANUAL):
            found, bad = parse_manual(text, today)
            return found, bad, True
    return {}, 0, False


def ensure_manual_line(client, block_id):
    client.call_once(
        "PATCH",
        f"/blocks/{quote(block_id, safe='')}/children",
        {
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Phone calls: none"}}]},
                }
            ]
        },
    )
