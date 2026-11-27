"""Source-keyed view models the opportunity and interview pages are rendered from.

Built only from canonical evidence. A source not connected is `None`, rendered as not verified; nothing is guessed.
The submitted resume comes only from the row's human-owned Submitted Résumé field."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from lifeos.platform import notion_text
from lifeos.platform.notion_client import NotionError
from .identity import valid_date

UNCONNECTED = ("Calendar", "Recruiter email threads", "LinkedIn contacts", "Submitted résumé and sent materials",
               "Original job description", "Interview transcripts")
SUBMITTED = "Submitted Résumé"
# Hand-set hold and next-action line formats; duplicated in lifeos.hiring.models and lifeos.hiring.decision, held equal by a contract test.
HAND_SET = "LIFE OS Hand-Set"
_AT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"
HOLD_LINE = re.compile(rf"v7-hold:1;state=(paused|waiting|active);at=({_AT});op=([0-9a-f]{{16}})")
NEXT_LINE = re.compile(rf"v7-next:1;due=(\d{{4}}-\d{{2}}-\d{{2}}|none);at=({_AT});op=([0-9a-f]{{16}});text=(.*)")
SENT_LINE = re.compile(rf"v7-sent:1;kind=(form|file);at=({_AT});op=([0-9a-f]{{16}});url=(https://\S+|none);text=(.*)")
UNSENT_LINE = re.compile(rf"v7-unsent:1;at=({_AT});op=([0-9a-f]{{16}});of=([0-9a-f]{{16}})")


@dataclass(frozen=True)
class Round:
    page_id: str
    ordinal: int | None
    date: str | None
    interviewer: str | None
    calendar: str | None = None  # "accepted" | "cancelled" from the round page's Logistics reference, else not verified
    joining: bool = False
    created: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Opportunity:
    company: str
    role: str
    stable_job_key: str | None
    requisition: str | None
    ledger_page_id: str | None
    ledger: str
    request_url: str | None
    pipeline_id: str
    compensation: str | None = None
    work_mode: str | None = None
    apply_url: str | None = None
    fit_summary: str | None = None
    rounds: tuple = ()
    recruiters: tuple = ()  # (person, company, thread url) of Recruiters rows linked to the request message
    recruiters_state: str | None = None  # None = not connected; "linked" | "none" | "unreadable"
    contacts: tuple = ()
    contacts_state: str | None = None  # None = no Ledger row linked; "linked" | "none" | "unreadable"
    done: tuple = ()
    decisions: tuple = ()
    page_id: str | None = None
    close_base: str | None = None
    resume: tuple = ()
    resume_state: str | None = None  # None = Ledger row not verified; "recorded" | "not_recorded" | "not_connected"
    hand: tuple = ()
    repaired: str = ""
    hand_ledger: tuple = ()
    sent_mail: tuple = ()  # (sent time ISO, subject) of the user's messages on the request thread
    sent_mail_state: str | None = None


def _plain(prop):
    if not isinstance(prop, dict):
        return None
    if prop.get("type") == "url":
        return (prop.get("url") or "").strip() or None
    value = notion_text.plain(prop.get(prop.get("type"))).strip()
    return value or None


def ledger_facts(client, page_id, stable_job_key):
    if not page_id:
        return "not_linked", {}
    try:
        row = client.call("GET", f"/pages/{page_id}")
    except NotionError:
        return "unreadable", {}
    props = row.get("properties") or {}
    if row.get("archived") or row.get("in_trash") or not stable_job_key or _plain(props.get("Stable Job Key")) != stable_job_key:
        return "mismatch", {}
    resume, resume_state = submitted_resume(props)
    hand = tuple(t for t in (_plain(props.get(HAND_SET)) or "").splitlines() if HOLD_LINE.fullmatch(t) or NEXT_LINE.fullmatch(t) or SENT_LINE.fullmatch(t) or UNSENT_LINE.fullmatch(t))
    return "linked", {"compensation": _plain(props.get("Compensation")), "work_mode": _plain(props.get("Location / Work Mode")),
                      "apply_url": _plain(props.get("Apply URL")), "fit_summary": _plain(props.get("Why It Fits")),
                      "resume": resume, "resume_state": resume_state, "hand_ledger": hand}


def submitted_resume(props):
    """The files recorded as sent: uploads keep only their name (the URL expires); external https links are kept."""
    prop = props.get(SUBMITTED)
    if not isinstance(prop, dict) or prop.get("type") != "files":
        return (), "not_connected"
    out = []
    for f in prop.get("files") or []:
        name = f.get("name") if isinstance(f, dict) else None
        if isinstance(name, str) and name.strip():
            ext = f.get("external") if f.get("type") == "external" else None
            url = ext.get("url") if isinstance(ext, dict) else None
            ok = isinstance(url, str) and url.startswith("https://") and not any(c.isspace() for c in url)
            out.append((name.strip(), url if ok else None))
    return tuple(out), ("recorded" if out else "not_recorded")


def rounds_of(children):
    return tuple(Round(c.page_id, c.ordinal, c.interview_date, c.interviewer, created=getattr(c, "created", None)) for c in getattr(children, "items", ())
                 if c.identity_valid is True or c.legacy)


def with_calendar(client, rounds):
    from .create import round_logistics
    out = []
    for r in rounds:
        state, meeting = None, None
        if valid_date(r.date) and r.page_id:
            try:
                ref, cancelled, meeting = round_logistics(client, r.page_id)
            except NotionError:
                ref, cancelled = None, False
            state = ("cancelled" if cancelled else "accepted") if ref else None
        out.append(Round(r.page_id, r.ordinal, r.date, r.interviewer, state, state == "accepted" and meeting is not None, r.created))
    return tuple(out)


def _live(r):
    return r.calendar != "cancelled"


def sort_rounds(rounds, today):
    """Upcoming confirmed interviews (nearest first), then Scheduling Pending, then past (most recent first)."""
    def key(r):
        number = r.ordinal if type(r.ordinal) is int else 10 ** 6
        if valid_date(r.date) and r.date >= today and _live(r):
            return (0, r.date, number, r.page_id)
        if not valid_date(r.date):
            return (1, "", number, r.page_id)
        return (2, "".join(chr(0x7f - ord(c)) for c in r.date), number, r.page_id)
    return tuple(sorted(rounds, key=key))


def opportunity(client, parent_query, query, pipeline_id, children=None, today="1970-01-01"):
    state, facts = ledger_facts(client, query.ledger_page_id, query.stable_job_key)
    request = (query.source or "").strip()
    return Opportunity(parent_query.company.strip(), parent_query.role.strip(), query.stable_job_key, query.requisition,
                       query.ledger_page_id if state == "linked" else None, state,
                       request if request.startswith("https://") else None, pipeline_id,
                       rounds=sort_rounds(with_calendar(client, rounds_of(children)), today),
                       recruiters=tuple(getattr(query, "recruiters", ()) or ()), recruiters_state=getattr(query, "recruiters_state", None),
                       contacts=tuple(getattr(query, "contacts", ()) or ()) if state == "linked" else (), contacts_state=getattr(query, "contacts_state", None) if state == "linked" else None,
                       sent_mail=tuple(getattr(query, "sent_mail", ()) or ()), sent_mail_state=getattr(query, "sent_mail_state", None), **facts)


def primary_action(view, today):
    """One primary action from verified evidence -> (label, action, due, source_url, basis); `basis` says what it
    rests on."""
    if not view.rounds and view.request_url and (view.stable_job_key or "").startswith("recruiter-"):
        return "PRIMARY ACTION", "Reply to the recruiter's message, or close this lead.", "No verified deadline", view.request_url, "derived from the linked recruiter message"
    for r in view.rounds:
        if not valid_date(r.date):
            name = f"Interview {r.ordinal}" if r.ordinal else "the next interview"
            basis = ("derived from the linked interview request" if view.request_url
                     else "DEGRADED · derived from the interview page; the request message is not connected")
            return "PRIMARY ACTION", f"Confirm a time for {name} with the recruiter.", "No verified deadline", view.request_url, basis
    upcoming = [r for r in view.rounds if valid_date(r.date) and r.date >= today and _live(r)]
    if upcoming:
        r = upcoming[0]
        return ("PRIMARY ACTION", f"Prepare for Interview {r.ordinal or ''} on {r.date}.".replace("  ", " "), r.date, None,
                "from the accepted Calendar invite" if r.calendar == "accepted" else "derived from the interview page's date; Calendar not verified")
    if view.rounds and view.rounds[0].calendar == "cancelled":
        r = view.rounds[0]
        name = f"Interview {r.ordinal}" if r.ordinal else "the interview"
        return ("PRIMARY ACTION", f"Ask the recruiter whether {name} moves to a new time.", "No verified deadline", view.request_url,
                "derived from the cancelled Calendar invite")
    return ("DEGRADED · NO VERIFIED ACTION", "Verify the latest interview outcome and any recruiter follow-up; those sources are "
            "not connected yet, so no next action is shown as current.", "Unverified", None, "source evidence incomplete")


def _latest(view, pattern):
    found = [(datetime.fromisoformat(m.group(2)), i, m) for i, m in enumerate(map(pattern.fullmatch, view.hand_ledger + view.hand)) if m]
    return max(found, key=lambda f: f[:2])[2] if found else None


def hand_action(view):
    """The hand-set next action -> (key, text, due or None), or None. The latest line on either record wins; Mark
    Complete clears it."""
    m = _latest(view, NEXT_LINE)
    if m is None:
        return None
    text, due = m.group(4).strip(), None if m.group(1) == "none" else m.group(1)
    if not (text or due) or m.group(3) in dict(view.done):
        return None
    return m.group(3), text or "Next step set by Jim", due


def hand_set_at(view):
    m = _latest(view, NEXT_LINE)
    return datetime.fromisoformat(m.group(2)) if m and hand_action(view) else None


def _created(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        return None


def evidence_since(view, since):
    """The first round page created after `since` -> (aware datetime, what), or None."""
    found = sorted((t, f"Interview {r.ordinal} page added" if r.ordinal else "an interview page added")
                   for r in view.rounds if (t := _created(r.created)) is not None and since is not None and t > since)
    return found[0] if found else None


def sent_material(view):
    """Sent material recorded on Manage, oldest first, each op once, none removed -> [(kind, text, url, datetime)]."""
    found, seen = [], {m.group(3) for m in map(UNSENT_LINE.fullmatch, view.hand_ledger + view.hand) if m}
    for m in sorted(filter(None, map(SENT_LINE.fullmatch, view.hand_ledger + view.hand)), key=lambda m: datetime.fromisoformat(m.group(2))):
        if m.group(3) not in seen:
            seen.add(m.group(3))
            found.append((m.group(1), m.group(5).strip(), None if m.group(4) == "none" else m.group(4), datetime.fromisoformat(m.group(2))))
    return found


def hold(view):
    """The hand-set Paused / Waiting state -> (state, set at, op, cleared) or None; `cleared` is (when, what) once
    newer evidence arrived."""
    m = _latest(view, HOLD_LINE)
    if m is None or m.group(1) == "active":
        return None
    at = datetime.fromisoformat(m.group(2))
    return m.group(1), at, m.group(3), evidence_since(view, at)


def stage(view, today):
    pending = [r for r in view.rounds if not valid_date(r.date)]
    upcoming = [r for r in view.rounds if valid_date(r.date) and r.date >= today and _live(r)]
    past = [r for r in view.rounds if valid_date(r.date) and (r.date < today or not _live(r))]
    if pending:
        current = f"Scheduling Pending · Interview {pending[0].ordinal}" if pending[0].ordinal else "Scheduling Pending"
    elif upcoming:
        current = f"Interview {upcoming[0].ordinal} scheduled" if upcoming[0].ordinal else "Interview scheduled"
    elif past and not _live(past[0]):
        current = f"Interview {past[0].ordinal} cancelled in Calendar" if past[0].ordinal else "Interview cancelled in Calendar"
    elif past:
        current = f"Interview {past[0].ordinal} date passed · outcome not verified" if past[0].ordinal else "Interview date passed"
    else:
        current = "No interview pages yet"
    return current, (upcoming[0].date if upcoming else "Unavailable")
