"""LIFE-88: Interview OS's normal path (the `interview` stage, in the V7 tick). Composes the accepted evidence V7 can read today into the
existing writer's queries; it lives under sources because it reads the Ledger, the Calendar and Interview OS together.

Evidence, never inference. A machine UI 3c opportunity page names its job (Stable Job Key) and its request message in its own machine region;
the exact Job Ledger row for that key links it (progression); an existing round page supplies the round identity; an accepted Calendar
interview event adds a round only when its title states the round number and no round page already holds that number. Anything else is a fixed
code for that item alone: nothing is guessed, no round is numbered from the pages that exist, and one gap never stops the others. Writes need
INTERVIEW_OS_WRITER=v7 (Jim's activation switch); without it the run is a dry run. Counts and fixed codes only."""
import os
import re
from hashlib import sha1
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.hiring import compile as hiring, outcomes
from lifeos.jobs import classify as job_site, identity, intake, lanes, ledger as jobs_ledger, publish, quality, store
from lifeos.interview import acceptance, create, notion, stage, view
from lifeos.interview.models import Ownership, ParentQuery, RoundQuery
from lifeos.interview.progression import Invite, progress
from lifeos.platform import mail
from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.names import same_company, same_role, split_title, tokens
from lifeos.recruiters import classify, qualify
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.runtime import RunContext
from lifeos.sources import hiring as pipeline, interview_sweep as sweep

TZ = ZoneInfo("America/Chicago")
ROUND = re.compile(r"\b(?:interview|round)\s+(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+(?:interview|round)\b", re.I)
REQUEST = "Request message ↗"
LEAD_LANE = "Recruiter"                # the Job Ledger's Visible Lane for a recruiter lead (Notion adds the option on first write)
HEADLINE_AT = re.compile(r"(?:\bat\b|@)\s+(?P<c>[A-Z][A-Za-z0-9&'’.\-]*(?:\s+[A-Za-z0-9&'’.\-]+){0,3}?)\s*(?:[|,·(\n]|$)")
STAGE_SECONDS = 720                    # the budget of the Notion write stage, started after the mail has been read: run 39556923423 spent 300 s on page scans and wrote nothing; the recruiters job limit is 30 minutes
LEADS_PER_RUN = 5                      # new lead pages per tick, newest first: the first live run drains a 14-day backlog over a few ticks instead of in one burst
ASKS = re.compile(r"\b(?:schedul\w*|set up|arrange|book)\b[^.?!\n]{0,60}\b(?:interview|call|conversation|chat|meeting|time)\b|\binterview (?:invitation|request|availability)\b|"
                  r"\binvit\w+ you to (?:an? )?(?:interview|call|chat)\b|\bselect (?:a|your) (?:time|slot)\b|\b(?:share|send|provide|confirm) your availability\b", re.I)
URL = re.compile(r"https?://[^\s<>\"')\]]+")
REQ = re.compile(r"\b(?:req(?:uisition)?|job|ref(?:erence)?)\s*(?:id|no\.?|number|#)?\s*[:#]?\s*([A-Z]{0,4}[-_]?\d{4,10})\b", re.I)


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


KEY_TEXT = re.compile(r"Stable Job Key\s+([a-z0-9][a-z0-9-]*)", re.I)


def legacy_facts(client, page_id, context):
    """(Stable Job Key, request URL or None) of a machine page still in the accepted UI 3b layout (marker fold, then the "Canonical opportunity parent" callout), read from the
    page's own text: exactly one key, else None. Only the pursuits Jim named reach this (migration is authorized for them)."""
    machine, lines, _ = create.machine_region(client, page_id, context)
    if machine is None or not lines or not notion.is_marker(notion.text(lines[0]), "opportunity") or len(machine) < 2 or not notion.text(machine[1]).startswith("Canonical opportunity parent"):
        return None
    stack, keys = list(notion.region_nodes(client, machine, [notion.TREE_MAX_CALLS], {b.get("id") for b in machine[:1]})), set()
    while stack:
        node = stack.pop()
        keys.update(m.lower() for m in KEY_TEXT.findall(node.get("text") or ""))
        stack += node.get("children", [])
    urls = set(_links(client, machine, context))
    return (keys.pop() if len(keys) == 1 else None), (urls.pop() if len(urls) == 1 else None)


def squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def matches(records, roster):
    """E01a, pure: ({Applied row id: (row, message, requisition)}, codes). A message that asks to schedule a call or interview, names exactly one Applied row
    (the company's words in order, the role's words) and is not a rejection or an offer. Automated mail counts; the newest request per row wins."""
    found, codes = {}, []
    for message in sorted(records, key=lambda m: m["received"]):
        text = f"{message.get('subject', '')}\n{(message.get('body') or '')[:3000]}"
        named = [r for r in roster if outcomes.mentions(message, r["company"])] if ASKS.search(text) and not outcomes.kind_of(text) else []
        by_role = [r for r in named if outcomes.role_in(message, r["role"])]
        if len({tuple(tokens(r["company"])) for r in named}) > 1 or len(by_role) > 1:
            codes.append("request_ambiguous")                               # two companies, or two roles the text cannot tell apart: never guessed
        elif by_role:
            req = REQ.search(text)
            found[by_role[0]["id"]] = (by_role[0], message, req.group(1) if req else None)
    return found, codes


def headline_company(message):
    """The employer a LinkedIn sender's own headline names ("... at Acme"), read only from the sender's part of the notification (before the footer that describes Jim), else ''."""
    text = str(message.get("body") or "")
    cut = classify.RELAY_FOOTER.search(text)
    for line in (text[:cut.start()] if cut else text)[:1500].splitlines():
        found = classify.HEADLINE.match(line.strip()) if len(line) <= 260 else None
        at = HEADLINE_AT.search(found.group("headline")) if found else None
        if at:
            return at.group("c").strip()
    return ""


def posting_link(message):
    """The one public job-posting link (an employer ATS page for a single vacancy) a recruiter's message carries, else None; two different ones are not guessed between."""
    found = {u.rstrip(".,;:") for u in URL.findall(str(message.get("body") or "")[:20000])}
    found = {u for u in found if job_site.apply_kind(u) == "ats" and not quality.link_problem(u)}
    return found.pop() if len(found) == 1 else None


def mint(company, role, requisition=None):
    """The Stable Job Key of a recruiter lead or a converted pursuit that has no public posting: recruiter-<company>-<requisition or role>."""
    slug = lambda text: "-".join(re.findall(r"[a-z0-9]+", " ".join(tokens(text)).lower()))[:40]
    return "recruiter-" + slug(company) + "-" + (squash(requisition) if requisition else slug(role))


def lead_of(message):
    """E01b, pure: a recruiter outreach that names a role -> (company, role, requisition, minted Stable Job Key, message link), else None. Email and LinkedIn messages in the
    inbox alike (the recruiters classifier decides who is a recruiter). The company is the client when the message names one, else the recruiter's company; a LinkedIn message that
    names neither takes the employer in the sender's own headline, else the sender's name, never invented; a message with no company at all is not a lead."""
    shape, reason, evidence = classify.classify(message)
    verdict, found = qualify.qualify(message, shape, reason, evidence)
    company = (found["client"] or found["company"] or ((headline_company(message) or f"LinkedIn · {found['person']}") if shape == classify.HUMAN_NAMED_RELAY else "")) if verdict == "CANDIDATE" else ""
    if not (company and found["role"] and str(message.get("url", "")).startswith("https://")):
        return None
    req = REQ.search(f"{message.get('subject', '')}\n{(message.get('body') or '')[:3000]}")
    link = posting_link(message)                                         # a public posting is a real job: its Stable Job Key is the one Jobs OS gives that link, so the Ledger row will match
    key = identity.url_hash(link) if link else mint(company, found["role"], req.group(1) if req else None)
    return company, found["role"], req.group(1) if req else None, key, message["url"]


def window(environ, live):
    """Mail days to read: the usual 14 on every live tick (the job has a 20 minute limit and this step reads mail again after the Recruiters step), or a longer look
    (INTERVIEW_MAIL_DAYS, up to 90) on a manual dry run only."""
    asked = (environ.get("INTERVIEW_MAIL_DAYS") or "").strip()
    return min(90, max(mail.WINDOW_DAYS, int(asked))) if asked.isdigit() and not live else mail.WINDOW_DAYS


def lead_job(company, role, link, message, now, connect=store.connect):
    """E01b: a recruiter's public posting link enters Jobs OS like any job (RESOLVED at its own ATS link, lane Recruiter, source `recruiter`), so Enrich reads the posting, the
    static Fit and the Cloud Fit (Luna, public postings only) score it, and Publish writes its Ledger row. The recruiter's own email text never leaves this job.
    -> True when the job is new, False when Jobs OS already holds that link."""
    job = {"url": link, "status": "RESOLVED", "title": role, "company": company, "location": "", "salary": "", "source": "recruiter", "provider": "Recruiter outreach",
           "lane": lanes.RECRUITER, "age_days": None, "provider_score": None,
           "received": datetime.fromisoformat(str(message["received"]).replace("Z", "+00:00")).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)}
    stamp = now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    with connect() as connection, connection.cursor() as cursor:
        key, new = intake.add_job(cursor, job, stamp)
        if new:
            cursor.execute("UPDATE v7_jobs SET final_apply_url=%s, apply_kind='ats', updated_at=%s WHERE dedupe_key=%s", (link, stamp, key))
    return new


