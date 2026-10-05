"""MegIBOW classification (D134): deterministic rules, no model. Pure.

A candidate (a sent message, or a calendar event occurrence) becomes one outcome: COUNTED, EXCLUDED, REVIEW or DEGRADED. Uncertain evidence is never counted: it goes to REVIEW.
Identity: a message by its id; Scheduled by the meeting's iCalendar UID (survives a reschedule); a completed call by its occurrence. Keys are hashed before they are stored or shown."""
from datetime import timedelta
import hashlib
import re

from lifeos.megibow.windows import week_of

COUNTED, EXCLUDED, REVIEW = "COUNTED", "EXCLUDED", "REVIEW"
OUTREACH, SCHEDULED, NETWORKING, RECRUITER, COMPANY = "Outreach", "Scheduled", "Networking Calls", "Recruiter Calls", "Company Calls"
ACTIVITIES = (OUTREACH, SCHEDULED, NETWORKING, RECRUITER, COMPANY)
CALLS = (NETWORKING, RECRUITER, COMPANY)
CORROBORATION_DAYS = 3

PERSONAL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com"}
AUTOMATED_LOCAL = re.compile(r"^(no-?reply|do-?not-?reply|notifications?|mailer|alerts?|bounce|support|billing|invoice|calendar-notification)\b", re.I)
RESOURCE = re.compile(r"resource\.calendar\.google\.com$|zoom\.us$", re.I)
RECRUITER_WORDS = re.compile(r"\b(recruit\w*|talent acquisition|sourcer|staffing|headhunt\w*|search firm|phone screen)\b", re.I)
NETWORK_WORDS = re.compile(r"\b(networking|listening tour|coffee chat|catch[- ]?up|intro(duction)? call|informational)\b", re.I)
PLAUSIBLE_WORDS = re.compile(r"\b(recruit\w*|talent|interview\w*|referr\w*|hiring|job|opportunit\w*|r[eé]sum[eé]|introduc\w*|networking|career|listening tour|coffee chat|informational)\b", re.I)
RESPONSES_OK = ("accepted", "organizer")


