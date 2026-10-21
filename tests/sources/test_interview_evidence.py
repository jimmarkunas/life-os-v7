"""LIFE-88: Interview OS's normal path composes only verified evidence, fails each gap visibly and alone, and writes only on Jim's switch."""
import unittest
from unittest.mock import patch

from lifeos.interview import create, notion
from lifeos.jobs import ledger as jobs_ledger
from lifeos.platform.notion_client import NotionError
from lifeos.interview.models import Child, Ownership, Parent, Scan
from lifeos.run import STAGES
from lifeos.sources import interview_evidence as ev

URL = "https://example.test/thread/1"
OTHER = "https://example.test/thread/2"
ENV = {"NOTION_INTERVIEW_TOKEN": "synthetic", "HIRING_PIPELINE_PAGE_ID": "root"}
ROW = {"page_id": "ledger-1", "company": "ExampleCo", "role": "Program Manager", "stable_job_key": "exampleco-r-1", "requisition": None}


class Calendar:
    def __init__(self, *events):
        self.events = list(events)

    def list_events(self, start, end):
        return self.events


def event(title, start="2026-10-28T10:00:00-05:00", **extra):
    return {"id": title, "summary": title, "status": "confirmed", "start": {"dateTime": start}, **extra}


def child(id="c1", date=None, who=None, ordinal=1):
    return Child(id, "p1", date, who, ordinal, True)


class Ledger:
    """The Interview token's Ledger client: a page read returns the Stable Job Key and Apply URL of the row asked for."""
    def __init__(self, key="exampleco-r-1", url="https://example.test/jobs/12345"):
        self.props = {"Stable Job Key": {"type": "rich_text", "rich_text": [{"plain_text": key}] if key else []}, "Apply URL": {"type": "url", "url": url}}

    def call(self, method, path):
        return {"properties": self.props}


APPLIED = {"id": "ledger-1", "company": "ExampleCo", "role": "Program Manager", "applied_on": "2026-10-01", "decision": None}


def mail(subject="Schedule your interview - ExampleCo Program Manager", body="Please select a time for a call. Requisition 12345.", **extra):
    return {"provider": "gmail", "id": subject, "received": "2026-10-20T15:00:00Z", "sender_name": "ExampleCo Talent", "sender_address": "no-reply.example",
            "subject": subject, "body": body, "folder": "INBOX", "markers": {"bulk": False, "auto": True, "noreply": True}, "url": "https://example.test/thread/9", **extra}


def outreach(role="Program Manager", client="ExampleCo", person="Pat Example", url="https://example.test/thread/5", received="2026-10-20T15:00:00Z", extra=""):
    """A recruiter's first message: a person who says they are a recruiter, a role and (when given) the client."""
    return {"provider": "gmail", "id": url, "received": received, "sender_name": person, "sender_address": "pat-example", "subject": f"{role} opportunity", "folder": "INBOX",
            "markers": {"bulk": False, "auto": False, "noreply": False}, "url": url,
            "body": f"Hi Jim,\n\nI'm a senior recruiter at Staffing Example Inc. I am hiring for a {role} role" + (f" for our client {client}" if client else "") + f". {extra}\n\n{person}\nSenior Recruiter\nStaffing Example Inc\n"}


class Writer:
    """The Jobs token's Ledger client: the target check, the key lookup, one create and its read-back."""
    source = "ledger-source"

    def __init__(self, held=0, lane=ev.LEAD_LANE, target=True, fail=False):
        self.held, self.lane, self.target, self.fail, self.created = held, lane, target, fail, []

    def call(self, method, path, body=None):
        if path == f"/data_sources/{self.source}":
            return {"properties": {n: {"type": k if self.target else "number"} for n, k in jobs_ledger.REQUIRED.items()}}
        if path.endswith("/query"):
            return {"results": [{"id": f"held-{n}"} for n in range(self.held)]}
        props = self.created[-1]
        return {"properties": {"Stable Job Key": props["Stable Job Key"] and {"type": "rich_text", "rich_text": [{"plain_text": props["Stable Job Key"]["rich_text"][0]["text"]["content"]}]},
                               "Visible Lane": {"select": {"name": self.lane}}}}

    def create(self, props, blocks):
        if self.fail:
            raise NotionError("NOTION_NETWORK")
        self.created.append(props)
        return "new-page"


