"""Gives a Hiring Decision set by hand in the Job Ledger the record the Close screen's close keeps.

For every Ledger row whose Hiring Decision is set: fill Decision On when empty (today, Central), append one history
line when none exists for the decision (op `hand-<page id>-<decision>`, so a repeat is a no-op), and set Freshness
Status to Closed. Hiring Decision itself is never changed; a row with all three is untouched. Each write is read back.
Dry run unless live. Counts and fixed codes only."""
import json
import re

from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.notion_pages import query
from . import decision as D

DECISION_ON, FRESHNESS, CLOSED = "Decision On", "Freshness Status", "Closed"
MAX_PAGES = 40
REASON = "Set by hand in the Job Ledger; recorded by the hourly run"


def _date(props, name):
    return ((props.get(name) or {}).get("date") or {}).get("start") or ""


def _op(page_id, decision):
    return "hand-" + page_id.replace("-", "")[:12] + "-" + re.sub(r"[^a-z0-9]", "", decision.lower())[:12]


def plan(page, now):
    """-> {"props": {...}, "history": new text | None} for one Ledger row, or None when it has all three."""
    props = page.get("properties") or {}
    decision = ((props.get(D.DECISION) or {}).get("select") or {}).get("name")
    if not decision or page.get("archived") or page.get("in_trash"):
        return None
    history = D._text(props.get(D.HISTORY), "rich_text")
    log = D.entries(history)
    out = {}
    if not _date(props, DECISION_ON):
        out[DECISION_ON] = {"date": {"start": now.date().isoformat()}}
    new = history
    if not any(e.get("outcome") == decision or e.get("op") == _op(page["id"], decision) for e in log):
        line = json.dumps({"op": _op(page["id"], decision), "action": "hand-set", "at": now.isoformat(timespec="seconds"), "reason": REASON, "outcome": decision}, separators=(",", ":"), sort_keys=True)
        new = f"{history}\n{line}" if history else line
        if len(new) > D.HISTORY_MAX:
            raise D.fail("HISTORY_FULL")
        out[D.HISTORY] = {"rich_text": rich_text(new)}
    if ((props.get(FRESHNESS) or {}).get("select") or {}).get("name") != CLOSED:
        out[FRESHNESS] = {"select": {"name": CLOSED}}
    return {"props": out, "history": new} if out else None


def run(client, live, now):
    counts = {"decided": 0, "would_sync": 0, "synced": 0, "failed": 0}
    try:
        pages = [row for page in query(client, None, {"page_size": 100, "filter": {"property": D.DECISION, "select": {"is_not_empty": True}}}, D.fail, incomplete="LEDGER_INCOMPLETE", limit=MAX_PAGES)
                 for row in page["results"]]
    except NotionError:
        counts["failed"] += 1
        return counts
    counts["decided"] = len(pages)
    for page in pages:
        try:
            todo = plan(page, now)
        except NotionError:
            counts["failed"] += 1
            continue
        if not todo:
            continue
        counts["would_sync"] += 1
        if not live:
            continue
        try:
            with fixed(NotionError, D.fail, "WRITE_FAILED"):
                client.update_page_properties(page["id"], todo["props"])
            with fixed(NotionError, D.fail, "READBACK_FAILED"):
                back = client.get_page(page["id"])
            if plan(back, now) is not None or D.snapshot(back)[1] != D.snapshot(page)[1]:
                raise D.fail("READBACK_MISMATCH")
            counts["synced"] += 1
        except NotionError:
            counts["failed"] += 1
    return counts
