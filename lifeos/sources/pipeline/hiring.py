"""Hiring Pipeline stage (no mail): compose Ledger, Pipeline pages and Calendar evidence into the Daily Report region.

Read-only toward every source except `confirm_rejected`, the only Ledger write (two properties of one confirmed row).
A
source read that is not provably complete counts as unavailable and the run shows DEGRADED with the last accepted
state,
never an empty table. Lives under sources because it may read the Jobs reader; `lifeos/hiring` imports platform only.
Outputs: counts and fixed codes only.
"""
import contextlib
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.hiring import auto_close, compile as C, decision, decision_sync, models as M, outcomes as outcome_mail, render, snapshot
from lifeos.jobs import hiring_pipeline, ledger
from lifeos.platform import db, notion_pages
from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.notion_text import plain

TZ = ZoneInfo("America/Chicago")
CALENDAR_PAGES = 40
PAST_DAYS, FUTURE_DAYS = 30, 45
MAX_PAGES = 40


def _text(prop, kind):
    return plain((prop or {}).get(kind)).strip()


def read_applied(client, stats=None):
    """Every Job Ledger row with Applied ticked: [{"company", "role", "applied_on"}]. Anything short of a provably
    complete read raises."""
    if ledger.verify(client) != ledger.OK:
        raise NotionError("HIRING_LEDGER_TARGET")
    out = []
    for page in notion_pages.query(client, None, {"page_size": 100, "filter": {"property": "Applied", "checkbox": {"equals": True}}}, NotionError, incomplete="HIRING_LEDGER_INCOMPLETE", limit=MAX_PAGES):
        for row in page["results"]:
            props = row.get("properties") or {}
            when = ((props.get("Applied On") or {}).get("date") or {}).get("start")
            out.append({"company": _text(props.get("Company"), "rich_text"), "role": _text(props.get("Job"), "title"), "applied_on": when[:10] if when else None,
                        "id": row.get("id"), "decision": ((props.get(decision.DECISION) or {}).get("select") or {}).get("name"),
                        "hand": M.hand_lines(_text(props.get(decision.HAND_SET), "rich_text"))})
    if stats is not None:
        stats.update(raw=len(out), unnamed=sum(1 for r in out if not (r["company"] and r["role"])), undated=sum(1 for r in out if not r["applied_on"]))
    return [r for r in out if r["company"] and r["role"]]


def _pipeline_client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _day(value):
    return render.local(value).strftime("%b %-d")


def read_parents(client, root_id, ledger_client=None):
    """Opportunity pages under Active and Retired Opportunities (read only) -> [{"id", "company", "role", "rounds",
    "retired"}].

    With `ledger_client`, pursuits come from Ledger rows with Stage set first, the page tree filling in the rest; "id"
    is the
    page-tree page, "row_id" the Ledger row page.
    """
    found, status = hiring_pipeline.snapshot(client, root_id, ledger=ledger_client)
    if status != "ok":
        raise NotionError("HIRING_PAGES_UNREADABLE")
    return [{"id": o.tree_id or o.page_id, "company": o.company, "role": o.role, "rounds": o.rounds, "retired": o.section == hiring_pipeline.RETIRED, "lead": o.lead, "replied": o.replied, "holds": o.holds, "row_id": o.page_id if o.from_row else None} for o in found]


def _with_deleted(calendar, time_min, time_max):
    """The window's single events including cancelled ones (showDeleted), read completely, or refused."""
    params, out = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime", "maxResults": 250, "showDeleted": "true"}, []
    for _ in range(CALENDAR_PAGES):
        page = calendar.request("GET", "/events", params)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise NotionError("HIRING_CALENDAR_INCOMPLETE")
        out += page["items"]
        token = page.get("nextPageToken")
        if not token:
            return out
        if not isinstance(token, str):
            raise NotionError("HIRING_CALENDAR_INCOMPLETE")
        params = {**params, "pageToken": token}
    raise NotionError("HIRING_CALENDAR_INCOMPLETE")


def read_events(calendar, now, cancelled=None):
    """Accepted events in the window (not cancelled, not declined, timed) -> [{"id", "title", "start"}], plus
    "meeting" when joining details exist.

    With a `cancelled` list, cancelled events that still name a title are added to it as {"id", "title"}.
    """
    window = ((now - timedelta(days=PAST_DAYS)).isoformat(), (now + timedelta(days=FUTURE_DAYS)).isoformat())
    raw = calendar.list_events(*window) if cancelled is None else _with_deleted(calendar, *window)
    if not isinstance(raw, list):
        raise NotionError("HIRING_CALENDAR_INCOMPLETE")
    events = []
    for event in raw:
        if not isinstance(event, dict):
            raise NotionError("HIRING_CALENDAR_INCOMPLETE")
        if event.get("status") == "cancelled":
            if cancelled is not None and isinstance(event.get("id"), str) and (event.get("summary") or "").strip():
                cancelled.append({"id": event["id"], "title": event["summary"].strip()})
            continue
        if event.get("eventType") == "workingLocation":
            continue
        if any(isinstance(a, dict) and a.get("self") is True and a.get("responseStatus") == "declined" for a in event.get("attendees") or []):
            continue
        start = (event.get("start") or {}).get("dateTime")
        if not isinstance(start, str) or not isinstance(event.get("id"), str):
            continue
        events.append({"id": event["id"], "title": (event.get("summary") or "").strip(), "start": datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(TZ).isoformat()})
        try:                                                          # when the invite last changed: compared with a hand-set hold
            events[-1]["updated"] = datetime.fromisoformat(event["updated"].replace("Z", "+00:00")).astimezone(TZ).isoformat()
        except (KeyError, AttributeError, TypeError, ValueError):
            pass
        if joining := meeting(event):
            events[-1]["meeting"] = joining
    return events


