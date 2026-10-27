"""The Job Ledger page body (contract v7.jd.1 text, v7.jd.2 layout; Jim, Oct 27: the approved two-column page, and no marker line showing).

Layout (v7.jd.2): a hidden marker paragraph, then ONE column_list. The left column (40%) holds the "Overview" heading, the Summary and a toggle "Full job description" with every other section; the right
column (60%) holds the LinkedIn connections block that lifeos.network.surface writes. The marker is a zero-width character whose link carries `v7-jd:N | key=<Stable Job Key>`, so nothing machine-made shows.
A page written before this (visible `v7-jd:1 | key=...` paragraph, the sections at the top level) is still read everywhere. Pure builders and readers; callers own the Notion calls.
Notion allows two levels of nesting in one request, so a toggle inside a column cannot carry its children at creation: `fill` appends them afterwards."""
from lifeos.platform import hidden, limits
from lifeos.platform.notion_client import NotionError, rich_text

MARK1, MARK2 = "v7-jd:1", "v7-jd:2"
SECTIONS = (("summary", "Summary"), ("responsibilities", "Responsibilities"), ("requirements", "Requirements"), ("qualifications", "Qualifications"))
OVERVIEW, FULL = "Overview", "Full job description"
LEFT, RIGHT = 0.4, 0.6


def _block(kind, content):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(content)}}


def _plain(block):
    inner = (block.get(block.get("type")) if block.get("type") else next((v for v in block.values() if isinstance(v, dict) and "rich_text" in v), None)) or {}
    return "".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in (inner.get("rich_text") or []) if isinstance(p, dict))


def marker_text(block):
    """The marker line of a paragraph: the hidden ref when it has one (v7.jd.2 and later), else its visible text (v7.jd.1 pages)."""
    return (hidden.refs(block) or [_plain(block)])[0]


def is_marker(block, key=None):
    return block.get("type", "paragraph") == "paragraph" and marker_text(block).startswith(MARK1[:-1]) and (key is None or key in marker_text(block))


def marker_block(key, version=MARK2):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [hidden.run(f"{version} | key={key}")]}}


def _lines(text, kind="paragraph"):
    out = []
    for line in str(text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        out.append(_block("bulleted_list_item", line.lstrip("• ").strip() or "-") if line.startswith("•") else _block(kind, line))
    return out


def legacy_blocks(key, description):
    """Contract v7.jd.1 as it was written: a visible marker paragraph, then heading_2 + bullets/paragraphs per non-empty section, all at the top level."""
    blocks = [_block("paragraph", f"{MARK1} | key={key}")]
    for field, heading in SECTIONS:
        text = (description.get(field) or "").strip()
        if text:
            blocks += [_block("heading_2", heading), *_lines(text)]
    return blocks


OVERVIEW_MAX = 90                                                       # blocks one request may carry in a column (Notion allows 100 per array); a longer Summary continues inside the toggle


def _split(description):
    """-> (the Summary's blocks that stay in the Overview, the rest of the Summary's blocks)."""
    lines = _lines((description.get("summary") or "").strip())
    return lines[:OVERVIEW_MAX], lines[OVERVIEW_MAX:]


def full_blocks(description):
    """The children of the "Full job description" toggle: any Summary overflow, then heading_3 + items for every other section."""
    blocks = list(_split(description)[1])
    for field, heading in SECTIONS[1:]:
        text = (description.get(field) or "").strip()
        if text:
            blocks += [_block("heading_3", heading), *_lines(text)]
    return blocks


def column_list(description):
    """The two-column block: Overview (left, with the Full job description toggle, empty) and a blank paragraph (right) that the leads block replaces."""
    left = [_block("heading_2", OVERVIEW), *_split(description)[0], {"object": "block", "type": "toggle", "toggle": {"rich_text": rich_text(FULL)}}]
    cols = [{"object": "block", "type": "column", "column": {"width_ratio": LEFT, "children": left}},
            {"object": "block", "type": "column", "column": {"width_ratio": RIGHT, "children": [_block("paragraph", "")]}}]
    return {"object": "block", "type": "column_list", "column_list": {"children": cols}}


def build(key, description):
    """v7.jd.2: [hidden marker, column_list]. The toggle is created empty (see the module note); `fill` gives it the rest of the description."""
    return [marker_block(key), column_list(description)]


def _kids(client, block_id):
    out, cursor = [], None
    for _ in range(50):
        page = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise NotionError("ledger_layout_unreadable")
        out += page["results"]
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
        if not cursor:
            raise NotionError("ledger_layout_unreadable")
    raise NotionError("ledger_layout_unreadable")


def find(client, page_id, top=None):
    """-> {"list", "left", "right", "toggle"} block ids of a v7.jd.2 page, or None for a page without the layout. Reads at most the top blocks, the column_list and the two columns."""
    top = top if top is not None else _kids(client, page_id)
    lists = [b for b in top if b.get("type") == "column_list"]
    if len(lists) != 1:
        return None
    columns = [b for b in _kids(client, lists[0]["id"]) if b.get("type") == "column"]
    if len(columns) != 2:
        return None
    left = _kids(client, columns[0]["id"])
    toggles = [b for b in left if b.get("type") == "toggle" and _plain(b) == FULL]
    return {"list": lists[0]["id"], "left": columns[0]["id"], "right": columns[1]["id"], "toggle": toggles[0]["id"] if len(toggles) == 1 else None}


def fill(client, page_id, description, strict=True):
    """Append the rest of the description into the toggle of a page `build` just wrote. -> number of blocks written. A toggle that already holds blocks is left alone (a replay writes nothing).
    strict=False is for an adopted page that may be the older layout: no layout means nothing to fill."""
    where = find(client, page_id)
    if not where or not where["toggle"]:
        if strict:
            raise NotionError("ledger_layout_missing")
        return 0
    if _kids(client, where["toggle"]):
        return 0
    blocks = full_blocks(description)
    for start in range(0, len(blocks), limits.NOTION_MAX_CHILD_BLOCKS):
        client.call("PATCH", f"/blocks/{where['toggle']}/children", {"children": blocks[start:start + limits.NOTION_MAX_CHILD_BLOCKS]})
    return len(blocks)


def text_of(client, page_id, top=None, limit=24000):
    """The job's own description from its page, either layout, as the resume worker takes it: summary lines, then every section's heading and items, in order."""
    top = top if top is not None else _kids(client, page_id)
    lines = []

    def take(blocks):
        for block in blocks:
            kind, line = block.get("type"), _plain(block)
            if kind in ("heading_2", "heading_3", "paragraph", "bulleted_list_item") and line.strip():
                lines.append(f"- {line}" if kind == "bulleted_list_item" else line)

    where = find(client, page_id, top)
    if where:
        left = _kids(client, where["left"])
        take([b for b in left if b.get("type") != "toggle" and not (b.get("type") == "heading_2" and _plain(b) == OVERVIEW)])
        if where["toggle"]:
            take(_kids(client, where["toggle"]))
    else:
        started = False
        for block in top:
            if not started:
                started = is_marker(block)
                continue
            if block.get("type") not in ("heading_2", "paragraph", "bulleted_list_item"):
                break
            take([block])
    return "\n".join(lines)[:limit]