class Run:
    """ev.run with the Notion reads replaced by plain data; `stage.run` is captured, never executed."""

    def __init__(self, parents=(("p1", "ExampleCo — Program Manager"),), facts=None, kids=None, rows=(ROW,), calendar=None, environ=ENV, live=True, roster=(), records=(), ledger=None, retired=(), writer=None):
        self.writer = writer
        self.roster, self.records, self.ledger = roster if isinstance(roster, Exception) else list(roster), None if records is None else list(records), ledger or Ledger()
        self.parents = [Parent(i, t) for i, t in parents] + [Parent("r" + str(n), t, False) for n, t in enumerate(retired)]
        self.facts = facts if facts is not None else {"p1": ("exampleco-r-1", URL)}
        self.kids = kids if kids is not None else {"p1": (child(),)}
        self.rows, self.calendar, self.environ, self.live = rows, calendar if calendar is not None else Calendar(), environ, live

    def __call__(self):
        captured = {}

        def fake_stage(limit, live, environ, client, evidence, context):
            captured.update(evidence=list(evidence), live=live)
            return {"blocked": 0, "why": {}}

        with patch.object(notion, "parent_scan", return_value=Scan(tuple(self.parents), True)), \
             patch.object(notion, "ownership", return_value=Ownership.MACHINE), \
             patch.object(ev, "facts", side_effect=lambda client, page_id, context: self.facts.get(page_id)), \
             patch.object(notion, "child_scan", side_effect=lambda client, page_id, context=None: Scan(self.kids.get(page_id, ()), True)), \
             patch.object(ev.pipeline, "read_applied", **({"side_effect": self.roster} if isinstance(self.roster, Exception) else {"return_value": self.roster})), \
             patch.object(ev.stage, "run", side_effect=fake_stage):
            counts = ev.run(20, self.live, environ=self.environ, client=object(), calendar=self.calendar, ledger_rows=lambda company: list(self.rows),
                            ledger=self.ledger, records=self.records, writer=self.writer)
        return counts, captured


class Ordinal(unittest.TestCase):
    def test_only_an_explicit_single_round_number_counts(self):
        for title, want in (("ExampleCo Interview 2", 2), ("Round 3 - ExampleCo", 3), ("ExampleCo 2nd interview", 2), ("ExampleCo interview", None),
                            ("Interview 2 and Round 3", None), ("Interview 2026", None), ("", None), (None, None)):
            self.assertEqual(ev.ordinal(title), want, title)


class Refresh(unittest.TestCase):
    def test_an_existing_scheduling_pending_page_is_refreshed_from_its_own_facts(self):
        counts, got = Run()()
        (parent, query), = got["evidence"]
        self.assertEqual((parent.company, parent.role), ("ExampleCo", "Program Manager"))
        self.assertEqual((query.interview_date, query.ordinal, query.source, query.stable_job_key, query.ledger_page_id), (None, 1, URL, "exampleco-r-1", "ledger-1"))
        self.assertEqual(counts["failed"], 0)

    def test_a_dated_round_keeps_the_job_and_the_request_the_progression_drops(self):
        _, got = Run(kids={"p1": (child("c2", "2026-10-01", "Pat Example", 2),)})()
        (_, query), = got["evidence"]
        self.assertEqual((query.interview_date, query.interviewer, query.ordinal, query.stable_job_key, query.source), ("2026-10-01", "Pat Example", 2, "exampleco-r-1", URL))

    def test_nothing_is_written_without_jims_switch(self):
        self.assertFalse(Run()()[1]["live"])
        self.assertFalse(Run(environ={**ENV, "INTERVIEW_OS_WRITER": "on"})()[1]["live"])
        self.assertTrue(Run(environ={**ENV, "INTERVIEW_OS_WRITER": "v7"})()[1]["live"])
        self.assertFalse(Run(environ={**ENV, "INTERVIEW_OS_WRITER": "v7"}, live=False)()[1]["live"])

    def test_wrong_or_missing_evidence_fails_that_page_visibly_and_writes_nothing(self):
        for label, run in (("no request message", Run(facts={"p1": ("exampleco-r-1", None)})),
                           ("ledger has another job", Run(rows=({**ROW, "stable_job_key": "exampleco-r-9"},))),
                           ("ledger empty", Run(rows=())),
                           ("page names no job", Run(facts={"p1": (None, URL)}))):
            counts, got = run()
            self.assertEqual(got["evidence"], [], label)
            self.assertEqual(counts["failed"], 1, label)

    def test_a_ui3b_page_waiting_for_migration_is_degraded_not_a_quiet_success(self):
        counts, got = Run(facts={"p1": None})()
        self.assertEqual((got["evidence"], counts["failed"], counts["why"]), ([], 1, {"parent_legacy_layout": 1}))

    def test_one_gap_does_not_stop_another_valid_page(self):
        run = Run(parents=(("p1", "ExampleCo — Program Manager"), ("p2", "OtherCo — Analyst")), facts={"p1": ("exampleco-r-1", URL), "p2": ("otherco-r-1", None)},
                  kids={"p1": (child(),), "p2": (child("c9"),)}, rows=(ROW, {**ROW, "page_id": "ledger-2", "company": "OtherCo", "role": "Analyst", "stable_job_key": "otherco-r-1"}))
        counts, got = run()
        self.assertEqual([p.company for p, _ in got["evidence"]], ["ExampleCo"])
        self.assertEqual(counts["failed"], 1)


