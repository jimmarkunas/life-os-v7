"""LIFE-973 (Jim, Nov 25 2026: "fill companies"): the Recruiter Company, Role and Client of a recruiter row, from the one message behind it.

Company, in order: the email signature (a company line), the sender's display name ("Name | Agency"), the sender's domain when it is not a free-mail domain. Role: the subject line's job title,
then the first job title named in the body. Client: when the message names the end client separately from the agency. Only words that appear in the message; nothing inferred. A field the message
does not state is written `Not stated`, so a checked-but-empty field is different from one never checked. A value that is already set is never replaced.
Every answer carries where it came from (signature, display name, domain, subject, body, not stated); the logs carry that label and the row's page id, never a name or a company (public repository)."""
import re

from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError, rich_text
from . import classify as C, notion as store, qualify as Q

NOT_STATED = "Not stated"
FIELDS_BASE = (("company", "Recruiter Company"), ("role", "Role"), ("client", "Client"))


def fields():
    """The fillable fields with their LIVE property names (LIFE-989: Recruiter Company is Company after the rename)."""
    return tuple((f, store.live(n)) for f, n in FIELDS_BASE)
TITLE_WORDS = (r"engineer|developer|architect|manager|director|analyst|consultant|administrator|designer|scientist|specialist|lead|officer|programmer|technician|"
               r"strategist|coordinator|supervisor|owner|head of [A-Za-z]+|vp|vice president|cto|cio|ciso|devops|sre|sdet|qa|tester|writer|editor|producer|planner|advisor|executive")
TITLE = re.compile(rf"\b(?:(?:senior|sr\.?|junior|jr\.?|staff|principal|lead|associate|chief|global|regional|technical|enterprise|cloud|data|software|solutions?|systems?|network|security|"
                   rf"infrastructure|platform|product|program|project|ai|ml|full[- ]stack|front[- ]end|back[- ]end|it|ux|ui)[ /-]+){{0,4}}(?:{TITLE_WORDS})\b(?:\s+(?:i{{1,3}}|iv|v|[1-5]))?", re.I)
SUBJECT_NOISE = re.compile(r"^(?:\s*(?:re|fw|fwd)\s*:\s*)+", re.I)
SEPARATORS = re.compile(r"\s+(?:[|\-–—:@]|at|with|for)\s+")
DISPLAY = re.compile(r"^(?P<n>[^|@\-–—()]{2,40}?)\s*(?:[|@]|\s[-–—]\s|\()\s*(?P<c>[A-Z0-9][A-Za-z0-9&'’.\- ]{1,50}?)\)?\s*$")
SIGNATURE_LINES = 14
MAX_WORDS_ROLE = 9


def _words_in(value, message):
    """True when every alphanumeric word of `value` appears in the message (sender, subject or body), the rule that nothing is invented."""
    haystack = " ".join(str(message.get(k) or "") for k in ("sender_name", "sender_address", "subject", "body")).casefold()
    return all(w in haystack for w in re.findall(r"[a-z0-9]+", value.casefold()))


def _signature_company(message, evidence):
    body = str(message.get("body") or "")
    found = Q._company(evidence) or Q._org_from_signature(body)
    if found:
        return found
    for line in [l.strip() for l in body.splitlines() if l.strip()][-SIGNATURE_LINES:]:       # "Recruiter | Acme" or "Senior Recruiter at Acme" in the closing lines
        m = Q.TITLE_AT.search(line) if len(line) <= 90 else None
        if m:
            return m.group("c").strip()
    return Q._stated_company(body)


def _display_company(message):
    m = DISPLAY.match(str(message.get("sender_name") or "").strip())
    if not m:
        return ""
    name, company = m.group("n").strip(), m.group("c").strip(" .|-")
    if not company or company.casefold() == name.casefold() or "@" in company or company.casefold() in Q.NOT_AN_ORG or len(company.split()) > 5:
        return ""
    return company


def _domain_company(message):
    label = Q._domain_label(message.get("sender_address", ""))
    if not label:
        return ""
    return Q._org_from_domain(message.get("sender_address", ""), message.get("body", "")) or label.capitalize()


def company(message, evidence):
    """-> (value, source) or ("", "")."""
    for source, value in (("signature", _signature_company(message, evidence)), ("display name", _display_company(message)), ("domain", _domain_company(message))):
        if value and _words_in(value, message):
            return value.strip(), source
    return "", ""


def _title_in(text):
    m = TITLE.search(text or "")
    if not m:
        return ""
    words = m.group(0).split()
    return " ".join(words) if len(words) <= MAX_WORDS_ROLE else ""


def _subject_role(subject):
    subject = SUBJECT_NOISE.sub("", str(subject or "")).strip()
    for part in [subject] + SEPARATORS.split(subject):
        hit = _title_in(part)
        if hit:
            return hit if len(part.split()) <= MAX_WORDS_ROLE + 2 else hit
    return ""


