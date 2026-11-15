"""MegIBOW on Jim & Matt V3 (LIFE-672): the two V3 callouts (Jim from the engine, Matt from his own typed database), Matt's warnings and both summary pages.

A V3 callout is: heading, gray stamp line, one 7 x 10 table (Activity, 8 Mondays, Cumulative), note lines, then `Summary for Claude: <page>`. Only the stamp, the table
cells, one machine notes paragraph and the summary page are written, each read back. Any other shape, or a block not found by its id, writes nothing: MEGIBOW_V3_* codes."""
from datetime import date
from statistics import median
import re
from urllib.parse import quote

from lifeos.megibow import classify as C, project as P, warnings as W
from lifeos.megibow.windows import chicago, elapsed_business_days, monday, visible_weeks
from lifeos.platform import readback, report_region
from lifeos.platform.notion_client import NotionError

DASH = "–"
ROWS, COLS = 7, 10
LABELS = ("Outreach", "Scheduled", "Networking calls", "Recruiter calls", "Company calls")
SUMMARY, DASH_NOTE, PHONE = "summary for claude:", "a dash means", "phone calls:"
SUMMARY_MAX = 1000
MIN_ENTERED = 4
REGISTER_TITLE = "V3 block ID register (Jim & Matt V3)"
MATT_SOURCE_TITLE = "Matt — MegIBOW weekly (manual)"
V3_PAGE_TITLE = "Jim & Matt V3"
HISTORY_TITLE = {"Jim": "Megibow history (Jim)", "Matt": "Megibow history (Matt)"}          # LIFE-687: one history page per person, linked from the callout's note line
TYPED_NOTE = "* typed over by Jim in the weekly database"
MATT_FIELDS = {"Week Start": "date", **{a: "number" for a in C.ACTIVITIES}}


class V3Error(NotionError):
    """Fixed codes only."""


def _text(block):
    return report_region.plain(block).strip()


def _rich(parts, color=None):
    """[(text, link or None)] -> Notion rich text."""
    out = []
    for content, link in parts:
        item = {"type": "text", "text": {"content": content, **({"link": {"url": link}} if link else {})}}
        if color:
            item["annotations"] = {"color": color}
        out.append(item)
    return out


def _flat(parts):
    return "".join(t for t, _ in parts)


def _cells(row):
    return [report_region.plain({"type": "x", "x": {"rich_text": cell}}) for cell in (row.get("table_row") or {}).get("cells") or []]


def _get_block(client, block_id):
    try:
        return client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    except NotionError as error:
        if "404" in str(error) or "400" in str(error):
            raise V3Error("MEGIBOW_V3_BLOCK_NOT_FOUND") from None             # also what a missing Notion connection on the V3 page looks like
        raise


def is_v3(client, block_id):
    """True when the block is a V3 callout (its first child is a heading). A block that cannot be found raises MEGIBOW_V3_BLOCK_NOT_FOUND."""
    meta = _get_block(client, block_id)
    if not isinstance(meta, dict) or meta.get("type") != "callout":
        return False
    kids = report_region.children(client, block_id, V3Error)
    return bool(kids) and kids[0].get("type") in ("heading_2", "heading_3")


