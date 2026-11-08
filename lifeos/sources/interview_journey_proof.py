"""LIFE-78 synthetic journey proof on real Notion. Manual only: the interview-proof workflow with `confirm = QA` (the Interview token only, its own queue,
no mail read), or a domains dispatch with `interview_journey_proof = QA` and Actually write UNticked, so every other step in that job stays a dry run.
The QA confirmation is this proof's only write switch.

It drives the REAL Interview runner (`interview_evidence.run`) and the REAL Notion writer with the Interview token, tick after tick, against a FRESH
sandbox made for this run under the QA Interview OS parent: a container page, its own "Hiring Pipeline" and "Active Opportunities", and (LIFE-361) its own
small Job Ledger data source where the Interview step makes each pursuit's row. LIFE-353: the journey ends with Confirm Rejected and then Reopen on the
ExampleCo row of that sandbox Ledger (the production `hiring.decision` writer, each repeated to prove it is a no-op), read back. Mail, Google Calendar and the Jobs side of the Ledger are synthetic and held
in memory (ExampleCo / SampleCorp, invented requisitions, example.test links): no real mailbox, calendar, Ledger row or Hiring Pipeline page is read or written. Prints fixed codes and counts per step only. Proves the machine on real Notion; it
is not M1/M2 evidence."""
from datetime import datetime, timedelta
from lifeos.hiring import decision as D
from lifeos.interview import create, notion
from lifeos.interview.ui_probe import QA_PARENT
from lifeos.jobs import ledger as jobs_ledger
from lifeos.platform.names import split_title
from lifeos.platform.notion_client import NotionError, rich_text
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
    """The Jobs side of the Job Ledger, keyed like the real one: the target check, an exact Stable Job Key lookup, one create and its read-back.
    LIFE-361: each row it creates is a REAL row of the sandbox's own Ledger (Job, Company, Stable Job Key), so the Interview step finds that row and every
    reader that opens the row page (Network Leads, the opportunity itself) reads a real page; its other answers stay in memory."""
    source = "synthetic-ledger"

    def __init__(self, client=None, sandbox=None):
        self.rows, self.client, self.sandbox = {}, client, sandbox

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
        if self.client is not None:                                            # the row lives in the sandbox Ledger: only the properties that Ledger has
            kept = {name: props[name] for name in ("Job", "Company", "Stable Job Key") if name in props}
            pid = self.client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": self.sandbox}, "properties": kept})["id"]
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


