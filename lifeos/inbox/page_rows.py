"""LIFE-711: the rows of the Action Inbox list (the inline Notion database on the page): create, update and archive only the rows this writer owns."""
# A row is owned when its `Managed by` is MANAGED_BY. Rows are keyed by `Record ID` (the source-qualified id page_view makes). The plan is a pure function of (current, desired): the same input gives the same
# plan, and applying a plan then planning again gives nothing. A row is archived only when its record is gone from a payload that read cleanly (`clean` workspaces); a failed or missing read keeps the
# last rows in place, so a down source never empties the list. Every write targets the data source the page's own database block names, or a row read from it through the owned-row filter.
from lifeos.inbox.model import InboxError

MANAGED_BY = "v7-inbox-writer"
FIELDS = ("Outcome", "Workspace", "Source", "Bucket", "Rank", "Owner", "Next move", "Due", "Freshness", "Link")


def _title(text):
    return {"title": [{"type": "text", "text": {"content": str(text)[:200]}}]}


def _select(name):
    return {"select": {"name": name}} if name else {"select": None}


def _text(text):
    return {"rich_text": [{"type": "text", "text": {"content": str(text)[:1900]}}] if text else []}


def props(row):
    """The Notion property payload for a desired row."""
    return {"Outcome": _title(row["Outcome"]), "Workspace": _select(row["Workspace"]), "Source": _select(row["Source"]), "Bucket": _select(row["Bucket"]), "Rank": {"number": row["Rank"]},
            "Owner": _select(row["Owner"]), "Next move": _text(row["Next move"]), "Due": _text(row["Due"]), "Freshness": _select(row["Freshness"]),
            "Link": {"url": row["Link"] or None}, "Record ID": _text(row["id"]), "Managed by": _select(MANAGED_BY)}


def _plain(prop):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(piece.get("plain_text", "") for piece in value or ())
    if kind == "select":
        return (value or {}).get("name", "")
    return value if value is not None else ""


def read(page):
    """A Notion row as the same dict shape `desired` uses (plus `page_id`), or None when it is not an owned row."""
    p = page.get("properties", {})
    if _plain(p.get("Managed by", {})) != MANAGED_BY:
        return None
    row = {"id": _plain(p.get("Record ID", {})), "page_id": page["id"], "Outcome": _plain(p.get("Outcome", {})), "Workspace": _plain(p.get("Workspace", {})), "Source": _plain(p.get("Source", {})),
           "Bucket": _plain(p.get("Bucket", {})), "Rank": _plain(p.get("Rank", {})), "Owner": _plain(p.get("Owner", {})), "Next move": _plain(p.get("Next move", {})), "Due": _plain(p.get("Due", {})),
           "Freshness": _plain(p.get("Freshness", {})), "Link": _plain(p.get("Link", {}))}
    return row


def plan(current, desired, clean):
    """-> ((op, id, row, page_id), ...) sorted: create missing, update changed, archive owned rows whose record left a cleanly read workspace."""
    have = {r["id"]: r for r in current if r["id"]}
    want = {r["id"]: r for r in desired}
    ops = []
    for key, row in want.items():
        old = have.get(key)
        if old is None:
            ops.append(("create", key, row, None))
        elif any(old[f] != (row[f] if row[f] is not None else "") for f in FIELDS):
            ops.append(("update", key, row, old["page_id"]))
    for key, old in have.items():
        if key not in want and old["Workspace"].lower() in clean:
            ops.append(("archive", key, old, old["page_id"]))
    return tuple(sorted(ops, key=lambda op: ({"create": 0, "update": 1, "archive": 2}[op[0]], op[1])))


def data_source(client, database_id):
    """The one data source of the page's list database."""
    sources = client.call("GET", f"/databases/{database_id}").get("data_sources") or []
    if len(sources) != 1:
        raise InboxError("list_data_source_missing")
    return sources[0]["id"]


def current(client, source_id):
    """Every owned row, through the owned-row filter, with paging."""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100, "filter": {"property": "Managed by", "select": {"equals": MANAGED_BY}}}
        if cursor:
            body["start_cursor"] = cursor
        page = client.call("POST", f"/data_sources/{source_id}/query", body)
        rows += [r for r in (read(p) for p in page.get("results", [])) if r]
        if not page.get("has_more"):
            return rows
        cursor = page.get("next_cursor")


def apply(client, source_id, ops):
    """Execute the plan. -> {'created', 'updated', 'archived'}."""
    done = {"created": 0, "updated": 0, "archived": 0}
    for op, _, row, page_id in ops:
        if op == "create":
            client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": props(row)})
            done["created"] += 1
        elif op == "update":
            client.call_once("PATCH", f"/pages/{page_id}", {"properties": props(row)})
            done["updated"] += 1
        else:
            client.call_once("PATCH", f"/pages/{page_id}", {"in_trash": True})
            done["archived"] += 1
    return done


def reconcile(client, database_id, desired, clean):
    source_id = data_source(client, database_id)
    return apply(client, source_id, plan(current(client, source_id), desired, clean))


__all__ = ["FIELDS", "apply", "current", "data_source", "plan", "props", "read", "reconcile"]
