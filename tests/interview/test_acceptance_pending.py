"""D231: the manual acceptance stage's Scheduling Pending case reads the exact Job Ledger rows with the Interview token, then hands
the same progression to the existing writer. Everything is invented; nothing real is reached."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from lifeos.interview import acceptance, notion
from lifeos.platform.notion_client import NotionError
from tests.interview.test_notion import ENV
from tests.interview.test_scheduling_pending import COMPANY, ROLE, SOURCE, opportunity

KEY = "exampleco-stpm-ex1234"
CASE = {"company": COMPANY, "role": ROLE, "confirmed_round": True, "interview_date": None, "interviewer": None, "ordinal": 1,
        "source": SOURCE, "requisition": None, "stable_job_key": KEY}
ROWS = [{"page_id": "L1", "company": COMPANY, "role": ROLE, "stable_job_key": KEY, "requisition": None}]


def environ(case=CASE):
    return {**ENV, "GITHUB_EVENT_NAME": "workflow_dispatch", "INTERVIEW_ACCEPTANCE_JSON": json.dumps(case)}


def ledger_page(company, job, key, page_id="p1", **extra):
    rich = lambda t: [{"plain_text": t}]   # noqa: E731
    return {"id": page_id, "properties": {"Company": {"rich_text": rich(company)}, "Job": {"title": rich(job)},
                                          "Stable Job Key": {"rich_text": rich(key)}}, **extra}


class LedgerClient:
    def __init__(self, pages, environ=None, token_name=None, source_name=None):
        self.pages, self.bodies, self.made = pages, [], (token_name, source_name)

    def query_data_source(self, source_id=None, body=None):
        self.bodies.append(body)
        return self.pages[len(self.bodies) - 1]


class PendingCase(unittest.TestCase):
    def test_the_case_creates_one_interview_1_through_the_existing_writer_and_replays_to_nothing(self):
        client = opportunity()
        first = acceptance.run(1, True, environ=environ(), read_rows=lambda company: ROWS, client=client)
        self.assertEqual((first["rounds_created"], first["writes"], first["readback_ok"], first["ledger"]), (1, 1, 1, "linked"))
        self.assertEqual(client.posts[0]["properties"]["title"]["title"][0]["text"]["content"], "Interview 1")
        again = acceptance.run(1, True, environ=environ(), read_rows=lambda company: ROWS, client=client)
        self.assertEqual((again["writes"], again["why"].get("child_match")), (0, 1))
        for private in (COMPANY, ROLE, KEY, SOURCE):
            self.assertNotIn(private, json.dumps(first) + json.dumps(again))

    def test_a_dry_run_plans_one_write_and_writes_nothing(self):
        client = opportunity()
        result = acceptance.run(1, False, environ=environ(), read_rows=lambda company: ROWS, client=client)
        self.assertEqual((result["writes_planned"], result["writes"]), (1, 0))
        self.assertEqual(client.posts, [])

    def test_refusals_carry_the_fixed_progression_code_and_never_reach_the_writer(self):
        cases = (({**CASE, "stable_job_key": "other-key"}, ROWS, "ledger_unverified"),
                 ({**CASE, "stable_job_key": None}, ROWS, "ledger_unverified"),
                 (CASE, [dict(ROWS[0], stable_job_key="")], "ledger_unverified"),
                 (CASE, ROWS + [dict(ROWS[0], page_id="L2")], "requisition_ambiguous"),
                 ({**CASE, "source": None}, ROWS, "request_source_missing"),
                 ({**CASE, "source": "http://mail.example/1"}, ROWS, "request_source_missing"))
        for case, rows, code in cases:
            with self.subTest(code=code, case=case):
                with patch.object(acceptance.stage, "run", side_effect=AssertionError("must not delegate")):
                    result = acceptance.run(1, True, environ=environ(case), read_rows=lambda company: rows)
                self.assertEqual((result["writes"], result["why"]), (0, {code: 1}))

    def test_wrong_shapes_are_invalid(self):
        for case in ({**CASE, "interview_date": "2026-10-22"}, {**CASE, "ordinal": 2}, {**CASE, "ordinal": True}, {**CASE, "source": "  "},
                     {**CASE, "stable_job_key": 7}, {**CASE, "confirmed_round": False}, {**CASE, "company": " "},
                     {k: v for k, v in CASE.items() if k != "source"}, {**CASE, "extra": 1}):
            with self.subTest(case=case):
                with patch.object(acceptance.stage, "run", side_effect=AssertionError("must not delegate")):
                    result = acceptance.run(1, True, environ=environ(case), read_rows=lambda company: ROWS)
                self.assertEqual((result["writes"], result["why"]), (0, {"acceptance_secret_invalid": 1}))

    def test_an_unreadable_ledger_writes_nothing(self):
        def broken(company):
            raise NotionError("NOTION_HTTP_404")
        with patch.object(acceptance.stage, "run", side_effect=AssertionError("must not delegate")):
            result = acceptance.run(1, True, environ=environ(), read_rows=broken)
            missing = acceptance.run(1, True, environ=environ())          # no Ledger id configured: the real reader refuses
        self.assertEqual((result["writes"], result["why"]), (0, {"ledger_unreadable": 1}))
        self.assertEqual((missing["writes"], missing["why"]), (0, {"ledger_unreadable": 1}))


class LedgerRead(unittest.TestCase):
    def read(self, pages):
        made = []
        def factory(environ, token_name, source_name):
            made.append(LedgerClient(pages, environ, token_name, source_name))
            return made[-1]
        with patch.object(acceptance, "Client", side_effect=factory):
            return acceptance.ledger_rows("ExampleCo, Inc.", {"NOTION_INTERVIEW_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "s"}), made[0]

    def test_reads_every_page_with_the_interview_token_and_skips_trashed_rows(self):
        pages = [{"results": [ledger_page(COMPANY, ROLE, KEY)], "has_more": True, "next_cursor": "c1"},
                 {"results": [ledger_page(COMPANY, "Data Engineer", "k2", "p2"), ledger_page(COMPANY, ROLE, "k3", "p3", in_trash=True)], "has_more": False}]
        rows, client = self.read(pages)
        self.assertEqual(client.made, ("NOTION_INTERVIEW_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID"))
        self.assertEqual(rows, [{"page_id": "p1", "company": COMPANY, "role": ROLE, "stable_job_key": KEY, "requisition": None},
                                {"page_id": "p2", "company": COMPANY, "role": "Data Engineer", "stable_job_key": "k2", "requisition": None}])
        self.assertEqual(client.bodies[0]["filter"], {"property": "Company", "rich_text": {"contains": "exampleco"}})
        self.assertEqual(client.bodies[1]["start_cursor"], "c1")

    def test_an_incomplete_read_is_refused_never_partial(self):
        for pages in ([{"results": [], "has_more": True, "next_cursor": None}],
                      [{"results": [], "has_more": True, "next_cursor": "c"}, {"results": [], "has_more": True, "next_cursor": "c"}],
                      [{"results": "x", "has_more": False}],
                      [{"results": [], "has_more": True, "next_cursor": f"c{n}"} for n in range(acceptance.LEDGER_PAGES)]):
            with self.subTest(pages=len(pages)), self.assertRaises(NotionError):
                self.read(pages)


class WorkflowStep(unittest.TestCase):
    def test_only_the_two_acceptance_steps_get_the_ledger_id_and_the_negative_case_never_runs_live(self):
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text()
        job = text[text.index("\n  interview:\n"):text.index("\n  report:\n")]
        self.assertIn('      NOTION_JOB_LEDGER_DATA_SOURCE_ID: ""', job)                       # blank for the whole job
        self.assertEqual(job.count("NOTION_JOB_LEDGER_DATA_SOURCE_ID: ${{ secrets.NOTION_JOB_LEDGER_DATA_SOURCE_ID }}"), 2)
        main = job[job.index("id: iacc\n"):job.index("id: iaccneg")]
        negative = job[job.index("id: iaccneg"):job.index("Interview warning")]
        for step in (main, negative):
            self.assertIn("NOTION_JOB_LEDGER_DATA_SOURCE_ID: ${{ secrets.NOTION_JOB_LEDGER_DATA_SOURCE_ID }}", step)
            self.assertNotIn("NOTION_API_TOKEN", step)
        self.assertIn("INTERVIEW_ACCEPTANCE_JSON: ${{ secrets.INTERVIEW_ACCEPTANCE_NEGATIVE_JSON }}", negative)
        self.assertNotIn("--live", negative)                                                    # D232: the negative case can never write
        self.assertNotIn("$LIVE", negative)


if __name__ == "__main__":
    unittest.main()
