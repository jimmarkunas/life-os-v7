"""D194 Hiring Decision, the first action: a person confirms an employer rejection (Confirm Rejected).

One bounded write to ONE exact Job Ledger row, aimed by its stable page id AND its company AND role: `Hiring Decision = Rejected` plus one appended line in `Hiring Decision History`.
`Applied`, `Applied On`, `Saturn Decision`, Notes pages and interview children are never written (read before and after; any difference fails the read-back). Detected mail is evidence, never
authority: the request must say `confirmed`. Notion has no conditional write, so the row is read, re-read just before the write (a changed edit time, decision or history refuses it) and
read back after it; the operation id makes a retry a no-op. Fixed codes only: no company, role, id or reason ever enters an error. The caller refreshes the Hiring Pipeline only after this returns."""
import json
import re
from datetime import datetime

from lifeos.platform.notion_client import NotionError, rich_text
from . import models as M

ACTION, REJECTED, PASSED = "CONFIRM_REJECTED", "Rejected", "Passed"
DECISION, HISTORY, DISMISSED = "Hiring Decision", "Hiring Decision History", "Dismissed Outcome Ref"
HISTORY_MAX, REASON_MAX, MAX_PAGES = 6000, 200, 40            # history longer than this is refused, never cut; a reason longer than this is refused, never shortened
OP = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")


class DecisionError(NotionError):
    """Fixed codes only."""


def fail(code):
    return DecisionError("DECISION_" + code)


def _text(prop, kind):
    return "".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in (prop or {}).get(kind) or []).strip()


def _page_id(value):
    value = str(value or "").replace("-", "").lower()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None


def parse(request):
    """The validated request, or a fixed code. Nothing is written for a request that is not an explicit, complete confirmation."""
    if not isinstance(request, dict) or request.get("action") != ACTION or request.get("confirmed") is not True:
        raise fail("NOT_CONFIRMED")
    reason = request.get("reason")
    fields = {k: (request.get(k).strip() if isinstance(request.get(k), str) else "") for k in ("company", "role")}
    if not (_page_id(request.get("id")) and fields["company"] and fields["role"] and isinstance(request.get("op"), str) and OP.match(request["op"])
            and isinstance(reason, str) and reason.strip() and len(reason.strip()) <= REASON_MAX and reason.strip().isprintable()):
        raise fail("REQUEST_INVALID")
    return {"id": _page_id(request["id"]), "op": request["op"], "reason": reason.strip(), **fields}


def check_schema(client):
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    options = {o.get("name") for o in ((props.get(DECISION) or {}).get("select") or {}).get("options") or []}
    if (props.get(DECISION) or {}).get("type") != "select" or not {REJECTED, PASSED} <= options or any((props.get(n) or {}).get("type") != "rich_text" for n in (HISTORY, DISMISSED)):
        raise fail("SCHEMA_MISSING")


def applied_pages(client):
    """Every page with Applied ticked, read completely or not at all."""
    out, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        page = client.query_data_source(None, {"page_size": 100, "filter": {"property": "Applied", "checkbox": {"equals": True}}, **({"start_cursor": cursor} if cursor else {})})
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise fail("LEDGER_INCOMPLETE")
        out += page["results"]
        cursor = page.get("next_cursor")
        if not page["has_more"]:
            return out
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise fail("LEDGER_INCOMPLETE")
        seen.add(cursor)
    raise fail("LEDGER_INCOMPLETE")


def target(pages, want):
    """The one row for this company AND role whose page id is the stable id in the request. None, several, or another id: refused."""
    found = [p for p in pages if M.same(_text(p["properties"].get("Company"), "rich_text"), _text(p["properties"].get("Job"), "title"), want["company"], want["role"])]
    if not found:
        raise fail("NO_MATCH")
    if len(found) > 1:
        raise fail("AMBIGUOUS")
    if _page_id(found[0].get("id")) != want["id"]:
        raise fail("STALE_IDENTITY")
    return found[0]


