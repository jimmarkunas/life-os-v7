"""LIFE-78 bounded synthetic M1/M2 engineering exit: ONE finite case from an acquired vacancy to a second interview round, through the real V7 code, on
native Notion QA surfaces. Manual only: the interview-proof workflow with `confirm = M1M2` (the Interview token only, its own queue, no mail read).
The confirmation is its only write switch.

Every write goes to a FRESH sandbox made for this run under the QA Interview OS parent, and nothing else:
  · a disposable Job Ledger data source, written by the real publish path (properties, key lookup, create) and Jim's simulated Applied tick;
  · a disposable Recruiters data source related to that Ledger, written by the real Recruiters stage create and by Mark as Sent / the reply status;
  · a synthetic Daily Report page holding the Hiring Pipeline floating region, written by the real Hiring card (lifeos.hiring.card.present);
  · its own Hiring Pipeline / Active Opportunities, written by the real Interview runner and Notion writer (interview_evidence.run), and one human page.
The vacancy enters through the real Jobs acquisition normalization (lifeos.jobs.enrich.parse_html: an employer ATS page's JSON-LD JobPosting ->
description sections, quality gate, title match, freshness, employer and place) on an invented, already-fetched page; nothing is fetched. Only the
outside sources stay synthetic and in memory: that page, a synthetic Fit profile, mail and Google Calendar (SampleWorks, invented requisitions,
example.test links). No real mailbox, calendar, Ledger, Recruiters row, Daily Report or Hiring Pipeline page is read or written; nothing is sent or
submitted. The final evidence is an independent read-back, with a new client, of every surface the case wrote. PASS/FAIL per assertion, counts only.
Machine evidence (Gate B); not Gate A, not M1/M2 acceptance."""
import copy
import json
import time
from datetime import datetime, timedelta

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

CONFIRM = "M1M2"
MAX_TICKS = 6
COMPANY, ROLE, REQ, LANE = "SampleWorks", "Senior Technical Program Manager", "880042", "US Remote"
POSTING = f"https://jobs.example.test/sampleworks/jobs/{REQ}"
TITLE = f"{COMPANY} — {ROLE}"
INVITE = f"{COMPANY} {ROLE} Interview"
REQUEST_URL = "https://example.test/m1m2/request"
RECRUITER, AGENCY, DECOY_AGENCY = "Riley Synthetic", "Staffing Example Inc", "Other Staffing Example"     # the decoy has the SAME person name
PROFILE = Profile({                                                       # synthetic, no personal data (the real profile is never read here)
    "years": 15, "scope": {"team_size": 10, "budget_usd": 20_000_000, "global": "direct", "enterprise": "direct", "multi_team": "direct",
                           "executive": "adjacent", "people_leadership": "direct"},
    "capabilities": [{"id": "erp", "label": "ERP integrations", "class": "direct", "terms": ["netsuite", "erp"], "committed": True},
                     {"id": "pay", "label": "payments", "class": "direct", "terms": ["stripe", "payment"], "bucket": "skill"},
                     {"id": "pm", "label": "program delivery", "class": "direct", "terms": ["program management", "delivery"], "bucket": "skill"},
                     {"id": "etl", "label": "data pipelines", "class": "method", "terms": ["etl", "snowflake"]}],
    "functions": [{"terms": ["program manager", "technical program manager", "roadmap"], "class": "direct"},
                  {"terms": ["implementation", "integration"], "class": "adjacent"}],
    "advantage": ["automation"], "specialization": ["technical program manager"], "exclusions": [],
})
DESCRIPTION = ("<p>SampleWorks (a synthetic employer) is hiring a Senior Technical Program Manager to lead delivery of its commerce platform. Fully remote, United States.</p>"
               "<h3>Responsibilities</h3><ul><li>Own the program roadmap and drive delivery across multiple teams</li>"
               "<li>Lead integration of NetSuite with the storefront and payment providers</li><li>Report to executive stakeholders on a global enterprise program</li></ul>"
               "<h3>Requirements</h3><ul><li>8+ years of experience in program management delivering enterprise software</li><li>Experience with NetSuite and Stripe</li>"
               "<li>Experience leading a team of 8 people</li></ul><h3>Preferred</h3><ul><li>Familiarity with Snowflake</li></ul>"
               + "<p>Join a collaborative remote team shipping software for merchants worldwide.</p>" * 12)