class CalendarRounds(unittest.TestCase):
    def test_an_event_that_states_its_round_adds_that_round_before_the_refresh(self):
        counts, got = Run(calendar=Calendar(event("ExampleCo Program Manager Interview 2")))()
        (_, new), (_, refresh) = got["evidence"]
        self.assertEqual((new.interview_date, new.ordinal, new.interviewer, new.stable_job_key, new.source), ("2026-10-28", 2, None, "exampleco-r-1", URL))
        self.assertEqual((refresh.interview_date, refresh.ordinal), (None, 1))
        self.assertEqual(counts["failed"], 0)

    def test_an_event_without_a_round_number_is_incomplete_and_never_numbered_from_the_pages(self):
        counts, got = Run(calendar=Calendar(event("ExampleCo Program Manager Interview")))()
        self.assertEqual(len(got["evidence"]), 1)                              # only the refresh of the existing page
        self.assertEqual((counts["why"], counts["failed"]), ({"round_identity_incomplete": 1}, 1))

    def test_the_same_round_number_on_another_date_conflicts_and_creates_nothing(self):
        counts, got = Run(kids={"p1": (child("c2", "2026-10-01", "Pat Example", 2),)}, calendar=Calendar(event("ExampleCo Program Manager Interview 2")))()
        self.assertEqual(len(got["evidence"]), 1)
        self.assertEqual((counts["why"], counts["failed"]), ({"round_conflict": 1}, 1))

    def test_an_event_for_a_round_page_that_exists_adds_nothing(self):
        counts, got = Run(kids={"p1": (child("c2", "2026-10-28", None, 2),)}, calendar=Calendar(event("ExampleCo Program Manager Interview 2")))()
        self.assertEqual((len(got["evidence"]), counts["failed"]), (1, 0))

    def test_events_that_are_not_interviews_or_not_this_job_or_not_accepted_are_ignored(self):
        declined = event("ExampleCo Program Manager Interview 3", attendees=[{"self": True, "responseStatus": "declined"}])
        cancelled = {**event("ExampleCo Program Manager Interview 4"), "status": "cancelled"}
        counts, got = Run(calendar=Calendar(event("ExampleCo Program Manager recruiter screen call"), event("Unrelated Corp Interview 2"), declined, cancelled))()
        self.assertEqual((len(got["evidence"]), counts["failed"], counts["why"]), (1, 0, {}))

    def test_an_unreadable_calendar_is_reported_and_the_refresh_still_runs(self):
        class Broken:
            def list_events(self, *_):
                raise RuntimeError("down")
        counts, got = Run(calendar=Broken())()
        self.assertEqual((len(got["evidence"]), counts["why"], counts["failed"]), (1, {"calendar_unavailable": 1}, 1))


