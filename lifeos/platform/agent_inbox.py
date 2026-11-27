"""The Agent Inbox: one place where agents and people write items for Jim, too big to ignore and too small for a ticket.

One Notion database ("Agent Inbox list"); `send(...)` adds a row with Status New, and Jim marks it Done, Dismissed,
Snoozed or Promoted. A write targets only the one data source with that title (found by search, never a committed id).
Subject, details and link are checked for secrets, personal data and synthetic hosts first; one sender may add at most
RATE_PER_HOUR rows an hour and the same key never adds a second row. Output is counts and fixed reason words only.
"""
import hashlib
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from lifeos.platform import notion_pages
from lifeos.platform.inbox_error import InboxError
from lifeos.platform.notion_client import Client, NotionError

DB_TITLE = "Agent Inbox list"
PAGE_TITLE = "Agent Inbox"
MERGED_TITLE = "Daily LIFE OS Report (Hourly)"
PAGE_TITLES = (MERGED_TITLE, PAGE_TITLE)
COUNTER_LINE = re.compile(r"Now \d+ · Today \d+")
MERGED_LABELS = ("This week", "FYI", "Snoozed")
URGENCY = ("Now", "Today", "This week", "FYI")
WORKSPACES = ("Personal", "Bytalos")
STATUSES = ("New", "Done", "Dismissed", "Snoozed", "Promoted")
TILES = (("Now", "New", "Now"), ("Today", "New", "Today"), ("This week", "New", "This week"), ("FYI", "New", "FYI"), ("Snoozed", "Snoozed", None))  # label, status, urgency (None: any)
SUBJECT_MAX, DETAILS_MAX, FROM_MAX, LINK_MAX, KEY_MAX = 160, 1500, 60, 500, 64
RATE_PER_HOUR = 20
CT = ZoneInfo("America/Chicago")
DETAILS_LINE_MAX, BODY_BLOCKS_MAX, CELL_MAX = 120, 90, 300

_FROM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,59}$")  # a name or an agent id: never an address
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SECRET = re.compile("|".join((
    r"nsec1[0-9a-z]{20,}", r"ghp_[A-Za-z0-9]{20,}", r"gh[ousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"sk-[A-Za-z0-9_-]{20,}", r"AKIA[0-9A-Z]{16}", r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY", r"ATATT[A-Za-z0-9_=-]{20,}", r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.", r"\b[A-Fa-f0-9]{40,}\b", r"(?i:bearer\s+[A-Za-z0-9._~+/=-]{20,})",
    r"(?i:(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S{8,})")))