_KINDS = {"title": {}, "rich_text": {}, "url": {}, "date": {}, "number": {}, "checkbox": {}, "select": {}, "multi_select": {}}
READ_FIELDS = {"Hiring Decision": "select", "Dismissed Outcome Ref": "rich_text"}       # what the Hiring readers read from a Ledger row


def ledger_schema():
    """The sandbox Ledger's properties: the Ledger target guard's required set, every property the real publish path can write (a row with every optional
    field present, incl. a Review reason), and the fields the Hiring readers read."""
    from datetime import date                                                                        # noqa: PLC0415
    sample = {"title": ROLE, "company": COMPANY, "url": POSTING, "source": "synthetic-ats", "provider": "synthetic-ats", "lane": LANE, "key": "sample",
              "first_seen": date(2026, 1, 1), "posted": date(2026, 1, 1), "fit": 80, "fit_line": "sample", "admission": lanes.REVIEW, "admission_reason": "sample",
              "eligible": [LANE], "work_mode": "remote", "salary": "$170,000 - $190,000", "location": "Remote, United States", "text": "sample", "apply_kind": "easy_apply"}
    return {**jobs_ledger.REQUIRED, **{n: next(iter(v)) for n, v in publish.properties(sample).items()}, **READ_FIELDS}


ACTIONS = ("Needs Action", "Waiting", *rstore.CONVERSATION, *rstore.HISTORY)
SLEEP, WAITS = time.sleep, 10                                             # a data-source query can trail a write by seconds: wait (bounded) to SEE the case's own write


def visible(probe):
    """Wait until `probe()` is truthy (the case's own write is readable), at most WAITS tries two seconds apart. -> the last answer. Never writes."""
    answer = probe()
    for _ in range(WAITS):
        if answer:
            break
        SLEEP(2)
        answer = probe()
    return answer


def ats_page(posted, description=DESCRIPTION, valid_through=None):
    """An invented, already-fetched employer ATS page carrying a schema.org JobPosting, as the Jobs detail stage receives it."""
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": ROLE, "description": description, "datePosted": posted,
               "hiringOrganization": {"@type": "Organization", "name": COMPANY}, "jobLocationType": "TELECOMMUTE",
               "applicantLocationRequirements": {"@type": "Country", "name": "United States"},
               "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"@type": "QuantitativeValue", "minValue": 170000, "maxValue": 190000, "unitText": "YEAR"}},
               **({"validThrough": valid_through} if valid_through else {})}
    return (f"<html><head><title>{ROLE} - {COMPANY} (synthetic)</title><script type=\"application/ld+json\">{json.dumps(posting)}</script></head>"
            f"<body><h1>{ROLE}</h1>{description}</body></html>")


def _scoped(client, source):
    """The same client (same token, same pacing) aimed at one sandbox data source."""
    scoped = copy.copy(client)
    scoped.source = source
    scoped.__dict__.pop("_ledger_target", None)
    return scoped


def _database(client, parent, title, properties):
    """One disposable data source under a sandbox page -> its data source id. A refusal raises (the run fails closed and says so)."""
    made = client.call_once("POST", "/databases", {"parent": {"type": "page_id", "page_id": parent}, "title": rich_text(title),
                                                    "initial_data_source": {"properties": properties}})
    sources = made.get("data_sources") if isinstance(made, dict) else None
    if not isinstance(sources, list) or len(sources) != 1 or not sources[0].get("id"):
        raise NotionError("M1M2_SANDBOX_DATABASE_UNREADABLE")
    return sources[0]["id"]


def _paragraph(value):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(value)}}


