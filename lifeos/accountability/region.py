"""Writes one V3 accountability callout by its register address, inside that callout only, and reads every write back.

A region is a heading, one gray stamp paragraph, the body, then the `Summary for Claude: <page>` line; anything
else raises ACCOUNTABILITY_SHAPE and writes nothing. A write outside the callout is refused (ScopedClient)."""

import re
from urllib.parse import quote, unquote

from lifeos.accountability import render
from lifeos.platform import notion_pages, readback, report_region
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


def children_of(client, block_id):
    return report_region.children(client, block_id, AccountabilityError)


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
    for prop in (obj.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(p.get("plain_text") or "" for p in prop.get("title") or [])
    return ""


def _page_of(notion, block_id):
    current = block_id
    for _ in range(4):
        parent = notion.call("GET", f"/blocks/{quote(current, safe='')}").get("parent") or {}
        if parent.get("page_id"):
            return _title(notion.call("GET", f"/pages/{quote(parent['page_id'], safe='')}")).strip()
        current = parent.get("block_id") or ""
        if not current:
            return ""
    return ""


def register_row(notion, key):
    """The block id the V3 register names for `key`, or None. Exactly one register page must exist, else
    ACCOUNTABILITY_TARGET_NOT_FOUND."""
    found = notion_pages.search_all(
        notion,
        REGISTER_TITLE,
        "page",
        fail,
        incomplete="ACCOUNTABILITY_TARGET_NOT_FOUND",
        capped="ACCOUNTABILITY_TARGET_NOT_FOUND",
    )
    hits = [
        r["id"]
        for r in found
        if isinstance(r, dict)
        and _title(r).strip() == REGISTER_TITLE
        and not r.get("in_trash")
        and not r.get("archived")
    ]
    if len(hits) != 1:
        raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
    pattern = re.compile(r"(?:^|\s)" + re.escape(key) + r"\s+([0-9a-fA-F-]{32,36})\b")
    for block in report_region.children(notion, hits[0], AccountabilityError):
        match = pattern.search(report_region.plain(block))
        if match:
            return match.group(1)
    return None


def address(notion, key):
    """The callout id from register row `key`, accepted only when it sits on the Jim & Matt V3 page, else
    ACCOUNTABILITY_TARGET_NOT_FOUND."""
    block_id = register_row(notion, key)
    if block_id is None or _page_of(notion, block_id) != V3_PAGE_TITLE:
        raise fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
    return block_id


def locate(client, block_id):
    kids = report_region.children(client, block_id, AccountabilityError)
    if len(kids) < 3 or kids[0].get("type") not in HEADINGS or kids[1].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SHAPE")
    last = kids[-1]
    mention = [
        p
        for p in (last.get("paragraph") or {}).get("rich_text") or []
        if p.get("type") == "mention" and (p.get("mention") or {}).get("type") == "page"
    ]
    if last.get("type") != "paragraph" or not text(last).lower().startswith(SUMMARY) or len(mention) != 1:
        raise fail("ACCOUNTABILITY_SHAPE")
    return {"heading": kids[0], "stamp": kids[1], "middle": kids[2:-1], "summary": mention[0]["mention"]["page"]["id"]}


def _patch(client, block, kind, content, color=None):
    client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {kind: {"rich_text": _rich(content, color)}})


def _only(found, kinds):
    if [b.get("type") for b in found["middle"]] != kinds:
        raise fail("ACCOUNTABILITY_SHAPE")


def _set(client, block, content, live, color=None):
    if text(block) == content.strip():
        return 0
    if live:
        _patch(client, block, block["type"], content, color)
    return 1


def _normalise(items):
    out = []
    for content, url in ((c, unquote(u) if u else u) for c, u in items):
        if out and out[-1][1] == url:
            out[-1] = (out[-1][0] + content, url)
        elif content:
            out.append((content, url))
    return out


def signature(block):
    pieces = []
    for item in (block.get(block.get("type")) or {}).get("rich_text") or []:
        link = (item.get("text") or {}).get("link") or {}
        pieces.append(
            (
                item.get("plain_text") or (item.get("text") or {}).get("content") or "",
                item.get("href") or link.get("url"),
            )
        )
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
    found = locate(client, block_id)
    # the body paragraph may have become a bullet; write the kind it is
    if [b.get("type") for b in found["middle"]] not in (["paragraph"], ["bulleted_list_item"]):
        raise fail("ACCOUNTABILITY_SHAPE")
    kind = found["middle"][0]["type"]
    want = _normalise([(content, url) for content, url, _ in parts])
    changed = _set(client, found["stamp"], stamp, live, "gray")
    if signature(found["middle"][0]) != want:
        changed += 1
        if live:
            client.call_once(
                "PATCH", f"/blocks/{quote(found['middle'][0]['id'], safe='')}", {kind: {"rich_text": _nodes(parts)}}
            )
    if live and changed:
        ok, _ = readback.confirmed(
            lambda: locate(client, block_id),
            lambda a: text(a["stamp"]) == stamp.strip() and signature(a["middle"][0]) == want,
        )
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed, "summary_page": found["summary"]}


