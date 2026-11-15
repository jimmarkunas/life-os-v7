"""Writes one V3 accountability callout (LIFE-671) by its register address, inside that callout only, and reads every write back.

A region is: a heading, one gray stamp paragraph, the body, then the `Summary for Claude: <page>` line. Anything else raises ACCOUNTABILITY_SHAPE and writes nothing. V7 writes only the stamp,
the body cells or text it owns and the summary page; text a person adds outside those blocks is never touched. A write outside the callout is refused (ScopedClient)."""
import re
from urllib.parse import quote

from lifeos.accountability import render
from lifeos.platform import readback, report_region
from lifeos.platform.notion_client import NotionError

SUMMARY = "summary for claude:"
REGISTER_TITLE = "V3 block ID register (Jim & Matt V3)"
V3_PAGE_TITLE = "Jim & Matt V3"
SUMMARY_MAX = render.SUMMARY_MAX
HEADINGS = ("heading_2", "heading_3")


class AccountabilityError(NotionError):
    """Fixed codes only."""


def fail(code):
    return AccountabilityError(code)


def scoped(notion, block_id):
    return report_region.ScopedClient(notion, block_id, lambda code: AccountabilityError("ACCOUNTABILITY_" + code))


def text(block):
    return report_region.plain(block).strip()


def _rich(content, color=None):
    node = {"type": "text", "text": {"content": content}}
    if color:
        node["annotations"] = {"color": color}
    return [node]


def _title(obj):
    if isinstance(obj.get("title"), list):
        return "".join(p.get("plain_text") or "" for p in obj["title"])
    return ""


def _page_of(notion, block_id):
    """The title of the page a block sits on, found by walking up through column blocks (a callout in a column has the column as its parent); "" when it is not found within four steps."""
    current = block_id
    for _ in range(4):
        parent = (notion.call("GET", f"/blocks/{quote(current, safe='')}").get("parent") or {})
        if parent.get("page_id"):
            return _title(notion.call("GET", f"/pages/{quote(parent['page_id'], safe='')}")).strip()
        current = parent.get("block_id") or ""
        if not current:
            return ""
    return ""


def address(notion, key):
    """The callout id from the V3 block ID register row `key` (jim.thisweek, matt.goals, ...), accepted only when that callout sits on the Jim & Matt V3 page,
    so an edited register row can never point a write anywhere else. Anything else raises ACCOUNTABILITY_TARGET_NOT_FOUND."""
    found = notion.call("POST", "/search", {"query": REGISTER_TITLE, "filter": {"property": "object", "value": "page"}, "page_size": 100})
    hits = [r["id"] for r in (found or {}).get("results") or [] if isinstance(r, dict) and _title(r).strip() == REGISTER_TITLE and not r.get("in_trash") and not r.get("archived")]
    if len(hits) != 1:
        raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
    pattern = re.compile(r"(?:^|\s)" + re.escape(key) + r"\s+([0-9a-fA-F-]{32,36})\b")
    for block in report_region.children(notion, hits[0], AccountabilityError):
        match = pattern.search(report_region.plain(block))
        if match:
            block_id = match.group(1)
            if _page_of(notion, block_id) != V3_PAGE_TITLE:
                raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
            return block_id
    raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")


def locate(client, block_id):
    """-> {heading, stamp, middle (blocks between the stamp and the summary line), summary (page id)}."""
    kids = report_region.children(client, block_id, AccountabilityError)
    if len(kids) < 3 or kids[0].get("type") not in HEADINGS or kids[1].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SHAPE")
    last = kids[-1]
    mention = [p for p in (last.get("paragraph") or {}).get("rich_text") or [] if p.get("type") == "mention" and (p.get("mention") or {}).get("type") == "page"]
    if last.get("type") != "paragraph" or not text(last).lower().startswith(SUMMARY) or len(mention) != 1:
        raise fail("ACCOUNTABILITY_SHAPE")
    return {"heading": kids[0], "stamp": kids[1], "middle": kids[2:-1], "summary": mention[0]["mention"]["page"]["id"]}


def _patch(client, block, kind, content, color=None):
    client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {kind: {"rich_text": _rich(content, color)}})


def _only(found, kinds):
    if [b.get("type") for b in found["middle"]] != kinds:
        raise fail("ACCOUNTABILITY_SHAPE")


def _set(client, block, content, live, color=None):
    """Replace a text block's text when it differs. -> 1 when written."""
    if text(block) == content.strip():
        return 0
    if live:
        _patch(client, block, block["type"], content, color)
    return 1


def write_text(client, block_id, stamp, body, live):
    """Weekly view: the middle is exactly one paragraph. -> {changed, summary_page}."""
    found = locate(client, block_id)
    _only(found, ["paragraph"])
    changed = _set(client, found["stamp"], stamp, live, "gray") + _set(client, found["middle"][0], body, live)
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate(client, block_id), lambda a: text(a["stamp"]) == stamp.strip() and text(a["middle"][0]) == body.strip())
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed, "summary_page": found["summary"]}


