"""Render the compiled Hiring Pipeline into the status line and the four-column table. Pure: no clock reads, no I/O.

Every date label is derived from the stored event start and the run's `now`, never stored, so a past interview can only read as held. One concrete status per row;
no checkbox and no task state. Source is ONE `Opportunity Notes` link when exactly one parent was resolved, else `Needs Notes` (a URL is never guessed)."""
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import models as M

TZ = ZoneInfo("America/Chicago")
STALE_HOURS = 6
MAX_ROWS = 80                                                   # one Notion append takes 100 blocks; more rows than this are counted, never silently cut
NOTES_URL = "https://www.notion.so/"
KIND_LABEL = {"assessment": "assessment", "hiring_manager": "hiring-manager interview", "recruiter_screen": "recruiter screen", "interview": "interview"}
REASON_TEXT = {"LEDGER_UNAVAILABLE": "Job Ledger unreadable", "OPPORTUNITIES_UNAVAILABLE": "Hiring Pipeline pages unreadable", "CALENDAR_UNAVAILABLE": "Calendar unreadable",
               "IDENTITY_AMBIGUOUS": "an opportunity matches more than one page", "CALENDAR_AMBIGUOUS": "a calendar event matches more than one opportunity",
               "EVENT_NOT_ON_CALENDAR": "a scheduled event is no longer on the Calendar", "PARENT_IDENTITY_UNREADABLE": "an opportunity page title is unreadable",
               "SNAPSHOT_STALE": "evidence is stale", "OUTCOME_MAIL_STALE": "outcome mail not read recently", "OUTCOME_MAIL_UNREADABLE": "outcome mail state unreadable", "DECISION_AMBIGUOUS": "a Hiring Decision sits on one of several applications for one company and role"}


def local(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)


def clock(moment):
    return moment.strftime("%-I:%M %p").lstrip("0") + " CT"


def when(start, now):
    """-> (is_past, label). Upcoming reads Today / Tomorrow / Upcoming; a past start reads Held and can never be upcoming."""
    at, now = local(start), local(now)
    if at <= now:
        return True, ("today" if at.date() == now.date() else at.strftime("%b %-d"))
    if at.date() == now.date():
        return False, f"Today {clock(at)}"
    if at.date() == (now + timedelta(days=1)).date():
        return False, f"Tomorrow {clock(at)}"
    return False, f"Upcoming {at.strftime('%b %-d')}, {clock(at)}"


RED, YELLOW, GREEN = "🔴", "🟡", "🟢"                              # actionable or due · waiting · verified upcoming activity (LIFE-47); a dot is a cue, the words say the same


def next_step(row, now):
    """-> (dot, next step · due, sort date). Due dates come only from supported evidence: a Calendar event, or evidence + AWAIT_DAYS (D189); none is ever invented."""
    outcome, kind = row.get("outcome"), KIND_LABEL.get(row.get("event_kind"), "interview")
    if row.get("stage") == M.LEAD and not outcome:                   # E01b: recruiter outreach is the first warning; there is no deadline to invent
        return RED, "Recruiter outreach · reply, or close it", ""
    if outcome:                                                      # D190: outcome mail is a source-backed action to confirm, never a stage or a deadline; its received date is the evidence
        sure, day = outcome.get("match") == "role", local(outcome["received"]).strftime("%b %-d")
        if outcome["kind"] == "OFFER":
            return RED, f"{'Offer' if sure else 'Possible offer'} email received {day} · review and respond", ""
        where = "confirm, then move the Notes page to Retired" if row.get("parent_state") == "one" and row.get("parent") else "confirm (Applied stays as history)"
        return RED, f"{'Rejection' if sure else 'Possible rejection'} email received {day} · {where}", ""
    if row.get("event_start"):
        past, label = when(row["event_start"], now)
        if not past:
            return (YELLOW if row.get("carried") else GREEN), f"Prepare for the {kind} · {label}", local(row["event_start"]).date().isoformat()
        start, waiting, quiet = row["event_start"], f"Awaiting outcome of the {kind} held {label}", "news"
    elif row.get("rounds"):
        return YELLOW, f"Awaiting next step · {row['rounds']} round(s) logged in Notes", "9"
    else:
        start, waiting, quiet = row.get("applied_on"), "Awaiting response", "response"
        if not start:
            return YELLOW, waiting, "9"
    if row.get("contact") and local(row["contact"]) > local(start if "T" in start else start + "T00:00:00"):
        start, waiting = row["contact"], f"Last contact {local(row['contact']).strftime('%b %-d')}"       # LIFE-26: a direct message resets the clock
        quiet = "contact"
    due, days = local(start).date() + timedelta(days=M.AWAIT_DAYS), (local(now).date() - local(start).date()).days
    if days >= M.AWAIT_DAYS:
        return RED, f"Stale · no {quiet} in {days} days · follow up (due {due.strftime('%b %-d')})", due.isoformat()
    return YELLOW, f"{waiting} · follow up {due.strftime('%b %-d')}", due.isoformat()


