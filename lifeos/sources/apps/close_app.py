"""The private Close Opportunity service: shows one Job Ledger pursuit and closes it with one of six outcomes.

The application behind the Edge gate (lifeos/edge/close_gate.py calls `handle`). The pursuit is resolved by its Job
Ledger row's page id; success is reported only after the read-back, and the decision rules live in
lifeos.hiring.decision. GET and the review step never write. Mark Complete (/complete) records an opportunity
page's primary action as done in its own fold; Manage and Reopen live in manage_app. Outputs: fixed codes only.
"""
import hashlib
import importlib
import re
from html import escape as e
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.jobs import hiring_pipeline
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.notion_text import block_text as _plain
from lifeos.hiring import close_screen as S, decision as D
from lifeos.sources.apps.signed import HTML, handler, invalid, live, mac as _mac, norm, same as _same, stopper, unverified      # noqa: F401 (_same: manage_app reads it as C._same)

CLOSURE = ("lifeos/hiring/close_screen.py", "lifeos/hiring/decision.py", "lifeos/hiring/models.py", "lifeos/jobs/__init__.py", "lifeos/jobs/hiring_pipeline.py", "lifeos/platform/names.py",
           "lifeos/sources/apps/close_app.py", "lifeos/sources/apps/__init__.py", "lifeos/sources/apps/signed.py", "lifeos/sources/apps/outreach_app.py", "lifeos/recruiters/__init__.py", "lifeos/recruiters/outreach.py", "lifeos/recruiters/notion.py", "lifeos/recruiters/identity.py", 
           "lifeos/network/__init__.py", "lifeos/network/surface.py", "lifeos/network/match.py", "lifeos/platform/hidden.py", "lifeos/platform/ledger_body.py",
           "lifeos/sources/apps/resume_app.py", "lifeos/platform/notion_files.py", "lifeos/platform/gdrive.py", "lifeos/platform/google_consent.py",
           "lifeos/sources/apps/manage_app.py", "lifeos/sources/apps/alerts_app.py", "lifeos/platform/alerts.py", "lifeos/platform/alert_registry.py", "lifeos/platform/alert_settings.py", "lifeos/platform/alert_store.py", "lifeos/platform/redact.py",
           "lifeos/platform/credentials.py", "lifeos/platform/credential_list.py", "lifeos/platform/fixed_code.py", "lifeos/platform/notion_text.py", "lifeos/platform/notion_pages.py", "lifeos/platform/api_counts.py")
ROUTES = {"/sent": ("apps.outreach_app", "handle"), "/referral": ("apps.outreach_app", "handle_status"),
          "/resume": ("apps.resume_app", "handle"), "/manage": ("apps.manage_app", "handle"), "/manage.css": ("apps.manage_app", "handle_css"), "/manage-picker.js": ("apps.manage_app", "handle_js"), "/alerts": ("apps.alerts_app", "handle"), "/alerts.css": ("apps.alerts_app", "handle_css")}
KIND = "ledger"
TTL, TZ = 900, ZoneInfo("America/Chicago")


def _now():
    return datetime.now(TZ)


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.result(eyebrow, title, text, detail, css), ()


def _state(client, root, pages=None):
    """-> (Ledger pursuit rows in decision.route's shape, opportunity pages [{"id", "tree_id", "company", "role",
    "retired"}]); anything short of a complete read raises.
    """
    rows = D.rows(D.ledger_pages(client) if pages is None else pages)
    found, status = hiring_pipeline.snapshot(client, root, rounds=False, ledger=client)
    if status != "ok":
        raise D.fail("PAGES_UNREADABLE")
    return rows, [{"id": o.page_id, "tree_id": o.tree_id, "company": o.company, "role": o.role, "retired": o.section == hiring_pipeline.RETIRED} for o in found]


def _fingerprint(kind, pid, company, role, closed):
    return hashlib.sha256(norm(f"{kind}|{pid}|{company}|{role}|{closed}").encode()).hexdigest()[:16]


LEAD_ACTION = "Reply to the recruiter's message, or close this lead"
FOLD, OPP_MARKER, COMPLETE_PATH = "⚙️ LIFE OS", "v7-interview:1;owner=machine;kind=opportunity", "Daily Report / Active Opportunities / Mark Complete"
ACTION_LINE, DONE_LINE = re.compile(r"v7-action:1;key=([0-9a-f]{16});text=(.+)"), re.compile(r"v7-done:1;key=([0-9a-f]{16});on=(\d{4}-\d{2}-\d{2})")