def report_page(client, container):
    """A synthetic Daily Report page holding the Hiring Pipeline floating region in the shape the real card validates: an anchor block on the same page,
    the heading, the four-column table, a divider. -> the card's three pins."""
    row = lambda cells: {"object": "block", "type": "table_row", "table_row": {"cells": [rich_text(c) for c in cells]}}     # noqa: E731
    blocks = [{"object": "block", "type": "callout", "callout": {"rich_text": rich_text("Synthetic anchor (stands in for the Jira card on the same page)")}},
              {"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text(HM.TITLE)}},
              {"object": "block", "type": "table", "table": {"table_width": 4, "has_column_header": True, "has_row_header": False, "children": [row(HM.HEADERS)]}},
              {"object": "block", "type": "divider", "divider": {}}]
    page = client.call_once("POST", "/pages", {"parent": {"type": "page_id", "page_id": container},
                                               "properties": {"title": {"title": rich_text("Daily Report (synthetic)")}}, "children": blocks})
    kids = notion.children(client, page["id"], [5])
    if [b.get("type") for b in kids][:4] != ["callout", "heading_3", "table", "divider"]:
        raise NotionError("M1M2_SANDBOX_REPORT_SHAPE")
    return {"JIRA_CARD_BLOCK_ID": kids[0]["id"], "HIRING_CARD_BLOCK_ID": kids[1]["id"], "HIRING_TABLE_BLOCK_ID": kids[2]["id"]}


def sandbox(client, now, report=None):
    """A fresh sandbox under the QA parent. -> its ids: pipeline, human page, Ledger and Recruiters data sources, the report's pinned blocks."""
    container = notion.write_tree(client, QA_PARENT, [], title=f"M1/M2 synthetic exit · {now.strftime('%Y-%m-%d %I:%M %p')} CT (synthetic)")
    pipe = notion.write_tree(client, container, [], title="Hiring Pipeline")
    active = notion.write_tree(client, pipe, [], title="Active Opportunities")
    human = notion.write_tree(client, active, [_paragraph("Synthetic human notes. LIFE OS must never change this page.")], title="HumanCo — Manager")
    spec = lambda kind: {kind: _KINDS[kind]}                       # noqa: E731
    led = _database(client, container, "Job Ledger (synthetic)", {n: spec(k) for n, k in ledger_schema().items()})
    rprops = {n: spec(k) for n, k in rstore.SCHEMA.items()}
    rprops["Action Status"] = {"select": {"options": [{"name": n} for n in ACTIONS]}}
    rprops["Job"] = {"relation": {"data_source_id": led, "single_property": {}}}
    rec = _database(client, container, "Recruiters (synthetic)", rprops)
    return {"pipe": pipe, "human": human, "ledger": led, "recruiters": rec, "report": report_page(report or client, container)}


def _plain(prop, kind):
    return "".join(r.get("plain_text") or (r.get("text") or {}).get("content", "") for r in (prop or {}).get(kind) or [])


def _ledger_rows(ledger):
    """The Interview runner's per-company Ledger rows, read from the sandbox Ledger (only a keyed recruiter lead reads them)."""
    def rows(company):
        found = ledger.query_data_source(None, {"page_size": 100, "filter": {"property": "Company", "rich_text": {"equals": company}}})
        return [{"page_id": p["id"], "company": company, "role": _plain(p["properties"].get("Job"), "title"),
                 "stable_job_key": _plain(p["properties"].get("Stable Job Key"), "rich_text"), "requisition": None} for p in found.get("results") or []]
    return rows


def _rows_text(client, page_id):
    """Every text on a page as Notion holds it (nested blocks and table cells, child pages left out), for presence checks only; never printed."""
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
    """The Hiring Pipeline table as Notion holds it, through the region validator -> [[cell text] x 4] data rows."""
    region = R.floating_region(client, pins["HIRING_CARD_BLOCK_ID"], HM.TITLE, lambda code: NotionError(code), with_status=False)
    return [R.cells_of(r) for r in region["rows"][1:]]


def drive(client, environ, box, now, results, counts, report=None):
    """The case against a fresh sandbox; appends (assertion, pass) to `results`. Fixed names and counts only."""
    check = lambda name, ok: results.append((name, bool(ok)))      # noqa: E731
    today, day = now.date(), (lambda n: (now + timedelta(days=n)).strftime("%Y-%m-%dT10:00:00-05:00"))
    ledger, recruiters, report = _scoped(client, box["ledger"]), _scoped(client, box["recruiters"]), report or client
    pins = {**environ, **box["report"]}

    # 1. Acquisition: the real detail-page normalization on an invented, already-fetched ATS page; then identity, Fit and lane qualification
    found = enrich.parse_html(POSTING, ROLE, ats_page(today.isoformat()), lane=LANE, first_party=True)
    desc = found.get("description") or {}
    check("1a acquisition: the JSON-LD page normalizes to READY with sections, the employer and today's posting date",
          found.get("outcome") == "ready" and found.get("company") == COMPANY and found.get("posted") == today and bool(desc.get("requirements"))
          and quality.jd_problem(desc.get("full_text", "")) is None)
    check("1b acquisition refuses an expired posting (validThrough in the past) as closed",
          enrich.parse_html(POSTING, ROLE, ats_page(today.isoformat(), valid_through=(today - timedelta(days=1)).isoformat()), lane=LANE, first_party=True)["outcome"] == "closed")
    tracked = POSTING + "?utm_source=newsletter&utm_medium=email"
    key = identity.url_key(POSTING)
    check("1c stable identity: the tracked link is the same vacancy", key == identity.url_key(tracked) and identity.vacancy_id(POSTING) == identity.vacancy_id(tracked)
          and quality.url_problem(POSTING) is None)
    fit = evaluate(ROLE, found.get("company") or "", desc.get("full_text", ""), PROFILE, today)
    check("1d Fit on the normalized description is Go at or above the floor", fit.decision == "Go" and fit.score is not None and fit.score >= lanes.FIT_FLOOR)
    check("1e the same page with a clinical scope is No-Go", evaluate(ROLE, COMPANY, desc.get("full_text", "").replace("commerce platform", "clinical trial platform")
                                                                      + " Clinical trial operations experience. More clinical trial work.", PROFILE, today).decision == "No-Go")
    facts = lanes.facts_for(fit.score, ROLE, found.get("location") or "Remote, United States", desc.get("full_text", ""), "$170,000 - $190,000", found.get("posted"), now,
                            market="US", first_party=True)
    decision, lane, eligible = lanes.decide_all(LANE, facts, today)
    check("1f the US Remote lane admits it", decision.status == lanes.ADMIT and lane == LANE)

    # 2. Exactly one canonical Ledger row in the sandbox Ledger, by the publish path: key lookup first, then create; a replay and the tracked link create nothing
    row = {"title": ROLE, "company": found.get("company"), "url": POSTING, "source": "synthetic-ats", "provider": "synthetic-ats", "lane": lane, "key": key,
           "first_seen": today, "posted": found.get("posted"), "fit": fit.score, "fit_line": fit.line, "admission": decision.status, "eligible": eligible,
           "work_mode": "remote", "salary": "$170,000 - $190,000", "location": found.get("location") or "Remote, United States", "text": desc.get("full_text", "")}
    creates = 0
    for attempt in (POSTING, tracked, POSTING):
        if not publish.existing_page(ledger, identity.url_key(attempt)):
            ledger.create(publish.properties({**row, "key": identity.url_key(attempt)}), publish.body_blocks(key, desc))
            creates += 1
            visible(lambda: publish.existing_page(ledger, key))               # the replay must be judged on a Ledger that already shows the row
    job_id = publish.existing_page(ledger, key)
    check("2a one Ledger row after three publishes (replay and tracked link adopt it)", creates == 1 and bool(job_id) and jobs_ledger.verify(ledger) == jobs_ledger.OK)
    if not job_id:
        return

    # 3-4. Jim's own outreach and application, simulated: Mark as Sent, the reply he records, Applied. Nothing is sent or submitted
    job = {"id": job_id, "company": COMPANY, "role": ROLE}
    sent = outreach.mark_sent(recruiters, job, RECRUITER, now, box["ledger"], AGENCY)
    again = outreach.mark_sent(recruiters, job, RECRUITER, now, box["ledger"], AGENCY)
    replied = outreach.set_conversation(recruiters, job, RECRUITER, rstore.REPLIED, box["ledger"])
    check("3a Mark as Sent records one Waiting conversation; a replay writes nothing", sent["status"] == "recorded" and again["status"] == "already_recorded"
          and len(outreach.job_rows(recruiters, job_id)) == 1)
    check("3b the recorded reply moves it to Replied, read back", replied["status"] == "recorded" and outreach.job_rows(recruiters, job_id)[0]["action"] == "Replied")
    try:
        outreach.set_conversation(recruiters, job, "Someone Else", rstore.REPLIED, box["ledger"])
        check("3c a status for a person never messaged is refused", False)
    except NotionError as error:
        check("3c a status for a person never messaged is refused", str(error) == "OUTREACH_NOT_RECORDED")
    ledger.update_page_properties(job_id, {"Applied": {"checkbox": True}, "Applied On": {"date": {"start": today.isoformat()}}})      # Jim's own act, simulated
    visible(lambda: pipeline.read_applied(ledger))

    # 5. The Hiring Pipeline region, written by the real card from the sandbox Ledger and pipeline, and read back from Notion
    def hiring_card(events=()):
        result = hiring.compile_rows(pipeline.read_applied(ledger), pipeline.read_parents(client, box["pipe"]), list(events), None,
                                     {"ledger": True, "parents": True, "calendar": True}, now)
        text, cells, _ = render.render(result, None, now)
        done = card.present(report, pins, text, cells, True)
        counts["hiring_writes"] += done["rows_changed"] + done["rows_added"] + done["rows_removed"]
        return done["verified"], [r for r in _hiring_rows(report, pins) if r[0] == HM.label(COMPANY, ROLE)]
    verified, mine = hiring_card()
    check("5a Hiring Pipeline region written and verified: one row, Submitted", verified and len(mine) == 1 and mine[0][1].startswith("Submitted"))

    # 6-7. The Interview path through the real runner and Notion writer, with negatives and replays
    request = _request(COMPANY, ROLE, REQ, REQUEST_URL, (now - timedelta(days=1)).isoformat())
    wrong = _request(COMPANY, ROLE, "999999", "https://example.test/m1m2/wrong-req", (now - timedelta(hours=20)).isoformat())
    stranger = _request("NotAppliedCo", "Program Lead", "123456", "https://example.test/m1m2/stranger", (now - timedelta(hours=19)).isoformat())
    amb = (_outreach("Data Engineer", "OtherCo", "111111", "https://example.test/m1m2/amb-1", (now - timedelta(hours=30)).isoformat()),
           _outreach("Data Engineer", "OtherCo", "222222", "https://example.test/m1m2/amb-2", (now - timedelta(hours=29)).isoformat()))

    def logged(agency, url):                                                 # what the Recruiters stage writes for a recruiter's message: its own create and read-back
        rstore.create(recruiters, {"person": RECRUITER, "company": agency, "client": COMPANY, "role": ROLE, "medium": "Email", "what": "", "needs_review": False,
                                   "last_contact": (now - timedelta(days=1)).isoformat(), "week_ending": week_ending(now - timedelta(days=1)), "source_url": url})
    logged(AGENCY, REQUEST_URL)                                              # E10: the recruiter's row for the exact request message
    logged(DECOY_AGENCY, "https://example.test/m1m2/another-thread")         # the same name, another message
    env = {**environ, "HIRING_PIPELINE_PAGE_ID": box["pipe"], "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on", "INTERVIEW_CONVERT": "", "INTERVIEW_TEST_COMPANIES": "",
           "INTERVIEW_JOURNEY_PROOF": ""}
    calendar = Calendar()

    def tick(records):
        counts["ticks"] += 1
        done = ev.run(20, True, environ=env, client=client, calendar=calendar, now=now, ledger=ledger, records=list(records),
                      ledger_rows=_ledger_rows(ledger), writer=ledger, recruiters=recruiters)
        counts["writes"] += done["writes"]
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
        scan = notion.parent_scan(client, box["pipe"])
        mine = [p for p in scan.items if p.active and p.title == TITLE]
        return mine, (notion.child_scan(client, mine[0].page_id).items if len(mine) == 1 else ())

    mail = amb + (stranger,)
    t, _, _ = settle(mail)
    opp, _ = opportunity()
    check("7a ambiguous recruiter leads and a request for a job never applied to write nothing", sum(x["writes"] for x in t) == 0 and not opp
          and t[0]["lead_review"].get("requisition_ambiguous") == 1)
    _, _, quiet = settle(mail + (request,))
    opp, rounds = opportunity()
    check("6a the employer's request makes one opportunity and Interview 1, Scheduling Pending", quiet and len(opp) == 1 and len(rounds) == 1
          and rounds[0].ordinal == 1 and rounds[0].interview_date is None)
    if len(opp) != 1:
        return
    page_id = opp[0].page_id
    client.call("PATCH", f"/blocks/{page_id}/children", {"children": [_paragraph("Synthetic human note under Jim’s Notes: never changed by LIFE OS.")]})
    notes, human_before = notion.protected_snapshot(client, page_id, "opportunity"), _human_tree(client, box["human"])
    text = _rows_text(client, page_id)
    check("6b E10: the recruiter on the exact request message is linked; the same-name decoy is not", RECRUITER in text and AGENCY in text and DECOY_AGENCY not in text)
    replay = tick(mail + (request,))
    check("6c replay writes nothing", replay["writes"] == 0 and replay["failed"] == 0)
    conflict = tick(mail + (request, wrong))
    check("7b a request naming another requisition is refused, nothing written", conflict["writes"] == 0 and conflict["why"].get("requisition_conflict") == 1)
    lookup = [(ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, stable_job_key=key, source=REQUEST_URL)),
              (ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, stable_job_key=key, source="http://example.test/m1m2/not-https")),
              (ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, source="https://example.test/m1m2/no-recruiter-row"))]
    linked, failed = ev.recruiter_threads(env, lookup, recruiters)
    down, down_failed = ev.recruiter_threads(env, lookup, _scoped(client, "0" * 32))           # a data source this token cannot read
    check("7c E10 lookup on the sandbox Recruiters source: the exact message links one row, an invalid source is never looked up, no row reads 'none'",
          not failed and linked[0][1].recruiters_state == "linked" and len(linked[0][1].recruiters) == 1 and linked[1][1].recruiters_state is None
          and linked[2][1].recruiters_state == "none")
    check("7d E10 lookup: an unreadable Recruiters source is DEGRADED, never 'no recruiter'", down_failed and down[0][1].recruiters_state == "unreadable"
          and down[1][1].recruiters_state is None)

    calendar.events.append(_event(INVITE, day(7)))
    _, why, quiet = settle(mail + (request,))
    _, rounds = opportunity()
    check("6d Calendar invite accepted dates Interview 1 in place", quiet and why.get("round_confirmed") == 1 and len(rounds) == 1 and rounds[0].interview_date == day(7)[:10])
    calendar.events[0] = _event(INVITE, day(14))
    _, why, quiet = settle(mail + (request,))
    _, rounds = opportunity()
    check("6e the same invite moved: rescheduled in place", quiet and why.get("round_rescheduled") == 1 and len(rounds) == 1 and rounds[0].interview_date == day(14)[:10])
    calendar.events[0] = _event(INVITE, day(14), "cancelled")
    _, why, quiet = settle(mail + (request,))
    _, rounds = opportunity()
    check("6f invite cancelled: the round reads cancelled", quiet and why.get("round_cancelled") == 1 and len(rounds) == 1
          and create.round_calendar(client, rounds[0].page_id) == (ev.reference(INVITE), True))
    calendar.events.append(_event(f"{INVITE} 2", day(21)))
    _, why, quiet = settle(mail + (request,))
    _, rounds = opportunity()
    second = [r for r in rounds if r.ordinal == 2]
    check("6g a distinct accepted 'Interview 2' invite adds Interview 2, dated", quiet and len(rounds) == 2 and len(second) == 1 and second[0].interview_date == day(21)[:10])
    calendar.events.append({**_event(f"{INVITE} 2", day(28)), "id": "another-interview-2"})
    clash = tick(mail + (request,))
    _, rounds = opportunity()
    check("7e a second 'Interview 2' at another time is refused: no third round, no rewrite", clash["writes"] == 0 and clash["why"].get("round_conflict", 0) >= 1
          and len(rounds) == 2)
    calendar.events.pop()
    verified, mine = hiring_card([{"title": f"{INVITE} 2", "start": day(21)}])
    check("5b Hiring Pipeline region rewritten and verified: the row reads Interviewing", verified and len(mine) == 1 and mine[0][1].startswith("Interviewing"))
    final = tick(mail + (request,))
    check("7f final replay writes nothing", final["writes"] == 0 and final["failed"] == 0)

    # 8. Protected content, before and after every later write
    check("8a Jim’s Notes on the opportunity are unchanged", notion.protected_snapshot(client, page_id, "opportunity") == notes)
    check("8b the human page is unchanged", _human_tree(client, box["human"]) == human_before)


