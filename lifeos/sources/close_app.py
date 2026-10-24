"""LIFE-47 S2 (Career): the application behind the private Close Opportunity service (the Edge gate in lifeos/edge/close_gate.py calls `handle`); a source adapter (D17): the one place that composes the Ledger, the Notes pages and the S1 decision rules. One exact opportunity is resolved by its canonical page id, routed
by the S1 decision rules, shown, reviewed and closed with one of the six outcomes; success is reported only after the S1 read-back. Nothing here is a second rule engine: identity, ambiguity, schema, idempotency,
conflict and read-back all belong to lifeos.hiring.decision. GET and the review step never write. Mark Complete (/complete) records one opportunity page's primary action as done in that page's own fold. Reopen and Manage are not reachable. Fixed codes only."""
import hashlib
import hmac
import re
from html import escape as e
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.jobs import hiring_pipeline
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.hiring import close_screen as S, decision as D, models as M

CLOSURE = ("lifeos/hiring/close_screen.py", "lifeos/hiring/decision.py", "lifeos/hiring/models.py", "lifeos/jobs/__init__.py", "lifeos/jobs/hiring_pipeline.py", "lifeos/platform/names.py",
           "lifeos/sources/outreach_app.py", "lifeos/recruiters/__init__.py", "lifeos/recruiters/outreach.py", "lifeos/recruiters/notion.py", "lifeos/recruiters/identity.py",
           "lifeos/network/__init__.py", "lifeos/network/surface.py", "lifeos/network/match.py",
           "lifeos/sources/resume_app.py", "lifeos/platform/notion_files.py")
TTL, TZ, HTML = 900, ZoneInfo("America/Chicago"), "text/html; charset=utf-8"


def _now():
    return datetime.now(TZ)


def _mac(secret, *parts):
    return hmac.new(secret, "\x1f".join(map(str, parts)).encode(), hashlib.sha256).hexdigest()


