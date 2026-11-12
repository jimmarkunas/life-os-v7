"""LIFE-609: the due-time logic. The real functions run; only the Actions API (a fake that answers from a small run history) is replaced."""
import datetime as dt
import json
import pathlib
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from lifeos.platform import due

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 12, 20, 17, tzinfo=UTC)                  # 2:17 PM CT
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def ago(hours):
    return NOW - dt.timedelta(hours=hours)


class History:
    """A fake Actions API: runs newest first, each with job conclusions. Records the paths it was asked for."""
    def __init__(self, runs, fail=False):
        self.runs, self.fail, self.paths = runs, fail, []

    def __call__(self, path, *args, **kwargs):
        self.paths.append(path)
        if self.fail:
            raise OSError("GitHub unreachable")
        if "/runs?" in path:
            return {"workflow_runs": [{"id": i, "created_at": r["at"].strftime(STAMP), "run_started_at": r["at"].strftime(STAMP)} for i, r in enumerate(self.runs)]}
        index = int(path.split("actions/runs/")[1].split("/")[0])
        return {"jobs": [{"name": name, "conclusion": result} for name, result in self.runs[index]["jobs"].items()]}


def run(hours_ago, **jobs):
    return {"at": ago(hours_ago), "jobs": jobs}


ROWS = {"mail": {"every_hours": 6}, "jira": {"every_hours": 1}, "retention": {"slots_ct": ["09:00"]}}


class Rules(unittest.TestCase):
    def test_a_six_hour_row_is_due_after_seven_hours_and_skipped_after_two(self):
        self.assertTrue(due.is_due({"every_hours": 6}, ago(7), NOW)[0])
        self.assertFalse(due.is_due({"every_hours": 6}, ago(2), NOW)[0])

    def test_the_fifteen_minute_slack_covers_a_tick_that_starts_a_little_early(self):
        self.assertTrue(due.is_due({"every_hours": 6}, ago(5.8), NOW)[0])
        self.assertFalse(due.is_due({"every_hours": 6}, ago(5.5), NOW)[0])

    def test_an_hourly_row_is_due_every_tick_even_if_it_ran_a_minute_ago(self):
        self.assertTrue(due.is_due({"every_hours": 1}, ago(0.02), NOW)[0])

    def test_a_job_with_no_earlier_success_is_due(self):
        self.assertTrue(due.is_due({"every_hours": 6}, None, NOW)[0])
        self.assertTrue(due.is_due({"slots_ct": ["09:00"]}, None, NOW)[0])

    def test_a_ct_slot_is_due_once_after_it_passes(self):
        nine_oh_five = dt.datetime(2026, 11, 12, 15, 5, tzinfo=UTC)          # 9:05 AM CT
        before_slot = dt.datetime(2026, 11, 12, 13, 0, tzinfo=UTC)           # 7:00 AM CT
        after_slot = dt.datetime(2026, 11, 12, 15, 2, tzinfo=UTC)            # 9:02 AM CT
        self.assertTrue(due.is_due({"slots_ct": ["09:00"]}, before_slot, nine_oh_five)[0])
        self.assertFalse(due.is_due({"slots_ct": ["09:00"]}, after_slot, nine_oh_five)[0])
        self.assertFalse(due.is_due({"slots_ct": ["09:00"]}, before_slot, dt.datetime(2026, 11, 12, 14, 0, tzinfo=UTC))[0])    # 8:00 AM CT: not yet

    def test_a_job_that_missed_two_slots_is_due_once_and_then_fresh(self):
        last = ago(13)                                                       # two 6-hour slots missed
        self.assertTrue(due.is_due({"every_hours": 6}, last, NOW)[0])
        self.assertFalse(due.is_due({"every_hours": 6}, NOW, NOW + dt.timedelta(hours=1))[0])     # after it ran, the next tick skips it
        two_days = due.is_due({"slots_ct": ["09:00"]}, ago(48), NOW)
        self.assertTrue(two_days[0])


class BillsEverySixHours(unittest.TestCase):
    """LIFE-612: the Bills job runs on the first tick after 12:00 AM, 6:00 AM, 12:00 PM and 6:00 PM CT."""
    ROW = due.load_table()["bills"]

    def due_ticks(self, start, hours, last, failing=()):
        found, now = [], start
        for n in range(hours):
            if due.is_due(self.ROW, last, now)[0]:
                found.append(now.astimezone(due.CENTRAL).strftime("%a %-I:%M %p"))
                if n not in failing:
                    last = now
            now += dt.timedelta(hours=1)
        return found

    def test_the_real_row_is_the_four_slots(self):
        self.assertEqual(self.ROW, {"slots_ct": ["00:00", "06:00", "12:00", "18:00"]})

    def test_a_day_of_hourly_ticks_runs_it_four_times_and_skips_the_other_twenty(self):
        found = self.due_ticks(dt.datetime(2026, 11, 12, 6, 17, tzinfo=UTC), 24, dt.datetime(2026, 11, 11, 23, 17, tzinfo=UTC))          # 12:17 AM CT on, after the 6:17 PM run
        self.assertEqual(found, ["Thu 12:17 AM", "Thu 6:17 AM", "Thu 12:17 PM", "Thu 6:17 PM"])

    def test_a_dropped_slot_is_caught_up_once_on_the_next_tick(self):
        found = self.due_ticks(dt.datetime(2026, 11, 12, 6, 17, tzinfo=UTC), 14, dt.datetime(2026, 11, 11, 23, 17, tzinfo=UTC), failing={6})        # the 6:17 AM run fails
        self.assertEqual(found, ["Thu 12:17 AM", "Thu 6:17 AM", "Thu 7:17 AM", "Thu 12:17 PM"])

    def test_a_manual_dispatch_is_not_a_tick_so_bills_runs_as_today(self):
        self.assertEqual(due.decide({"bills": self.ROW}, {"bills": dt.datetime(2026, 11, 12, 15, 17, tzinfo=UTC)}, NOW, tick=False), {"bills": (True, "not a tick")})


