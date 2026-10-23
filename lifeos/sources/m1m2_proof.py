"""LIFE-78 bounded synthetic M1/M2 engineering exit: ONE finite case from a vacancy to a second interview round, through the real V7 code.
Manual only: the interview-proof workflow with `confirm = M1M2` (the Interview token only, its own queue, no mail read). The confirmation is its only
write switch, and the only Notion writes are to a FRESH sandbox made for this run under the QA Interview OS parent.

Real production code drives every step: Jobs identity, URL quality, Fit and lane qualification (lifeos.jobs); the Ledger publish path's properties, key
lookup and create (lifeos.jobs.publish); Mark as Sent and the reply status (lifeos.recruiters.outreach); the Hiring Pipeline compile and render
(lifeos.hiring) over a real read of the sandbox; the Interview runner and Notion writer (interview_evidence.run), including the exact-message recruiter
link (E10). Only the sources are synthetic and held in memory: the vacancy, the Job Ledger, the Recruiters data source, mail and Google Calendar
(SampleWorks, invented requisitions, example.test links, a synthetic profile). No real mailbox, calendar, Ledger, Recruiters row or Hiring Pipeline
page is read or written; nothing is sent or submitted. Prints PASS/FAIL per assertion and counts only. It proves the machine (Gate B); it is not Gate A
and not M1/M2 acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta

from lifeos.hiring import compile as hiring, render
from lifeos.interview import create, notion
from lifeos.interview.models import Ownership, ParentQuery, RoundQuery
from lifeos.interview.ui_probe import QA_PARENT
from lifeos.jobs import identity, lanes, ledger as jobs_ledger, publish, quality
from lifeos.jobs.fit.profile import Profile
from lifeos.jobs.fit.score import evaluate
from lifeos.platform.notion_client import NotionError
from lifeos.recruiters import notion as rstore, outreach
from lifeos.sources import hiring as pipeline, interview_evidence as ev
from lifeos.sources.interview_journey_proof import Calendar, _event, _outreach, _request

CONFIRM = "M1M2"
MAX_TICKS = 6
COMPANY, ROLE, REQ = "SampleWorks", "Senior Technical Program Manager", "880042"
POSTING = f"https://jobs.example.test/sampleworks/jobs/{REQ}"
TITLE = f"{COMPANY} — {ROLE}"
INVITE = f"{COMPANY} {ROLE} Interview"
RECRUITER, DECOY = "Riley Synthetic", "Riley Synthetic"                     # the decoy has the SAME name: only the exact message may link it
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
JD = ("About the role\nSampleWorks (a synthetic employer) is hiring a Senior Technical Program Manager to lead delivery of its commerce platform. Fully remote, United States.\n\n"
      "Responsibilities\n• Own the program roadmap and drive delivery across multiple teams\n• Lead integration of NetSuite with the storefront and payment providers\n"
      "• Report to executive stakeholders on a global enterprise program\n\nRequirements\n• 8+ years of experience in program management delivering enterprise software\n"
      "• Experience with NetSuite and Stripe\n• Experience leading a team of 8 people\n\nPreferred\n• Familiarity with Snowflake\n"
      + "Join a collaborative remote team shipping software for merchants worldwide. " * 12)
EXCLUDED_JD = JD.replace("commerce platform", "clinical trial platform") + " Clinical trial operations experience. More clinical trial work."


def _text(value):
    return [{"type": "text", "plain_text": value, "text": {"content": value}}]


class Ledger:
    """The Job Ledger, in memory, in the real shapes: the target check, the publish key lookup, page reads, the Applied query and the create."""
    source = "51" * 16

    def __init__(self):
        self.pages, self.creates = {}, 0

    def call(self, method, path, body=None):
        if method == "GET" and path == f"/data_sources/{self.source}":
            return {"properties": {n: {"type": k} for n, k in jobs_ledger.REQUIRED.items()}}
        if method == "POST" and path.endswith("/query"):
            key = body["filter"]["rich_text"]["equals"]
            return {"results": [{"id": pid} for pid, p in self.pages.items() if publish_key(p) == key]}
        if method == "GET" and path.startswith("/pages/"):
            return {"id": path.rsplit("/", 1)[1], "properties": deepcopy(self.pages[path.rsplit("/", 1)[1]])}
        raise NotionError("SYNTHETIC_LEDGER_UNSUPPORTED")

    def query_data_source(self, source, body):
        wanted = (body.get("filter") or {}).get("checkbox", {}).get("equals")
        return {"results": [{"id": pid, "properties": deepcopy(p)} for pid, p in self.pages.items() if p["Applied"]["checkbox"] is wanted], "has_more": False}

    def create(self, props, blocks):
        self.creates += 1
        pid = f"{0x5100 + self.creates:032x}"
        page = {name: {"type": next(iter(v)), **v} for name, v in props.items()}
        for name, value in page.items():
            if value["type"] in ("title", "rich_text"):
                value[value["type"]] = _text("".join(r["text"]["content"] for r in value[value["type"]]))
        page.setdefault("Applied", {"type": "checkbox", "checkbox": False})
        self.pages[pid] = page
        return pid

    def apply(self, pid, on):
        """Jim's own act, simulated: he applied (nothing is submitted anywhere)."""
        self.pages[pid]["Applied"] = {"type": "checkbox", "checkbox": True}
        self.pages[pid]["Applied On"] = {"type": "date", "date": {"start": on}}

    def for_company(self, company):
        return [{"page_id": pid, "company": company, "role": "".join(r["plain_text"] for r in p["Job"]["title"]), "stable_job_key": publish_key(p),
                 "requisition": None, "lane": (p.get("Visible Lane") or {}).get("select", {}).get("name")}
                for pid, p in self.pages.items() if "".join(r["plain_text"] for r in p["Company"]["rich_text"]) == company]