def _same(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.result(eyebrow, title, text, detail, css), ()


def _state(client, root, pid=None, pages=None):
    """-> (Applied Ledger rows, Notes pages) in the shapes decision.route reads; anything short of a complete read raises. When `pid` is one of the Applied rows the Notes pages are
    not walked: decision.route closes the one Ledger application without reading them (#497), so the walk would only cost paced calls (Jim, Oct 24: confirm too slow)."""
    rows = [{"company": D._text(p["properties"].get("Company"), "rich_text"), "role": D._text(p["properties"].get("Job"), "title"), "id": p.get("id"),
             "decision": ((p["properties"].get(D.DECISION) or {}).get("select") or {}).get("name")} for p in (D.applied_pages(client) if pages is None else pages)]
    rows = [r for r in rows if r["company"] and r["role"]]
    if pid and any(D._page_id(r["id"]) == pid for r in rows):
        return rows, []
    found, status = hiring_pipeline.snapshot(client, root, rounds=False)            # decision.route never reads interview counts: skip one paced call per opportunity
    if status != "ok":
        raise D.fail("PAGES_UNREADABLE")
    return rows, [{"id": o.page_id, "company": o.company, "role": o.role, "retired": o.section == hiring_pipeline.RETIRED} for o in found]


def resolve(client, config, pid):
    """The ONE canonical record with this exact page id, routed by decision.route to that same record, or a fixed refusal: absent, duplicated, ambiguous, or closing on another record (a Notes page whose pursuit
    has a Ledger application). -> {kind, id, company, role, closed, st}; `st` fingerprints what the screen was shown."""
    pages = D.applied_pages(client)                                     # read once: the confirm's write selects its row from this same fresh read
    rows, parents = _state(client, config["pipeline"], pid, pages)
    mine = [("ledger", r) for r in rows if D._page_id(r["id"]) == pid] + [("notes", p) for p in parents if D._page_id(p["id"]) == pid]
    if len(mine) != 1:
        raise D.fail("NOT_FOUND" if not mine else "AMBIGUOUS")
    kind, rec = mine[0]
    want = {"action": D.ACTION, "id": pid, "company": rec["company"], "role": rec["role"]}
    routed, target = D.route(rows, parents, want)
    if routed != kind or D._page_id(target["id"]) != pid:
        raise D.fail("STALE_IDENTITY")
    closed = bool(target.get("decision")) if kind == "ledger" else bool(target["retired"])
    if kind == "notes" and not closed and D.unapplied_matches(client, want):
        raise D.fail("LEDGER_UNAPPLIED")                                 # a Ledger row for this role that is not marked Applied may be this application: resolve it first
    return {"kind": kind, "id": pid, "company": rec["company"], "role": rec["role"], "closed": closed, "st": _fingerprint(kind, pid, rec["company"], rec["role"], closed), "pages": pages}


def _fingerprint(kind, pid, company, role, closed):
    return hashlib.sha256(f"{kind}|{pid}|{company}|{role}|{closed}".encode()).hexdigest()[:16]


def _fields(secret, login, opp, exp):
    return {"o": opp["id"], "k": opp["kind"], "co": opp["company"], "ro": opp["role"], "exp": str(exp), "st": opp["st"], "t": _mac(secret, "t", login, opp["id"], opp["st"], exp)}


def _shown(form, pid, st):
    """The open record exactly as the GET showed it, proven by the signed fingerprint `st` and read from no source: Edit and Review re-render without a Notion
    round trip (they never write). None when the carried fields are not the ones `st` was computed from. Confirm always resolves afresh."""
    kind, company, role = form.get("k", ""), form.get("co", ""), form.get("ro", "")
    return {"kind": kind, "id": pid, "company": company, "role": role, "closed": False, "st": st} if kind in ("ledger", "notes") and _same(_fingerprint(kind, pid, company, role, False), st) else None


def _get(req, who, secrets):
    pid = D._page_id(req["query"].get("o"))
    if not pid:
        return _stop(400, "Invalid link", "No opportunity selected", "Open this screen from an opportunity link.", "Nothing was changed.")
    opp = resolve(secrets["reader"], secrets["config"], pid)
    if opp["closed"]:
        return _stop(409, "Already decided", "This opportunity is already closed", "A decision is already recorded for it.", "Nothing was changed.")
    return 200, HTML, S.form(opp, _fields(secrets["link"], who["login"], opp, int(_now().timestamp()) + TTL)), ()


def _post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    pid, step, exp, st = D._page_id(form.get("o")), form.get("step"), form.get("exp", ""), form.get("st", "")
    outcome, notes = form.get("outcome", ""), (form.get("notes") or "").strip()
    if not pid or step not in ("cancel", "edit", "review", "confirm") or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not _same(form.get("t"), _mac(secret, "t", login, pid, st, exp)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the opportunity from its link.", "Nothing was changed.", "danger")
    if step == "cancel":
        return _stop(200, "Cancelled", "No changes made", "The proposed closure was cancelled. The opportunity remains in its current state.", "Nothing was written.", "")
    config = secrets["config"]
    if step == "confirm":
        client = Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]})
        opp = resolve(client, config, pid)                                       # the write is authorized only by a fresh read of the canonical record
        if not opp["closed"] and opp["st"] != st:
            return _stop(409, "Conflict", "Opportunity changed", "The record changed since this form was opened.", "No write attempted. Reload the opportunity and review it again.")
    else:
        opp = _shown(form, pid, st)
        if opp is None:
            return _stop(403, "Not accepted", "This form is no longer valid", "Reload the opportunity from its link.", "Nothing was changed.", "danger")
    fields = {"o": pid, "k": opp["kind"], "co": opp["company"], "ro": opp["role"], "exp": exp, "st": st, "t": form["t"]}     # the token was verified above; the review step adds its own
    if step == "edit":
        return 200, HTML, S.form(opp, fields, outcome, notes), ()
    if opp["kind"] == "notes" and outcome in M.LEAD_REASONS:         # E01b: "I'm passing", "Not a real job", "Went nowhere" are recorded as an existing outcome plus a fixed note
        outcome, fixed = M.LEAD_REASONS[outcome]
        notes = (fixed + (": " + notes if notes else ""))[:D.REASON_MAX]
    if outcome not in M.OUTCOMES or len(notes) > D.REASON_MAX or not (notes or outcome != "Other") or not (notes or "x").isprintable():
        return 400, HTML, S.form(opp, fields, outcome if outcome in M.OUTCOMES else "", notes[:D.REASON_MAX], "Choose a closure reason. Other needs a short note (up to 200 characters)."), ()
    sign = _mac(secret, "c", login, pid, st, exp, outcome, notes)
    if step == "review":
        return 200, HTML, S.review(opp, {**fields, "outcome": outcome, "notes": notes, "c": sign}, outcome, notes), ()
    if not _same(form.get("c"), sign):
        return _stop(403, "Not accepted", "This confirmation is not valid", "Review the closure again.", "Nothing was changed.", "danger")
    request = {"action": D.ACTION, "confirmed": True, "id": pid, "company": opp["company"], "role": opp["role"], "reason": notes or outcome, "outcome": outcome,
               "op": _mac(secret, "op", pid, st, exp, outcome, notes)[:32]}
    try:
        done = (D.confirm_rejected(client, request, True, _now(), opp["pages"]) if opp["kind"] == "ledger"
                else D.move_page(client, config["pipeline"], request, True, _now(), lambda: _state(client, config["pipeline"])[1]))
    except NotionError as error:
        code = str(error)[:60]
        if code in ("DECISION_CONFLICT", "DECISION_PENDING", "DECISION_SCHEMA_MISSING"):
            return _stop(409, "Not changed", "This closure was not recorded", "A conflicting decision exists, or this outcome is not enabled on the Ledger yet.", f"Refused: {code}. Nothing was changed.")
        return _stop(500, "Unavailable", "Could not verify closure", "Do not assume the opportunity was closed or left unchanged.", f"Status: DEGRADED · {code}", "danger")
    if done.get("verified") is not True or done.get("status") not in ("recorded", "already_recorded"):
        return _stop(500, "Unavailable", "Could not verify closure", "Do not assume the opportunity was closed or left unchanged.", "Status: DEGRADED · not verified", "danger")
    again = " (already recorded)" if done["status"] == "already_recorded" else ""
    return _stop(200, "Closed", "Opportunity closed", "The canonical record was updated and read back successfully.", f"{opp['company']} — {opp['role']} · Closed — {outcome}{again}.", "ok")


