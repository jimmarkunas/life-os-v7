"""LIFE-78 synthetic journey proof on real Notion. Manual only: the interview-proof workflow with `confirm = QA` (the Interview token only, its own queue,
no mail read), or a domains dispatch with `interview_journey_proof = QA` and Actually write UNticked, so every other step in that job stays a dry run.
The QA confirmation is this proof's only write switch.

It drives the REAL Interview runner (`interview_evidence.run`) and the REAL Notion writer with the Interview token, tick after tick, against a FRESH
sandbox made for this run under the QA Interview OS parent: a container page, its own "Hiring Pipeline" and "Active Opportunities". Mail, Google
Calendar and the Job Ledger are synthetic and held in memory (ExampleCo / SampleCorp, invented requisitions, example.test links): no real mailbox,
calendar, Ledger row or Hiring Pipeline page is read or written. Prints fixed codes and counts per step only. Proves the machine on real Notion; it
is not M1/M2 evidence."""
from datetime import datetime, timedelta
from lifeos.interview import create, notion
from lifeos.interview.ui_probe import QA_PARENT
from lifeos.jobs import ledger as jobs_ledger
from lifeos.sources import interview_evidence as ev

CONFIRM = "QA"
MAX_TICKS = 6


def _message(url, received, subject, body, person="Pat Example", auto=False):
    return {"provider": "gmail", "id": url, "received": received, "sender_name": person, "sender_address": "no-reply.example" if auto else "pat-example",
            "subject": subject, "body": body, "folder": "INBOX", "url": url, "markers": {"bulk": False, "auto": auto, "noreply": auto}}


def _outreach(role, client, req, url, received):
    body = (f"Hi Jim,\n\nI'm a senior recruiter at Staffing Example Inc. I am hiring for a {role} role for our client {client}."
            + (f" Requisition {req}." if req else "") + "\n\nPat Example\nSenior Recruiter\nStaffing Example Inc\n")
    return _message(url, received, f"{role} opportunity", body)


def _request(company, role, req, url, received):
    return _message(url, received, f"Schedule your interview - {company} {role}", f"Please select a time for a call. Requisition {req}.", f"{company} Talent", True)


LEAD = _outreach("Program Manager", "ExampleCo", "777777", "https://example.test/proof/lead", "2026-10-19T15:00:00Z")
NO_REQ = _outreach("Data Analyst", "OtherCo", None, "https://example.test/proof/noreq", "2026-10-19T16:00:00Z")
REQUEST = _request("ExampleCo", "Program Manager", "777777", "https://example.test/proof/request", "2026-10-20T15:00:00Z")
LEAD_B = _outreach("Analyst", "SampleCorp", "555555", "https://example.test/proof/lead-b", "2026-10-19T17:00:00Z")
WRONG_B = _request("SampleCorp", "Analyst", "444444", "https://example.test/proof/wrong-b", "2026-10-20T16:00:00Z")
INVITE = "ExampleCo Program Manager Interview"


class Ledger:
    """The Job Ledger, in memory, keyed like the real one: the target check, an exact Stable Job Key lookup, one create and its read-back."""
    source = "synthetic-ledger"

    def __init__(self):
        self.rows = {}

    def call(self, method, path, body=None):
        if path == f"/data_sources/{self.source}":
            return {"properties": {n: {"type": k} for n, k in jobs_ledger.REQUIRED.items()}}
        if path.endswith("/query"):
            key = body["filter"]["rich_text"]["equals"]
            return {"results": [{"id": pid} for pid, row in self.rows.items() if row["stable_job_key"] == key]}
        row = self.rows[path.rsplit("/", 1)[1]]
        return {"properties": {"Stable Job Key": {"type": "rich_text", "rich_text": [{"plain_text": row["stable_job_key"]}]}, "Visible Lane": {"select": {"name": row["lane"]}}}}

    def query_data_source(self, source, body):
        return {"results": [], "has_more": False}                             # no Applied rows: the journey is the recruiter-lead path

    def create(self, props, blocks):
        pid = f"synthetic-ledger-{len(self.rows) + 1}"
        self.rows[pid] = {"page_id": pid, "company": props["Company"]["rich_text"][0]["text"]["content"], "role": props["Job"]["title"][0]["text"]["content"],
                          "stable_job_key": props["Stable Job Key"]["rich_text"][0]["text"]["content"], "requisition": None,
                          "lane": props["Visible Lane"]["select"]["name"]}
        return pid

    def for_company(self, company):
        return [r for r in self.rows.values() if r["company"] == company]


class Calendar:
    """Google Calendar, in memory: the accepted listing and the deleted-inclusive read."""
    def __init__(self):
        self.events = []

    def list_events(self, time_min, time_max):
        return [e for e in self.events if e.get("status") != "cancelled"]

    def request(self, method, path, params=None):
        return {"items": list(self.events)}


def _event(title, start, status="confirmed"):
    return {"id": title, "summary": title, "status": status, "start": {"dateTime": start}}


