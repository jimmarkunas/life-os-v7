"""LIFE-791: the Agent Inbox. One place where agents and people write items for Jim that are too big to ignore and too small for a Jira ticket or a Todoist task."""
# Jim, Nov 16 2026: "tasks that need my attention from Agents and people that are not big enough to be a jira ticket, or too small to be a Todoist task ... a centralized place for the various agents to write to
# me (similar to what we built with the Concord inbox in Orca)". It is NOT a view of Jira, Todoist or Calendar: "Jira stays the only task list", so no row ever mirrors another system's record.
#
# The inbox is one Notion database ("Agent Inbox list", the inline database on Jim's Agent Inbox page). A writer adds one row: `send(...)` here (the `send_to_jim` tool and CLI call it) or a direct Notion write
# with the property names in docs/platform/agent-inbox.md. Jim marks a row Done or Dismissed, writes a Reply, sets Snoozed or Promoted. Nothing here reads Jira, Todoist, Calendar or any other source.
#
# Safety: a write targets only the one data source named "Agent Inbox list" (found by search, never a committed id; zero or several is a fixed refusal); a row is created with Status New and nothing else; the subject,
# details and link are checked for secrets, personal data and synthetic hosts before anything leaves the process; one sender may add at most RATE_PER_HOUR rows an hour; the same key never adds a second row.
# Output is counts and fixed reason words only: never a subject, a sender, a link or a token.
import hashlib
import re
from datetime import datetime, timedelta, timezone

from lifeos.inbox.model import InboxError
from lifeos.platform import notion_pages
from lifeos.platform.notion_client import Client, NotionError

DB_TITLE = "Agent Inbox list"
PAGE_TITLE = "Agent Inbox"
URGENCY = ("Now", "Today", "This week", "FYI")
WORKSPACES = ("Personal", "Bytalos")
STATUSES = ("New", "Done", "Dismissed", "Snoozed", "Promoted")
TILES = (("Now", "New", "Now"), ("Today", "New", "Today"), ("This week", "New", "This week"), ("FYI", "New", "FYI"), ("Snoozed", "Snoozed", None))   # label, status, urgency (None: any)
SUBJECT_MAX, DETAILS_MAX, FROM_MAX, LINK_MAX, KEY_MAX = 160, 1500, 60, 500, 64
RATE_PER_HOUR = 20

_FROM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,59}$")                       # a name or an agent id: never an address
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


def normalize(item):
    """A validated item: from, subject, urgency, workspace, due, link, details, key. InboxError with a fixed word otherwise; nothing is ever echoed back."""
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
    out = {"from": sender, "subject": subject, "urgency": urgency, "workspace": workspace, "due": _due(item.get("due")), "link": _link(item.get("link")),
           "details": _text(item.get("details"), DETAILS_MAX, "details")}
    key = str(item.get("key") or "").strip()
    if key and not _KEY.match(key):
        raise InboxError("key_invalid")
    out["key"] = key or hashlib.sha256("\x1f".join((sender, subject, out["due"], out["link"])).encode()).hexdigest()[:32]
    return out


def _rich(text):
    return {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}] if text else []}


def properties(item):
    """The Notion property payload for a normalized item: Status is always New, nothing else is ever preset."""
    props = {"Subject": {"title": [{"type": "text", "text": {"content": item["subject"]}}]}, "From": _rich(item["from"]), "Workspace": {"select": {"name": item["workspace"]}},
             "Urgency": {"select": {"name": item["urgency"]}}, "Status": {"select": {"name": "New"}}, "Key": _rich(item["key"]), "Details": _rich(item["details"])}
    if item["due"]:
        props["Due"] = {"date": {"start": item["due"]}}
    if item["link"]:
        props["Link"] = {"url": item["link"]}
    return props


def _plain_title(rich):
    return "".join(piece.get("plain_text", "") for piece in rich or ())