# ---------- E05 Mark Complete (Jim, Oct 24): one opportunity page's one primary action, recorded in that page's own fold after two steps, read back ----------
LEAD_ACTION = "Reply to the recruiter's message, or close this lead"
FOLD, OPP_MARKER, COMPLETE_PATH = "⚙️ LIFE OS", "v7-interview:1;owner=machine;kind=opportunity", "Daily Report / Active Opportunities / Mark Complete"
ACTION_LINE, DONE_LINE = re.compile(r"v7-action:1;key=([0-9a-f]{16});text=(.+)"), re.compile(r"v7-done:1;key=([0-9a-f]{16});on=(\d{4}-\d{2}-\d{2})")   # lifeos.interview.template's formats (contract-tested)


def _plain(block):
    body = block.get(block.get("type")) or {}
    return "".join(p.get("plain_text") or (p.get("text") or {}).get("content", "") for p in body.get("rich_text") or [])


def _lines(client, block_id):
    out = client.call("GET", f"/blocks/{block_id}/children?page_size=100")
    if not isinstance(out, dict) or out.get("has_more") or not isinstance(out.get("results"), list):
        raise D.fail("PAGE_UNREADABLE")
    return out["results"]


def action(client, root, pid, key):
    """The open opportunity page `pid` (exactly one active Notes page in the pipeline) and its fold's one primary action whose key is `key`, or a fixed refusal:
    not that page, not a LIFE OS page, or the action changed since the link was written. -> {id, company, role, key, text, fold, done}"""
    pages = [p for p in _state(client, root)[1] if D._page_id(p["id"]) == pid]
    if len(pages) != 1:
        raise D.fail("NOT_FOUND" if not pages else "AMBIGUOUS")
    if pages[0]["retired"]:
        raise D.fail("RETIRED")
    tops = _lines(client, pid)
    if not tops or tops[0].get("type") != "toggle" or _plain(tops[0]) != FOLD:
        raise D.fail("NOT_A_LIFE_OS_PAGE")
    lines = [_plain(b) for b in _lines(client, tops[0]["id"]) if b.get("type") == "paragraph"]
    if not lines or lines[0] != OPP_MARKER:
        raise D.fail("NOT_A_LIFE_OS_PAGE")
    found = [m.group(2) for m in map(ACTION_LINE.fullmatch, lines) if m and m.group(1) == key]
    if len(found) != 1:
        raise D.fail("ACTION_CHANGED")
    done = [m.group(2) for m in map(DONE_LINE.fullmatch, lines) if m and m.group(1) == key]
    return {"id": pid, "company": pages[0]["company"], "role": pages[0]["role"], "key": key, "text": found[0], "fold": tops[0]["id"], "done": done[0] if done else None}


