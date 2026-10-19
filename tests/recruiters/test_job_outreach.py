"""LIFE-47 (D222), synthetic only: the native Job Outreach floating region beneath Active Opportunities. The Daily Report shape is the Hiring Pipeline test page plus
the Job Outreach heading -> table under it; the outreach rows come from the D221 Mark as Sent flow. Nothing is live."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform import report_region as R, router
from lifeos.sources import job_outreach as J
from tests.hiring.test_hiring import ENV as HIRING_ENV, page as hiring_page, table_state, writes
from tests.kit.recruiters import FakeRecruiters
from tests.recruiters.test_outreach import JOB_A, JOB_B, LEDGER, SCHEMA, Ledger, Service, ledger_page, mark

HEAD, TABLE = "0000000000000000000000000000c001", "0000000000000000000000000000c003"
ENV = {**HIRING_ENV, "JOB_OUTREACH_CARD_BLOCK_ID": HEAD, "JOB_OUTREACH_TABLE_BLOCK_ID": TABLE}
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))


def report(headers=J.HEADERS, rows=0, heading=J.TITLE):
    """The Hiring Pipeline test page with the Job Outreach region inserted right after the Hiring table, in the same column."""
    t = hiring_page(extra_rows=1)
    right = t.kids["right"]
    at = right.index("0000000000000000000000000000a003") + 1
    t.add(HEAD, "heading_3", "right", text=heading)
    t.table(TABLE, "right", [list(headers)] + [["old job", "x", "y", "z"] for _ in range(rows)])
    for block in (HEAD, TABLE):                                        # Tree.add appends; move both to sit under the Hiring table
        right.remove(block)
    right[at:at] = [HEAD, TABLE]
    return t


class Region(unittest.TestCase):
    def test_dry_run_validates_and_writes_nothing(self):
        t = report()
        self.assertEqual(J.present(t, ENV, "Updated 2:00 PM CT", J.cells([]), False)["verified"], False)
        self.assertEqual(writes(t), [])

    def test_live_writes_rows_reads_back_and_leaves_the_hiring_table_alone(self):
        t = report(rows=2)
        hiring_before = table_state(t)
        rows = [{"job": "Northwind Labs — Senior TPM", "outreach": "2 contacted · 0 need action", "next": "Follow up · Oct 19", "link": "https://www.notion.so/" + JOB_A}]
        done = J.present(t, ENV, "Updated 2:00 PM CT", J.cells(rows), True)
        self.assertEqual((done["verified"], done["rows_changed"], done["rows_removed"]), (True, 1, 1))
        self.assertEqual(table_state(t, TABLE), [[("Northwind Labs — Senior TPM", None), ("2 contacted · 0 need action", None), ("Follow up · Oct 19", None),
                                                  ("Job Ledger ↗", "https://www.notion.so/" + JOB_A)]])
        self.assertEqual(R.plain(t.blocks[HEAD]), "🤝 Job Outreach (Updated 2:00 PM CT)")
        self.assertEqual(table_state(t), hiring_before)
        again = len(writes(t))
        J.present(t, ENV, "Updated 2:00 PM CT", J.cells(rows), True)                  # replay: nothing to change
        self.assertEqual(len(writes(t)), again)

    def test_empty_state_is_one_explicit_row(self):
        t = report(rows=1)
        J.present(t, ENV, "Updated 2:00 PM CT", J.cells([]), True)
        self.assertEqual(table_state(t, TABLE), [[(J.EMPTY, None), ("", None), ("", None), ("", None)]])

    def test_wrong_shape_or_ids_write_nothing(self):
        for t, env, code in ((report(headers=("Job", "Outreach", "Next", "Notes")), ENV, "HEADERS_CHANGED"),
                             (report(), {**ENV, "JOB_OUTREACH_TABLE_BLOCK_ID": "0000000000000000000000000000a003"}, "PINNED_BLOCK_MISMATCH"),
                             (report(), {**ENV, "JOB_OUTREACH_CARD_BLOCK_ID": "not-an-id"}, "ID_INVALID"),
                             (report(heading="Job Outreach"), ENV, "FLOATING_HEADING_INVALID")):
            with self.subTest(code=code):
                with self.assertRaises(J.JobOutreachError) as caught:
                    J.present(t, env, "Updated 2:00 PM CT", J.cells([]), True)
                self.assertIn(code, str(caught.exception))
                self.assertEqual(writes(t), [])

    def test_the_region_has_one_owner(self):
        self.assertEqual(router.OWNERS[router.JOB_OUTREACH_REGION], "v7-job-outreach")
        self.assertFalse(router.is_callout(router.JOB_OUTREACH_REGION))
        with self.assertRaises(router.RouterError):
            router.check_write("v7-hiring", [router.JOB_OUTREACH_REGION])


class Stage(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(ledger_page(JOB_A, "Northwind Labs", "Senior Technical Program Manager"), ledger_page(JOB_B, "Northwind Labs", "Technical Program Manager"))
        self.rec = FakeRecruiters(schema=SCHEMA)

    def stage(self, t, env=ENV, recruiters=None):
        return J.run(0, True, environ=env, recruiters=recruiters or self.rec, ledger=self.ledger, client=t, now=NOW)

    def test_unconfigured_is_dormant_and_touches_nothing(self):
        t = report()
        self.assertEqual(J.run(0, True, environ=HIRING_ENV, recruiters=self.rec, ledger=self.ledger, client=t), {"job_outreach": "NOT_CONFIGURED"})
        self.assertEqual((t.log, self.rec.queries), ([], 0))

    def test_mark_as_sent_appears_as_one_row_per_exact_job(self):
        svc = Service(self.ledger, self.rec)
        for job_id, person in ((JOB_A, "Avery Synthetic"), (JOB_A, "Blake Synthetic"), (JOB_B, "Avery Synthetic")):
            mark(svc, job_id, person)
        t = report()
        done = self.stage(t)
        self.assertEqual((done["jobs"], done["conversations"], done["verified"]), (2, 3, True))
        self.assertEqual([r[0][0] for r in table_state(t, TABLE)], ["Northwind Labs — Senior Technical Program Manager", "Northwind Labs — Technical Program Manager"])
        self.assertEqual(table_state(t, TABLE)[0][1:3], [("2 contacted · 0 need action", None), ("Follow up · Oct 19", None)])

    def test_a_job_closed_after_outreach_drops_out(self):
        mark(Service(self.ledger, self.rec), JOB_A, "Avery Synthetic")
        self.ledger.pages[JOB_A]["properties"]["Hiring Decision"]["select"] = {"name": "Rejected"}
        t = report()
        self.assertEqual(self.stage(t)["jobs"], 0)
        self.assertEqual(table_state(t, TABLE), [[(J.EMPTY, None), ("", None), ("", None), ("", None)]])

    def test_an_unreadable_source_marks_degraded_and_keeps_the_rows(self):
        t = report(rows=1)
        before = table_state(t, TABLE)
        with self.assertRaises(J.JobOutreachError) as caught:
            self.stage(t, recruiters=FakeRecruiters(schema=SCHEMA, fail_query=True))
        self.assertIn("SOURCE_UNREADABLE", str(caught.exception))
        self.assertEqual(R.plain(t.blocks[HEAD]), "🤝 Job Outreach (DEGRADED)")
        self.assertEqual(table_state(t, TABLE), before)


if __name__ == "__main__":
    unittest.main()
