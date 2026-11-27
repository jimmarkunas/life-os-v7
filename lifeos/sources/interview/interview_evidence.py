"""The `interview` stage: compose the evidence V7 can read into the Interview writer's queries.

Evidence, never inference: an opportunity page names its job and request message, a round page supplies the round
identity, and an accepted Calendar event adds a round only when its title states the number. Anything else is a
fixed code for that item. Writes need INTERVIEW_OS_WRITER=v7, else a dry run."""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.hiring import compile as hiring, outcomes
from lifeos.jobs import classify as job_site, identity, lanes, ledger as jobs_ledger, publish, quality, store
from lifeos.interview import acceptance, create, notion, stage, view
from lifeos.interview.models import Ownership, ParentQuery, RoundQuery
from lifeos.interview.progression import Invite, progress
from lifeos.platform import ledger_claim, mail
from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.names import same_company, same_role, split_title, tokens
from lifeos.recruiters import classify, qualify
from lifeos.recruiters import notion as recruiters_store
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.runtime import RunContext
from lifeos.sources.pipeline import hiring as pipeline, manual_job
from lifeos.sources.interview import interview_sweep as sweep
from lifeos.sources.interview import mail_snapshot as snapshots
from lifeos.sources.interview import unreadable_streak as streaks

TZ = ZoneInfo("America/Chicago")
ROUND = re.compile(r"\b(?:interview|round)\s+(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+(?:interview|round)\b", re.I)
REQUEST = "Request message ↗"
LEAD_LANE = "Recruiter"
HEADLINE_AT = re.compile(
    r"(?:\bat\b|@)\s+(?P<c>[A-Z][A-Za-z0-9&'’.\-]*(?:\s+[A-Za-z0-9&'’.\-]+){0,3}?)\s*(?:[|,·(\n]|$)"
)
STAGE_SECONDS = 720
HELD_BLOCKS = ("human_page",)  # a hand-written page is never written to: `permanent`, not `failed`
LEADS_PER_RUN = 5
ASKS = re.compile(
    r"\b(?:schedul\w*|set up|arrange|book)\b[^.?!\n]{0,60}\b(?:interview|call|conversation|chat|meeting|time)\b|\binterview (?:invitation|request|availability)\b|"
    r"\binvit\w+ you to (?:an? )?(?:interview|call|chat)\b|\bselect (?:a|your) (?:time|slot)\b|\b(?:share|send|provide|confirm) your availability\b",
    re.I,
)
URL = re.compile(r"https?://[^\s<>\"')\]]+")
REQ = re.compile(
    r"\b(?:req(?:uisition)?|job|ref(?:erence)?)\s*(?:id|no\.?|number|#)?\s*[:#]?\s*([A-Z]{0,4}[-_]?\d{4,10})\b", re.I
)


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
            yield from _links(
                client, notion.children(client, block["id"], [notion.MAX_CALLS], context), context, depth + 1
            )


def facts(client, page_id, context):
    """(Stable Job Key or None, request URL or None) of an opportunity page this writer wrote, else None."""
    machine, lines, _ = create.machine_region(client, page_id, context)
    renders = [m for m in (create.RENDER.fullmatch(notion.text(b)) for b in lines) if m]
    if machine is None or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity") or len(renders) != 1:
        return None
    urls = set(_links(client, machine, context))
    return (None if renders[0].group(1) == "none" else renders[0].group(1)), (urls.pop() if len(urls) == 1 else None)


KEY_TEXT = re.compile(r"Stable Job Key\s+([a-z0-9][a-z0-9-]*)", re.I)


def legacy_facts(client, page_id, context):
    """(Stable Job Key, request URL or None) of an older-layout machine page: exactly one key, else None."""
    machine, lines, _ = create.machine_region(client, page_id, context)
    if (
        machine is None
        or not lines
        or not notion.is_marker(notion.text(lines[0]), "opportunity")
        or len(machine) < 2
        or not notion.text(machine[1]).startswith("Canonical opportunity parent")
    ):
        return None
    stack, keys = (
        list(notion.region_nodes(client, machine, [notion.TREE_MAX_CALLS], {b.get("id") for b in machine[:1]})),
        set(),
    )
    while stack:
        node = stack.pop()
        keys.update(m.lower() for m in KEY_TEXT.findall(node.get("text") or ""))
        stack += node.get("children", [])
    urls = set(_links(client, machine, context))
    return (keys.pop() if len(keys) == 1 else None), (urls.pop() if len(urls) == 1 else None)


def squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def matches(records, roster):
    """Pure. ({Applied row id: (row, message, requisition)}, codes) for messages that ask to schedule and name exactly
    one Applied row; the newest per row wins."""
    found, codes = {}, []
    for message in sorted(records, key=lambda m: m["received"]):
        text = f"{message.get('subject', '')}\n{(message.get('body') or '')[:3000]}"
        named = (
            [r for r in roster if outcomes.mentions(message, r["company"])]
            if ASKS.search(text) and not outcomes.kind_of(text)
            else []
        )
        by_role = [r for r in named if outcomes.role_in(message, r["role"])]
        if len({tuple(tokens(r["company"])) for r in named}) > 1 or len(by_role) > 1:
            codes.append("request_ambiguous")
        elif by_role:
            req = REQ.search(text)
            found[by_role[0]["id"]] = (by_role[0], message, req.group(1) if req else None)
    return found, codes


def headline_company(message):
    text = str(message.get("body") or "")
    cut = classify.RELAY_FOOTER.search(text)
    for line in (text[: cut.start()] if cut else text)[:1500].splitlines():
        found = classify.HEADLINE.match(line.strip()) if len(line) <= 260 else None
        at = HEADLINE_AT.search(found.group("headline")) if found else None
        if at:
            return at.group("c").strip()
    return ""


def posting_link(message):
    found = {u.rstrip(".,;:") for u in URL.findall(str(message.get("body") or "")[:20000])}
    found = {u for u in found if job_site.apply_kind(u) == "ats" and not quality.link_problem(u)}
    return found.pop() if len(found) == 1 else None


def reference(event_id):
    return sha1(str(event_id).encode()).hexdigest()[:12]


def mint(company, role, requisition=None):
    slug = lambda text: "-".join(re.findall(r"[a-z0-9]+", " ".join(tokens(text)).lower()))[:40]
    return "recruiter-" + slug(company) + "-" + (squash(requisition) if requisition else slug(role))


def lead_of(message):
    """Pure. A recruiter outreach that names a role -> (company, role, requisition, minted key, message link), else
    None. Company: the client, else the recruiter's, else the sender's headline employer."""
    shape, reason, evidence = classify.classify(message)
    verdict, found = qualify.qualify(message, shape, reason, evidence)
    company = (
        (
            found["client"]
            or found["company"]
            or (
                (headline_company(message) or f"LinkedIn · {found['person']}")
                if shape == classify.HUMAN_NAMED_RELAY
                else ""
            )
        )
        if verdict == "CANDIDATE"
        else ""
    )
    if not (company and found["role"] and str(message.get("url", "")).startswith("https://")):
        return None
    req = REQ.search(f"{message.get('subject', '')}\n{(message.get('body') or '')[:3000]}")
    link = posting_link(message)
    requisition = req.group(1) if req else None  # no posting: only the employer's requisition names the job
    key = identity.url_hash(link) if link else (mint(company, found["role"], requisition) if requisition else None)
    return company, found["role"], requisition, key, message["url"]


def window(environ, live):
    asked = (environ.get("INTERVIEW_MAIL_DAYS") or "").strip()
    return min(90, max(mail.WINDOW_DAYS, int(asked))) if asked.isdigit() and not live else mail.WINDOW_DAYS


def lead_job(company, role, link, message, now, connect=store.connect):
    """Enter a recruiter's posting link into Jobs OS like any job; True when the job is new."""
    received = (
        datetime.fromisoformat(str(message["received"]).replace("Z", "+00:00"))
        .astimezone(ZoneInfo("UTC"))
        .replace(tzinfo=None)
    )
    with connect() as connection, connection.cursor() as cursor:
        return manual_job.enter(
            cursor,
            link,
            company,
            role,
            "recruiter",
            "Recruiter outreach",
            received,
            now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
            "ats",
        )[1]


def lead_row(writer, company, role, key, url, today):
    """Create the lead's Ledger row once per Stable Job Key, read back -> 'created' | 'present'; a wrong target or
    differing read-back raises NotionError."""
    if jobs_ledger.verify(writer) != jobs_ledger.OK:
        raise NotionError("lead_row_target")
    claimant = ledger_claim.who("lead")
    state, _ = ledger_claim.claim(key, claimant)
    if state == ledger_claim.CREATED:
        return "present"
    if state == ledger_claim.HELD:  # adopt only: a held key may already have its page
        if publish.existing_page(writer, key):
            return "present"
        raise NotionError("lead_row_held")
    if publish.existing_page(writer, key):
        ledger_claim.release(key, claimant)
        return "present"
    page = writer.create(
        publish.properties(
            {
                "title": role,
                "company": company,
                "url": url,
                "provider": "Recruiter outreach",
                "lane": LEAD_LANE,
                "key": key,
                "first_seen": today,
                "eligible": [LEAD_LANE],
                "admission": lanes.ADMIT,
            }
        ),
        [],
    )
    props = writer.call("GET", f"/pages/{page}").get("properties") or {}
    if (
        view._plain(props.get("Stable Job Key")) != key
        or ((props.get("Visible Lane") or {}).get("select") or {}).get("name") != LEAD_LANE
    ):
        raise NotionError("lead_row_readback")
    ledger_claim.release(key, claimant)
    return "created"


