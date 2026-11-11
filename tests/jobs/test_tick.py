import unittest
from unittest import mock

from lifeos.jobs import tick
from lifeos.jobs.tick import Step
from tests.kit import shared


Clock = shared.Clock


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


class StageSeconds(unittest.TestCase):
    """LIFE-569: one line per stage with its seconds, and a summary table; nothing about what a stage found or saved changes."""
    def test_each_stage_prints_its_outcome_and_seconds_and_fills_the_timings(self):
        import io
        from contextlib import redirect_stdout
        ticks = iter([0, 0, 0, 12, 12, 12, 40])
        clock = lambda: next(ticks)                                                   # noqa: E731
        out, timings = io.StringIO(), {}
        with redirect_stdout(out):
            result = tick.tick(False, plan=(Step("a", 100), Step("b", 100)), stages={"a": lambda limit, live: 0, "b": lambda limit, live: 0}, clock=clock, timings=timings)
        self.assertEqual(result, {"a": "ok", "b": "ok"})
        self.assertEqual(timings, {"a": 12, "b": 28})
        self.assertEqual(out.getvalue().splitlines(), ["stage a: ok 12.0s", "stage b: ok 28.0s"])

    def test_a_failing_stage_still_reports_its_seconds_and_the_next_stage_runs(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        steps = iter(range(0, 100, 5))
        out, timings = io.StringIO(), {}

        def boom(limit, live):
            raise RuntimeError("x")
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            result = tick.tick(False, plan=(Step("a", 100), Step("b", 100)), stages={"a": boom, "b": lambda limit, live: 0}, clock=lambda: next(steps), timings=timings)
        self.assertEqual(result, {"a": "failed", "b": "ok"})
        self.assertEqual([line.split(" ")[:3] for line in out.getvalue().splitlines()], [["stage", "a:", "failed"], ["stage", "b:", "ok"]])

    def test_the_summary_table_lists_every_stage_with_seconds_and_share_and_a_total(self):
        table = tick.summary_table({"a": "ok", "b": "skipped"}, {"a": 30.0}, 120.0)
        self.assertIn("| a | ok | 30.0 | 25% |", table)
        self.assertIn("| b | skipped | 0.0 | 0% |", table)
        self.assertIn("| **total** | | **120.0** | |", table)
        self.assertIn("-", tick.summary_table({"a": "ok"}, {"a": 1.0}, 0).splitlines()[4])           # no division by zero



class SideBySide(unittest.TestCase):
    """LIFE-569: consecutive independent stages run as threads of the one process when TICK_CONCURRENCY=on; caps, isolation, order between groups and the results stay as they are in a sequential run."""
    def gate(self):
        import threading
        state = {"now": 0, "max": 0, "lock": threading.Lock(), "log": []}

        def stage(name, pause=0.05, error=None, result=0):
            def run(limit, live):
                import time
                with state["lock"]:
                    state["now"] += 1
                    state["max"] = max(state["max"], state["now"])
                    state["log"].append(("start", name))
                try:
                    time.sleep(pause)
                    if error:
                        raise error
                    return result
                finally:
                    with state["lock"]:
                        state["now"] -= 1
                        state["log"].append(("end", name))
            return run
        return state, stage

    def run_tick(self, plan, stages, **conc):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            result = tick.tick(False, plan=plan, stages=stages, concurrency={"caps": {"linkedin": 1, "tinyfish": 1, "ats": 8}, "parallel": 4, **conc})
        return result, out.getvalue()

    def test_concurrency_is_off_unless_the_variable_says_on_and_a_bad_cap_falls_back_to_the_safe_default(self):
        for environ in ({}, {"TICK_CONCURRENCY": "off"}, {"TICK_CONCURRENCY": "yes"}, {"TICK_CONCURRENCY": ""}):
            self.assertIsNone(tick.concurrency_from_env(environ), environ)
        on = tick.concurrency_from_env({"TICK_CONCURRENCY": "on"})
        self.assertEqual(on, {"caps": {"linkedin": 1, "tinyfish": 1, "jobright": 1, "dice": 1, "ats": 8}, "parallel": 4})
        odd = tick.concurrency_from_env({"TICK_CONCURRENCY": " ON ", "TICK_CAP_LINKEDIN": "0", "TICK_CAP_TINYFISH": "99", "TICK_CAP_ATS": "x", "TICK_CAP_DICE": "3", "TICK_MAX_PARALLEL": "2"})
        self.assertEqual((odd["caps"]["linkedin"], odd["caps"]["tinyfish"], odd["caps"]["ats"], odd["caps"]["dice"], odd["parallel"]), (1, 1, 8, 3, 2))      # junk never raises a cap

    def test_stages_of_one_group_run_side_by_side_and_a_service_cap_of_one_keeps_two_stages_in_turn(self):
        state, stage = self.gate()
        plan = (Step("a", 20, group="G", providers=("jobright",)), Step("b", 20, group="G", providers=("dice",)), Step("c", 20, group="G", providers=("ats",)))
        result, _ = self.run_tick(plan, {n: stage(n) for n in "abc"}, caps={"jobright": 1, "dice": 1, "ats": 8})
        self.assertEqual((result, state["max"]), ({"a": "ok", "b": "ok", "c": "ok"}, 3))
        state, stage = self.gate()
        plan = (Step("a", 20, group="G", providers=("linkedin",)), Step("b", 20, group="G", providers=("linkedin",)), Step("c", 20, group="G"))
        result, _ = self.run_tick(plan, {n: stage(n) for n in "abc"})
        self.assertEqual((result, state["max"]), ({"a": "ok", "b": "ok", "c": "ok"}, 2))                  # a and b share LinkedIn (cap 1): never both; c runs beside either

    def test_the_stage_thread_limit_is_honoured(self):
        state, stage = self.gate()
        plan = tuple(Step(n, 20, group="G") for n in "abcd")
        self.run_tick(plan, {n: stage(n) for n in "abcd"}, parallel=2)
        self.assertEqual(state["max"], 2)

    def test_a_stage_that_fails_is_one_red_stage_and_the_others_finish(self):
        state, stage = self.gate()
        plan = (Step("a", 20, group="G"), Step("b", 20, group="G"), Step("c", 20, group="G"))
        result, out = self.run_tick(plan, {"a": stage("a", error=RuntimeError("x")), "b": stage("b"), "c": stage("c")})
        self.assertEqual(result, {"a": "failed", "b": "ok", "c": "ok"})
        self.assertEqual(sorted(line.split(":")[0] for line in out.splitlines() if line.startswith("stage ")), ["stage a", "stage b", "stage c"])

    def test_a_stage_over_its_own_budget_is_stopped_and_counted_while_the_others_carry_on(self):
        import time
        state, stage = self.gate()

        def stuck(limit, live):
            while True:
                time.sleep(0.01)
        plan = (Step("slow", 1, group="G"), Step("b", 20, group="G"))
        began = time.monotonic()
        result, _ = self.run_tick(plan, {"slow": stuck, "b": stage("b", pause=0.1)})
        self.assertEqual(result, {"slow": "timeout", "b": "ok"})
        self.assertLess(time.monotonic() - began, 5)

    def test_the_results_equal_a_sequential_run_and_the_order_between_groups_and_plain_steps_is_kept(self):
        state, stage = self.gate()
        plan = (Step("a1", 20, group="A"), Step("a2", 20, group="A"), Step("plain", 20), Step("b1", 20, group="B"), Step("b2", 20, group="B"))
        stages = {n: stage(n, result=0) for n in ("a1", "a2", "plain", "b1", "b2")}
        concurrent, _ = self.run_tick(plan, stages)
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            sequential = tick.tick(False, plan=plan, stages=stages)
        self.assertEqual(concurrent, sequential)
        self.assertEqual(list(concurrent), ["a1", "a2", "plain", "b1", "b2"])                          # plan order, whatever order they finished in
        first = state["log"][:10]
        starts = [n for kind, n in first if kind == "start"]
        self.assertEqual(starts[2], "plain")                                                          # plain starts only after both A stages ended
        self.assertTrue({("end", "a1"), ("end", "a2")} <= set(first[:5]))

    def test_a_bulk_group_stage_is_skipped_when_the_run_has_no_time_left(self):
        state, stage = self.gate()
        plan = (Step("b1", 20, bulk=True, group="B"), Step("b2", 20, bulk=True, group="B"))
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            result = tick.tick(False, plan=plan, stages={"b1": stage("b1"), "b2": stage("b2")}, budget=30, concurrency={"caps": {}, "parallel": 4})
        self.assertEqual(result, {"b1": "skipped", "b2": "skipped"})

    def test_off_means_the_old_sequential_path_even_for_grouped_steps(self):
        state, stage = self.gate()
        plan = (Step("a", 20, group="G"), Step("b", 20, group="G"))
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            tick.tick(False, plan=plan, stages={"a": stage("a"), "b": stage("b")})
        self.assertEqual(state["max"], 1)

    def test_the_real_plan_groups_only_the_independent_stages_and_keeps_publish_after_the_inputs(self):
        names = [s.name for s in tick.PLAN]
        group = lambda g: [s.name for s in tick.PLAN if s.group == g]                                  # noqa: E731
        self.assertEqual(group("A"), ["resolve-jobright", "resolve-linkedin", "linkedin-search", "resolve-dice", "web"])
        self.assertEqual(group("B"), ["boards", "web-scale-up", "web-titlewatch", "discover-jobs", "sponsors"])
        for g in ("A", "B"):                                                                           # a group is one unbroken run of the plan
            where = [names.index(n) for n in group(g)]
            self.assertEqual(where, list(range(where[0], where[0] + len(where))))
        for name in ("audit-ready", "publish", "enrich", "fit", "publish-fresh", "audit", "finalize", "drain-publish", "drain-enrich", "drain-fit"):
            self.assertEqual(next(s for s in tick.PLAN if s.name == name).group, "")                   # Notion, Fit and the ordered chain never run side by side
        self.assertEqual(next(s for s in tick.PLAN if s.name == "discover-jobs").providers, ("tinyfish", "linkedin"))
        for pair in (("resolve-linkedin", "linkedin-search"),):
            self.assertTrue(all("linkedin" in next(s for s in tick.PLAN if s.name == n).providers for n in pair))


class AtsSlots(unittest.TestCase):
    """LIFE-569: one global limit on board reads across the stages that read boards, so side by side never means more at once than a single stage's pool does today."""
    def test_reads_in_many_threads_never_exceed_the_slots_and_unset_means_no_extra_limit(self):
        import threading
        import time
        from lifeos.platform import read_slots
        from lifeos.sources.web import lister, run as web_run
        seen = {"now": 0, "max": 0, "lock": threading.Lock()}

        def reader(source):
            with seen["lock"]:
                seen["now"] += 1
                seen["max"] = max(seen["max"], seen["now"])
            time.sleep(0.03)
            with seen["lock"]:
                seen["now"] -= 1
            return lister.Listing(lister.COMPLETE, [])
        for slots, ceiling in ((2, 2), (None, 12)):
            seen.update(now=0, max=0)
            read_slots.configure("ats", slots)
            try:
                call = web_run.bounded(reader, 5)
                threads = [threading.Thread(target=call, args=({"id": i},)) for i in range(12)]
                [t.start() for t in threads]
                [t.join() for t in threads]
            finally:
                read_slots.configure("ats", None)
            self.assertLessEqual(seen["max"], ceiling)
            if slots is None:
                self.assertGreater(seen["max"], 2)
            else:
                self.assertEqual(seen["max"], 2)