def publish_key(page):
    return "".join(r["plain_text"] for r in page["Stable Job Key"]["rich_text"])


class Recruiters:
    """The Recruiters data source, in memory, in the real shapes: its schema (Job relates to the synthetic Ledger), the three filters V7 uses, one create, one update."""
    source = "52" * 16

    def __init__(self, ledger_source, fail=False):
        self.ledger_source, self.pages, self.fail = ledger_source, [], fail

    def call(self, method, path, body=None):
        if method == "GET" and path.startswith("/data_sources/"):
            props = {n: {"type": t} for n, t in rstore.SCHEMA.items()}
            props["Job"] = {"type": "relation", "relation": {"data_source_id": self.ledger_source}}
            props["Action Status"] = {"type": "select", "select": {"options": [{"name": n} for n in ("Needs Action", "Waiting", *rstore.CONVERSATION, *rstore.HISTORY)]}}
            return {"properties": props}
        raise NotionError("SYNTHETIC_RECRUITERS_UNSUPPORTED")

    def query_data_source(self, source, body):
        if self.fail:
            raise NotionError("NOTION_HTTP_503")
        where = body["filter"]
        if "relation" in where:
            hit = lambda p: where["relation"]["contains"] in [r["id"] for r in p["properties"]["Job"]["relation"]]           # noqa: E731
        elif "url" in where:
            hit = lambda p: p["properties"]["Source URL"]["url"] == where["url"]["equals"]                                   # noqa: E731
        else:
            hit = lambda p: (p["properties"]["Action Status"].get("select") or {}).get("name") == where["select"]["equals"]  # noqa: E731
        return {"results": [deepcopy(p) for p in self.pages if hit(p)], "has_more": False}

    def add(self, person, url, company="Staffing Example Inc", action="Needs Action", job=None):
        props = {"Recruiter / Person": {"title": rstore_text(person)}, "Recruiter Company": {"rich_text": rstore_text(company)}, "Source URL": {"url": url},
                 "Action Status": {"select": {"name": action}}, "Done": {"checkbox": False}, "Active": {"checkbox": True}, "Medium": {"rich_text": rstore_text("Email")}}
        if job:
            props["Job"] = {"relation": [{"id": job}]}
        self._store(props)

    def _store(self, props):
        page = {"id": f"{0x5200 + len(self.pages) + 1:032x}", "properties": {}}
        for name, kind in {**rstore.SCHEMA, "Job": "relation"}.items():
            value = props.get(name) or {kind: [] if kind in ("title", "rich_text", "relation") else None}
            page["properties"][name] = {"type": kind, **value}
            if kind in ("title", "rich_text"):
                page["properties"][name][kind] = _text("".join(r.get("text", {}).get("content", r.get("plain_text", "")) for r in value[kind]))
        self.pages.append(page)

    def call_once(self, method, path, body=None):
        assert (method, path) == ("POST", "/pages")
        self._store(body["properties"])
        return {}

    def update_page_properties(self, page_id, properties):
        page = next(p for p in self.pages if p["id"] == page_id)
        for name, value in properties.items():
            page["properties"][name] = {"type": next(iter(value)), **value}
        return {}


