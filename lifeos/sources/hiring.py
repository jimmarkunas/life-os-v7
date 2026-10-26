"""Hiring Pipeline stage (HIRE-1.2, no mail): the one place that composes the canonical Job Ledger Applied state, the read-only Hiring Pipeline pages and accepted Calendar evidence
into the pure compiler, keeps one private prior-state row and writes the floating Daily Report region.

Lives under sources because it may read the Jobs reader; `lifeos/hiring` itself imports platform only. Read-only toward every source except one: the Hiring Decision action (D194, `confirm_rejected`, the only Ledger write, two properties of one confirmed row); no mail, no Calendar write, no Jira, no Interview page write. A source read that is not provably complete counts as unavailable, and the run shows DEGRADED with the last accepted state, never an empty table.
Counts and fixed codes only."""
import contextlib
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.hiring import auto_close, card, compile as C, decision, models as M, outcomes as outcome_mail, render, snapshot
from lifeos.jobs import hiring_pipeline, ledger
from lifeos.platform import db
from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.notion_client import Client, NotionError

TZ = ZoneInfo("America/Chicago")
CALENDAR_PAGES = 40                                               # the same page cap as the Calendar client's own listing
PAST_DAYS, FUTURE_DAYS = 30, 45                                  # the Calendar window the compiler may use as evidence
MAX_PAGES = 40


def _text(prop, kind):
    parts = (prop or {}).get(kind) or []
    return "".join((p.get("plain_text") or (p.get("text") or {}).get("content") or "") for p in parts).strip()


def read_applied(client, stats=None):
    """Every Job Ledger row with Applied ticked: [{"company", "role", "applied_on"}]. Anything short of a provably complete read raises (the caller marks the Ledger unavailable)."""
    if ledger.verify(client) != ledger.OK:
        raise NotionError("HIRING_LEDGER_TARGET")
    out, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        body = {"page_size": 100, "filter": {"property": "Applied", "checkbox": {"equals": True}}, **({"start_cursor": cursor} if cursor else {})}
        page = client.query_data_source(None, body)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list) or not isinstance(page.get("has_more"), bool):
            raise NotionError("HIRING_LEDGER_INCOMPLETE")
        for row in page["results"]:
            props = row.get("properties") or {}
            when = ((props.get("Applied On") or {}).get("date") or {}).get("start")
            out.append({"company": _text(props.get("Company"), "rich_text"), "role": _text(props.get("Job"), "title"), "applied_on": when[:10] if when else None,
                        "id": row.get("id"), "decision": ((props.get(decision.DECISION) or {}).get("select") or {}).get("name"),            # D194: the stable row id and a confirmed Hiring Decision, read only
                        "hand": M.hand_lines(_text(props.get(decision.HAND_SET), "rich_text"))})                                       # LIFE-111: a hand-set hold, once the Ledger has the property
        if not page["has_more"]:
            if stats is not None:
                stats.update(raw=len(out), unnamed=sum(1 for r in out if not (r["company"] and r["role"])), undated=sum(1 for r in out if not r["applied_on"]))
            return [r for r in out if r["company"] and r["role"]]
        cursor = page.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise NotionError("HIRING_LEDGER_INCOMPLETE")
        seen.add(cursor)
    raise NotionError("HIRING_LEDGER_INCOMPLETE")


def _pipeline_client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _day(value):
    return render.local(value).strftime("%b %-d")


