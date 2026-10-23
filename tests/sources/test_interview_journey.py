"""LIFE-78 synthetic journey: the REAL Interview OS runner (`interview_evidence.run`) and the REAL Notion writer (`stage` / `create`), tick after tick,
against the production-shaped Notion fake. Only the outside world is faked: the mailbox, Google Calendar and the Job Ledger. Everything is invented
(ExampleCo, requisition 777777, example.test links). This proves the machine; it is not M1/M2 evidence and never stands in for a real interview."""
import unittest
from datetime import datetime
from unittest.mock import patch
from lifeos.interview import create, notion
from lifeos.jobs import ledger as jobs_ledger
from lifeos.sources import interview_evidence as ev
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV
from tests.sources.test_interview_evidence import event, mail, outreach

LIVE = {**ENV, "INTERVIEW_OS_WRITER": "v7", "INTERVIEW_LEADS": "on"}
NOW = datetime(2026, 10, 21, 12, tzinfo=ev.TZ)
REQ, KEY = "777777", "recruiter-exampleco-777777"
LEAD = outreach(url="https://example.test/thread/lead", received="2026-10-19T15:00:00Z", extra=f"Requisition {REQ}.")
REQUEST = mail(subject="Schedule your interview - ExampleCo Program Manager", body=f"Please select a time for a call. Requisition {REQ}.",
               url="https://example.test/thread/request", received="2026-10-20T15:00:00Z")
NO_REQ = outreach(role="Data Analyst", client="OtherCo", url="https://example.test/thread/noreq", received="2026-10-19T16:00:00Z")
INVITE = "ExampleCo Program Manager Interview"


class Ledger:
    """The Job Ledger, keyed like the real one: the target check, an exact Stable Job Key lookup, one create and its read-back."""
    source = "ledger-source"

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

    def create(self, props, blocks):
        pid = f"ledger-{len(self.rows) + 1}"
        self.rows[pid] = {"page_id": pid, "company": props["Company"]["rich_text"][0]["text"]["content"], "role": props["Job"]["title"][0]["text"]["content"],
                          "stable_job_key": props["Stable Job Key"]["rich_text"][0]["text"]["content"], "requisition": None,
                          "lane": props["Visible Lane"]["select"]["name"]}
        return pid

    def for_company(self, company):
        return [r for r in self.rows.values() if r["company"] == company]


class NotionLike(WritableFake):
    """The production-shaped fake, plus what Notion itself adds on read: every rich-text run carries its plain_text."""
    def register(self, prefix, items, depth=0):
        out = super().register(prefix, items, depth)
        for item in out:
            for run in (item.get(item["type"]) or {}).get("rich_text") or []:
                run.setdefault("plain_text", (run.get("text") or {}).get("content", ""))
        return out


class Journey(unittest.TestCase):
    def setUp(self):
        self.notion, self.ledger, self.events = NotionLike(), Ledger(), []
        self.human = [dict(b) for b in self.notion.blocks["human"]]

    def tick(self, *records):
        """One natural run of the Interview step, exactly as the runner calls it, with the mailbox and Calendar as they are now."""
        calendar = type("Calendar", (), {"list_events": lambda s, a, b: [e for e in self.events if e.get("status") != "cancelled"],
                                         "request": lambda s, m, p, params=None: {"items": list(self.events)}})()
        with patch.object(ev.pipeline, "read_applied", return_value=[]):
            return ev.run(20, True, environ=LIVE, client=self.notion, calendar=calendar, now=NOW, ledger=object(), records=list(records),
                          ledger_rows=self.ledger.for_company, writer=self.ledger)

    def settle(self, *records):
        """Ticks until a tick writes nothing (each opportunity takes at most one write per tick); -> every tick's counts."""
        ticks = [self.tick(*records)]
        while ticks[-1]["writes"] and len(ticks) < 6:
            ticks.append(self.tick(*records))
        self.assertEqual(ticks[-1]["writes"], 0, "the journey never settled")
        self.assertEqual((ticks[-1]["failed"], ticks[-1]["why"].get("round_identity_incomplete")), (0, None), "a settled run must not stay DEGRADED")
        return ticks

    def shown(self, page_id):
        """Every text on a page, nested blocks and table cells included, as Jim reads it."""
        def walk(block_id):
            for b in self.notion.blocks.get(block_id, []):
                yield notion.text(b)
                yield from ("".join(r.get("text", {}).get("content", "") for r in cell) for cell in (b.get("table_row") or {}).get("cells", []))
                yield from walk(b.get("id"))
        return "\n".join(walk(page_id))

    def pages(self):
        """The opportunities this journey made (the fake pipeline also holds one human page, "Legacy — Manager", which must stay untouched)."""
        scan = notion.parent_scan(self.notion, "root")
        return [(p, notion.child_scan(self.notion, p.page_id).items) for p in scan.items if p.active and p.page_id != "human"]

    def test_recruiter_lead_to_interview_scheduled_rescheduled_cancelled_on_one_page(self):
        # 1. A recruiter names the job and the employer's requisition: one Ledger row, one opportunity page. A second recruiter gives no requisition: REVIEW, nothing written.
        first = self.tick(LEAD, NO_REQ)
        self.assertEqual((first["lead_rows"], first["parents_created"], first["lead_review"]), (1, 1, {"requisition_missing": 1}))
        (row,) = self.ledger.rows.values()
        self.assertEqual((row["stable_job_key"], row["lane"]), (KEY, "Recruiter"))
        self.settle(LEAD, NO_REQ)
        (page, kids), = self.pages()
        self.assertEqual((page.title, kids), ("ExampleCo — Program Manager", ()))
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

    def test_a_request_naming_another_requisition_for_the_same_job_creates_nothing(self):
        self.settle(LEAD)
        wrong = {**REQUEST, "body": "Please select a time for a call. Requisition 999999.", "url": "https://example.test/thread/wrong"}
        counts = self.tick(LEAD, wrong)
        (page, kids), = self.pages()
        self.assertEqual((kids, counts["writes"]), ((), 0))
        self.assertGreater(counts["failed"], 0)                                       # visible, never a quiet zero


if __name__ == "__main__":
    unittest.main()
