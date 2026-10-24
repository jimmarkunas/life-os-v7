"""D194 Hiring Decision, the first action: a person confirms an employer rejection (Confirm Rejected).

One bounded write to ONE exact Job Ledger row, aimed by its stable page id AND its company AND role: `Hiring Decision = Rejected` plus one appended line in `Hiring Decision History`.
`Applied`, `Applied On`, `Saturn Decision`, Notes pages and interview children are never written (read before and after; any difference fails the read-back). Detected mail is evidence, never
authority: the request must say `confirmed`. Notion has no conditional write, so the row is read, re-read just before the write (a changed edit time, decision or history refuses it) and
read back after it; the operation id makes a retry a no-op. Fixed codes only: no company, role, id or reason ever enters an error. The caller refreshes the Hiring Pipeline only after this returns."""
import json
import re
from datetime import datetime

from lifeos.platform import names
from lifeos.platform.notion_client import NotionError, rich_text
from . import models as M

ACTION, REOPEN, REJECTED, PASSED = "CONFIRM_REJECTED", "REOPEN", "Rejected", "Passed"
ACTIVE_TITLE, RETIRED_TITLE = "Active Opportunities", "Retired Opportunities"      # the container pages under the Hiring Pipeline page that the page reader already understands (D189)
MOVE_VERSION = "2026-03-11"                                                          # the Move page API's documented Notion-Version; the pinned version is not assumed to carry it
RECORD = re.compile(r"Hiring decision \(V7\) · (Pending )?(" + "|".join(map(re.escape, M.OUTCOMES + ("Reopened",))) + r") · (\d{4}-\d{2}-\d{2}) · .+ · op ([A-Za-z0-9_-]{8,64})\Z")
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
    if not isinstance(request, dict) or request.get("action") not in (ACTION, REOPEN) or request.get("confirmed") is not True:
        raise fail("NOT_CONFIRMED")
    reason, outcome = request.get("reason"), request.get("outcome", REJECTED)
    fields = {k: (request.get(k).strip() if isinstance(request.get(k), str) else "") for k in ("company", "role")}
    if not (outcome in M.OUTCOMES and _page_id(request.get("id")) and fields["company"] and fields["role"] and isinstance(request.get("op"), str) and OP.match(request["op"])
            and isinstance(reason, str) and reason.strip() and len(reason.strip()) <= REASON_MAX and reason.strip().isprintable()):
        raise fail("REQUEST_INVALID")
    ack = request.get("not_this_application") or []
    if not isinstance(ack, list) or not all(_page_id(x) for x in ack):
        raise fail("REQUEST_INVALID")
    return {"id": _page_id(request["id"]), "op": request["op"], "reason": reason.strip(), "action": request["action"], "outcome": outcome, "hired_ok": request.get("reopen_hired") is True, "ack": sorted({_page_id(x) for x in ack}), **fields}


def check_schema(client, outcome=REJECTED):
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    options = {o.get("name") for o in ((props.get(DECISION) or {}).get("select") or {}).get("options") or []}
    if (props.get(DECISION) or {}).get("type") != "select" or not {REJECTED, PASSED, outcome} <= options or any((props.get(n) or {}).get("type") != "rich_text" for n in (HISTORY, DISMISSED)):
        raise fail("SCHEMA_MISSING")


def _scan(client, flt):
    """Every Ledger page matching a filter, read completely or not at all."""
    out, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        page = client.query_data_source(None, {"page_size": 100, "filter": flt, **({"start_cursor": cursor} if cursor else {})})
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


def applied_pages(client):
    """Every page with Applied ticked."""
    return _scan(client, {"property": "Applied", "checkbox": {"equals": True}})


