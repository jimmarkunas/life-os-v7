"""AI Capacity card (LIFE-77, D228; Jim approved the design Oct 20 2026): for each account allowance Oracle can read, what is left, what is used, the window and its reset,
on the Daily Report. Oracle's `limits` request supplies the readings (no model run). The card finds itself by its table's header row: the one top-level callout on the page (or, until it has moved, the one callout beside the
Attention callout) whose table carries this header row. Jim (Oct 21) moved it out of the Daily Report's half-width column: it lives at the top level of the page, under the last full-width divider, so its table
is full width. A run that finds none there inserts it directly after that divider; a card still in the old column is then removed, but only after the new one is written and read back.
After that it writes only the callout's own text and the cells of its one fixed-shape table, in place, read back (the MegIBOW writer's method, D134).
V7_AI_CAPACITY_CARD unset or not "on" -> not configured: nothing read or written.
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
    if extra:
        text += f" · {extra} more not shown"
    lines = [x for x in (codex_line(reading), guard_line(reading), proposed_line(reading), signin_line(reading)) if x]       # one short labelled line each
    return "\n".join([text, *lines]), [HEADER] + cells


def codex_line(reading):
    """The number Jim looks for first: how much of each Codex window is used, and where Cloud Fit stops. None when Oracle reports no Codex pool."""
    pool = next((p for p in reading["pools"] if p["provider"] == "openai"), None)
    if not pool or not pool["windows"]:
        return None
    parts = [f"{WINDOWS.get(w['label'], (w['label'],))[0]} {w['used']:g}% used" for w in sorted(pool["windows"], key=lambda w: 0 if w["label"] == "5h" else 1)]
    stop = pool.get("stop")
    return "Codex allowance · " + " · ".join(parts) + (f" · Cloud Fit stops at {stop}%" if stop else "")


def guard_line(reading):
    cap = reading.get("safety_cap")
    return f"Cloud Fit guard · {cap['runs_today']:,} of {cap['limit']:,} runs today (our own limit, not a provider's)" if cap else None


def rich(text):
    """The callout's rich text for `text`: the label before the first ' · ' of each line is bold, the rest plain. The plain text is exactly `text`."""
    out = []
    for i, line in enumerate(text.split("\n")):
        label, sep, rest = line.partition(" · ")
        tail = ("\n" if i < text.count("\n") else "")
        out.append({"type": "text", "text": {"content": label}, "annotations": {"bold": True}})
        if sep or tail:
            out.append({"type": "text", "text": {"content": sep + rest + tail}})
    return out


SIGNIN_NAMES = {"fit": "Cloud Fit sign-in", "meter": "Usage meter sign-in"}
SIGNIN_WORDS = {"EXPIRED": "EXPIRED: sign in again on Oracle", "COOLDOWN": "FAILING: OpenAI refused its last refresh", "UNREADABLE": "UNREADABLE"}


def signin_line(reading):
    """D239 (Jim, Oct 20: "a permanent solution to the sign in"): each OpenAI sign-in Oracle uses, OK with its expiry in Central time, or what is wrong with it."""
    signins = reading.get("signins")
    if not signins:
        return None
    parts = [f"{SIGNIN_NAMES[k]}: " + (f"OK until {_reset(v['expires'])}" if v["state"] == "OK" else SIGNIN_WORDS[v["state"]])
             for k, v in sorted(signins.items(), key=lambda kv: list(SIGNIN_NAMES).index(kv[0]))]
    return "Sign-ins · " + " · ".join(parts)


def proposed_line(reading):
    """The preview's proposed-usage line: what the Cloud Fit backlog V7 last reported would use of each Codex window, from Oracle's measured per-call share
    (ESTIMATED), or an upper bound while the 1% meter has not moved (BELOW_RESOLUTION). None when no backlog has been reported or nothing is measured yet."""
    pool = next((p for p in reading["pools"] if p["provider"] == "openai" and p.get("estimate")), None)
    if not pool:
        return None
    est, stop = pool["estimate"], pool.get("stop")
    if est.get("proposed") == 0:
        return None                                                      # an empty backlog proposes nothing: no line, rather than "0 jobs: about 0%"
    used = {w["label"]: w["used"] for w in pool["windows"]}
    parts, fits, basis = [], True, 0
    for w in est["windows"]:
        label = WINDOWS.get(w["label"], (w["label"],))[0]
        basis = max(basis, w["basis_calls"])
        if "proposed_percent" in w:
            parts.append(f"about {w['proposed_percent']:g}% of the {label} window")
            share = w["proposed_percent"]
        elif "proposed_percent_at_most" in w:
            parts.append(f"at most {w['proposed_percent_at_most']:g}% of the {label} window")
            share = w["proposed_percent_at_most"]
        else:
            continue
        fits = fits and (not stop or used.get(w["label"], 0) + share < stop)
    if not parts:
        return None
    left = min((w.get("calls_left", w.get("calls_left_at_least")) for w in est["windows"] if "calls_left" in w or "calls_left_at_least" in w), default=None)
    verdict = f"fits under the {stop}% stop" if fits or not stop else f"about {left:,} calls fit before the {stop}% stop; the rest wait for the reset"
    return f"Proposed usage · Cloud Fit backlog ({est['proposed']:,} jobs): " + " and ".join(parts) + f" (ESTIMATED from {basis} metered calls) · {verdict}"


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


