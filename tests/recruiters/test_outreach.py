"""LIFE-21 / LIFE-47 (D221), synthetic only: Mark as Sent on the private Close service -> one canonical Waiting outreach row linked to the exact Job Ledger job ->
the two-business-day follow-up -> one native Job Outreach row. Every name, id and date is invented; nothing is live."""
import html
import json
import re
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from lifeos.recruiters import notion as store, outreach as O, stage
from lifeos.sources import close_app, outreach_app
from tests.kit.recruiters import FakeRecruiters, mail, page

LEDGER, PIPELINE, RECRUITERS = "1" * 32, "2" * 32, "3" * 32
JOB_A, JOB_B = "a" * 32, "b" * 32
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=ZoneInfo("America/Chicago"))      # a Thursday
SECRET, LOGIN = b"link-signing-key-0123456789abcdef", "jim@example.com"
WHO = {"login": LOGIN, "node": "node-1"}
SCHEMA = {"Job": {"type": "relation", "relation": {"data_source_id": LEDGER}}}


def ledger_page(pid, company, role, parent=LEDGER, archived=False, decision=None):
    return {"id": pid, "archived": archived, "parent": {"type": "data_source_id", "data_source_id": parent},
            "properties": {"Company": {"type": "rich_text", "rich_text": [{"plain_text": company}]}, "Job": {"type": "title", "title": [{"plain_text": role}]},
                           "Hiring Decision": {"type": "select", "select": {"name": decision} if decision else None}}}


class Ledger:
    def __init__(self, *pages):
        self.pages, self.source, self.writes = {p["id"]: p for p in pages}, LEDGER, []

    def get_page(self, pid):
        if pid not in self.pages:
            raise store.NotionError("NOTION_HTTP_404")
        return self.pages[pid]

    def call(self, method, path, body=None):
        self.writes.append((method, path))
        raise AssertionError("the Ledger is never written")


class Service:
    """Drives the gate's application the way close_gate does: GET gets a read-only reader; confirm builds clients from the token, patched to the fakes."""

    def __init__(self, ledger, recruiters, config=None):
        self.ledger, self.recruiters, self.clock = ledger, recruiters, NOW
        self.config = config or {"ledger": LEDGER, "pipeline": PIPELINE, "recruiters": RECRUITERS}

    def _client(self, environ, source_name="NOTION_JOB_LEDGER_DATA_SOURCE_ID", **kw):
        if source_name == "NOTION_RECRUITERS_DATA_SOURCE_ID":
            if not environ.get(source_name):
                raise store.NotionError("NOTION_CONFIG_MISSING")
            return self.recruiters
        return self.ledger

    def __call__(self, method, path, query=None, form=None):
        secrets = {"link": SECRET, "config": self.config}
        secrets.update({"reader": self.ledger} if method == "GET" else {"notion": b"token"})
        with mock.patch.object(outreach_app, "Client", self._client), mock.patch.object(close_app, "_now", lambda: self.clock), \
                mock.patch.object(outreach_app, "_now", lambda: self.clock):
            return close_app.handle({"method": method, "path": path, "query": query or {}, "form": form or {}}, WHO, secrets)


def fields(body):
    return {k: html.unescape(v) for k, v in re.findall(r'name="([^"]+)" value="([^"]*)"', body.decode())}


def mark(svc, job_id, person):
    status, _, body, _ = svc("GET", "/sent", {"j": job_id})
    assert status == 200, body
    review = svc("POST", "/sent", form={**fields(body), "person": person, "step": "review"})
    assert review[0] == 200, review[2]
    return svc("POST", "/sent", form={**fields(review[2]), "step": "confirm"})


