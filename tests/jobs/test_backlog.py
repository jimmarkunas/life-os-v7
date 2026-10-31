"""LIFE-212 (a) (Jim, Oct 31 2026): the backlog report lists every job not on the Job Ledger yet and why it waits, to one dated private Notion page; it reads the store only."""
import unittest
from datetime import date, datetime

from lifeos.jobs import backlog
from tests.jobs.test_exclusions import Cursor
from tests.jobs.test_private_report import FakeClient

NOW = datetime(2026, 10, 31, 7, 0)                                       # 2:00 AM CT
ROWS = [
    ("READY", None, "REVIEW", "Fit 66 below 72", 66, "DONE", "Stripe", "Solutions Architect", "US Remote", "web:stripe", datetime(2026, 10, 4, 15, 0), date(2026, 8, 24), "https://stripe.com/jobs/1"),
    ("READY", None, None, None, None, None, "ExampleCo", "Program Manager", "US Remote", "mail:jobright", datetime(2026, 10, 30, 20, 0), None, "https://example.test/2"),
    ("READY", None, "EXCLUDE", "older than 14 days", 80, None, "OldCo", "TPM", "US Remote", "mail:linkedin", datetime(2026, 10, 1, 9, 0), date(2026, 9, 1), "https://example.test/3"),
    ("HOLD", "jd_thin", None, None, None, None, "Thin Inc", "Senior PM", "Scale-Up", "web:su-thin", datetime(2026, 10, 20, 9, 0), None, "https://example.test/4"),
    ("NEW", None, None, None, None, None, "Fresh Ltd", "Delivery Lead", "Scale-Up", "web:su-fresh", datetime(2026, 10, 31, 6, 0), date(2026, 10, 30), "https://example.test/5"),
]


class Report(unittest.TestCase):
    def test_every_job_has_one_plain_reason_and_ready_comes_first_oldest_first(self):
        rows, groups = backlog.rows_of(ROWS, NOW)
        self.assertEqual([r[1] for r in rows], ["OldCo", "Stripe", "ExampleCo", "Thin Inc", "Fresh Ltd"])
        self.assertEqual(rows[1], ["ready · waiting to publish (Review, Fit 66)", "Stripe", "Solutions Architect", "US Remote", "27", "68", "web:stripe", "https://stripe.com/jobs/1"])
        self.assertEqual(rows[2][0], "ready · waiting for a Fit score")
        self.assertEqual(rows[0][0], "ready · excluded by the lane rules (older than 14 days)")
        self.assertEqual(rows[3][0], "held · jd_thin")
        self.assertEqual((rows[4][0], rows[4][5]), ("new · waiting for its link to resolve", "1"))
        self.assertEqual(groups["ready · waiting to publish"], 1)
        self.assertEqual(sum(groups.values()), 5)
        self.assertNotIn("Stripe", " ".join(groups))                                        # the counts carry reasons only, never a company or title

    def test_a_dry_run_reads_only_and_a_live_run_writes_one_dated_page(self):
        cursor = Cursor(ROWS)
        counts = backlog.run(0, False, connect=lambda: cursor, client=FakeClient(), now=NOW)
        self.assertEqual((counts["total"], counts["page"], counts["failed"], counts["left_out"]), (5, "dry", 0, 0))
        self.assertEqual(cursor.sql, ["SELECT"])
        client = FakeClient()
        counts = backlog.run(0, True, connect=lambda: Cursor(ROWS), client=client, now=NOW)
        self.assertEqual((counts["page"], counts["rows"], counts["failed"]), ("new", 5, 0))
        page = next(b for m, p, b in client.writes if m == "POST" and p == "/pages")
        self.assertEqual(page["properties"]["title"]["title"][0]["text"]["content"], "LIFE-212 Jobs backlog — Oct 31, 2026 02:00 AM CT — 5 not on the board")
        intro = "".join(t["text"]["content"] for t in page["children"][0]["paragraph"]["rich_text"])
        self.assertIn("5 jobs are not on the Job Ledger yet", intro)
        self.assertIn("1 · ready · waiting to publish", intro)
        self.assertEqual(page["children"][1]["type"], "table")
