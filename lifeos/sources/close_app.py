"""LIFE-47 S2 (Career): the application behind the private Close Opportunity service (the Edge gate in lifeos/edge/close_gate.py calls `handle`); a source adapter (D17): the one place that composes the Ledger, the Notes pages and the S1 decision rules. One exact opportunity is resolved by its canonical page id, routed
by the S1 decision rules, shown, reviewed and closed with one of the six outcomes; success is reported only after the S1 read-back. Nothing here is a second rule engine: identity, ambiguity, schema, idempotency,
conflict and read-back all belong to lifeos.hiring.decision. GET and the review step never write. Reopen and Manage are not reachable. Fixed codes only."""
import hashlib
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.jobs import hiring_pipeline
from lifeos.platform.notion_client import Client, NotionError
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


def _state(client, root):
    """-> (Applied Ledger rows, Notes pages) in the shapes decision.route reads; anything short of a complete read raises."""
    rows = [{"company": D._text(p["properties"].get("Company"), "rich_text"), "role": D._text(p["properties"].get("Job"), "title"), "id": p.get("id"),
             "decision": ((p["properties"].get(D.DECISION) or {}).get("select") or {}).get("name")} for p in D.applied_pages(client)]
    found, status = hiring_pipeline.snapshot(client, root, rounds=False)            # decision.route never reads interview counts: skip one paced call per opportunity
    if status != "ok":
        raise D.fail("PAGES_UNREADABLE")
    return [r for r in rows if r["company"] and r["role"]], [{"id": o.page_id, "company": o.company, "role": o.role, "retired": o.section == hiring_pipeline.RETIRED} for o in found]


def resolve(client, config, pid):
    """The ONE canonical record with this exact page id, routed by decision.route to that same record, or a fixed refusal: absent, duplicated, ambiguous, or closing on another record (a Notes page whose pursuit
    has a Ledger application). -> {kind, id, company, role, closed, st}; `st` fingerprints what the screen was shown."""
    rows, parents = _state(client, config["pipeline"])
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
    return {"kind": kind, "id": pid, "company": rec["company"], "role": rec["role"], "closed": closed, "st": _fingerprint(kind, pid, rec["company"], rec["role"], closed)}


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
        done = (D.confirm_rejected(client, request, True, _now()) if opp["kind"] == "ledger"
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


def handle(req, who, secrets):
    """The Edge gate's application: (status, content type, body bytes, extra headers). Anything refused before a write says so and writes nothing."""
    try:
        if req["method"] == "GET" and req["path"] == "/close.css":
            return 200, "text/css; charset=utf-8", S.CSS.encode(), ()
        if req["path"] == "/sent" and req["method"] in ("GET", "POST"):                       # D221: Mark as Sent, same gate and signing key
            from . import outreach_app
            return outreach_app.handle(req, who, secrets)
        if req["path"] == "/resume" and req["method"] in ("GET", "POST"):                     # D234: Build resume, same gate, signing key and fresh read
            from . import resume_app
            return resume_app.handle(req, who, secrets)
        if req["path"] != "/" or req["method"] not in ("GET", "POST"):
            return _stop(404, "Not found", "Nothing here", "This service only closes one opportunity at a time.", "Nothing was changed.")
        return (_get if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        return _stop(409, "Not changed", "This opportunity cannot be closed here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")