def locate(client, block_id):
    """-> {stamp, table, rows, notes (block or None), anchor (id), summary (page id)}. Anything unexpected raises; nothing is written."""
    meta = _get_block(client, block_id)
    if not isinstance(meta, dict) or meta.get("type") != "callout":
        raise V3Error("MEGIBOW_V3_BLOCK_NOT_FOUND")
    kids = report_region.children(client, block_id, V3Error)
    tables = [i for i, b in enumerate(kids) if b.get("type") == "table"]
    if (len(kids) < 4 or kids[0].get("type") not in ("heading_2", "heading_3") or kids[1].get("type") != "paragraph" or len(tables) != 1 or tables[0] != 2
            or (kids[2].get("table") or {}).get("table_width") != COLS):
        raise V3Error("MEGIBOW_V3_BLOCK_SHAPE")
    last = kids[-1]
    mention = [p for p in (last.get("paragraph") or {}).get("rich_text") or [] if p.get("type") == "mention" and (p.get("mention") or {}).get("type") == "page"]
    if last.get("type") != "paragraph" or not _text(last).lower().startswith(SUMMARY) or len(mention) != 1:
        raise V3Error("MEGIBOW_V3_BLOCK_SHAPE")
    rows = report_region.children(client, kids[2]["id"], V3Error)
    if len(rows) != ROWS or any(len(_cells(r)) != COLS for r in rows):
        raise V3Error("MEGIBOW_V3_BLOCK_SHAPE")
    anchor = 2
    if kids[3].get("type") == "paragraph" and _text(kids[3]).lower().startswith(DASH_NOTE):        # Matt's fixed note line stays where it is
        anchor = 3
    after = kids[anchor + 1] if anchor + 1 < len(kids) - 1 else None
    notes = after if after is not None and after.get("type") == "paragraph" and not _text(after).lower().startswith(PHONE) else None
    return {"stamp": kids[1], "table": kids[2], "rows": rows, "notes": notes, "anchor": kids[anchor]["id"], "summary": mention[0]["mention"]["page"]["id"]}


def write(client, block_id, stamp, table_rows, notes, live):
    """stamp: the gray first line; table_rows: ROWS x COLS strings or None (leave the table); notes: [(text, link)] or None (leave them). -> counts."""
    found = locate(client, block_id)
    if table_rows is not None and (len(table_rows) != ROWS or any(len(r) != COLS for r in table_rows)):
        raise V3Error("MEGIBOW_V3_RENDER_SHAPE")
    changed = [(r["id"], want) for r, want in zip(found["rows"], table_rows or [], strict=False) if _cells(r) != want]
    stamp_changed = _text(found["stamp"]) != stamp
    notes_changed = notes is not None and (found["notes"] is None or _text(found["notes"]) != _flat(notes).strip())
    out = {"rows_written": 0, "stamp_written": False, "notes_written": False, "rows_changed": len(changed), "summary_page": found["summary"], "verified": True}
    if not live or not (changed or stamp_changed or notes_changed):
        return out
    for row_id, want in changed:
        client.call_once("PATCH", f"/blocks/{quote(row_id, safe='')}", {"table_row": {"cells": [[{"type": "text", "text": {"content": c}}] for c in want]}})
        out["rows_written"] += 1
    if stamp_changed:
        client.call_once("PATCH", f"/blocks/{quote(found['stamp']['id'], safe='')}", {"paragraph": {"rich_text": _rich([(stamp, None)], "gray")}})
        out["stamp_written"] = True
    if notes_changed:
        body = {"paragraph": {"rich_text": _rich(notes)}}
        if found["notes"] is not None:
            client.call_once("PATCH", f"/blocks/{quote(found['notes']['id'], safe='')}", body)
        else:                                                                             # the first run adds the one notes paragraph inside the callout, never outside it
            client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": [{"object": "block", "type": "paragraph", **body}], "after": found["anchor"]})
        out["notes_written"] = True

    def matches(after):
        return (_text(after["stamp"]) == stamp and (table_rows is None or [_cells(r) for r in after["rows"]] == table_rows)
                and (notes is None or (after["notes"] is not None and _text(after["notes"]) == _flat(notes).strip())))
    ok, _ = readback.confirmed(lambda: locate(client, block_id), matches)
    if not ok:
        raise V3Error("MEGIBOW_V3_VERIFY_FAILED")
    return out


def write_summary(client, page_id, text, live):
    """The summary page for Claude: one plain paragraph of at most SUMMARY_MAX characters, edited in place and read back. -> True when written."""
    text = text[:SUMMARY_MAX]
    kids = report_region.children(client, page_id, V3Error)
    if len(kids) != 1 or kids[0].get("type") != "paragraph":
        raise V3Error("MEGIBOW_V3_SUMMARY_SHAPE")
    if _text(kids[0]) == text.strip() or not live:
        return False
    client.call_once("PATCH", f"/blocks/{quote(kids[0]['id'], safe='')}", {"paragraph": {"rich_text": _rich([(text, None)])}})
    ok, _ = readback.confirmed(lambda: report_region.children(client, page_id, V3Error), lambda after: len(after) == 1 and _text(after[0]) == text.strip())
    if not ok:
        raise V3Error("MEGIBOW_V3_VERIFY_FAILED")
    return True


