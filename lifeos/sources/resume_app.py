"""LIFE-87 (D229): Build resume on the private Close service (same Edge gate, signing key and fresh-read rule as Mark as Sent, D221). Jim opens one exact open Job Ledger
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
    """Upload the .docx, append it to the job's page as a file block, and read it back. -> True only when the page now holds a file block with this name."""
    upload_id = (upload or notion_files.upload)(token, filename, docx, DOCX)              # looked up at call time
    caption = f"Tailored resume · built {now.strftime('%b %-d, %-I:%M %p')} CT · LIFE OS"
    client.call_once("PATCH", f"/blocks/{pid}/children", {"children": [{"object": "block", "type": "file", "file": {
        "type": "file_upload", "file_upload": {"id": upload_id}, "name": filename, "caption": [{"type": "text", "text": {"content": caption}}]}}]})
    return any(b.get("type") == "file" and ((b.get("file") or {}).get("name") == filename) for b in _children(client, pid))


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
        '<div class="actions"><button class="btn link" name="step" value="cancel" formnovalidate>Cancel</button><button class="btn primary" name="step" value="build">Build resume</button></div></form>'),
        PATH, "Build resume"), ()


def _post(req, who, secrets):
    form, secret, login = req["form"], secrets["link"], who["login"]
    pid, step, exp, st = D._page_id(form.get("j")), form.get("step"), form.get("exp", ""), form.get("st", "")
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
        verified = attach(client, token, pid, filename, docx, _now())
    except NotionError as error:
        return _stop(500, "Unavailable", "Could not attach the resume", "Do not assume it is on the job's page.", f"Status: DEGRADED · {str(error)[:60]}", "danger")
    if not verified:
        return _stop(500, "Unavailable", "Could not verify the attachment", "Do not assume it is on the job's page.", "Status: DEGRADED · not read back", "danger")
    return _stop(200, "Attached", "Resume attached", "It is on the job's Ledger page and was read back.",
                 f"{current['company']} · {current['role']} · {filename}", "ok")


def handle(req, who, secrets):
    try:
        return (_get if req["method"] == "GET" else _post)(req, who, secrets)
    except NotionError as error:
        return _stop(409, "Not changed", "A resume cannot be built for this job here", "The request was refused before anything was run or written.", f"Refused: {str(error)[:60]}")
