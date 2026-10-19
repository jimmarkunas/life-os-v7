"""LIFE-21 / LIFE-47 (D221): Mark as Sent on the private Close service (same Edge gate, same signed-form pattern as close_app). Jim confirms that he messaged one person
about one exact Job Ledger job; only the confirm POST writes, through lifeos.recruiters.outreach.mark_sent, and success is shown only after its read-back.
GET and Review never write; nothing is sent to anyone. Fixed codes only."""
from html import escape as e

from lifeos.hiring import close_screen as S, decision as D
from lifeos.platform.notion_client import Client, NotionError
from lifeos.recruiters import outreach as O
from .close_app import TTL, HTML, _mac, _now, _same

PATH = "Daily Report / Job Outreach / Mark as Sent"


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.page(title, "A Mark as Sent counts only after the canonical record is read back.",
                                f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                f'<div class="notice {css}">{e(detail)}</div></section>', PATH, "Mark as Sent"), ()


def job(client, config, pid):
    """The ONE open Job Ledger job with this exact page id -> {id, company, role}, or a fixed refusal. Read only."""
    try:
        page = client.get_page(pid)
    except NotionError:
        raise D.fail("NOT_FOUND") from None
    parent = (page.get("parent") or {}).get("data_source_id") or ""
    props = page.get("properties") or {}
    company, role = D._text(props.get("Company"), "rich_text"), D._text(props.get("Job"), "title")
    if page.get("archived") or page.get("in_trash") or O._id(parent) != O._id(config.get("ledger")) or not company or not role:
        raise D.fail("NOT_FOUND")
    return {"id": D._page_id(page.get("id")) or pid, "company": company, "role": role}


def _job_section(j):
    return (f'<section class="section"><div class="eyebrow">Exact job</div><div class="jobhead"><div><h2>{e(j["role"])}</h2><div class="company">{e(j["company"])}</div></div>'
            '<span class="status">Job Ledger</span></div></section>')


def _form(j, fields, person="", notice=""):
    warn = f'<div class="notice alert">{e(notice)}</div>' if notice else ""
    return S.page("Mark as Sent", "Confirm only after you have sent the message yourself. LIFE OS never sends it.", _job_section(j) + (
        f'<form method="post" action="/sent">{S._hidden(fields)}<section class="section"><div class="eyebrow">Action · Mark as Sent</div>'
        '<label class="fieldname" for="person">Who did you message about this job?</label>'
        f'<textarea id="person" name="person" maxlength="{O.PERSON_MAX}" required>{e(person)}</textarea>{warn}'
        '<div class="notice">This records one tracked conversation for this exact job, sets it to Waiting and schedules a follow-up two business days out.</div></section>'
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="review">Review →</button></div></form>'),
        PATH, "Mark as Sent")


def _review(j, fields, person):
    return S.page("Review Mark as Sent", "Your confirmation is required before anything is recorded.", (
        f'<form method="post" action="/sent">{S._hidden(fields)}<section class="section"><div class="eyebrow">Review action</div>'
        f'<dl class="review"><dt>Company</dt><dd>{e(j["company"])}</dd><dt>Role</dt><dd>{e(j["role"])}</dd><dt>Messaged</dt><dd>{e(person)}</dd></dl>'
        '<div class="notice alert">Confirming records the conversation as Waiting; it is shown as done only after the canonical record is read back.</div></section>'
        '<div class="actions"><button class="btn" name="step" value="edit" formnovalidate>← Edit</button><button class="btn primary" name="step" value="confirm">Confirm Mark as Sent</button></div></form>'),
        PATH, "Mark as Sent")


def _fp(j):
    return _mac(b"fp", j["id"], j["company"], j["role"])[:16]


def _get(req, who, secrets):
    pid = D._page_id(req["query"].get("j"))
    if not pid:
        return _stop(400, "Invalid link", "No job selected", "Open this screen from a Job Ledger job.", "Nothing was changed.")
    j = job(secrets["reader"], secrets["config"], pid)
    exp = str(int(_now().timestamp()) + TTL)
    st = _fp(j)
    fields = {"j": j["id"], "co": j["company"], "ro": j["role"], "exp": exp, "st": st, "t": _mac(secrets["link"], "s", who["login"], j["id"], st, exp)}
    return 200, HTML, _form(j, fields), ()


def _post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    pid, step, exp, st = D._page_id(form.get("j")), form.get("step"), form.get("exp", ""), form.get("st", "")
    if not pid or step not in ("cancel", "edit", "review", "confirm") or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not _same(form.get("t"), _mac(secret, "s", login, pid, st, exp)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")
    if step == "cancel":
        return _stop(200, "Cancelled", "No changes made", "Nothing was recorded.", "Nothing was written.", "")
    shown = {"id": pid, "company": form.get("co", ""), "role": form.get("ro", "")}
    if not _same(_fp(shown), st):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")
    fields = {k: form.get(k, "") for k in ("j", "co", "ro", "exp", "st", "t")}
    person = O.valid_person(form.get("person"))
    if step == "edit":
        return 200, HTML, _form(shown, fields, person), ()
    if not person:
        return 400, HTML, _form(shown, fields, "", f"Enter the person's name (up to {O.PERSON_MAX} characters)."), ()
    sign = _mac(secret, "m", login, pid, st, exp, person)
    if step == "review":
        return 200, HTML, _review(shown, {**fields, "person": person, "c": sign}, person), ()
    if not _same(form.get("c"), sign):
        return _stop(403, "Not accepted", "This confirmation is not valid", "Review it again.", "Nothing was changed.", "danger")
    config = secrets["config"]
    token = secrets["notion"].decode()
    current = job(Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]}), config, pid)   # the write is authorized only by a fresh read
    if _fp(current) != st:
        return _stop(409, "Conflict", "Job changed", "The job changed since this form was opened.", "No write attempted. Reload the job and review it again.")
    try:
        recruiters = Client({"NOTION_API_TOKEN": token, "NOTION_RECRUITERS_DATA_SOURCE_ID": config.get("recruiters") or ""}, source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")
        done = O.mark_sent(recruiters, current, person, _now(), config["ledger"])
    except NotionError as error:
        code = str(error)[:60]
        if code in ("OUTREACH_SCHEMA_MISSING", "OUTREACH_AMBIGUOUS", "NOTION_CONFIG_MISSING"):
            return _stop(409, "Not changed", "Mark as Sent was not recorded", "Job Outreach is not enabled for this record yet, or it is ambiguous.", f"Refused: {code}. Nothing was changed.")
        return _stop(500, "Unavailable", "Could not verify Mark as Sent", "Do not assume it was recorded or left unchanged.", f"Status: DEGRADED · {code}", "danger")
    if done.get("verified") is not True:
        return _stop(500, "Unavailable", "Could not verify Mark as Sent", "Do not assume it was recorded or left unchanged.", "Status: DEGRADED · not verified", "danger")
    again = " (already recorded)" if done["status"] == "already_recorded" else ""
    return _stop(200, "Recorded", "Marked as sent", "The canonical record was written and read back.",
                 f"{current['company']} — {current['role']} · {person} · Waiting · follow-up {done['follow_up']}{again}.", "ok")


def handle(req, who, secrets):
    try:
        return (_get if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        return _stop(409, "Not changed", "This job cannot be marked here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")