def role(message):
    """-> (value, source): the subject's job title first, then the body's sentence pattern, `Role:` label or first title phrase."""
    found = _subject_role(message.get("subject"))
    if found:
        return found, "subject"
    body = str(message.get("body") or "")[:6000]
    found = Q._role("", body)[0] or ""
    if not found:
        for line in body.splitlines():
            hit = _title_in(line) if len(line) <= 160 else ""
            if hit:
                found = hit
                break
    return (found, "body") if found else ("", "")


def client(message):
    found = Q._search(Q.CLIENT, "c", str(message.get("body") or "")[:6000])
    return (found, "body") if found else ("", "")


def derive(message):
    """-> {"company": (value, source), "role": ..., "client": ...}; a field the message does not state is (NOT_STATED, "not stated"). Only words from the message (checked)."""
    shape, _reason, evidence = C.classify(message)
    out = {"company": company(message, evidence), "role": role(message), "client": client(message)}
    for field, (value, source) in list(out.items()):
        if not value or not _words_in(value, message):
            out[field] = (NOT_STATED, "not stated")
    return out


def plan(row, message):
    """-> (props, sources): Notion properties filling only the BLANK fields of `row`, and {field: source} for the fields written. A set value (including `Not stated`) is never touched."""
    found, props, sources = derive(message), {}, {}
    for field, name in fields():
        if str(row[field] or "").strip():
            continue
        value, source = found[field]
        props[name] = {"rich_text": rich_text(value)}
        sources[field] = source
    return props, sources


def apply(notion, row, message, write):
    """Fill one row from its message. -> {field: source} (what was or would be written). One write, read back: the filled fields hold the values and nothing else changed."""
    props, sources = plan(row, message)
    if not props or not write:
        return sources
    values = {f: props[n]["rich_text"][0]["text"]["content"] for f, n in fields() if n in props}
    with fixed(NotionError, store.RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
        store.set_props(notion, row, props, values)
    return sources


def blank_rows(notion):
    """The open (Needs Action or Waiting) rows with at least one blank Company, Role or Client: the rows to fill."""
    rows = store.rows_where(notion, {"or": [{"property": "Action Status", "select": {"equals": s}} for s in (store.NEEDS_ACTION, store.WAITING)]})
    return [r for r in rows if r["active"] and not r["done"] and any(not str(r[f] or "").strip() for f, _ in fields())]


# ---------- LIFE-989: What they want is never empty ----------
ASK_STATUSES = (store.NEEDS_ACTION, store.WAITING, store.REPLIED)


def ask_of(message):
    """-> (the one-sentence ask, used_fallback) for the message behind a row: the extracted ask, else `Reply to their message about <role or subject>.`, else `Reply to their message.` Never empty.
    Only the sentence is kept; no message text beyond it."""
    shape, reason, evidence = C.classify(message)
    verdict, detail = Q.qualify(message, shape, reason, evidence)
    found = detail if verdict == "CANDIDATE" and isinstance(detail, dict) else {}
    subject = SUBJECT_NOISE.sub("", str(message.get("subject") or "")).strip()
    return store.what_or_fallback({"what": found.get("what", ""), "role": found.get("role") or role(message)[0], "subject": subject})


def blank_ask_rows(notion):
    """The open (Needs Action, Waiting or Replied) rows whose What they want is blank: the rows the ask backfill fills."""
    rows = store.rows_where(notion, {"or": [{"property": "Action Status", "select": {"equals": s}} for s in ASK_STATUSES]})
    return [r for r in rows if not str(r["what"] or "").strip()]


def ask_plan(row, message):
    """-> (props, fallback): the properties that fill a blank What they want (and tick Needs review when the fallback was used); ({}, False) for a row that already has text."""
    if str(row["what"] or "").strip():
        return {}, False
    text, fallback = ask_of(message)
    props = {"What they want": {"rich_text": rich_text(text)}}
    if fallback:
        props["Needs review"] = {"checkbox": True}
    return props, fallback


def apply_ask(notion, row, message, write):
    """Fill one blank What they want. -> "ask" | "fallback" | "" (what was or would be written). One write, read back: the ask holds, Needs review as written, nothing else changed."""
    props, fallback = ask_plan(row, message)
    if not props:
        return ""
    if write:
        text = props["What they want"]["rich_text"][0]["text"]["content"]
        expect = {"what": text, **({"needs_review": True} if fallback else {})}
        with fixed(NotionError, store.RecruitersError, "RECRUITERS_NOTION_WRITE_FAILED"):
            store.set_props(notion, row, props, expect)
    return "fallback" if fallback else "ask"