def entries(text):
    """The history as a list of entries; an unreadable line is refused, never skipped."""
    out = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            raise fail("HISTORY_UNREADABLE") from None
        if not isinstance(item, dict) or not isinstance(item.get("op"), str):
            raise fail("HISTORY_UNREADABLE")
        out.append(item)
    return out


def current(page, want, source):
    """The fetched page itself, not the query that found it, must still be the requested application: a row of the canonical Ledger (its parent is that data source), this page id, not
    archived, Applied ticked, and company AND role as requested."""
    props, parent = page.get("properties") or {}, page.get("parent") or {}
    if parent.get("type") != "data_source_id" or not _page_id(parent.get("data_source_id")) or _page_id(parent["data_source_id"]) != _page_id(source):
        raise fail("NOT_LEDGER_ROW")
    if (page.get("archived") or page.get("in_trash") or _page_id(page.get("id")) != want["id"] or (props.get("Applied") or {}).get("checkbox") is not True
            or not M.same(_text(props.get("Company"), "rich_text"), _text(props.get("Job"), "title"), want["company"], want["role"])):
        raise fail("STALE_IDENTITY")


def snapshot(page):
    """What a read-back must find unchanged, and the decision and history as they stand."""
    props = page.get("properties") or {}
    kept = {"Applied": (props.get("Applied") or {}).get("checkbox"), "Applied On": (props.get("Applied On") or {}).get("date"),      # the whole date object: start, end and time zone
            "Saturn Decision": ((props.get("Saturn Decision") or {}).get("select") or {}).get("name"), "Company": _text(props.get("Company"), "rich_text"), "Job": _text(props.get("Job"), "title"),
            "Stable Job Key": _text(props.get("Stable Job Key"), "rich_text"), DISMISSED: _text(props.get(DISMISSED), "rich_text")}
    return kept, ((props.get(DECISION) or {}).get("select") or {}).get("name"), _text(props.get(HISTORY), "rich_text")


def confirm_rejected(client, request, live, now):
    """-> {"status": dry_run | recorded | already_recorded, "verified": bool, "entries": n} or a DecisionError. Never success from an HTTP response alone: the exact row is fetched again and compared."""
    want = parse(request)
    check_schema(client)
    row = target(applied_pages(client), want)
    page = client.get_page(row["id"])
    current(page, want, client.source)
    kept, decision, history = snapshot(page)
    ops = [e["op"] for e in entries(history)]
    if want["op"] in ops:
        if decision == REJECTED:
            return {"status": "already_recorded", "verified": True, "entries": len(ops)}      # a retry of the same confirmed operation: nothing is written twice
        raise fail("CONFLICT")
    if decision is not None:
        raise fail("CONFLICT")                                                                  # another decision is already recorded: never replaced here
    line = json.dumps({"op": want["op"], "action": ACTION, "at": now.isoformat(timespec="seconds"), "reason": want["reason"]}, separators=(",", ":"), sort_keys=True)
    new = f"{history}\n{line}" if history else line
    if len(new) > HISTORY_MAX:
        raise fail("HISTORY_FULL")
    if not live:
        return {"status": "dry_run", "verified": False, "entries": len(ops)}
    again = client.get_page(row["id"])
    current(again, want, client.source)                                                                        # still the requested application immediately before the write
    if again.get("last_edited_time") != page.get("last_edited_time") or snapshot(again) != (kept, decision, history):
        raise fail("CONCURRENT_CHANGE")
    try:
        client.update_page_properties(row["id"], {DECISION: {"select": {"name": REJECTED}}, HISTORY: {"rich_text": rich_text(new)}})
    except NotionError:
        raise fail("WRITE_FAILED") from None
    try:
        back = client.get_page(row["id"])
    except NotionError:
        raise fail("READBACK_FAILED") from None
    if snapshot(back) != (kept, REJECTED, new):
        raise fail("READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "entries": len(ops) + 1}
