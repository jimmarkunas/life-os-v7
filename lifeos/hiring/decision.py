"""Hiring Decision on the Job Ledger row.

Confirm Rejected (any closing outcome) and Reopen are each one bounded write to ONE Ledger row found by page id
(`route`): `Hiring Decision` is set or cleared with one appended `Hiring Decision History` line, and nothing else on
the row is written (read before and after; any difference fails the read-back). Detected mail is evidence, never
authority: the request must say `confirmed`. Notion has no conditional write, so the row is re-read just before the
write; the history's operation id makes a retry a no-op. Errors carry fixed codes only."""
import json
import re

from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.platform.notion_pages import query
from . import models as M

ACTION, REOPEN, REJECTED, PASSED = "CONFIRM_REJECTED", "REOPEN", "Rejected", "Passed"
DECISION, HISTORY, DISMISSED = "Hiring Decision", "Hiring Decision History", "Dismissed Outcome Ref"
HISTORY_MAX, REASON_MAX, MAX_PAGES = 6000, 200, 40
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
    if not isinstance(request, dict) or request.get("action") not in (ACTION, REOPEN) or request.get("confirmed") is not True:
        raise fail("NOT_CONFIRMED")
    reason, outcome = request.get("reason"), request.get("outcome", REJECTED)
    fields = {k: (request.get(k).strip() if isinstance(request.get(k), str) else "") for k in ("company", "role")}
    if not (outcome in M.OUTCOMES and _page_id(request.get("id")) and fields["company"] and fields["role"] and isinstance(request.get("op"), str) and OP.match(request["op"])
            and isinstance(reason, str) and reason.strip() and len(reason.strip()) <= REASON_MAX and reason.strip().isprintable()):
        raise fail("REQUEST_INVALID")
    return {"id": _page_id(request["id"]), "op": request["op"], "reason": reason.strip(), "action": request["action"], "outcome": outcome, "hired_ok": request.get("reopen_hired") is True, **fields}


def check_schema(client, outcome=REJECTED):
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    options = {o.get("name") for o in ((props.get(DECISION) or {}).get("select") or {}).get("options") or []}
    if (props.get(DECISION) or {}).get("type") != "select" or not {REJECTED, PASSED, outcome} <= options or any((props.get(n) or {}).get("type") != "rich_text" for n in (HISTORY, DISMISSED)):
        raise fail("SCHEMA_MISSING")


def _scan(client, flt):
    return [row for page in query(client, None, {"page_size": 100, "filter": flt}, fail, incomplete="LEDGER_INCOMPLETE", limit=MAX_PAGES) for row in page["results"]]


PURSUIT = {"or": [{"property": "Applied", "checkbox": {"equals": True}}, {"property": "Stage", "select": {"is_not_empty": True}}]}


def ledger_pages(client):
    """Every Ledger row that is a pursuit (Applied ticked or Stage set), read completely or not at all."""
    return _scan(client, PURSUIT)


def _pursuit(props):
    return (props.get("Applied") or {}).get("checkbox") is True or bool(((props.get("Stage") or {}).get("select") or {}).get("name"))


def entries(text):
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
    props, parent = page.get("properties") or {}, page.get("parent") or {}
    if parent.get("type") != "data_source_id" or not _page_id(parent.get("data_source_id")) or _page_id(parent["data_source_id"]) != _page_id(source):
        raise fail("NOT_LEDGER_ROW")
    if (page.get("archived") or page.get("in_trash") or _page_id(page.get("id")) != want["id"] or not _pursuit(props)
            or not M.same(_text(props.get("Company"), "rich_text"), _text(props.get("Job"), "title"), want["company"], want["role"])):
        raise fail("STALE_IDENTITY")


def snapshot(page):
    props = page.get("properties") or {}
    kept = {"Applied": (props.get("Applied") or {}).get("checkbox"), "Applied On": (props.get("Applied On") or {}).get("date"),
            "Saturn Decision": ((props.get("Saturn Decision") or {}).get("select") or {}).get("name"),
            "Stage": ((props.get("Stage") or {}).get("select") or {}).get("name"), "Company": _text(props.get("Company"), "rich_text"), "Job": _text(props.get("Job"), "title"),
            "Stable Job Key": _text(props.get("Stable Job Key"), "rich_text"), DISMISSED: _text(props.get(DISMISSED), "rich_text")}
    return kept, ((props.get(DECISION) or {}).get("select") or {}).get("name"), _text(props.get(HISTORY), "rich_text")


def rows(pages):
    out = [{"id": p.get("id"), "company": _text((p.get("properties") or {}).get("Company"), "rich_text"), "role": _text((p.get("properties") or {}).get("Job"), "title"),
            "decision": (((p.get("properties") or {}).get(DECISION) or {}).get("select") or {}).get("name")} for p in pages]
    return [r for r in out if r["company"] and r["role"] and _page_id(r["id"])]


def route(rows, want):
    """-> the ONE Ledger row carrying this pursuit's decision, or a fixed code. Nothing is written.

    A page id resolves to that row (company AND role must still match, else `STALE_IDENTITY`); otherwise company AND
    role must match exactly one pursuit, else `NO_MATCH` or `AMBIGUOUS`."""
    named = lambda r: not (want.get("company") or want.get("role")) or M.same(r["company"], r["role"], want.get("company", ""), want.get("role", ""))
    pid = _page_id(want.get("id"))
    mine = [r for r in rows if pid and _page_id(r.get("id")) == pid]
    if len(mine) > 1:
        raise fail("AMBIGUOUS")
    if mine:
        if not named(mine[0]):
            raise fail("STALE_IDENTITY")
        return mine[0]
    if not (want.get("company") and want.get("role")):
        raise fail("NO_MATCH")
    found = [r for r in rows if named(r)]
    if len(found) > 1:
        raise fail("AMBIGUOUS")
    if not found:
        raise fail("NO_MATCH")
    return found[0]