_PERSONAL = re.compile("|".join((r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", r"\b\d{3}-\d{2}-\d{4}\b", r"\b(?:\d[ -]?){13,16}\b")))
_SYNTHETIC_HOST = re.compile(r"(^|\.)(test|invalid|example|localhost)$|^example\.(com|org|net)$")


def _text(value, limit, reason, required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise InboxError(f"{reason}_missing")
        return ""
    text = str(value).strip()
    if len(text) > limit or any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise InboxError(f"{reason}_invalid")
    if _SECRET.search(text):
        raise InboxError(f"{reason}_has_secret")
    if _PERSONAL.search(text):
        raise InboxError(f"{reason}_has_personal_data")
    return text


def _due(value):
    if value is None or str(value).strip() == "":
        return ""
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise InboxError("due_invalid") from None
    return text if "T" in text else parsed.date().isoformat()


def due_for(urgency, moment):
    """The Due an urgency means for an item created at `moment`: Now is that moment, Today and This week are 11:59 PM CT
    that day and the coming Sunday, FYI has none.
    """
    local = moment.astimezone(CT)
    if urgency == "Now":
        return local.replace(microsecond=0).isoformat()
    if urgency not in ("Today", "This week"):
        return ""
    ahead = 0 if urgency == "Today" else (6 - local.weekday()) % 7
    return datetime.combine(local.date() + timedelta(days=ahead), time(23, 59), tzinfo=CT).isoformat()


def _link(value):
    if value is None or str(value).strip() == "":
        return ""
    text = str(value).strip()
    match = re.match(r"^https?://([^/\s:?#]+)(?::\d+)?(?:[/?#]\S*)?$", text, re.I)
    if not match or len(text) > LINK_MAX or _SECRET.search(text):
        raise InboxError("link_invalid")
    if _SYNTHETIC_HOST.search(match.group(1).lower()):
        raise InboxError("link_synthetic_host")
    return text


def normalize(item, now=None):
    """A validated item; `due` is the sender's own, else the one its urgency means at `now`. Raises InboxError with a
    fixed word.
    """
    if not isinstance(item, dict):
        raise InboxError("item_invalid")
    sender = _text(item.get("from"), FROM_MAX, "from", required=True)
    if not _FROM.match(sender):
        raise InboxError("from_invalid")
    subject = _text(item.get("subject"), SUBJECT_MAX, "subject", required=True)
    urgency = str(item.get("urgency") or "Today").strip()
    if urgency not in URGENCY:
        raise InboxError("urgency_invalid")
    workspace = str(item.get("workspace") or "Personal").strip()
    if workspace not in WORKSPACES:
        raise InboxError("workspace_invalid")
    given = _due(item.get("due"))
    out = {"from": sender, "subject": subject, "urgency": urgency, "workspace": workspace, "due": given or due_for(urgency, now or datetime.now(timezone.utc)), "due_given": bool(given),
           "link": _link(item.get("link")), "details": _text(item.get("details"), DETAILS_MAX, "details")}
    key = str(item.get("key") or "").strip()
    if key and not _KEY.match(key):
        raise InboxError("key_invalid")
    out["key"] = key or hashlib.sha256("\x1f".join((sender, subject, given, out["link"])).encode()).hexdigest()[:32]  # the key never depends on a computed Due
    return out


def _rich(text):
    return {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}] if text else []}


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _spans(text):
    out, last = [], 0
    for found in list(_BOLD.finditer(text)) + [None]:
        plain = text[last:found.start() if found else len(text)]
        if plain:
            out.append({"type": "text", "text": {"content": plain[:1900]}})
        if found:
            out.append({"type": "text", "text": {"content": found.group(1)[:1900]}, "annotations": {"bold": True}})
            last = found.end()
    return out


def gist(details):
    for line in str(details or "").splitlines():
        words = re.sub(r"^\s*(?:#{1,3}\s+|[-*]\s+|\d+\.\s+)", "", line.replace("**", "")).strip()
        if words and not re.fullmatch(r"[\s|:-]+", words):
            words = " · ".join(c.strip() for c in words.split("|") if c.strip())
            return words if len(words) <= DETAILS_LINE_MAX else words[:DETAILS_LINE_MAX - 1].rstrip() + "…"
    return ""


def _row(cells, width):
    cells = [c.strip()[:CELL_MAX] for c in cells][:width]
    return {"object": "block", "type": "table_row", "table_row": {"cells": [_spans(c) for c in cells + [""] * (width - len(cells))]}}


def body_blocks(details):
    """The item's text as styled Notion blocks (first line a callout, `a | b | c` rows a table, lists, headings), at most
    BODY_BLOCKS_MAX.
    """
    lines, blocks, i = [ln.rstrip() for ln in str(details or "").splitlines()], [], 0
    while i < len(lines) and len(blocks) < BODY_BLOCKS_MAX:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if not blocks:
            blocks.append({"object": "block", "type": "callout", "callout": {"rich_text": _spans(line), "icon": {"type": "emoji", "emoji": "📌"}}})
        elif line.count("|") >= 1 and " | " in f" {line} ":
            rows = []
            while i < len(lines) and "|" in lines[i]:
                cells = [c for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in cells):
                    rows.append(cells)
                i += 1
            width = max(len(r) for r in rows)
            blocks.append({"object": "block", "type": "table", "table": {"table_width": width, "has_column_header": True, "has_row_header": False, "children": [_row(r, width) for r in rows[:30]]}})
            continue
        elif re.match(r"^#{1,2}\s+", line):
            kind = "heading_2" if line.startswith("# ") else "heading_3"
            blocks.append({"object": "block", "type": kind, kind: {"rich_text": _spans(re.sub(r"^#{1,2}\s+", "", line))}})
        elif re.match(r"^[-*]\s+", line):
            blocks.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _spans(re.sub(r"^[-*]\s+", "", line))}})
        elif re.match(r"^\d+\.\s+", line):
            blocks.append({"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": _spans(re.sub(r"^\d+\.\s+", "", line))}})
        else:
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _spans(line)}})
        i += 1
    return blocks