class MarkAsSent(unittest.TestCase):
    def world(self, **kw):
        return Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), ledger_page(JOB_B, "Northwind Labs", "Technical Program Manager")), \
            FakeRecruiters(schema=SCHEMA, **kw)

    def test_confirm_records_one_waiting_row_with_a_two_business_day_follow_up(self):
        ledger, rec = self.world()
        status, _, body, _ = mark(Service(ledger, rec), JOB_A, "Avery Synthetic")
        self.assertEqual(status, 200, body)
        self.assertIn(b"Marked as sent", body)
        [row] = [store.read(p) for p in rec.pages]
        self.assertEqual((row["person"], row["client"], row["role"], row["medium"], row["action"], row["follow_up"], row["jobs"], row["done"], row["active"]),
                         ("Avery Synthetic", "Northwind Labs", "Senior Technical Program Manager", "LinkedIn", "Waiting", "2026-10-19", [JOB_A], False, True))
        self.assertEqual(ledger.writes, [])

    def test_replay_is_already_recorded_and_never_a_duplicate(self):
        ledger, rec = self.world()
        svc = Service(ledger, rec)
        mark(svc, JOB_A, "Avery Synthetic")
        status, _, body, _ = mark(svc, JOB_A, " avery  synthetic ")
        self.assertEqual((status, len(rec.pages), len([w for w in rec.writes if w[0] == "create"])), (200, 1, 1))
        self.assertIn(b"Already marked as sent", body)
        self.assertIn(b"currently Waiting", body)

    def test_distinct_jobs_and_people_are_distinct_conversations(self):
        ledger, rec = self.world()
        svc = Service(ledger, rec)
        for job_id, person in ((JOB_A, "Avery Synthetic"), (JOB_B, "Avery Synthetic"), (JOB_A, "Blake Synthetic")):
            self.assertEqual(mark(svc, job_id, person)[0], 200)
        self.assertEqual(sorted((store.read(p)["jobs"][0], store.read(p)["person"]) for p in rec.pages),
                         [(JOB_A, "Avery Synthetic"), (JOB_A, "Blake Synthetic"), (JOB_B, "Avery Synthetic")])

    def test_get_and_review_never_write(self):
        ledger, rec = self.world()
        svc = Service(ledger, rec)
        status, _, body, _ = svc("GET", "/sent", {"j": JOB_A})
        svc("POST", "/sent", form={**fields(body), "person": "Avery Synthetic", "step": "review"})
        self.assertEqual((status, rec.writes, rec.queries, ledger.writes), (200, [], 0, []))

    def test_refusals_write_nothing(self):
        ledger, rec = self.world()
        svc = Service(ledger, rec)
        status, _, body, _ = svc("GET", "/sent", {"j": JOB_A})
        good = fields(body)
        for bad in ({**good, "t": "0" * 64}, {**good, "ro": "Another Role"}, {**good, "exp": "1"}):
            self.assertEqual(svc("POST", "/sent", form={**bad, "person": "Avery Synthetic", "step": "review"})[0], 403)
        review = svc("POST", "/sent", form={**good, "person": "Avery Synthetic", "step": "review"})
        self.assertEqual(svc("POST", "/sent", form={**fields(review[2]), "person": "Someone Else", "step": "confirm"})[0], 403)      # the confirmation signs the person
        self.assertEqual(svc("POST", "/sent", form={**good, "person": " ", "step": "review"})[0], 400)
        self.assertEqual(svc("GET", "/sent", {"j": "c" * 32})[0], 409)                                                             # not a Ledger job
        self.assertEqual(rec.writes, [])

    def test_a_job_that_is_not_in_the_ledger_is_refused(self):
        ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager", parent="9" * 32))
        self.assertEqual(Service(ledger, FakeRecruiters(schema=SCHEMA))("GET", "/sent", {"j": JOB_A})[0], 409)

    def test_without_the_job_relation_or_config_nothing_is_written(self):
        ledger, _ = self.world()
        rec = FakeRecruiters()                                                   # the additive Job relation is not there yet
        status, _, body, _ = mark(Service(ledger, rec), JOB_A, "Avery Synthetic")
        self.assertEqual((status, rec.writes), (409, []))
        self.assertIn(b"OUTREACH_SCHEMA_MISSING", body)
        rec = FakeRecruiters(schema=SCHEMA)
        status, _, body, _ = mark(Service(ledger, rec, {"ledger": LEDGER, "pipeline": PIPELINE}), JOB_A, "Avery Synthetic")
        self.assertEqual((status, rec.writes), (409, []))
        wrong = FakeRecruiters(schema={"Job": {"type": "relation", "relation": {"data_source_id": "9" * 32}}})
        self.assertEqual((mark(Service(ledger, wrong), JOB_A, "Avery Synthetic")[0], wrong.writes), (409, []))

    def test_replay_reports_the_persisted_state_and_never_relabels_or_writes(self):
        for kind, action, extra, code, words in (("due", "Needs Action", {}, 200, b"currently Needs Action"),
                                                 ("completed", "Completed", {}, 409, b"already closed"),
                                                 ("dismissed", "Dismissed", {}, 409, b"already closed"),
                                                 ("done", "Waiting", {"done": True}, 409, b"already closed"),
                                                 ("inactive", "Waiting", {"active": False}, 409, b"already closed")):
            with self.subTest(kind=kind):
                ledger, rec = self.world()
                svc = Service(ledger, rec)
                mark(svc, JOB_A, "Avery Synthetic")
                target = rec.pages[0]["properties"]
                target["Action Status"] = {"type": "select", "select": {"name": action}}
                for name, value in extra.items():
                    target[{"done": "Done", "active": "Active"}[name]] = {"type": "checkbox", "checkbox": value}
                prior, writes = store.read(rec.pages[0]), len(rec.writes)
                status, _, body, _ = mark(svc, JOB_A, "Avery Synthetic")
                self.assertEqual((status, len(rec.writes), len(rec.pages)), (code, writes, 1), body)
                self.assertIn(words, body)
                self.assertNotIn(b"Waiting \xc2\xb7 follow-up", body)
                self.assertEqual(store.read(rec.pages[0]), prior)

    def test_a_closed_or_unknown_ledger_job_is_refused_at_open_and_at_confirm(self):
        for decision in ("Rejected", "Withdrew", "Hired"):
            with self.subTest(decision=decision):
                ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager", decision=decision))
                rec = FakeRecruiters(schema=SCHEMA)
                status, _, body, _ = Service(ledger, rec)("GET", "/sent", {"j": JOB_A})
                self.assertEqual((status, rec.writes), (409, []))
                self.assertIn(b"JOB_CLOSED", body)
        ledger, rec = self.world()                                      # open when the form is shown, closed before Confirm: the fresh read refuses
        svc = Service(ledger, rec)
        status, _, body, _ = svc("GET", "/sent", {"j": JOB_A})
        review = svc("POST", "/sent", form={**fields(body), "person": "Avery Synthetic", "step": "review"})
        ledger.pages[JOB_A]["properties"]["Hiring Decision"]["select"] = {"name": "Position filled"}
        status, _, body, _ = svc("POST", "/sent", form={**fields(review[2]), "step": "confirm"})
        self.assertEqual((status, rec.writes), (409, []))
        self.assertIn(b"JOB_CLOSED", body)
        unknown = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"))
        del unknown.pages[JOB_A]["properties"]["Hiring Decision"]
        self.assertEqual(Service(unknown, FakeRecruiters(schema=SCHEMA))("GET", "/sent", {"j": JOB_A})[0], 409)

    def test_hostile_text_is_escaped(self):
        ledger = Ledger(ledger_page(JOB_A, "<script>x</script> Co", "<b>Role</b>"))
        svc = Service(ledger, FakeRecruiters(schema=SCHEMA))
        status, _, body, _ = svc("GET", "/sent", {"j": JOB_A})
        review = svc("POST", "/sent", form={**fields(body), "person": "<img src=x>", "step": "review"})
        for page_body in (body, review[2]):
            self.assertNotIn(b"<script>x", page_body)
            self.assertNotIn(b"<img src=x>", page_body)
            self.assertNotIn(b"<b>Role", page_body)

    def test_a_lost_create_is_degraded_not_success(self):
        ledger, rec = self.world(drop_writes=True)
        status, _, body, _ = mark(Service(ledger, rec), JOB_A, "Avery Synthetic")
        self.assertEqual(status, 500)
        self.assertIn(b"DEGRADED", body)