def decide(request, live, environ=os.environ, ledger_client=None, now=None, **refresh):
    """D194/D195, one confirmed action end to end for ONE correlated pursuit: `decision.route` picks the Ledger application (exact write with read-back) or, when there is no ELIGIBLE APPLIED Ledger row (an unapplied row is not an application), the one Active Notes page
    (moved into Retired Opportunities, or back on Reopen); several matches or a Ledger-closed Reopen are refused with nothing written. THEN the Hiring Pipeline projection is refreshed, so a pursuit leaves the
    queue only after the decision was verified, and `cleared` needs the exact pursuit absent from the rendered table. Any decision error is raised before the projection is touched; a refresh that fails after a
    verified write is reported as `projection: FAILED` with the decision standing."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    client = ledger_client or Client(environ)
    if ledger.verify(client) != ledger.OK:
        raise NotionError("HIRING_LEDGER_TARGET")
    want, root, pipe = decision.parse(request), (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip(), refresh.get("pipeline_client") or _pipeline_client(environ)
    if not root:
        raise NotionError("HIRING_PAGES_UNREADABLE")
    kind, target = decision.route(read_applied(client), read_parents(pipe, root), want)
    if decision._page_id(target["id"]) != want["id"]:
        raise decision.fail("STALE_IDENTITY")
    if kind == "notes" and want["action"] == decision.ACTION and decision.unapplied_matches(client, want) != want["ack"]:
        raise decision.fail("LEDGER_UNAPPLIED")                        # a Ledger row for this company and role that is not marked Applied may be this application: mark it Applied (then it closes on the Ledger), or acknowledge exactly those row ids as not this application
    done = decision.confirm_rejected(client, request, live, now) if kind == "ledger" else decision.move_page(pipe, root, request, live, now, lambda: read_parents(pipe, root))
    done["cleared"] = False                                            # the queue is cleared only when the refreshed projection is fresh, its write verified and the exact pursuit is gone
    try:
        counts = run(0, live, environ, ledger_client=client, now=now, target=(request["company"], request["role"]), **refresh)
        done["projection"] = {k: counts[k] for k in ("status", "rows", "closed", "verified", "target_listed")}
        done["cleared"] = bool(live) and counts["status"] == "fresh" and counts["verified"] is True and counts["target_listed"] is False
    except Exception:                                                  # noqa: BLE001 - the verified decision must still be reported
        done["projection"] = "FAILED"
    return done


confirm_rejected = decide                                              # the D194 name


def read_parents(client, root_id):
    """Opportunity pages under Active and Retired Opportunities (read only) -> [{"id", "company", "role", "rounds", "retired"}]. D189: a page under Retired Opportunities means that pursuit is closed."""
    found, status = hiring_pipeline.snapshot(client, root_id)
    if status != "ok":
        raise NotionError("HIRING_PAGES_UNREADABLE")
    return [{"id": o.page_id, "company": o.company, "role": o.role, "rounds": o.rounds, "retired": o.section == hiring_pipeline.RETIRED, "lead": o.lead, "replied": o.replied, "holds": o.holds} for o in found]


def _with_deleted(calendar, time_min, time_max):
    """The window's single events including cancelled ones (showDeleted), read completely through the Calendar client's own request, or refused.
    The shared client's list_events leaves cancelled events out; this reader needs them to tell a cancelled invite from one outside the window."""
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
    """Accepted events in the window: not cancelled, not declined, timed (an all-day event is no interview time). -> [{"id", "title", "start"}], plus "meeting" when the invite carries joining details.
    With a `cancelled` list (LIFE-46 E07), the window is read with deleted events and each cancelled one that still names a title is added to it
    as {"id", "title"}; the accepted list is the same."""
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
        try:                                                          # LIFE-111: when the invite last changed, the evidence time a hand-set hold is compared with
            events[-1]["updated"] = datetime.fromisoformat(event["updated"].replace("Z", "+00:00")).astimezone(TZ).isoformat()
        except (KeyError, AttributeError, TypeError, ValueError):
            pass                                                      # no readable time: never evidence newer than a hold
        if joining := meeting(event):
            events[-1]["meeting"] = joining
    return events


def meeting(event):
    """E10: an accepted invite's own joining details, (join link, location), or None when it carries neither. The join link is the invite's video entry point,
    else its Meet link, https only; the location is the invite's own text. Nothing is read from the description or guessed."""
    points = [p.get("uri") for p in (event.get("conferenceData") or {}).get("entryPoints") or [] if isinstance(p, dict) and p.get("entryPointType") == "video"]
    join = next((u for u in points + [event.get("hangoutLink")] if isinstance(u, str) and u.startswith("https://")), None)
    place = " ".join(str(event.get("location") or "").split())[:200] or None
    return (join, place) if join or place else None


def close_ids(rows, applied, parents):
    """D194 S3: {row key: the page id `decision.route` would close it on}, the very rule the private Close screen runs; an ambiguous or unmatched pursuit has none."""
    ids = {}
    for r in rows or []:
        try:
            pid = decision._page_id(decision.route(applied, parents, {"company": r["company"], "role": r["role"], "action": decision.ACTION})[1].get("id"))
        except decision.DecisionError:
            continue
        if pid:
            ids[r["key"]] = pid
    return ids


