"""administrative cancellations never count as delivered work (R1.1 to R1.6)."""

import copy
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import closure, read
from lifeos.jira import reconcile as rc
from lifeos.platform.jira import JiraError
from tests.accountability.fakes import child

FIRST, LAST = date(2026, 10, 7), date(2027, 1, 5)
RESOLVED = "2026-11-10T10:00:00.000-0600"
SINCE = datetime(2026, 11, 25, 14, 30, tzinfo=ZoneInfo("America/Chicago"))
AFTER = datetime(2026, 11, 26, 16, 13, tzinfo=ZoneInfo("America/Chicago"))


def creative_q4():
    """Matt's Creative Q4: 3 delivered, 3 cancelled (MEJ-38, 147, 352) and 11 open under one goal."""
    kids = [child(f"MEJ-{n}", "MEJ-1", True, RESOLVED) for n in (10, 11, 12)]
    kids += [
        child(f"MEJ-{n}", "MEJ-1", True, RESOLVED, resolution="Won't Do", labels=["superseded"]) for n in (38, 147, 352)
    ]
    kids += [child(f"MEJ-{n}", "MEJ-1", False) for n in range(200, 211)]
    return kids


class Tally(unittest.TestCase):
    def test_the_migration_fixture_goes_from_6_of_17_to_3_of_14(self):
        kids = creative_q4()
        without_resolution_check = [
            dict(k, fields=dict(k["fields"], resolution={"name": "Done"}, labels=[])) for k in kids
        ]
        self.assertEqual(read._counts(without_resolution_check, FIRST, LAST), {"MEJ-1": [6, 17]})  # R1.2 before
        self.assertEqual(read._counts(kids, FIRST, LAST), {"MEJ-1": [3, 14]})  # R1.1 and R1.2 after

    def test_an_epic_contributes_nothing(self):
        epic = child("MEJ-375", "MEJ-1", True, RESOLVED, resolution="Won't Do", labels=["superseded"])
        epic["fields"]["issuetype"] = {"name": "Epic", "subtask": False}
        self.assertEqual(read._counts([epic], FIRST, LAST), {})  # R1.3

    def test_unread_or_contradictory_evidence_degrades_the_calculation(self):
        unread = child("MEJ-20", "MEJ-1", True, RESOLVED)
        del unread["fields"]["resolution"]
        contradictory = child("MEJ-22", "MEJ-1", True, RESOLVED, labels=["superseded"])  # superseded but resolved Done
        auto_cancelled = child("MEJ-23", "MEJ-1", True, RESOLVED, resolution="Won't Do", labels=["auto-closed"])
        for bad in (unread, contradictory, auto_cancelled):
            with self.assertRaises(JiraError, msg=bad["key"]):
                read._counts([bad], FIRST, LAST)  # R1.5

    def test_an_older_done_issue_with_no_resolution_still_counts_as_done_and_is_never_cancelled(self):
        legacy = child("MEJ-222", "MEJ-1", True, RESOLVED)
        legacy["fields"]["resolution"] = None
        self.assertEqual(read._counts([legacy], FIRST, LAST), {"MEJ-1": [1, 1]})
        self.assertEqual(closure.classify(legacy["fields"], AFTER, SINCE), "unknown")


class Classes(unittest.TestCase):
    def test_every_closed_issue_has_exactly_one_class_and_the_totals_reconcile(self):
        cases = {
            "manual": ({"resolution": {"name": "Done"}, "labels": []}, AFTER),
            "auto": ({"resolution": {"name": "Done"}, "labels": ["auto-closed"]}, AFTER),
            "cancelled": ({"resolution": {"name": "Won't Do"}, "labels": ["superseded"]}, AFTER),
            "unknown": (
                {"resolution": {"name": "Done"}, "labels": []},
                datetime(2026, 11, 1, tzinfo=ZoneInfo("America/Chicago")),
            ),
        }
        got = {name: closure.classify(fields, at, SINCE) for name, (fields, at) in cases.items()}
        self.assertEqual(
            got,
            {"manual": "delivered_manual", "auto": "delivered_auto", "cancelled": "cancelled", "unknown": "unknown"},
        )
        totals = closure.counts(got.values())
        self.assertEqual(
            totals, {"delivered_manual": 1, "delivered_auto": 1, "cancelled": 1, "unknown": 1, "total": 4}
        )  # R1.4

    def test_without_a_tracking_start_a_done_issue_is_unknown_never_manual(self):
        self.assertEqual(closure.classify({"resolution": {"name": "Done"}, "labels": []}, AFTER, None), "unknown")

    def test_the_weekly_closure_split_never_shows_a_cancellation_as_delivered_by_hand(self):
        def issue(key, resolution, labels):
            return {
                "key": key,
                "fields": {
                    "labels": labels,
                    "resolution": {"name": resolution},
                    "resolutiondate": "2026-11-26T16:13:15.000-0600",
                    "statuscategorychangedate": "2026-11-26T16:13:15.000-0600",
                    "status": {"statusCategory": {"key": "done"}},
                },
            }

        issues = [
            issue("MEJ-364", "Done", []),
            issue("MEJ-38", "Won't Do", ["superseded"]),
            issue("MEJ-147", "Won't Do", ["superseded"]),
            issue("MEJ-5", "Done", ["auto-closed"]),
        ]
        got = rc.closure_counts(issues, SINCE)
        self.assertEqual(got, {"manual": 1, "automatic": 1, "cancelled": 2, "total": 4, "split": True})


class Snapshots(unittest.TestCase):
    def test_a_published_snapshot_is_never_edited_and_the_correction_is_versioned_beside_it(self):
        original = {"MEJ-1": [6, 17]}
        before = copy.deepcopy(original)
        pair = closure.snapshot_pair(original, {"MEJ-1": [3, 14]})
        self.assertEqual(original, before)  # R1.6 originals unchanged
        self.assertEqual(pair["original"], before)
        self.assertEqual(pair["corrected"], {"MEJ-1": [3, 14]})
        self.assertEqual(pair["correction_version"], closure.CORRECTION_VERSION)


if __name__ == "__main__":
    unittest.main()