class SchedulingRequests(unittest.TestCase):
    """E01a: a scheduling request in mail (a person's or an automated one) for a job Jim applied to becomes Interview 1, Scheduling Pending."""
    def run_with(self, *records, parents=(), **kw):
        return Run(parents=parents, facts={} if not parents else None, kids={}, roster=[APPLIED], records=records, **kw)()

    def test_an_automated_request_for_an_applied_job_creates_the_pending_interview_1(self):
        counts, got = self.run_with(mail())
        (parent, query), = got["evidence"]
        self.assertEqual((parent.company, parent.role), ("ExampleCo", "Program Manager"))
        self.assertEqual((query.interview_date, query.ordinal, query.source, query.stable_job_key, query.ledger_page_id, query.requisition),
                         (None, 1, "https://example.test/thread/9", "exampleco-r-1", "ledger-1", "12345"))
        self.assertEqual((counts["failed"], counts["requests"]), (0, 1))

    def test_a_person_writing_counts_the_same_and_the_newest_request_wins(self):
        newer = mail(url="https://example.test/thread/10", sender_name="Pat Example", sender_address="pat-example", markers={"bulk": False, "auto": False, "noreply": False})
        newer["received"] = "2026-10-21T15:00:00Z"
        _, got = self.run_with(newer, mail())
        self.assertEqual([q.source for _, q in got["evidence"]], ["https://example.test/thread/10"])

    def test_rejections_offers_other_companies_and_other_mail_are_not_requests(self):
        for label, message in (("rejection", mail(body="We will not be moving forward. Unfortunately we cannot schedule an interview.")),
                               ("offer", mail(body="We are pleased to offer you the role. Schedule a call to discuss.")),
                               ("other company", mail(subject="Schedule your interview - OtherCorp Analyst", body="Select a time.", sender_name="OtherCorp")),
                               ("newsletter", mail(subject="ExampleCo Program Manager jobs this week", body="Ten new roles."))):
            counts, got = self.run_with(message)
            self.assertEqual((got["evidence"], counts["failed"]), ([], 0), label)

    def test_a_request_with_no_applied_job_is_left_for_the_lead_path(self):
        counts, got = Run(parents=(), kids={}, roster=[], records=[mail()])()
        self.assertEqual((got["evidence"], counts["failed"]), ([], 0))

    def test_two_applied_rows_the_text_cannot_tell_apart_are_ambiguous_and_nothing_is_written(self):
        twin = {**APPLIED, "id": "ledger-2"}
        counts, got = Run(parents=(), kids={}, roster=[APPLIED, twin], records=[mail()])()
        self.assertEqual((got["evidence"], counts["why"], counts["failed"]), ([], {"request_ambiguous": 1}, 1))

    def test_a_requisition_the_apply_url_does_not_carry_blocks_the_link(self):
        counts, got = self.run_with(mail(body="Select a time. Requisition 99999."))
        self.assertEqual((got["evidence"], counts["why"], counts["failed"]), ([], {"requisition_conflict": 1}, 1))

    def test_a_ledger_row_without_a_stable_job_key_is_not_guessed(self):
        counts, got = Run(parents=(), kids={}, roster=[APPLIED], records=[mail()], ledger=Ledger(key=""))()
        self.assertEqual((got["evidence"], counts["why"]), ([], {"job_key_missing": 1}))

    def test_a_job_that_already_has_a_round_is_not_given_interview_1_again(self):
        same = mail(url=URL)
        counts, got = Run(roster=[APPLIED], records=[same])()                 # the page's own request message: nothing new, the refresh runs
        self.assertEqual((len(got["evidence"]), counts["failed"]), (1, 0))
        counts, got = Run(roster=[APPLIED], records=[mail()])()               # another message: its round number is unknown, so it is reported, not numbered
        self.assertEqual((counts["why"], counts["failed"]), ({"round_identity_incomplete": 1}, 1))
        self.assertEqual([q.source for _, q in got["evidence"]], [URL])

    def test_a_machine_parent_with_no_round_gets_interview_1(self):
        _, got = Run(kids={"p1": ()}, roster=[APPLIED], records=[mail()])()
        self.assertEqual([q.source for _, q in got["evidence"]], ["https://example.test/thread/9"])

    def test_a_page_for_another_job_key_is_a_conflict(self):
        counts, got = Run(facts={"p1": ("exampleco-r-2", URL)}, roster=[APPLIED], records=[mail()])()
        self.assertEqual(counts["why"].get("identity_conflict"), 1)

    def test_unread_mail_is_degraded_and_never_a_quiet_zero(self):
        with patch.object(ev, "read_mail", return_value=([], 1)):
            counts, _ = Run(parents=(), kids={}, roster=[APPLIED], records=None)()
        self.assertEqual((counts["why"], counts["failed"]), ({"mail_unavailable": 1}, 1))

    def test_an_unreadable_ledger_is_degraded(self):
        counts, got = Run(parents=(), kids={}, roster=RuntimeError("down"), records=[mail()])()
        self.assertEqual((got["evidence"], counts["why"], counts["failed"]), ([], {"ledger_unreadable": 1}, 1))


