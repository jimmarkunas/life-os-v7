"""LIFE-88 E14 (Jim, Oct 24 2026; 12a + 12b): Manage on the private Close service, for ONE interview opportunity page.
12a: GET /manage?o=<opportunity page id> is a read-only hub. It finds the record that carries this pursuit's decision (decision.route: its one Ledger application,
or the Notes page itself), says whether it is open or closed, and links the actions that already exist, each of which keeps its own signed review and confirm:
Close, Mark Complete, Build résumé / I applied with this résumé, Mark as Sent. GET never writes.
12b: Reopen a closed pursuit, from the same hub, through a signed review and confirm: a fresh read routes the REOPEN to the record that closed it, and
decision.confirm_rejected (Ledger) or decision.move_page (Notes page back to Active Opportunities) writes and reads it back. Reopening a Hired outcome needs its
own tick. LIFE-111 / LIFE-112 (Jim, Oct 25): on an open pursuit, set Paused, Waiting on employer or back to Active, or a next action and/or due date of Jim's own,
through the same signed review and confirm: a fresh read routes it to the open record, and decision.set_hand writes one line on it and reads it back. LIFE-114 (Jim, Oct 29):
record sent material the same way: an application form or portal answers (an optional link), or a Google Drive file (a project plan, an article) that must exist and not be
in the trash when it is reviewed and again when it is confirmed. Fixed codes only."""
import re
from datetime import date
from html import escape as e
from urllib.parse import urlencode

from lifeos.platform.notion_client import Client, NotionError
from lifeos.hiring import close_screen as S, decision as D, models as M
from . import close_app as C

PATH, TAB = "Daily Report / Active Opportunities / Manage", "Manage opportunity"
REASON = "Reopened on the Close screen"
HOLD_CHOICES = ((M.PAUSED, "Pause it"), (M.WAITING, "Waiting on employer"), (M.ACTIVE, "Back to Active"))
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
QMAX, HITS = 100, 15                                                       # LIFE-173: a Drive search's longest query and most results shown
FOLDER = "application/vnd.google-apps.folder"
OP = re.compile(r"[0-9a-f]{16}")                                          # LIFE-172: the op of the sent item to remove
DRIVE_LINK = re.compile(r"https://(?:drive\.google\.com/file|docs\.google\.com/(?:document|spreadsheets|presentation))/d/([A-Za-z0-9_-]{10,200})(?:/[A-Za-z]*)?(?:[?#]\S*)?")
LINK = re.compile(r"https://[^\s<>\"]{1,500}")


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, C.HTML, S.page(title, "One interview opportunity: its actions, each confirmed on its own screen.",
                                  f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                  f'<div class="notice {css}">{e(detail)}</div></section>', PATH, TAB), ()


def _record(rows, parents, page):
    """-> (kind, record, closed) for the pursuit on this opportunity page, routed exactly as Close would route it."""
    kind, rec = D.route(rows, parents, {"action": D.ACTION, "id": D._page_id(page["id"]), "company": page["company"], "role": page["role"]})
    return kind, rec, bool(rec.get("decision")) if kind == "ledger" else bool(rec.get("retired"))


def _fold_lines(client, pid):
    """The paragraph lines of the page's ⚙️ LIFE OS fold, or [] for a page V7 did not write."""
    tops = C._lines(client, pid)
    if not tops or tops[0].get("type") != "toggle" or C._plain(tops[0]) != C.FOLD:
        return []
    return [C._plain(b) for b in C._lines(client, tops[0]["id"]) if b.get("type") == "paragraph"]


def _open_actions(lines):
    """The page's completable actions that are not done yet -> [(key, text)]: the derived primary action and Jim's own (LIFE-112), each with its own key."""
    done = {m.group(1) for m in map(C.DONE_LINE.fullmatch, lines) if m}
    return [(m.group(1), m.group(2)) for m in map(C.ACTION_LINE.fullmatch, lines) if m and m.group(1) not in done]


def _link(label, href, note):
    return f'<div class="actions"><a class="btn" href="{e(href)}">{e(label)} →</a><span class="lead">{e(note)}</span></div>'


def _fields(secret, login, kind, rec, page, exp):
    st = C._fingerprint(kind, D._page_id(rec["id"]), page["company"], page["role"], True)
    o = D._page_id(rec["id"])
    return {"o": o, "k": kind, "co": page["company"], "ro": page["role"], "exp": str(exp), "st": st, "t": C._mac(secret, "manage-reopen", login, o, st, exp)}