class Lifecycle(unittest.TestCase):
    """The same canonical row through the existing recruiters stage (D220) and into the native Job Outreach projection."""

    def setUp(self):
        self.ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), ledger_page(JOB_B, "Northwind Labs", "Technical Program Manager"))
        self.rec = FakeRecruiters(schema=SCHEMA)
        mark(Service(self.ledger, self.rec), JOB_A, "Avery Synthetic")
        self.jobs = {JOB_A: {"company": "Northwind Labs", "role": "Senior Technical Program Manager", "url": "https://app.notion.com/p/" + JOB_A},
                     JOB_B: {"company": "Northwind Labs", "role": "Technical Program Manager", "url": "https://app.notion.com/p/" + JOB_B}}

    def run_stage(self, when, records=()):
        from tests.recruiters.test_recruiters import ReadOnlyGmail
        return stage.run(10, True, gmail=ReadOnlyGmail(list(records)), outlook_accounts=[], notion=self.rec, now=when)

    def rows(self):
        return [store.read(p) for p in self.rec.pages]

    def test_projection_is_one_row_per_exact_job_with_the_follow_up(self):
        mark(Service(self.ledger, self.rec), JOB_A, "Blake Synthetic")
        out = O.project(self.rows(), self.jobs, NOW.date())
        self.assertEqual(out, [{"job": "Northwind Labs — Senior Technical Program Manager", "outreach": "2 contacted · replies not tracked · referrals not tracked",
                                "next": "Oct 19", "link": self.jobs[JOB_A]["url"], "sort": "2026-10-19", "contacted": 2}])
        self.assertEqual(O.project(self.rows(), {}, NOW.date()), [])                       # a job that is no longer open is not shown

    def test_the_follow_up_is_kept_then_returns_to_needs_action_when_due(self):
        before = self.run_stage(datetime(2026, 10, 16, 17, 0, tzinfo=ZoneInfo("UTC")))     # Friday: Waiting, date already set
        self.assertEqual((before["waiting"], before["scheduled"], before["reopened_due"], self.rows()[0]["action"]), (1, 0, 0, "Waiting"))
        due = self.run_stage(datetime(2026, 10, 19, 17, 0, tzinfo=ZoneInfo("UTC")))        # Monday: the checkpoint
        [row] = self.rows()
        self.assertEqual((due["reopened_due"], row["action"], row["follow_up"], row["jobs"]), (1, "Needs Action", "2026-10-19", [JOB_A]))
        out = O.project(self.rows(), self.jobs, datetime(2026, 10, 19).date())
        self.assertEqual((out[0]["outreach"], out[0]["next"]), ("1 contacted · replies not tracked · referrals not tracked", "Needs action · Today"))

    def test_inbound_recruiter_mail_never_merges_into_an_outreach_row(self):
        body = "Hi,\n\nI'm reaching out about a Senior Technical Program Manager role.\n\nBest,\nAvery Synthetic\nSenior Recruiter at Northwind Labs\n"
        counts = self.run_stage(datetime(2026, 10, 16, 17, 0, tzinfo=ZoneInfo("UTC")),
                                [mail(sender_name="Avery Synthetic", body=body, received="2026-10-16T15:00:00+00:00")])
        outreach = [r for r in self.rows() if r["jobs"]]
        self.assertEqual((counts["created"], counts["updated"], len(outreach), outreach[0]["action"], outreach[0]["last_contact"][:10]), (1, 0, 1, "Waiting", "2026-10-15"))

    def test_counts_and_codes_carry_no_private_value(self):
        counts = self.run_stage(datetime(2026, 10, 19, 17, 0, tzinfo=ZoneInfo("UTC")))
        self.assertNotIn("Avery", json.dumps(counts))
        self.assertNotIn("Northwind", json.dumps(counts))


if __name__ == "__main__":
    unittest.main()