def meeting(event):
    """An accepted invite's joining details, (join link, location), or None when it carries neither."""
    points = [p.get("uri") for p in (event.get("conferenceData") or {}).get("entryPoints") or [] if isinstance(p, dict) and p.get("entryPointType") == "video"]
    join = next((u for u in points + [event.get("hangoutLink")] if isinstance(u, str) and u.startswith("https://")), None)
    place = " ".join(str(event.get("location") or "").split())[:200] or None
    return (join, place) if join or place else None


def _stale_hours(environ):
    """The outcome-mail staleness limit: the real one, or a smaller number of hours from a manual proof input; it can
    only tighten."""
    raw = (environ.get("HIRING_OUTCOME_STALE_HOURS") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) < outcome_mail.STALE_HOURS else outcome_mail.STALE_HOURS


def run(limit, live, environ=os.environ, ledger_client=None, pipeline_client=None, calendar=None, connection=None, client=None, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    ok, applied, parents, events = {"ledger": True, "parents": True, "calendar": True}, [], [], []
    ledger_stats = {}
    ledger_client = ledger_client or Client(environ)
    try:
        applied = read_applied(ledger_client, ledger_stats)
    except Exception:
        ok["ledger"] = False
    root = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    try:
        if not root:
            raise NotionError("HIRING_PAGES_UNREADABLE")
        pipeline_client = pipeline_client or _pipeline_client(environ)
        parents = read_parents(pipeline_client, root, ledger_client)
    except Exception:
        ok["parents"] = False
    try:
        events = read_events(calendar or GoogleCalendar.from_env(environ), now)
    except Exception:
        ok["calendar"] = False
    with contextlib.ExitStack() as stack:
        conn = connection if connection is not None else stack.enter_context(db.connect())
        snapshot.STORE.ensure(conn)
        prior = snapshot.load(conn)
        outcome_reasons, unread = [], []
        try:
            heard, scanned, *rest = outcome_mail.load(conn)
            contacts = rest[0] if rest else {}
            unread = rest[1] if len(rest) > 1 else []
            if prior and prior.get("rows") and (scanned is None or datetime.fromisoformat(scanned) < now - timedelta(hours=_stale_hours(environ))):
                outcome_reasons.append("OUTCOME_MAIL_STALE")          # a failed or incomplete outcome-mail save is never shown as a fresh "no outcome"
            if unread:
                outcome_reasons.append("OUTCOME_MAIL_PARTIAL")
        except Exception:                                             # noqa: BLE001
            heard, contacts, outcome_reasons, unread = {}, {}, ["OUTCOME_MAIL_UNREADABLE"], []
        result = C.compile_rows(applied, parents, events, prior, ok, now, outcomes=heard, contacts=contacts)
        if outcome_reasons:
            result["reasons"] = sorted({*result["reasons"], *outcome_reasons})
            result["outcome_unread"] = unread
        auto = {"due": 0, "closed": [], "refused": {}, "switch": False}
        if all(ok.values()) and not result["reasons"] and result["rows"]:     # a known rejection closes itself only when every source was read and nothing is degraded
            auto = auto_close.run(result["rows"], environ, live, now, ledger_client, _day)
            gone = {e["key"] for e in auto["closed"]}
            result["rows"], result["closed"] = [r for r in result["rows"] if r["key"] not in gone], result["closed"] + len(gone)
        recent = auto_close.shown(prior, auto["closed"], now, [r["key"] for r in result["rows"] or []])
        _text, cells, counts = render.render(result, prior, now, (None, {}))
        if cells is not None:
            counts["rows"] += len(auto_close.cells(recent, _day)[:max(0, render.MAX_ROWS - len(cells))])
        counts["auto_close"] = {"due": auto["due"], "closed": len(auto["closed"]), "refused": auto["refused"], "switch": auto["switch"], "shown": len(recent)}
        counts.update({"applied": len(applied), "opportunities": sum(1 for p in parents if not p.get("retired")), "mail_outcomes": len(heard), "retired_pages": sum(1 for p in parents if p.get("retired")), "closed": result["closed"], "events": len(events), "unsupported": result["unsupported"],
                       "unmatched_events": result["unmatched_events"], "untitled_pages": result["untitled_pages"], "ledger": ledger_stats, "saved": 0, "blocks_written": 0, "sources": dict(ok)})
        new = snapshot.build(result, prior, now)
        if new is not None:
            new["auto_closed"] = recent
        if live and new is not None:
            snapshot.save(conn, new)
            counts["saved"] = 1
    counts["decision_sync"] = decision_sync.run(ledger_client, live, now)
    return counts
