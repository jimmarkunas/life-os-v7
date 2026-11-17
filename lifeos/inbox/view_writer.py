"""LIFE-711: the Action Inbox view's Notion writer. It owns ONE Notion database (the view) and only the rows in it that it created; it touches no other page or block."""
# Jim, Nov 16 2026: 1 test page = the QA area (LIFE OS QA Home); 2 the existing Notion integration, nothing new; 3 writer scope = create, update and archive only the Agent Inbox rows it owns, tracked by a
# server-side row map, never any other page or block, the only writer on that surface; 5 rollback = archive the rows the writer created and restore the "not connected" text, with a read-back receipt each run.
# The row map is the database itself: a row is ours only when it sits in the view's data source AND its "Managed by" property is MANAGED. `current_rows()` returns exactly those rows (and remembers their page ids);
# every update and archive goes through that map, so a page that is not in it (another page, another database, a row a person added by hand) is refused with INBOX_VIEW_WRITE_OUT_OF_SCOPE before any request is sent.
# Nothing here reads a provider or a credential; it takes a `lifeos.platform.notion_client.Client` and the row dicts of `view_rows.project_rows` (the plan is `view_plan.plan`).
from lifeos.inbox import view_plan
from lifeos.inbox.model import InboxError
from lifeos.platform.notion_client import NotionError

MANAGED = "v7-inbox-writer"
DB_TITLE = "Agent Inbox (V7 view)"
QA_PARENT_TITLE = "LIFE OS QA Home"
TITLE = "Title"
RICH = ("Record ID", "Source status", "Due", "Next move", "Source updated", "Source health", "Last refreshed", "Last OK")
SELECT = ("Kind", "Workspace", "Source", "Bucket", "Owner cue", "Checkpoint", "Freshness", "Health state", "Managed by")
URL = ("Link",)
CHECK = ("Counted",)
PROPERTIES = (TITLE, *RICH, *SELECT, *URL, *CHECK)
_LIMIT = 1900                                           # Notion rich text allows 2,000 characters per item; the view never holds a body, so this only trims a long title or note


class ScopeError(InboxError):
    """A write aimed at something outside the view: a fixed code, never a page title or id."""


def schema():
    """The data source's properties as Notion creates them (the title property is `Title`)."""
    out = {TITLE: {"title": {}}}
    out.update({name: {"rich_text": {}} for name in RICH})
    out.update({name: {"select": {}} for name in SELECT})
    out.update({name: {"url": {}} for name in URL})
    out.update({name: {"checkbox": {}} for name in CHECK})
    return out


def _text(value):
    return str(value or "")[:_LIMIT]


def _select(value):
    return _text(value).replace(",", ";").strip()        # a Notion select name cannot hold a comma


def canon(row):
    """The row as Notion will hand it back: the same trimming and select cleaning, so a read-back can be compared with what was planned. `Archived` is the plan's own flag and is dropped."""
    out = {}
    for name in PROPERTIES:
        value = row.get(name, "" if name not in CHECK else False)
        if name in SELECT:
            out[name] = _select(value)
        elif name in CHECK:
            out[name] = bool(value)
        else:
            out[name] = _text(value)
    out["Managed by"] = MANAGED
    return out


def to_properties(row, only=None):
    """Notion property JSON for the row (or only the named properties); empty text is an empty value, an empty select or url is null."""
    row = canon(row)
    names = [n for n in PROPERTIES if only is None or n in only]
    props = {}
    for name in names:
        value = row[name]
        if name == TITLE:
            props[name] = {"title": [{"type": "text", "text": {"content": value}}] if value else []}
        elif name in RICH:
            props[name] = {"rich_text": [{"type": "text", "text": {"content": value}}] if value else []}
        elif name in SELECT:
            props[name] = {"select": {"name": value} if value else None}
        elif name in URL:
            props[name] = {"url": value or None}
        else:
            props[name] = {"checkbox": value}
    return props