TREE_KINDS = ("paragraph", "heading_3", "bulleted_list_item", "toggle", "callout", "column_list")
INLINE_LEVELS = 2


def _rich_sig(items):
    out = []
    for text_, url, bold, color in items:
        key = (unquote(url) if url else None, bool(bold), color if color and color != "default" else None)
        if out and out[-1][1:] == key:
            out[-1] = (out[-1][0] + text_, *key)
        elif text_:
            out.append((text_, *key))
    return out


def _live_rich(block):
    return _rich_sig(
        [
            (
                p.get("plain_text") or (p.get("text") or {}).get("content") or "",
                p.get("href") or ((p.get("text") or {}).get("link") or {}).get("url"),
                (p.get("annotations") or {}).get("bold"),
                (p.get("annotations") or {}).get("color"),
            )
            for p in (block.get(block.get("type")) or {}).get("rich_text") or []
        ]
    )


def _spec_sig(item):
    return (item["type"], _rich_sig(item["rich"]), [_spec_sig(c) for c in item["children"]])


def _tree_sig(client, block):
    kids = report_region.children(client, block["id"], AccountabilityError) if block.get("has_children") else []
    return (block.get("type"), _live_rich(block), [_tree_sig(client, k) for k in kids])


def _payload(item, levels):
    kind = item["type"]
    body = {} if kind in ("column_list", "column") else {"rich_text": [_node(*piece) for piece in item["rich"]]}
    if kind == "callout":
        body["icon"] = {"type": "emoji", "emoji": item.get("icon") or "📋"}
        body["color"] = item.get("color") or "default"
    if item["children"] and levels > 0:
        body["children"] = [_payload(c, levels - 1) for c in item["children"]]
    return {"object": "block", "type": kind, kind: body}


def _node(text_, url=None, bold=False, color=None):
    out = {"type": "text", "text": {"content": text_, **({"link": {"url": url}} if url else {})}}
    if bold or color:
        out["annotations"] = {**({"bold": True} if bold else {}), **({"color": color} if color else {})}
    return out


def _depth(item):
    return 1 + max(_depth(c) for c in item["children"]) if item["children"] else 0


def _complete(client, block_id, item, left):
    if _depth(item) <= left:
        return
    if left <= 0:
        client.call_once(
            "PATCH",
            f"/blocks/{quote(block_id, safe='')}/children",
            {"children": [_payload(c, INLINE_LEVELS) for c in item["children"]]},
        )
        left = INLINE_LEVELS + 1
    for kid, child in zip(report_region.children(client, block_id, AccountabilityError), item["children"]):
        _complete(client, kid["id"], child, left - 1)


def write_tree(client, block_id, stamp, specs, live):
    """Write a V3 week callout body as exactly `specs` (see weekview): created under the stamp, the old body removed,
    all read back. Any block other than the one paragraph or an earlier copy of this tree raises
    ACCOUNTABILITY_SHAPE."""
    found = locate(client, block_id)
    if not found["middle"] or any(b.get("type") not in TREE_KINDS for b in found["middle"]):
        raise fail("ACCOUNTABILITY_SHAPE")
    want = [_spec_sig(s) for s in specs]
    old = found["middle"]
    changed = _set(client, found["stamp"], stamp, live, "gray")
    if [_tree_sig(client, b) for b in old] != want:
        changed += 1
        if live:
            client.call_once(
                "PATCH",
                f"/blocks/{quote(block_id, safe='')}/children",
                {"children": [_payload(s, INLINE_LEVELS) for s in specs], "after": found["stamp"]["id"]},
            )
            gone = {b["id"] for b in old}
            created = [k for k in report_region.children(client, block_id, AccountabilityError) if k["id"] not in gone][
                2:-1
            ][: len(specs)]
            if len(created) != len(specs):
                raise fail("ACCOUNTABILITY_VERIFY_FAILED")
            for block, item in zip(created, specs):
                _complete(client, block["id"], item, INLINE_LEVELS)
            for block in old:
                client.call_once("DELETE", f"/blocks/{quote(block['id'], safe='')}")

    def matches(after):
        return text(after["stamp"]) == stamp.strip() and [_tree_sig(client, b) for b in after["middle"]] == want

    if live and changed:
        ok, _ = readback.confirmed(lambda: locate(client, block_id), matches)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed, "summary_page": found["summary"]}


