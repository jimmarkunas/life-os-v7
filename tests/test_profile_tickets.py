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
        self.assertEqual(sorted(json.loads(text)["rows"][0]), sorted(["pr", "key", "files", "added", "removed", "commit_to_merge_min", "ci_runs", "cancelled_runs", "failed_before_green", "first_run_to_green_min", "scope"]))
        for title_word in ("First", "Second", "Not a ticket"):
            self.assertNotIn(title_word, text)                                    # no PR title text is copied into the output

    def test_a_failed_gh_call_is_a_fixed_message(self):
        class Done:
            returncode, stdout = 1, "secret detail"
        with self.assertRaises(RuntimeError) as caught:
            pt.gh(["pr", "list"], run=lambda *a, **k: Done())
        self.assertEqual(str(caught.exception), "gh failed")


if __name__ == "__main__":
    unittest.main()
