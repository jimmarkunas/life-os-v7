"""Render the compiled Hiring Pipeline into the status line and the four-column table. Pure: no clock reads, no I/O.

Every date label is derived from the stored event start and the run's `now`, never stored, so a past interview can only read as held. One concrete status per row;
no checkbox and no task state. Source is ONE `Opportunity Notes` link when exactly one parent was resolved, else `Needs Notes` (a URL is never guessed)."""
from datetime import datetime, timedelta
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
               "SNAPSHOT_STALE": "evidence is stale"}


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


def row_cells(row, now):
    """-> [(text, link)] x 4."""
    stage, update, action = row["stage"], "", ""
    if row.get("event_start"):
        past, label = when(row["event_start"], now)
        kind = KIND_LABEL.get(row["event_kind"], "interview")
        if past:
            update, action = f"{kind.capitalize()} held {label}", f"Awaiting outcome of the {kind} held {label}"
        else:
            update, action = f"{kind.capitalize()} {label}", f"Prepare for the {kind} · {label}"
    elif row.get("rounds"):
        update, action = f"{row['rounds']} round(s) logged in Notes", "Awaiting next step"
    else:
        update, action = (f"Applied {local(row['applied_on']).strftime('%b %-d')}" if row.get("applied_on") else "Application submitted"), "Awaiting response"
    if row.get("carried"):
        update += " (last accepted)"
    source = (M.NOTES_LABEL, NOTES_URL + row["parent"].replace("-", "")) if row.get("parent_state") == "one" and row.get("parent") else (M.NO_NOTES, None)
    return [(M.label(row["company"], row["role"]), None), (f"{stage} · {update}", None), (action, None), source]


def _older(row, now):
    """A submitted role with no newer evidence stays listed for APPLIED_DAYS after Applied On; an undated one stays."""
    return (row["stage"] == M.SUBMITTED and row.get("applied_on") and not row.get("carried")
            and (now.date() - local(row["applied_on"]).date()).days > M.APPLIED_DAYS)


older_submitted = _older                                             # the one active-row rule, shared with network matching (NET-2.2); not a second lifecycle


def render(result, stored, now):
    """result: compile_rows output. -> (status text, table rows or None, counts). None leaves the table as it was (UNAVAILABLE)."""
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
    shown = rows[:MAX_ROWS]
    cells = [row_cells(r, now) for r in shown]
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