def unapplied_matches(client, want):
    """Page ids of Ledger rows for this company AND role that are NOT marked Applied (a read through the Notion API filtered by the company's first word, then matched with platform.names). The Applied read proves
    only that no eligible application exists, never that no Ledger row does: a row that may be this application is surfaced, never ignored."""
    words = names.tokens(want["company"])
    if not words:
        raise fail("REQUEST_INVALID")
    rows = _scan(client, {"and": [{"property": "Company", "rich_text": {"contains": words[0]}}, {"property": "Applied", "checkbox": {"equals": False}}]})
    return sorted(_page_id(r.get("id")) for r in rows if M.same(_text(r["properties"].get("Company"), "rich_text"), _text(r["properties"].get("Job"), "title"), want["company"], want["role"]))


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


def route(applied, parents, want):
    """-> ("ledger", row) | ("notes", page) for ONE correlated pursuit, or a fixed code and nothing written. A Ledger application, when there is exactly one, carries the decision and its Notes page is never
    moved; a pursuit with no Ledger application is closed through its one Active Notes page; several Ledger rows, several Notes pages, or a Notes page that is both Active and Retired is a review, never a guess.
    Reopen acts only on the record that closed the pursuit: a Ledger-closed application is never reopened (or resurrected) through its Notes page."""
    same = lambda r: M.same(r["company"], r["role"], want["company"], want["role"])
    led, act, ret = [r for r in applied if same(r)], [p for p in parents if not p.get("retired") and same(p)], [p for p in parents if p.get("retired") and same(p)]
    if len(led) > 1:
        raise fail("AMBIGUOUS")
    if led and want["action"] != REOPEN:
        return "ledger", led[0]                                        # a confirm on the one Ledger application never moves a Notes page, so duplicate pages cannot make it ambiguous (Walmart, Oct 24)
    if len(act) > 1 or len(ret) > 1 or (act and ret):
        raise fail("AMBIGUOUS")
    page = ret or act                                                  # at most one: the page the pursuit lives on, wherever it is now (a retry after a move that landed must still reach move_page's own checks)
    if want["action"] == REOPEN:
        if led and (led[0].get("decision") in M.OUTCOMES + (PASSED,) or (want.get("id") and _page_id(led[0].get("id")) == want["id"])):
            return "ledger", led[0]                                    # an application is reopened on its own row (a finished retry included), never through its Notes page
        if page:
            return "notes", page[0]
        raise fail("NOT_CLOSED")
    if led:
        return "ledger", led[0]
    if page:
        return "notes", page[0]
    raise fail("NO_MATCH")


def listing(client, block_id):
    """Every child block of a page, read completely or not at all."""
    out, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        data = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        if not isinstance(data, dict) or not isinstance(data.get("results"), list) or not isinstance(data.get("has_more"), bool):
            raise fail("PAGES_INCOMPLETE")
        out += data["results"]
        cursor = data.get("next_cursor")
        if not data["has_more"]:
            return out
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise fail("PAGES_INCOMPLETE")
        seen.add(cursor)
    raise fail("PAGES_INCOMPLETE")


def _digest(block):
    kind = block.get("type")
    text = (block.get(kind) or {}).get("title") if kind == "child_page" else "".join(r.get("plain_text", "") for r in (block.get(kind) or {}).get("rich_text") or [])
    return block.get("id"), kind, text


