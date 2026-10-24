"""LIFE-88 E14 (Jim, Oct 24 2026; 12a + 12b): Manage on the private Close service, for ONE interview opportunity page.
12a: GET /manage?o=<opportunity page id> is a read-only hub. It finds the record that carries this pursuit's decision (decision.route: its one Ledger application,
or the Notes page itself), says whether it is open or closed, and links the actions that already exist, each of which keeps its own signed review and confirm:
Close, Mark Complete, Build résumé / I applied with this résumé, Mark as Sent. GET never writes.
12b: Reopen a closed pursuit, from the same hub, through a signed review and confirm: a fresh read routes the REOPEN to the record that closed it, and
decision.confirm_rejected (Ledger) or decision.move_page (Notes page back to Active Opportunities) writes and reads it back. Reopening a Hired outcome needs its
own tick. Fixed codes only."""
from html import escape as e
from urllib.parse import urlencode

from lifeos.platform.notion_client import Client, NotionError
from lifeos.hiring import close_screen as S, decision as D
from . import close_app as C

PATH, TAB = "Daily Report / Active Opportunities / Manage", "Manage opportunity"
REASON = "Reopened on the Close screen"


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, C.HTML, S.page(title, "One interview opportunity: its actions, each confirmed on its own screen.",
                                  f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                  f'<div class="notice {css}">{e(detail)}</div></section>', PATH, TAB), ()


def _record(rows, parents, page):
    """-> (kind, record, closed) for the pursuit on this opportunity page, routed exactly as Close would route it."""
    kind, rec = D.route(rows, parents, {"action": D.ACTION, "id": D._page_id(page["id"]), "company": page["company"], "role": page["role"]})
    return kind, rec, bool(rec.get("decision")) if kind == "ledger" else bool(rec.get("retired"))


def _open_action(client, pid):
    """The page's one completable primary action that is not done yet -> its key, or None."""
    tops = C._lines(client, pid)
    if not tops or tops[0].get("type") != "toggle" or C._plain(tops[0]) != C.FOLD:
        return None
    lines = [C._plain(b) for b in C._lines(client, tops[0]["id"]) if b.get("type") == "paragraph"]
    done = {m.group(1) for m in map(C.DONE_LINE.fullmatch, lines) if m}
    keys = [m.group(1) for m in map(C.ACTION_LINE.fullmatch, lines) if m and m.group(1) not in done]
    return keys[0] if len(keys) == 1 else None


def _link(label, href, note):
    return f'<div class="actions"><a class="btn" href="{e(href)}">{e(label)} →</a><span class="lead">{e(note)}</span></div>'


def _fields(secret, login, kind, rec, page, exp):
    st = C._fingerprint(kind, D._page_id(rec["id"]), page["company"], page["role"], True)
    o = D._page_id(rec["id"])
    return {"o": o, "k": kind, "co": page["company"], "ro": page["role"], "exp": str(exp), "st": st, "t": C._mac(secret, "manage-reopen", login, o, st, exp)}


def _hub(req, who, secrets):
    pid = D._page_id(req["query"].get("o"))
    if not pid:
        return _stop(400, "Invalid link", "No opportunity selected", "Open Manage from an interview opportunity page.", "Nothing was changed.")
    reader, config = secrets["reader"], secrets["config"]
    rows, parents = C._state(reader, config["pipeline"])
    mine = [p for p in parents if D._page_id(p["id"]) == pid]
    if len(mine) != 1:
        return _stop(404, "Not found", "This opportunity page is not in the Hiring Pipeline", "Open Manage from the opportunity page itself.", "Nothing was changed.")
    page = mine[0]
    kind, rec, closed = _record(rows, parents, page)
    rid = D._page_id(rec["id"])
    body = ('<section class="section"><div class="eyebrow">Exact opportunity</div><div class="jobhead"><div>'
            f'<h2>{e(page["role"])}</h2><div class="company">{e(page["company"])}</div></div>'
            f'<span class="status">{"Closed" if closed else "Open"} · {"Job Ledger" if kind == "ledger" else "Notes page"}</span></div></section><section class="section">'
            '<div class="eyebrow">Actions</div><p class="lead">Each action opens its own screen and changes nothing until you confirm there.</p>')
    if closed:
        try:                                                               # offered only when the POST's REOPEN routing would reach this same record
            routed, again = D.route(rows, parents, {"action": D.REOPEN, "id": rid, "company": page["company"], "role": page["role"]})
            why = None if (routed, D._page_id(again["id"])) == (kind, rid) else "DECISION_STALE_IDENTITY"
        except D.DecisionError as error:
            why = str(error)[:60]
        if why:
            body += f'<div class="notice alert">Reopen is not available here: {e(why)}. Nothing was changed.</div>'
        else:
            fields = _fields(secrets["link"], who["login"], kind, rec, page, int(C._now().timestamp()) + C.TTL)
            hired = rec.get("decision") == "Hired" or kind == "notes"          # a Notes page's outcome lives in its own record lines; the decision rules check it
            body += (f'<form method="post" action="/manage">{S._hidden(fields)}<div class="actions"><button class="btn primary" name="step" value="review">Reopen this opportunity</button>'
                     '<span class="lead">Puts it back among the open opportunities; its history is kept.</span></div>'
                     + ('<label class="lead"><input type="checkbox" name="h" value="1"> If it was recorded as Hired: reopen it anyway</label>' if hired else "") + '</form>')
    else:
        body += _link("Close this opportunity", "/?" + urlencode({"o": rid}), "Record why it ended (rejected, withdrew, filled, ...).")
        key = _open_action(reader, pid)
        if key:
            body += _link("Mark the next action complete", "/complete?" + urlencode({"o": pid, "a": key}), "The page's current primary action.")
    if kind == "ledger":
        body += _link("Build résumé · I applied with this résumé", "/resume?" + urlencode({"j": rid}), "Tailor a résumé, or record which one you sent.")
        if not closed:
            body += _link("Mark outreach as sent", "/sent?" + urlencode({"j": rid}), "Record a message you sent to a contact for this job.")
    body += (f'</section><div class="actions"><a class="btn link" href="https://www.notion.so/{pid}" target="_blank" rel="noopener">Open the opportunity page in Notion →</a></div>')
    return 200, C.HTML, S.page("Manage opportunity", "One interview opportunity: its actions, each confirmed on its own screen.", body, PATH, TAB), ()


