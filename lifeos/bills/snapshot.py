"""Read the complete Notion Bill Tracker into a private snapshot; logs contain counts only."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform import db
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.snapshot_store import Store

LOCAL_TZ = "America/Chicago"
SCHEMA_V = 1
CRITICAL = ("Name", "Status", "Cycle", "Paid", "Due Date", "Next Due")      # these drive the due-state: a surprise here fails closed
FIELDS = CRITICAL + ("Last Paid", "Costs per Cycle", "Last Observed Amount")  # display-only: an unfamiliar shape (rollup, currency) is None, as in V1
STORE = Store("v7_bills_snapshot")


class BillsError(NotionError):
    """Fixed codes only: never expose bill names, values, tokens, or API bodies."""


def _plain(items):
    if not isinstance(items, list):
        raise BillsError("BILLS_PROPERTY_INVALID")
    if any(not isinstance(item, dict) or not isinstance(item.get("plain_text"), str) for item in items):
        raise BillsError("BILLS_PROPERTY_INVALID")
    return "".join(item["plain_text"] for item in items)


def _property(properties, name):
    try:
        return _strict(properties, name)
    except BillsError:
        if name in CRITICAL:
            raise
        return None


def _strict(properties, name):
    prop = properties.get(name)
    if not isinstance(prop, dict):
        raise BillsError("BILLS_PROPERTY_MISSING")
    kind = prop.get("type")
    if kind == "title":
        return _plain(prop.get("title"))
    if kind == "rich_text":
        return _plain(prop.get("rich_text"))
    if kind == "checkbox":
        value = prop.get("checkbox")
        if not isinstance(value, bool):
            raise BillsError("BILLS_PROPERTY_INVALID")
        return value
    if kind == "number":
        return prop.get("number")
    if kind in ("select", "status"):
        item = prop.get(kind)
        return item.get("name") if isinstance(item, dict) else None
    if kind == "date":
        item = prop.get("date")
        return item.get("start") if isinstance(item, dict) else None
    if kind == "formula":
        formula = prop.get("formula")
        if not isinstance(formula, dict) or formula.get("type") not in ("date", "string", "number", "boolean"):
            raise BillsError("BILLS_FORMULA_INVALID")
        result = formula.get(formula["type"])
        return result.get("start") if formula["type"] == "date" and isinstance(result, dict) else result
    raise BillsError("BILLS_PROPERTY_INVALID")


def compact(page):
    properties = page.get("properties")
    if not isinstance(properties, dict) or not isinstance(page.get("id"), str) or not page["id"]:
        raise BillsError("BILLS_PAGE_INVALID")
    values = {name: _property(properties, name) for name in FIELDS}
    if not values["Name"].strip():
        raise BillsError("BILLS_PROPERTY_INVALID")
    return {"page_id": page["id"], **values}


def query_all(client):
    try:
        return _query_all(client)
    except NotionError:
        raise
    except Exception:
        raise BillsError("BILLS_READ_FAILED") from None


def _query_all(client):
    rows, cursor, seen = [], None, set()
    while True:
        body = {"page_size": 100}
        if cursor:
            if cursor in seen:
                raise BillsError("BILLS_PAGINATION_INVALID")
            seen.add(cursor)
            body["start_cursor"] = cursor
        page = client.query_data_source(body=body)
        results, has_more = page.get("results"), page.get("has_more")
        if not isinstance(results, list) or not isinstance(has_more, bool):
            raise BillsError("BILLS_PAGE_INCOMPLETE")
        rows.extend(compact(item) for item in results)
        if not has_more:
            return rows
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise BillsError("BILLS_PAGE_INCOMPLETE")


def save(connection, snapshot):
    STORE.save(connection, 1, snapshot)


def load(connection):
    return STORE.load(connection, 1)


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    client = client or Client(environ, token_name="NOTION_BILLS_TOKEN", source_name="NOTION_BILLS_DATA_SOURCE_ID")
    now = now or datetime.now(ZoneInfo(LOCAL_TZ))
    now = now.astimezone(ZoneInfo(LOCAL_TZ)) if now.tzinfo else now.replace(tzinfo=ZoneInfo(LOCAL_TZ))
    rows = query_all(client)                         # limit never truncates the requested full snapshot
    snapshot = {"schema": SCHEMA_V, "taken_at": now.isoformat(), "timezone": LOCAL_TZ, "rows": rows}
    from . import state
    buckets = state.classify(rows, now.date())
    counts = {"rows": len(rows), "active": sum(row["Status"] == "Active" for row in rows),
              "paid": len(buckets["paid"]), "stale_due": len(buckets["stale_due"]),
              "due_today": len(buckets["due_today"]), "due_7d": len(buckets["due_next_7_days"]), "saved": 0}
    if live:
        try:
            with (connect or db.connect)() as connection:
                STORE.save_verified(connection, 1, snapshot, lambda code: BillsError("BILLS_" + code))
        except BillsError:
            raise
        except Exception:
            raise BillsError("BILLS_STORE_FAILED") from None
        counts["saved"] = 1
    return counts