def _hand_fields(secret, login, kind, o, company, role, exp, act):
    st = C._fingerprint(kind, o, company, role, False)
    return {"o": o, "k": kind, "co": company, "ro": role, "exp": str(exp), "st": st, "act": act, "step": "review", "t": C._mac(secret, "manage-hand", login, o, st, exp, act)}


def _day(at):
    return f"{date.fromisoformat(at[:10]):%b} {int(at[8:10])}"


def _hand(secret, login, kind, rec, page, lines, ledger_ready, exp):
    """LIFE-111 / LIFE-112: the hand-set state of the open pursuit and its two signed forms (nothing is written from here)."""
    o, hold, nxt = D._page_id(rec["id"]), M.hold_of(lines), M.next_of(lines)
    if nxt and nxt["op"] in {m.group(1) for m in map(C.DONE_LINE.fullmatch, lines) if m}:
        nxt = None                                                               # Mark Complete cleared it
    state = f"{M.HOLD_LABEL[hold['state']]} · set by Jim {_day(hold['at'])}" if hold else "Active"
    body = ('<div class="eyebrow">Set by Jim</div><p class="lead">A hand-set state never changes the stage evidence shows; new mail or Calendar evidence clears Paused or Waiting.</p>'
            f'<p class="lead"><b>State:</b> {e(state)}</p>')
    if nxt:
        body += f'<p class="lead"><b>Your next action:</b> {e(nxt["text"] or "—")} · <b>Due:</b> {e(nxt["due"] or "No date set")}</p>'
    if kind == "ledger" and not ledger_ready:
        return body + f'<div class="notice alert">Not available yet: the Job Ledger has no “{e(D.HAND_SET)}” text property, so nothing can be stored on this application. Nothing was changed.</div>'
    buttons = "".join(f'<button class="btn" name="v" value="{v}">{e(label)}</button>' for v, label in HOLD_CHOICES if v != (hold or {}).get("state", M.ACTIVE))
    body += (f'<form method="post" action="/manage">{S._hidden(_hand_fields(secret, login, kind, o, page["company"], page["role"], exp, "hold"))}<div class="actions">{buttons}</div></form>'
             f'<form method="post" action="/manage">{S._hidden(_hand_fields(secret, login, kind, o, page["company"], page["role"], exp, "next"))}'
             f'<label class="lead">Next action <input type="text" name="nx" maxlength="{M.NEXT_MAX}"></label> <label class="lead">Due <input type="date" name="due"></label>'
             '<div class="actions"><button class="btn">Review next action</button><span class="lead">Shown beside the derived action, labelled set by Jim; Mark Complete clears it.</span></div></form>')
    sent, unsent = M.sent_of(lines), _hand_fields(secret, login, kind, o, page["company"], page["role"], exp, "unsent")
    body += '<div class="eyebrow">Materials sent</div>' + ("".join(                          # LIFE-172: each item can be removed, through its own review and confirm
        f'<form method="post" action="/manage">{S._hidden(unsent | {"v": m["op"], "sx": m["text"]})}<p class="lead">{e(M.SENT_KINDS[m["kind"]])}: {e(m["text"])} · recorded {e(_day(m["at"]))} '
        '<button class="btn link">Remove</button></p></form>' for m in sent) or '<p class="lead">None recorded yet.</p>')
    kinds = "".join(f'<label class="lead"><input type="radio" name="sk" value="{k}"{" checked" if k == M.FORM else ""}> {e(label)}</label> ' for k, label in M.SENT_KINDS.items())
    body += (f'<form method="post" action="/manage">{S._hidden(_hand_fields(secret, login, kind, o, page["company"], page["role"], exp, "sent"))}<div class="actions">{kinds}</div>'
             f'<label class="lead">What it was <input type="text" name="sx" maxlength="{M.NEXT_MAX}"></label> <label class="lead">Link <input type="url" name="su" maxlength="500"></label>'
             '<div class="actions"><button class="btn">Review sent material</button><span class="lead">A form: say what it was, a link is optional. A Drive file: paste its link.</span></div></form>'
             f'<form method="post" action="/manage">{S._hidden(_hand_fields(secret, login, kind, o, page["company"], page["role"], exp, "drive") | {"p": D._page_id(page["id"])})}'      # LIFE-173: find the file instead of hunting for its link
             f'<label class="lead">Search my Drive <input type="search" name="q" maxlength="{QMAX}" placeholder="Part of the file name"></label>'
             '<div class="actions"><button class="btn">Search my Drive</button><span class="lead">Pick a file from the results; you review it before anything is recorded.</span></div></form>')
    return body