class History_(unittest.TestCase):
    def test_last_success_takes_the_start_of_the_newest_run_where_the_job_succeeded(self):
        api = History([run(1, mail="skipped", jira="success"), run(7, mail="success", jira="success"), run(13, mail="success")])
        found = due.fetch_last_success({"mail", "jira"}, api)
        self.assertEqual(found, {"jira": ago(1), "mail": ago(7)})                       # mail was skipped one hour ago: that is not a success

    def test_a_failed_job_is_not_a_success_and_reading_stops_when_every_job_is_found(self):
        api = History([run(2, mail="failure"), run(5, mail="success"), run(9, mail="success")])
        self.assertEqual(due.fetch_last_success({"mail"}, api), {"mail": ago(5)})
        self.assertFalse(any("actions/runs/2/jobs" in p for p in api.paths))           # the third run was never read

    def test_only_runs_started_by_a_tick_are_read(self):
        api = History([run(1, mail="success")])
        due.fetch_last_success({"mail"}, api)
        self.assertIn("event=workflow_run", api.paths[0])                              # a manual or benchmark run never makes a card look fresh


class Main(unittest.TestCase):
    def go(self, rows, history, event="workflow_run"):
        with tempfile.TemporaryDirectory() as tmp:
            out, summary = pathlib.Path(tmp, "out"), pathlib.Path(tmp, "summary")
            code = due.main({"GITHUB_EVENT_NAME": event, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}, api=history, now=NOW, table=rows)
            return code, dict(line.split("=") for line in out.read_text().split()), summary.read_text()

    def test_the_gate_writes_a_due_output_per_job_and_a_summary_that_names_each_as_due_or_skipped(self):
        history = History([run(2, mail="success", jira="success"), run(3, mail="success", jira="success")])
        code, outputs, summary = self.go(ROWS, history)
        self.assertEqual((code, outputs), (0, {"due_mail": "false", "due_jira": "true", "due_retention": "true"}))
        self.assertIn("| mail | every 6 h |", summary)
        self.assertIn("| skipped |", summary)
        self.assertIn("| due |", summary)

    def test_an_unreadable_history_makes_every_job_due_and_says_so(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code, outputs, summary = self.go(ROWS, History([], fail=True))
        self.assertEqual((code, set(outputs.values())), (0, {"true"}))
        self.assertIn("history unreadable", buffer.getvalue())
        self.assertIn("every job is due (fail open)", summary)

    def test_a_dispatch_or_benchmark_runs_everything_and_reads_no_history(self):
        history = History([run(2, mail="success")])
        code, outputs, _ = self.go(ROWS, history, event="workflow_dispatch")
        self.assertEqual(set(outputs.values()), {"true"})
        self.assertEqual(history.paths, [])

    def test_all_hourly_rows_read_no_history_and_every_job_is_due(self):
        history = History([run(0.1, mail="success")])
        _, outputs, _ = self.go({"mail": {"every_hours": 1}, "jira": {"every_hours": 1}}, history)
        self.assertEqual(set(outputs.values()), {"true"})
        self.assertEqual(history.paths, [])


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class TableMatchesTheWorkflow(unittest.TestCase):
    def test_every_domain_job_has_exactly_one_row_and_every_row_is_valid(self):
        jobs = yaml.safe_load((pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/domains.yml").read_text())["jobs"]
        domain = set(jobs) - {"gate", "report", "bench_report"}
        table = due.load_table()
        self.assertEqual(set(table), domain)
        for job, row in table.items():
            self.assertEqual(len(row), 1, job)
            if "every_hours" in row:
                self.assertTrue(isinstance(row["every_hours"], int) and row["every_hours"] >= 1, job)
            else:
                self.assertTrue(all(len(s) == 5 and s[2] == ":" for s in row["slots_ct"]), job)

    def test_jims_confirmed_rows_are_in_the_table_and_the_three_with_their_own_tickets_are_still_hourly(self):
        table = due.load_table()
        self.assertEqual((table["mail"], table["amazon"], table["megibow"]), ({"every_hours": 6}, {"every_hours": 3}, {"every_hours": 3}))     # comment 12803
        for job in ("agenda", "hiring", "recruiters"):
            self.assertEqual(table[job], {"every_hours": 1}, job)                      # LIFE-611 and 612 flipped retention and bills; the recruiters JOB stays hourly (it also runs Interview OS and the outreach cards): LIFE-610 puts its 3-hour schedule on the mail step, in the table's `steps`; agenda and hiring stay hourly by Jim's answer


if __name__ == "__main__":
    unittest.main()
