"""LIFE-78 bounded synthetic M1/M2 exit: ONE finite case, an acquired vacancy to a second interview round, through real V7 code on native Notion QA
surfaces. Manual only: interview-proof with `confirm = M1M2` (Interview token only). Every write goes to a FRESH sandbox under the QA Interview OS parent:
its own Job Ledger and Recruiters data sources, a synthetic Daily Report page (the Hiring card's region) and its own Hiring Pipeline pages. Only the
outside sources are synthetic and in memory (the fetched page, the Fit profile, mail, Calendar; m1m2_fixture.json); nothing real is read or written,
nothing is sent or submitted. Independent read-back with a new client; PASS/FAIL per assertion, counts only. Gate B machine evidence, not Gate A."""
import copy
import json
import pathlib
import time
from datetime import date, datetime, timedelta

from lifeos.hiring import card, compile as hiring, models as HM, render
from lifeos.interview import create, notion
from lifeos.interview.models import Ownership, ParentQuery, RoundQuery
from lifeos.interview.ui_probe import QA_PARENT
from lifeos.jobs import enrich, identity, lanes, ledger as jobs_ledger, publish, quality
from lifeos.jobs.fit.profile import Profile
from lifeos.jobs.fit.score import evaluate
from lifeos.platform import report_region as R
from lifeos.platform.notion_client import NotionError, rich_text
from lifeos.recruiters import notion as rstore, outreach
from lifeos.recruiters.identity import week_ending
from lifeos.sources import hiring as pipeline, interview_evidence as ev
from lifeos.sources.interview_journey_proof import Calendar, _event, _outreach, _request

CONFIRM, MAX_TICKS = "M1M2", 6
COMPANY, ROLE, REQ, LANE = "SampleWorks", "Senior Technical Program Manager", "880042", "US Remote"
POSTING, REQUEST_URL = f"https://jobs.example.test/sampleworks/jobs/{REQ}", "https://example.test/m1m2/request"
TITLE, INVITE = f"{COMPANY} — {ROLE}", f"{COMPANY} {ROLE} Interview"
RECRUITER, AGENCY, DECOY_AGENCY = "Riley Synthetic", "Staffing Example Inc", "Other Staffing Example"     # the decoy has the SAME person name
FIXTURE = json.loads((pathlib.Path(__file__).with_name("m1m2_fixture.json")).read_text())
PROFILE, DESCRIPTION, SALARY, PLACE = Profile(FIXTURE["profile"]), FIXTURE["description_html"], "$170,000 - $190,000", "Remote, United States"
READ_FIELDS = {"Hiring Decision": "select", "Dismissed Outcome Ref": "rich_text"}       # what the Hiring readers read from a Ledger row
ACTIONS = ("Needs Action", "Waiting", *rstore.CONVERSATION, *rstore.HISTORY)
SLEEP, WAITS = time.sleep, 10                                             # a data-source query can trail a write: wait (bounded) to SEE the case's own write


def visible(probe):
    """Wait until `probe()` is truthy, at most WAITS tries two seconds apart. -> the last answer. Never writes."""
    answer = probe()
    for _ in range(WAITS):
        if answer:
            break
        SLEEP(2)
        answer = probe()
    return answer


def ats_page(posted, valid_through=None):
    """An invented, already-fetched employer ATS page carrying a schema.org JobPosting, as the Jobs detail stage receives it."""
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": ROLE, "description": DESCRIPTION, "datePosted": posted,
               "hiringOrganization": {"@type": "Organization", "name": COMPANY}, "jobLocationType": "TELECOMMUTE",
               "applicantLocationRequirements": {"@type": "Country", "name": "United States"}, **({"validThrough": valid_through} if valid_through else {})}
    return f"<html><head><title>{ROLE} - {COMPANY}</title><script type=\"application/ld+json\">{json.dumps(posting)}</script></head><body>{DESCRIPTION}</body></html>"