def readback(reader, box, report=None):
    """Independent final evidence: a NEW client reads, from each sandbox surface the case wrote, the canonical state it claims. -> [(assertion, pass)]."""
    report = report or reader
    ledger, recruiters = _scoped(reader, box["ledger"]), _scoped(reader, box["recruiters"])
    key = identity.url_key(POSTING)
    rows = ledger.query_data_source(None, {"page_size": 100}).get("results") or []
    props = rows[0]["properties"] if len(rows) == 1 else {}
    out = [("9a Ledger read-back: exactly one row, the vacancy's key, its company, role, lane, Fit and Applied",
            len(rows) == 1 and _plain(props.get("Stable Job Key"), "rich_text") == key and _plain(props.get("Company"), "rich_text") == COMPANY
            and _plain(props.get("Job"), "title") == ROLE and ((props.get("Visible Lane") or {}).get("select") or {}).get("name") == LANE
            and isinstance((props.get("LIFE OS Fit") or {}).get("number"), (int, float)) and (props.get("Applied") or {}).get("checkbox") is True)]
    job_rows = outreach.job_rows(recruiters, rows[0]["id"]) if len(rows) == 1 else []
    thread = [rstore.read(p) for p in recruiters.query_data_source(None, {"page_size": 20, "filter": {"property": "Source URL", "url": {"equals": REQUEST_URL}}})["results"]]
    out.append(("9b Recruiters read-back: one conversation for the job, Replied; one row for the exact request message", len(job_rows) == 1 and job_rows[0]["action"] == "Replied"
                and job_rows[0]["person"] == RECRUITER and len(thread) == 1 and thread[0]["company"] == AGENCY))
    hiring_rows = [r for r in _hiring_rows(report, box["report"]) if r[0] == HM.label(COMPANY, ROLE)]
    out.append(("9c Hiring Pipeline read-back: one row for the job, Interviewing", len(hiring_rows) == 1 and hiring_rows[0][1].startswith("Interviewing")))
    scan = notion.parent_scan(reader, box["pipe"])
    active = [p for p in scan.items if p.active]
    opp = [p for p in active if p.title == TITLE]
    rounds = notion.child_scan(reader, opp[0].page_id).items if len(opp) == 1 else ()
    machine = [p for p in active if notion.ownership(reader, p.page_id, "opportunity") == Ownership.MACHINE]
    first = [r for r in rounds if r.ordinal == 1]
    second = [r for r in rounds if r.ordinal == 2]
    out += [("9d Interview read-back: the pipeline reads completely; one machine opportunity; the human page still human", scan.complete and len(opp) == 1
             and len(machine) == 1 and notion.ownership(reader, box["human"], "opportunity") == Ownership.HUMAN),
            ("9e Interview read-back: two rounds numbered 1 and 2, identities valid", sorted(r.ordinal for r in rounds) == [1, 2] and all(r.identity_valid is True for r in rounds)),
            ("9f Interview read-back: Interview 1 cancelled by its own invite, Interview 2 dated", len(first) == 1 and len(second) == 1
             and create.round_calendar(reader, first[0].page_id)[1] is True and bool(second[0].interview_date))]
    return out