def _drive(secrets):
    """The Drive client on a POST that has the credential (close_gate passes it on POST only), else None."""
    if not secrets.get("gdrive"):
        return None
    from .resume_app import gdrive_client
    return gdrive_client(secrets["gdrive"])


def _drive_file(drive, fid):
    """-> (name, link) of a Drive file that exists and is not in the trash, else None. Read only."""
    meta = drive.get(fid)
    name, link = meta.get("name"), meta.get("webViewLink")
    if meta.get("trashed") or not isinstance(name, str) or not name.strip() or not (isinstance(link, str) and LINK.fullmatch(link)):
        return None
    return " ".join(name.split())[:M.NEXT_MAX], link


def _sent_check(form, secrets):
    """LIFE-114: -> (kind, text, url, None) for a valid sent-material form, or (None, None, None, the refusal). A Drive file is read (never changed) to name it."""
    sk, text, url = form.get("sk", ""), " ".join((form.get("sx") or "").split()), (form.get("su") or "").strip()
    if sk == M.FORM:
        if not text or len(text) > M.NEXT_MAX or not text.isprintable() or (url and not LINK.fullmatch(url)):
            return None, None, None, _stop(400, "Not accepted", "Say what the form was; a link is optional", f"Up to {M.NEXT_MAX} characters; a link starts with https://.", "Nothing was changed.")
        return sk, text, url or None, None
    if sk != M.FILE:
        return None, None, None, _stop(400, "Not accepted", "Choose a form or a Drive file", "Reload Manage and choose one.", "Nothing was changed.")
    hit = DRIVE_LINK.fullmatch(url)
    if not hit:
        return None, None, None, _stop(400, "Not accepted", "Paste the Google Drive link of the file", "Open the file in Drive and copy its link.", "Nothing was changed.")
    drive = _drive(secrets)
    if drive is None:
        return None, None, None, _stop(409, "Not recorded", "Google Drive is not connected", "The Drive credential is not installed on the Close service.", "Nothing was changed.")
    try:
        found = _drive_file(drive, hit.group(1))
    except RuntimeError as error:                                          # gdrive.DriveError: fixed codes only
        return None, None, None, _stop(409, "Not recorded", "That Drive file could not be read", "Check the link and that the file is shared with you.", f"Refused: {str(error)[:60]}. Nothing was changed.")
    if not found:
        return None, None, None, _stop(409, "Not recorded", "That Drive file is in the trash or has no name", "Restore it in Drive or pick another file.", "Nothing was changed.")
    return sk, found[0], found[1], None