def ledger_schema():
    """The sandbox Ledger: the target guard's required set, every property the real publish path can write (all optional fields present), the read fields."""
    sample = {"title": ROLE, "company": COMPANY, "url": POSTING, "source": "s", "provider": "s", "lane": LANE, "key": "s", "first_seen": date(2026, 1, 1),
              "posted": date(2026, 1, 1), "fit": 80, "fit_line": "s", "admission": lanes.REVIEW, "admission_reason": "s", "eligible": [LANE], "work_mode": "remote",
              "salary": SALARY, "location": PLACE, "text": "s", "apply_kind": "easy_apply"}
    return {**jobs_ledger.REQUIRED, **{n: next(iter(v)) for n, v in publish.properties(sample).items()}, **READ_FIELDS}


def _scoped(client, source):
    """The same client (same token, same pacing) aimed at one sandbox data source."""
    scoped = copy.copy(client)
    scoped.source = source
    scoped.__dict__.pop("_ledger_target", None)
    return scoped


def _database(client, parent, title, properties):
    """One disposable data source under a sandbox page -> its id. A refusal raises: the run fails closed and says so."""
    made = client.call_once("POST", "/databases", {"parent": {"type": "page_id", "page_id": parent}, "title": rich_text(title), "initial_data_source": {"properties": properties}})
    sources = made.get("data_sources") if isinstance(made, dict) else None
    if not isinstance(sources, list) or len(sources) != 1 or not sources[0].get("id"):
        raise NotionError("M1M2_SANDBOX_DATABASE_UNREADABLE")
    return sources[0]["id"]


def _block(kind, text="", **extra):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text), **extra} if kind != "divider" else {}}


def report_page(client, container):
    """A synthetic Daily Report page with the Hiring Pipeline region in the shape the real card validates (an anchor on the same page, the heading, the table)."""
    header = {"object": "block", "type": "table_row", "table_row": {"cells": [rich_text(c) for c in HM.HEADERS]}}
    blocks = [_block("callout", "Synthetic anchor (stands in for the Jira card)"), _block("heading_3", HM.TITLE),
              {"object": "block", "type": "table", "table": {"table_width": 4, "has_column_header": True, "has_row_header": False, "children": [header]}}, _block("divider")]
    page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": container}, "properties": {"title": {"title": rich_text("Daily Report (synthetic)")}},
                                               "children": blocks})
    kids = notion.children(client, page["id"], [5])
    if [b.get("type") for b in kids][:4] != ["callout", "heading_3", "table", "divider"]:
        raise NotionError("M1M2_SANDBOX_REPORT_SHAPE")
    return {"JIRA_CARD_BLOCK_ID": kids[0]["id"], "HIRING_CARD_BLOCK_ID": kids[1]["id"], "HIRING_TABLE_BLOCK_ID": kids[2]["id"]}


def sandbox(client, now, report=None):
    """A fresh sandbox under the QA parent. -> its ids: pipeline, human page, Ledger and Recruiters data sources, the report's pinned blocks."""
    container = notion.write_tree(client, QA_PARENT, [], title=f"M1/M2 synthetic exit · {now.strftime('%Y-%m-%d %I:%M %p')} CT (synthetic)")
    pipe = notion.write_tree(client, container, [], title="Hiring Pipeline")
    active = notion.write_tree(client, pipe, [], title="Active Opportunities")
    human = notion.write_tree(client, active, [_block("paragraph", "Synthetic human notes. LIFE OS must never change this page.")], title="HumanCo — Manager")
    led = _database(client, container, "Job Ledger (synthetic)", {n: {k: {}} for n, k in ledger_schema().items()})
    rprops = {**{n: {k: {}} for n, k in rstore.SCHEMA.items()}, "Action Status": {"select": {"options": [{"name": n} for n in ACTIONS]}},
              "Job": {"relation": {"data_source_id": led, "single_property": {}}}}
    rec = _database(client, container, "Recruiters (synthetic)", rprops)
    return {"pipe": pipe, "human": human, "ledger": led, "recruiters": rec, "report": report_page(report or client, container)}


