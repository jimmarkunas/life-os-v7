"""LIFE-87 (D234): Build resume on the private Close service (same Edge gate, signing key and fresh-read rule as Mark as Sent, D221). Jim opens one exact open Job Ledger
job and presses Build. The confirm POST re-reads the job, takes its description from the page V7 published (the v7-jd sections), and hands it to the Oracle resume
worker over its private socket. The worker writes the resume with Luna under Jim's own resume rules and evidence, checks it with the claims guard, and returns the
.docx. This attaches the .docx to the job's own Ledger page and shows success only after reading it back. GET never writes. Fixed codes only; no resume text,
evidence or contact detail ever reaches a log."""
import base64
from html import escape as e
import json
import re
import socket

from lifeos.hiring import close_screen as S, decision as D
from lifeos.platform import notion_files
from lifeos.platform.notion_client import Client, NotionError
from .close_app import TTL, HTML, _mac, _now, _same
from .outreach_app import _fp, _job_section, job

PATH = "Job Ledger / Build resume"
WORKER, WORKER_WAIT = "/run/lifeos-resume/worker.sock", 140      # the worker waits for Cloud Fit's one-call lock, then about a minute of Luna
MAX_REPLY, MAX_DESCRIPTION, MIN_DESCRIPTION = 4 * 1024 * 1024, 24000, 200
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILENAME = re.compile(r"Jim_Markunas_(Resume|CV)_[A-Za-z0-9_]{1,80}\.docx\Z")
MARKER = "v7-jd:1"                                              # publish's first body line (lifeos.jobs.publish.body_blocks)
RESUME_COLUMN = "Resume"                                        # the Ledger's Files column: the latest resume built for this job
SUBMITTED_COLUMN = "Submitted Résumé"                           # LIFE-88 E10: the file Jim sent (lifeos.interview.view.SUBMITTED; a test holds them equal)
DRIVE_FILE = re.compile(r"https://(?:drive\.google\.com/file|docs\.google\.com/document)/d/([A-Za-z0-9_-]{10,200})/(?:view|edit)")   # Drive links a .docx as docs.google.com/document/.../edit (live, Oct 25)
SHOWN_BUILDS = 5                                               # the newest Drive builds offered as "applied with this one"
LOCK_REASON = "Submitted with the application for this job (LIFE OS)"
TEXT = ("heading_2", "paragraph", "bulleted_list_item")
REASONS = {"QUOTA_RESERVE": "Luna's allowance is at your 50% reserve; it resets on its own (see the AI Capacity card).",
           "METER_UNAVAILABLE": "Luna's usage meter could not be read, so nothing was run.",
           "QUOTA_UNCONFIGURED": "Luna's usage policy is missing on Oracle, so nothing was run.",
           "BUSY": "Luna stayed busy with Cloud Fit for too long. Try again in a minute.",
           "REJECTED": "Luna's draft broke your resume rules twice, so nothing was attached.",
           "PURGE_FAILED": "Luna's private transcript could not be cleared, so the result was not used.",
           "USAGE_UNAVAILABLE": "Luna's usage could not be measured, so nothing was run.",
           "MODEL_DRIFT": "A different model answered, so the result was not used."}


def _stop(status, eyebrow, title, text, detail, css="alert"):
    return status, HTML, S.page(title, "A resume counts only after it is attached to the job's Ledger page and read back.",
                                f'<section class="section"><div class="eyebrow">{e(eyebrow)}</div><h3 class="step-title">{e(title)}</h3><p class="lead">{e(text)}</p>'
                                f'<div class="notice {css}">{e(detail)}</div></section>', PATH, "Build resume"), ()


def _children(client, block_id):
    out, cursor = [], None
    while True:
        page = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += page.get("results") or []
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")


def _plain(block):
    return "".join(r.get("plain_text") or "" for r in ((block.get(block.get("type")) or {}).get("rich_text") or []))


def description(client, pid):
    """The job's own description from its Ledger page: the sections V7 published after its v7-jd marker, in order, up to the first block that is not one of them."""
    text, started = [], False
    for block in _children(client, pid):
        kind, line = block.get("type"), _plain(block)
        if not started:
            started = kind == "paragraph" and line.startswith(MARKER)
            continue
        if kind not in TEXT:
            break
        text.append(f"- {line}" if kind == "bulleted_list_item" else line)
    return "\n".join(t for t in text if t.strip())[:MAX_DESCRIPTION]


def location(client, pid):
    props = client.get_page(pid).get("properties") or {}
    return D._text(props.get("Location / Work Mode"), "rich_text")


