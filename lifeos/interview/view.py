"""LIFE-88 / LIFE-46: the source-keyed view models the UI 3c opportunity page and the UI 4 interview page are rendered from.

Built only from canonical evidence V7 can read today: the exact Job Ledger row (Interview token, read only, confirmed by its
Stable Job Key), the exact interview request message, and the machine interview pages under the opportunity (their identity
lines). Every other source the approved layout names (Calendar, the exact-job Gmail/Outlook threads, the LinkedIn Connections
sheet, the other sent files, the verbatim job description, interview transcripts) is not connected to this writer yet and is
carried as `None`, which the template renders as not verified. Nothing here is guessed or defaulted. The submitted résumé comes
only from the exact row's human-owned Submitted Résumé field (Jim, Oct 24), never from the Resume column, which holds the latest
résumé LIFE OS built, not the one Jim sent."""
from dataclasses import dataclass
from lifeos.platform.notion_client import NotionError
from .identity import valid_date

UNCONNECTED = ("Calendar", "Recruiter email threads", "LinkedIn contacts", "Submitted résumé and sent materials",
               "Original job description", "Interview transcripts")
SUBMITTED = "Submitted Résumé"                   # the Job Ledger's Files field Jim fills with the file he actually sent (LIFE-88 E10)


@dataclass(frozen=True)
class Round:
    page_id: str
    ordinal: int | None
    date: str | None
    interviewer: str | None
    calendar: str | None = None                  # LIFE-78 E10: "accepted" | "cancelled" from the round page's own Logistics reference, else not verified
    joining: bool = False                        # E10: that accepted round page records its invite's joining details (link or location, #481)


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
    recruiters: tuple = ()                       # LIFE-78 E10: (person, company, thread url) from Recruiters rows linked to this job's exact request message
    recruiters_state: str | None = None          # None = not connected; "linked" | "none" | "unreadable"
    contacts: tuple = ()                         # E10: (name, why) from the job page's own Network Leads, read by the source adapter
    contacts_state: str | None = None            # None = no Ledger row linked; "linked" | "none" | "unreadable"
    done: tuple = ()                             # E05: (action key, YYYY-MM-DD) completion lines the Close screen recorded in this page's fold, carried forward
    decisions: tuple = ()                        # Jim, Oct 25: the Close / Reopen record lines in this page's fold (template.DECISION_LINE), carried forward as written
    page_id: str | None = None                   # E05: this opportunity page's own id, once it exists (the Mark Complete link names it)
    close_base: str | None = None                # E05: the private Close origin (V7_CLOSE_BASE_URL); unset, Mark Complete stays unavailable
    resume: tuple = ()                           # E10: (file name, stable link or None) from the exact row's Submitted Résumé field
    resume_state: str | None = None              # None = Job Ledger row not verified; "recorded" | "not_recorded" | "not_connected" (no such field)


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
    resume, resume_state = submitted_resume(props)
    return "linked", {"compensation": _plain(props.get("Compensation")), "work_mode": _plain(props.get("Location / Work Mode")),
                      "apply_url": _plain(props.get("Apply URL")), "fit_summary": _plain(props.get("Why It Fits")),
                      "resume": resume, "resume_state": resume_state}


def submitted_resume(props):
    """E10: the files Jim recorded as sent. An uploaded file's own URL expires within the hour, so it is named (the page links the row
    that holds it); an external link is stable and is kept, https only. Anything that is not a files value is not connected."""
    prop = props.get(SUBMITTED)
    if not isinstance(prop, dict) or prop.get("type") != "files":
        return (), "not_connected"
    out = []
    for f in prop.get("files") or []:
        name = f.get("name") if isinstance(f, dict) else None
        if isinstance(name, str) and name.strip():
            ext = f.get("external") if f.get("type") == "external" else None
            url = ext.get("url") if isinstance(ext, dict) else None
            ok = isinstance(url, str) and url.startswith("https://") and not any(c.isspace() for c in url)      # a hand-typed bad link would fail the whole page write
            out.append((name.strip(), url if ok else None))
    return tuple(out), ("recorded" if out else "not_recorded")


def rounds_of(children):
    """Machine interview pages with a verified identity line (and, for a pursuit Jim named, hand-made ones whose title states the number and date), in D15 order (dated before undated is decided in sort_rounds)."""
    return tuple(Round(c.page_id, c.ordinal, c.interview_date, c.interviewer) for c in getattr(children, "items", ())
                 if c.identity_valid is True or c.legacy)


def with_calendar(client, rounds):
    """E10: each dated machine round with the Calendar state its own Logistics records (E07 writes it with the event reference). A page whose layout
    is not UI 4, or that cannot be read this run, stays not verified; nothing is inferred from a date alone."""
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
        out.append(Round(r.page_id, r.ordinal, r.date, r.interviewer, state, state == "accepted" and meeting is not None))
    return tuple(out)


def _live(r):
    return r.calendar != "cancelled"


def sort_rounds(rounds, today):
    """D15: upcoming confirmed interviews first (nearest first), then Scheduling Pending, then past ones (most recent first);
    ties by round number, then page id, so the order is stable."""
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
                       contacts=tuple(getattr(query, "contacts", ()) or ()) if state == "linked" else (), contacts_state=getattr(query, "contacts_state", None) if state == "linked" else None, **facts)


def primary_action(view, today):
    """D02-D06: one primary action derived from verified evidence, never invented. (label, action, due, source_url, basis): `basis` says what it
    rests on, and an action whose request or Calendar evidence is not connected is labelled derived, never source-backed."""
    if not view.rounds and view.request_url and (view.stable_job_key or "").startswith("recruiter-"):          # E01b: recruiter outreach (a minted recruiter key) with no round yet
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
    if view.rounds and view.rounds[0].calendar == "cancelled":                        # the latest round's invite was cancelled and nothing is upcoming
        r = view.rounds[0]
        name = f"Interview {r.ordinal}" if r.ordinal else "the interview"
        return ("PRIMARY ACTION", f"Ask the recruiter whether {name} moves to a new time.", "No verified deadline", view.request_url,
                "derived from the cancelled Calendar invite")
    return ("DEGRADED · NO VERIFIED ACTION", "Verify the latest interview outcome and any recruiter follow-up; those sources are "
            "not connected yet, so no next action is shown as current.", "Unverified", None, "source evidence incomplete")


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