def _cell(value):
    return DASH if value is None else str(value)


def table(weeks, counts, cumulative, typed=None):
    """7 rows x 10 columns: header (Activity, the 8 Mondays, Cumulative), the five activities, Total. A week that is None is a dash everywhere, never 0.
    `typed` {monday: activities} marks a number Jim typed over (LIFE-687) with a trailing star."""
    typed = typed or {}
    rows = [["Activity"] + [f"{w.strftime('%b')} {w.day}" for w in weeks] + ["Cumulative"]]
    for activity, label in zip(C.ACTIVITIES, LABELS, strict=True):
        rows.append([label] + [_cell(counts[w][activity] if counts[w] else None) + ("*" if activity in typed.get(w, ()) else "") for w in weeks]
                    + [_cell(cumulative[activity] if cumulative else None)])
    rows.append(["Total"] + [_cell(P.total(counts[w]) if counts[w] else None) for w in weeks] + [_cell(P.total(cumulative) if cumulative else None)])
    return rows


def summary(who, stamp, weeks, counts, cumulative, notes):
    """Plain text for the summary page: one line per week, counts only, at most SUMMARY_MAX characters (trailing note lines are dropped first)."""
    def line(c):
        return DASH if not c else "/".join(str(c[a]) for a in C.ACTIVITIES) + f" = {P.total(c)}"
    lines = [f"Megibow ({who}) · {stamp}", "Each week: Outreach/Scheduled/Networking/Recruiter/Company = Total; a dash is a week with no entry."]
    lines += [f"Week of {w.strftime('%b')} {w.day}: {line(counts[w])}" for w in weeks] + [f"Cumulative: {line(cumulative)}"] + list(notes)
    while len("\n".join(lines)) > SUMMARY_MAX and len(lines) > len(weeks) + 3:
        lines.pop()
    return "\n".join(lines)[:SUMMARY_MAX]