def from_page(page):
    """The row dict a Notion page holds: property name -> text, bool, or "" for an empty value."""
    out = {}
    props = page.get("properties") or {}
    for name in PROPERTIES:
        prop = props.get(name) or {}
        kind = prop.get("type")
        if kind in ("title", "rich_text"):
            out[name] = "".join(piece.get("plain_text") or (piece.get("text") or {}).get("content", "") for piece in prop.get(kind) or [])
        elif kind == "select":
            out[name] = (prop.get("select") or {}).get("name", "")
        elif kind == "url":
            out[name] = prop.get("url") or ""
        elif kind == "checkbox":
            out[name] = bool(prop.get("checkbox"))
        else:
            out[name] = False if name in CHECK else ""
    return out


def _plain_title(items):
    return "".join(piece.get("plain_text") or (piece.get("text") or {}).get("content", "") for piece in items or [])


def _page_title(page):
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _plain_title(prop.get("title"))
    return ""


def _id(value):
    return str(value or "").replace("-", "").lower()


def find_parent(client, title=QA_PARENT_TITLE):
    """The one page called `title` (the QA area). None or more than one is a fixed refusal: the writer never guesses where to write."""
    found = client.call("POST", "/search", {"query": title, "filter": {"property": "object", "value": "page"}, "page_size": 25})
    pages = [r for r in found.get("results", []) if r.get("object") == "page" and not r.get("in_trash") and not r.get("archived") and _page_title(r) == title]
    if not pages:
        raise InboxError("view_parent_not_found")
    if len(pages) > 1:
        raise InboxError("view_parent_ambiguous")
    return _id(pages[0]["id"])


def find_view(client, parent_id):
    """(database id, data source id) of the view under the parent page, or None."""
    found = client.call("POST", "/search", {"query": DB_TITLE, "filter": {"property": "object", "value": "data_source"}, "page_size": 25})
    hits = []
    for r in found.get("results", []):
        if r.get("object") != "data_source" or r.get("in_trash") or r.get("archived") or _plain_title(r.get("title")) != DB_TITLE:
            continue
        home = (r.get("database_parent") or {}).get("page_id") or (r.get("parent") or {}).get("page_id")
        if _id(home) == _id(parent_id):
            hits.append(r)
    if len(hits) > 1:
        raise InboxError("view_ambiguous")
    if not hits:
        return None
    parent = hits[0].get("parent") or {}
    return _id(parent.get("database_id") or ""), _id(hits[0]["id"])


def create_view(client, parent_id):
    """Create the view database under the parent page (the one container the writer creates). -> (database id, data source id)."""
    made = client.call_once("POST", "/databases", {"parent": {"type": "page_id", "page_id": parent_id}, "title": [{"type": "text", "text": {"content": DB_TITLE}}],
                                                    "initial_data_source": {"properties": schema()}})
    sources = made.get("data_sources") or []
    if not sources:
        raise InboxError("view_create_unverified")
    return _id(made["id"]), _id(sources[0]["id"])


def ensure_view(client, create=True):
    """-> (database id, data source id, created): finds the QA parent, then the view under it, creating the view only when asked and when it does not exist."""
    parent = find_parent(client)
    found = find_view(client, parent)
    if found:
        return (*found, False)
    if not create:
        raise InboxError("view_missing")
    return (*create_view(client, parent), True)


