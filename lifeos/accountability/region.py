"""Writes one V3 accountability callout (LIFE-671) by its register address, inside that callout only, and reads every write back.

A region is: a heading, one gray stamp paragraph, the body, then the `Summary for Claude: <page>` line. Anything else raises ACCOUNTABILITY_SHAPE and writes nothing. V7 writes only the stamp,
the body cells or text it owns and the summary page; text a person adds outside those blocks is never touched. A write outside the callout is refused (ScopedClient)."""
import re
from urllib.parse import quote, unquote

from lifeos.accountability import render
from lifeos.platform import readback, report_region
from lifeos.platform.notion_client import NotionError

SUMMARY = "summary for claude:"
REGISTER_TITLE = "V3 block ID register (Jim & Matt V3)"
V3_PAGE_TITLE = "Jim & Matt V3"
SUMMARY_MAX = render.SUMMARY_MAX
HEADINGS = ("heading_2", "heading_3")
REVIEWS_TITLE = "Quarterly Reviews"


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
    for prop in (obj.get("properties") or {}).values():                # a Notion page keeps its title in its title property
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(p.get("plain_text") or "" for p in prop.get("title") or [])
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


def register_row(notion, key):
    """The block id the V3 block ID register names for `key`, or None when the register has no such row. Exactly one register page must exist (else ACCOUNTABILITY_TARGET_NOT_FOUND)."""
    found = notion.call("POST", "/search", {"query": REGISTER_TITLE, "filter": {"property": "object", "value": "page"}, "page_size": 100})
    hits = [r["id"] for r in (found or {}).get("results") or [] if isinstance(r, dict) and _title(r).strip() == REGISTER_TITLE and not r.get("in_trash") and not r.get("archived")]
    if len(hits) != 1:
        raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
    pattern = re.compile(r"(?:^|\s)" + re.escape(key) + r"\s+([0-9a-fA-F-]{32,36})\b")
    for block in report_region.children(notion, hits[0], AccountabilityError):
        match = pattern.search(report_region.plain(block))
        if match:
            return match.group(1)
    return None


def address(notion, key):
    """The callout id from the V3 block ID register row `key` (jim.thisweek, matt.goals, ...), accepted only when that callout sits on the Jim & Matt V3 page,
    so an edited register row can never point a write anywhere else. Anything else raises ACCOUNTABILITY_TARGET_NOT_FOUND."""
    block_id = register_row(notion, key)
    if block_id is None or _page_of(notion, block_id) != V3_PAGE_TITLE:
        raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
    return block_id


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


def _normalise(items):
    """[(text, link)] with adjacent pieces that share a link merged, so two ways of cutting the same text compare equal. A link is compared decoded: Notion stores some characters of
    an address unescaped (parentheses, "!") that were sent escaped, and the same address must still read back as written."""
    out = []
    for content, url in ((c, unquote(u) if u else u) for c, u in items):
        if out and out[-1][1] == url:
            out[-1] = (out[-1][0] + content, url)
        elif content:
            out.append((content, url))
    return out


def signature(block):
    """What a text block shows: [(text, link)] read from its rich text."""
    pieces = []
    for item in (block.get(block.get("type")) or {}).get("rich_text") or []:
        link = (item.get("text") or {}).get("link") or {}
        pieces.append((item.get("plain_text") or (item.get("text") or {}).get("content") or "", item.get("href") or link.get("url")))
    return _normalise(pieces)


def _nodes(parts):
    out = []
    for content, url, bold in parts:
        node = {"type": "text", "text": {"content": content, **({"link": {"url": url}} if url else {})}}
        if bold:
            node["annotations"] = {"bold": True}
        out.append(node)
    return out


def write_parts(client, block_id, stamp, parts, live):
    """The body paragraph of a callout as linked, partly bold pieces [(text, link, bold)]: the middle is exactly one paragraph. -> {changed, summary_page}."""
    found = locate(client, block_id)
    _only(found, ["paragraph"])
    want = _normalise([(content, url) for content, url, _ in parts])
    changed = _set(client, found["stamp"], stamp, live, "gray")
    if signature(found["middle"][0]) != want:
        changed += 1
        if live:
            client.call_once("PATCH", f"/blocks/{quote(found['middle'][0]['id'], safe='')}", {"paragraph": {"rich_text": _nodes(parts)}})
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate(client, block_id), lambda a: text(a["stamp"]) == stamp.strip() and signature(a["middle"][0]) == want)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed, "summary_page": found["summary"]}


def _cells(row):
    return [report_region.plain({"type": "x", "x": {"rich_text": cell}}).strip() for cell in (row.get("table_row") or {}).get("cells") or []]


def _linked(content, url=None):
    node = {"type": "text", "text": {"content": content, **({"link": {"url": url}} if url else {})}}
    return [node]


def locate_combined(client, block_id):
    """-> {heading, stamp, middle, legacy}: the combined Quarterly Goals & Review callout is a heading, one gray stamp paragraph and the body; a trailing `Summary for Claude:` line (the shape before
    LIFE-840) is returned as `legacy` and is not part of the body. Anything else raises ACCOUNTABILITY_SHAPE."""
    kids = report_region.children(client, block_id, AccountabilityError)
    if len(kids) < 3 or kids[0].get("type") not in HEADINGS or kids[1].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SHAPE")
    legacy = kids[-1] if text(kids[-1]).lower().startswith(SUMMARY) else None
    return {"heading": kids[0], "stamp": kids[1], "middle": kids[2:-1] if legacy else kids[2:], "legacy": legacy}