def _lines(client, block_id):
    out = client.call("GET", f"/blocks/{block_id}/children?page_size=100")
    if not isinstance(out, dict) or out.get("has_more") or not isinstance(out.get("results"), list):
        raise D.fail("PAGE_UNREADABLE")
    return out["results"]


def action(client, root, pid, key):
    """The open opportunity page `pid` and its fold's primary action `key`, or a fixed refusal (not that page, not a
    LIFE OS page, or the action changed). -> {id, company, role, key, text, fold, done}
    """
    pages = [p for p in _state(client, root)[1] if pid in (D._page_id(p["id"]), D._page_id(p["tree_id"]))]
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


_complete_stop = stopper("A completion counts only after it is read back from the opportunity page.", COMPLETE_PATH, "Mark complete")


def _complete_fp(a):
    return hashlib.sha256(norm(f"{a['id']}|{a['key']}|{a['company']}|{a['role']}").encode()).hexdigest()[:16]


def _complete_get(req, who, secrets):
    pid, key = D._page_id(req["query"].get("o")), (req["query"].get("a") or "")
    if not pid or not ACTION_LINE.fullmatch(f"v7-action:1;key={key};text=x"):
        return _complete_stop(400, "Invalid link", "No action selected", "Open this screen from an opportunity page's Mark Complete link.", "Nothing was changed.")
    a = action(secrets["reader"], secrets["config"]["pipeline"], pid, key)
    if a["done"]:
        return _complete_stop(200, "Already done", "Already marked complete", f"Marked complete on {a['done']}.", "Nothing was changed.", "")
    exp, st = str(int(_now().timestamp()) + TTL), _complete_fp(a)
    fields = {"o": pid, "a": key, "exp": exp, "st": st, "t": _mac(secrets["link"], "m", who["login"], pid, key, st, exp)}
    lead = a["text"].startswith(LEAD_ACTION)
    ask, cta = ("What happened with this lead?", "I replied \u2014 mark it done") if lead else (f'Mark \u201c{e(a["text"])}\u201d complete?', "Mark complete")
    close = ""
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
    pid, key, step, st = D._page_id(form.get("o")), form.get("a", ""), form.get("step"), form.get("st", "")
    if not live(form, secrets["link"], "m", login, pid, st, _now(), ("cancel", "confirm"), (key,)):
        return invalid(_complete_stop, "Reload it from the opportunity page.")
    if step == "cancel":
        return _complete_stop(200, "Cancelled", "No changes made", "Nothing was recorded.", "Nothing was written.", "")
    client = Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": secrets["config"]["ledger"]})
    a = action(client, secrets["config"]["pipeline"], pid, key)
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
        return unverified(_complete_stop, "Could not verify the completion", code=str(error)[:60])
    if back["done"] != day:
        return unverified(_complete_stop, "Could not verify the completion")
    return _complete_stop(200, "Recorded", "Marked complete", "Recorded on the opportunity page and read back. The page shows it as done after the next hourly run.", f"{head} · Done {day}.", "ok")


handle_complete = handler(_complete_get, _complete_post, _complete_stop, "This action cannot be marked complete here")


def handle(req, who, secrets):
    try:
        if req["method"] == "GET" and req["path"] == "/close.css":
            return 200, "text/css; charset=utf-8", S.CSS.encode(), ()
        if req["method"] == "GET" and req["path"] == "/close.js":
            return 200, "text/javascript; charset=utf-8", S.JS.encode(), ()
        if req["path"] in ROUTES and req["method"] in ("GET", "POST"):
            module, name = ROUTES[req["path"]]
            return getattr(importlib.import_module("lifeos.sources." + module), name)(req, who, secrets)
        if req["path"] == "/complete" and req["method"] in ("GET", "POST"):
            return handle_complete(req, who, secrets)
        return _stop(404, "Not found", "Nothing here", "There is no screen at this address.", "Nothing was changed.")
    except NotionError as error:
        return _stop(409, "Not changed", "This opportunity cannot be closed here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")