def run(environ, live, now=None, client=None, reader=None, report=None):
    """-> counts only: assertions as PASS/FAIL and `failed`. A sandbox that cannot be built fails closed with its fixed code and claims nothing."""
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() != CONFIRM:
        return {"proof": "refused", "code": "proof_confirm_missing", "failed": 1, "why": {}, "writes": 0}
    if live:
        return {"proof": "refused", "code": "proof_requires_dry_dispatch", "failed": 1, "why": {}, "writes": 0}
    now = (now or datetime.now(ev.TZ)).astimezone(ev.TZ)
    client = client or notion.make_client(environ)
    try:
        box = sandbox(client, now, report)
    except NotionError as error:                                             # e.g. the integration may not create a database under the QA parent
        return {"proof": "blocked", "code": f"sandbox:{str(error)[:40]}", "detail": str(getattr(error, "detail", ""))[:80], "failed": 1, "why": {}, "writes": 0}
    results, counts = [], {"ticks": 0, "writes": 0, "hiring_writes": 0}
    try:
        drive(client, environ, box, now, results, counts, report)
    except NotionError as error:                                             # a refusal mid-case is a failed assertion with its fixed code, never a crash or a silent PASS
        results.append((f"stopped: {str(error)[:60]}", False))
    try:
        results += readback(reader or notion.make_client(environ), box, report)
    except NotionError as error:
        results.append((f"read-back stopped: {str(error)[:60]}", False))
    return {"proof": "m1m2", "assertions": {name: "PASS" if ok else "FAIL" for name, ok in results}, "passed": sum(ok for _, ok in results),
            "failed": sum(not ok for _, ok in results), "why": {}, "writes": counts["writes"], "hiring_writes": counts["hiring_writes"], "ticks": counts["ticks"]}
