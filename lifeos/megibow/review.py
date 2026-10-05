"""The MegIBOW Review list (D134): a small Notion database Jim answers with a dropdown. Private to Jim's workspace; fixed codes in logs, counts only.

V7 adds one row per uncertain item (Resolution empty), reads Jim's Resolution on the next run, hands the decisions back, marks the row Applied, and sets it to Archived 14 days after (never deletes a row).
An item that stops being uncertain with no decision is marked Superseded. An item left alone stays Open and never counts."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from lifeos.platform.notion_client import NotionError, rich_text

SCHEMA = {"Item": "title", "Week of": "date", "Candidate": "select", "Why uncertain": "select", "Person or company": "rich_text", "Resolution": "select",
          "Status": "select", "Source key": "rich_text"}
WHY = {"relevance": "Relevance", "occurrence": "Occurrence", "contact_type": "Contact type", "creation_time_unknown": "Creation time unknown", "deferred": "Deferred"}
ARCHIVE_AFTER = timedelta(days=14)


class ReviewError(NotionError):
    """Fixed codes only."""


def _text(prop):
    return "".join(i.get("plain_text") or "" for i in (prop or {}).get("rich_text") or [] if isinstance(i, dict))


def check_schema(notion, source_id):
    try:
        props = notion.call("GET", f"/data_sources/{quote(source_id, safe='')}").get("properties")
    except NotionError as error:
        raise ReviewError(f"MEGIBOW_REVIEW_SCHEMA_READ_FAILED:{error}") from None
    if not isinstance(props, dict) or any((props.get(n) or {}).get("type") != t for n, t in SCHEMA.items()):
        raise ReviewError("MEGIBOW_REVIEW_SCHEMA_MISMATCH")


def read(notion, source_id):
    rows, cursor = [], None
    for _ in range(20):
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        page = notion.query_data_source(source_id, body)
        for p in page.get("results", []):
            props = p.get("properties") or {}
            rows.append({"id": p["id"], "key": _text(props.get("Source key")), "resolution": ((props.get("Resolution") or {}).get("select") or {}).get("name") or "",
                         "status": ((props.get("Status") or {}).get("select") or {}).get("name") or "", "edited": p.get("last_edited_time") or ""})
        if not page.get("has_more"):
            return rows
        cursor = page.get("next_cursor")
    raise ReviewError("MEGIBOW_REVIEW_READ_INCOMPLETE")


def plan(items, rows, now):
    """items: [{key, candidate, why, week, who}] now uncertain. -> {create, decisions{key: resolution}, applied[id], superseded[id], archive[id]}."""
    by_key = {r["key"]: r for r in rows if r["key"]}
    active = {i["key"] for i in items}
    out = {"create": [i for i in items if i["key"] not in by_key], "decisions": {}, "applied": [], "superseded": [], "archive": []}
    for r in rows:
        if r["resolution"] and r["resolution"] != "Defer" and r["status"] != "Applied":
            out["decisions"][r["key"]] = r["resolution"]
            out["applied"].append(r["id"])
        elif r["status"] == "Open" and r["key"] not in active and not r["resolution"]:
            out["superseded"].append(r["id"])
        elif r["status"] in ("Applied", "Superseded") and r["edited"]:
            try:
                age = now - datetime.fromisoformat(r["edited"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if age > ARCHIVE_AFTER:
                out["archive"].append(r["id"])
    return out


def _props(i):
    return {"Item": {"title": rich_text(f"{i['candidate']} · {i['who']}")}, "Week of": {"date": {"start": i["week"]}}, "Candidate": {"select": {"name": i["candidate"]}},
            "Why uncertain": {"select": {"name": WHY.get(i["why"], "Relevance")}}, "Person or company": {"rich_text": rich_text(i["who"])},
            "Status": {"select": {"name": "Open"}}, "Source key": {"rich_text": rich_text(i["key"])}}


def write(notion, source_id, plan_):
    for item in plan_["create"]:
        notion.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": _props(item)})
    for page_id in plan_["applied"]:
        notion.update_page_properties(page_id, {"Status": {"select": {"name": "Applied"}}})
    for page_id in plan_["superseded"]:
        notion.update_page_properties(page_id, {"Status": {"select": {"name": "Superseded"}}})
    for page_id in plan_["archive"]:                                    # never deleted: the row only leaves Jim's Open view
        notion.update_page_properties(page_id, {"Status": {"select": {"name": "Archived"}}})