def _stale_hours(environ):
    """The outcome-mail staleness limit: the real one, or a smaller whole number of hours from the manual LIFE-19 proof input. It can only tighten the limit, never loosen it; anything else is ignored."""
    raw = (environ.get("HIRING_OUTCOME_STALE_HOURS") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) < outcome_mail.STALE_HOURS else outcome_mail.STALE_HOURS


def run(limit, live, environ=os.environ, ledger_client=None, pipeline_client=None, calendar=None, connection=None, client=None, now=None, target=None):
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
        parents = read_parents(pipeline_client, root)
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
        outcome_reason = None
        try:
            heard, scanned, *rest = outcome_mail.load(conn)           # D190: what the mail scan last found; unavailable never stops the pipeline
            contacts = rest[0] if rest else {}                         # LIFE-26: each row's last direct contact (a scan saved before LIFE-26 has none)
            if prior and prior.get("rows") and (scanned is None or datetime.fromisoformat(scanned) < now - timedelta(hours=_stale_hours(environ))):
                outcome_reason = "OUTCOME_MAIL_STALE"                 # E13: a failed or incomplete outcome-mail save is never shown as a fresh "no outcome"
        except Exception:                                             # noqa: BLE001
            heard, contacts, outcome_reason = {}, {}, "OUTCOME_MAIL_UNREADABLE"
        result = C.compile_rows(applied, parents, events, prior, ok, now, outcomes=heard, contacts=contacts)
        if outcome_reason:                                            # DEGRADED, with the last accepted outcomes (none when the store cannot be read) and rows kept
            result["reasons"] = sorted({*result["reasons"], outcome_reason})
        auto = {"due": 0, "closed": [], "refused": {}, "switch": False}
        if all(ok.values()) and not result["reasons"] and result["rows"]:     # Jim, Oct 24: a known rejection closes itself, only when every source was read and nothing is degraded
            auto = auto_close.run(result["rows"], applied, parents, environ, live, now, ledger_client, pipeline_client, root, lambda: read_parents(pipeline_client, root), _day)
            gone = {e["key"] for e in auto["closed"]}
            result["rows"], result["closed"] = [r for r in result["rows"] if r["key"] not in gone], result["closed"] + len(gone)
        recent = auto_close.shown(prior, auto["closed"], now, [r["key"] for r in result["rows"] or []])
        text, cells, counts = render.render(result, prior, now, ((environ.get("V7_CLOSE_BASE_URL") or "").strip(), close_ids(result["rows"], applied, parents)))
        if cells is not None:
            extra = auto_close.cells(recent, _day)[:max(0, render.MAX_ROWS - len(cells))]     # shown for 24 hours as closed, then dropped; inside the table's row cap
            cells, counts["rows"] = cells + extra, counts["rows"] + len(extra)
        counts["auto_close"] = {"due": auto["due"], "closed": len(auto["closed"]), "refused": auto["refused"], "switch": auto["switch"], "shown": len(recent)}
        counts.update({"applied": len(applied), "opportunities": sum(1 for p in parents if not p.get("retired")), "mail_outcomes": len(heard), "retired_pages": sum(1 for p in parents if p.get("retired")), "closed": result["closed"], "events": len(events), "unsupported": result["unsupported"],
                       "unmatched_events": result["unmatched_events"], "untitled_pages": result["untitled_pages"], "ledger": ledger_stats, "saved": 0, "blocks_written": 0, "sources": dict(ok)})
        if target:                                                # D194: is the requested pursuit still in the table this run renders? (company and role never leave this function)
            shown = {c[0][0] for c in (cells or [])}                                   # the first cell is "Company — Role"
            counts["target_listed"] = None if cells is None else any(M.label(r["company"], r["role"]) in shown for r in result["rows"] or [] if M.same(r["company"], r["role"], *target))
        new = snapshot.build(result, prior, now)
        if new is not None:
            new["auto_closed"] = recent
        if live and new is not None:
            snapshot.save(conn, new)                              # saved with authoritative read-back; a failed save raises before the presentation is touched
            counts["saved"] = 1
    done = card.present(client or card.writer(environ), environ, text, cells, live)
    counts.update(rows_changed=done["rows_changed"], rows_added=done["rows_added"], rows_removed=done["rows_removed"], verified=done["verified"], header=done["header"])
    if live:
        counts["blocks_written"] = done["rows_changed"] + done["rows_added"] + 1
    return counts
