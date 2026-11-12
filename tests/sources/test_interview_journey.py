"""LIFE-78 synthetic journey: the REAL Interview OS runner (`interview_evidence.run`) and the REAL Notion writer (`stage` / `create`), tick after tick,
against the production-shaped Notion fake. Only the outside world is faked: the mailbox, Google Calendar and the Job Ledger. Everything is invented
(ExampleCo, requisition 777777, example.test links). This proves the machine; it is not M1/M2 evidence and never stands in for a real interview."""
import unittest
from datetime import datetime
from unittest.mock import patch
from lifeos.interview import create, notion
from lifeos.jobs import ledger as jobs_ledger
from lifeos.platform.notion_client import NotionError
from lifeos.sources.interview import interview_evidence as ev
from tests.interview.support import WritableFake
from tests.interview.support import ENV
from tests.sources.test_interview_evidence import event, mail, outreach
from tests.sources.support import INVITE, KEY, LEAD, NO_REQ, REQ, REQUEST
from tests.sources.support import JourneyFixture, LIVE, Ledger, NOW, NotionLike





class Recruiters:
    """The Recruiters data source, read only: rows filtered by an exactly equal Source URL, as Notion answers."""
    def __init__(self, rows=(), broken=False):
        self.rows, self.broken, self.queries = list(rows), broken, []

    def query_data_source(self, source, body):
        if self.broken:
            raise NotionError("NOTION_HTTP_503")
        url = body["filter"]["url"]["equals"]
        self.queries.append(url)
        text = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}
        return {"results": [{"id": f"rec-{i}", "properties": {"Recruiter / Person": text(person), "Recruiter Company": text(company), "Source URL": {"type": "url", "url": link}}}
                            for i, (person, company, link) in enumerate(self.rows) if link == url], "has_more": False}