def _complete_stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.page(title, "A completion counts only after it is read back from the opportunity page.",
                                f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                f'<div class="notice {css}">{e(detail)}</div></section>', COMPLETE_PATH, "Mark complete"), ()


def _complete_fp(a):
    return hashlib.sha256(f"{a['id']}|{a['key']}|{a['company']}|{a['role']}".encode()).hexdigest()[:16]


def _complete_get(req, who, secrets):
    pid, key = D._page_id(req["query"].get("o")), (req["query"].get("a") or "")
    if not pid or not ACTION_LINE.fullmatch(f"v7-action:1;key={key};text=x"):
        return _complete_stop(400, "Invalid link", "No action selected", "Open this screen from an opportunity page's Mark Complete link.", "Nothing was changed.")
    a = action(secrets["reader"], secrets["config"]["pipeline"], pid, key)
    if a["done"]:
        return _complete_stop(200, "Already done", "Already marked complete", f"Marked complete on {a['done']}.", "Nothing was changed.", "")
    exp, st = str(int(_now().timestamp()) + TTL), _complete_fp(a)
    fields = {"o": pid, "a": key, "exp": exp, "st": st, "t": _mac(secrets["link"], "m", who["login"], pid, key, st, exp)}
    lead = a["text"].startswith(LEAD_ACTION)                                     # "Reply to the recruiter's message, or close this lead.": both outcomes are offered (Jim, Oct 24)
    ask, cta = ("What happened with this lead?", "I replied \u2014 mark it done") if lead else (f'Mark \u201c{e(a["text"])}\u201d complete?', "Mark complete")
    close = f'<a class="btn" href="/?o={pid}">Close this lead instead \u2192</a>' if lead else ""
    return 200, HTML, S.page("Mark complete", "Your confirmation is required before anything is recorded.", (
        f'<form method="post" action="/complete">{S._hidden(fields)}<section class="section"><div class="eyebrow">Opportunity</div>'
        f'<h2>{e(a["company"])} — {e(a["role"])}</h2></section><section class="section"><div class="eyebrow">{e(a["text"])}</div>'
        f'<h3 class="step-title">{ask}</h3>'
        '<div class="notice">' + ('Replied: records this action as done on the opportunity page and reads it back; the page shows it after the next hourly run. Close: opens the Close screen, where you choose why the lead is closed. '
                                  if lead else 'Confirming records this one action as done on the opportunity page and reads it back. The page shows it as done after the next hourly run. ')
        + 'Nothing else changes and no one is contacted.</div></section>'
        f'<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button>{close}<button class="btn primary" name="step" value="confirm">{cta}</button></div></form>'),
        COMPLETE_PATH, "Mark complete"), ()