def ask_worker(payload, path=WORKER, wait=WORKER_WAIT):
    """One request to the Oracle resume worker -> its reply {status, filename?, docx?, codes?}, or {"status": "WORKER_UNAVAILABLE"}."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(wait)
            s.connect(path)
            s.sendall(json.dumps(payload).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n") and len(data) < MAX_REPLY:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data)
        return reply if isinstance(reply, dict) and isinstance(reply.get("status"), str) else {"status": "WORKER_OFF_CONTRACT"}
    except (OSError, ValueError):
        return {"status": "WORKER_UNAVAILABLE"}


def attach(client, token, pid, filename, docx, now, upload=None):
    """Upload the .docx twice (Notion does not promise one upload can be attached in two places): append one copy to the job's page as a file block (every
    build stays there, newest last) and set the Ledger's Resume column to the other (the latest build, visible in the table). Then read both back.
    -> True only when the page holds a file block with this name AND its Resume column is exactly this file."""
    send = upload or notion_files.upload                                                       # looked up at call time
    caption = f"Tailored resume · built {now.strftime('%b %-d, %-I:%M %p')} CT · LIFE OS"
    block_upload = send(token, filename, docx, DOCX)
    client.call_once("PATCH", f"/blocks/{pid}/children", {"children": [{"object": "block", "type": "file", "file": {
        "type": "file_upload", "file_upload": {"id": block_upload}, "name": filename, "caption": [{"type": "text", "text": {"content": caption}}]}}]})
    column_upload = send(token, filename, docx, DOCX)
    client.call_once("PATCH", f"/pages/{pid}", {"properties": {RESUME_COLUMN: {"files": [{"type": "file_upload", "file_upload": {"id": column_upload}, "name": filename}]}}})
    on_page = any(b.get("type") == "file" and ((b.get("file") or {}).get("name") == filename) for b in _children(client, pid))
    column = [f.get("name") for f in ((client.get_page(pid).get("properties") or {}).get(RESUME_COLUMN) or {}).get("files") or []]
    return on_page and column == [filename]


def attach_drive(client, drive, pid, filename, docx, now):
    """Jim, Oct 24 2026: with the Drive credential the .docx lives in Drive (My Drive / LIFE OS / Résumés, as itself, never converted) and Notion
    holds links: one external file block on the job's page (every build stays there, newest last) and the Ledger's Resume column set to this
    build. The Drive file is tagged with the job, so a later "applied with this résumé" step can list this job's builds. Then all three are
    read back. -> True only when Drive holds these exact bytes, the page links this file and the Resume column is exactly this file."""
    caption = f"Tailored resume · built {now.strftime('%b %-d, %-I:%M %p')} CT · Google Drive · LIFE OS"
    meta = drive.upload(drive.folder(), filename, docx, DOCX, {"lifeos": "resume", "job": pid.replace("-", "")})
    link = meta["webViewLink"]
    client.call_once("PATCH", f"/blocks/{pid}/children", {"children": [{"object": "block", "type": "file", "file": {
        "type": "external", "external": {"url": link}, "name": filename, "caption": [{"type": "text", "text": {"content": caption}}]}}]})
    client.call_once("PATCH", f"/pages/{pid}", {"properties": {RESUME_COLUMN: {"files": [{"type": "external", "external": {"url": link}, "name": filename}]}}})
    stored = drive.get(meta["id"])
    on_page = any(b.get("type") == "file" and ((b.get("file") or {}).get("external") or {}).get("url") == link for b in _children(client, pid))
    column = [(f.get("name"), (f.get("external") or {}).get("url")) for f in ((client.get_page(pid).get("properties") or {}).get(RESUME_COLUMN) or {}).get("files") or []]
    return stored.get("md5Checksum") == meta["md5Checksum"] and not stored.get("trashed") and on_page and column == [(filename, link)]


def page_url(pid):
    return "https://www.notion.so/" + pid.replace("-", "")


def drive_builds(client, pid):
    """The job page's Drive builds, newest last: (file id, name, link) from its external file blocks (attach_drive writes them)."""
    out = []
    for b in _children(client, pid):
        f = b.get("file") or {} if b.get("type") == "file" else {}
        url = ((f.get("external") or {}).get("url")) or ""
        hit = DRIVE_FILE.match(url)
        if hit and f.get("name"):
            out.append((hit.group(1), f["name"], url))                    # the link exactly as the page holds it
    return out


