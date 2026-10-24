"""LIFE-21 / LIFE-47 (D221): Mark as Sent on the private Close service (same Edge gate, same signed-form pattern as close_app). Jim confirms that he messaged one person
about one exact Job Ledger job; only the confirm POST writes, through lifeos.recruiters.outreach.mark_sent, and success is shown only after its read-back.
GET and Review never write; nothing is sent to anyone. Fixed codes only.
The people offered are the job page's own Network Leads (lifeos.network.surface, read only, dismissed leads left out), signed into the form with the job so a tampered
name is refused; "Other" takes a typed name. The person's employer is optional: typed, or the current employer the chosen lead's own line states ("Listed at ..."),
shown on Review and signed with the person, and stored as the row's Recruiter Company; nothing is guessed."""
from html import escape as e

from lifeos.hiring import close_screen as S, decision as D
from lifeos.network import surface as N
from lifeos.platform.notion_client import Client, NotionError
from lifeos.recruiters import notion as store, outreach as O
from .close_app import TTL, HTML, _mac, _now, _same

PATH = "Daily Report / Job Outreach / Mark as Sent"
PEOPLE_MAX = 10
STATUS_PATH = "Job Ledger / Outreach / Record reply or referral"
OUTREACH_TITLE = "🤝 Outreach"
CHOICES = (("Replied", "They replied"), ("Referral Offered", "They offered to refer me (not yet confirmed)"),
           ("Referral Confirmed", "The referral is confirmed (submitted by them)"), ("Declined", "They declined"))


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.page(title, "A Mark as Sent counts only after the canonical record is read back.",
                                f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                f'<div class="notice {css}">{e(detail)}</div></section>', PATH, "Mark as Sent"), ()


def job(client, config, pid):
    """The ONE open Job Ledger job with this exact page id -> {id, company, role}, or a fixed refusal. Read only. Open follows the Close rules (D194): a recorded
    Hiring Decision closes the job (JOB_CLOSED); a Ledger page without the Hiring Decision property is not provably open and is refused (JOB_STATE_UNKNOWN)."""
    try:
        page = client.get_page(pid)
    except NotionError:
        raise D.fail("NOT_FOUND") from None
    parent = (page.get("parent") or {}).get("data_source_id") or ""
    props = page.get("properties") or {}
    company, role = D._text(props.get("Company"), "rich_text"), D._text(props.get("Job"), "title")
    if page.get("archived") or page.get("in_trash") or O._id(parent) != O._id(config.get("ledger")) or not company or not role:
        raise D.fail("NOT_FOUND")
    decision = props.get(D.DECISION)
    if not isinstance(decision, dict) or decision.get("type") != "select":
        raise D.fail("JOB_STATE_UNKNOWN")
    if (decision.get("select") or {}).get("name"):
        raise D.fail("JOB_CLOSED")
    return {"id": D._page_id(page.get("id")) or pid, "company": company, "role": role}


def _job_section(j):
    return (f'<section class="section"><div class="eyebrow">Exact job</div><div class="jobhead"><div><h2>{e(j["role"])}</h2><div class="company">{e(j["company"])}</div></div>'
            '<span class="status">Job Ledger</span></div></section>')


def _people_fields(secret, login, pid, st, exp, people):
    """The offered people as hidden fields, signed with the job: l0/r0 (name, reason) ... and lm."""
    out = {}
    for i, (name, why) in enumerate(people):
        out[f"l{i}"], out[f"r{i}"] = name, why
    out["lm"] = _mac(secret, "l", login, pid, st, exp, *[part for pair in people for part in pair])
    return out


def _people(form):
    people = []
    while f"l{len(people)}" in form and len(people) < PEOPLE_MAX:
        people.append((form[f"l{len(people)}"], form.get(f"r{len(people)}", "")))
    return people