def rstore_text(value):
    return [{"type": "text", "text": {"content": value}}]


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
    """The human page as Notion holds it: its own blocks, by id and content (the last-edited fields are left out)."""
    return [{k: v for k, v in b.items() if k not in ("last_edited_time", "last_edited_by", "request_id")} for b in notion.children(client, page_id, [10])]


def _paragraph(value):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": value}}]}}


def sandbox(client, now):
    """A fresh sandbox under the QA parent: container -> "Hiring Pipeline" -> "Active Opportunities", plus one human page Jim owns. -> (pipeline id, human page id)."""
    container = notion.write_tree(client, QA_PARENT, [], title=f"M1/M2 synthetic exit · {now.strftime('%Y-%m-%d %I:%M %p')} CT (synthetic)")
    pipe = notion.write_tree(client, container, [], title="Hiring Pipeline")
    active = notion.write_tree(client, pipe, [], title="Active Opportunities")
    human = notion.write_tree(client, active, [_paragraph("Synthetic human notes. LIFE OS must never change this page.")], title="HumanCo — Manager")
    return pipe, human


def drive(client, environ, pipe, human, now):
    """The case against a fresh sandbox. -> [(assertion, pass)], counts. Fixed names and counts only."""
    results, counts = [], {"ticks": 0, "writes": 0}
    check = lambda name, ok: results.append((name, bool(ok)))      # noqa: E731
    today, day = now.date(), (lambda n: (now + timedelta(days=n)).strftime("%Y-%m-%dT10:00:00-05:00"))

    # 1. A source-backed synthetic vacancy: stable identity, normalization, Fit and lane qualification (lifeos.jobs, unchanged)
    tracked = POSTING + "?utm_source=newsletter&utm_medium=email"
    key = identity.url_key(POSTING)
    check("1a stable key: the tracked link is the same vacancy", key == identity.url_key(tracked) and identity.vacancy_id(POSTING) == identity.vacancy_id(tracked))
    check("1b the posting link and description pass the quality gate", quality.url_problem(POSTING) is None and quality.jd_problem(JD) is None)
    fit = evaluate(ROLE, COMPANY, JD, PROFILE, today)
    check("1c Fit is Go at or above the floor", fit.decision == "Go" and fit.score is not None and fit.score >= lanes.FIT_FLOOR)
    check("1d an excluded variant is No-Go", evaluate(ROLE, COMPANY, EXCLUDED_JD, PROFILE, today).decision == "No-Go")
    facts = lanes.facts_for(fit.score, ROLE, "Remote, United States", JD, "$170,000 - $190,000", today, now, market="US")
    decision, lane, _ = lanes.decide_all("US Remote", facts, today)
    check("1e US Remote lane admits it", decision.status == lanes.ADMIT and lane == "US Remote")

    # 2. Exactly one canonical Ledger identity: the publish path's own key lookup before its create; a replay and the tracked link create nothing
    ledger = Ledger()
    row = {"title": ROLE, "company": COMPANY, "url": POSTING, "source": "synthetic", "provider": "synthetic", "lane": lane, "key": key, "first_seen": today,
           "posted": today, "fit": fit.score, "fit_line": fit.line, "admission": decision.status, "work_mode": "remote", "salary": "$170,000 - $190,000",
           "location": "Remote, United States", "text": JD}
    for attempt in (POSTING, tracked, POSTING):
        if not publish.existing_page(ledger, identity.url_key(attempt)):
            ledger.create(publish.properties({**row, "key": identity.url_key(attempt)}), publish.body_blocks(key, {"summary": JD[:400]}))
    (job_id,) = ledger.pages
    check("2a one Ledger row after three publishes (replay and tracked link adopt it)", ledger.creates == 1 and publish_key(ledger.pages[job_id]) == key)
    twin = Ledger()
    twin.pages = {"a" * 32: deepcopy(ledger.pages[job_id]), "b" * 32: deepcopy(ledger.pages[job_id])}
    try:
        publish.existing_page(twin, key)
        check("2b two rows with one key are refused, never picked", False)
    except NotionError as error:
        check("2b two rows with one key are refused, never picked", str(error) == "ledger_key_ambiguous")

    # 3-4. Jim's own outreach and application, simulated: Mark as Sent, the reply he records, Applied; nothing is sent or submitted
    recruiters = Recruiters(ledger.source)
    job = {"id": job_id, "company": COMPANY, "role": ROLE}
    sent = outreach.mark_sent(recruiters, job, RECRUITER, now, ledger.source, "Staffing Example Inc")
    again = outreach.mark_sent(recruiters, job, RECRUITER, now, ledger.source, "Staffing Example Inc")
    replied = outreach.set_conversation(recruiters, job, RECRUITER, rstore.REPLIED, ledger.source)
    check("3a Mark as Sent records one Waiting conversation; a replay writes nothing", sent["status"] == "recorded" and again["status"] == "already_recorded"
          and len(outreach.job_rows(recruiters, job_id)) == 1)
    check("3b the recorded reply moves it to Replied, read back", replied["status"] == "recorded" and outreach.job_rows(recruiters, job_id)[0]["action"] == "Replied")
    try:
        outreach.set_conversation(recruiters, job, "Someone Else", rstore.REPLIED, ledger.source)
        check("3c a status for a person never messaged is refused", False)
    except NotionError as error:
        check("3c a status for a person never messaged is refused", str(error) == "OUTREACH_NOT_RECORDED")
    ledger.apply(job_id, today.isoformat())

    # 5. Hiring Pipeline mapping after the application (real compile and render over a real read of the sandbox)
    def hiring_row(events=()):
        result = hiring.compile_rows(pipeline.read_applied(ledger), pipeline.read_parents(client, pipe), list(events), None,
                                     {"ledger": True, "parents": True, "calendar": True}, now)
        mine = [r for r in result["rows"] or [] if r.get("company") == COMPANY]
        return mine, [[t for t, _ in render.row_cells(r, now)] for r in mine]
    rows, cells = hiring_row()
    check("5a the application shows as one Hiring Pipeline row, Submitted", len(rows) == 1 and cells[0][1].startswith("Submitted"))

    # 6-7. The Interview path through the real runner and Notion writer, with negatives and replays
    request = _request(COMPANY, ROLE, REQ, "https://example.test/m1m2/request", (now - timedelta(days=1)).isoformat())
    wrong = _request(COMPANY, ROLE, "999999", "https://example.test/m1m2/wrong-req", (now - timedelta(hours=20)).isoformat())
    stranger = _request("NotAppliedCo", "Program Lead", "123456", "https://example.test/m1m2/stranger", (now - timedelta(hours=19)).isoformat())
    amb = (_outreach("Data Engineer", "OtherCo", "111111", "https://example.test/m1m2/amb-1", (now - timedelta(hours=30)).isoformat()),
           _outreach("Data Engineer", "OtherCo", "222222", "https://example.test/m1m2/amb-2", (now - timedelta(hours=29)).isoformat()))
    recruiters.add(RECRUITER, request["url"])                                  # E10: the recruiter's row for the exact request message
    recruiters.add(DECOY, "https://example.test/m1m2/another-thread", company="Other Staffing Example")      # same name, another message
    env = {**environ, "HIRING_PIPELINE_PAGE_ID": pipe, "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on", "INTERVIEW_CONVERT": "", "INTERVIEW_TEST_COMPANIES": "",
           "INTERVIEW_JOURNEY_PROOF": ""}
    calendar = Calendar()

    def tick(records, source=recruiters):
        counts["ticks"] += 1
        done = ev.run(20, True, environ=env, client=client, calendar=calendar, now=now, ledger=ledger, records=list(records),
                      ledger_rows=ledger.for_company, writer=ledger, recruiters=source)
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
        scan = notion.parent_scan(client, pipe)
        mine = [p for p in scan.items if p.active and p.title == TITLE]
        return scan, mine, (notion.child_scan(client, mine[0].page_id).items if len(mine) == 1 else ())

    mail = amb + (stranger,)
    t, why, quiet = settle(mail)
    _, mine, _ = opportunity()
    check("7a ambiguous recruiter leads and a request for a job never applied to write nothing", sum(x["writes"] for x in t) == 0 and not mine
          and t[0]["lead_review"].get("requisition_ambiguous") == 1)
    ledger_before = deepcopy(ledger.pages)
    t, why, quiet = settle(mail + (request,))
    scan, mine, rounds = opportunity()
    check("6a the employer's request makes one opportunity and Interview 1, Scheduling Pending", quiet and len(mine) == 1 and len(rounds) == 1
          and rounds[0].ordinal == 1 and rounds[0].interview_date is None)
    if len(mine) != 1:
        return results, counts
    page_id = mine[0].page_id
    client.call("PATCH", f"/blocks/{page_id}/children", {"children": [_paragraph("Synthetic human note under Jim’s Notes: never changed by LIFE OS.")]})
    notes, human_before = notion.protected_snapshot(client, page_id, "opportunity"), _human_tree(client, human)
    text = _rows_text(client, page_id)
    check("6b E10: the recruiter on the exact request message is linked; the same-name decoy is not", RECRUITER in text and "Other Staffing Example" not in text)
    replay = tick(mail + (request,))
    check("6c replay writes nothing", replay["writes"] == 0 and replay["failed"] == 0)
    conflict = tick(mail + (request, wrong))
    check("7b a request naming another requisition is refused, nothing written", conflict["writes"] == 0 and conflict["why"].get("requisition_conflict") == 1)
    lookup = [(ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, stable_job_key=key, source=request["url"])),
              (ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, stable_job_key=key, source="http://example.test/m1m2/not-https"))]
    linked, failed = ev.recruiter_threads(env, lookup, recruiters)
    down, down_failed = ev.recruiter_threads(env, lookup, Recruiters(ledger.source, fail=True))
    check("7c E10 lookup: exact message linked, invalid source never looked up, unreadable source DEGRADED (never 'no recruiter')",
          not failed and linked[0][1].recruiters_state == "linked" and len(linked[0][1].recruiters) == 1 and linked[1][1].recruiters_state is None
          and down_failed and down[0][1].recruiters_state == "unreadable" and down[1][1].recruiters_state is None)
    missing, _ = ev.recruiter_threads(env, [(ParentQuery(COMPANY, ROLE), RoundQuery(True, None, None, 1, source="https://example.test/m1m2/no-recruiter-row"))], recruiters)
    check("7d E10 lookup: a request with no Recruiters row reads 'none'", missing[0][1].recruiters_state == "none" and missing[0][1].recruiters == ())

    calendar.events.append(_event(INVITE, day(7)))
    _, why, quiet = settle(mail + (request,))
    _, _, rounds = opportunity()
    check("6d Calendar invite accepted dates Interview 1 in place", quiet and why.get("round_confirmed") == 1 and len(rounds) == 1 and rounds[0].interview_date == day(7)[:10])
    calendar.events[0] = _event(INVITE, day(14))
    _, why, quiet = settle(mail + (request,))
    _, _, rounds = opportunity()
    check("6e the same invite moved: rescheduled in place", quiet and why.get("round_rescheduled") == 1 and len(rounds) == 1 and rounds[0].interview_date == day(14)[:10])
    calendar.events[0] = _event(INVITE, day(14), "cancelled")
    _, why, quiet = settle(mail + (request,))
    _, _, rounds = opportunity()
    check("6f invite cancelled: the round reads cancelled", quiet and why.get("round_cancelled") == 1 and len(rounds) == 1
          and create.round_calendar(client, rounds[0].page_id) == (ev.reference(INVITE), True))
    calendar.events.append(_event(f"{INVITE} 2", day(21)))
    _, why, quiet = settle(mail + (request,))
    _, _, rounds = opportunity()
    second = [r for r in rounds if r.ordinal == 2]
    check("6g a distinct accepted 'Interview 2' invite adds Interview 2, dated", quiet and len(rounds) == 2 and len(second) == 1 and second[0].interview_date == day(21)[:10])
    calendar.events.append({**_event(f"{INVITE} 2", day(28)), "id": "another-interview-2"})
    clash = tick(mail + (request,))
    _, _, rounds = opportunity()
    check("7e a second 'Interview 2' at another time is refused: no third round, no rewrite", clash["writes"] == 0 and clash["why"].get("round_conflict", 0) >= 1
          and len(rounds) == 2)
    calendar.events.pop()
    rows, cells = hiring_row([{"title": f"{INVITE} 2", "start": day(21)}])
    check("5b the Hiring Pipeline row now reads Interviewing", len(rows) == 1 and cells[0][1].startswith("Interviewing"))
    final = tick(mail + (request,))
    check("7f final replay writes nothing", final["writes"] == 0 and final["failed"] == 0)

    # 8. Protected content, before and after every later write
    check("8a Jim’s Notes on the opportunity are unchanged", notion.protected_snapshot(client, page_id, "opportunity") == notes)
    check("8b the human page is unchanged", _human_tree(client, human) == human_before)
    check("8c the Ledger row was never rewritten by the Interview path", ledger.pages == ledger_before and ledger.creates == 1)
    return results, counts