def lead_row(writer, company, role, key, url, today):
    """E01b: the lead's Job Ledger row, once per Stable Job Key (Visible Lane Recruiter, Admitted, not Applied; no fit gate: a recruiter wrote to Jim, whatever the role), read back.
    -> 'created' | 'present'; a wrong target, an ambiguous key or a read-back that differs raises NotionError."""
    if jobs_ledger.verify(writer) != jobs_ledger.OK:
        raise NotionError("lead_row_target")
    if publish.existing_page(writer, key):
        return "present"
    page = writer.create(publish.properties({"title": role, "company": company, "url": url, "provider": "Recruiter outreach", "lane": LEAD_LANE, "key": key, "first_seen": today,
                                             "eligible": [LEAD_LANE], "admission": lanes.ADMIT}), [])
    props = writer.call("GET", f"/pages/{page}").get("properties") or {}
    if view._plain(props.get("Stable Job Key")) != key or ((props.get("Visible Lane") or {}).get("select") or {}).get("name") != LEAD_LANE:
        raise NotionError("lead_row_readback")
    return "created"


def read_mail(environ, now, gmail=None, outlook_accounts=None, days=mail.WINDOW_DAYS):
    """-> (records, sources that could not be read in full). Read only; Gmail and Outlook are read whole, as the recruiters stage does, because a lead has no company on any roster."""
    records, down, limit = [], 0, mail.MAX_MESSAGES * -(-days // mail.WINDOW_DAYS)
    reads = [lambda: mail.gmail_records(gmail or Gmail.from_env(), now, limit=limit, days=days)]
    for label, account in outlook_accounts if outlook_accounts is not None else mail.outlook_accounts(environ):
        reads.append(lambda a=account, l=label: mail.outlook_records(a, l, now, limit=limit, days=days) if a else ([], 1))
    for read in reads:
        try:
            got, bad = read()
            records.extend(got)
            down += bad
        except (mail.MailError, GmailError):
            down += 1
    return records, down


def run(limit, live, environ=os.environ, client=None, calendar=None, now=None, ledger_rows=None, context=None, ledger=None, records=None, gmail=None, outlook_accounts=None, writer=None):
    live = bool(live) and environ.get("INTERVIEW_OS_WRITER") == "v7"
    injected = context
    context, now, client = context or RunContext.start(120), (now or datetime.now(TZ)).astimezone(TZ), client or notion.make_client(environ)
    read_rows = ledger_rows or (lambda company: acceptance.ledger_rows(company, environ))
    why, evidence, ready, where = {}, [], {}, {}

    def flag(code, site=None):
        why[code] = why.get(code, 0) + 1
        if site:                                                          # which check raised it, as a fixed word: counts only, never a title, id or address
            where[site] = where.get(site, 0) + 1

    def queue(company, role, job, url, rows, date, who, number, requisition=None):
        made = progress(Invite(company, role, date, who, number, requisition, job, url), rows)
        if made.code == "ready":                                          # the dated shape drops the job and request: they are the page's own facts
            evidence.append((made.parent, replace(made.round, stable_job_key=job, source=url)))
        else:
            flag(made.code, "progression_identity" if made.code == "round_identity_incomplete" else None)

    scan = notion.parent_scan(client, (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip(), context)
    titles = [(p, *split_title(p.title)) for p in scan.items if p.active and scan.complete]
    convert = {"adopt": 0, "upgrade": 0, "awaiting_adopt_line": 0, "migrate": 0}

    def conversion(company, role, how):                                      # a pursuit Jim named: its existing page becomes the UI 3c page through the page's own adopt / upgrade path
        convert[how] += 1
        evidence.append((ParentQuery(company, role), RoundQuery(True, None, None, None, stable_job_key=mint(company, role))))

    for parent, company, role in titles:
        if not (company.strip() and role.strip()):
            continue
        owner = notion.ownership(client, parent.page_id, "opportunity")
        named = notion.is_legacy_company(environ, company)
        if owner == Ownership.ADOPT:
            conversion(company, role, "adopt")                               # Jim's own `LIFE OS: adopt` line is the opt-in, for any page
        elif owner == Ownership.HUMAN and named:
            convert["awaiting_adopt_line"] += 1                              # a named page without his `LIFE OS: adopt` first line: nothing is written to it
        if owner != Ownership.MACHINE:
            continue
        page = facts(client, parent.page_id, context)
        if page is None:
            try:
                tops = notion.children(client, parent.page_id, [notion.MAX_CALLS], context)
                has_notes = any(b.get("type", "").startswith("heading") and notion.text(b) in notion.NOTES for b in tops)
            except Exception:                                                # noqa: BLE001 - an unread page is a visible gap, never an upgrade
                has_notes = True
            legacy = legacy_facts(client, parent.page_id, context) if has_notes and named else None
            if legacy and legacy[0]:
                try:                                                         # a named UI 3b page: its own key and request, then the existing refresh migrates it (create.refresh(migrate=True))
                    ready[parent.page_id] = (company, role, *legacy, notion.child_scan(client, parent.page_id, context), read_rows(company))
                    convert["migrate"] += 1
                except Exception:                                            # noqa: BLE001 - a fixed code, never the exception text
                    flag("ledger_unreadable")
            elif has_notes:
                flag("parent_legacy_layout")                                 # a UI 3b page waits for its own migration (only the pursuits Jim named are migrated)
            else:
                conversion(company, role, "upgrade")                         # V7's own skeleton page (LIFE-46 option B): the approved sections go in once, Jim's notes protected
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
        if not kids.complete:
            flag("child_scan_incomplete")
        elif any(c.interview_date == day for c in held):
            continue                                                         # the round page exists: no number is needed to add nothing
        elif number is None:                                                 # a new round is never numbered from the pages; E07 names the pending case
            flag("round_identity_incomplete", "calendar_event_pending_round" if any(c.interview_date is None for c in held) else "calendar_event_no_ordinal")
        elif any(c.ordinal == number for c in held):
            flag("round_conflict")                                           # the same number on another date: never a second page, never a rewrite
        else:
            queue(company, role, job, url, rows, day, None, number)
    roster = []
    try:
        ledger = ledger or Client(environ, token_name="NOTION_INTERVIEW_TOKEN", source_name="NOTION_JOB_LEDGER_DATA_SOURCE_ID")
        roster = pipeline.read_applied(ledger)
    except Exception:                                                        # noqa: BLE001
        flag("ledger_unreadable")
    pursuits = [{"id": "page:" + p.page_id, "company": c, "role": r, "title": p.title, "pursuit": True} for p, c, r in titles
                if c.strip() and r.strip() and not any(same_company(c, a["company"]) and same_role(r, a["role"]) for a in roster)]
    if records is None:
        records, down = read_mail(environ, now, gmail, outlook_accounts, window(environ, live))
        if down:
            flag("mail_unavailable")                                         # a source read in part is never a trustworthy "no request"
    asked, codes = matches(records or [], roster + pursuits)
    for code in codes:
        flag(code)
    leads = [v for v in asked.values() if v[0].get("pursuit")]                 # a request for a pursuit with no Applied row: the lead path (E01b), reported here, never written
    asked = {k: v for k, v in asked.items() if not v[0].get("pursuit")}
    for row, message, req in asked.values():                                 # E01a: a scheduling request for a job Jim applied to -> Interview 1, Scheduling Pending
        try:
            props = ledger.call("GET", f"/pages/{row['id']}").get("properties") or {}
            key, apply_url = view._plain(props.get("Stable Job Key")), view._plain(props.get("Apply URL"))
        except Exception:                                                    # noqa: BLE001
            flag("ledger_unreadable")
            continue
        page = next((v for v in ready.values() if same_company(v[0], row["company"]) and same_role(v[1], row["role"])), None)
        _, _, page_job, page_url, page_kids, _ = page or (None,) * 6
        if not key:
            flag("job_key_missing")
        elif req and squash(req) not in squash(apply_url):
            flag("requisition_conflict")                                     # the message names a requisition the Applied row's Apply URL does not carry
        elif page and page_job != key:
            flag("identity_conflict")
        elif page and any(c.identity_valid is True for c in page_kids.items):
            if page_url != message["url"]:
                flag("round_identity_incomplete", "applied_row_other_request")      # another request for a job that already has a round: its round number is unknown
        elif not page or page_kids.complete:
            queue(row["company"], row["role"], key, message["url"], [{"page_id": row["id"], "company": row["company"], "role": row["role"], "stable_job_key": key, "requisition": None}],
                  None, None, None, req)
    pursuit_requests, handled = 0, set()
    for row, message, req in leads:                                          # M1 (LIFE-25): a request for a keyed V7 pursuit page with no Applied row is that page's Interview 1
        page = ready.get(row["id"].split(":", 1)[1])
        if page is None:
            continue                                                         # no key on the page: counted below, never guessed
        company, role, page_job, page_url, page_kids, page_rows = page
        handled.add(row["id"])
        if any(c.identity_valid is True for c in page_kids.items):
            if page_url != message["url"]:
                flag("round_identity_incomplete", "pursuit_other_request")          # the page already has a round and names another message: its number is unknown
        elif page_kids.complete:
            queue(company, role, page_job, message["url"], page_rows, None, None, None, req)
            pursuit_requests += 1
        else:
            flag("child_scan_incomplete")
    counts_requests = len(asked)
    every = [split_title(p.title) for p in scan.items] + [(a["company"], a["role"]) for a in roster]
    fresh = {}                                                               # one lead per job: Stable Job Key -> (lead, its own message); the newest message wins
    for message in sorted(records if scan.complete else [], key=lambda m: m["received"]):     # E01b: recruiter outreach that names a role is the first warning; no reply is needed
        found = lead_of(message)
        if found and not any(same_company(found[0], c) and same_role(found[1], r) for c, r in every):      # a retired page counts: a closed lead never comes back
            fresh[found[3]] = (found, message)
    leads_live = live and environ.get("INTERVIEW_LEADS") == "on"              # Jim's second switch: lead rows, jobs and pages stay off until it is "on"
    made_rows = made_jobs = 0
    chosen, titles_taken = [], set()
    for lead, source in sorted(fresh.values(), key=lambda v: v[1]["received"], reverse=True):
        title = (tuple(tokens(lead[0])), tuple(tokens(lead[1])))
        if title not in titles_taken and len(chosen) < LEADS_PER_RUN:        # two links for one company and role are one pursuit, never two pages
            titles_taken.add(title)
            chosen.append((lead, source))
    for (company, role, req, key, url), source in chosen:
        if live and not leads_live:
            continue                                                         # live, but Jim has not turned leads on: nothing is written or queued for a lead
        if leads_live:                                                       # the job or Ledger row first: no page for a lead Jobs OS does not hold
            try:
                link = posting_link(source)                                  # this lead's own message, never another one's
                if link:
                    made_jobs += bool(lead_job(company, role, link, source, now))
                else:
                    made_rows += lead_row(writer or Client(environ), company, role, key, url, now.date()) == "created"
            except Exception:                                                # noqa: BLE001 - a fixed code, never the exception text
                flag("lead_row_failed")
                continue
        evidence.append((ParentQuery(company, role), RoundQuery(True, None, None, 1, source=url, requisition=req, stable_job_key=key)))
    for company, role, job, url, kids, ledger_rows_of in ready.values():
        held = [c for c in kids.items if c.identity_valid is True]
        if not kids.complete:
            flag("child_scan_incomplete")
        elif held:
            queue(company, role, job, url, ledger_rows_of, held[0].interview_date, held[0].interviewer, held[0].ordinal)
    counts = stage.run(limit, live, environ, client=client, evidence=evidence, context=injected or RunContext.start(STAGE_SECONDS))      # a fresh clock: the mail read above can take minutes, and the writes need their own budget
    if where:
        counts["where"] = where
    counts["why"], counts["requests"], counts["leads"], counts["lead_rows"], counts["lead_jobs"] = {**counts["why"], **why}, counts_requests, len(fresh), made_rows, made_jobs
    counts["leads_this_run"] = len(chosen)
    counts["convert"] = convert
    if sweep.mode(environ) and scan.complete:                                # Jim, Oct 21: every opportunity page becomes the new page; INTERVIEW_CONVERT=inventory only counts them
        counts["sweep"] = sweep.run(environ, live, client, scan, mint, lambda c, page_id, context: (legacy_facts(c, page_id, context) or (None,))[0], context=RunContext.start(sweep.SWEEP_SECONDS))
    counts["lead_requests"] = {}                                            # per pursuit, by a short hash of its title: logs carry counts only
    counts["pursuit_requests"] = pursuit_requests
    for row, *_ in [v for v in leads if v[0]["id"] not in handled]:
        label = sha1(row["title"].encode()).hexdigest()[:6]
        counts["lead_requests"][label] = counts["lead_requests"].get(label, 0) + 1
    counts["failed"] = sum(why.values()) + counts["blocked"] + counts.get("sweep", {}).get("failed", 0)      # a UI 3b page waiting for migration is DEGRADED too: it is not refreshed
    return counts
