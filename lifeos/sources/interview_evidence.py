"""LIFE-88: Interview OS's normal path (the `interview` stage, in the V7 tick). Composes the accepted evidence V7 can read today into the
existing writer's queries; it lives under sources because it reads the Ledger, the Calendar and Interview OS together.

Evidence, never inference. A machine UI 3c opportunity page names its job (Stable Job Key) and its request message in its own machine region;
the exact Job Ledger row for that key links it (progression); an existing round page supplies the round identity; an accepted Calendar
interview event adds a round only when its title states the round number and no round page already holds that number. Anything else is a fixed
code for that item alone: nothing is guessed, no round is numbered from the pages that exist, and one gap never stops the others. Writes need
INTERVIEW_OS_WRITER=v7 (Jim's activation switch); without it the run is a dry run. Counts and fixed codes only."""
import os
import re
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.hiring import compile as hiring
from lifeos.interview import acceptance, create, notion, stage
from lifeos.interview.models import Ownership
from lifeos.interview.progression import Invite, progress
from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.names import split_title
from lifeos.platform.runtime import RunContext
from lifeos.sources import hiring as pipeline

TZ = ZoneInfo("America/Chicago")
ROUND = re.compile(r"\b(?:interview|round)\s+(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+(?:interview|round)\b", re.I)
REQUEST = "Request message ↗"


def ordinal(title):
    """The one round number a title states, else None (none, or two different ones)."""
    found = {int(a or b) for a, b in ROUND.findall(title or "")}
    return found.pop() if len(found) == 1 else None


def _links(client, blocks, context, depth=0):
    for block in blocks:
        for run in (block.get(block.get("type")) or {}).get("rich_text") or []:
            url = ((run.get("text") or {}).get("link") or {}).get("url")
            if run.get("plain_text") == REQUEST and isinstance(url, str) and url.startswith("https://"):
                yield url
        if block.get("has_children") and block.get("type") != "child_page" and depth < 6:
            yield from _links(client, notion.children(client, block["id"], [notion.MAX_CALLS], context), context, depth + 1)


def facts(client, page_id, context):
    """(Stable Job Key or None, request URL or None) of a UI 3c page this writer wrote, else None."""
    machine, lines, _ = create.machine_region(client, page_id, context)
    renders = [m for m in (create.RENDER.fullmatch(notion.text(b)) for b in lines) if m]
    if machine is None or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity") or len(renders) != 1:
        return None
    urls = set(_links(client, machine, context))
    return (None if renders[0].group(1) == "none" else renders[0].group(1)), (urls.pop() if len(urls) == 1 else None)


def run(limit, live, environ=os.environ, client=None, calendar=None, now=None, ledger_rows=None, context=None):
    live = bool(live) and environ.get("INTERVIEW_OS_WRITER") == "v7"
    context, now, client = context or RunContext.start(120), (now or datetime.now(TZ)).astimezone(TZ), client or notion.make_client(environ)
    read_rows = ledger_rows or (lambda company: acceptance.ledger_rows(company, environ))
    why, evidence, ready = {}, [], {}

    def flag(code):
        why[code] = why.get(code, 0) + 1

    def queue(company, role, job, url, rows, date, who, number):
        made = progress(Invite(company, role, date, who, number, None, job, url), rows)
        if made.code == "ready":                                          # the dated shape drops the job and request: they are the page's own facts
            evidence.append((made.parent, replace(made.round, stable_job_key=job, source=url)))
        else:
            flag(made.code)

    scan = notion.parent_scan(client, (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip(), context)
    titles = [(p, *split_title(p.title)) for p in scan.items if p.active and scan.complete]
    for parent, company, role in titles:
        if not (company.strip() and role.strip()) or notion.ownership(client, parent.page_id, "opportunity") != Ownership.MACHINE:
            continue
        page = facts(client, parent.page_id, context)
        if page is None:
            flag("parent_legacy_layout")
        elif page[0] is None:
            flag("job_key_missing")
        else:
            try:
                ready[parent.page_id] = (company, role, *page, notion.child_scan(client, parent.page_id, context), read_rows(company))
            except Exception:                                                # noqa: BLE001 - a fixed code, never the exception text
                flag("ledger_unreadable")
    events = []
    try:
        for event in pipeline.read_events(calendar or GoogleCalendar.from_env(environ), now):
            at, _ = hiring.match_event(event["title"], [{"company": c, "role": r} for _, c, r in titles])
            if at is not None and hiring.event_kind(event["title"]) in ("interview", "hiring_manager") and titles[at][0].page_id in ready:
                events.append((titles[at][0].page_id, event))
    except Exception:                                                        # noqa: BLE001
        flag("calendar_unavailable")
    for page_id, event in events:                                            # a new round first: the refresh below then waits for the next run
        company, role, job, url, kids, rows = ready[page_id]
        held, number, day = [c for c in kids.items if c.identity_valid is True], ordinal(event["title"]), event["start"][:10]
        if number is None:
            flag("round_identity_incomplete")
        elif not kids.complete:
            flag("child_scan_incomplete")
        elif any(c.interview_date == day for c in held):
            continue                                                         # the round page exists
        elif any(c.ordinal == number for c in held):
            flag("round_conflict")                                           # the same number on another date: never a second page, never a rewrite
        else:
            queue(company, role, job, url, rows, day, None, number)
    for company, role, job, url, kids, rows in ready.values():
        held = [c for c in kids.items if c.identity_valid is True]
        if not kids.complete:
            flag("child_scan_incomplete")
        elif held:
            queue(company, role, job, url, rows, held[0].interview_date, held[0].interviewer, held[0].ordinal)
    counts = stage.run(limit, live, environ, client=client, evidence=evidence, context=context)
    counts["why"] = {**counts["why"], **why}
    counts["failed"] = sum(why.values()) - why.get("parent_legacy_layout", 0) + counts["blocked"]       # a UI 3b page waiting for migration is reported, not a failure
    return counts
