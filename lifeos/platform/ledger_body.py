"""The Job Ledger page body (contract v7.jd.1 text, v7.jd.2 layout).

v7.jd.2: a hidden marker paragraph (`v7-jd:N | key=<Stable Job Key>` in a zero-width link), then ONE column_list:
Overview, Summary and a "Full job description" toggle on the left, the LinkedIn connections block on the right.
v7.jd.1 pages are still read. Notion allows two levels of nesting per request, so `fill` appends the toggle's children
afterwards."""

from typing import Any

from lifeos.platform import hidden, limits
from lifeos.platform.notion_client import NotionError, rich_text

MARK1, MARK2 = "v7-jd:1", "v7-jd:2"
SECTIONS = (
    ("summary", "Summary"),
    ("responsibilities", "Responsibilities"),
    ("requirements", "Requirements"),
    ("qualifications", "Qualifications"),
)
OVERVIEW, FULL = "Overview", "Full job description"
LEFT, RIGHT = 0.4, 0.6


def _block(kind, content):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(content)}}


def _plain(block):
    inner = (
        block.get(block.get("type"))
        if block.get("type")
        else next((v for v in block.values() if isinstance(v, dict) and "rich_text" in v), None)
    ) or {}
    return "".join(
        (p.get("plain_text") or (p.get("text") or {}).get("content") or "")
        for p in (inner.get("rich_text") or [])
        if isinstance(p, dict)
    )


def marker_text(block: dict[str, Any]) -> str:
    """The marker line of a paragraph: the hidden ref when it has one (v7.jd.2 and later), else its visible text
    (v7.jd.1 pages)."""
    return (hidden.refs(block) or [_plain(block)])[0]


def is_marker(block: dict[str, Any], key: str | None = None) -> bool:
    return (
        block.get("type", "paragraph") == "paragraph"
        and marker_text(block).startswith(MARK1[:-1])
        and (key is None or key in marker_text(block))
    )


def marker_block(key: str, version: str = MARK2) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [hidden.run(f"{version} | key={key}")]}}


def _lines(text, kind="paragraph"):
    out = []
    for line in str(text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        out.append(
            _block("bulleted_list_item", line.lstrip("• ").strip() or "-")
            if line.startswith("•")
            else _block(kind, line)
        )
    return out


def legacy_blocks(key: str, description: dict[str, Any]) -> list[dict[str, Any]]:
    """Contract v7.jd.1: a visible marker paragraph, then heading_2 + bullets/paragraphs per non-empty section, all at
    the top level."""
    blocks = [_block("paragraph", f"{MARK1} | key={key}")]
    for field, heading in SECTIONS:
        text = (description.get(field) or "").strip()
        if text:
            blocks += [_block("heading_2", heading), *_lines(text)]
    return blocks


OVERVIEW_MAX = 90


def _split(description):
    lines = _lines((description.get("summary") or "").strip())
    return lines[:OVERVIEW_MAX], lines[OVERVIEW_MAX:]


def full_blocks(description: dict[str, Any]) -> list[dict[str, Any]]:
    """The children of the "Full job description" toggle: any Summary overflow, then heading_3 + items for every other
    section."""
    blocks = list(_split(description)[1])
    for field, heading in SECTIONS[1:]:
        text = (description.get(field) or "").strip()
        if text:
            blocks += [_block("heading_3", heading), *_lines(text)]
    return blocks


def column_list(description: dict[str, Any]) -> dict[str, Any]:
    """The two-column block: Overview with an empty Full job description toggle (left) and a blank paragraph
    (right)."""
    left = [
        _block("heading_2", OVERVIEW),
        *_split(description)[0],
        {"object": "block", "type": "toggle", "toggle": {"rich_text": rich_text(FULL)}},
    ]
    cols = [
        {"object": "block", "type": "column", "column": {"width_ratio": LEFT, "children": left}},
        {"object": "block", "type": "column", "column": {"width_ratio": RIGHT, "children": [_block("paragraph", "")]}},
    ]
    return {"object": "block", "type": "column_list", "column_list": {"children": cols}}


def build(key: str, description: dict[str, Any]) -> list[dict[str, Any]]:
    """v7.jd.2: [hidden marker, column_list]."""
    return [marker_block(key), column_list(description)]


def _kids(client, block_id):
    out, cursor = [], None
    for _ in range(50):
        page = client.call(
            "GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        )
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise NotionError("ledger_layout_unreadable")
        out += page["results"]
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
        if not cursor:
            raise NotionError("ledger_layout_unreadable")
    raise NotionError("ledger_layout_unreadable")


def description_list(top: list[dict[str, Any]], key: str | None = None) -> dict[str, Any] | None:
    """The job description's column_list among a page's top-level blocks, or None."""

    def after(i):
        return top[i + 1] if i + 1 < len(top) and top[i + 1].get("type") == "column_list" else None

    if key:
        found = [after(i) for i, b in enumerate(top) if is_marker(b, key) and after(i)]
        return found[0] if found else None
    lists = [b for b in top if b.get("type") == "column_list"]
    if len(lists) == 1:
        return lists[0]
    found = [after(i) for i, b in enumerate(top) if is_marker(b) and after(i)]
    return found[0] if len(found) == 1 else None


def description_duplicates(top: list[dict[str, Any]], key: str | None) -> int:
    """How many EXTRA descriptions carry this row's key (0 normally); reported for repair, the top-most is in use."""
    return max(0, sum(1 for b in top if is_marker(b, key)) - 1) if key else 0


def find(
    client: Any, page_id: str, top: list[dict[str, Any]] | None = None, key: str | None = None
) -> dict[str, Any] | None:
    """-> {"list", "left", "right", "toggle"} block ids of a v7.jd.2 page, or None for a page without the layout."""
    top = top if top is not None else _kids(client, page_id)
    found = description_list(top, key)
    if found is None:
        return None
    lists = [found]
    columns = [b for b in _kids(client, lists[0]["id"]) if b.get("type") == "column"]
    if len(columns) != 2:
        return None
    left = _kids(client, columns[0]["id"])
    toggles = [b for b in left if b.get("type") == "toggle" and _plain(b) == FULL]
    return {
        "list": lists[0]["id"],
        "left": columns[0]["id"],
        "right": columns[1]["id"],
        "toggle": toggles[0]["id"] if len(toggles) == 1 else None,
    }


def fill(client: Any, page_id: str, description: dict[str, Any], strict: bool = True) -> int:
    """Append the rest of the description into the toggle of a page `build` just wrote."""
    where = find(client, page_id)
    if not where or not where["toggle"]:
        if strict:
            raise NotionError("ledger_layout_missing")
        return 0
    if _kids(client, where["toggle"]):
        return 0
    blocks = full_blocks(description)
    for start in range(0, len(blocks), limits.NOTION_MAX_CHILD_BLOCKS):
        client.call(
            "PATCH",
            f"/blocks/{where['toggle']}/children",
            {"children": blocks[start : start + limits.NOTION_MAX_CHILD_BLOCKS]},
        )
    return len(blocks)


def text_of(client: Any, page_id: str, top: list[dict[str, Any]] | None = None, limit: int = 24000) -> str:
    """The job's own description from its page, either layout: summary lines, then every section's heading and
    items."""
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
        take(
            [
                b
                for b in left
                if b.get("type") != "toggle" and not (b.get("type") == "heading_2" and _plain(b) == OVERVIEW)
            ]
        )
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