def replace_body(client, page_id, details):
    blocks = body_blocks(details)
    if not blocks:
        return
    cursor = None
    while True:
        page = client.call("GET", f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        for old in page.get("results", []):
            client.call("DELETE", f"/blocks/{old['id']}")
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
    client.call_once("PATCH", f"/blocks/{page_id}/children", {"children": blocks})


def properties(item):
    props = {"Subject": {"title": [{"type": "text", "text": {"content": item["subject"]}}]}, "From": _rich(item["from"]), "Workspace": {"select": {"name": item["workspace"]}},
             "Urgency": {"select": {"name": item["urgency"]}}, "Status": {"select": {"name": "New"}}, "Key": _rich(item["key"]), "Details": _rich(gist(item["details"]))}
    if item["due"]:
        props["Due"] = {"date": {"start": item["due"]}}
    if item["link"]:
        props["Link"] = {"url": item["link"]}
    return props


def _plain_title(rich):
    return "".join(piece.get("plain_text", "") for piece in rich or ())


def locate(client):
    """The data source id of the one database called DB_TITLE; None or several is a fixed refusal."""
    found = notion_pages.search_all(client, DB_TITLE, "data_source", InboxError, incomplete="inbox_not_found", capped="inbox_not_found")
    hits = [r for r in found if r.get("object") == "data_source" and not r.get("in_trash") and not r.get("archived") and _plain_title(r.get("title")) == DB_TITLE]
    if len(hits) != 1:
        raise InboxError("inbox_not_found" if not hits else "inbox_ambiguous")
    return str(hits[0]["id"])


def _count(client, source, filters, cap_pages=20):
    total, cursor = 0, None
    for _ in range(cap_pages):
        body = {"page_size": 100, "filter": {"and": filters} if len(filters) > 1 else filters[0]}
        if cursor:
            body["start_cursor"] = cursor
        page = client.query_data_source(source, body)
        total += len(page.get("results", ()))
        if not page.get("has_more"):
            return total
        cursor = page.get("next_cursor")
    return total


def _select(name, value):
    return {"property": name, "select": {"equals": value}}


def send(client, item, now=None, source=None):
    """Add one row -> {"created": 1} or {"duplicate": 1}. Raises InboxError (fixed word) when the item is refused or over
    the rate, NotionError when Notion refuses.
    """
    now = now or datetime.now(timezone.utc)
    item = normalize(item, now)
    source = source or locate(client)
    matched = client.query_data_source(source, {"page_size": 2, "filter": {"property": "Key", "rich_text": {"equals": item["key"]}}}).get("results") or []
    if matched:
        # Fail closed if the same key names several rows.
        if len(matched) != 1 or not matched[0].get("id"):
            raise InboxError("duplicate_key_ambiguous")
        rich = matched[0].get("properties", {}).get("From", {}).get("rich_text", [])
        owner = "".join(part.get("plain_text") or part.get("text", {}).get("content", "") for part in rich)
        if owner != item["from"]:
            raise InboxError("key_other_sender")
        props = properties(item)
        props.pop("Status")
        if not item["due_given"] and ((matched[0].get("properties", {}).get("Due") or {}).get("date") or {}).get("start"):
            props.pop("Due", None)  # a resend never moves a Due that is already set unless the sender names one
        client.call_once("PATCH", f"/pages/{matched[0]['id']}", {"properties": props})
        replace_body(client, matched[0]["id"], item["details"])
        return {"updated": 1}
    since = (now - timedelta(hours=1)).isoformat()
    recent = _count(client, source, [{"property": "From", "rich_text": {"equals": item["from"]}}, {"timestamp": "created_time", "created_time": {"after": since}}], cap_pages=1)
    if recent >= RATE_PER_HOUR:
        raise InboxError("rate_limited")
    client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source}, "properties": properties(item), **({"children": body_blocks(item["details"])} if item["details"] else {})})
    return {"created": 1}


def fill_missing_due(client, source, now=None, statuses=("New",), cap=40):
    """Give every open row with an urgency but no Due the one its urgency means at its Received time. -> {"filled": n,
    "left": n}.
    """
    out, cursor = {"filled": 0, "left": 0}, None
    flt = {"and": [{"or": [_select("Status", st) for st in statuses]} if len(statuses) > 1 else _select("Status", statuses[0]), {"property": "Due", "date": {"is_empty": True}}]}
    for _ in range(5):
        page = client.query_data_source(source, {"page_size": 100, "filter": flt, **({"start_cursor": cursor} if cursor else {})})
        for row in page.get("results", ()):
            urgency = ((row.get("properties", {}).get("Urgency") or {}).get("select") or {}).get("name") or ""
            made = due_for(urgency, datetime.fromisoformat(row["created_time"].replace("Z", "+00:00"))) if urgency in URGENCY and row.get("created_time") else ""
            if not made:
                out["left"] += 1
            elif out["filled"] < cap:
                client.call_once("PATCH", f"/pages/{row['id']}", {"properties": {"Due": {"date": {"start": made}}}})
                out["filled"] += 1
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
    return out