def read_mail(
    environ, now, gmail=None, outlook_accounts=None, days=mail.WINDOW_DAYS, seconds=None, detail=None, reuse=None
):
    """-> (records, count of sources not read in full). Read only. `seconds` and `detail`, when given, are filled with
    timings and counts; `reuse` ({Gmail message id: record}) makes Gmail read only the rest."""
    records, down, limit = [], 0, mail.MAX_MESSAGES * -(-days // mail.WINDOW_DAYS)
    gmail_timing = {} if detail is not None else None
    reads = [
        (
            "gmail",
            lambda: mail.gmail_records(
                gmail or Gmail.from_env(),
                now,
                limit=limit,
                days=days,
                workers=MAIL_WORKERS,
                timing=gmail_timing,
                heal=True,
                **({"reuse": reuse} if reuse else {}),
            ),
        )
    ]
    for number, (label, account) in enumerate(
        outlook_accounts if outlook_accounts is not None else mail.outlook_accounts(environ), 1
    ):
        reads.append(
            (
                f"outlook_{number}",
                lambda a=account, l=label: mail.outlook_records(a, l, now, limit=limit, days=days) if a else ([], 1),
            )
        )
    for name, read in reads:
        began = time.monotonic()
        try:
            got, bad = read()
            records.extend(got)
            down += bad
        except (mail.MailError, GmailError):
            down += 1
        finally:
            if seconds is not None:
                seconds[name] = round(time.monotonic() - began, 1)
    if detail is not None:
        detail["gmail"] = gmail_timing
        down -= len(gmail_timing.get("unreadable") or {})
    return records, down


def recruiter_threads(environ, evidence, recruiters=None):
    """Attach each request's Recruiters rows, matched by an exactly equal Source URL -> (evidence, failed?)."""
    urls = sorted({q.source for _, q in evidence if (q.source or "").startswith("https://")})
    if not urls:
        return evidence, False
    if recruiters is None:
        if not (
            (environ.get("NOTION_RECRUITERS_TOKEN") or "").strip()
            and (environ.get("NOTION_RECRUITERS_DATA_SOURCE_ID") or "").strip()
        ):
            return evidence, False
        recruiters = Client(
            environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID"
        )
    found = {}
    try:
        for url in urls:
            page = recruiters.query_data_source(
                None, {"page_size": 20, "filter": {"property": "Source URL", "url": {"equals": url}}}
            )
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("results"), list)
                or page.get("has_more") is not False
            ):
                raise NotionError("RECRUITERS_QUERY_INCOMPLETE")
            rows = [r for r in map(recruiters_store.read, page["results"]) if r["source_url"] == url]
            found[url] = tuple(sorted({(r["person"].strip(), r["company"].strip(), r["source_url"]) for r in rows}))
    except NotionError:  # unknown is never "no recruiter"
        return [
            (p, replace(q, recruiters_state="unreadable") if (q.source or "").startswith("https://") else q)
            for p, q in evidence
        ], True
    return [
        (
            p,
            replace(q, recruiters=found[q.source], recruiters_state="linked" if found[q.source] else "none")
            if q.source in found
            else q,
        )
        for p, q in evidence
    ], False


GMAIL_THREAD = re.compile(r"https://mail\.google\.com/mail/u/0/#all/([0-9a-f]{8,32})")
SENT_SHOWN = 10
STILL_UNREADABLE = {"gmail_message_incomplete"}
MAIL_WORKERS = 4


def gmail_cause(error):
    text = str(error)
    status = re.search(r"HTTP (\d{3})\b", text)
    fixed = re.fullmatch(r"[A-Z][A-Z0-9_]{2,47}", text)
    return f"http_{status.group(1)}" if status else fixed.group(0).lower() if fixed else "other"