class Leads(unittest.TestCase):
    """E01b: every recruiter outreach that names a role is a lead with a minted Stable Job Key, and its page is created on the first message, with no reply needed."""
    def test_a_recruiters_first_message_names_a_client_a_role_and_a_minted_key(self):
        self.assertEqual(ev.lead_of(outreach()), ("ExampleCo", "Program Manager", None, "recruiter-exampleco-program-manager", "https://example.test/thread/5"))
        company, role, req, key, _ = ev.lead_of(outreach(extra="Requisition 123456."))
        self.assertEqual((req, key), ("123456", "recruiter-exampleco-123456"))
        self.assertEqual(ev.lead_of(outreach(client=""))[0], "Staffing Example Inc")             # no client named: the recruiter's company, as stated

    def test_no_role_an_automated_sender_or_no_company_is_not_a_lead(self):
        noreply = {**outreach(), "markers": {"bulk": False, "auto": True, "noreply": True}, "sender_address": "no-reply.example"}
        bulk = {**outreach(), "markers": {"bulk": True, "auto": False, "noreply": False}}
        norole = {**outreach(), "subject": "Hello", "body": "Hi Jim,\n\nI'm a senior recruiter at Staffing Example Inc. Are you open to a call?\n\nPat Example\nSenior Recruiter\nStaffing Example Inc\n"}
        for label, message in (("automated", noreply), ("bulk", bulk), ("no role", norole)):
            self.assertIsNone(ev.lead_of(message), label)

    def test_the_first_message_queues_a_parent_with_no_round_and_the_minted_key(self):
        counts, got = Run(parents=(), kids={}, roster=[], records=[outreach()])()
        (parent, query), = got["evidence"]
        self.assertEqual((parent.company, parent.role), ("ExampleCo", "Program Manager"))
        self.assertEqual((query.stable_job_key, query.source, query.ledger_page_id, query.interview_date), ("recruiter-exampleco-program-manager", "https://example.test/thread/5", None, None))
        self.assertEqual((counts["leads"], counts["failed"]), (1, 0))

    def test_an_existing_active_or_retired_page_or_an_applied_job_is_never_a_second_lead(self):
        for label, run in (("active page", Run(roster=[], records=[outreach()])), ("retired page", Run(parents=(), retired=["ExampleCo — Program Manager"], kids={}, roster=[], records=[outreach()])),
                           ("applied job", Run(parents=(), kids={}, roster=[APPLIED], records=[outreach()]))):
            counts, got = run()
            self.assertEqual(counts["leads"], 0, label)
            self.assertNotIn("recruiter-exampleco-program-manager", [q.stable_job_key for _, q in got["evidence"]], label)

    def test_one_job_from_two_messages_is_one_lead_and_a_burst_is_capped_newest_first(self):
        twice = [outreach(url="https://example.test/thread/1", received="2026-10-19T10:00:00Z"), outreach(url="https://example.test/thread/2", received="2026-10-20T10:00:00Z")]
        _, got = Run(parents=(), kids={}, roster=[], records=twice)()
        self.assertEqual([q.source for _, q in got["evidence"]], ["https://example.test/thread/2"])
        many = [outreach(role=f"Role Number {n}", client=f"Client{chr(65 + n)} Corp", url=f"https://example.test/m/{n}", received=f"2026-10-{10 + n}T10:00:00Z") for n in range(8)]
        counts, got = Run(parents=(), kids={}, roster=[], records=many)()
        self.assertEqual((counts["leads"], len(got["evidence"])), (8, ev.LEADS_PER_RUN))
        self.assertEqual([q.source for _, q in got["evidence"]][:2], ["https://example.test/m/7", "https://example.test/m/6"])

    def test_an_incomplete_pipeline_scan_creates_no_lead(self):
        run = Run(parents=(), kids={}, roster=[], records=[outreach()])
        with patch.object(notion, "parent_scan", return_value=Scan((), False)), patch.object(ev.stage, "run", return_value={"blocked": 0, "why": {}}):
            counts = ev.run(20, True, environ=ENV, client=object(), calendar=Calendar(), ledger=Ledger(), records=run.records, ledger_rows=lambda c: [])
        self.assertEqual(counts["leads"], 0)