def recorded(client, pid):
    """The job's Submitted Résumé entries as (name, link)."""
    files = ((client.get_page(pid).get("properties") or {}).get(SUBMITTED_COLUMN) or {}).get("files") or []
    return [(f.get("name"), (f.get("external") or {}).get("url")) for f in files]


def _submit_forms(secret, login, j, st, exp, builds):
    """One signed "applied with this one" form per Drive build (newest first)."""
    out = ""
    for fid, name, _ in reversed(builds[-SHOWN_BUILDS:]):
        fields = {"j": j["id"], "co": j["company"], "ro": j["role"], "exp": exp, "st": st, "f": fid, "n": name, "a": _mac(secret, "a", login, j["id"], st, exp, fid, name)}
        out += (f'<form method="post" action="/resume">{S._hidden(fields)}<div class="actions"><span class="lead">{e(name)}</span>'
                '<button class="btn" name="step" value="submit">I applied with this résumé</button></div></form>')
    return out


def _applied_section(reader, secret, login, j, st, exp):
    """Jim, Oct 24: record which built résumé was actually sent. Shown under Build; read only here."""
    try:
        done, builds = recorded(reader, j["id"]), drive_builds(reader, j["id"])
    except NotionError:                                                    # Build still works; only this list is unavailable this time
        return '<section class="section"><div class="notice alert">Submitted résumé: unavailable this time. Reload to try again.</div></section>'
    if done:
        return ('<section class="section"><div class="eyebrow">Submitted résumé</div>'
                f'<div class="notice ok">Recorded: {e(", ".join(n or "" for n, _ in done))}. Change it in the job\'s Submitted Résumé field in Notion.</div></section>')
    if not builds:
        return ""
    return ('<section class="section"><div class="eyebrow">Already applied?</div><p class="lead">Record which résumé you sent. It is locked read-only in Google Drive and '
            'linked in the job\'s Submitted Résumé field; nothing is sent to anyone.</p>' + _submit_forms(secret, login, j, st, exp, builds) + '</section>')


def _submit(form, who, secrets, step):
    """Review, then record: the Drive file must be a build of THIS job (its tag) still linked on its page; it is locked read-only, then the Ledger's
    Submitted Résumé field is set to it, and both are read back. Nothing else is written; replay of the same file writes nothing."""
    pid, exp, st, fid, name = D._page_id(form.get("j")), form.get("exp", ""), form.get("st", ""), form.get("f", ""), form.get("n", "")
    if (not pid or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not fid or not name
            or not _same(form.get("a"), _mac(secrets["link"], "a", who["login"], pid, st, exp, fid, name))
            or not _same(_fp({"id": pid, "company": form.get("co", ""), "role": form.get("ro", "")}), st)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")
    config = secrets["config"]
    client = Client({"NOTION_API_TOKEN": secrets["notion"].decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]})
    current = job(client, config, pid)
    if _fp(current) != st:
        return _stop(409, "Conflict", "Job changed", "The job changed since this page was opened.", "Nothing was changed. Reload the job and try again.")
    link = next((url for f, n, url in drive_builds(client, pid) if (f, n) == (fid, name)), None)
    if not link:
        return _stop(409, "Not recorded", "That résumé is no longer on the job's page", "Reload the job and pick again.", "Nothing was changed.")
    done = recorded(client, pid)
    if done == [(name, link)]:
        return _stop(200, "Already recorded", "This résumé is already recorded as submitted", f"{name}", "Nothing was changed.", "ok")
    if done:
        return _stop(409, "Not recorded", "A submitted résumé is already recorded", "Change it in the job's Submitted Résumé field in Notion.", "Nothing was changed.")
    if step == "submit":
        fields = {k: form.get(k, "") for k in ("j", "co", "ro", "exp", "st", "f", "n", "a")}
        return 200, HTML, S.page("Record submitted résumé", "Which résumé you actually sent with this application.", _job_section(current) + (
            f'<form method="post" action="/resume">{S._hidden(fields)}<section class="section"><div class="eyebrow">Review</div>'
            f'<p class="step-title">{e(name)}</p><p class="lead">It will be locked read-only in Google Drive and set as this job\'s Submitted Résumé. Nothing is sent to anyone.</p></section>'
            '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="record">Record it</button></div></form>'),
            PATH, "Build resume"), ()
    if not secrets.get("gdrive"):
        return _stop(409, "Not recorded", "Google Drive is not connected", "The Drive credential is not installed on the Close service.", "Nothing was changed.")
    try:
        return _record(client, current, gdrive_client(secrets["gdrive"]), pid, fid, name, link)
    except RuntimeError as error:                                          # NotionError or gdrive.DriveError: fixed codes only
        if not isinstance(error, NotionError) and type(error).__name__ != "DriveError":
            raise
        return _stop(500, "Unavailable", "Could not record the submitted résumé", "Do not assume it is recorded. The Drive file may already be locked read-only; "
                     "trying again is safe.", f"Status: DEGRADED · {str(error)[:60]}", "danger")