def _plain(prop, kind):
    return "".join(r.get("plain_text") or (r.get("text") or {}).get("content", "") for r in (prop or {}).get(kind) or [])


def _ledger_rows(ledger):
    """The Interview runner's per-company Ledger rows, read from the sandbox Ledger."""
    def rows(company):
        found = ledger.query_data_source(None, {"page_size": 100, "filter": {"property": "Company", "rich_text": {"equals": company}}})
        return [{"page_id": p["id"], "company": company, "role": _plain(p["properties"].get("Job"), "title"), "requisition": None,
                 "stable_job_key": _plain(p["properties"].get("Stable Job Key"), "rich_text")} for p in found.get("results") or []]
    return rows


def _rows_text(client, page_id):
    """Every text on a page as Notion holds it (nested blocks, table cells; not child pages), for presence checks only; never printed."""
    out, budget = [], [120]

    def walk(block_id, depth):
        for block in notion.children(client, block_id, budget):
            out.append(notion.text(block))
            out.extend("".join(p.get("plain_text") or (p.get("text") or {}).get("content", "") for p in cell) for cell in (block.get("table_row") or {}).get("cells") or [])
            if block.get("has_children") and block.get("type") != "child_page" and depth < 5:
                walk(block["id"], depth + 1)
    walk(page_id, 0)
    return "\n".join(out)


def _human_tree(client, page_id):
    return [{k: v for k, v in b.items() if k not in ("last_edited_time", "last_edited_by", "request_id")} for b in notion.children(client, page_id, [10])]


def _hiring_rows(client, pins):
    """The job's Hiring Pipeline rows as Notion holds them, through the region validator -> [[cell text] x 4]."""
    region = R.floating_region(client, pins["HIRING_CARD_BLOCK_ID"], HM.TITLE, NotionError, with_status=False)
    return [c for c in map(R.cells_of, region["rows"][1:]) if c[0] == HM.label(COMPANY, ROLE)]