LIVE = {**ENV, "INTERVIEW_OS_WRITER": "v7"}


class LeadRows(unittest.TestCase):
    """E01b: a lead is a Job Ledger row (Visible Lane Recruiter, Admitted, not Applied), written and read back before its page, once per Stable Job Key."""
    def go(self, writer, live=True):
        return Run(parents=(), kids={}, roster=[], records=[outreach()], environ=LIVE if live else ENV, writer=writer)()

    def test_a_live_run_writes_the_ledger_row_then_queues_the_page(self):
        writer = Writer()
        counts, got = self.go(writer)
        row, = writer.created
        self.assertEqual((row["Visible Lane"]["select"]["name"], row["Admission Status"]["select"]["name"], row["Stable Job Key"]["rich_text"][0]["text"]["content"]),
                         ("Recruiter", "Admitted", "recruiter-exampleco-program-manager"))
        self.assertNotIn("Applied", row)
        self.assertEqual((counts["lead_rows"], len(got["evidence"]), counts["failed"]), (1, 1, 0))

    def test_a_dry_run_writes_no_row(self):
        writer = Writer()
        counts, _ = self.go(writer, live=False)
        self.assertEqual((writer.created, counts["lead_rows"], counts["leads"]), ([], 0, 1))

    def test_a_row_that_exists_is_not_created_again_but_its_page_is_still_queued(self):
        writer = Writer(held=1)
        counts, got = self.go(writer)
        self.assertEqual((writer.created, counts["lead_rows"], len(got["evidence"])), ([], 0, 1))

    def test_a_failed_wrong_target_or_unreadable_row_creates_no_page_and_is_degraded(self):
        for label, writer in (("create failed", Writer(fail=True)), ("not the Ledger", Writer(target=False)), ("read-back differs", Writer(lane="Scale-up"))):
            counts, got = self.go(writer)
            self.assertEqual((got["evidence"], counts["why"].get("lead_row_failed"), counts["failed"] > 0), ([], 1, True), label)

    def test_two_rows_for_one_key_are_refused(self):
        counts, got = self.go(Writer(held=2))
        self.assertEqual((got["evidence"], counts["why"].get("lead_row_failed")), ([], 1))


class Exceptions(unittest.TestCase):
    """A named test pursuit may be older than 14 days; nothing else is."""
    NOW = ev.datetime(2026, 10, 21, 12, tzinfo=ev.TZ)

    def test_the_window_widens_only_when_test_pursuits_are_named(self):
        self.assertEqual((ev.window({}, True), ev.window({"INTERVIEW_TEST_COMPANIES": "Alpha, Beta"}, True), ev.window({"INTERVIEW_TEST_COMPANIES": " , "}, True)), (14, 60, 14))

    def test_an_old_message_is_kept_only_when_it_names_a_test_pursuit(self):
        old, new = "2026-09-10T10:00:00Z", "2026-10-20T10:00:00Z"
        named, other = {**mail(), "received": old, "subject": "Alpha interview"}, {**mail(), "received": old, "subject": "Gamma interview"}
        recent = {**mail(), "received": new, "subject": "Gamma interview"}
        env = {"INTERVIEW_TEST_COMPANIES": "Alpha"}
        self.assertEqual(ev.recent_or_named([named, other, recent], env, self.NOW, True), [named, recent])
        self.assertEqual(ev.recent_or_named([named, other, recent], {**env, "INTERVIEW_MAIL_DAYS": "60"}, self.NOW, False), [named, other, recent])      # an explicit dry-run look keeps all

    def test_a_linkedin_sender_headline_names_the_employer(self):
        body = "You have a new message\n\nPat Example (Senior Recruiter at Example Corp | Hiring)\n(https://example.test/in/pat)\n\nThis email was intended for Jim (Engineer at Elsewhere)\n"
        self.assertEqual(ev.headline_company({"body": body}), "Example Corp")
        self.assertEqual(ev.headline_company({"body": body.replace("Senior Recruiter at Example Corp | Hiring", "Recruiter")}), "")      # the footer describes Jim, never the sender


