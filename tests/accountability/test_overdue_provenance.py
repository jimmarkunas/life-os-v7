"""Overdue scheduled work and closure provenance on the V3 weekly view (R4.1 to R4.6). Synthetic keys only."""

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import read, weekview
from lifeos.platform.jira import JiraError
from tests.accountability.test_v3_week_view import Board, issue

TODAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
SINCE = datetime(2026, 11, 25, 14, 30, tzinfo=ZoneInfo("America/Chicago"))
AFTER, BEFORE = "2026-11-26T16:13:15.000-0600", "2026-11-23T10:00:00.000-0600"


def closed(key, resolution="Done", labels=(), at=AFTER):
    row = issue(key, "Done")
    row["fields"].update(
        {
            "resolution": {"name": resolution},
            "labels": list(labels),
            "statuscategorychangedate": at,
            "resolutiondate": at,
        }
    )
    return row


def week(board):
    return read.week(board, "MEJ", TODAY, v3_week_view=True, since=SINCE)


class Overdue(unittest.TestCase):
    def test_an_overdue_item_in_a_future_sprint_is_its_own_section_not_backlog(self):
        outside = [issue("MEJ-234", "To Do", "2026-11-25"), issue("MEJ-161", "To Do", "2026-09-30")]
        board = Board(
            "MEJ", [], outside, future=[issue("MEJ-234", "To Do", "2026-11-25")], sprints={"MEJ-234": "MEJ W50 2026"}
        )
        got = week(board)
        self.assertEqual([r["key"] for r in got["overdue"]], ["MEJ-161"])  # R4.2: no active or future sprint
        self.assertEqual(
            [(r["key"], r["sprint"]) for r in got["overdue_scheduled"]], [("MEJ-234", "MEJ W50 2026")]
        )  # R4.1
        text = "\n".join(weekview.plain(weekview.spec(got, TODAY, "https://example.invalid", "MEJ", NOW)))
        self.assertIn("Overdue backlog — 1", text)
        self.assertIn("Overdue in a future sprint — 1", text)
        self.assertIn("MEJ-234 — Synthetic MEJ-234 · MEJ W50 2026 · due Nov 25", text)
        self.assertIn("+ 1 overdue in a future sprint", text)

    def test_active_overdue_stays_with_the_commitments_and_only_parents_count(self):
        sprint = [issue("MEJ-150", "In Progress", "2026-11-06"), issue("MEJ-238", "To Do", None, "Sub-task", "MEJ-150")]
        got = week(Board("MEJ", sprint, []))
        self.assertEqual(
            ([r["key"] for r in got["open"]], got["overdue"], got["overdue_scheduled"]), (["MEJ-150"], [], [])
        )
        self.assertEqual(sum(len(v) for v in got["subtasks"].values()), 1)  # R4.3

    def test_a_sprint_that_cannot_be_read_degrades_the_region(self):
        board = Board(
            "MEJ", [], [issue("MEJ-234", "To Do", "2026-11-25")], future=[issue("MEJ-234", "To Do", "2026-11-25")]
        )
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_WEEK_SPRINT_UNREAD"):
            week(board)  # R4.6


class Closures(unittest.TestCase):
    def done_board(self):
        rows = [
            closed("MEJ-364"),
            closed("MEJ-263", at=BEFORE),
            closed("MEJ-38", "Won't Do", ["superseded"]),
            closed("MEJ-147", "Won't Do", ["superseded"]),
            closed("MEJ-352", "Won't Do", ["superseded"]),
            closed("MEJ-5", labels=["auto-closed"]),
        ]
        return Board("MEJ", [], [], rows)

    def test_a_cancellation_is_never_delivered_and_a_pre_tracking_closure_is_never_by_hand(self):
        got = week(self.done_board())
        self.assertEqual(
            {r["key"]: r["kind"] for r in got["done"]},
            {"MEJ-364": "delivered_manual", "MEJ-263": "unknown", "MEJ-5": "delivered_auto"},
        )
        self.assertEqual([r["key"] for r in got["cancelled"]], ["MEJ-38", "MEJ-147", "MEJ-352"])  # R4.4 and R4.5
        text = "\n".join(weekview.plain(weekview.spec(got, TODAY, "https://example.invalid", "MEJ", NOW)))
        self.assertIn("Done this week | 1 by hand · 1 auto · 1 before tracking", text)
        self.assertIn("Cancelled this week (not delivered) — 3", text)
        self.assertIn("MEJ-263 — Synthetic MEJ-263 · before tracking", text)

    def test_closed_work_without_its_evidence_degrades_the_region(self):
        bad = issue("MEJ-9", "Done")  # no resolution or labels were read
        del bad["fields"]["resolution"]
        with self.assertRaisesRegex(JiraError, "ACCOUNTABILITY_CLOSURE_EVIDENCE_MISSING"):
            week(Board("MEJ", [], [], [bad]))  # R4.6


if __name__ == "__main__":
    unittest.main()