def drive(client, environ, box, now, results, counts, report=None):
    """The case against a fresh sandbox; appends (assertion, pass) to `results`. Fixed names and counts only."""
    check = lambda name, ok: results.append((name, bool(ok)))      # noqa: E731
    today, day = now.date(), (lambda n: (now + timedelta(days=n)).strftime("%Y-%m-%dT10:00:00-05:00"))
    ledger, recruiters, report, pins = _scoped(client, box["ledger"]), _scoped(client, box["recruiters"]), report or client, {**environ, **box["report"]}
    found = enrich.parse_html(POSTING, ROLE, ats_page(today.isoformat()), lane=LANE, first_party=True)       # 1. acquisition, then identity, Fit, lane
    desc = found.get("description") or {}
    text = desc.get("full_text", "")
    check("1a acquisition: the JSON-LD page normalizes to READY with sections, the employer and today's posting date", found.get("outcome") == "ready"
          and found.get("company") == COMPANY and found.get("posted") == today and bool(desc.get("requirements")) and quality.jd_problem(text) is None)
    expired = enrich.parse_html(POSTING, ROLE, ats_page(today.isoformat(), (today - timedelta(days=1)).isoformat()), lane=LANE, first_party=True)
    check("1b acquisition refuses an expired posting (validThrough in the past) as closed", expired["outcome"] == "closed")
    tracked, key = POSTING + "?utm_source=newsletter&utm_medium=email", identity.url_key(POSTING)
    check("1c stable identity: the tracked link is the same vacancy", key == identity.url_key(tracked) and identity.vacancy_id(POSTING) == identity.vacancy_id(tracked)
          and quality.url_problem(POSTING) is None)
    fit = evaluate(ROLE, found.get("company") or "", text, PROFILE, today)
    check("1d Fit on the normalized description is Go at or above the floor", fit.decision == "Go" and fit.score is not None and fit.score >= lanes.FIT_FLOOR)
    clinical = text.replace("commerce platform", "clinical trial platform") + " Clinical trial operations experience. More clinical trial work."
    check("1e the same page with a clinical scope is No-Go", evaluate(ROLE, COMPANY, clinical, PROFILE, today).decision == "No-Go")
    decision, lane, eligible = lanes.decide_all(LANE, lanes.facts_for(fit.score, ROLE, found.get("location") or PLACE, text, SALARY, found.get("posted"), now, market="US",
                                                                       first_party=True), today)
    check("1f the US Remote lane admits it", decision.status == lanes.ADMIT and lane == LANE)
    row = {"title": ROLE, "company": found.get("company"), "url": POSTING, "source": "synthetic-ats", "provider": "synthetic-ats", "lane": lane, "key": key, "first_seen": today,
           "posted": found.get("posted"), "fit": fit.score, "fit_line": fit.line, "admission": decision.status, "eligible": eligible, "work_mode": "remote", "salary": SALARY,
           "location": found.get("location") or PLACE, "text": text}
    creates = 0
    for attempt in (POSTING, tracked, POSTING):                               # 2. the publish path: key lookup, then create; a replay and the tracked link adopt
        if not publish.existing_page(ledger, identity.url_key(attempt)):
            ledger.create(publish.properties({**row, "key": identity.url_key(attempt)}), publish.body_blocks(key, desc))
            creates += 1
            visible(lambda: publish.existing_page(ledger, key))
    job_id = publish.existing_page(ledger, key)
    check("2a one Ledger row after three publishes (replay and tracked link adopt it)", creates == 1 and bool(job_id) and jobs_ledger.verify(ledger) == jobs_ledger.OK)
    if not job_id:
        return
    job = {"id": job_id, "company": COMPANY, "role": ROLE}                  # 3-4. Jim's own outreach and application, simulated; nothing is sent or submitted
    sent, again = (outreach.mark_sent(recruiters, job, RECRUITER, now, box["ledger"], AGENCY) for _ in range(2))
    replied = outreach.set_conversation(recruiters, job, RECRUITER, rstore.REPLIED, box["ledger"])
    check("3a Mark as Sent records one Waiting conversation; a replay writes nothing", (sent["status"], again["status"]) == ("recorded", "already_recorded")
          and len(outreach.job_rows(recruiters, job_id)) == 1)
    check("3b the recorded reply moves it to Replied, read back", replied["status"] == "recorded" and outreach.job_rows(recruiters, job_id)[0]["action"] == "Replied")
    try:
        outreach.set_conversation(recruiters, job, "Someone Else", rstore.REPLIED, box["ledger"])
        refused = None
    except NotionError as error:
        refused = str(error)
    check("3c a status for a person never messaged is refused", refused == "OUTREACH_NOT_RECORDED")
    ledger.update_page_properties(job_id, {"Applied": {"checkbox": True}, "Applied On": {"date": {"start": today.isoformat()}}})
    visible(lambda: pipeline.read_applied(ledger))

    def hiring_card(events=()):                                              # 5. the real Hiring card from the sandbox Ledger and pipeline, read back from Notion
        result = hiring.compile_rows(pipeline.read_applied(ledger), pipeline.read_parents(client, box["pipe"]), list(events), None,
                                     {"ledger": True, "parents": True, "calendar": True}, now)
        status, cells, _ = render.render(result, None, now)
        done = card.present(report, pins, status, cells, True)
        counts["hiring_writes"] += done["rows_changed"] + done["rows_added"] + done["rows_removed"]
        return done["verified"], _hiring_rows(report, pins)
    verified, mine = hiring_card()
    check("5a Hiring Pipeline region written and verified: one row, Submitted", verified and len(mine) == 1 and mine[0][1].startswith("Submitted"))
    age = lambda hours: (now - timedelta(hours=hours)).isoformat()             # noqa: E731
    request, wrong = _request(COMPANY, ROLE, REQ, REQUEST_URL, age(24)), _request(COMPANY, ROLE, "999999", "https://example.test/m1m2/wrong-req", age(20))
    mail = (_outreach("Data Engineer", "OtherCo", "111111", "https://example.test/m1m2/amb-1", age(30)),
            _outreach("Data Engineer", "OtherCo", "222222", "https://example.test/m1m2/amb-2", age(29)),
            _request("NotAppliedCo", "Program Lead", "123456", "https://example.test/m1m2/stranger", age(19)))
    for agency, url in ((AGENCY, REQUEST_URL), (DECOY_AGENCY, "https://example.test/m1m2/another-thread")):      # the Recruiters stage's own create for each message
        rstore.create(recruiters, {"person": RECRUITER, "company": agency, "client": COMPANY, "role": ROLE, "medium": "Email", "what": "", "needs_review": False,
                                   "last_contact": (now - timedelta(hours=24)).replace(second=0, microsecond=0).isoformat(), "week_ending": week_ending(now - timedelta(hours=24)), "source_url": url})
    env = {**environ, "HIRING_PIPELINE_PAGE_ID": box["pipe"], "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on", "INTERVIEW_CONVERT": "", "INTERVIEW_TEST_COMPANIES": "",
           "INTERVIEW_JOURNEY_PROOF": ""}
    calendar = Calendar()

    def tick(records):                                                       # 6-7. the real Interview runner and Notion writer, with negatives and replays
        counts["ticks"] += 1
        done = ev.run(20, True, environ=env, client=client, calendar=calendar, now=now, ledger=ledger, records=list(records), ledger_rows=_ledger_rows(ledger),
                      writer=ledger, recruiters=recruiters)
        counts["writes"] += done["writes"]
        counts["tick_codes"].append(f"w{done['writes']}f{done['failed']}:" + ",".join(sorted(k for k in done["why"] if k not in ("child_match", "parent_unchanged"))))      # fixed codes only
        return done

    def settle(records):
        ticks = [tick(records)]
        while ticks[-1]["writes"] and len(ticks) < MAX_TICKS:
            ticks.append(tick(records))
        why = {}
        for t in ticks:
            for k, v in t["why"].items():
                why[k] = why.get(k, 0) + v
        return ticks, why, ticks[-1]["writes"] == 0 and ticks[-1]["failed"] == 0

    def opportunity():
        mine = [p for p in notion.parent_scan(client, box["pipe"]).items if p.active and p.title == TITLE]
        return mine, (notion.child_scan(client, mine[0].page_id).items if len(mine) == 1 else ())

    t, _, _ = settle(mail)
    check("7a ambiguous recruiter leads and a request for a job never applied to write nothing", sum(x["writes"] for x in t) == 0 and not opportunity()[0]
          and t[0]["lead_review"].get("requisition_ambiguous") == 1)
    _, _, quiet = settle(mail + (request,))
    opp, rounds = opportunity()
    check("6a the employer's request makes one opportunity and Interview 1, Scheduling Pending", quiet and len(opp) == 1 and len(rounds) == 1
          and rounds[0].ordinal == 1 and rounds[0].interview_date is None)
    if len(opp) != 1:
        return
    page_id = opp[0].page_id
    client.call("PATCH", f"/blocks/{page_id}/children", {"children": [_block("paragraph", "Synthetic human note under Jim’s Notes: never changed by LIFE OS.")]})
    notes, human_before, shown = notion.protected_snapshot(client, page_id, "opportunity"), _human_tree(client, box["human"]), _rows_text(client, page_id)
    check("6b E10: the recruiter on the exact request message is linked; the same-name decoy is not", RECRUITER in shown and AGENCY in shown and DECOY_AGENCY not in shown)
    replay, conflict = tick(mail + (request,)), tick(mail + (request, wrong))
    check("6c replay writes nothing", replay["writes"] == 0 and replay["failed"] == 0)
    check("7b a request naming another requisition is refused, nothing written", conflict["writes"] == 0 and conflict["why"].get("requisition_conflict") == 1)
    lookup = [(ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, source=s)) for s in (REQUEST_URL, "http://example.test/m1m2/not-https",
                                                                                                 "https://example.test/m1m2/no-recruiter-row")]
    (linked, failed), (down, down_failed) = ev.recruiter_threads(env, lookup, recruiters), ev.recruiter_threads(env, lookup, _scoped(client, "0" * 32))
    check("7c E10 lookup on the sandbox Recruiters source: the exact message links one row, an invalid source is never looked up, no row reads 'none'",
          not failed and [q.recruiters_state for _, q in linked] == ["linked", None, "none"] and len(linked[0][1].recruiters) == 1)
    check("7d E10 lookup: an unreadable Recruiters source is DEGRADED, never 'no recruiter'", down_failed and [q.recruiters_state for _, q in down][:2] == ["unreadable", None])
    for name, event, code, dated in (("6d Calendar invite accepted dates Interview 1 in place", _event(INVITE, day(7)), "round_confirmed", day(7)),
                                     ("6e the same invite moved: rescheduled in place", _event(INVITE, day(14)), "round_rescheduled", day(14)),
                                     ("6f invite cancelled: the round reads cancelled", _event(INVITE, day(14), "cancelled"), "round_cancelled", day(14))):
        calendar.events[:1] = [event]
        _, why, quiet = settle(mail + (request,))
        _, rounds = opportunity()
        check(name, quiet and why.get(code) == 1 and len(rounds) == 1 and rounds[0].interview_date == dated[:10]
              and (code != "round_cancelled" or create.round_calendar(client, rounds[0].page_id) == (ev.reference(INVITE), True)))
    calendar.events.append(_event(f"{INVITE} 2", day(21)))
    _, why, quiet = settle(mail + (request,))
    _, rounds = opportunity()
    second = [r for r in rounds if r.ordinal == 2]
    check("6g a distinct accepted 'Interview 2' invite adds Interview 2, dated", quiet and len(rounds) == 2 and len(second) == 1 and second[0].interview_date == day(21)[:10])
    calendar.events.append({**_event(f"{INVITE} 2", day(28)), "id": "another-interview-2"})
    clash = tick(mail + (request,))
    check("7e a second 'Interview 2' at another time is refused: no third round, no rewrite", clash["writes"] == 0 and clash["why"].get("round_conflict", 0) >= 1
          and len(opportunity()[1]) == 2)
    calendar.events.pop()
    verified, mine = hiring_card([{"title": f"{INVITE} 2", "start": day(21)}])
    check("5b Hiring Pipeline region rewritten and verified: the row reads Interviewing", verified and len(mine) == 1 and mine[0][1].startswith("Interviewing"))
    final = tick(mail + (request,))
    check("7f final replay writes nothing", final["writes"] == 0 and final["failed"] == 0)
    check("8a Jim’s Notes on the opportunity are unchanged", notion.protected_snapshot(client, page_id, "opportunity") == notes)       # 8. protected content
    check("8b the human page is unchanged", _human_tree(client, box["human"]) == human_before)