def readback(client, pipe, human):
    """Independent final evidence: a NEW client reads the sandbox as Notion holds it. -> [(assertion, pass)]."""
    scan = notion.parent_scan(client, pipe)
    active = [p for p in scan.items if p.active]
    mine = [p for p in active if p.title == TITLE]
    rounds = notion.child_scan(client, mine[0].page_id).items if len(mine) == 1 else ()
    machine = [p for p in active if notion.ownership(client, p.page_id, "opportunity") == Ownership.MACHINE]
    return [("9a read-back: the pipeline reads completely", scan.complete),
            ("9b read-back: exactly one machine opportunity, the human page still human", len(mine) == 1 and len(machine) == 1
             and notion.ownership(client, human, "opportunity") == Ownership.HUMAN),
            ("9c read-back: two rounds, numbered 1 and 2, identities valid", sorted(r.ordinal for r in rounds) == [1, 2] and all(r.identity_valid is True for r in rounds)),
            ("9d read-back: Interview 1 cancelled by its own invite, Interview 2 dated", len(rounds) == 2
             and create.round_calendar(client, [r for r in rounds if r.ordinal == 1][0].page_id)[1] is True
             and bool([r for r in rounds if r.ordinal == 2][0].interview_date))]


def run(environ, live, now=None, client=None, reader=None):
    """-> counts only: assertions as PASS/FAIL and `failed` (assertions that did not pass)."""
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() != CONFIRM:
        return {"proof": "refused", "code": "proof_confirm_missing", "failed": 1, "why": {}, "writes": 0}
    if live:
        return {"proof": "refused", "code": "proof_requires_dry_dispatch", "failed": 1, "why": {}, "writes": 0}
    now = (now or datetime.now(ev.TZ)).astimezone(ev.TZ)
    client = client or notion.make_client(environ)
    pipe, human = sandbox(client, now)
    results, counts = drive(client, environ, pipe, human, now)
    results += readback(reader or notion.make_client(environ), pipe, human)
    assertions = {name: "PASS" if ok else "FAIL" for name, ok in results}
    return {"proof": "m1m2", "assertions": assertions, "passed": sum(ok for _, ok in results), "failed": sum(not ok for _, ok in results), "why": {},
            "writes": counts["writes"], "ticks": counts["ticks"]}
