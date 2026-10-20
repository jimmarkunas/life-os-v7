"""AI Capacity card (LIFE-77, D228; Jim approved the design Oct 20 2026): for each account allowance Oracle can read, what is left, what is used, the window and its reset,
on the Daily Report. Oracle's `limits` request supplies the readings (no model run); this writes only inside the one callout whose id is AI_CAPACITY_BLOCK_ID: the callout's
own text and the cells of the one fixed-shape table it holds, in place, read back (the MegIBOW writer's method, D134). Unset -> not configured, nothing read or written.
A reading that cannot be had is DEGRADED: the line says so and the table keeps the last reading. Unreadable providers are shown as UNAVAILABLE, never as empty or full."""
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo
import os
import time

from lifeos.jobs.fit import edge
from lifeos.platform import report_region
from lifeos.platform.notion_client import Client, NotionError

TZ = ZoneInfo("America/Chicago")
HEADER = ["Pool", "Remaining", "Used", "Window", "Resets", "Status"]
ROWS, COLS = 7, 6                    # the header and six pool rows; the table is made once at setup and only its cells change
NEAR_REMAINING = 25                  # NEAR LIMIT at or below this percent remaining (the preview's threshold; Jim can change it)
NAMES = {"openai": "Codex allowance", "openrouter": "OpenRouter", "groq": "Groq", "google": "Gemini", "ollama": "Ollama on Oracle", "openclaw_usage": "Other providers"}
WINDOWS = {"5h": ("5-hour", "5 hours"), "Week": ("weekly", "Week")}


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("CAPACITY_" + code)


def _bar(used):
    filled = min(10, max(0, int(used / 10 + 0.5)))
    return "▰" * filled + "▱" * (10 - filled)


def _reset(iso):
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%a %b %-d, %-I:%M %p %Z")
    except ValueError:
        return "—"


def render(reading, now):
    """reading: edge.limits() -> (the callout's text, ROWS x COLS cells). Pure. Tightest window first; the one with the least left is marked."""
    rows = []
    for pool in reading["pools"]:
        name = NAMES.get(pool["provider"], pool["provider"])
        for w in pool["windows"]:
            left = round(100 - w["used"], 1)
            label, window = WINDOWS.get(w["label"], (w["label"], w["label"]))
            stop = pool.get("stop")
            if w["used"] >= 100:
                status = "EXHAUSTED"
            elif stop and w["used"] >= stop:
                status = "RESERVE REACHED"                                # Cloud Fit has stopped at Jim's reserve; the rest is his
            elif left <= NEAR_REMAINING:
                status = "NEAR LIMIT"
            else:
                status = "AVAILABLE"
            used = f"{_bar(w['used'])} {w['used']:g}%" + (f" · Cloud Fit stops at {stop}%" if stop else "")
            rows.append([left, [f"{name} · {label}", f"{left:g}%", used, window, _reset(w["reset_at"]), status]])
    rows.sort(key=lambda r: r[0])
    if rows:
        rows[0][1][0] += " ◀ tightest"
    cells = [r[1] for r in rows] + [[NAMES.get(u, u), "—", "▱▱▱▱▱▱▱▱▱▱ not exposed", "—", "—", "UNAVAILABLE"] for u in reading["unavailable"]]
    extra = max(0, len(cells) - (ROWS - 1))
    cells = cells[:ROWS - 1] + [[""] * COLS] * max(0, ROWS - 1 - len(cells))
    near = sum(1 for c in cells if c[5] in ("NEAR LIMIT", "RESERVE REACHED"))
    exhausted = sum(1 for c in cells if c[5] == "EXHAUSTED")
    text = f"AI Capacity · Updated {now.strftime('%-I:%M %p')} CT · {near} near limit · {exhausted} exhausted"
    cap = reading.get("safety_cap")
    if cap:
        text += f" · our Cloud Fit guard {cap['runs_today']}/{cap['limit']} runs today (not a provider limit)"
    if extra:
        text += f" · {extra} more not shown"
    return text, [HEADER] + cells


def _cells(row):
    return [report_region.plain({"type": "x", "x": {"rich_text": cell}}) for cell in (row.get("table_row") or {}).get("cells") or []]


def _callout_text(meta):
    return report_region.plain({"type": "callout", "callout": (meta or {}).get("callout") or {}})


def locate(client, block_id):
    """-> (callout meta, table rows). Anything but one callout holding one 6-column, 7-row table raises: nothing is written."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout":
        raise _fail("BLOCK_NOT_FOUND")
    tables = [b for b in report_region.children(client, block_id, _fail) if b.get("type") == "table"]
    if len(tables) != 1 or (tables[0].get("table") or {}).get("table_width") != COLS:
        raise _fail("BLOCK_SHAPE")
    rows = report_region.children(client, tables[0]["id"], _fail)
    if len(rows) != ROWS or any(len(_cells(r)) != COLS for r in rows):
        raise _fail("BLOCK_SHAPE")
    return meta, rows


def write(client, block_id, text, table, live, sleep=time.sleep):
    """In place only: each changed row and the callout's own text are PATCHed, then read back. table None leaves the table as it is (DEGRADED)."""
    meta, rows = locate(client, block_id)
    if table is not None and (len(table) != ROWS or any(len(r) != COLS for r in table)):
        raise _fail("RENDER_SHAPE")
    changed = [(r["id"], want) for r, want in zip(rows, table)] if table is not None else []
    changed = [(rid, want) for (rid, want), r in zip(changed, rows) if _cells(r) != want]
    text_changed = _callout_text(meta) != text
    result = {"rows_written": 0, "text_written": False, "rows_changed": len(changed), "text_changed": text_changed}
    if not live or not (changed or text_changed):
        return result
    for row_id, want in changed:
        bold = {"annotations": {"bold": True}} if row_id == rows[0]["id"] else {}      # the header row
        client.call("PATCH", f"/blocks/{quote(row_id, safe='')}", {"table_row": {"cells": [[{"type": "text", "text": {"content": c}, **bold}] if c else [] for c in want]}})
        result["rows_written"] += 1
    if text_changed:
        client.call("PATCH", f"/blocks/{quote(block_id, safe='')}", {"callout": {"rich_text": [{"type": "text", "text": {"content": text}}]}})
        result["text_written"] = True
    for attempt in range(4):                                                          # authoritative read-back; Notion may briefly show the old state
        if attempt:
            sleep(3)
        after_meta, after_rows = locate(client, block_id)
        if (table is None or [_cells(r) for r in after_rows] == table) and _callout_text(after_meta) == text:
            return result
    raise _fail("VERIFY_FAILED")


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, client=None, read=None, now=None):
    block_id = report_region.clean_id((environ.get("AI_CAPACITY_BLOCK_ID") or "").strip())
    if not block_id:
        return {"status": "not_configured", "rows_written": 0}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    client = client or _writer(environ)
    try:
        reading = (read or (lambda: edge.limits((environ.get("IMESSAGE_ALERTS_HOST") or "").strip())))()
    except edge.EdgeError as error:
        text = f"AI Capacity · DEGRADED · Oracle limits read failed at {now.strftime('%-I:%M %p')} CT ({error}) · showing the last reading"
        result = write(client, block_id, text, None, live)
        return {**result, "status": "degraded", "code": str(error), "failed": 1}           # a failed read is a failed stage, by name
    text, table = render(reading, now)
    result = write(client, block_id, text, table, live)
    return {**result, "status": "fresh", "pools": len(reading["pools"]), "unavailable": len(reading["unavailable"])}