def _title(obj):
    if isinstance(obj.get("title"), list):
        return "".join(p.get("plain_text") or "" for p in obj["title"])
    for prop in (obj.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(p.get("plain_text") or "" for p in prop.get("title") or [])
    return ""


def find(client, title, kind):
    """The one page or data source whose title is exactly `title` (no secret holds these ids). None or several -> MEGIBOW_V3_TARGET_NOT_FOUND."""
    found = client.call("POST", "/search", {"query": title, "filter": {"property": "object", "value": kind}, "page_size": 100})
    hits = [r["id"] for r in (found or {}).get("results") or [] if isinstance(r, dict) and _title(r).strip() == title and not r.get("in_trash") and not r.get("archived")]
    if len(hits) != 1:
        raise V3Error("MEGIBOW_V3_TARGET_NOT_FOUND")
    return hits[0]


def registered(client, key):
    """A callout id from the V3 block ID register row `key` (jim.megibow, matt.megibow), accepted only when that callout sits on the Jim & Matt V3 page,
    so an edited register row can never point a write anywhere else. -> id; else MEGIBOW_V3_TARGET_NOT_FOUND."""
    pattern = re.compile(r"(?:^|\s)" + re.escape(key) + r"\s+([0-9a-fA-F-]{32,36})\b")
    register = find(client, REGISTER_TITLE, "page")
    for block in report_region.children(client, register, V3Error):
        match = pattern.search(report_region.plain(block))
        if match:
            block_id = match.group(1)
            parent = (_get_block(client, block_id).get("parent") or {}).get("page_id") or ""
            if not parent or _title(client.call("GET", f"/pages/{quote(parent, safe='')}")).strip() != V3_PAGE_TITLE:
                raise V3Error("MEGIBOW_V3_TARGET_NOT_FOUND")
            return block_id
    raise V3Error("MEGIBOW_V3_TARGET_NOT_FOUND")


def matt_block(client):
    """Matt's callout id, from the register row `matt.megibow`."""
    return registered(client, "matt.megibow")


def matt_rows(client, source_id):
    """Matt's typed weeks -> ({monday: counts}, rows ignored). A row is entered when at least one number is filled; a blank number in an entered row is 0.
    Two rows for one week: the most recently edited wins (counted in `ignored`). An unreadable or changed database raises."""
    meta = client.call("GET", f"/data_sources/{quote(source_id, safe='')}")
    props = (meta or {}).get("properties") or {}
    if any((props.get(n) or {}).get("type") != t for n, t in MATT_FIELDS.items()):
        raise V3Error("MEGIBOW_V3_MATT_SCHEMA")
    weeks, edited, ignored, cursor = {}, {}, 0, None
    for _ in range(20):
        body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        page = client.query_data_source(source_id, body)
        for row in page.get("results") or []:
            p = row.get("properties") or {}
            start = ((p.get("Week Start") or {}).get("date") or {}).get("start") or ""
            numbers = {a: (p.get(a) or {}).get("number") for a in C.ACTIVITIES}
            try:
                week = monday(date.fromisoformat(start[:10]))
                if any(n is not None and (n < 0 or n != int(n)) for n in numbers.values()):
                    raise ValueError
            except ValueError:
                ignored += 1
                continue
            if all(n is None for n in numbers.values()):
                continue
            stamp = row.get("last_edited_time") or ""
            if week in weeks:
                ignored += 1
                if stamp <= edited[week]:
                    continue
            weeks[week], edited[week] = {a: int(n or 0) for a, n in numbers.items()}, stamp
        if not page.get("has_more"):
            return weeks, ignored
        cursor = page.get("next_cursor")
    raise V3Error("MEGIBOW_V3_MATT_READ_INCOMPLETE")


def matt_project(entered, today):
    """-> {weeks, counts (None = not entered), cumulative (entered weeks up to this week; None when there are none), entered}."""
    weeks = visible_weeks(today)
    counts = {w: entered.get(w) for w in weeks}
    upto = [c for w, c in entered.items() if w <= weeks[-1]]
    cumulative = {a: sum(c[a] for c in upto) for a in C.ACTIVITIES} if upto else None
    return {"weeks": weeks, "counts": counts, "cumulative": cumulative, "entered": sum(1 for c in counts.values() if c)}


def matt_warnings(proj, now):
    """Counts-only warnings (no evidence or forward-pipeline rules): no new meetings, outreach drop, conversation drop. None until MIN_ENTERED weeks are entered,
    and none from a week that is not entered. -> (warnings, suppressed)."""
    current = proj["counts"][proj["weeks"][-1]]
    baseline = [c for w in proj["weeks"][:-1] for c in [proj["counts"][w]] if c]
    if proj["entered"] < MIN_ENTERED or current is None:
        return [], True
    weekday, elapsed = chicago(now).weekday(), elapsed_business_days(now)
    out = []
    if weekday >= 3 and current[C.SCHEDULED] == 0:
        out.append(W.NO_MEETINGS)
    trend = len(baseline) >= MIN_ENTERED
    med_out = median([c[C.OUTREACH] for c in baseline]) if baseline else 0
    med_calls = median([W.calls(c) for c in baseline]) if baseline else 0
    if trend and med_out >= 3 and weekday >= 2 and current[C.OUTREACH] < 0.6 * med_out * elapsed / 5:
        out.append(W.OUTREACH_DROP)
    if trend and med_calls >= 2 and ((weekday >= 5 and W.calls(current) < 0.5 * med_calls) or (weekday == 4 and W.calls(current) == 0 and med_calls >= 3)):
        out.append(W.CONVERSATION_DROP)
    return out[:W.MAX], False


def history_link(client, who):
    """-> the notion.so address of that person's "Megibow history" page, or None when it cannot be found (the note line then simply has no link; never an error)."""
    try:
        return "https://www.notion.so/" + find(client, HISTORY_TITLE[who], "page").replace("-", "")
    except NotionError:
        return None