def counts(client, source=None):
    source = source or locate(client)
    return {label: _count(client, source, [_select("Status", status)] + ([_select("Urgency", urgency)] if urgency else [])) for label, status, urgency in TILES}


def _children(client, block_id):
    out, cursor = [], None
    while True:
        page = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += page.get("results", [])
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")


def _words(block):
    return "".join(p.get("plain_text", "") for p in (block.get(block.get("type")) or {}).get("rich_text") or ())


def _tiles(client, page_id):
    body = _children(client, page_id)
    if not body or body[0].get("type") != "column_list":
        raise InboxError("tiles_shape_changed")
    out = []
    for column in _children(client, body[0]["id"]):
        callouts = [b for b in _children(client, column["id"]) if b.get("type") == "callout"]
        inner = _children(client, callouts[0]["id"]) if len(callouts) == 1 else []
        kinds = [b.get("type") for b in inner]
        if len(callouts) != 1 or kinds[:2] != ["heading_3", "paragraph"]:
            raise InboxError("tiles_shape_changed")
        out.append((callouts[0]["id"], inner[0], inner[1]))
    if len(out) != len(TILES):
        raise InboxError("tiles_shape_changed")
    return out


def _merged_row(client, page_id):
    """The merged page's blocks as (box callout, box heading, box gray line or None, [(callout id, number block, label
    block)] for This week, FYI and Snoozed), or None. Found by what they say; two of a kind or a missing one gives
    None (the caller falls back to the old page).
    """
    boxes, tiles = [], {}

    def scan(blocks, depth):
        for block in blocks:
            kind = block.get("type")
            if kind == "callout":
                kids = _children(client, block["id"])
                if kids and kids[0].get("type") == "heading_3":
                    head = _words(kids[0])
                    if head.startswith("Now") and "Today" in head:
                        boxes.append((block, kids[0], None))
                    elif len(kids) > 1 and kids[1].get("type") == "paragraph" and COUNTER_LINE.fullmatch(_words(kids[1]).strip()):
                        boxes.append((block, kids[0], kids[1]))
                    elif len(kids) > 1 and kids[1].get("type") == "paragraph" and _words(kids[1]).strip() in MERGED_LABELS:
                        tiles.setdefault(_words(kids[1]).strip(), []).append((block["id"], kids[0], kids[1]))
                if depth < 3:
                    scan(kids, depth + 1)
            elif kind in ("column_list", "column") and depth < 4:
                scan(_children(client, block["id"]), depth + 1)

    scan(_children(client, page_id)[:6], 0)
    if len(boxes) != 1 or any(len(tiles.get(label, ())) != 1 for label in MERGED_LABELS):
        return None
    return boxes[0][0], boxes[0][1], boxes[0][2], [tiles[label][0] for label in MERGED_LABELS]


def _live_titled(page, titles):
    title = "".join(t.get("plain_text", "") for t in (page.get("properties", {}).get("title") or {}).get("title", []))
    return title in titles and not page.get("in_trash") and not page.get("archived")


def _parent_page(client, source):
    """The page holding the inbox database the run already found, accepted only when titled PAGE_TITLE, live, and
    starting with the tile row; else None.
    """
    try:
        parent = (client.call("GET", f"/data_sources/{source}") or {}).get("parent") or {}
        if parent.get("type") == "database_id":
            parent = (client.call("GET", f"/databases/{parent['database_id']}") or {}).get("parent") or {}
        if parent.get("type") != "page_id":
            return None
        page = client.call("GET", f"/pages/{parent['page_id']}") or {}
        if not _live_titled(page, (PAGE_TITLE,)):
            return None
        _tiles(client, page["id"])
        return page["id"]
    except (NotionError, InboxError, KeyError, AttributeError, TypeError):
        return None