POSTING = "https://boards.greenhouse.io/exampleco/jobs/1234567"


class Cursor:
    def __init__(self, new=True):
        self.sql, self.rowcount = [], 1 if new else 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        self.sql.append((sql.split()[0], args))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class LinkLeads(unittest.TestCase):
    """E01b: a recruiter's public posting link is a real job. It goes through Jobs OS (Enrich, Fit, Luna on the public posting, Publish); the email text never does."""
    def message(self, *links):
        return outreach(extra=" ".join(links))

    def test_exactly_one_public_ats_posting_is_the_job_and_its_key_is_the_one_jobs_os_gives_that_link(self):
        from lifeos.jobs import identity
        self.assertEqual(ev.posting_link(self.message(POSTING)), POSTING)
        self.assertEqual(ev.lead_of(self.message(POSTING))[3], identity.url_hash(POSTING))
        for label, links in (("two postings", (POSTING, "https://boards.greenhouse.io/exampleco/jobs/7654321")), ("not an ATS", ("https://www.example.com/jobs/1",)),
                             ("a listing, not one vacancy", ("https://boards.greenhouse.io/exampleco",)), ("no link", ())):
            self.assertIsNone(ev.posting_link(self.message(*links)), label)
        self.assertEqual(ev.lead_of(self.message())[3], "recruiter-exampleco-program-manager")

    def test_a_new_lead_job_is_inserted_resolved_in_the_recruiter_lane_at_its_own_link(self):
        cursor = Cursor()
        self.assertTrue(ev.lead_job("ExampleCo", "Program Manager", POSTING, outreach(), ev.datetime(2026, 10, 21, 12, tzinfo=ev.TZ), connect=lambda: cursor))
        insert = next(args for kind, args in cursor.sql if kind == "INSERT")
        update = cursor.sql[-1]
        self.assertIn("RESOLVED", insert)
        self.assertTrue({"Recruiter", "recruiter", "Recruiter outreach"} <= set(insert))
        self.assertEqual((update[0], update[1][0]), ("UPDATE", POSTING))
        again = Cursor(new=False)
        self.assertFalse(ev.lead_job("ExampleCo", "Program Manager", POSTING, outreach(), ev.datetime(2026, 10, 21, tzinfo=ev.TZ), connect=lambda: again))
        self.assertEqual([kind for kind, _ in again.sql if kind == "UPDATE"], ["UPDATE"])      # only the seen-count bump of a link Jobs OS already holds; no new row, no link rewrite

    def test_a_live_run_sends_a_link_lead_to_jobs_os_and_a_linkless_one_to_a_direct_row(self):
        writer = Writer()
        with patch.object(ev, "lead_job", return_value=True) as job:
            counts, got = Run(parents=(), kids={}, roster=[], records=[outreach(extra=POSTING)], environ=LIVE, writer=writer)()
        self.assertEqual((job.call_count, writer.created, counts["lead_jobs"], counts["lead_rows"], len(got["evidence"])), (1, [], 1, 0, 1))
        with patch.object(ev, "lead_job", return_value=True) as job:
            counts, _ = Run(parents=(), kids={}, roster=[], records=[outreach()], environ=LIVE, writer=writer)()
        self.assertEqual((job.call_count, len(writer.created), counts["lead_rows"]), (0, 1, 1))

    def test_a_job_store_failure_is_degraded_and_creates_no_page(self):
        with patch.object(ev, "lead_job", side_effect=RuntimeError("db")):
            counts, got = Run(parents=(), kids={}, roster=[], records=[outreach(extra=POSTING)], environ=LIVE, writer=Writer())()
        self.assertEqual((got["evidence"], counts["why"].get("lead_row_failed")), ([], 1))