def _complete_post(req, who, secrets):
    form, login = req["form"], who["login"]
    pid, key, step, exp, st = D._page_id(form.get("o")), form.get("a", ""), form.get("step"), form.get("exp", ""), form.get("st", "")
    if not pid or step not in ("cancel", "confirm") or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not _same(form.get("t"), _mac(secrets["link"], "m", login, pid, key, st, exp)):
        return _complete_stop(403, "Not accepted", "This form is no longer valid", "Reload it from the opportunity page.", "Nothing was changed.", "danger")
    if step == "cancel":
        return _complete_stop(200, "Cancelled", "No changes made", "Nothing was recorded.", "Nothing was written.", "")
    client = Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": secrets["config"]["ledger"]})
    a = action(client, secrets["config"]["pipeline"], pid, key)                 # the write is authorized only by a fresh read of the page and its current action
    if _complete_fp(a) != st:
        return _complete_stop(409, "Conflict", "Opportunity changed", "The opportunity changed since this screen was opened.", "No write attempted. Reload it from the page.")
    head = f"{a['company']} — {a['role']} · {a['text']}"
    if a["done"]:
        return _complete_stop(200, "Already recorded", "Already marked complete", f"Marked complete on {a['done']}; nothing was changed.", head, "")
    day = _now().date().isoformat()
    try:
        client.call_once("PATCH", f"/blocks/{a['fold']}/children", {"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(f"v7-done:1;key={key};on={day}")}}]})
        back = action(client, secrets["config"]["pipeline"], pid, key)
    except NotionError as error:
        return _complete_stop(500, "Unavailable", "Could not verify the completion", "Do not assume it was recorded or left unchanged.", f"Status: DEGRADED · {str(error)[:60]}", "danger")
    if back["done"] != day:
        return _complete_stop(500, "Unavailable", "Could not verify the completion", "Do not assume it was recorded or left unchanged.", "Status: DEGRADED · not verified", "danger")
    return _complete_stop(200, "Recorded", "Marked complete", "Recorded on the opportunity page and read back. The page shows it as done after the next hourly run.", f"{head} · Done {day}.", "ok")


def handle_complete(req, who, secrets):
    try:
        return (_complete_get if req["method"] == "GET" else _complete_post)(req, who, secrets)
    except NotionError as error:
        return _complete_stop(409, "Not changed", "This action cannot be marked complete here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")


def handle(req, who, secrets):
    """The Edge gate's application: (status, content type, body bytes, extra headers). Anything refused before a write says so and writes nothing."""
    try:
        if req["method"] == "GET" and req["path"] == "/close.css":
            return 200, "text/css; charset=utf-8", S.CSS.encode(), ()
        if req["method"] == "GET" and req["path"] == "/close.js":                             # the buttons' busy spinner; nothing depends on it
            return 200, "text/javascript; charset=utf-8", S.JS.encode(), ()
        if req["path"] == "/sent" and req["method"] in ("GET", "POST"):                       # D221: Mark as Sent, same gate and signing key
            from . import outreach_app
            return outreach_app.handle(req, who, secrets)
        if req["path"] == "/referral" and req["method"] in ("GET", "POST"):                   # reply / referral status on a recorded conversation, same gate and signing key
            from . import outreach_app
            return outreach_app.handle_status(req, who, secrets)
        if req["path"] == "/recruiter" and req["method"] in ("GET", "POST"):                  # LIFE-20: close one open recruiter row (Completed / Dismissed), same gate and signing key
            from . import outreach_app
            return outreach_app.handle_close(req, who, secrets)
        if req["path"] == "/complete" and req["method"] in ("GET", "POST"):                   # E05: Mark Complete, same gate, signing key and fresh read
            return handle_complete(req, who, secrets)
        if req["path"] == "/resume" and req["method"] in ("GET", "POST"):                     # D234: Build resume, same gate, signing key and fresh read
            from . import resume_app
            return resume_app.handle(req, who, secrets)
        if req["path"] != "/" or req["method"] not in ("GET", "POST"):
            return _stop(404, "Not found", "Nothing here", "This service only closes one opportunity at a time.", "Nothing was changed.")
        return (_get if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        return _stop(409, "Not changed", "This opportunity cannot be closed here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")