def _hub(req, who, secrets):
    pid = D._page_id(req["query"].get("o"))
    if not pid:
        return _stop(400, "Invalid link", "No opportunity selected", "Open Manage from an interview opportunity page.", "Nothing was changed.")
    reader, config = secrets["reader"], secrets["config"]
    pages = D.applied_pages(reader)
    rows, parents = C._state(reader, config["pipeline"], None, pages)
    mine = [p for p in parents if D._page_id(p["id"]) == pid]
    if len(mine) != 1:
        return _stop(404, "Not found", "This opportunity page is not in the Hiring Pipeline", "Open Manage from the opportunity page itself.", "Nothing was changed.")
    page = mine[0]
    kind, rec, closed = _record(rows, parents, page)
    rid = D._page_id(rec["id"])
    body = ('<section class="section"><div class="eyebrow">Exact opportunity</div><div class="jobhead"><div>'
            f'<h2>{e(page["role"])}</h2><div class="company">{e(page["company"])}</div></div>'
            f'<a class="status" href="https://www.notion.so/{rid}" target="_blank" rel="noopener">{"Closed" if closed else "Open"} · {"Job Ledger" if kind == "ledger" else "Notes page"} ↗</a></div></section><section class="section">'
            '<div class="eyebrow">Actions</div><p class="lead">Each action opens its own screen and changes nothing until you confirm there.</p>')
    exp = int(C._now().timestamp()) + C.TTL
    if closed:
        try:                                                               # offered only when the POST's REOPEN routing would reach this same record
            routed, again = D.route(rows, parents, {"action": D.REOPEN, "id": rid, "company": page["company"], "role": page["role"]})
            why = None if (routed, D._page_id(again["id"])) == (kind, rid) else "DECISION_STALE_IDENTITY"
        except D.DecisionError as error:
            why = str(error)[:60]
        if why:
            body += f'<div class="notice alert">Reopen is not available here: {e(why)}. Nothing was changed.</div>'
        else:
            fields = _fields(secrets["link"], who["login"], kind, rec, page, exp)
            hired = rec.get("decision") == "Hired" or kind == "notes"          # a Notes page's outcome lives in its own record lines; the decision rules check it
            body += (f'<form method="post" action="/manage">{S._hidden(fields)}<div class="actions"><button class="btn primary" name="step" value="review">Reopen this opportunity</button>'
                     '<span class="lead">Puts it back among the open opportunities; its history is kept.</span></div>'
                     + ('<label class="lead"><input type="checkbox" name="h" value="1"> If it was recorded as Hired: reopen it anyway</label>' if hired else "") + '</form>')
    else:
        body += _link("Close this opportunity", "/?" + urlencode({"o": rid}), "Record why it ended (rejected, withdrew, filled, ...).")
        lines = _fold_lines(reader, pid)
        for key, text in _open_actions(lines):
            body += _link("Mark the next action complete", "/complete?" + urlencode({"o": pid, "a": key}), "Your own next action." if any(
                (M.NEXT_LINE.fullmatch(t) or [None] * 4)[3] == key for t in lines) else "The page's current primary action.")
        raw = next((p for p in pages if D._page_id(p.get("id")) == rid), {}) if kind == "ledger" else {}
        props = raw.get("properties") or {}
        ledger_lines = M.hand_lines(D._text(props.get(D.HAND_SET), "rich_text")) if kind == "ledger" else ()
        body += '</section><section class="section">' + _hand(secrets["link"], who["login"], kind, rec, page, ledger_lines + tuple(lines), D.HAND_SET in props, exp)
    if kind == "ledger":
        body += _link("Build résumé · I applied with this résumé", "/resume?" + urlencode({"j": rid}), "Tailor a résumé, or record which one you sent.")
        if not closed:
            body += _link("Mark outreach as sent", "/sent?" + urlencode({"j": rid}), "Record a message you sent to a contact for this job.")
    body += (f'</section><div class="actions"><a class="btn link" href="https://www.notion.so/{pid}" target="_blank" rel="noopener">Open the opportunity page in Notion →</a></div>')
    return 200, C.HTML, S.page("Manage opportunity", "One interview opportunity: its actions, each confirmed on its own screen.", body, PATH, TAB), ()


def unapplied(rows):
    """LIFE-169 (Jim, Oct 30 2026): Close's DECISION_LEDGER_UNAPPLIED, saying which Job Ledger row blocks the closure and what to do, not only its code. Nothing is written."""
    links = "".join(f'<a class="btn link" href="https://www.notion.so/{e(r)}" target="_blank" rel="noopener">Open the Job Ledger row →</a>' for r in rows)
    return 409, C.HTML, S.page("This opportunity cannot be closed yet", "See the status below. A closure counts as done only after the canonical record is read back.", (
        '<section class="section"><div class="eyebrow">Not changed</div><h3 class="step-title">A Job Ledger row for this role is not marked Applied</h3>'
        '<p class="lead">That row may be this same application, so Close will not choose between the two records.</p>'
        '<p class="lead">If it is this application, tick <b>Applied</b> on that row, then open Close again from the opportunity page: it closes on the Ledger row. '
        'If it is a different job, leave it unticked and ask for a review; Close stays blocked until then.</p>'
        f'<div class="notice alert">Refused: DECISION_LEDGER_UNAPPLIED. Nothing was changed.</div><div class="actions">{links}</div></section>')), ()