def locate(client):
    """The data source id of the one database called DB_TITLE. None or several is a fixed refusal: a writer never guesses where to write."""
    found = notion_pages.search_all(client, DB_TITLE, "data_source", InboxError, incomplete="inbox_not_found", capped="inbox_not_found")           # LIFE-891: every result page, not the first 25
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
    """Add one row. -> {"created": 1} or {"duplicate": 1}. InboxError (fixed word) when the item is refused or the sender is over the rate; NotionError (fixed code) when Notion refuses."""
    item = normalize(item)
    now = now or datetime.now(timezone.utc)
    source = source or locate(client)
    matched = client.query_data_source(source, {"page_size": 2, "filter": {"property": "Key", "rich_text": {"equals": item["key"]}}}).get("results") or []
    if matched:
        # Fail closed if the same key already names multiple rows. Never guess a
        # target or reset Jim's own Status when an agent resubmits an item.
        if len(matched) != 1 or not matched[0].get("id"):
            raise InboxError("duplicate_key_ambiguous")
        rich = matched[0].get("properties", {}).get("From", {}).get("rich_text", [])
        owner = "".join(part.get("plain_text") or part.get("text", {}).get("content", "") for part in rich)
        if owner != item["from"]:
            raise InboxError("key_other_sender")
        props = properties(item)
        props.pop("Status")
        client.call_once("PATCH", f"/pages/{matched[0]['id']}", {"properties": props})
        return {"updated": 1}
    since = (now - timedelta(hours=1)).isoformat()
    recent = _count(client, source, [{"property": "From", "rich_text": {"equals": item["from"]}}, {"timestamp": "created_time", "created_time": {"after": since}}], cap_pages=1)
    if recent >= RATE_PER_HOUR:
        raise InboxError("rate_limited")
    client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source}, "properties": properties(item)})
    return {"created": 1}


def counts(client, source=None):
    """{label: number} for the five tiles: New items per urgency, and Snoozed items."""
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
    """The five tile callouts of the page as [(callout id, number block, label block)] in order, or InboxError tiles_shape_changed."""
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


def _parent_page(client, source):
    """LIFE-891: the page that holds the inbox database the run already found (data source, then its database, then that database's parent page), accepted only when its title
    is exactly PAGE_TITLE, it is live, and its first block is the tile row. None for anything else: the caller then falls back to the paged title search."""
    try:
        parent = (client.call("GET", f"/data_sources/{source}") or {}).get("parent") or {}
        if parent.get("type") == "database_id":
            parent = (client.call("GET", f"/databases/{parent['database_id']}") or {}).get("parent") or {}
        if parent.get("type") != "page_id":
            return None
        page = client.call("GET", f"/pages/{parent['page_id']}") or {}
        title = "".join(t.get("plain_text", "") for t in (page.get("properties", {}).get("title") or {}).get("title", []))
        if title != PAGE_TITLE or page.get("in_trash") or page.get("archived"):
            return None
        _tiles(client, page["id"])
        return page["id"]
    except (NotionError, InboxError, KeyError, AttributeError, TypeError):
        return None


def locate_page(client, source=None):
    """The one page titled PAGE_TITLE whose first block is the tile row. None or several is a fixed refusal; no id is ever committed.
    LIFE-891: with `source` (the inbox database the run already located) its parent page is used first; otherwise, or when that is not the tile page, every result page of the title search is read."""
    if source:
        parent = _parent_page(client, source)
        if parent:
            return parent
    found = notion_pages.search_all(client, PAGE_TITLE, "page", InboxError, incomplete="page_not_found", capped="page_not_found")
    holders = []
    for page in found:
        title = "".join(t.get("plain_text", "") for t in (page.get("properties", {}).get("title") or {}).get("title", []))
        if title != PAGE_TITLE or page.get("in_trash") or page.get("archived"):
            continue
        try:
            _tiles(client, page["id"])
        except InboxError:
            continue
        holders.append(page["id"])
    if len(holders) != 1:
        raise InboxError("page_not_found" if not holders else "page_ambiguous")
    return holders[0]


def recount_page(client, page_id=None, source=None):
    """Write the five tile numbers and labels from the database. Only the number and label text of the five tiles is ever patched, and only when it differs. -> {"tiles_changed": n, "tiles": 5}."""
    source = source or locate(client)
    numbers = counts(client, source)
    tiles = _tiles(client, page_id or locate_page(client, source))
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


TEST_KEY_PREFIXES = ("proof-", "direct-")                                          # the keys the inbox proof writes; only these rows may ever be trashed


def archive_keys(client, keys, source=None):
    """Trash the rows whose Key is in `keys`: only the proof's own test rows (keys that start with a TEST_KEY_PREFIXES word); any other key is a refusal and nothing is trashed. -> number archived."""
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