def write_combined(client, block_id, heading, stamp, lanes, extra, review_parts, live):
    """LIFE-840: the one Quarterly Goals & Review callout of a person. Body: the two-column Epic table (`lanes` {lane: (done / all, issue url or None)}; a row is matched by the lane name its first
    cell starts with, its first cell is linked to the Epic, a lane with no epic shows a dash), one note paragraph, then the toggle "Quarterly Reviews" whose one paragraph links the review pages
    (`review_parts` [(text, link, bold)]). The title is set to `heading`. A legacy `Summary for Claude:` line is removed (its summary page stays in Notion, untouched). -> {changed}."""
    found = locate_combined(client, block_id)
    kinds = [b.get("type") for b in found["middle"]]
    if kinds not in (["table"], ["table", "paragraph"], ["table", "paragraph", "toggle"]) or (found["middle"][0].get("table") or {}).get("table_width") != 2:
        raise fail("ACCOUNTABILITY_SHAPE")
    table, note = found["middle"][0], (found["middle"][1] if len(kinds) > 1 else None)
    toggle = found["middle"][2] if len(kinds) == 3 else None
    inner = report_region.children(client, toggle["id"], AccountabilityError) if toggle else []
    if toggle and (len(inner) != 1 or inner[0].get("type") != "paragraph"):
        raise fail("ACCOUNTABILITY_SHAPE")
    rows = report_region.children(client, table["id"], AccountabilityError)[1:]
    wanted = []
    for row in rows:
        label = _cells(row)[0]
        value, url = next(((v, u) for lane, (v, u) in lanes.items() if label.lower().startswith(lane.lower())), ("–", None))
        wanted.append((row, [(label, url), (value, None)]))
    want_review = _normalise([(c, u) for c, u, _ in review_parts])
    changed = _set(client, found["heading"], heading, live) + _set(client, found["stamp"], stamp, live, "gray")
    for row, cells in wanted:
        if _row_signature(row) != _normalise_cells(cells):
            changed += 1
            if live:
                client.call_once("PATCH", f"/blocks/{quote(row['id'], safe='')}", {"table_row": {"cells": [_linked(c, u) for c, u in cells]}})
    pending = ([{"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(extra or " ")}}] if note is None else []) + (
        [{"object": "block", "type": "toggle", "toggle": {"rich_text": _rich(REVIEWS_TITLE), "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": _nodes(review_parts)}}]}}] if toggle is None else [])
    if pending:
        changed += len(pending)
        if live:
            client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": pending, "after": (note or table)["id"]})
    if note is not None:
        changed += _set(client, note, extra or "", live)
    if toggle is not None:
        changed += _set(client, toggle, REVIEWS_TITLE, live)
        if signature(inner[0]) != want_review:
            changed += 1
            if live:
                client.call_once("PATCH", f"/blocks/{quote(inner[0]['id'], safe='')}", {"paragraph": {"rich_text": _nodes(review_parts)}})
    if found["legacy"] is not None:
        changed += 1
        if live:
            client.call_once("DELETE", f"/blocks/{quote(found['legacy']['id'], safe='')}")

    def matches(after):
        mid = after["middle"]
        if after["legacy"] is not None or [b.get("type") for b in mid] != ["table", "paragraph", "toggle"]:
            return False
        rows_after = report_region.children(client, mid[0]["id"], AccountabilityError)[1:]
        again = report_region.children(client, mid[2]["id"], AccountabilityError)
        return (text(after["heading"]) == heading.strip() and text(after["stamp"]) == stamp.strip() and [_row_signature(r) for r in rows_after] == [_normalise_cells(c) for _, c in wanted]
                and (not extra or text(mid[1]) == extra.strip()) and len(again) == 1 and signature(again[0]) == want_review)
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate_combined(client, block_id), matches)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed}


def _row_signature(row):
    return [_normalise([(piece.get("plain_text") or (piece.get("text") or {}).get("content") or "", piece.get("href") or ((piece.get("text") or {}).get("link") or {}).get("url"))
                        for piece in cell]) for cell in (row.get("table_row") or {}).get("cells") or []]


def _normalise_cells(cells):
    return [_normalise([(c, u)]) for c, u in cells]


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


def append_bullet(client, block_id, content, live):
    """Canon / Change Log: add one bullet after the last one; earlier lines are never edited, moved or removed. The middle must be bullets only. -> {appended, lines, last, summary_page}."""
    found = locate(client, block_id)
    if any(b.get("type") != "bulleted_list_item" for b in found["middle"]):
        raise fail("ACCOUNTABILITY_SHAPE")
    before = [text(b) for b in found["middle"]]
    if content.strip() in before or not live:
        return {"appended": 0, "lines": len(before), "last": before[-1] if before else "", "summary_page": found["summary"], "would_append": int(content.strip() not in before)}
    after_id = found["middle"][-1]["id"] if found["middle"] else found["stamp"]["id"]
    client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich(content)}}], "after": after_id})

    def matches(now):
        return [text(b) for b in now["middle"]] == before + [content.strip()]
    ok, _ = readback.confirmed(lambda: locate(client, block_id), matches)
    if not ok:
        raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"appended": 1, "lines": len(before) + 1, "last": content.strip(), "summary_page": found["summary"]}