def _post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    o, step, exp, st, kind = D._page_id(form.get("o")), form.get("step"), form.get("exp", ""), form.get("st", ""), form.get("k", "")
    company, role = form.get("co", ""), form.get("ro", "")
    if (not o or step not in ("cancel", "review", "confirm") or kind not in ("ledger", "notes") or not (exp.isascii() and exp.isdigit()) or int(exp) < C._now().timestamp()
            or not C._same(form.get("t"), C._mac(secret, "manage-reopen", login, o, st, exp)) or not C._same(C._fingerprint(kind, o, company, role, True), st)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload Manage from the opportunity page.", "Nothing was changed.", "danger")
    if step == "cancel":
        return _stop(200, "Cancelled", "Nothing reopened", "The opportunity stays closed.", "Nothing was written.", "")
    hired = form.get("h") == "1"
    if step == "review":
        fields = {k: form.get(k, "") for k in ("o", "k", "co", "ro", "exp", "st", "t")} | ({"h": "1"} if hired else {})
        return 200, C.HTML, S.page("Reopen opportunity", "Puts a closed opportunity back among the open ones.", (
            f'<section class="section"><div class="eyebrow">Review</div><p class="step-title">{e(role)}</p><p class="lead">{e(company)}</p>'
            '<p class="lead">Its recorded decision is cleared (the history keeps the close and adds this reopen). Nothing else changes and nothing is sent.</p></section>'
            f'<form method="post" action="/manage">{S._hidden(fields)}<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button>'
            '<button class="btn primary" name="step" value="confirm">Reopen it</button></div></form>'), PATH, TAB), ()
    config = secrets["config"]
    client = Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]})
    pages = D.applied_pages(client)                                     # the write is authorized only by a fresh read
    rows, parents = C._state(client, config["pipeline"], None, pages)
    routed, target = D.route(rows, parents, {"action": D.REOPEN, "id": o, "company": company, "role": role})
    if routed != kind or D._page_id(target["id"]) != o:
        return _stop(409, "Conflict", "Opportunity changed", "The record that closed it is not the one this form names.", "Nothing was changed. Reload Manage and try again.")
    request = {"action": D.REOPEN, "confirmed": True, "id": o, "company": company, "role": role, "reason": REASON, "op": C._mac(secret, "op-r", o, st, exp)[:32], "reopen_hired": hired}
    try:
        done = (D.confirm_rejected(client, request, True, C._now(), pages) if kind == "ledger"
                else D.move_page(client, config["pipeline"], request, True, C._now(), lambda: C._state(client, config["pipeline"], pages=[])[1], parents))
    except NotionError as error:
        code = str(error)[:60]
        if code == "DECISION_HIRED_REOPEN_UNCONFIRMED":
            return _stop(409, "Not changed", "It was recorded as Hired", "Tick “If it was recorded as Hired: reopen it anyway” on Manage to reopen it.", f"Refused: {code}. Nothing was changed.")
        if code in ("DECISION_NOT_CLOSED", "DECISION_CONFLICT", "DECISION_PENDING"):
            return _stop(409, "Not changed", "This opportunity was not reopened", "It is already open, or its decision changed since Manage was opened.", f"Refused: {code}. Nothing was changed.")
        return _stop(500, "Unavailable", "Could not verify the reopen", "Do not assume it was reopened or left unchanged.", f"Status: DEGRADED · {code}", "danger")
    if done.get("verified") is not True or done.get("status") not in ("recorded", "already_recorded"):
        return _stop(500, "Unavailable", "Could not verify the reopen", "Do not assume it was reopened or left unchanged.", "Status: DEGRADED · not verified", "danger")
    secrets.get("after", []).append("hiring")                          # the Hiring Pipeline region is redrawn now (close_gate.REFRESH)
    return _stop(200, "Reopened", "Opportunity reopened", "The canonical record was updated and read back successfully.",
                 f"{company} — {role} · Reopened{' (already recorded)' if done['status'] == 'already_recorded' else ''}.", "ok")


def handle(req, who, secrets):
    try:
        return (_hub if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        return _stop(409, "Not changed", "This opportunity cannot be managed here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")