def move_page(client, root_id, request, live, now, parents, known=None):
    """Notes-only: the ONE exact opportunity page moves into the existing Retired Opportunities container (Confirm Rejected), or back into the Active one (Reopen). Placement is the D189 closure signal and the only
    status: the compiler and renderer read the section a page is in, never any text on it. Two append-only RECORD callouts (annotations, read only here for idempotency, never as status) keep the confirmed reason and
    date: `Pending` (intent) before the move and the plain label after the move is verified. States, from what is on the page: no record = nothing written; Pending only = partially applied; Pending + plain label = complete.
    A different operation while any Pending is open is `DECISION_PENDING`; the same operation reconciles (only the missing steps run, no record twice). A move is attempted only while the page is still in the expected
    section (checked on both fetches against `parents()`, the page reader: column or container) and its read-back is checked before the plain label is written. Read-back covers the page's parent, title and its TOP-LEVEL
    blocks (ids, types, plain text) before and after; nested content is not compared (Notion's move carries the page with its content). `known`: the caller's own complete page read in this
    same request (the Close screen's), used for the first section check only; the check immediately before the write always reads `parents()` again (Jim, Oct 24: confirm too slow)."""
    want = parse(request)
    reopen = want["action"] == REOPEN
    label, dest_title = ("Reopened", ACTIVE_TITLE) if reopen else (want["outcome"], RETIRED_TITLE)
    found = {}
    for block in listing(client, root_id):
        if block.get("type") == "child_page" and (block.get("child_page") or {}).get("title") in (ACTIVE_TITLE, RETIRED_TITLE):
            found.setdefault(block["child_page"]["title"], []).append(block["id"])
    if len(found.get(dest_title, [])) != 1:
        raise fail("CONTAINER_MISSING" if not found.get(dest_title) else "CONTAINER_AMBIGUOUS")
    dest, retired = _page_id(found[dest_title][0]), _page_id((found.get(RETIRED_TITLE) or [""])[0])

    def fetch():
        page = client.call("GET", f"/pages/{want['id']}")
        company, role = names.split_title("".join(r.get("plain_text", "") for r in ((page.get("properties") or {}).get("title") or {}).get("title") or []))
        if page.get("archived") or page.get("in_trash") or _page_id(page.get("id")) != want["id"] or not M.same(company, role, want["company"], want["role"]):
            raise fail("STALE_IDENTITY")
        return page, [_digest(b) for b in listing(client, want["id"])]

    def parent(page):
        return _page_id((page.get("parent") or {}).get("page_id")) if (page.get("parent") or {}).get("type") == "page_id" else None

    def in_source(listed=None):
        here = [p for p in (parents() if listed is None else listed) if _page_id(p["id"]) == want["id"]]
        if len(here) != 1 or bool(here[0].get("retired")) != reopen:
            raise fail("STALE_SOURCE")                                                                  # moved out of the section it was routed from (a column under its heading, or its container)
    page, kids = fetch()
    marks, closed_as = {}, None
    for _, _, text in kids:
        m = RECORD.match(text or "")
        if m:
            marks.setdefault(m.group(4), set()).add(("pending" if m.group(1) else "done", m.group(2)))
            closed_as = m.group(2) if not m.group(1) and m.group(2) != "Reopened" else closed_as
    mine = marks.get(want["op"], set())
    if any(lab != label for _, lab in mine):
        raise fail("CONFLICT")
    if any(("pending", lab) in s and ("done", lab) not in s for op, s in marks.items() for lab in [l for _, l in s] if op != want["op"]):
        raise fail("PENDING")                                                                            # another operation is half applied: reconcile it (same operation id) before anything else
    if ("done", label) in mine:
        if parent(page) != dest:
            raise fail("CONFLICT")
        return {"status": "already_recorded", "verified": True, "route": "notes", "entries": len(marks)}
    if not mine and (parent(page) == dest or (reopen and parent(page) != retired)):
        raise fail("CONFLICT" if not reopen else "NOT_CLOSED")
    if reopen and closed_as == "Hired" and not want["hired_ok"]:
        raise fail("HIRED_REOPEN_UNCONFIRMED")                                                   # reopening a Hired outcome needs its own explicit confirmation
    if parent(page) != dest:
        in_source(known)
    if not live:
        return {"status": "dry_run", "verified": False, "route": "notes", "entries": len(marks)}
    again, kids_again = fetch()
    if again.get("last_edited_time") != page.get("last_edited_time") or kids_again != kids or parent(again) != parent(page):
        raise fail("CONCURRENT_CHANGE")
    if parent(again) != dest:
        in_source()                                                                                      # still in the expected section immediately before the write
    new = []

    def append(text):
        client.call_once("PATCH", f"/blocks/{want['id']}/children", {"children": [{"type": "callout", "callout": {"rich_text": rich_text(text), "icon": {"type": "emoji", "emoji": "📌"}, "color": "gray_background"}}]})
        new.append(("callout", text))
    tail = f"{now.date().isoformat()} · {want['reason']} · op {want['op']}"
    try:
        if not mine:
            append(f"Hiring decision (V7) · Pending {label} · {tail}")
        if parent(page) != dest:
            client.call_once("POST", f"/pages/{want['id']}/move", {"parent": {"type": "page_id", "page_id": dest}}, version=MOVE_VERSION)
    except NotionError:
        raise fail("WRITE_FAILED") from None
    try:
        moved, _ = fetch()
    except NotionError:
        raise fail("READBACK_FAILED") from None
    if parent(moved) != dest:
        raise fail("READBACK_MISMATCH")                                                                  # the move did not happen: no completion record is written, the page stays Pending
    try:
        append(f"Hiring decision (V7) · {label} · {tail}")
    except NotionError:
        raise fail("WRITE_FAILED") from None
    try:
        back, kids_back = fetch()
        landed = [m for m in listing(client, root_id) if m.get("type") == "child_page" and (m.get("child_page") or {}).get("title") == dest_title]
    except NotionError:
        raise fail("READBACK_FAILED") from None
    if parent(back) != dest or len(landed) != 1 or kids_back[:len(kids)] != kids or [(k, t) for _, k, t in kids_back[len(kids):]] != new:
        raise fail("READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "route": "notes", "entries": len(marks | {want["op"]: 1})}


def confirm_rejected(client, request, live, now, pages=None):
    """-> {"status": dry_run | recorded | already_recorded, "verified": bool, "entries": n} or a DecisionError. Never success from an HTTP response alone: the exact row is fetched again and compared.
    `pages`: the caller's own fresh Applied read in this same request, so the Ledger is not scanned twice; it only selects the row, which is then fetched and checked itself."""
    want = parse(request)
    reopen, was = want["action"] == REOPEN, None
    check_schema(client, want["outcome"])
    row = target(applied_pages(client) if pages is None else pages, want)
    page = client.get_page(row["id"])
    current(page, want, client.source)
    kept, decision, history = snapshot(page)
    log = entries(history)
    ops, after = [e["op"] for e in log], None if reopen else want["outcome"]                  # `after`: the decision this operation leaves on the row
    if want["op"] in ops:
        if decision == after:
            return {"status": "already_recorded", "verified": True, "entries": len(ops)}      # a retry of the same confirmed operation: nothing is written twice
        raise fail("CONFLICT")
    if reopen:                                                                                  # only the decision V7 itself recorded last is reopened; its history stays
        was = (log[-1].get("outcome") or REJECTED) if log and log[-1].get("action") == ACTION else None
        if decision is None or decision != was:
            raise fail("NOT_CLOSED")
        if was == "Hired" and not want["hired_ok"]:
            raise fail("HIRED_REOPEN_UNCONFIRMED")                                              # reopening a Hired outcome needs its own explicit confirmation
    elif decision is not None:
        raise fail("CONFLICT")                                                                  # another decision is already recorded: never replaced here
    line = json.dumps({"op": want["op"], "action": want["action"], "at": now.isoformat(timespec="seconds"), "reason": want["reason"], "outcome": was or want["outcome"]}, separators=(",", ":"), sort_keys=True)
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
        client.update_page_properties(row["id"], {DECISION: {"select": None if reopen else {"name": want["outcome"]}}, HISTORY: {"rich_text": rich_text(new)}})
    except NotionError:
        raise fail("WRITE_FAILED") from None
    try:
        back = client.get_page(row["id"])
    except NotionError:
        raise fail("READBACK_FAILED") from None
    if snapshot(back) != (kept, after, new):
        raise fail("READBACK_MISMATCH")
    return {"status": "recorded", "verified": True, "entries": len(ops) + 1}