def close_url(base, pid):
    """D194 S3: the private Close screen link, or None. `base` must be a bare https origin (host and optional port: no credentials, path, query or fragment) and `pid` one 32-hex page id; nothing is guessed or half-built."""
    return f"{base.rstrip('/')}/?{urlencode({'o': pid})}" if re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d{1,5})?/?", base or "") and re.fullmatch(r"[0-9a-f]{32}", pid or "") else None


def row_cells(row, now, close=None):
    """-> [(text, link)] x 4: Company — Role · Stage · Next step · Due · Notes. Plain text; the Notes page is linked only when exactly one parent was resolved (a URL is never guessed). `close` adds the second Notes link as segments."""
    _, step, _ = next_step(row, now)                                 # the dot classifies and sorts the row (render below); it is never printed beside the name
    source = (M.NOTES_LABEL, NOTES_URL + row["parent"].replace("-", "")) if row.get("parent_state") == "one" and row.get("parent") else (M.NO_NOTES, None)
    source = [source, (" · ", None), ("Close ↗", close)] if close else source
    return [(M.label(row['company'], row['role']), None), (row["stage"] + (" (last accepted)" if row.get("carried") else ""), None), (step, None), source]


def _older(row, now):
    """A submitted role with no newer evidence stays listed for APPLIED_DAYS after Applied On; an undated one stays."""
    return (row["stage"] == M.SUBMITTED and row.get("applied_on") and not row.get("carried")
            and (now.date() - local(row["applied_on"]).date()).days > M.APPLIED_DAYS)


older_submitted = _older                                             # the one active-row rule, shared with network matching (NET-2.2); not a second lifecycle


def render(result, stored, now, close=(None, {})):
    """result: compile_rows output; close: (private base URL, {row key: page id}). -> (status text, table rows or None, counts). None leaves the table as it was (UNAVAILABLE)."""
    now = local(now)
    reasons = list(result["reasons"])
    accepted = (stored or {}).get("accepted_at")
    if accepted and not reasons and local(accepted) < now - timedelta(hours=STALE_HOURS):
        reasons.append("SNAPSHOT_STALE")
    rows = result["rows"]
    note = " · ".join(REASON_TEXT.get(r, r.lower()) for r in reasons)
    if rows is None:
        return (f"DEGRADED · Hiring Pipeline unavailable at {clock(now)} · {note} · no accepted state yet",
                None, {"status": "unavailable", "rows": 0, "reasons": reasons})
    hidden_older = sum(1 for r in rows if _older(r, now))
    rows = [r for r in rows if not _older(r, now)]
    rows.sort(key=lambda r: ((n := next_step(r, now))[0] != RED, n[0] != YELLOW, n[2], r["company"].lower(), r["role"].lower()))     # attention first, then by due date
    shown = rows[:MAX_ROWS]
    cells = [row_cells(r, now, close_url(close[0], close[1].get(r["key"]))) for r in shown]
    extra = []
    if len(rows) > len(shown):
        extra.append(f"{len(rows) - len(shown)} more not shown")
    if hidden_older:
        extra.append(f"{hidden_older} older submitted not shown")
    tail = (" · " + " · ".join(extra)) if extra else ""
    if reasons:
        when_text = local(accepted).strftime("%b %-d, ") + clock(local(accepted)) if accepted else "never"
        text = f"DEGRADED · Hiring Pipeline {note} at {clock(now)} · showing last accepted state ({when_text}) · {len(rows)} active{tail}"
    else:
        text = f"Updated {clock(now)} · {len(rows)} active{tail}" if rows else f"Updated {clock(now)} · no submitted or interviewing opportunities{tail}"
    return text, cells, {"status": "degraded" if reasons else "fresh", "rows": len(shown), "reasons": reasons}
