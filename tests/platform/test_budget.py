"""LIFE-691: every stage has a time budget; a stage over it stops, records one fixed code, and fails the run only when it is a required stage."""
import contextlib
import io
import json
import signal
import time
import unittest
from unittest import mock

from lifeos import run
from lifeos.jobs import tick
from lifeos.jobs.fit import cloud
from lifeos.platform import budget

TABLE = {"default_seconds": 600, "stages": {"slow": {"seconds": 5, "required": True}, "quiet": {"seconds": 7, "required": False}}}


class BudgetTable(unittest.TestCase):
    def test_a_stage_gets_its_row_and_an_unlisted_stage_the_default_as_optional(self):
        self.assertEqual(budget.budget_for("slow", TABLE), (5, True))
        self.assertEqual(budget.budget_for("quiet", TABLE), (7, False))
        self.assertEqual(budget.budget_for("never-heard-of-it", TABLE), (600, False))

    def test_over_budget_counts_carry_only_the_fixed_code_and_numbers(self):
        counts, code = budget.over_budget(5, 5.7, True)
        self.assertEqual((counts, code), ({"stage_budget_exceeded": 1, "code": "STAGE_BUDGET_EXCEEDED", "budget_seconds": 5, "elapsed_seconds": 5, "required": True}, 1))
        self.assertEqual(budget.over_budget(5, 5.7, False)[1], 0)                              # an optional stage lets the run pass


class Guard(unittest.TestCase):
    def test_a_stage_over_its_budget_is_stopped_and_cannot_swallow_it(self):
        ran = []
        with self.assertRaises(budget.StageBudgetExceeded):
            with budget.guard(0.05):
                try:
                    time.sleep(2)
                except Exception:                                                              # noqa: BLE001 - the stage's own handler: the alarm is a BaseException and passes through
                    ran.append("swallowed")
                finally:
                    ran.append("finally ran")
        self.assertEqual(ran, ["finally ran"])

    def test_a_stage_within_its_budget_is_untouched_and_the_alarm_is_cleaned_up(self):
        before = signal.getsignal(signal.SIGALRM)
        with budget.guard(5):
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)

    def test_it_stays_out_of_the_way_where_another_alarm_is_armed_or_when_switched_off(self):
        signal.setitimer(signal.ITIMER_REAL, 30)                                               # the Jobs tick arms one per stage
        try:
            with budget.guard(0.01):
                time.sleep(0.05)                                                               # would have been stopped
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)                     # and the tick's alarm is still there
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        with budget.guard(0.01, environ={"STAGE_BUDGETS": "off"}):
            time.sleep(0.05)


class RunMain(unittest.TestCase):
    """The real lifeos.run.main with a stage that outlasts its budget."""

    def go(self, name, required):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(run.STAGES, {name: lambda limit, live: time.sleep(3)}), mock.patch.object(budget, "budget_for", return_value=(0.05, required)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run.main([name])
        return code, out.getvalue(), err.getvalue()

    def test_a_required_stage_over_budget_fails_the_run_with_the_fixed_code(self):
        code, out, err = self.go("slow-required", True)
        self.assertEqual(code, 1)
        self.assertIn("SLOW-REQUIRED FAILED: STAGE_BUDGET_EXCEEDED", err)
        self.assertEqual(json.loads(out.split(": ", 1)[1])["code"], "STAGE_BUDGET_EXCEEDED")

    def test_an_optional_stage_over_budget_records_the_code_and_the_run_passes(self):
        code, out, err = self.go("slow-optional", False)
        self.assertEqual(code, 0)
        self.assertIn("SLOW-OPTIONAL STOPPED: STAGE_BUDGET_EXCEEDED", err)
        self.assertEqual(json.loads(out.split(": ", 1)[1])["stage_budget_exceeded"], 1)

    def test_a_stage_within_its_budget_runs_exactly_as_before(self):
        out = io.StringIO()
        with mock.patch.dict(run.STAGES, {"quick-stage": lambda limit, live: {"done": 1}}), contextlib.redirect_stdout(out):
            self.assertEqual(run.main(["quick-stage"]), 0)
        self.assertEqual(json.loads(out.getvalue().split(": ", 1)[1].splitlines()[0]), {"done": 1})


class TickRule(unittest.TestCase):
    """The Jobs tick keeps its own alarm and code; an optional (bulk) stage that times out no longer fails the tick, a required one still does."""

    def test_which_timeouts_fail_the_tick(self):
        plan = tick.PLAN
        bulk = next(step.name for step in plan if step.bulk)
        required = next(step.name for step in plan if not step.bulk)
        self.assertEqual(tick.failing_stages({bulk: "timeout"}, plan), [])
        self.assertEqual(tick.failing_stages({required: "timeout"}, plan), [required])
        self.assertEqual(tick.failing_stages({bulk: "failed"}, plan), [bulk])                 # a failure is a failure, bulk or not
        self.assertEqual(tick.failing_stages({required: "ok", bulk: "skipped"}, plan), [])


class CloudBacklogStageAlarm(unittest.TestCase):
    def test_the_backlog_stage_alarm_never_cuts_a_scheduled_batch_short(self):
        """Nov 19 2026: the stage alarm was 180 s and cloud.remaining() honours it, so every hourly batch stopped after 2 calls (budget reached) and the backlog never drained.
        The alarm must cover the batch's whole cap plus the one call still in flight."""
        seconds, _ = budget.budget_for("fit-cloud-backlog")
        self.assertGreaterEqual(seconds, cloud.BACKLOG_BUDGET_MAX + cloud.CALL_SECONDS)

    def test_a_batch_with_the_stage_alarm_armed_still_has_time_for_many_calls_after_two(self):
        seconds, _ = budget.budget_for("fit-cloud-backlog")
        previous = signal.signal(signal.SIGALRM, lambda *a: None)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            self.assertGreaterEqual(cloud.remaining(120, cloud.BACKLOG_BUDGET_MAX), cloud.CALL_SECONDS)       # 120 s in (two calls done): the next call still starts
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


if __name__ == "__main__":
    unittest.main()