def _is_card(client, block):
    """True when `block` is a callout whose first child is a 6-column table with this card's header row."""
    if block.get("type") != "callout":
        return False
    kids = report_region.children(client, block["id"], _fail)
    first = kids[0] if kids else {}
    if first.get("type") != "table" or (first.get("table") or {}).get("table_width") != COLS:
        return False
    rows = report_region.children(client, first["id"], _fail)
    return bool(rows) and _cells(rows[0]) == HEADER


def _page_of(client, meta):
    """The id of the page that holds the block described by `meta`, climbing its parents (a column, a column list, then the page). Anything else is ANCHOR_NOT_FOUND."""
    for _ in range(8):
        parent = (meta.get("parent") if isinstance(meta, dict) else None) or {}
        if parent.get("type") == "page_id" and parent.get("page_id"):
            return parent["page_id"]
        if parent.get("type") != "block_id" or not parent.get("block_id"):
            break
        meta = client.call("GET", f"/blocks/{quote(parent['block_id'], safe='')}")
    raise _fail("ANCHOR_NOT_FOUND")


def find(client, anchor):
    """-> (the card at the page's top level or None, the old card still in the Attention column or None, the page id, the last top-level divider's id or None).
    More than one card in either place is refused."""
    meta = client.call("GET", f"/blocks/{quote(report_region.clean_id(anchor), safe='')}")
    column = ((meta.get("parent") if isinstance(meta, dict) else None) or {})
    if column.get("type") != "block_id":
        raise _fail("ANCHOR_NOT_FOUND")
    page_id = _page_of(client, meta)
    old = [b["id"] for b in report_region.children(client, column["block_id"], _fail) if _is_card(client, b)]
    top = report_region.children(client, page_id, _fail)
    new = [b["id"] for b in top if _is_card(client, b)]
    dividers = [b["id"] for b in top if b.get("type") == "divider"]
    if len(old) > 1 or len(new) > 1:
        raise _fail("CARD_AMBIGUOUS")
    return (new[0] if new else None), (old[0] if old else None), page_id, (dividers[-1] if dividers else None)


def install(client, parent_id, after_id):
    """Insert the empty card (callout, header row, six blank rows) directly after the page's last top-level divider. -> the new callout's id."""
    rows = [{"object": "block", "type": "table_row", "table_row": {"cells": [[{"type": "text", "text": {"content": c}, "annotations": {"bold": True}}] for c in HEADER]}}]
    rows += [{"object": "block", "type": "table_row", "table_row": {"cells": [[] for _ in range(COLS)]}} for _ in range(ROWS - 1)]
    callout = {"object": "block", "type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": "AI Capacity · setting up"}}], "icon": {"type": "emoji", "emoji": "🔋"},
                                                                    "color": "gray_background", "children": [{"object": "block", "type": "table",
                                                                    "table": {"table_width": COLS, "has_column_header": True, "has_row_header": False, "children": rows}}]}}
    result = client.call("PATCH", f"/blocks/{quote(parent_id, safe='')}/children", {"children": [callout], "after": after_id})
    made = [b for b in (result or {}).get("results", []) if b.get("type") == "callout"]
    if not made:
        raise _fail("INSTALL_FAILED")
    return made[0]["id"]


def retire(client, block_id):
    """Trash the card Jim moved away from (it is this card's own earlier copy: same header row, found beside the Attention callout), then read it back as trashed.
    Protection first: only a callout whose table has exactly this card's header row is ever trashed; anything else (Jim's own blocks included) is RETIRE_NOT_OWNED."""
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not _is_card(client, {"type": "callout", "id": block_id}):
        raise _fail("RETIRE_NOT_OWNED")
    client.call("PATCH", f"/blocks/{quote(block_id, safe='')}", {"in_trash": True})
    gone = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(gone, dict) or not (gone.get("in_trash") or gone.get("archived")):
        raise _fail("RETIRE_FAILED")


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
        client.call("PATCH", f"/blocks/{quote(block_id, safe='')}", {"callout": {"rich_text": rich(text)}})
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
    anchor = (environ.get("ATTENTION_CARD_BLOCK_ID") or "").strip()
    if (environ.get("V7_AI_CAPACITY_CARD") or "").strip().lower() != "on" or not anchor:
        return {"status": "not_configured", "rows_written": 0}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    client = client or _writer(environ)
    block_id, old_id, page_id, divider = find(client, anchor)
    installed = False
    if block_id is None:
        if divider is None:
            raise _fail("PLACE_NOT_FOUND")                              # no full-width divider to sit under: nothing is inserted anywhere else
        if not live:
            return {"status": "would_install", "rows_written": 0, "old_card": int(old_id is not None)}
        block_id, installed = install(client, page_id, divider), True
    try:
        reading = (read or (lambda: edge.limits((environ.get("IMESSAGE_ALERTS_HOST") or "").strip())))()
    except edge.EdgeError as error:
        text = f"AI Capacity · DEGRADED · Oracle limits read failed at {now.strftime('%-I:%M %p')} CT ({error}) · showing the last reading"
        result = write(client, block_id, text, None, live)
        return {**result, "status": "degraded", "code": str(error), "failed": 1}           # a failed read is a failed stage, by name; the old card stays until a fresh reading is verified
    text, table = render(reading, now)
    result = write(client, block_id, text, table, live)
    removed = 0
    if old_id is not None and live:
        retire(client, old_id)                                                             # only after the new card is written and read back
        removed = 1
    return {**result, "status": "fresh", "installed": installed, "old_card_removed": removed, "old_card": int(old_id is not None), "pools": len(reading["pools"]), "unavailable": len(reading["unavailable"])}