def drive(client, environ, pipeline_id, now, ledger_id):
    """The journey against `pipeline_id` (a sandbox Hiring Pipeline) and `ledger_id` (its sandbox Ledger): -> (steps, checks), counts and fixed codes only."""
    env = {**environ, "HIRING_PIPELINE_PAGE_ID": pipeline_id, "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on", "INTERVIEW_CONVERT": "",
           "INTERVIEW_TEST_COMPANIES": "", "INTERVIEW_JOURNEY_PROOF": "", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": ledger_id}
    ledger, calendar, steps = Ledger(client, ledger_id), Calendar(), []

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

    scan = notion.parents(client, env)                                       # LIFE-361: each pursuit is its sandbox Ledger row
    titles = {split_title(p.title): p.page_id for p in scan.items if p.active}
    rounds = notion.child_scan(client, titles[("ExampleCo", "Program Manager")]).items if ("ExampleCo", "Program Manager") in titles else ()
    other = notion.child_scan(client, titles[("SampleCorp", "Analyst")]).items if ("SampleCorp", "Analyst") in titles else ()
    calendar_state = create.round_calendar(client, rounds[0].page_id) if len(rounds) == 1 else (None, False)
    checks = {"pipeline_complete": scan.complete, "opportunities": len(titles), "ledger_rows": len(ledger.rows),
              "exampleco_rounds": len(rounds), "exampleco_round_dated": bool(rounds and rounds[0].interview_date == day(14)[:10]),
              "exampleco_round_cancelled": calendar_state == (ev.reference(INVITE), True), "samplecorp_rounds": len(other)}
    checks["pass"] = (checks["pipeline_complete"] and checks["opportunities"] == 2 and checks["ledger_rows"] == 2 and checks["exampleco_rounds"] == 1
                      and checks["exampleco_round_dated"] and checks["exampleco_round_cancelled"] and checks["samplecorp_rounds"] == 0)
    steps.append(decide(client, ledger_id, titles.get(("ExampleCo", "Program Manager")), now))
    return steps, checks


class Rows:
    """The sandbox Ledger as the decision writer reads it: its own data source id, the row page read and one properties write (no retry)."""
    def __init__(self, client, source):
        self.client, self.source = client, source

    def call(self, method, path, body=None):
        return self.client.call(method, path, body)

    def query_data_source(self, source_id=None, body=None):
        return self.client.query_data_source(self.source, body)

    def get_page(self, page_id):
        return self.client.call("GET", f"/pages/{page_id}")

    def update_page_properties(self, page_id, properties):
        return self.client.call_once("PATCH", f"/pages/{page_id}", {"properties": properties})


def decide(client, ledger_id, row_id, now):
    """LIFE-353 GWT-DW-1/2 on the sandbox: Confirm Rejected then Reopen on the ExampleCo row, each sent twice (the second is `already_recorded`), the row read back
    after each: Hiring Decision set then cleared, two history lines, Stage unchanged. -> one step, codes and counts only."""
    out = {"step": "D Confirm Rejected then Reopen on the Ledger row", "ticks": 0, "writes": 0, "lead_rows": 0, "lead_review": {}, "why": {}, "settled_failed": 0, "pass": False}
    if not row_id:
        out["why"] = {"decision_row_missing": 1}
        return out
    rows, stamp = Rows(client, ledger_id), now.strftime("%Y%m%d%H%M")
    base = {"confirmed": True, "id": row_id, "company": "ExampleCo", "role": "Program Manager"}
    close = {**base, "action": D.ACTION, "outcome": D.REJECTED, "reason": "Synthetic proof close", "op": f"proof-close-{stamp}"}
    reopen = {**base, "action": D.REOPEN, "reason": "Synthetic proof reopen", "op": f"proof-reopen-{stamp}"}

    def state():
        props = (rows.get_page(row_id) or {}).get("properties") or {}
        return (((props.get(D.DECISION) or {}).get("select") or {}).get("name"), len(D.entries(D._text(props.get(D.HISTORY), "rich_text"))),
                ((props.get("Stage") or {}).get("select") or {}).get("name"))
    try:
        routed = D._page_id(D.route(D.rows(D.ledger_pages(rows)), close)["id"]) == D._page_id(row_id)
        before = state()
        done = [D.confirm_rejected(rows, close, True, now)["status"], D.confirm_rejected(rows, close, True, now)["status"]]
        closed = state()
        done += [D.confirm_rejected(rows, reopen, True, now)["status"], D.confirm_rejected(rows, reopen, True, now)["status"]]
        opened = state()
    except NotionError as error:
        out["why"] = {str(error)[:60]: 1}
        return out
    out["why"] = {f"decision_{s}": done.count(s) for s in sorted(set(done))}
    out["writes"] = done.count("recorded")
    out["pass"] = (routed and done == ["recorded", "already_recorded", "recorded", "already_recorded"] and before[:2] == (None, 0)
                   and closed[:2] == (D.REJECTED, 1) and opened[:2] == (None, 2) and before[2] == closed[2] == opened[2] and before[2] is not None)
    return out


LEDGER_SCHEMA = {"Job": {"title": {}}, "Company": {"rich_text": {}}, "Stable Job Key": {"rich_text": {}}, "Source Types": {"multi_select": {}},
                 "Stage": {"select": {"options": [{"name": n} for n in notion.LEDGER_STAGES]}},
                 "Hiring Decision": {"select": {"options": [{"name": D.REJECTED}, {"name": D.PASSED}]}},             # the sandbox's own options; the real Ledger is never changed
                 "Hiring Decision History": {"rich_text": {}}, "Dismissed Outcome Ref": {"rich_text": {}}}


def sandbox(client, now):
    """A fresh sandbox for this run under the QA parent: container -> "Hiring Pipeline" -> "Active Opportunities", and the container's own small Job Ledger
    (only the properties the Interview step reads and writes). -> (the pipeline page id, the Ledger data source id). A refused database fails the run closed."""
    container = notion.write_tree(client, QA_PARENT, [], title=f"Interview journey proof · {now.strftime('%Y-%m-%d %I:%M %p')} CT (synthetic)")
    pipeline = notion.write_tree(client, container, [], title="Hiring Pipeline")
    notion.write_tree(client, pipeline, [], title="Active Opportunities")
    made = client.call_once("POST", "/databases", {"parent": {"type": "page_id", "page_id": container}, "title": rich_text("Job Ledger (synthetic)"),
                                                   "initial_data_source": {"properties": LEDGER_SCHEMA}})
    sources = made.get("data_sources") if isinstance(made, dict) else None
    if not isinstance(sources, list) or len(sources) != 1 or not sources[0].get("id"):
        raise NotionError("JOURNEY_SANDBOX_LEDGER_UNREADABLE")
    return pipeline, sources[0]["id"]


def run(environ, live, now=None, client=None):
    """-> counts only: per-step results, final read-back checks and `failed` (steps or checks that did not pass)."""
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() == "M1M2":            # LIFE-78: the bounded synthetic M1/M2 exit case, same switch and sandbox rules
        from lifeos.sources import m1m2_proof
        return m1m2_proof.run(environ, live, now=now, client=client)
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() == "E10":              # LIFE-88: the synthetic Network Leads / LinkedIn Contacts QA, same switch and sandbox rules
        from lifeos.sources import interview_contacts_proof
        return interview_contacts_proof.run(environ, live, now=now, client=client)
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() == "LAYOUT":           # Jim, Oct 27: the two-column Ledger page and its migration, on real Notion in a fresh sandbox
        from lifeos.sources import interview_contacts_proof
        return interview_contacts_proof.run_layout(environ, live, now=now, client=client)
    if (environ.get("INTERVIEW_JOURNEY_PROOF") or "").strip() != CONFIRM:
        return {"proof": "refused", "code": "proof_confirm_missing", "failed": 1, "why": {}, "writes": 0}
    if live:
        return {"proof": "refused", "code": "proof_requires_dry_dispatch", "failed": 1, "why": {}, "writes": 0}      # Actually write would make the job's other steps live
    now = (now or datetime.now(ev.TZ)).astimezone(ev.TZ)
    client = client or notion.make_client(environ)
    pipeline, ledger_id = sandbox(client, now)
    steps, checks = drive(client, environ, pipeline, now, ledger_id)
    failed = sum(not s["pass"] for s in steps) + (not checks["pass"])
    return {"proof": "journey", "steps": steps, "checks": checks, "failed": failed, "why": {}, "writes": sum(s["writes"] for s in steps)}