def drive(client, environ, pipeline_id, now):
    """The journey against `pipeline_id` (a sandbox Hiring Pipeline): -> (steps, checks), counts and fixed codes only."""
    env = {**environ, "HIRING_PIPELINE_PAGE_ID": pipeline_id, "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on", "INTERVIEW_CONVERT": "",
           "INTERVIEW_TEST_COMPANIES": "", "INTERVIEW_JOURNEY_PROOF": ""}
    ledger, calendar, steps = Ledger(), Calendar(), []

    def tick(records):
        return ev.run(20, True, environ=env, client=client, calendar=calendar, now=now, ledger=ledger, records=list(records),
                      ledger_rows=ledger.for_company, writer=ledger)

    def step(name, records, expect, settle=True):
        ticks = [tick(records)]
        while settle and ticks[-1]["writes"] and len(ticks) < MAX_TICKS:
            ticks.append(tick(records))
        why = {}
        for t in ticks:
            for k, v in t["why"].items():
                why[k] = why.get(k, 0) + v
        last = ticks[-1]
        ok = expect(ticks, why) and (not settle or (last["writes"] == 0 and last["failed"] == 0))
        steps.append({"step": name, "ticks": len(ticks), "writes": sum(t["writes"] for t in ticks), "lead_rows": sum(t["lead_rows"] for t in ticks),
                      "lead_review": last.get("lead_review", {}), "why": {k: v for k, v in why.items() if k not in ("child_match", "parent_unchanged")},
                      "settled_failed": last["failed"], "pass": bool(ok)})

    mail = (LEAD, NO_REQ, LEAD_B)
    day = lambda n: (now + timedelta(days=n)).strftime("%Y-%m-%dT10:00:00-05:00")
    step("1 recruiter leads (one with requisition, one without, a second job)", mail,
         lambda t, w: sum(x["lead_rows"] for x in t) == 2 and w.get("parent_created") == 2 and t[0]["lead_review"] == {"requisition_missing": 1})
    step("2 replay", mail, lambda t, w: t[0]["writes"] == 0, settle=False)
    step("3 employer asks for availability", mail + (REQUEST,), lambda t, w: w.get("round_created") == 1)
    step("3 replay", mail + (REQUEST,), lambda t, w: t[0]["writes"] == 0 and t[0]["failed"] == 0, settle=False)
    calendar.events.append(_event(INVITE, day(7)))
    step("4 Calendar invite accepted", mail + (REQUEST,), lambda t, w: w.get("round_confirmed") == 1)
    calendar.events[0] = _event(INVITE, day(14))
    step("5 same invite moved a week", mail + (REQUEST,), lambda t, w: w.get("round_rescheduled") == 1)
    calendar.events[0] = _event(INVITE, day(14), "cancelled")
    step("6 invite cancelled", mail + (REQUEST,), lambda t, w: w.get("round_cancelled") == 1)
    step("6 replay", mail + (REQUEST,), lambda t, w: t[0]["writes"] == 0 and t[0]["failed"] == 0, settle=False)
    step("N request naming another requisition (second job)", mail + (REQUEST, WRONG_B),
         lambda t, w: t[0]["writes"] == 0 and w.get("requisition_conflict") == 1, settle=False)

    scan = notion.parent_scan(client, pipeline_id)
    titles = {p.title: p.page_id for p in scan.items if p.active}
    rounds = notion.child_scan(client, titles.get("ExampleCo — Program Manager", "")).items if "ExampleCo — Program Manager" in titles else ()
    other = notion.child_scan(client, titles.get("SampleCorp — Analyst", "")).items if "SampleCorp — Analyst" in titles else ()
    calendar_state = create.round_calendar(client, rounds[0].page_id) if len(rounds) == 1 else (None, False)
    checks = {"pipeline_complete": scan.complete, "opportunities": len(titles), "ledger_rows": len(ledger.rows),
              "exampleco_rounds": len(rounds), "exampleco_round_dated": bool(rounds and rounds[0].interview_date == day(14)[:10]),
              "exampleco_round_cancelled": calendar_state == (ev.reference(INVITE), True), "samplecorp_rounds": len(other)}
    checks["pass"] = (checks["pipeline_complete"] and checks["opportunities"] == 2 and checks["ledger_rows"] == 2 and checks["exampleco_rounds"] == 1
                      and checks["exampleco_round_dated"] and checks["exampleco_round_cancelled"] and checks["samplecorp_rounds"] == 0)
    return steps, checks


def sandbox(client, now):
    """A fresh sandbox for this run under the QA parent: container -> "Hiring Pipeline" -> "Active Opportunities". -> the pipeline page id."""
    container = notion.write_tree(client, QA_PARENT, [], title=f"Interview journey proof · {now.strftime('%Y-%m-%d %I:%M %p')} CT (synthetic)")
    pipeline = notion.write_tree(client, container, [], title="Hiring Pipeline")
    notion.write_tree(client, pipeline, [], title="Active Opportunities")
    return pipeline


def run(environ, live, now=None, client=None):
    """-> counts only: per-step results, final read-back checks and `failed` (steps or checks that did not pass)."""
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() == "M1M2":            # LIFE-78: the bounded synthetic M1/M2 exit case, same switch and sandbox rules
        from lifeos.sources import m1m2_proof
        return m1m2_proof.run(environ, live, now=now, client=client)
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() != CONFIRM:
        return {"proof": "refused", "code": "proof_confirm_missing", "failed": 1, "why": {}, "writes": 0}
    if live:
        return {"proof": "refused", "code": "proof_requires_dry_dispatch", "failed": 1, "why": {}, "writes": 0}      # Actually write would make the job's other steps live
    now = (now or datetime.now(ev.TZ)).astimezone(ev.TZ)
    client = client or notion.make_client(environ)
    steps, checks = drive(client, environ, sandbox(client, now), now)
    failed = sum(not s["pass"] for s in steps) + (not checks["pass"])
    return {"proof": "journey", "steps": steps, "checks": checks, "failed": failed, "why": {}, "writes": sum(s["writes"] for s in steps)}
