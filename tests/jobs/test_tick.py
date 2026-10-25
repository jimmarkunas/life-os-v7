import unittest
from unittest import mock

from lifeos.jobs import tick
from lifeos.jobs.tick import Step


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TickTests(unittest.TestCase):
    """LIFE-76 PR 4b (D208): one process, fixed order, the fast path first, budgets, and a failure never stops the next stage."""
    def setUp(self):
        self.calls, self.clock = [], Clock()

    def stage(self, name, result=None, cost=0, error=None):
        def run(limit, live):
            self.calls.append((name, limit, live))
            self.clock.now += cost
            if error:
                raise error
            return result or {"ok": 1}
        return run

    def test_the_plan_runs_the_fast_path_before_any_bulk_discovery(self):
        names = [s.name for s in tick.PLAN]
        fast = [names.index(n) for n in ("resolve-jobright", "resolve-linkedin", "publish", "enrich", "fit", "publish-fresh", "finalize")]
        bulk = [i for i, s in enumerate(tick.PLAN) if s.bulk]
        self.assertEqual(fast, sorted(fast))
        self.assertLess(max(fast), min(bulk))
        self.assertEqual({s.name for s in tick.PLAN if s.bulk}, {"boards", "web-scale-up", "web-titlewatch", "discover-jobs", "sponsors", "boards-backfill",
                                                                 "drain-audit-ready", "drain-publish", "drain-enrich", "drain-fit"})
        self.assertLess(names.index("finalize"), names.index("boards-backfill"))     # D217: a first pass never takes time from the hourly work
        self.assertFalse({"sweep", "extract"} & set(names))                 # the prep job sweeps and extracts on every run

    def test_the_gate_runs_just_before_each_publish_and_the_full_audit_just_after(self):
        """LIFE-76: nothing failing the guards reaches the Ledger, and the full pass (page reads and trashes) never delays it."""
        names = [s.name for s in tick.PLAN]
        self.assertEqual(names[names.index("publish") - 1], "audit-ready")
        self.assertEqual(names[names.index("publish-fresh") - 1], "audit-fresh")
        self.assertEqual(names[names.index("publish-fresh") + 1], "audit")
        from lifeos import run
        from lifeos.jobs import audit
        with mock.patch.object(audit, "run", return_value={}) as gate:
            run.STAGES["audit-fresh"](60, True)
        gate.assert_called_once_with(2000, True, ready_only=True)                   # the seconds-long READY gate, never the full pass
        self.assertEqual(run.STAGES["publish-fresh"].target, run.STAGES["publish"].target)

    def test_jobs_already_scored_are_published_before_new_descriptions_are_read(self):
        """LIFE-76 (Jim, Oct 25: publish first): the noon tick spent 2.3 of its 7.5 minutes to the first page in enrich."""
        names = [s.name for s in tick.PLAN]
        self.assertLess(names.index("publish"), names.index("enrich"))
        self.assertLess(names.index("fit"), names.index("publish-fresh"))
        self.assertFalse(any(s.bulk for s in tick.PLAN if s.name in ("audit-fresh", "publish-fresh")))
        self.assertFalse(next(s for s in tick.PLAN if s.name == "audit").bulk)      # the full pass is never skipped for lack of time

    def test_the_drain_pass_runs_last_gated_and_inside_the_ledger_caps(self):
        """LIFE-76 (Jim, Oct 25): the backlog drains in the run's spare time; nothing reaches the Ledger ungated and no call exceeds NOTION_PER_RUN."""
        from lifeos import run
        from lifeos.platform import limits
        names = [s.name for s in tick.PLAN]
        self.assertEqual(names[-4:], ["drain-audit-ready", "drain-publish", "drain-enrich", "drain-fit"])
        self.assertTrue(all(s.bulk for s in tick.PLAN[-4:]))                    # never before the hourly work, skipped when the run has no time left
        step = next(s for s in tick.PLAN if s.name == "drain-publish")
        self.assertLessEqual(step.limit, limits.NOTION_PER_RUN)
        self.assertLessEqual(step.limit * 6, step.budget)                         # twice today's ~3 s a page
        self.assertEqual(run.STAGES["drain-enrich"].target, ("lifeos.jobs.enrich", "run"))
        self.assertLessEqual(tick.QUICK_LIMITS["drain-publish"], tick.QUICK_LIMITS["publish"])

    def test_the_publish_cap_fits_its_budget(self):
        """Tick 39170290502 (Oct 18): 60 pages at about 13 s each overran the 720 s publish budget."""
        step = next(s for s in tick.PLAN if s.name == "publish")
        self.assertLessEqual(step.limit * 13, step.budget * 0.8)                      # room for a slow Notion minute

    def test_stages_run_in_order_with_their_limits_and_the_live_switch(self):
        plan = (Step("a", 10, 5), Step("b", 10, 7, bulk=True))
        out = tick.tick(True, plan=plan, stages={"a": self.stage("a"), "b": self.stage("b")}, clock=self.clock)
        self.assertEqual(out, {"a": "ok", "b": "ok"})
        self.assertEqual(self.calls, [("a", 5, True), ("b", 7, True)])

    def test_a_failing_stage_never_stops_the_next(self):
        plan = (Step("a", 10), Step("b", 10))
        out = tick.tick(False, plan=plan, stages={"a": self.stage("a", error=RuntimeError("x")), "b": self.stage("b")}, clock=self.clock)
        self.assertEqual(out, {"a": "failed", "b": "ok"})

    def test_a_stage_over_its_budget_is_stopped_and_counted(self):
        def slow(limit, live):
            raise tick.StageTimeout()                      # what the alarm raises; a stage's own `except Exception` cannot swallow it
        out = tick.tick(False, plan=(Step("a", 10), Step("b", 10)), stages={"a": slow, "b": self.stage("b")}, clock=self.clock)
        self.assertEqual(out, {"a": "timeout", "b": "ok"})
        self.assertFalse(issubclass(tick.StageTimeout, Exception))

    def test_bulk_work_is_skipped_when_the_run_has_no_time_left_but_the_fast_path_never_is(self):
        plan = (Step("a", 10), Step("big", 600, bulk=True), Step("late", 10))
        out = tick.tick(False, plan=plan, stages={"a": self.stage("a", cost=990), "big": self.stage("big"), "late": self.stage("late")},
                        clock=self.clock, budget=1000)
        self.assertEqual(out, {"a": "ok", "big": "skipped", "late": "ok"})

    def test_a_bulk_stage_gets_only_the_runs_remaining_time(self):
        alarms = []
        with mock.patch.object(tick.signal, "alarm", side_effect=alarms.append):
            tick.tick(False, plan=(Step("a", 50), Step("big", 600, bulk=True)), stages={"a": self.stage("a", cost=700), "big": self.stage("big")},
                      clock=self.clock, budget=1000)
        self.assertEqual([a for a in alarms if a], [50, 300])

    def test_a_late_fast_path_stage_still_runs_with_its_floor_so_the_run_stays_bounded(self):
        alarms = []
        with mock.patch.object(tick.signal, "alarm", side_effect=alarms.append):
            out = tick.tick(False, plan=(Step("a", 50), Step("publish", 720)), stages={"a": self.stage("a", cost=990), "publish": self.stage("publish")},
                            clock=self.clock, budget=1000)
        self.assertEqual(out, {"a": "ok", "publish": "ok"})
        self.assertEqual([a for a in alarms if a], [50, tick.FAST_FLOOR_SECONDS])

    def test_quick_publishes_few_pages(self):
        tick.tick(True, quick=True, plan=(Step("publish", 10, 60),), stages={"publish": self.stage("publish")}, clock=self.clock)
        self.assertEqual(self.calls, [("publish", 10, True)])

    def test_quick_reads_few_boards(self):
        tick.tick(True, quick=True, plan=(Step("boards", 10, 600, bulk=True),), stages={"boards": self.stage("boards")}, clock=self.clock)
        self.assertEqual(self.calls, [("boards", 50, True)])

    def test_main_exits_1_when_any_stage_failed_or_timed_out(self):
        for outcome, code in (({"a": "ok", "b": "skipped"}, 0), ({"a": "ok", "b": "failed"}, 1), ({"a": "timeout"}, 1)):
            with mock.patch.object(tick, "tick", return_value=outcome):
                self.assertEqual(tick.main(["--live"]), code)

    def test_main_prints_each_stage_line_as_it_happens(self):
        with mock.patch.object(tick, "tick", return_value={"a": "ok"}), mock.patch.object(tick.sys.stdout, "reconfigure") as reconfigure:
            tick.main([])
        reconfigure.assert_called_once_with(line_buffering=True)                   # proof run 39163729298 printed every stage's line only at exit

    def test_every_planned_stage_exists(self):
        from lifeos import run
        import importlib
        for step in tick.PLAN:
            if step.module:
                self.assertTrue(callable(importlib.import_module(step.module).main), step.name)
            else:
                self.assertIn(step.name, run.STAGES)


if __name__ == "__main__":
    unittest.main()