def _moved(secret, login, act, routed, target, parents, o, company, role, form):
    """LIFE-169 (Jim, Oct 30 2026): the pursuit no longer lives on the record this form names. Nothing is written; the screen says where it lives now and, when that record
    is open, offers the same change for review there with Jim's entry carried over (a form signed for that record only; its confirm does its own fresh read)."""
    closed = target.get("decision") if routed == "ledger" else target.get("retired")
    page = target if routed == "notes" else next((p for p in parents if D._page_id(p["id"]) == o), None)
    hub = _link("Open Manage again", "/manage?" + urlencode({"o": D._page_id(page["id"])}), "From the opportunity page, with its current state.") if page else ""
    if closed:
        body = ('<section class="section"><div class="eyebrow">Conflict</div><h3 class="step-title">This opportunity was closed after Manage opened</h3>'
                '<p class="lead">A closed opportunity takes no next action or hold.</p><div class="notice alert">Nothing was changed.</div>' + hub + '</section>')
        return 409, C.HTML, S.page("Opportunity changed", "One interview opportunity: its actions, each confirmed on its own screen.", body, PATH, TAB), ()
    where = "its Job Ledger row" if routed == "ledger" else "its Notes page"
    why = ("It was marked Applied on the Job Ledger after Manage opened, so the opportunity now lives on that row."
           if routed == "ledger" else "Applied was unticked on the Job Ledger after Manage opened, so the opportunity now lives on its Notes page.")
    keep = ({"v": form.get("v", "")} if act == "hold" else {"sk": form.get("sk", ""), "sx": form.get("sx", ""), "su": form.get("su", "")} if act == "sent"
            else {"v": form.get("v", ""), "sx": form.get("sx", "")} if act == "unsent" else {"nx": form.get("nx", ""), "due": form.get("due", "")})
    fields = _hand_fields(secret, login, routed, D._page_id(target["id"]), company, role, int(C._now().timestamp()) + C.TTL, act) | keep
    body = (f'<section class="section"><div class="eyebrow">Conflict</div><h3 class="step-title">This opportunity now lives on <a href="https://www.notion.so/{D._page_id(target["id"])}" target="_blank" rel="noopener">{e(where)} ↗</a></h3><p class="lead">{e(why)}</p>'
            '<div class="notice alert">Nothing was changed. Your entry is kept below: review it on the new record, then confirm.</div>'
            f'<form method="post" action="/manage">{S._hidden(fields)}<div class="actions"><button class="btn primary">Review this change on {e(where)}</button></div></form>'
            + hub + '</section>')
    return 409, C.HTML, S.page("Opportunity changed", "One interview opportunity: its actions, each confirmed on its own screen.", body, PATH, TAB), ()


