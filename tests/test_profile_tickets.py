"""LIFE-556: the read-only ticket profile. A saved, trimmed fixture stands in for `gh` (no network, no credentials); the numbers are worked out by hand in the comments."""
import io
import json
import unittest
from pathlib import Path

from lifeos import profile_tickets as pt

FX = json.loads((Path(__file__).parent / "fixtures" / "profile_tickets.json").read_text())


def fake_gh(args):
    if args[:2] == ["pr", "list"]:
        return FX["list"]
    if args[:2] == ["pr", "view"]:
        return FX["view"][args[2]]
    if args[:2] == ["run", "list"]:
        return FX["runs"][args[args.index("--branch") + 1]]
    raise AssertionError(args)


class Rows(unittest.TestCase):
    def rows(self, jira=None):
        return pt.collect(20, jira, fake_gh)

    def test_only_ticket_titled_prs_newest_first(self):
        self.assertEqual([r["key"] for r in self.rows()], ["LIFE-902", "LIFE-901"])

    def test_the_numbers_for_one_pr_worked_by_hand(self):
        row = {r["key"]: r for r in self.rows()}["LIFE-901"]
        self.assertEqual((row["files"], row["added"], row["removed"]), (3, 30, 4))
        self.assertEqual(row["commit_to_merge_min"], 10.0)                       # first commit 18:00, merged 18:10
        self.assertEqual((row["ci_runs"], row["cancelled_runs"], row["failed_before_green"]), (3, 1, 1))
        self.assertEqual(row["first_run_to_green_min"], 6.0)                     # first run 18:02, green on the final head at 18:08
        self.assertEqual(row["scope"], ["platform"])                              # generated docs/CODEMAP.md is ignored, tests/platform and lifeos/platform are one package

    def test_jira_minutes_are_used_only_when_both_times_exist(self):
        rows = {r["key"]: r for r in self.rows(FX["jira"])}
        self.assertEqual(rows["LIFE-901"]["jira_start_to_done_min"], 90.0)
        self.assertNotIn("jira_start_to_done_min", rows["LIFE-902"])

    def test_no_green_on_the_final_head_is_reported_as_none_not_guessed(self):
        runs = [{"conclusion": "failure", "createdAt": "2026-11-10T18:02:00Z", "updatedAt": "2026-11-10T18:04:00Z", "headSha": "aaa"}]
        row = pt.profile(FX["list"][0], FX["view"]["11"], runs)
        self.assertIsNone(row["first_run_to_green_min"])
        self.assertEqual(row["failed_before_green"], 1)

    def test_summary_median_and_worst_and_one_jira_value_per_ticket(self):
        rows = self.rows(FX["jira"])
        rows.append({**rows[1], "pr": 99})                                        # a second PR of the same ticket
        summary = pt.summary(rows)
        self.assertEqual(summary["jira_start_to_done_min_per_ticket"]["n"], 1)
        self.assertEqual(summary["files"]["worst"], 3)
        self.assertEqual(summary["prs_with_a_failed_first_run"], 2)


class Output(unittest.TestCase):
    def test_the_report_has_numbers_and_keys_only(self):
        out = io.StringIO()
        pt.main(["--last", "2", "--json"], call=fake_gh, out=out)
        text = out.getvalue()
        self.assertEqual(sorted(json.loads(text)["rows"][0]), sorted(["pr", "key", "files", "added", "removed", "commit_to_merge_min", "ci_runs", "cancelled_runs", "failed_before_green", "first_run_to_green_min", "scope", "first_commit", "merged_at"]))                    # LIFE-598 added the two times the split needs; still numbers, keys and times only
        for title_word in ("First", "Second", "Not a ticket"):
            self.assertNotIn(title_word, text)                                    # no PR title text is copied into the output

    def test_a_failed_gh_call_is_a_fixed_message(self):
        class Done:
            returncode, stdout = 1, "secret detail"
        with self.assertRaises(RuntimeError) as caught:
            pt.gh(["pr", "list"], run=lambda *a, **k: Done())
        self.assertEqual(str(caught.exception), "gh failed")


class Split(unittest.TestCase):
    """LIFE-598: the ticket's minutes split in three: Agent Go to the first commit, first commit to the last merge, last merge to Done. Worked by hand from the fixture."""
    def rows(self, jira):
        return pt.collect(20, jira, fake_gh)

    def test_the_three_parts_of_one_ticket_add_up_to_its_total(self):
        jira = {"LIFE-901": {"start": "2026-11-10T17:30:00Z", "done": "2026-11-10T19:10:00Z"}}      # first commit 18:00, merged 18:10
        parts = pt.split(self.rows(jira), jira)["LIFE-901"]
        self.assertEqual((parts["queue_and_orientation_min"], parts["building_min"], parts["after_merge_to_done_min"], parts["total_min"]), (30.0, 10.0, 60.0, 100.0))

    def test_a_ticket_without_both_jira_times_is_left_out_and_counted(self):
        jira = {"LIFE-901": {"start": "2026-11-10T17:30:00Z"}}
        self.assertEqual(pt.split(self.rows(jira), jira), {})
        report = pt.split_summary({})
        self.assertEqual((report["n"], report["largest"]), (0, None))

    def test_the_largest_part_is_named_by_the_sum_of_minutes_with_its_share(self):
        tickets = {"A": {"queue_and_orientation_min": 10.0, "building_min": 5.0, "after_merge_to_done_min": 85.0, "total_min": 100.0},
                   "B": {"queue_and_orientation_min": 30.0, "building_min": 20.0, "after_merge_to_done_min": 50.0, "total_min": 100.0}}
        report = pt.split_summary(tickets)
        self.assertEqual((report["n"], report["largest"]), (2, "after_merge_to_done_min"))
        self.assertEqual(report["share_pct"], {"queue_and_orientation_min": 20, "building_min": 12, "after_merge_to_done_min": 68})
        self.assertEqual(report["median_min"]["after_merge_to_done_min"], 67.5)

    def test_a_ticket_with_two_prs_uses_the_first_commit_of_the_earlier_and_the_merge_of_the_later(self):
        rows = [{"key": "T-1", "first_commit": "2026-11-10T10:00:00Z", "merged_at": "2026-11-10T10:30:00Z"}, {"key": "T-1", "first_commit": "2026-11-10T10:20:00Z", "merged_at": "2026-11-10T11:00:00Z"}]
        jira = {"T-1": {"start": "2026-11-10T09:00:00Z", "done": "2026-11-10T12:00:00Z"}}
        parts = pt.split(rows, jira)["T-1"]
        self.assertEqual((parts["queue_and_orientation_min"], parts["building_min"], parts["after_merge_to_done_min"]), (60.0, 60.0, 60.0))


if __name__ == "__main__":
    unittest.main()