class Journey(JourneyFixture, unittest.TestCase):

    def test_recruiter_lead_to_interview_scheduled_rescheduled_cancelled_on_one_page(self):
        # 1. A recruiter names the job and the employer's requisition: one Ledger row, one opportunity page. A second recruiter gives no requisition: REVIEW, nothing written.
        first = self.tick(LEAD, NO_REQ)
        self.assertEqual((first["lead_rows"], first["parents_created"], first["lead_review"]), (1, 1, {"requisition_missing": 1}))
        (row,) = self.ledger.rows.values()
        self.assertEqual((row["stable_job_key"], row["lane"]), (KEY, "Recruiter"))
        self.settle(LEAD, NO_REQ)
        (page, kids), = self.pages()
        self.assertEqual((page.page_id, page.title, kids), ("ledger-1", "ExampleCo - Program Manager", ()))      # the lead's own row, no second one
        notes = notion.protected_snapshot(self.notion, page.page_id, "opportunity")

        # 2. Replay of the same mail: nothing new anywhere.
        again = self.tick(LEAD, NO_REQ)
        self.assertEqual((again["writes"], again["lead_rows"], len(self.ledger.rows), len(self.pages())), (0, 0, 1, 1))

        # 3. The employer asks for availability (no time yet): Interview 1, Scheduling Pending, under the same page and linked to the same Ledger row.
        self.settle(LEAD, NO_REQ, REQUEST)
        (page, kids), = self.pages()
        (round_,) = kids
        self.assertEqual((round_.interview_date, round_.ordinal, round_.identity_valid), (None, 1, True))
        self.assertEqual(self.tick(LEAD, NO_REQ, REQUEST)["writes"], 0)              # replay: still one round

        # 4. Jim accepts the Calendar invite: that same round page is dated in place and records the event.
        self.events.append(event(INVITE, start="2026-10-28T10:00:00-05:00"))
        ticks = self.settle(LEAD, NO_REQ, REQUEST)
        self.assertEqual(ticks[0]["why"].get("round_confirmed"), 1)
        (page, (round_,)), = self.pages()
        self.assertEqual((round_.page_id, round_.interview_date), (kids[0].page_id, "2026-10-28"))
        self.assertEqual(create.round_calendar(self.notion, round_.page_id), (ev.reference(INVITE), False))
        self.settle(LEAD, NO_REQ, REQUEST)                                            # E10: the opportunity reads the round's recorded invite
        opportunity = self.shown(page.page_id)
        self.assertIn("2026-10-28 · accepted Calendar invite", opportunity)
        self.assertIn("from the accepted Calendar invite", opportunity)

        # 5. The recruiter moves the same invite a week: the same page is re-dated, no second round.
        self.events[0] = event(INVITE, start="2026-11-04T10:00:00-05:00")
        ticks = self.settle(LEAD, NO_REQ, REQUEST)
        self.assertEqual(ticks[0]["why"].get("round_rescheduled"), 1)
        (page, (round_,)), = self.pages()
        self.assertEqual((round_.page_id, round_.interview_date), (kids[0].page_id, "2026-11-04"))

        # 6. The invite is cancelled: the same page says Cancelled, keeps its date and identity; replay writes nothing.
        self.events[0] = {**self.events[0], "status": "cancelled"}
        ticks = self.settle(LEAD, NO_REQ, REQUEST)
        self.assertEqual(ticks[0]["why"].get("round_cancelled"), 1)
        (page, (round_,)), = self.pages()
        self.assertEqual((round_.page_id, round_.interview_date, round_.identity_valid), (kids[0].page_id, "2026-11-04", True))
        self.assertEqual(create.round_calendar(self.notion, round_.page_id), (ev.reference(INVITE), True))
        opportunity = self.shown(page.page_id)                                       # the opportunity no longer says scheduled or Prepare
        self.assertIn("Interview 1 cancelled in Calendar", opportunity)
        self.assertIn("Ask the recruiter whether Interview 1 moves to a new time.", opportunity)
        self.assertNotIn("Prepare for Interview 1", opportunity)

        # Across the whole journey: one Ledger row, one opportunity, one round, Jim's notes on the opportunity and the human page never touched.
        self.assertEqual((len(self.ledger.rows), len(self.pages()), len(self.pages()[0][1])), (1, 1, 1))
        self.assertEqual(notion.protected_snapshot(self.notion, page.page_id, "opportunity"), notes)
        self.assertEqual(self.notion.blocks["human"], self.human)

    def test_e10_the_recruiter_of_the_exact_request_message_is_linked_and_a_same_name_row_elsewhere_is_not(self):
        """LIFE-78 E10: the Recruiters row whose Source URL IS the request message reaches the opportunity page; a row for the same person on another
        message never does (no name matching), and an unreadable Recruiters source is DEGRADED on the page and in the run, never "no recruiter"."""
        self.recruiters = Recruiters([("Pat Example", "Staffing Example Inc", LEAD["url"]),
                                      ("Pat Example", "Other Staffing", "https://example.test/thread/elsewhere")])
        self.settle(LEAD, NO_REQ)
        (page, _), = self.pages()
        shown = self.shown(page.page_id)
        self.assertIn("Recruiter · Staffing Example Inc", shown)
        self.assertIn("1 thread(s) linked from the Recruiters database", shown)
        self.assertNotIn("Other Staffing", shown)
        self.assertNotIn("https://example.test/thread/elsewhere", self.recruiters.queries)          # only the opportunities' own request links are asked for
        self.assertEqual(self.tick(LEAD, NO_REQ)["writes"], 0)                                       # replay: nothing changes

    def test_e10_an_unreadable_recruiters_source_is_degraded_never_no_recruiter(self):
        self.recruiters = Recruiters(broken=True)
        first = self.tick(LEAD, NO_REQ)
        self.assertEqual(first["why"].get("recruiters_unreadable"), 1)
        self.assertGreater(first["failed"], 0)
        self.settle_until_quiet(LEAD, NO_REQ)
        (page, _), = self.pages()
        shown = self.shown(page.page_id)
        self.assertIn("Recruiter email threads: DEGRADED · Recruiters source unreadable this run", shown)
        self.assertNotIn("No verified recruiters or coordinators", shown)

    def test_e10_without_the_recruiters_token_in_this_step_nothing_is_asked_and_the_page_says_not_connected(self):
        evidence = [(None, ev.RoundQuery(True, None, None, 1, source=LEAD["url"]))]
        self.assertEqual(ev.recruiter_threads({}, evidence), (evidence, False))
        self.settle(LEAD, NO_REQ)
        (page, _), = self.pages()
        self.assertIn("Recruiter email threads: not connected to LIFE OS yet", self.shown(page.page_id))

    def settle_until_quiet(self, *records):
        for _ in range(6):
            if not self.tick(*records)["writes"]:
                return

    def test_a_request_naming_another_requisition_for_the_same_job_creates_nothing(self):
        self.settle(LEAD)
        wrong = {**REQUEST, "body": "Please select a time for a call. Requisition 999999.", "url": "https://example.test/thread/wrong"}
        counts = self.tick(LEAD, wrong)
        (page, kids), = self.pages()
        self.assertEqual((kids, counts["writes"]), ((), 0))
        self.assertGreater(counts["failed"], 0)                                       # visible, never a quiet zero


if __name__ == "__main__":
    unittest.main()