def write_directory(client, block_id, stamp, lines, bulleted, live):
    """Write the body of an Artifacts callout. V7 owns only the trailing run of bulleted_list_item blocks or, with
    none, the last paragraph; surplus or wrong-kind blocks are removed after the new ones exist, as is a `Summary
    for Claude:` line. Any other ending raises ACCOUNTABILITY_SHAPE."""
    found = locate_combined(client, block_id)
    middle = found["middle"]
    tail = []
    for block in reversed(middle):
        if block.get("type") != "bulleted_list_item":
            break
        tail.insert(0, block)
    owned = tail or middle[-1:]
    if (
        not middle
        or any(b.get("type") not in ("paragraph", "bulleted_list_item") for b in owned)
        or (not tail and owned[0].get("type") != "paragraph")
    ):
        raise fail("ACCOUNTABILITY_SHAPE")
    kind = "bulleted_list_item" if bulleted else "paragraph"
    want = [_normalise([(c, u) for c, u, _ in parts]) for parts in lines]
    keep = owned if owned[0]["type"] == kind else []
    changed = _set(client, found["stamp"], stamp, live, "gray")
    for block, parts, expected in zip(keep, lines, want):
        if signature(block) != expected:
            changed += 1
            if live:
                client.call_once(
                    "PATCH", f"/blocks/{quote(block['id'], safe='')}", {kind: {"rich_text": _nodes(parts)}}
                )
    added = lines[len(keep) :]
    if added:
        changed += len(added)
        if live:
            client.call_once(
                "PATCH",
                f"/blocks/{quote(block_id, safe='')}/children",
                {
                    "children": [
                        {"object": "block", "type": kind, kind: {"rich_text": _nodes(parts)}} for parts in added
                    ],
                    "after": middle[-1]["id"],
                },
            )
    removed = owned[len(lines) :] if keep else owned
    if found["legacy"] is not None:
        removed = removed + [found["legacy"]]
    for block in removed:
        changed += 1
        if live:
            client.call_once("DELETE", f"/blocks/{quote(block['id'], safe='')}")
    lead = [b["id"] for b in middle[: len(middle) - len(owned)]]

    def matches(after):
        body = after["middle"][len(lead) :]
        return (
            after["legacy"] is None
            and text(after["stamp"]) == stamp.strip()
            and [b["id"] for b in after["middle"][: len(lead)]] == lead
            and [b.get("type") for b in body] == [kind] * len(lines)
            and [signature(b) for b in body] == want
        )

    if live and changed:
        ok, _ = readback.confirmed(lambda: locate_combined(client, block_id), matches)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed}


def _cells(row):
    return [
        report_region.plain({"type": "x", "x": {"rich_text": cell}}).strip()
        for cell in (row.get("table_row") or {}).get("cells") or []
    ]


def _linked(content, url=None):
    node = {"type": "text", "text": {"content": content, **({"link": {"url": url}} if url else {})}}
    return [node]


def locate_combined(client, block_id):
    kids = report_region.children(client, block_id, AccountabilityError)
    if len(kids) < 3 or kids[0].get("type") not in HEADINGS or kids[1].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SHAPE")
    legacy = kids[-1] if text(kids[-1]).lower().startswith(SUMMARY) else None
    return {"heading": kids[0], "stamp": kids[1], "middle": kids[2:-1] if legacy else kids[2:], "legacy": legacy}


