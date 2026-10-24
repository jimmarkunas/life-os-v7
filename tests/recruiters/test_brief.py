"""LIFE-35 (RECR-2.2), synthetic only: the recruiter brief is a read-only page built from the records on file when it opens. Each line comes from one record with
its own source link or a fixed state; nothing is stored, written, scored or sent. Unreadable is never shown as none."""
import unittest
from unittest import mock

from lifeos.edge import close_gate
from lifeos.jobs import hiring_pipeline
from lifeos.platform.notion_client import NotionError
from lifeos.recruiters import notion as store, outreach_card
from lifeos.sources import close_app, outreach_app as A
from tests.kit.recruiters import page

ROW, JOB, LEDGER, RECR, PIPE = "c" * 32, "a" * 32, "1" * 32, "2" * 32, "3" * 32
CONFIG = {"ledger": LEDGER, "pipeline": PIPE, "recruiters": RECR}
GMAIL = "https://mail.google.com/mail/u/0/#all/abc123"
rt = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}         # noqa: E731


class Reader:
    """Read calls only; any write fails the test."""
    def __init__(self, row, job=None, unreadable=()):
        self.pages = {ROW: {**row, "parent": {"type": "data_source_id", "data_source_id": RECR}}}
        if job:
            self.pages[JOB] = job
        self.unreadable, self.calls = set(unreadable), []

    def get_page(self, pid):
        self.calls.append(("GET", pid))
        if pid in self.unreadable or pid not in self.pages:
            raise NotionError("NOTION_HTTP_404")
        return self.pages[pid]

    def call(self, method, path, body=None):
        assert method == "GET", (method, path)
        self.calls.append((method, path))
        return {"results": [], "has_more": False}


def recruiter(**kw):
    p = page("Avery Synthetic", company="Example Staffing", role="Program Manager", client="Acme Grid", what="TPM contract, 6 months", action="Needs Action",
             follow_up="2026-10-27", url=GMAIL, pid=ROW, **kw)
    p["properties"]["First Contact"] = {"type": "date", "date": {"start": "2026-10-20T14:05:00+00:00"}}
    p["properties"]["Job"] = {"type": "relation", "relation": [{"id": JOB}]}
    return p


def ledger_job(fit="Strong fit: delivery ownership", parent=LEDGER):
    return {"id": JOB, "parent": {"type": "data_source_id", "data_source_id": parent}, "properties": {
        "Company": rt("Acme Grid"), "Job": {"type": "title", "title": [{"plain_text": "Technical Program Manager"}]}, "Why It Fits": rt(fit)}}


def brief(reader, opps=None, status="ok", leads=None, lead_error=False):
    row = store.read(reader.pages[ROW])
    with mock.patch.object(hiring_pipeline, "snapshot", return_value=(opps or [], status)), \
            mock.patch.object(A.N, "leads_on_page", side_effect=NotionError("X") if lead_error else (lambda c, p, n: leads or [])):
        return dict((label, (text, url)) for label, text, url in A.brief_lines(row, A.brief_facts(reader, CONFIG, row)))


OPP = hiring_pipeline.Opportunity(page_id="4" * 32, title="Acme Grid — Technical Program Manager", section="ACTIVE", rounds=2, company="Acme Grid", role="Technical Program Manager")