def confirm_rejected(client, request, live, now):
    """-> {"status": dry_run | recorded | already_recorded, "verified": bool, "entries": n} or a DecisionError.

    Confirm sets Hiring Decision; Reopen clears it (a Hired outcome only with `reopen_hired`); either appends one
    history line carrying the op id. A history line without its decision (partial write) is finished, never repeated.
    Success is never taken from the HTTP response alone: the row is fetched again and compared."""
    want = parse(request)
    reopen = want["action"] == REOPEN
    check_schema(client, want["outcome"])
    page = client.get_page(want["id"])
    current(page, want, client.source)
    kept, decision, history = snapshot(page)
    log = entries(history)
    ops, after = [e["op"] for e in log], None if reopen else want["outcome"]
    mine = [e for e in log if e["op"] == want["op"]]
    if mine:
        if decision == after:
            return {"status": "already_recorded", "verified": True, "entries": len(ops)}
        before = mine[0].get("outcome") if reopen else None
        if len(mine) > 1 or log[-1] is not mine[0] or mine[0].get("action") != want["action"] or decision != before:
            raise fail("CONFLICT")                                                              # something happened after this operation: a retry never overwrites it
        new = history
    else:
        if reopen:
            was = decision
            if was not in M.OUTCOMES + (PASSED,):
                raise fail("NOT_CLOSED")
            if was == "Hired" and not want["hired_ok"]:
                raise fail("HIRED_REOPEN_UNCONFIRMED")
        elif decision is not None:
            raise fail("CONFLICT")
        line = json.dumps({"op": want["op"], "action": want["action"], "at": now.isoformat(timespec="seconds"), "reason": want["reason"], "outcome": decision if reopen else want["outcome"]},
                          separators=(",", ":"), sort_keys=True)
        new = f"{history}\n{line}" if history else line
        if len(new) > HISTORY_MAX:
            raise fail("HISTORY_FULL")
    if not live:
        return {"status": "dry_run", "verified": False, "entries": len(ops)}
    again = client.get_page(want["id"])
    current(again, want, client.source)
    if again.get("last_edited_time") != page.get("last_edited_time") or snapshot(again) != (kept, decision, history):
        raise fail("CONCURRENT_CHANGE")
    with fixed(NotionError, fail, "WRITE_FAILED"):
        client.update_page_properties(want["id"], {DECISION: {"select": None if reopen else {"name": want["outcome"]}}, **({HISTORY: {"rich_text": rich_text(new)}} if new != history else {})})
    with fixed(NotionError, fail, "READBACK_FAILED"):
        back = client.get_page(want["id"])
    if snapshot(back) != (kept, after, new):
        raise fail("READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "entries": len(entries(new))}


HAND_SET = "LIFE OS Hand-Set"            # the Job Ledger rich_text property carrying them; absent from the schema = DECISION_SCHEMA_MISSING


def _op(line):
    m = M.HOLD_LINE.fullmatch(line) or M.NEXT_LINE.fullmatch(line) or M.SENT_LINE.fullmatch(line)
    if m:
        return m.group(3)
    m = M.UNSENT_LINE.fullmatch(line)
    return m.group(2) if m else None


def _kept(lines, line):
    kind = line.split(":", 1)[0]
    keep = {"v7-hold": 1, "v7-next": 1, "v7-sent": M.SENT_MAX, "v7-unsent": M.SENT_MAX}
    out = []
    for prefix, n in keep.items():
        mine = [t for t in lines if t.startswith(prefix + ":")]
        out += mine[-(n - 1):] if prefix == kind and n > 1 else [] if prefix == kind else mine[-n:]
    return out + [line]


def set_hand(client, want, line, live):
    """-> {"status": dry_run | recorded | already_recorded, "verified": bool} or a DecisionError.

    Writes ONE hand-set line on the ONE open Ledger row `want` names; re-read before the write and read back after."""
    op = _op(line)
    if not op or not _page_id(want.get("id")):
        raise fail("REQUEST_INVALID")
    want = {**want, "id": _page_id(want["id"])}
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    if (props.get(HAND_SET) or {}).get("type") != "rich_text":
        raise fail("SCHEMA_MISSING")

    def read():
        page = client.get_page(want["id"])
        current(page, want, client.source)
        text = _text((page.get("properties") or {}).get(HAND_SET), "rich_text")
        if snapshot(page)[1] is not None:
            raise fail("CLOSED")
        if text and list(M.hand_lines(text)) != text.splitlines():
            raise fail("HISTORY_UNREADABLE")                                              # text in the property that is not ours is never replaced
        return page, text
    page, text = read()
    if any(_op(t) == op for t in text.splitlines()):
        return {"status": "already_recorded", "verified": True}
    new = "\n".join(_kept(M.hand_lines(text), line))
    if not live:
        return {"status": "dry_run", "verified": False}
    again, text_again = read()
    if again.get("last_edited_time") != page.get("last_edited_time") or (snapshot(again), text_again) != (snapshot(page), text):
        raise fail("CONCURRENT_CHANGE")
    with fixed(NotionError, fail, "WRITE_FAILED"):
        client.update_page_properties(want["id"], {HAND_SET: {"rich_text": rich_text(new)}})
    with fixed(NotionError, fail, "READBACK_FAILED"):
        back = client.get_page(want["id"])
    if snapshot(back) != snapshot(page) or _text((back.get("properties") or {}).get(HAND_SET), "rich_text") != new:
        raise fail("READBACK_MISMATCH")
    return {"status": "recorded", "verified": True}