def readback(reader, box, report=None):
    """Independent final evidence: a NEW client reads, from each sandbox surface the case wrote, the canonical state it claims."""
    ledger, recruiters, key = _scoped(reader, box["ledger"]), _scoped(reader, box["recruiters"]), identity.url_key(POSTING)
    rows = ledger.query_data_source(None, {"page_size": 100}).get("results") or []
    props = rows[0]["properties"] if len(rows) == 1 else {}
    out = [("9a Ledger read-back: exactly one row, the vacancy's key, its company, role, lane, Fit and Applied",
            len(rows) == 1 and _plain(props.get("Stable Job Key"), "rich_text") == key and _plain(props.get("Company"), "rich_text") == COMPANY
            and _plain(props.get("Job"), "title") == ROLE and ((props.get("Visible Lane") or {}).get("select") or {}).get("name") == LANE
            and isinstance((props.get("LIFE OS Fit") or {}).get("number"), (int, float)) and (props.get("Applied") or {}).get("checkbox") is True)]
    job_rows = outreach.job_rows(recruiters, rows[0]["id"]) if len(rows) == 1 else []
    thread = [rstore.read(p) for p in recruiters.query_data_source(None, {"page_size": 20, "filter": {"property": "Source URL", "url": {"equals": REQUEST_URL}}})["results"]]
    out.append(("9b Recruiters read-back: one conversation for the job, Replied; one row for the exact request message", len(job_rows) == 1
                and (job_rows[0]["action"], job_rows[0]["person"]) == ("Replied", RECRUITER) and len(thread) == 1 and thread[0]["company"] == AGENCY))
    hiring_rows = _hiring_rows(report or reader, box["report"])
    out.append(("9c Hiring Pipeline read-back: one row for the job, Interviewing", len(hiring_rows) == 1 and hiring_rows[0][1].startswith("Interviewing")))
    scan = notion.parent_scan(reader, box["pipe"])
    active = [p for p in scan.items if p.active]
    opp = [p for p in active if p.title == TITLE]
    rounds = {r.ordinal: r for r in (notion.child_scan(reader, opp[0].page_id).items if len(opp) == 1 else ())}
    machine = [p for p in active if notion.ownership(reader, p.page_id, "opportunity") == Ownership.MACHINE]
    return out + [("9d Interview read-back: the pipeline reads completely; one machine opportunity; the human page still human", scan.complete and len(opp) == 1
                   and len(machine) == 1 and notion.ownership(reader, box["human"], "opportunity") == Ownership.HUMAN),
                  ("9e Interview read-back: two rounds numbered 1 and 2, identities valid", sorted(rounds) == [1, 2] and all(r.identity_valid is True for r in rounds.values())),
                  ("9f Interview read-back: Interview 1 cancelled by its own invite, Interview 2 dated", sorted(rounds) == [1, 2]
                   and create.round_calendar(reader, rounds[1].page_id)[1] is True and bool(rounds[2].interview_date))]