def _cells(row):
    return [report_region.plain({"type": "x", "x": {"rich_text": cell}}).strip() for cell in (row.get("table_row") or {}).get("cells") or []]


def write_goals(client, block_id, stamp, values, extra, live):
    """Quarterly goals: the middle is the two-column table (and, once written, one note paragraph). `values` {lane: "done / all"}; a table row is matched by the lane
    name its first cell starts with (so "Health, Finances & Travel" is Health). A row whose lane has no epic shows a dash. `extra` is the note line under the table."""
    found = locate(client, block_id)
    kinds = [b.get("type") for b in found["middle"]]
    if kinds not in (["table"], ["table", "paragraph"]) or (found["middle"][0].get("table") or {}).get("table_width") != 2:
        raise fail("ACCOUNTABILITY_SHAPE")
    rows = report_region.children(client, found["middle"][0]["id"], AccountabilityError)[1:]
    wanted = []
    for row in rows:
        label = _cells(row)[0]
        hit = next((v for lane, v in values.items() if label.lower().startswith(lane.lower())), "–")
        wanted.append((row, [label, hit]))
    changed = _set(client, found["stamp"], stamp, live, "gray")
    for row, cells in wanted:
        if _cells(row) != cells:
            changed += 1
            if live:
                client.call_once("PATCH", f"/blocks/{quote(row['id'], safe='')}", {"table_row": {"cells": [_rich(c) for c in cells]}})
    note = found["middle"][1] if len(found["middle"]) == 2 else None
    if note is None and live and extra:
        client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(extra)}}], "after": found["middle"][0]["id"]})
        changed += 1
    elif note is not None:
        changed += _set(client, note, extra or "", live)

    def matches(after):
        rows_after = report_region.children(client, after["middle"][0]["id"], AccountabilityError)[1:]
        note_after = after["middle"][1] if len(after["middle"]) == 2 else None
        return (text(after["stamp"]) == stamp.strip() and [_cells(r) for r in rows_after] == [c for _, c in wanted]
                and (not extra or (note_after is not None and text(note_after) == extra.strip())))
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate(client, block_id), matches)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed, "summary_page": found["summary"]}


def write_review(client, block_id, stamp, current, new_past, live):
    """Quarterly review: the middle is the toggle (one paragraph inside it, the current quarter) then one paragraph for past quarters. Past lines already there are never rewritten;
    a quarter that has no line yet gets one appended. -> {changed, past_added, summary_page}."""
    found = locate(client, block_id)
    _only(found, ["toggle", "paragraph"])
    toggle, past = found["middle"]
    inner = report_region.children(client, toggle["id"], AccountabilityError)
    if len(inner) != 1 or inner[0].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SHAPE")
    have = [line for line in text(past).split("\n") if render.PAST_LINE.match(line)]
    add = [line for line in new_past if line.split(" — ")[0] not in {h.split(" — ")[0] for h in have}]
    past_text = "\n".join(have + add) if (have or add) else text(past)
    changed = _set(client, found["stamp"], stamp, live, "gray") + _set(client, inner[0], current, live) + _set(client, past, past_text, live)

    def matches(after):
        again = report_region.children(client, after["middle"][0]["id"], AccountabilityError)
        return (text(after["stamp"]) == stamp.strip() and len(again) == 1 and text(again[0]) == current.strip() and text(after["middle"][1]) == past_text.strip())
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate(client, block_id), matches)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed, "past_added": len(add), "summary_page": found["summary"]}


def degrade(client, block_id, make, live):
    """Jira could not be read: only the stamp changes, to DEGRADED with the last accepted stamp; the body stays. -> 1 when written."""
    found = locate(client, block_id)
    new = make(text(found["stamp"]))
    changed = _set(client, found["stamp"], new, live, "orange")
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate(client, block_id), lambda a: text(a["stamp"]) == new.strip())
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return changed


def write_summary(client, page_id, content, live):
    """The callout's own summary page (named by the mention inside it) is the one other block this card may write; its paragraph is learned by reading it."""
    client.adopt(page_id)
    content = content[:SUMMARY_MAX]
    kids = report_region.children(client, page_id, AccountabilityError)
    if len(kids) != 1 or kids[0].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SUMMARY_SHAPE")
    if text(kids[0]) == content.strip() or not live:
        return False
    _patch(client, kids[0], "paragraph", content)
    ok, _ = readback.confirmed(lambda: report_region.children(client, page_id, AccountabilityError), lambda after: len(after) == 1 and text(after[0]) == content.strip())
    if not ok:
        raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return True