def locate_page(client, source=None):
    """The page that holds the tiles: the one live page titled MERGED_TITLE with the merged tile row, else the one titled
    PAGE_TITLE with the old tile row. None or several of the winning kind is a fixed refusal.
    """
    merged = []
    for page in notion_pages.search_all(client, MERGED_TITLE, "page", InboxError, incomplete="page_not_found", capped="page_not_found"):
        if _live_titled(page, (MERGED_TITLE,)) and _merged_row(client, page["id"]):
            merged.append(page["id"])
    if merged:
        if len(merged) != 1:
            raise InboxError("page_ambiguous")
        return merged[0]
    if source:
        parent = _parent_page(client, source)
        if parent:
            return parent
    holders = []
    for page in notion_pages.search_all(client, PAGE_TITLE, "page", InboxError, incomplete="page_not_found", capped="page_not_found"):
        if not _live_titled(page, (PAGE_TITLE,)):
            continue
        try:
            _tiles(client, page["id"])
        except InboxError:
            continue
        holders.append(page["id"])
    if len(holders) != 1:
        raise InboxError("page_not_found" if not holders else "page_ambiguous")
    return holders[0]


def _line_key(rich):
    return [(piece.get("plain_text") or (piece.get("text") or {}).get("content", ""), piece.get("href") or ((piece.get("text") or {}).get("link") or {}).get("url") or "") for piece in rich]


def _set_counter(client, box, heading, line, now, today):
    changed = 0
    if (box.get("callout") or {}).get("color") != "default":
        client.call_once("PATCH", f"/blocks/{box['id']}", {"callout": {"color": "default"}})
        changed += 1
    if _words(heading) != str(now + today):
        client.update_block(heading["id"], "heading_3", [{"type": "text", "text": {"content": str(now + today)}}])
        changed += 1
    text = f"Now {now} · Today {today}"
    rich = [{"type": "text", "text": {"content": text}, "annotations": {"color": "gray"}}]
    if line is None:
        client.call_once("PATCH", f"/blocks/{box['id']}/children", {"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}}]})
        changed += 1
    elif _words(line) != text or any(p.get("annotations", {}).get("color") != "gray" for p in (line.get("paragraph") or {}).get("rich_text") or ()):
        client.update_block(line["id"], "paragraph", rich)
        changed += 1
    keep = {heading["id"], (line or {}).get("id")}
    for extra in [b for b in _children(client, box["id"]) if b["id"] not in keep and not (line is None and b.get("type") == "paragraph" and COUNTER_LINE.fullmatch(_words(b).strip()))]:
        client.call_once("DELETE", f"/blocks/{extra['id']}")
        changed += 1
    return changed


def recount_page(client, page_id=None, source=None):
    source = source or locate(client)
    numbers = counts(client, source)
    page_id = page_id or locate_page(client, source)
    merged = _merged_row(client, page_id)
    if merged:
        box, heading, line, tiles = merged
        changed = _set_counter(client, box, heading, line, numbers["Now"], numbers["Today"])
        for label, (_, number_block, label_block) in zip(MERGED_LABELS, tiles, strict=True):
            if _words(number_block) != str(numbers[label]):
                client.update_block(number_block["id"], "heading_3", [{"type": "text", "text": {"content": str(numbers[label])}}])
                changed += 1
        return {"tiles": 4, "tiles_changed": changed, "layout": "merged"}
    tiles = _tiles(client, page_id)
    changed = 0
    for (label, _, _), (_, number_block, label_block) in zip(TILES, tiles, strict=True):
        want = str(numbers[label])
        if _words(number_block) != want:
            client.update_block(number_block["id"], "heading_3", [{"type": "text", "text": {"content": want}}])
            changed += 1
        if _words(label_block) != label:
            client.update_block(label_block["id"], "paragraph", [{"type": "text", "text": {"content": label}, "annotations": {"bold": True}}])
            changed += 1
    return {"tiles": len(TILES), "tiles_changed": changed}


TEST_KEY_PREFIXES = ("proof-", "direct-")  # only rows with these key prefixes may ever be trashed


def archive_keys(client, keys, source=None):
    """Trash the rows whose Key starts with a TEST_KEY_PREFIXES word; any other key is a refusal. -> number archived.
    """
    if any(not str(key).startswith(TEST_KEY_PREFIXES) for key in keys):
        raise InboxError("archive_not_a_test_row")
    source = source or locate(client)
    done = 0
    for key in keys:
        for row in client.query_data_source(source, {"page_size": 5, "filter": {"property": "Key", "rich_text": {"equals": key}}}).get("results", ()):
            client.call_once("PATCH", f"/pages/{row['id']}", {"in_trash": True})
            done += 1
    return done


__all__ = ["archive_keys", "counts", "locate", "locate_page", "normalize", "properties", "recount_page", "send"]