class Lines(unittest.TestCase):
    def test_a_linked_recruiter_brief_has_seven_lines_each_from_its_own_record(self):
        lines = brief(Reader(recruiter(), ledger_job()), [OPP], leads=[{"name": "Pat Example"}, {"name": "Sam Example"}])
        self.assertEqual(list(lines), ["Who", "Contact", "Role", "Fit", "Interview", "Network", "Status"])
        self.assertEqual(lines["Who"], ("Avery Synthetic · Example Staffing · Gmail", GMAIL))
        self.assertIn("first Oct 20, 2026, 9:05 AM CT", lines["Contact"][0])                     # Central Time, never UTC
        self.assertIn("TPM contract, 6 months", lines["Contact"][0])
        self.assertEqual(lines["Role"], ("Acme Grid · Technical Program Manager (Job Ledger)", f"https://www.notion.so/{JOB}"))
        self.assertEqual(lines["Fit"][0], "Strong fit: delivery ownership")
        self.assertEqual(lines["Interview"], ("Acme Grid — Technical Program Manager · 2 interview page(s) · Active", f"https://www.notion.so/{'4' * 32}"))
        self.assertEqual(lines["Network"][0], "Pat Example, Sam Example")
        self.assertEqual(lines["Status"], ("Needs Action · follow up Oct 27, 2026", f"https://www.notion.so/{ROW}"))
        for label, (text, url) in lines.items():
            self.assertTrue(text, label)
            self.assertTrue(url is None or url.startswith("https://"), label)

    def test_no_fit_never_blocks_the_brief_and_no_score_is_made(self):
        lines = brief(Reader(recruiter(), ledger_job(fit="")), [OPP])
        self.assertEqual(lines["Fit"], ("Fit not available", None))
        self.assertEqual(lines["Network"][0], "none on file")

    def test_no_job_two_jobs_or_a_page_outside_the_ledger_are_named_never_guessed(self):
        none = recruiter()
        none["properties"]["Job"] = {"type": "relation", "relation": []}
        self.assertIn("not linked to a Job Ledger job", brief(Reader(none))["Role"][0])
        two = recruiter()
        two["properties"]["Job"] = {"type": "relation", "relation": [{"id": JOB}, {"id": "b" * 32}]}
        lines = brief(Reader(two, ledger_job()), [OPP])
        self.assertIn("more than one match", lines["Role"][0])
        self.assertEqual(lines["Interview"], ("more than one match: not shown", None))
        elsewhere = brief(Reader(recruiter(), ledger_job(parent="9" * 32)), [OPP])
        self.assertIn("unreadable this time", elsewhere["Role"][0])

    def test_an_unreadable_source_is_unreadable_never_none(self):
        self.assertEqual(brief(Reader(recruiter(), ledger_job()), status="unreadable")["Interview"], ("unreadable this time", None))
        self.assertEqual(brief(Reader(recruiter(), ledger_job()), [OPP], lead_error=True)["Network"], ("unreadable this time", None))
        self.assertIn("unreadable this time", brief(Reader(recruiter(), unreadable=[JOB]))["Fit"][0])

    def test_the_interview_page_is_the_one_for_the_same_job_and_two_are_never_picked_between(self):
        other = hiring_pipeline.Opportunity(page_id="5" * 32, title="Other Co — Analyst", section="ACTIVE", company="Other Co", role="Analyst")
        self.assertEqual(brief(Reader(recruiter(), ledger_job()), [other])["Interview"], ("none on file", None))
        self.assertEqual(brief(Reader(recruiter(), ledger_job()), [OPP, OPP])["Interview"][0], "more than one match: not shown")

    def test_the_same_records_render_the_same_brief(self):
        args = (Reader(recruiter(), ledger_job()), [OPP])
        self.assertEqual(brief(*args), brief(*args))

    def test_the_channel_names_are_the_outreach_cards(self):
        self.assertEqual(A.CHANNELS, outreach_card.CHANNELS)


class Screen(unittest.TestCase):
    def call(self, reader, method="GET", query=None):
        req = {"method": method, "path": "/brief", "query": query if query is not None else {"r": ROW}, "headers": {}, "body": b"", "form": {}}
        secrets = {"link": b"k" * 32, "config": CONFIG}
        if method == "GET":
            secrets["reader"] = close_gate.ReadOnly(reader)
        with mock.patch.object(hiring_pipeline, "snapshot", return_value=([OPP], "ok")), mock.patch.object(A.N, "leads_on_page", return_value=[]):
            status, _, body, _ = close_app.handle(req, {"login": "jim@example.com", "node": "n"}, secrets)
        return status, body.decode()

    def test_get_renders_the_brief_and_writes_nothing(self):
        reader = Reader(recruiter(), ledger_job())
        reader.source = RECR
        status, body = self.call(reader)
        self.assertEqual(status, 200, body)
        self.assertIn("Recruiter brief", body)
        self.assertIn("Nothing is stored, written or sent.", body)
        self.assertTrue(all(method == "GET" for method, _ in reader.calls))

    def test_post_a_missing_link_or_a_row_elsewhere_are_refused(self):
        reader = Reader(recruiter(), ledger_job())
        reader.source = RECR
        self.assertEqual(self.call(reader, "POST")[0], 405)
        self.assertEqual(self.call(reader, query={})[0], 400)
        reader.pages[ROW]["parent"]["data_source_id"] = "9" * 32
        status, body = self.call(reader)
        self.assertEqual(status, 409)
        self.assertIn("RECRUITERS_ROW_NOT_IN_SOURCE", body)

    def test_hostile_text_is_escaped(self):
        row = recruiter()
        row["properties"]["What they want"] = rt("<script>alert(1)</script>")
        reader = Reader(row, ledger_job())
        reader.source = RECR
        body = self.call(reader)[1]
        self.assertNotIn("<script>alert", body)
        self.assertIn("&lt;script&gt;", body)


if __name__ == "__main__":
    unittest.main()