def _hand_post(req, who, secrets):
    """LIFE-111 / LIFE-112: review, then confirm, one hand-set line on the open record. Review never reads Notion; confirm writes only after a fresh read routes to the same open record."""
    form, secret, login = req["form"], secrets["link"], who["login"]
    o, step, exp, st, kind, act = D._page_id(form.get("o")), form.get("step"), form.get("exp", ""), form.get("st", ""), form.get("k", ""), form.get("act", "")
    company, role = form.get("co", ""), form.get("ro", "")
    if (not o or step not in ("cancel", "review", "confirm") or kind not in ("ledger", "notes") or not (exp.isascii() and exp.isdigit()) or int(exp) < C._now().timestamp()
            or not C._same(form.get("t"), C._mac(secret, "manage-hand", login, o, st, exp, act)) or not C._same(C._fingerprint(kind, o, company, role, False), st)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload Manage from the opportunity page.", "Nothing was changed.", "danger")
    if step == "cancel":
        return _stop(200, "Cancelled", "Nothing changed", "The opportunity keeps its current state and next action.", "Nothing was written.", "")
    value, text, due = form.get("v", ""), " ".join((form.get("sx" if act == "unsent" else "nx") or "").split()), (form.get("due") or "").strip()
    if act == "sent":                                                     # LIFE-114: the Drive file is read at review and again at confirm, before anything is written
        value, text, due, refused = _sent_check(form, secrets)
        if refused:
            return refused
    if act == "unsent" and not (OP.fullmatch(value) and len(text) <= M.NEXT_MAX and text.isprintable()):
        return _stop(400, "Not accepted", "Choose an item to remove", "Reload Manage and choose one.", "Nothing was changed.")
    if act == "hold" and value not in M.HOLD_LABEL and value != M.ACTIVE:
        return _stop(400, "Not accepted", "Choose Paused, Waiting on employer or Active", "Reload Manage and choose one.", "Nothing was changed.")
    if act == "next":
        try:
            valid = bool(DAY.fullmatch(due) and date.fromisoformat(due)) if due else True       # exactly the date Jim typed, a real one, or none: never a guessed date
        except ValueError:
            valid = False
        if not valid or len(text) > M.NEXT_MAX or not (text or due) or not (text or "x").isprintable():
            return _stop(400, "Not accepted", "Enter a next action, a real due date, or both", f"A next action is up to {M.NEXT_MAX} characters; a due date is a real calendar date.", "Nothing was changed.")
    payload = value if act == "hold" else f"{value}\x1f{text}\x1f{due or ''}" if act == "sent" else f"{value}\x1f{text}" if act == "unsent" else f"{text}\x1f{due}"
    sign = C._mac(secret, "manage-hand-c", login, o, st, exp, act, payload)
    what = (f"Set it to {M.HOLD_LABEL.get(value, 'Active')}" if act == "hold" else f"Sent: {M.SENT_KINDS[value]} · {text}" if act == "sent" else f"Remove sent material: {text}" if act == "unsent"
            else f"Your next action: {text or '—'} · Due: {due or 'No date set'}")
    if step == "review":
        fields = {k: form.get(k, "") for k in ("o", "k", "co", "ro", "exp", "st", "act", "t")} | (
            {"v": value} if act == "hold" else {"sk": value, "sx": form.get("sx", ""), "su": form.get("su", "")} if act == "sent"
            else {"v": value, "sx": text} if act == "unsent" else {"nx": text, "due": due}) | {"c": sign}
        note = ("The stage stays as the evidence shows; the Hiring Pipeline lists it with no to-do until you set it back to Active or new mail or Calendar evidence arrives."
                if act == "hold" and value != M.ACTIVE else "It goes back on the to-do list." if act == "hold"
                else "Listed under Materials Sent on the opportunity page after the next hourly run." if act == "sent"
                else "Taken off Materials Sent after the next hourly run; the record keeps a removal line, so its history stays." if act == "unsent"
                else "Shown beside the derived action, labelled set by Jim, after the next hourly run. New evidence never changes it; Mark Complete clears it.")
        return 200, C.HTML, S.page("Review the change", "Nothing is recorded until you confirm.", (
            f'<section class="section"><div class="eyebrow">Review</div><p class="step-title">{e(role)}</p><p class="lead">{e(company)}</p>'
            f'<p class="lead"><b>{e(what)}</b></p><p class="lead">{e(note)} Nothing else changes and nothing is sent.</p></section>'
            f'<form method="post" action="/manage">{S._hidden(fields)}<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button>'
            '<button class="btn primary" name="step" value="confirm">Confirm</button></div></form>'), PATH, TAB), ()
    if not C._same(form.get("c"), sign):
        return _stop(403, "Not accepted", "This confirmation is not valid", "Review the change again.", "Nothing was changed.", "danger")
    config = secrets["config"]
    client = Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]})
    pages = D.applied_pages(client)                                     # the write is authorized only by a fresh read
    rows, parents = C._state(client, config["pipeline"], o, pages)
    routed, target = D.route(rows, parents, {"action": D.ACTION, "id": o, "company": company, "role": role})
    if routed != kind or D._page_id(target["id"]) != o or (target.get("decision") if kind == "ledger" else target.get("retired")):
        return _moved(secret, login, act, routed, target, parents, o, company, role, form)
    at, op = C._now().isoformat(timespec="seconds"), C._mac(secret, "op-h", o, st, exp, act, payload)[:16]
    line = (M.hold_line(value, at, op) if act == "hold" else M.sent_line(value, text, due, at, op) if act == "sent" else M.unsent_line(value, at, op) if act == "unsent"
            else M.next_line(text, due, at, op))
    try:
        done = D.set_hand(client, kind, {"id": o, "company": company, "role": role}, line, True, pages, lambda: C._state(client, config["pipeline"], pages=[])[1])
    except NotionError as error:
        code = str(error)[:60]
        if code == "DECISION_SCHEMA_MISSING":
            return _stop(409, "Not changed", "The Job Ledger cannot store this yet", f"It needs a “{D.HAND_SET}” text property on the Job Ledger.", f"Refused: {code}. Nothing was changed.")
        if code.removeprefix("DECISION_") in ("CLOSED", "STALE_IDENTITY", "CONCURRENT_CHANGE", "NOT_A_LIFE_OS_PAGE", "HISTORY_UNREADABLE"):
            return _stop(409, "Not changed", "This change was not recorded", "The opportunity closed or changed since Manage was opened.", f"Refused: {code}. Nothing was changed.")
        return _stop(500, "Unavailable", "Could not verify the change", "Do not assume it was recorded or left unchanged.", f"Status: DEGRADED · {code}", "danger")
    if done.get("verified") is not True or done.get("status") not in ("recorded", "already_recorded"):
        return _stop(500, "Unavailable", "Could not verify the change", "Do not assume it was recorded or left unchanged.", "Status: DEGRADED · not verified", "danger")
    if act == "hold":
        secrets.get("after", []).append("hiring")                      # the Hiring Pipeline region is redrawn now (close_gate.REFRESH)
    return _stop(200, "Recorded", "Recorded and read back", "The opportunity's own record was updated and read back successfully.",
                 f"{company} — {role} · {what}{' (already recorded)' if done['status'] == 'already_recorded' else ''}.", "ok")