def sent_mail(environ, evidence, gmail=None, report=None):
    """Attach the owner's sent messages on each request's Gmail thread (metadata only) -> (evidence, failed?). A 404
    thread is unreadable but not `failed?`; any other error is. `report` gets each unreadable thread's cause code."""
    threads = sorted({m.group(1) for _, q in evidence if (m := GMAIL_THREAD.fullmatch((q.source or "").strip()))})
    if not threads:
        return evidence, False
    if gmail is None:
        names = ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN")
        if not all((environ.get(n) or "").strip() for n in names):
            return evidence, False
        gmail = Gmail(*(environ[n] for n in names))
    found, failed = {}, False

    def read_thread(thread):
        try:
            return (
                thread,
                tuple(
                    (at.isoformat(timespec="seconds"), subject)
                    for at, subject in gmail.thread_sent(thread)[-SENT_SHOWN:]
                ),
                None,
            )
        except GmailError as error:
            return thread, None, error

    if len(threads) > 1:
        first = [read_thread(threads[0])]
        with ThreadPoolExecutor(max_workers=MAIL_WORKERS) as pool:
            results = first + list(pool.map(read_thread, threads[1:]))
    else:
        results = [read_thread(thread) for thread in threads]
    for thread, shown, error in results:
        if error is None:
            found[thread] = (shown, "read")
            if report is not None:
                report[thread] = None
        else:
            cause = gmail_cause(error)
            found[thread], failed = ((), "unreadable"), failed or cause != "http_404"
            if report is not None:
                report[thread] = cause
    out = []
    for p, q in evidence:
        m = GMAIL_THREAD.fullmatch((q.source or "").strip())
        out.append((p, replace(q, sent_mail=found[m.group(1)][0], sent_mail_state=found[m.group(1)][1]) if m else q))
    return out, failed


CONTACTS_SHOWN = 5


def network_contacts(client, evidence, context=None, seconds=60):
    """Attach each request's job page Network Leads, at most CONTACTS_SHOWN, as (name, why) -> (evidence, failed?)."""
    from lifeos.network import surface as network

    out, failed, seen = [], False, {}
    clock = context or RunContext.start(seconds)
    for p, q in evidence:
        pid = q.ledger_page_id
        if not pid:
            out.append((p, q))
            continue
        if pid not in seen and clock.expired():
            seen[pid], failed = ((), "unreadable"), True
        if pid not in seen:
            try:
                leads = network.leads_on_page(client, pid, CONTACTS_SHOWN)
                seen[pid] = (tuple((lead["name"], lead["reason"]) for lead in leads), "linked" if leads else "none")
            except NotionError:
                seen[pid], failed = ((), "unreadable"), True
        contacts, state = seen[pid]
        out.append((p, replace(q, contacts=contacts, contacts_state=state)))
    return out, failed