class ViewWriter:
    """Create, update and archive rows of ONE data source, tracked by the row map built from `current_rows()`."""

    def __init__(self, client, source_id):
        self.client, self.source_id = client, _id(source_id)
        self.pages = {}                                  # Record ID -> page id of the rows we own
        self.extras = []                                 # page ids of duplicate rows (same Record ID): archived by `reconcile`

    def current_rows(self):
        """Every row we own, from the data source (all pages of the query), as dicts with `Archived` False. Rebuilds the row map."""
        self.pages, self.extras = {}, []
        rows, cursor = {}, None
        while True:
            body = {"page_size": 100, "filter": {"property": "Managed by", "select": {"equals": MANAGED}}, "sorts": [{"timestamp": "created_time", "direction": "ascending"}]}
            if cursor:
                body["start_cursor"] = cursor
            answer = self.client.query_data_source(self.source_id, body)
            for page in answer.get("results", []):
                if page.get("in_trash") or page.get("archived"):
                    continue
                if _id((page.get("parent") or {}).get("data_source_id")) != self.source_id:
                    continue                             # another data source: never ours
                row = from_page(page)
                key = row["Record ID"]
                if not key:
                    continue
                if key in self.pages:
                    self.extras.append(_id(page["id"]))
                    continue
                self.pages[key] = _id(page["id"])
                rows[key] = {**row, "Archived": False}
            if not answer.get("has_more"):
                break
            cursor = answer.get("next_cursor")
        return [rows[k] for k in sorted(rows)]

    def write(self, op):
        """One plan operation (op, Record ID, payload). A create goes to our data source; an update or archive only to a page in the row map."""
        kind, key, payload = op
        if kind == view_plan.CREATE:
            row = {**payload, "Record ID": key}
            self.client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": self.source_id}, "properties": to_properties(row)})
            return
        page = self.pages.get(key)
        if page is None:
            raise ScopeError("INBOX_VIEW_WRITE_OUT_OF_SCOPE")
        if kind == view_plan.UPDATE:
            changed = {k: v for k, v in payload.items() if k != "Archived" and k in PROPERTIES}
            if changed:
                self.client.update_page_properties(page, to_properties(changed, only=set(changed)))
            if payload.get("Archived") is False:
                self.client.call_once("PATCH", f"/pages/{page}", {"in_trash": False})
            return
        if kind == view_plan.ARCHIVE:
            self.client.call_once("PATCH", f"/pages/{page}", {"in_trash": True})
            return
        raise ScopeError("INBOX_VIEW_OPERATION_UNKNOWN")

    def archive_extras(self):
        """Duplicate rows (two pages for one Record ID, e.g. after a retried create) are archived; the oldest stays."""
        for page in self.extras:
            self.client.call_once("PATCH", f"/pages/{page}", {"in_trash": True})
        done, self.extras = len(self.extras), []
        return done


def reconcile(writer, desired):
    """Plan, write, read back, plan again. -> a receipt of counts and flags only. `readback_equal` is true when planning again against what Notion now holds gives nothing (no churn, nothing missing)."""
    wanted = [canon(row) for row in desired]
    before = writer.current_rows()
    extras = writer.archive_extras()
    ops = view_plan.plan(before, wanted)
    view_plan.execute(ops, writer.write)
    after = writer.current_rows()
    again = view_plan.plan(after, wanted)
    counts = {op: sum(1 for o in ops if o[0] == op) for op in (view_plan.CREATE, view_plan.UPDATE, view_plan.ARCHIVE)}
    kinds = {}
    for row in after:
        kinds[row["Kind"]] = kinds.get(row["Kind"], 0) + 1
    return {"created": counts[view_plan.CREATE], "updated": counts[view_plan.UPDATE], "archived": counts[view_plan.ARCHIVE], "duplicates_archived": extras, "rows": len(after),
            "by_kind": kinds, "readback_equal": not again, "replan_ops": len(again), "wanted": len(wanted)}


def rollback(writer):
    """Archive every row we own (rollback, item 5); the "not connected" block text is restored by the production step, not here. -> a receipt: how many were archived and how many remain."""
    rows = writer.current_rows()
    for row in rows:
        writer.write((view_plan.ARCHIVE, row["Record ID"], None))
    remaining = writer.current_rows()
    return {"archived": len(rows), "remaining": len(remaining), "rolled_back": not remaining}


__all__ = ["NotionError", "ScopeError", "ViewWriter", "canon", "create_view", "ensure_view", "find_parent", "find_view", "from_page", "reconcile", "rollback", "schema", "to_properties"]