def gdrive_client(raw):
    from lifeos.platform import gdrive                                     # imported here, so a release without the Drive client still serves every other route
    return gdrive.Drive(raw)


def _record(client, current, drive, pid, fid, name, link):
    from lifeos.platform.gdrive import locked as is_locked
    meta = drive.get(fid)
    tags = meta.get("appProperties") or {}
    if meta.get("trashed") or meta.get("name") != name or tags.get("lifeos") != "resume" or tags.get("job") != pid.replace("-", ""):
        return _stop(409, "Not recorded", "That file is not a résumé built for this job", "Reload the job and pick again.", "Nothing was changed.")
    locked = drive.lock(fid, LOCK_REASON)
    client.call_once("PATCH", f"/pages/{pid}", {"properties": {SUBMITTED_COLUMN: {"files": [{"type": "external", "external": {"url": link}, "name": name}]}}})
    again = drive.get(fid)
    if not (is_locked(locked) and is_locked(again) and again.get("md5Checksum") == meta.get("md5Checksum") and recorded(client, pid) == [(name, link)]):
        return _stop(500, "Unavailable", "Could not verify the submitted résumé", "Do not assume it is recorded.", "Status: DEGRADED · not read back", "danger")
    return 200, HTML, S.page("Submitted résumé recorded", "Which résumé you actually sent with this application.", (
        '<section class="section"><div class="eyebrow">Recorded</div><h3 class="step-title">Submitted résumé recorded</h3>'
        f'<p class="lead">{e(current["company"])} · {e(current["role"])}</p>'
        f'<div class="notice ok">{e(name)} · locked read-only in Google Drive and set as the job\'s Submitted Résumé · read back.</div></section>'
        f'<div class="actions"><a class="btn primary" href="{e(page_url(pid))}" target="_blank" rel="noopener">Open the job in Notion →</a></div>'),
        PATH, "Build resume"), ()


def _attached(current, filename, pid, drive=False):
    """The success screen: what was attached, and a link straight to the job's Ledger page (its Resume column and the file at the bottom of the page)."""
    return 200, HTML, S.page("Resume attached", "A resume counts only after it is attached to the job's Ledger page and read back.", (
        '<section class="section"><div class="eyebrow">Attached</div><h3 class="step-title">Resume attached</h3>'
        f'<p class="lead">{e(current["company"])} · {e(current["role"])}</p>'
        f'<div class="notice ok">{e(filename)} · {"saved in Google Drive (My Drive / LIFE OS / Résumés) and linked " if drive else ""}in the job\'s Resume column and at the bottom of its page · read back.</div></section>'
        f'<div class="actions"><a class="btn primary" href="{e(page_url(pid))}" target="_blank" rel="noopener">Open the job in Notion →</a></div>'),
        PATH, "Build resume"), ()


def _get(req, who, secrets):
    pid = D._page_id(req["query"].get("j"))
    if not pid:
        return _stop(400, "Invalid link", "No job selected", "Open this screen from a Job Ledger job.", "Nothing was changed.")
    j = job(secrets["reader"], secrets["config"], pid)
    exp = str(int(_now().timestamp()) + TTL)
    st = _fp(j)
    fields = {"j": j["id"], "co": j["company"], "ro": j["role"], "exp": exp, "st": st, "t": _mac(secrets["link"], "r", who["login"], j["id"], st, exp)}
    return 200, HTML, S.page("Build resume", "A tailored resume for this exact job, written under your resume rules and attached to its Ledger page.", _job_section(j) + (
        f'<form method="post" action="/resume">{S._hidden(fields)}<section class="section"><div class="eyebrow">Action · Build resume</div>'
        '<p class="lead">Luna writes it from this job\'s description and your career evidence; the claims guard checks every title, date and number before it is built.</p>'
        '<div class="notice">This takes about a minute and a half. Keep this page open; the .docx is attached to the job\'s Ledger page and read back before you see it here.</div></section>'
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="build" data-wait="90" data-label="Building your résumé" '
        'data-late="If nothing appears by 3:30, look on the job\'s Ledger page (or My Drive / LIFE OS / Résumés) before trying again.">Build resume</button></div></form>'
        + _applied_section(secrets["reader"], secrets["link"], who["login"], j, st, exp)), PATH, "Build resume"), ()