def write_combined(client, block_id, heading, stamp, lanes, extra, review_parts, live, wide=None):
    """Write a person's Quarterly Goals & Review callout: title, Epic table (`lanes` {lane: (done / all, issue url or
    None)}), note paragraph and "Quarterly Reviews" toggle. `wide` ({lane: [progress, done / all, new count, status
    cells]}) makes it five columns, so a new table is created under the stamp and the old removed (Notion cannot
    widen one)."""
    found = locate_combined(client, block_id)
    kinds = [b.get("type") for b in found["middle"]]
    width = (found["middle"][0].get("table") or {}).get("table_width") if kinds and kinds[0] == "table" else None
    if (
        kinds not in (["table"], ["table", "paragraph"], ["table", "paragraph", "toggle"])
        or width != (2 if wide is None else width)
        or (wide is not None and width not in (2, 5))
    ):
        raise fail("ACCOUNTABILITY_SHAPE")
    replaced = 0
    if wide is not None and width == 2:
        replaced = 1
        if live:
            old_rows = report_region.children(client, found["middle"][0]["id"], AccountabilityError)[1:]
            labels = [_cells(r)[0] for r in old_rows]
            client.call_once(
                "PATCH",
                f"/blocks/{quote(block_id, safe='')}/children",
                {"children": [_table_payload(labels, lanes, wide)], "after": found["stamp"]["id"]},
            )
            client.call_once("DELETE", f"/blocks/{quote(found['middle'][0]['id'], safe='')}")
            found = locate_combined(client, block_id)
            kinds = [b.get("type") for b in found["middle"]]
            if kinds[:1] != ["table"] or (found["middle"][0].get("table") or {}).get("table_width") != 5:
                raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    table, note = found["middle"][0], (found["middle"][1] if len(kinds) > 1 else None)
    toggle = found["middle"][2] if len(kinds) == 3 else None
    inner = report_region.children(client, toggle["id"], AccountabilityError) if toggle else []
    if toggle and (len(inner) != 1 or inner[0].get("type") != "paragraph"):
        raise fail("ACCOUNTABILITY_SHAPE")
    every = (
        report_region.children(client, table["id"], AccountabilityError)
        if (wide is None or not replaced or live)
        else []
    )
    rows = every[1:]
    wanted = []
    for row in rows:
        label = _cells(row)[0]
        value, url = next(
            ((v, u) for lane, (v, u) in lanes.items() if label.lower().startswith(lane.lower())), ("–", None)
        )
        if wide is not None:
            cells = next((c for lane, c in wide.items() if label.lower().startswith(lane.lower())), _EMPTY_WIDE)
            wanted.append((row, [[(label, url, False, None)]] + [list(c) for c in cells]))
        else:
            wanted.append((row, [(label, url), (value, None)]))
    want_review = _normalise([(c, u) for c, u, _ in review_parts])
    changed = _set(client, found["heading"], heading, live) + _set(client, found["stamp"], stamp, live, "gray")
    changed += replaced
    if wide is not None and every:
        header = [[(h, None, False, None)] for h in WIDE_HEADER]
        if _wide_signature(every[0]) != _rich_cells(header):
            changed += 1
            if live:
                client.call_once(
                    "PATCH",
                    f"/blocks/{quote(every[0]['id'], safe='')}",
                    {"table_row": {"cells": [[_node(*p) for p in cell] for cell in header]}},
                )
    for row, cells in wanted:
        if wide is not None:
            if _wide_signature(row) != _rich_cells(cells):
                changed += 1
                if live:
                    client.call_once(
                        "PATCH",
                        f"/blocks/{quote(row['id'], safe='')}",
                        {"table_row": {"cells": [[_node(*p) for p in cell] for cell in cells]}},
                    )
        elif _row_signature(row) != _normalise_cells(cells):
            changed += 1
            if live:
                client.call_once(
                    "PATCH",
                    f"/blocks/{quote(row['id'], safe='')}",
                    {"table_row": {"cells": [_linked(c, u) for c, u in cells]}},
                )
    pending = (
        [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(extra or " ")}}]
        if note is None
        else []
    ) + (
        [
            {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": _rich(REVIEWS_TITLE),
                    "children": [
                        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _nodes(review_parts)}}
                    ],
                },
            }
        ]
        if toggle is None
        else []
    )
    if pending:
        changed += len(pending)
        if live:
            client.call_once(
                "PATCH",
                f"/blocks/{quote(block_id, safe='')}/children",
                {"children": pending, "after": (note or table)["id"]},
            )
    if note is not None:
        changed += _set(client, note, extra or "", live)
    if toggle is not None:
        changed += _set(client, toggle, REVIEWS_TITLE, live)
        if signature(inner[0]) != want_review:
            changed += 1
            if live:
                client.call_once(
                    "PATCH",
                    f"/blocks/{quote(inner[0]['id'], safe='')}",
                    {"paragraph": {"rich_text": _nodes(review_parts)}},
                )
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
        same = (
            ([_wide_signature(r) for r in rows_after] == [_rich_cells(c) for _, c in wanted])
            if wide is not None
            else ([_row_signature(r) for r in rows_after] == [_normalise_cells(c) for _, c in wanted])
        )
        return (
            text(after["heading"]) == heading.strip()
            and text(after["stamp"]) == stamp.strip()
            and same
            and (not extra or text(mid[1]) == extra.strip())
            and len(again) == 1
            and signature(again[0]) == want_review
        )

    if live and changed:
        ok, _ = readback.confirmed(lambda: locate_combined(client, block_id), matches)
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"changed": changed}