def key_hash(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def domain(address):
    return address.rsplit("@", 1)[-1].lower().strip() if "@" in address else ""


def label(host):
    """Second-level name of a host: mail.acme.co.uk -> acme."""
    parts = [p for p in host.lower().split(".") if p]
    if len(parts) < 2:
        return host.lower()
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return parts[-3]
    return parts[-2]


def normal(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def external(addresses, ctx):
    """Human, external addresses: not Jim's own, not a resource or automated address."""
    out = []
    for address in addresses:
        a = address.lower().strip()
        if not a or a in ctx["self"] or RESOURCE.search(domain(a)) or AUTOMATED_LOCAL.match(a.split("@", 1)[0]):
            continue
        out.append(a)
    return out


def known(addresses, ctx):
    """True when an address belongs to a company already in Jim's job search (its name matches the address's domain name)."""
    labels = {label(domain(a)) for a in addresses if domain(a) and domain(a) not in PERSONAL}
    return any(normal(l) and normal(l) in ctx["known"] for l in labels)


def _excluded_domain(addresses, ctx):
    return bool(addresses) and all(domain(a) in ctx["excluded"] for a in addresses)


def _out(status, activity, week, reason, key, candidate=None):
    return {"status": status, "activity": activity, "week": week, "reason": reason, "key": key, "candidate": candidate}


def message(msg, ctx):
    """One sent message -> outcome. msg: id, sent_at (aware), to, cc, subject, snippet, bulk."""
    week = week_of(msg["sent_at"])
    key = key_hash("msg", msg["id"])
    decided = ctx["overrides"].get(key)
    if decided:
        return _decided(decided, OUTREACH, week, key)
    people = external(list(msg.get("to") or []) + list(msg.get("cc") or []), ctx)
    if not people:
        return _out(EXCLUDED, None, week, "self_or_automated_recipient", key)
    if msg.get("bulk"):
        return _out(EXCLUDED, None, week, "bulk_or_automated", key)
    if _excluded_domain(people, ctx) and not known(people, ctx):
        return _out(EXCLUDED, None, week, "work_or_client", key)
    if any(key_hash("contact", p) in ctx["contacts"] for p in people) or known(people, ctx):
        return _out(COUNTED, OUTREACH, week, "known_contact_or_company", key)
    if PLAUSIBLE_WORDS.search(f"{msg.get('subject', '')} {msg.get('snippet', '')}"):
        return _out(REVIEW, OUTREACH, week, "relevance", key, "Outreach")
    return _out(EXCLUDED, None, week, "no_job_search_evidence", key)


def _decided(decision, default_activity, week, key):
    """Jim's dropdown choice for a candidate: 'Count as X' counts X, 'Exclude' excludes, 'Defer' keeps it in review."""
    if decision.startswith("Count as "):
        wanted = decision[len("Count as "):]
        if wanted in ACTIVITIES or wanted + "s" in ACTIVITIES:
            return _out(COUNTED, wanted if wanted in ACTIVITIES else wanted + "s", week, "jim_decision", key)
    if decision == "Exclude":
        return _out(EXCLUDED, None, week, "jim_excluded", key)
    return _out(REVIEW, default_activity, week, "deferred", key, default_activity)


def contact_type(people, text, ctx):
    """Recruiter first, then company, then networking, else unknown. -> ACTIVITY or None."""
    kinds = {ctx["contacts"].get(key_hash("contact", p)) for p in people}
    if RECRUITER in kinds or RECRUITER_WORDS.search(text):
        return RECRUITER
    if COMPANY in kinds or known(people, ctx):
        return COMPANY
    if NETWORKING in kinds or NETWORK_WORDS.search(text):
        return NETWORKING
    return None


def event(ev, ctx, now):
    """One calendar event occurrence -> a list of outcomes (Scheduled, and the completed call once it has happened).
    ev: uid, occ, created (aware or None), start, end, title, preview, cancelled, all_day, my_response, attendees [{email, response}], bridged."""
    if ev.get("bridged"):
        return [_out(EXCLUDED, None, week_of(ev["start"]), "bridged_copy", key_hash("ev", ev["uid"]))]
    start_week = week_of(ev["start"])
    if ev.get("all_day"):
        return [_out(EXCLUDED, None, start_week, "all_day", key_hash("call", ev["occ"]))]
    sched_key, call_key = key_hash("sched", ev["uid"]), key_hash("call", ev["occ"])
    if ev.get("cancelled"):
        return [_out(EXCLUDED, None, start_week, "cancelled", call_key)]
    if ev.get("my_response") == "declined":
        return [_out(EXCLUDED, None, start_week, "declined", call_key)]
    people = external([a["email"] for a in ev.get("attendees") or []], ctx)
    if not people:
        return [_out(EXCLUDED, None, start_week, "no_external_person", call_key)]
    if _excluded_domain(people, ctx) and not known(people, ctx):
        return [_out(EXCLUDED, None, start_week, "work_or_client", call_key)]
    text = f"{ev.get('title', '')} {ev.get('preview', '')}"
    strong = known(people, ctx) or any(key_hash("contact", p) in ctx["contacts"] for p in people) or bool(RECRUITER_WORDS.search(text) or NETWORK_WORDS.search(text))
    plausible = strong or bool(PLAUSIBLE_WORDS.search(text))
    if not plausible:
        return [_out(EXCLUDED, None, start_week, "no_job_search_evidence", call_key)]
    out = []
    # Scheduled: counted once, in the week the meeting was created, by the meeting's own identity.
    decided = ctx["overrides"].get(sched_key)
    if decided:
        out.append(_decided(decided, SCHEDULED, week_of(ev["created"]) if ev.get("created") else start_week, sched_key))
    elif not strong:
        out.append(_out(REVIEW, SCHEDULED, start_week, "relevance", sched_key, "Scheduled"))
    elif ev.get("created") is None:
        out.append(_out(REVIEW, SCHEDULED, start_week, "creation_time_unknown", sched_key, "Scheduled"))
    else:
        out.append(_out(COUNTED, SCHEDULED, week_of(ev["created"]), "created", sched_key))
    # Completed call: only after the end, only with occurrence evidence, and only with a settled contact type.
    if ev["end"] > now:
        return out
    decided = ctx["overrides"].get(call_key)
    if decided:
        out.append(_decided(decided, "Call", start_week, call_key))
        return out
    occurred = ev.get("my_response") in RESPONSES_OK and any(
        ev["end"] <= sent <= ev["end"] + timedelta(days=CORROBORATION_DAYS) for p in people for sent in ctx["sent_to"].get(p, []))
    if not occurred:
        out.append(_out(REVIEW, "Call", start_week, "occurrence", call_key, "Call"))
        return out
    kind = contact_type(people, text, ctx) if strong else None
    if kind is None:
        out.append(_out(REVIEW, "Call", start_week, "contact_type", call_key, "Call"))
    else:
        out.append(_out(COUNTED, kind, start_week, "completed", call_key))
    return out
