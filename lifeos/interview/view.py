"""LIFE-88 / LIFE-46: the source-keyed view models the UI 3c opportunity page and the UI 4 interview page are rendered from.

Built only from canonical evidence V7 can read today: the exact Job Ledger row (Interview token, read only, confirmed by its
Stable Job Key), the exact interview request message, and the machine interview pages under the opportunity (their identity
lines). Every other source the approved layout names (Calendar, the exact-job Gmail/Outlook threads, the LinkedIn Connections
sheet, the submitted résumé and sent files, the verbatim job description, interview transcripts) is not connected to this
writer yet and is carried as `None`, which the template renders as not verified. Nothing here is guessed or defaulted."""
from dataclasses import dataclass
from lifeos.platform.notion_client import NotionError
from .identity import valid_date

UNCONNECTED = ("Calendar", "Recruiter email threads", "LinkedIn contacts", "Submitted résumé and sent materials",
               "Original job description", "Interview transcripts")


@dataclass(frozen=True)
class Round:
    page_id: str
    ordinal: int | None
    date: str | None
    interviewer: str | None


@dataclass(frozen=True)
class Opportunity:
    company: str
    role: str
    stable_job_key: str | None
    requisition: str | None
    ledger_page_id: str | None
    ledger: str                                  # linked | not_linked | unreadable | mismatch
    request_url: str | None
    pipeline_id: str
    compensation: str | None = None
    work_mode: str | None = None
    apply_url: str | None = None
    fit_summary: str | None = None
    rounds: tuple = ()


def _plain(prop):
    if not isinstance(prop, dict):
        return None
    if prop.get("type") == "url":
        return (prop.get("url") or "").strip() or None
    value = "".join(p.get("plain_text", "") for p in prop.get(prop.get("type"), []) or []).strip()
    return value or None


def ledger_facts(client, page_id, stable_job_key):
    """The exact Job Ledger row, only when its Stable Job Key is the job's. Returns (state, facts)."""
    if not page_id:
        return "not_linked", {}
    try:
        row = client.call("GET", f"/pages/{page_id}")
    except NotionError:
        return "unreadable", {}
    props = row.get("properties") or {}
    if row.get("archived") or row.get("in_trash") or not stable_job_key or _plain(props.get("Stable Job Key")) != stable_job_key:
        return "mismatch", {}
    return "linked", {"compensation": _plain(props.get("Compensation")), "work_mode": _plain(props.get("Location / Work Mode")),
                      "apply_url": _plain(props.get("Apply URL")), "fit_summary": _plain(props.get("Why It Fits"))}


def rounds_of(children):
    """Machine interview pages with a verified identity line, in D15 order (dated before undated is decided in sort_rounds)."""
    return tuple(Round(c.page_id, c.ordinal, c.interview_date, c.interviewer) for c in getattr(children, "items", ())
                 if c.identity_valid is True)


def sort_rounds(rounds, today):
    """D15: upcoming confirmed interviews first (nearest first), then Scheduling Pending, then past ones (most recent first);
    ties by round number, then page id, so the order is stable."""
    def key(r):
        number = r.ordinal if type(r.ordinal) is int else 10 ** 6
        if valid_date(r.date) and r.date >= today:
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
                       rounds=sort_rounds(rounds_of(children), today), **facts)


def primary_action(view, today):
    """D02-D06: one source-backed primary action, never invented. (label, action, due, source_url)."""
    for r in view.rounds:
        if not valid_date(r.date):
            name = f"Interview {r.ordinal}" if r.ordinal else "the next interview"
            return "PRIMARY ACTION", f"Confirm a time for {name} with the recruiter.", "No verified deadline", view.request_url
    upcoming = [r for r in view.rounds if valid_date(r.date) and r.date >= today]
    if upcoming:
        r = upcoming[0]
        return "PRIMARY ACTION", f"Prepare for Interview {r.ordinal or ''} on {r.date}.".replace("  ", " "), r.date, None
    return ("DEGRADED · NO VERIFIED ACTION", "Verify the latest interview outcome and any recruiter follow-up; those sources are "
            "not connected yet, so no next action is shown as current.", "Unverified", None)


def stage(view, today):
    pending = [r for r in view.rounds if not valid_date(r.date)]
    upcoming = [r for r in view.rounds if valid_date(r.date) and r.date >= today]
    past = [r for r in view.rounds if valid_date(r.date) and r.date < today]
    if pending:
        current = f"Scheduling Pending · Interview {pending[0].ordinal}" if pending[0].ordinal else "Scheduling Pending"
    elif upcoming:
        current = f"Interview {upcoming[0].ordinal} scheduled" if upcoming[0].ordinal else "Interview scheduled"
    elif past:
        current = f"Interview {past[0].ordinal} date passed · outcome not verified" if past[0].ordinal else "Interview date passed"
    else:
        current = "No interview pages yet"
    return current, (upcoming[0].date if upcoming else "Unavailable")