def _post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    pid, step, exp, st = D._page_id(form.get("j")), form.get("step"), form.get("exp", ""), form.get("st", "")
    if step in ("submit", "record") or (step == "cancel" and form.get("a")):
        if step == "cancel":
            return _stop(200, "Cancelled", "Nothing recorded", "No résumé was marked as submitted.", "Nothing was written.", "")
        return _submit(form, who, secrets, step)
    if not pid or step not in ("cancel", "build") or not (exp.isascii() and exp.isdigit()) or int(exp) < _now().timestamp() or not _same(form.get("t"), _mac(secret, "r", login, pid, st, exp)):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")
    if step == "cancel":
        return _stop(200, "Cancelled", "No resume built", "Nothing was run or attached.", "Nothing was written.", "")
    if not _same(_fp({"id": pid, "company": form.get("co", ""), "role": form.get("ro", "")}), st):
        return _stop(403, "Not accepted", "This form is no longer valid", "Reload the job from its link.", "Nothing was changed.", "danger")
    config, token = secrets["config"], secrets["notion"].decode()
    client = Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": config["ledger"]})
    current = job(client, config, pid)                                     # the build is authorized only by a fresh read of the same open job
    if _fp(current) != st:
        return _stop(409, "Conflict", "Job changed", "The job changed since this page was opened.", "Nothing was run. Reload the job and try again.")
    text = description(client, pid)
    if len(text) < MIN_DESCRIPTION:
        return _stop(409, "Not built", "This job has no description to tailor to", "Its Ledger page does not carry the published description.", "Refused: JD_MISSING. Nothing was run.")
    reply = ask_worker({"job": {"company": current["company"], "title": current["role"], "location": location(client, pid), "description": text}})
    if reply["status"] != "OK":
        code = reply["status"][:40]
        why = REASONS.get(code, "The resume could not be built.")
        detail = f"Refused: {code}" + (f" · {', '.join(str(c)[:60] for c in (reply.get('codes') or [])[:3])}" if reply.get("codes") else "") + ". Nothing was attached."
        return _stop(409 if code in REASONS else 500, "Not built", "No resume was attached", why, detail, "danger" if code not in REASONS else "alert")
    filename = reply.get("filename") or ""
    try:
        docx = base64.b64decode(reply.get("docx") or "", validate=True)
    except ValueError:
        docx = b""
    if not FILENAME.match(filename) or not docx.startswith(b"PK"):
        return _stop(500, "Unavailable", "The resume came back malformed", "Nothing was attached.", "Status: DEGRADED · WORKER_OFF_CONTRACT", "danger")
    try:
        if secrets.get("gdrive"):                                          # the Drive credential is installed: the file goes to Drive, Notion links it
            verified = attach_drive(client, gdrive_client(secrets["gdrive"]), pid, filename, docx, _now())
        else:
            verified = attach(client, token, pid, filename, docx, _now())
    except RuntimeError as error:                                          # NotionError or gdrive.DriveError: fixed codes only; anything else is the gate's 500
        if not isinstance(error, NotionError) and type(error).__name__ != "DriveError":
            raise
        code = str(error)[:60]
        why = {"GDRIVE_SCOPE_NARROW": "The Drive sign-in predates full access: rerun it (docs/DRIVE_RESUMES.md). Nothing was attached.",
               "GDRIVE_LIFE_OS_FOLDER_MISSING": "No LIFE OS folder at the top of My Drive. Nothing was attached.",
               "GDRIVE_LIFE_OS_FOLDER_AMBIGUOUS": "More than one LIFE OS folder at the top of My Drive. Nothing was attached."}.get(code, "Do not assume it is on the job's page.")
        return _stop(500, "Unavailable", "Could not attach the resume", why, f"Status: DEGRADED · {code}", "danger")
    if not verified:
        return _stop(500, "Unavailable", "Could not verify the attachment", "Do not assume it is on the job's page.", "Status: DEGRADED · not read back", "danger")
    return _attached(current, filename, pid, bool(secrets.get("gdrive")))


def handle(req, who, secrets):
    try:
        return (_get if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        recording = req["method"] == "POST" and req["form"].get("step") in ("submit", "record")
        return _stop(409, "Not changed", "A submitted résumé cannot be recorded for this job here" if recording else "A resume cannot be built for this job here",
                     "The request was refused before anything was run or written.", f"Refused: {str(error)[:60]}")