def _drive_search(req, who, secrets):
    """LIFE-173 (Jim, Oct 30 2026): "I don't want to have to hunt for Google Drive files". A signed search of Jim's Drive by file name (read only, the Close service's own Drive
    credential, newest edits first); each hit opens the usual sent-material review for that file, so nothing is recorded until he confirms there."""
    form, secret, login = req["form"], secrets["link"], who["login"]
    o, exp, st, kind, company, role = D._page_id(form.get("o")), form.get("exp", ""), form.get("st", ""), form.get("k", ""), form.get("co", ""), form.get("ro", "")
    if (not o or kind not in ("ledger", "notes") or not (exp.isascii() and exp.isdigit()) or int(exp) < C._now().timestamp()
            or not C._same(form.get("t"), C._mac(secret, "manage-hand", login, o, st, exp, "drive")) or not C._same(C._fingerprint(kind, o, company, role, False), st)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload Manage from the opportunity page.", "Nothing was changed.", "danger")
    q = " ".join((form.get("q") or "").split())
    if not q or len(q) > QMAX or not q.isprintable():
        return _stop(400, "Not accepted", "Type part of the file name", f"Up to {QMAX} characters.", "Nothing was searched.")
    drive = _drive(secrets)
    if drive is None:
        return _stop(409, "Not searched", "Google Drive is not connected", "The Drive credential is not installed on the Close service.", "Nothing was searched.")
    from lifeos.platform.gdrive import _quote
    try:
        files = drive.find(f"name contains '{_quote(q)}' and mimeType != '{FOLDER}'", order="modifiedTime desc")
    except RuntimeError as error:                                          # gdrive.DriveError: fixed codes only
        return _stop(409, "Not searched", "Google Drive could not be searched", "Try again in a minute.", f"Refused: {str(error)[:60]}. Nothing was changed.")
    hits = [f for f in files if isinstance(f.get("name"), str) and DRIVE_LINK.fullmatch(str(f.get("webViewLink") or ""))][:HITS]
    pick = _hand_fields(secret, login, kind, o, company, role, int(exp), "sent") | {"sk": M.FILE, "sx": ""}
    rows = "".join(f'<form method="post" action="/manage">{S._hidden(pick | {"su": f["webViewLink"]})}<div class="actions"><button class="btn">Pick</button>'
                   f'<span class="lead">{e(" ".join(f["name"].split()))} · <a href="{e(f["webViewLink"])}" target="_blank" rel="noopener">open ↗</a></span></div></form>' for f in hits)
    body = (f'<section class="section"><div class="eyebrow">Search my Drive</div><p class="step-title">{e(role)}</p><p class="lead">{e(company)}</p>'
            f'<p class="lead">Files whose name contains “{e(q)}”, most recently edited first{f" (the first {HITS})" if len(hits) == HITS else ""}.</p>'
            + (rows or '<div class="notice alert">No Drive file matches. Try a shorter part of the name.</div>')
            + (_link("Back to Manage", "/manage?" + urlencode({"o": back}), "Nothing was recorded.") if (back := D._page_id(form.get("p"))) else "") + '</section>')
    return 200, C.HTML, S.page("Search my Drive", "Pick the file you sent; you review it before anything is recorded.", body, PATH, TAB), ()


def _post(req, who, secrets):
    if req["form"].get("act") == "drive":
        return _drive_search(req, who, secrets)
    if req["form"].get("act") in ("hold", "next", "sent", "unsent"):
        return _hand_post(req, who, secrets)
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