def _form(j, fields, people=(), choice="", person="", notice="", employer=""):
    warn = f'<div class="notice alert">{e(notice)}</div>' if notice else ""
    picked = choice or ("" if people else "other")
    options = "".join(f'<label class="reason"><input type="radio" name="who" value="lead:{i}" required{" checked" if picked == f"lead:{i}" else ""}>'
                      f'<span><strong>{e(name)}</strong>{f"""<span class="fine">{e(why)}</span>""" if why else ""}</span></label>' for i, (name, why) in enumerate(people))
    options += f'<label class="reason"><input type="radio" name="who" value="other" required{" checked" if picked == "other" else ""}><span><strong>Other</strong></span></label>'
    return S.page("Mark as Sent", "Confirm only after you have sent the message yourself. LIFE OS never sends it.", _job_section(j) + (
        f'<form method="post" action="/sent">{S._hidden(fields)}<section class="section"><div class="eyebrow">Action · Mark as Sent</div>'
        f'<fieldset><legend class="step-title">Who did you message about this job? <span aria-label="Required">*</span></legend><div class="people">{options}</div></fieldset>'
        '<label class="fieldname" for="person">Other — their name <span class="fine inl">(only when Other is chosen)</span></label>'
        f'<textarea id="person" name="person" maxlength="{O.PERSON_MAX}">{e(person)}</textarea>'
        '<label class="fieldname" for="employer">Their employer <span class="fine inl">(optional; a chosen lead\'s current employer is used when left blank)</span></label>'
        f'<textarea id="employer" name="employer" maxlength="{O.PERSON_MAX}">{e(employer)}</textarea>{warn}'
        '<div class="notice">This records one tracked conversation for this exact job, sets it to Waiting and schedules a follow-up two business days out.</div></section>'
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="review">Review →</button></div></form>'),
        PATH, "Mark as Sent")


def _review(j, fields, person, employer):
    return S.page("Review Mark as Sent", "Your confirmation is required before anything is recorded.", (
        f'<form method="post" action="/sent">{S._hidden(fields)}<section class="section"><div class="eyebrow">Review action</div>'
        f'<dl class="review"><dt>Company</dt><dd>{e(j["company"])}</dd><dt>Role</dt><dd>{e(j["role"])}</dd><dt>Messaged</dt><dd>{e(person)}</dd><dt>Their employer</dt><dd>{e(employer) or "Not recorded"}</dd></dl>'
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
    people = [(O.valid_person(lead["name"]), lead["reason"]) for lead in N.leads_on_page(secrets["reader"], j["id"], PEOPLE_MAX) if O.valid_person(lead["name"])]
    exp = str(int(_now().timestamp()) + TTL)
    st = _fp(j)
    fields = {"j": j["id"], "co": j["company"], "ro": j["role"], "exp": exp, "st": st, "t": _mac(secrets["link"], "s", who["login"], j["id"], st, exp)}
    return 200, HTML, _form(j, {**fields, **_people_fields(secrets["link"], who["login"], j["id"], st, exp, people)}, people), ()


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
    people = _people(form)
    if not _same(form.get("lm"), _people_fields(secret, login, pid, st, exp, people)["lm"]):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")
    fields = {**{k: form.get(k, "") for k in ("j", "co", "ro", "exp", "st", "t")}, **_people_fields(secret, login, pid, st, exp, people)}
    choice = form.get("who") or ("" if people else "other")                 # with people offered, a choice is required; without, the typed name is the only one
    employer = O.valid_person(form.get("employer"))
    if step == "review":                                                   # the choice decides the person: an offered lead's signed name, or the typed Other
        index = choice[5:] if choice.startswith("lead:") else ""
        lead = people[int(index)] if index.isdigit() and int(index) < len(people) else None
        person = O.valid_person(lead[0]) if lead else O.valid_person(form.get("person")) if choice == "other" else ""
        employer = employer or (O.employer_from(lead[1]) if lead else "")   # typed wins; else only the current employer the lead's own signed line states
    else:
        person = O.valid_person(form.get("person"))                        # edit and confirm carry the reviewed person and employer, bound by the confirmation signature
    if step == "edit":
        return 200, HTML, _form(shown, fields, people, choice, person if choice == "other" else "", employer=employer), ()
    if not person:
        ask = "Choose who you messaged, or choose Other and enter their name" if people else "Enter the person's name"
        return 400, HTML, _form(shown, fields, people, choice, "", f"{ask} (up to {O.PERSON_MAX} characters).", employer), ()
    sign = _mac(secret, "m", login, pid, st, exp, person, employer)
    if step == "review":
        return 200, HTML, _review(shown, {**fields, "who": choice, "person": person, "employer": employer, "c": sign}, person, employer), ()
    if not _same(form.get("c"), sign):
        return _stop(403, "Not accepted", "This confirmation is not valid", "Review it again.", "Nothing was changed.", "danger")
    config = secrets["config"]
    token = secrets["notion"].decode()
    current = job(Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]}), config, pid)   # the write is authorized only by a fresh read
    if _fp(current) != st:
        return _stop(409, "Conflict", "Job changed", "The job changed since this form was opened.", "No write attempted. Reload the job and review it again.")
    try:
        recruiters = Client({"NOTION_API_TOKEN": token, "NOTION_RECRUITERS_DATA_SOURCE_ID": config.get("recruiters") or ""}, source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")
        done = O.mark_sent(recruiters, current, person, _now(), config["ledger"], employer)
    except NotionError as error:
        code = str(error)[:60]
        if code in ("OUTREACH_SCHEMA_MISSING", "OUTREACH_AMBIGUOUS", "NOTION_CONFIG_MISSING"):
            return _stop(409, "Not changed", "Mark as Sent was not recorded", "Job Outreach is not enabled for this record yet, or it is ambiguous.", f"Refused: {code}. Nothing was changed.")
        return _stop(500, "Unavailable", "Could not verify Mark as Sent", "Do not assume it was recorded or left unchanged.", f"Status: DEGRADED · {code}", "danger")
    if done.get("verified") is not True:
        return _stop(500, "Unavailable", "Could not verify Mark as Sent", "Do not assume it was recorded or left unchanged.", "Status: DEGRADED · not verified", "danger")
    head = f"{current['company']} — {current['role']} · {person}" + (f" ({employer})" if employer else "")
    if done["status"] == "recorded":
        secrets.get("after", []).append("recruiters")                      # Jim, Oct 24: the gate asks for the Recruiter Outreach table to be redrawn now (close_gate.REFRESH)
        return _stop(200, "Recorded", "Marked as sent", "The canonical record was written and read back.", f"{head} · Waiting · follow-up {done['follow_up']}.", "ok")
    if done["state"] == "closed":                                              # never relabel a closed, inactive or finished conversation (D221)
        return _stop(409, "Not changed", "This conversation is already closed", "It was recorded before and is no longer open.",
                     f"{head} · {done['action'] or 'no status'} · nothing was changed.")
    follow = f" · follow-up {done['follow_up']}" if done["follow_up"] else ""
    return _stop(200, "Already recorded", "Already marked as sent", "This conversation was recorded before; nothing was changed.",
                 f"{head} · currently {done['action'] or 'no status'}{follow}.", "")


def handle(req, who, secrets):
    try:
        return (_get if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        return _stop(409, "Not changed", "This job cannot be marked here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")


def conversations_on_page(client, page_id):
    """The people with a recorded conversation on this job page: the first line of the first cell of each row of the page's own Outreach table. Read only; no table -> []."""
    top = N.children(client, page_id)
    for i, block in enumerate(top):
        if block.get("type") == "heading_3" and N._plain(block).startswith(OUTREACH_TITLE) and i + 1 < len(top) and top[i + 1].get("type") == "table":
            rows = N.children(client, top[i + 1]["id"])[1:]
            first = lambda row: (((row.get("table_row") or {}).get("cells") or [[]])[0] or [{}])[0].get("plain_text", "")      # noqa: E731 - the bold person line
            people = [O.valid_person(first(row)) for row in rows]
            return [p for p in people if p][:PEOPLE_MAX]
    return []


def _status_form(j, fields, people, notice="", picked="", status=""):
    warn = f'<div class="notice alert">{e(notice)}</div>' if notice else ""
    who = "".join(f'<label class="reason"><input type="radio" name="who" value="{i}" required{" checked" if picked == str(i) else ""}><span><strong>{e(name)}</strong></span></label>' for i, name in enumerate(people))
    what = "".join(f'<label class="reason"><input type="radio" name="status" value="{e(value)}" required{" checked" if status == value else ""}><span><strong>{e(label)}</strong></span></label>' for value, label in CHOICES)
    return S.page("Record reply or referral", "Record only what you have seen. LIFE OS never contacts anyone and never marks the job Applied.", _job_section(j) + (
        f'<form method="post" action="/referral">{S._hidden(fields)}<section class="section"><div class="eyebrow">Action · Outreach status</div>'
        f'<fieldset><legend class="step-title">Which conversation? <span aria-label="Required">*</span></legend><div class="people">{who}</div></fieldset>'
        f'<fieldset><legend class="step-title">What happened? <span aria-label="Required">*</span></legend><div class="people">{what}</div></fieldset>{warn}'
        '<div class="notice">An offered referral stays Offered until you confirm it. Confirming stops redundant referral prompts; applying is still yours to mark.</div></section>'
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="review">Review →</button></div></form>'),
        STATUS_PATH, "Record reply or referral")


def _status_review(j, fields, person, status):
    return S.page("Review outreach status", "Your confirmation is required before anything is recorded.", (
        f'<form method="post" action="/referral">{S._hidden(fields)}<section class="section"><div class="eyebrow">Review action</div>'
        f'<dl class="review"><dt>Company</dt><dd>{e(j["company"])}</dd><dt>Role</dt><dd>{e(j["role"])}</dd><dt>Conversation</dt><dd>{e(person)}</dd><dt>Record</dt><dd>{e(status)}</dd></dl>'
        '<div class="notice alert">Confirming updates the recorded conversation; it is shown as done only after the canonical record is read back.</div></section>'
        '<div class="actions"><button class="btn" name="step" value="edit" formnovalidate>← Edit</button><button class="btn primary" name="step" value="confirm">Confirm</button></div></form>'),
        STATUS_PATH, "Record reply or referral")


def _status_stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.page(title, "A status counts only after the canonical record is read back.",
                                f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                f'<div class="notice {css}">{e(detail)}</div></section>', STATUS_PATH, "Record reply or referral"), ()


def _people_sign(secret, login, pid, st, exp, people):
    return {**{f"l{i}": name for i, name in enumerate(people)}, "lm": _mac(secret, "q", login, pid, st, exp, *people)}


def _status_get(req, who, secrets):
    pid = D._page_id(req["query"].get("j"))
    if not pid:
        return _status_stop(400, "Invalid link", "No job selected", "Open this screen from a Job Ledger job.", "Nothing was changed.")
    j = job(secrets["reader"], secrets["config"], pid)
    people = conversations_on_page(secrets["reader"], j["id"])
    if not people:
        return _status_stop(409, "Nothing to update", "No recorded conversation", "Mark someone as sent on this job first.", "Nothing was changed.")
    exp, st = str(int(_now().timestamp()) + TTL), _fp(j)
    fields = {"j": j["id"], "co": j["company"], "ro": j["role"], "exp": exp, "st": st, "t": _mac(secrets["link"], "r", who["login"], j["id"], st, exp)}
    return 200, HTML, _status_form(j, {**fields, **_people_sign(secrets["link"], who["login"], j["id"], st, exp, people)}, people), ()


def _status_post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    pid, step, exp, st = D._page_id(form.get("j")), form.get("step"), form.get("exp", ""), form.get("st", "")
    bad = lambda: _status_stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")   # noqa: E731
    if not pid or step not in ("cancel", "edit", "review", "confirm") or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not _same(form.get("t"), _mac(secret, "r", login, pid, st, exp)):
        return bad()
    if step == "cancel":
        return _status_stop(200, "Cancelled", "No changes made", "Nothing was recorded.", "Nothing was written.", "")
    shown = {"id": pid, "company": form.get("co", ""), "role": form.get("ro", "")}
    people = []
    while f"l{len(people)}" in form and len(people) < PEOPLE_MAX:
        people.append(form[f"l{len(people)}"])
    if not _same(_fp(shown), st) or not _same(form.get("lm"), _people_sign(secret, login, pid, st, exp, people)["lm"]):
        return bad()
    fields = {**{k: form.get(k, "") for k in ("j", "co", "ro", "exp", "st", "t")}, **_people_sign(secret, login, pid, st, exp, people)}
    index, status = form.get("who", ""), form.get("status", "")
    person = people[int(index)] if index.isdigit() and int(index) < len(people) else ""
    if step == "edit":
        return 200, HTML, _status_form(shown, fields, people, picked=index, status=status), ()
    if not person or status not in [value for value, _ in CHOICES]:
        return 400, HTML, _status_form(shown, fields, people, "Choose the conversation and what happened.", index, status), ()
    sign = _mac(secret, "u", login, pid, st, exp, person, status)
    if step == "review":
        return 200, HTML, _status_review(shown, {**fields, "who": index, "status": status, "c": sign}, person, status), ()
    if not _same(form.get("c"), sign):
        return _status_stop(403, "Not accepted", "This confirmation is not valid", "Review it again.", "Nothing was changed.", "danger")
    config, token = secrets["config"], secrets["notion"].decode()
    current = job(Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]}), config, pid)
    if _fp(current) != st:
        return _status_stop(409, "Conflict", "Job changed", "The job changed since this form was opened.", "No write attempted. Reload the job and review it again.")
    try:
        recruiters = Client({"NOTION_API_TOKEN": token, "NOTION_RECRUITERS_DATA_SOURCE_ID": config.get("recruiters") or ""}, source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")
        done = O.set_conversation(recruiters, current, person, status, config["ledger"])
    except NotionError as error:
        code = str(error)[:60]
        if code in ("OUTREACH_SCHEMA_MISSING", "OUTREACH_AMBIGUOUS", "NOTION_CONFIG_MISSING", "OUTREACH_STATUS_UNAVAILABLE", "OUTREACH_NOT_RECORDED"):
            return _status_stop(409, "Not changed", "Status was not recorded", "This status is not enabled yet, or the conversation is not recorded.", f"Refused: {code}. Nothing was changed.")
        return _status_stop(500, "Unavailable", "Could not verify the status", "Do not assume it was recorded or left unchanged.", f"Status: DEGRADED · {code}", "danger")
    if done.get("verified") is not True:
        return _status_stop(500, "Unavailable", "Could not verify the status", "Do not assume it was recorded or left unchanged.", "Status: DEGRADED · not verified", "danger")
    head = f"{current['company']} — {current['role']} · {person}"
    if done["status"] == "recorded":
        secrets.get("after", []).append("recruiters")
        return _status_stop(200, "Recorded", f"Recorded: {status}", "The canonical record was written and read back.", f"{head} · {status}.", "ok")
    if done["status"] == "already_recorded":
        return _status_stop(200, "Already recorded", "Already recorded", "This status was recorded before; nothing was changed.", f"{head} · {done['action']}.", "")
    return _status_stop(409, "Not changed", "Nothing was changed", "The conversation is closed, or already further along than this status.", f"{head} · currently {done['action'] or 'no status'}.")


def handle_status(req, who, secrets):
    try:
        return (_status_get if req["method"] == "GET" else _status_post)(req, who, secrets)
    except NotionError as error:
        return _status_stop(409, "Not changed", "This job cannot be updated here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")


# --- LIFE-20 (Jim, Oct 23 2026): close one open recruiter row from the Daily Report's Recruiter Outreach table -------------------------------------------------------------
CLOSE_PATH = "Daily Report / Recruiter Outreach / Close"
CLOSE_CHOICES = (("Completed", "Done — I have handled it"), ("Dismissed", "Not relevant — dismiss it"))


def _recruiters(secrets):
    config = secrets["config"]
    return Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_RECRUITERS_DATA_SOURCE_ID": config.get("recruiters") or ""}, source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")