class Window(unittest.TestCase):
    def test_a_longer_mail_window_is_for_a_dry_run_only_and_never_beyond_90_days(self):
        for asked, live, want in (("", False, 14), ("60", False, 60), ("60", True, 14), ("5", False, 14), ("500", False, 90), ("abc", False, 14), ("-3", False, 14)):
            self.assertEqual(ev.window({"INTERVIEW_MAIL_DAYS": asked}, live), want, (asked, live))
        self.assertEqual(ev.window({}, False), 14)


class Pursuits(unittest.TestCase):
    """A pursuit with no Applied Ledger row (an inbound lead) is matched and reported, never written, until E01b."""
    def test_a_request_for_a_pursuit_with_no_applied_row_is_counted_as_a_lead_and_nothing_is_queued_for_it(self):
        counts, got = Run(parents=(("p1", "ExampleCo — Program Manager"),), kids={"p1": ()}, roster=[], records=[mail()])()
        self.assertEqual(got["evidence"], [])
        self.assertEqual(sorted(counts["lead_requests"].values()), [1])
        self.assertEqual((counts["requests"], counts["failed"]), (0, 0))
        self.assertTrue(all(len(label) == 6 for label in counts["lead_requests"]))                  # a short hash, never a name

    def test_a_pursuit_that_is_also_an_applied_row_is_one_job_not_two(self):
        counts, got = Run(parents=(("p1", "ExampleCo — Program Manager"),), kids={"p1": ()}, roster=[APPLIED], records=[mail()])()
        self.assertEqual((counts["lead_requests"], counts["requests"], counts["why"]), ({}, 1, {}))
        self.assertEqual(len(got["evidence"]), 1)


class Facts(unittest.TestCase):
    def regions(self, request_links, renders=1):
        run = lambda text, link=None: {"plain_text": text, "text": {"content": text, **({"link": {"url": link}} if link else {})}}
        callout = {"id": "k", "type": "callout", "callout": {"rich_text": [run(ev.REQUEST, link) for link in request_links]}}
        lines = [{"id": "m", "type": "paragraph", "paragraph": {"rich_text": [run(notion.MARKERS["opportunity"])]}}] + [
            {"id": f"r{i}", "type": "paragraph", "paragraph": {"rich_text": [run("v7-render:1;job=exampleco-r-1;source=0123456789abcdef;page=pending")]}} for i in range(renders)]
        return [callout], lines

    def read(self, request_links, renders=1):
        with patch.object(create, "machine_region", return_value=(*self.regions(request_links, renders), [])):
            return ev.facts(object(), "p1", None)

    def test_the_page_names_its_job_and_its_one_request_message(self):
        self.assertEqual(self.read([URL]), ("exampleco-r-1", URL))

    def test_no_request_link_two_different_ones_or_two_render_lines_are_not_guessed(self):
        self.assertEqual(self.read([]), ("exampleco-r-1", None))
        self.assertEqual(self.read([URL, OTHER]), ("exampleco-r-1", None))
        self.assertEqual(self.read([URL], renders=2), None)
        self.assertEqual(self.read(["http://example.test/x"]), ("exampleco-r-1", None))


class Registered(unittest.TestCase):
    def test_the_interview_stage_is_this_path_and_the_manual_ones_are_untouched(self):
        with patch.object(ev, "run", return_value={"failed": 0}) as run:
            self.assertEqual(STAGES["interview"](7, False), {"failed": 0})
        run.assert_called_once_with(7, False)
        self.assertEqual(STAGES["interview-acceptance"].target, ("lifeos.interview.acceptance", "run"))
        self.assertEqual(STAGES["interview-probe"].target, ("lifeos.interview.stage", "probe"))


if __name__ == "__main__":
    unittest.main()