def run(
    limit,
    live,
    environ=os.environ,
    client=None,
    calendar=None,
    now=None,
    ledger_rows=None,
    context=None,
    ledger=None,
    records=None,
    gmail=None,
    outlook_accounts=None,
    writer=None,
    recruiters=None,
    connect=None,
):
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip():
        from lifeos.sources.proofs import interview_journey_proof

        return interview_journey_proof.run(environ, live, now=now, client=client)
    live = bool(live) and environ.get("INTERVIEW_OS_WRITER") == "v7"
    injected = context
    context, now, client = (
        context or RunContext.start(120),
        (now or datetime.now(TZ)).astimezone(TZ),
        client or notion.make_client(environ),
    )
    read_rows = ledger_rows or (lambda company: acceptance.ledger_rows(company, environ))
    why, evidence, ready, where, permanent = {}, [], {}, {}, {}
    mail_seconds, mail_detail = {}, {}
    laps, mark = {}, [time.monotonic()]

    def lap(name):
        now_ = time.monotonic()
        laps[name] = round(laps.get(name, 0.0) + now_ - mark[0], 1)
        mark[0] = now_

    def flag(code, site=None, stays=False):
        why[code] = why.get(code, 0) + 1
        if stays:
            permanent[code] = permanent.get(code, 0) + 1
        if site:
            where[site] = where.get(site, 0) + 1

    def queue(company, role, job, url, rows, date, who, number, requisition=None, ref=None, meeting=None):
        made = progress(Invite(company, role, date, who, number, requisition, job, url), rows)
        if made.code == "ready":
            evidence.append(
                (made.parent, replace(made.round, stable_job_key=job, source=url, calendar_ref=ref, meeting=meeting))
            )
        else:
            flag(made.code, "progression_identity" if made.code == "round_identity_incomplete" else None)

    scan = notion.parents(client, environ, context)
    lap("pages_scan")
    titles = [(p, *split_title(p.title)) for p in scan.items if p.active and scan.complete]
    convert = {"adopt": 0, "upgrade": 0, "awaiting_adopt_line": 0, "migrate": 0}

    def conversion(company, role, how):
        convert[how] += 1
        evidence.append(
            (ParentQuery(company, role), RoundQuery(True, None, None, None, stable_job_key=mint(company, role)))
        )

    for parent, company, role in titles:
        if not (company.strip() and role.strip()):
            continue
        owner = notion.ownership(client, parent.page_id, "opportunity")
        named = notion.is_legacy_company(environ, company)
        if owner == Ownership.ADOPT:
            conversion(company, role, "adopt")
        elif owner == Ownership.HUMAN and named:
            convert["awaiting_adopt_line"] += 1
        if owner != Ownership.MACHINE:
            continue
        page = facts(client, parent.page_id, context)
        if page is None:
            try:
                tops = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
                has_notes = any(
                    b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES for b in tops
                )
            except Exception:  # noqa: BLE001
                has_notes = True
            legacy = legacy_facts(client, parent.page_id, context) if has_notes and named else None
            if legacy and legacy[0]:
                try:
                    ready[parent.page_id] = (
                        company,
                        role,
                        *legacy,
                        notion.child_scan(client, parent.page_id, context),
                        read_rows(company),
                    )
                    convert["migrate"] += 1
                except Exception:  # noqa: BLE001
                    flag("ledger_unreadable")
            elif has_notes:
                flag("parent_legacy_layout")
            else:
                conversion(company, role, "upgrade")
        elif page[0] is None:
            flag("job_key_missing")
        else:
            try:
                ready[parent.page_id] = (
                    company,
                    role,
                    *page,
                    notion.child_scan(client, parent.page_id, context),
                    read_rows(company),
                )
            except Exception:  # noqa: BLE001
                flag("ledger_unreadable")
    lap("page_reads")
    events, gone = [], []
    try:
        cancelled = []

        def keep(event, into):
            at = hiring.match_event(event["title"], [{"company": c, "role": r} for _, c, r in titles])[0]
            if (
                at is not None
                and hiring.event_kind(event["title"]) in ("interview", "hiring_manager")
                and titles[at][0].page_id in ready
            ):
                into.append((titles[at][0].page_id, event))

        for event in pipeline.read_events(calendar or GoogleCalendar.from_env(environ), now, cancelled):
            keep(event, events)
        for event in cancelled:
            keep(event, gone)
    except Exception:  # noqa: BLE001
        flag("calendar_unavailable")
    lap("calendar")
    recorded = {}

    def calendar_of(child):
        if child.page_id not in recorded:
            try:
                recorded[child.page_id] = create.round_calendar(client, child.page_id)
            except Exception:  # noqa: BLE001
                recorded[child.page_id] = (None, False)
        return recorded[child.page_id]

    def joined(child, ref, joining):
        try:
            found = create.round_logistics(client, child.page_id)
        except Exception:  # noqa: BLE001
            found = (None, False, None)
        recorded[child.page_id] = found[:2]
        return found[:2] == (ref, False) and found[2] != joining

    confirms, moves, cancels = {}, {}, {}
    for page_id, event in events:
        company, role, job, url, kids, rows = ready[page_id]
        held, number, day, ref = (
            [c for c in kids.items if c.identity_valid is True],
            ordinal(event["title"]),
            event["start"][:10],
            reference(event["id"]),
        )
        joining = event.get("meeting")
        if not kids.complete:
            flag("child_scan_incomplete")
        elif same := [c for c in held if c.interview_date == day]:
            if len(same) == 1 and joined(same[0], ref, joining):
                moves.setdefault(same[0].page_id, (page_id, same[0], set()))[2].add((day, joining))
        elif number in (None, 1) and len(held) == 1 and held[0].interview_date is None and held[0].ordinal == 1:
            confirms.setdefault(page_id, set()).add((day, ref, joining))
        elif len(linked := [c for c in held if c.interview_date and calendar_of(c)[0] == ref]) == 1:
            moves.setdefault(linked[0].page_id, (page_id, linked[0], set()))[2].add((day, joining))
        elif number is None:
            flag(
                "round_identity_incomplete",
                "calendar_event_pending_round"
                if any(c.interview_date is None for c in held)
                else "calendar_event_no_ordinal",
            )
        elif any(c.ordinal == number for c in held):
            flag("round_conflict")
        else:
            queue(company, role, job, url, rows, day, None, number, ref=ref, meeting=joining)
    for page_id, event in gone:
        kids = ready[page_id][4]
        for child in [c for c in kids.items if c.identity_valid is True and c.interview_date] if kids.complete else []:
            if calendar_of(child) == (reference(event["id"]), False):
                cancels[child.page_id] = (page_id, child)
    for page_id, found in confirms.items():
        company, role, job, url, kids, rows = ready[page_id]
        if len(found) > 1:
            flag("calendar_event_ambiguous", "calendar_event_pending_round")
            continue
        ((day, ref, joining),) = found
        pending = [c for c in kids.items if c.identity_valid is True][0]
        evidence.append(
            (
                ParentQuery(company, role),
                RoundQuery(
                    True,
                    day,
                    pending.interviewer,
                    1,
                    explicit_child_page_id=pending.page_id,
                    stable_job_key=job,
                    source=url,
                    calendar_ref=ref,
                    meeting=joining,
                ),
            )
        )
    for child_id, (page_id, child, days) in moves.items():
        company, role, job, url, kids, rows = ready[page_id]
        if len(days) > 1 or child_id in cancels:
            flag("calendar_event_ambiguous", "calendar_event_moved")
            continue
        day, joining = days.pop()
        evidence.append(
            (
                ParentQuery(company, role),
                RoundQuery(
                    True,
                    day,
                    child.interviewer,
                    child.ordinal,
                    explicit_child_page_id=child_id,
                    stable_job_key=job,
                    source=url,
                    calendar_ref=calendar_of(child)[0],
                    meeting=joining,
                ),
            )
        )
    for child_id, (page_id, child) in cancels.items():
        if child_id in moves:
            continue
        company, role, job, url, kids, rows = ready[page_id]
        evidence.append(
            (
                ParentQuery(company, role),
                RoundQuery(
                    True,
                    child.interview_date,
                    child.interviewer,
                    child.ordinal,
                    explicit_child_page_id=child_id,
                    stable_job_key=job,
                    source=url,
                    calendar_ref=calendar_of(child)[0],
                    cancelled=True,
                ),
            )
        )
    lap("round_reads")
    roster = []
    try:
        ledger = ledger or Client(
            environ, token_name="NOTION_INTERVIEW_TOKEN", source_name="NOTION_JOB_LEDGER_DATA_SOURCE_ID"
        )
        roster = pipeline.read_applied(ledger)
    except Exception:  # noqa: BLE001
        flag("ledger_unreadable")
    lap("ledger_roster")
    pursuits = [
        {"id": "page:" + p.page_id, "company": c, "role": r, "title": p.title, "pursuit": True}
        for p, c, r in titles
        if c.strip()
        and r.strip()
        and not any(same_company(c, a["company"]) and same_role(r, a["role"]) for a in roster)
    ]
    snapshot_why = None
    if records is None:
        began = time.monotonic()
        taken = (
            snapshots.take(environ, now, window(environ, live), connect)
            if gmail is None and outlook_accounts is None
            else {"why": "no_tick"}
        )
        if taken["why"] == "used":
            records, down = taken["records"], taken["down"]
            mail_detail.update(taken["detail"])
            mail_seconds["snapshot"] = round(time.monotonic() - began, 1)
        else:
            records, down = read_mail(
                environ, now, gmail, outlook_accounts, window(environ, live), mail_seconds, mail_detail
            )
        snapshot_why = None if taken["why"] == "no_tick" else taken["why"]
        if down:
            flag("mail_unavailable")  # a source read in part is never a trustworthy "no request"
    lap("mail")
    asked, codes = matches(records or [], roster + pursuits)
    for code in codes:
        flag(code)
    leads = [v for v in asked.values() if v[0].get("pursuit")]
    asked = {k: v for k, v in asked.items() if not v[0].get("pursuit")}
    retired_skips = 0
    for row, message, req in asked.values():
        try:
            props = ledger.call("GET", f"/pages/{row['id']}").get("properties") or {}
            key, apply_url = view._plain(props.get("Stable Job Key")), view._plain(props.get("Apply URL"))
        except Exception:  # noqa: BLE001
            flag("ledger_unreadable")
            continue
        page = next(
            (v for v in ready.values() if same_company(v[0], row["company"]) and same_role(v[1], row["role"])), None
        )
        _, _, page_job, page_url, page_kids, _ = page or (None,) * 6
        states = {
            p.active
            for p, c, r in ((p, *split_title(p.title)) for p in scan.items)
            if same_company(c, row["company"]) and same_role(r, row["role"])
        }
        if states == {False}:
            retired_skips += 1
            continue
        if not key:
            flag("job_key_missing")
        elif req and squash(req) not in squash(apply_url):
            flag("requisition_conflict")
        elif page and page_job != key:
            flag("identity_conflict")
        elif page and any(c.identity_valid is True for c in page_kids.items):
            if page_url != message["url"]:
                flag("round_identity_incomplete", "applied_row_other_request")
        elif not page or page_kids.complete:
            queue(
                row["company"],
                row["role"],
                key,
                message["url"],
                [
                    {
                        "page_id": row["id"],
                        "company": row["company"],
                        "role": row["role"],
                        "stable_job_key": key,
                        "requisition": None,
                    }
                ],
                None,
                None,
                None,
                req,
            )
    lap("ledger_requests")
    pursuit_requests, handled = 0, set()
    outreach = {
        m["url"]
        for m in records or []
        if lead_of(m) and not ASKS.search(f"{m.get('subject', '')}\n{(m.get('body') or '')[:3000]}")
    }
    for row, message, req in leads:
        page = ready.get(row["id"].split(":", 1)[1])
        if page is None:
            continue
        company, role, page_job, page_url, page_kids, page_rows = page
        handled.add(row["id"])
        keyed_by_requisition = (page_job or "").startswith("recruiter-") and page_job != mint(company, role)
        if req and keyed_by_requisition and page_job != mint(company, role, req):
            flag("requisition_conflict", "pursuit_requisition")
        elif any(c.identity_valid is True for c in page_kids.items):
            held = [c for c in page_kids.items if c.identity_valid is True]
            if page_url != message["url"] and page_url in outreach and len(held) == 1 and held[0].ordinal == 1:
                ready[row["id"].split(":", 1)[1]] = (company, role, page_job, message["url"], page_kids, page_rows)
            elif page_url != message["url"]:
                flag("round_identity_incomplete", "pursuit_other_request")
        elif page_kids.complete:
            queue(company, role, page_job, message["url"], page_rows, None, None, None, req)
            pursuit_requests += 1
        else:
            flag("child_scan_incomplete")
    counts_requests = len(asked)
    every = [split_title(p.title) for p in scan.items] + [(a["company"], a["role"]) for a in roster]
    fresh = {}
    for message in sorted(records if scan.complete else [], key=lambda m: m["received"]):
        found = lead_of(message)
        if found and not any(same_company(found[0], c) and same_role(found[1], r) for c, r in every):
            fresh[found[3] or ("review", message["url"])] = (found, message)
    leads_live = live and environ.get("INTERVIEW_LEADS") == "on"
    made_rows = made_jobs = 0
    review = {}
    groups = {}
    for lead, source in sorted(fresh.values(), key=lambda v: v[1]["received"], reverse=True):
        groups.setdefault((tuple(tokens(lead[0])), tuple(tokens(lead[1]))), []).append((lead, source))
    chosen = []
    for items in groups.values():
        keyed = [(lead, source) for lead, source in items if lead[3]]
        if not keyed:
            review["requisition_missing"] = review.get("requisition_missing", 0) + 1
        elif len({lead[3] for lead, source in keyed if not posting_link(source)}) > 1:
            review["requisition_ambiguous"] = review.get("requisition_ambiguous", 0) + 1
        elif len(chosen) < LEADS_PER_RUN:
            chosen.append(keyed[0])
    for (company, role, req, key, url), source in chosen:
        if live and not leads_live:
            continue
        link = posting_link(source)
        if not link:
            try:
                same = [
                    r
                    for r in read_rows(company)
                    if same_company(r.get("company") or "", company) and same_role(r.get("role") or "", role)
                ]
            except Exception:  # noqa: BLE001
                flag("ledger_unreadable")
                continue
            held = [
                r
                for r in same
                if r.get("stable_job_key") == key or (r.get("requisition") and squash(r["requisition"]) == squash(req))
            ]
            if len(held) > 1 or (same and not held):
                flag("identity_conflict", "lead_requisition")
                continue
            key = held[0]["stable_job_key"] if held else key
        if leads_live:
            try:
                if link:
                    made_jobs += bool(lead_job(company, role, link, source, now))
                else:
                    made_rows += lead_row(writer or Client(environ), company, role, key, url, now.date()) == "created"
            except Exception:  # noqa: BLE001
                flag("lead_row_failed")
                continue
        evidence.append(
            (
                ParentQuery(company, role),
                RoundQuery(True, None, None, 1, source=url, requisition=req, stable_job_key=key),
            )
        )
    leads_refresh = []
    for company, role, job, url, kids, ledger_rows_of in ready.values():
        held = [c for c in kids.items if c.identity_valid is True]
        if not kids.complete:
            flag("child_scan_incomplete")
        elif held:
            queue(company, role, job, url, ledger_rows_of, held[0].interview_date, held[0].interviewer, held[0].ordinal)
        elif (
            not kids.items and job
        ):  # a keyed machine page with no rounds is refreshed from its key, never given a round
            leads_refresh.append(
                (
                    ParentQuery(company, role),
                    RoundQuery(True, None, None, None, source=url, stable_job_key=job, refresh_only=True),
                )
            )
    evidence += [
        item
        for item in leads_refresh
        if (item[0].company, item[0].role) not in {(p.company, p.role) for p, _ in evidence}
    ]
    lap("decisions")
    evidence, unreadable = recruiter_threads(environ, evidence, recruiters)
    lap("recruiters")
    if unreadable:
        flag("recruiters_unreadable")
    evidence, unreadable = network_contacts(client, evidence)
    lap("network")
    if unreadable:
        flag("network_unreadable")
    reads, causes = {}, {}
    evidence, _ = sent_mail(environ, evidence, gmail, reads)
    for message_id, code in ((mail_detail.get("gmail") or {}).get("unreadable") or {}).items():
        reads["message:" + message_id] = code.lower()
    unreadable = {thread: cause for thread, cause in reads.items() if cause}
    before = streaks.previous(connect) if reads else None
    runs = {streaks.label(thread): (before or {}).get(streaks.label(thread), 0) + 1 for thread in unreadable}
    for thread, cause in sorted(unreadable.items()):
        if thread.startswith("message:"):
            flag(
                "mail_message_unreadable",
                stays=cause in STILL_UNREADABLE or runs[streaks.label(thread)] > streaks.FAILED_RUNS,
            )
            continue
        flag("sent_mail_unreadable", stays=cause == "http_404" or runs[streaks.label(thread)] > streaks.FAILED_RUNS)
        causes[cause] = causes.get(cause, 0) + 1
    kept = not reads or not live or streaks.save(runs, before, connect)
    lap("sent_mail")
    counts = stage.run(
        limit,
        live,
        environ,
        client=client,
        evidence=evidence,
        context=injected or RunContext.start(STAGE_SECONDS),
        roster=True,
    )
    lap("writer")
    if where:
        counts["where"] = where
    if mail_detail.get("gmail", {}).get("unreadable"):
        counts["mail_causes"] = {}
        for code in mail_detail["gmail"]["unreadable"].values():
            counts["mail_causes"][code.lower()] = counts["mail_causes"].get(code.lower(), 0) + 1
    if causes:
        counts["sent_mail_causes"], counts["sent_mail_runs"] = causes, max(runs.values())
    if (before is None or not kept) and any(cause != "http_404" for cause in causes):
        counts["streak_store"] = "unavailable"
    held = {code: counts["blocked_why"][code] for code in HELD_BLOCKS if counts.get("blocked_why", {}).get(code)}
    counts["why"], counts["requests"], counts["leads"], counts["lead_rows"], counts["lead_jobs"] = (
        {**counts["why"], **why},
        counts_requests,
        len(fresh),
        made_rows,
        made_jobs,
    )
    counts["leads_this_run"] = len(chosen)
    counts["lead_review"] = review
    counts["convert"] = convert
    if sweep.mode(environ) and scan.complete:
        counts["sweep"] = sweep.run(
            environ,
            live,
            client,
            scan,
            mint,
            lambda c, page_id, context: (legacy_facts(c, page_id, context) or (None,))[0],
            context=RunContext.start(sweep.SWEEP_SECONDS),
        )
    counts["lead_requests"] = {}
    counts["pursuit_requests"] = pursuit_requests
    counts["retired_skipped"] = retired_skips
    for row, *_ in [v for v in leads if v[0]["id"] not in handled]:
        label = sha1(row["title"].encode()).hexdigest()[:6]
        counts["lead_requests"][label] = counts["lead_requests"].get(label, 0) + 1
    counts["permanent"] = {**permanent, **held}
    counts["failed"] = (
        sum(why.values())
        - sum(permanent.values())
        + counts["blocked"]
        - sum(held.values())
        + counts.get("sweep", {}).get("failed", 0)
    )
    lap("after_writer")
    counts["mail_gmail"] = mail_detail.get("gmail") or {}
    counts["mail_seconds"] = mail_seconds
    if snapshot_why:
        counts["mail_snapshot"] = snapshot_why
    counts["seconds"] = {**laps, "total": round(sum(laps.values()), 1)}
    return counts