def _row_fp(r):
    return _mac(b"fp", r["page_id"], r["person"], r["company"], r["role"], r["action"])[:16]


def _row_section(r):
    return ('<section class="section"><div class="eyebrow">Recruiter</div><div class="jobhead"><div>'
            f'<h2>{e(r["person"] or "Name not recorded")}</h2><div class="company">{e(r["company"] or "Company not recorded")} · {e(r["role"] or "Role not recorded")}</div></div></div>'
            f'<div class="fine">Currently: {e(r["action"] or "no status")}</div></section>')


def _close_form(r, fields, notice="", status=""):
    warn = f'<div class="notice alert">{e(notice)}</div>' if notice else ""
    what = "".join(f'<label class="reason"><input type="radio" name="status" value="{e(value)}" required{" checked" if status == value else ""}><span><strong>{e(label)}</strong></span></label>' for value, label in CLOSE_CHOICES)
    return S.page("Close recruiter record", "Close it only when nothing is left to do. LIFE OS never contacts anyone.", _row_section(r) + (
        f'<form method="post" action="/recruiter">{S._hidden(fields)}<section class="section"><div class="eyebrow">Action · Close recruiter record</div>'
        f'<fieldset><legend class="step-title">How should it close? <span aria-label="Required">*</span></legend><div class="people">{what}</div></fieldset>{warn}'
        '<div class="notice">Closing sets this one record\'s Action Status; it leaves the Recruiter Outreach table on the next refresh. Nothing else changes.</div></section>'
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="review">Review →</button></div></form>'),
        CLOSE_PATH, "Close recruiter record")


def _close_review(r, fields, status):
    return S.page("Review close", "Your confirmation is required before anything is recorded.", (
        f'<form method="post" action="/recruiter">{S._hidden(fields)}<section class="section"><div class="eyebrow">Review action</div>'
        f'<dl class="review"><dt>Recruiter</dt><dd>{e(r["person"])}</dd><dt>Company</dt><dd>{e(r["company"] or "Not recorded")}</dd><dt>Role</dt><dd>{e(r["role"] or "Not recorded")}</dd>'
        f'<dt>Record</dt><dd>{e(status)}</dd></dl>'
        '<div class="notice alert">Confirming closes this record; it is shown as done only after the canonical record is read back.</div></section>'
        '<div class="actions"><button class="btn" name="step" value="edit" formnovalidate>← Edit</button><button class="btn primary" name="step" value="confirm">Confirm</button></div></form>'),
        CLOSE_PATH, "Close recruiter record")


def _close_stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.page(title, "A close counts only after the canonical record is read back.",
                                f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                f'<div class="notice {css}">{e(detail)}</div></section>', CLOSE_PATH, "Close recruiter record"), ()


def _close_get(req, who, secrets):
    pid = D._page_id(req["query"].get("r"))
    if not pid:
        return _close_stop(400, "Invalid link", "No recruiter selected", "Open this screen from the Recruiter Outreach table.", "Nothing was changed.")
    if not secrets["config"].get("recruiters"):
        return _close_stop(409, "Not changed", "Not configured", "The Recruiters database is not configured for this service.", "Refused: NOTION_CONFIG_MISSING. Nothing was changed.")
    r = store.own_row(secrets["reader"], pid, secrets["config"]["recruiters"])          # GET reads through the gate's read-only client
    if r["done"] or not r["active"] or r["action"] not in (store.NEEDS_ACTION, store.WAITING):
        return _close_stop(409, "Nothing to close", "This record is not open", "It is already closed or not an action item.", f"Currently: {r['action'] or 'no status'}. Nothing was changed.", "")
    exp, st = str(int(_now().timestamp()) + TTL), _row_fp(r)
    fields = {"r": pid, "pe": r["person"], "co": r["company"], "ro": r["role"], "ac": r["action"], "exp": exp, "st": st, "t": _mac(secrets["link"], "k", who["login"], pid, st, exp)}
    return 200, HTML, _close_form(r, fields), ()


def _close_post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    pid, step, exp, st = D._page_id(form.get("r")), form.get("step"), form.get("exp", ""), form.get("st", "")
    bad = lambda: _close_stop(403, "Not accepted", "This form is no longer valid", "Reload it from the Recruiter Outreach table.", "Nothing was changed.", "danger")   # noqa: E731
    if not pid or step not in ("cancel", "edit", "review", "confirm") or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not _same(form.get("t"), _mac(secret, "k", login, pid, st, exp)):
        return bad()
    if step == "cancel":
        return _close_stop(200, "Cancelled", "No changes made", "Nothing was recorded.", "Nothing was written.", "")
    shown = {"page_id": pid, "person": form.get("pe", ""), "company": form.get("co", ""), "role": form.get("ro", ""), "action": form.get("ac", "")}
    if not _same(_row_fp(shown), st):
        return bad()
    fields = {k: form.get(k, "") for k in ("r", "pe", "co", "ro", "ac", "exp", "st", "t")}
    status = form.get("status", "")
    if step == "edit":
        return 200, HTML, _close_form(shown, fields, status=status), ()
    if status not in [value for value, _ in CLOSE_CHOICES]:
        return 400, HTML, _close_form(shown, fields, "Choose how it should close.", status), ()
    sign = _mac(secret, "x", login, pid, st, exp, status)
    if step == "review":
        return 200, HTML, _close_review(shown, {**fields, "status": status, "c": sign}, status), ()
    if not _same(form.get("c"), sign):
        return _close_stop(403, "Not accepted", "This confirmation is not valid", "Review it again.", "Nothing was changed.", "danger")
    recruiters = _recruiters(secrets)
    if _row_fp(store.own_row(recruiters, pid)) != st:
        return _close_stop(409, "Conflict", "Record changed", "The record changed since this form was opened.", "No write attempted. Reload it and review it again.")
    try:
        done = store.close_row(recruiters, pid, status)
    except NotionError as error:
        return _close_stop(500, "Unavailable", "Could not verify the close", "Do not assume it was recorded or left unchanged.", f"Status: DEGRADED · {str(error)[:60]}", "danger")
    head = f"{shown['person']} · {shown['company'] or 'Company not recorded'}"
    if done.get("verified") is not True:
        return _close_stop(500, "Unavailable", "Could not verify the close", "Do not assume it was recorded or left unchanged.", "Status: DEGRADED · not verified", "danger")
    if done["status"] == "recorded":
        secrets.get("after", []).append("recruiters")
        return _close_stop(200, "Recorded", f"Closed: {status}", "The canonical record was written and read back.", f"{head} · {status}.", "ok")
    if done["status"] == "already_recorded":
        return _close_stop(200, "Already recorded", "Already closed", "This record was closed that way before; nothing was changed.", f"{head} · {done['action']}.", "")
    return _close_stop(409, "Not changed", "Nothing was changed", "The record is no longer open.", f"{head} · currently {done['action'] or 'no status'}.")


def handle_close(req, who, secrets):
    try:
        return (_close_get if req["method"] == "GET" else _close_post)(req, who, secrets)
    except NotionError as error:
        return _close_stop(409, "Not changed", "This record cannot be closed here", "The request was refused before anything was written.", f"Refused: {str(error)[:60]}")


# ---------- LIFE-35 (RECR-2.2): the recruiter brief, a read-only page built from the records on file when it is opened; nothing is stored or written ----------
BRIEF_PATH = "Daily Report / Recruiter Outreach / Brief"
NETWORK_SHOWN = 3
CHANNELS = (("mail.google.com", "Gmail"), ("outlook.office.com", "Outlook"), ("outlook.office365.com", "Outlook"), ("outlook.live.com", "Outlook"), ("linkedin.com", "LinkedIn"))   # = outreach_card.CHANNELS (a test holds them equal; that module is not in the Close release)


def _when(text):
    """An ISO date or date-time from the row -> 'Oct 24, 2026' (a time, when recorded, in Central Time), or ''."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if len(text) <= 10:
        return f"{moment:%b} {moment.day}, {moment.year}"
    local = moment.astimezone(ZoneInfo("America/Chicago"))
    return f"{local:%b} {local.day}, {local.year}, {local:%-I:%M %p} CT"


def _https(url):
    url = (url or "").strip()
    return url if url.startswith("https://") and " " not in url else None


def brief_facts(reader, config, row):
    """-> the facts a brief shows, each read from its own record. The Ledger job is the row's one `Job` relation (two is a mismatch, none is not linked); the
    Hiring Pipeline page is the one Active or Retired page for that same job's company and role; Network leads are that job page's own (dismissed ones left out).
    A source that cannot be read in full is `unreadable`, never `none`."""
    out = {"ledger": "not_linked", "job": None, "fit": None, "pipeline": "not_linked", "page": None, "network": "not_linked", "leads": []}
    if len(row["jobs"]) > 1:
        out.update(ledger="mismatch", pipeline="mismatch", network="mismatch")
        return out
    if not row["jobs"]:
        return out
    pid = D._page_id(row["jobs"][0])
    try:
        page = reader.get_page(pid)
        props = page.get("properties") or {}
        parent = D._page_id((page.get("parent") or {}).get("data_source_id"))
        if parent != D._page_id(config.get("ledger")) or page.get("in_trash") or page.get("archived"):
            raise NotionError("NOT_LEDGER")
        out["job"] = {"id": pid, "company": D._text(props.get("Company"), "rich_text"), "role": D._text(props.get("Job"), "title")}
        out["fit"] = D._text(props.get("Why It Fits"), "rich_text") or None
        out["ledger"] = "linked"
    except NotionError:
        out.update(ledger="unreadable", pipeline="unreadable", network="unreadable")
        return out
    from lifeos.jobs import hiring_pipeline
    from lifeos.hiring import models as M
    found, status = hiring_pipeline.snapshot(reader, config["pipeline"])
    if status != "ok":
        out["pipeline"] = "unreadable"
    else:
        same = [o for o in found if M.same(o.company, o.role, out["job"]["company"], out["job"]["role"])]
        out["pipeline"], out["page"] = ("linked", same[0]) if len(same) == 1 else ("mismatch" if same else "none", None)
    try:
        out["leads"] = [lead["name"] for lead in N.leads_on_page(reader, pid, NETWORK_SHOWN)]
        out["network"] = "linked" if out["leads"] else "none"
    except NotionError:
        out["network"] = "unreadable"
    return out


STATE_TEXT = {"not_linked": "not linked to a Job Ledger job", "mismatch": "more than one match: not shown", "unreadable": "unreadable this time", "none": "none on file"}


def brief_lines(row, facts):
    """-> [(label, text, source link or None)]: seven fixed lines, each from one record, nothing derived beyond dates and counts; no score, status or task is made."""
    link = _https(row["source_url"])
    host = link.split("/")[2].lower() if link else ""
    channel = next((name for domain, name in CHANNELS if host == domain or host.endswith("." + domain)), None) or row["medium"].strip() or "Channel not recorded"
    first, last = _when(row["first_contact"]), _when(row["last_contact"])
    job, page = facts["job"], facts["page"]
    lines = [("Who", " · ".join(x for x in (row["person"] or "Name not recorded", row["company"], channel) if x), _https(row["contact"]) or link),
             ("Contact", " · ".join(x for x in (f"first {first}" if first else "", f"last {last}" if last else "", row["what"]) if x) or "No contact recorded", link),
             ("Role", (" · ".join(x for x in (job["company"], job["role"]) if x) + " (Job Ledger)") if job else
              " · ".join(x for x in (row["client"], row["role"]) if x) + f" · {STATE_TEXT[facts['ledger']]}", f"https://www.notion.so/{job['id']}" if job else None),
             ("Fit", facts["fit"] or ("Fit not available" if job else STATE_TEXT[facts["ledger"]]), f"https://www.notion.so/{job['id']}" if job and facts["fit"] else None),
             ("Interview", f"{page.title} · {page.rounds} interview page(s) · {'Active' if page.section != 'RETIRED' else 'Retired'}" if page else STATE_TEXT[facts["pipeline"]],
              f"https://www.notion.so/{page.page_id.replace('-', '')}" if page else None),
             ("Network", ", ".join(facts["leads"]) if facts["leads"] else STATE_TEXT[facts["network"]], f"https://www.notion.so/{job['id']}" if job and facts["leads"] else None),
             ("Status", " · ".join(x for x in (row["action"] or "No status", f"follow up {_when(row['follow_up'])}" if row["follow_up"] else "") if x),
              f"https://www.notion.so/{D._page_id(row['page_id'])}" if D._page_id(row["page_id"]) else None)]
    return lines


def _brief_page(row, lines):
    items = "".join(f'<tr><th>{e(label)}</th><td>{e(text)}</td><td>{f"<a href=\"{e(url)}\" target=\"_blank\" rel=\"noopener\">Source ↗</a>" if url else "—"}</td></tr>'
                    for label, text, url in lines)
    return S.page("Recruiter brief", "Built from the records on file as this page opened. Nothing is stored, written or sent.",
                  f'<section class="section"><div class="eyebrow">Recruiter brief</div><h3 class="step-title">{e(row["person"] or "Recruiter")}</h3>'
                  f'<table class="brief">{items}</table></section>', BRIEF_PATH, "Recruiter brief")


def handle_brief(req, who, secrets):
    """GET /brief?r=<Recruiters row>: read only, through the gate's read-only client. Anything else is refused; nothing is ever written."""
    if req["method"] != "GET":
        return _close_stop(405, "Not allowed", "The brief is read only", "Open it from its link.", "Nothing was changed.")
    pid = D._page_id(req["query"].get("r"))
    if not pid:
        return _close_stop(400, "Invalid link", "No recruiter selected", "Open the brief from the Recruiter Outreach table.", "Nothing was changed.")
    if not secrets["config"].get("recruiters"):
        return _close_stop(409, "Not available", "Not configured", "The Recruiters database is not configured for this service.", "Refused: NOTION_CONFIG_MISSING.")
    try:
        row = store.own_row(secrets["reader"], pid, secrets["config"]["recruiters"])
        return 200, HTML, _brief_page(row, brief_lines(row, brief_facts(secrets["reader"], secrets["config"], row))), ()
    except NotionError as error:
        return _close_stop(409, "Not available", "This brief cannot be shown", "The record could not be read in full.", f"Refused: {str(error)[:60]}")