WIDE_HEADER = ("Lane", "Progress", "Done / all", "New count (preview)", "Status")
_EMPTY_WIDE = (
    [("–", None, False, None)],
    [("–", None, False, None)],
    [("—", None, False, None)],
    [("—", None, False, None)],
)


def _rich_cells(cells):
    return [_rich_sig(cell) for cell in cells]


def _wide_signature(row):
    return [
        _live_rich({"type": "x", "x": {"rich_text": cell}}) for cell in (row.get("table_row") or {}).get("cells") or []
    ]


def _table_payload(labels, lanes, wide):
    def row(cells):
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": [[_node(*p) for p in cell] for cell in cells]},
        }

    rows = [row([[(h, None, False, None)] for h in WIDE_HEADER])]
    for label in labels:
        url = next((u for lane, (_, u) in lanes.items() if label.lower().startswith(lane.lower())), None)
        cells = next((c for lane, c in wide.items() if label.lower().startswith(lane.lower())), _EMPTY_WIDE)
        rows.append(row([[(label, url, False, None)]] + [list(c) for c in cells]))
    return {
        "object": "block",
        "type": "table",
        "table": {"table_width": 5, "has_column_header": True, "has_row_header": False, "children": rows},
    }


def _row_signature(row):
    return [
        _normalise(
            [
                (
                    piece.get("plain_text") or (piece.get("text") or {}).get("content") or "",
                    piece.get("href") or ((piece.get("text") or {}).get("link") or {}).get("url"),
                )
                for piece in cell
            ]
        )
        for cell in (row.get("table_row") or {}).get("cells") or []
    ]


def _normalise_cells(cells):
    return [_normalise([(c, u)]) for c, u in cells]


def degrade(client, block_id, make, live):
    found = locate_combined(client, block_id)
    new = make(text(found["stamp"]))
    changed = _set(client, found["stamp"], new, live, "orange")
    if live and changed:
        ok, _ = readback.confirmed(lambda: locate_combined(client, block_id), lambda a: text(a["stamp"]) == new.strip())
        if not ok:
            raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return changed


def write_summary(client, page_id, content, live):
    client.adopt(page_id)
    content = content[:SUMMARY_MAX]
    kids = report_region.children(client, page_id, AccountabilityError)
    if len(kids) != 1 or kids[0].get("type") != "paragraph":
        raise fail("ACCOUNTABILITY_SUMMARY_SHAPE")
    if text(kids[0]) == content.strip() or not live:
        return False
    _patch(client, kids[0], "paragraph", content)
    ok, _ = readback.confirmed(
        lambda: report_region.children(client, page_id, AccountabilityError),
        lambda after: len(after) == 1 and text(after[0]) == content.strip(),
    )
    if not ok:
        raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return True


def append_bullet(client, block_id, content, live):
    """Add one bullet after the last; earlier lines are never edited. The middle must be bullets only."""
    found = locate(client, block_id)
    if any(b.get("type") != "bulleted_list_item" for b in found["middle"]):
        raise fail("ACCOUNTABILITY_SHAPE")
    before = [text(b) for b in found["middle"]]
    if content.strip() in before or not live:
        return {
            "appended": 0,
            "lines": len(before),
            "last": before[-1] if before else "",
            "summary_page": found["summary"],
            "would_append": int(content.strip() not in before),
        }
    after_id = found["middle"][-1]["id"] if found["middle"] else found["stamp"]["id"]
    client.call_once(
        "PATCH",
        f"/blocks/{quote(block_id, safe='')}/children",
        {
            "children": [
                {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich(content)}}
            ],
            "after": after_id,
        },
    )

    def matches(now):
        return [text(b) for b in now["middle"]] == before + [content.strip()]

    ok, _ = readback.confirmed(lambda: locate(client, block_id), matches)
    if not ok:
        raise fail("ACCOUNTABILITY_VERIFY_FAILED")
    return {"appended": 1, "lines": len(before) + 1, "last": content.strip(), "summary_page": found["summary"]}