def run(environ, live, now=None, client=None, reader=None, report=None):
    """-> counts only: assertions as PASS/FAIL and `failed`. A sandbox that cannot be built fails closed with its fixed code and claims nothing."""
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() != CONFIRM:
        return {"proof": "refused", "code": "proof_confirm_missing", "failed": 1, "why": {}, "writes": 0}
    if live:
        return {"proof": "refused", "code": "proof_requires_dry_dispatch", "failed": 1, "why": {}, "writes": 0}
    now, client = (now or datetime.now(ev.TZ)).astimezone(ev.TZ), client or notion.make_client(environ)
    try:
        box = sandbox(client, now, report)
    except NotionError as error:                                             # e.g. the integration may not create a database under the QA parent
        return {"proof": "blocked", "code": f"sandbox:{str(error)[:40]}", "detail": str(getattr(error, "detail", ""))[:80], "failed": 1, "why": {}, "writes": 0}
    results, counts = [], {"ticks": 0, "writes": 0, "hiring_writes": 0, "tick_codes": []}
    for stage, call in (("stopped", lambda: drive(client, environ, box, now, results, counts, report)),
                        ("read-back stopped", lambda: results.extend(readback(reader or notion.make_client(environ), box, report)))):
        try:
            call()
        except NotionError as error:                                         # a refusal is a failed assertion with its fixed code, never a crash or a silent PASS
            results.append((f"{stage}: {str(error)[:60]}", False))
    return {"proof": "m1m2", "assertions": {name: "PASS" if ok else "FAIL" for name, ok in results}, "passed": sum(ok for _, ok in results),
            "failed": sum(not ok for _, ok in results), "why": {}, **counts}
