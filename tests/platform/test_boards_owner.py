"""one crawler owner of the employer boards, the tick or the four windows, chosen by the BOARDS_IN_TICK switch
(lifeos/platform/switches.json).
"""

import datetime as dt
import json
import unittest
from unittest import mock

from lifeos.platform import boards_window as bw, source_registry, switches
from lifeos.sources.web import boards

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 23, 14, 5, tzinfo=UTC)
ON, OFF = {"BOARDS_IN_TICK": "on"}, {"BOARDS_IN_TICK": "off"}


class Switch(unittest.TestCase):
    def test_the_file_names_the_owner_and_the_owner_follows_it(self):
        state = json.loads(switches.FILE.read_text())["BOARDS_IN_TICK"]["state"]
        self.assertIn(state, ("on", "off"))
        self.assertEqual(bw.owner({}), "tick" if state == "on" else "windows")

    def test_a_repository_variable_flips_it_either_way(self):
        self.assertEqual((bw.owner(ON), bw.owner(OFF)), ("tick", "windows"))
        self.assertEqual(bw.owner({"BOARDS_IN_TICK": "typo"}), bw.owner({}))  # anything else defers to the file


class WindowsStandDown(unittest.TestCase):
    def test_the_scheduled_gate_skips_while_the_tick_owns_the_boards(self):
        out = []
        env = {**ON, "GITHUB_OUTPUT": ""}
        bw.main(
            environ=env, api=mock.Mock(side_effect=AssertionError("history must not be read")), now=NOW, out=out.append
        )
        self.assertIn("skip (tick_owns)", out[0])

    def test_a_manual_run_still_runs_whoever_owns_the_boards(self):
        out = []
        bw.main(
            environ={**ON, "BOARDS_EVENT": "workflow_dispatch", "BOARDS_AUTO": "false"},
            api=mock.Mock(),
            now=NOW,
            out=out.append,
        )
        self.assertIn("Decision: run", out[0])

    def test_the_tick_does_not_start_a_window_while_it_owns_the_boards(self):
        api, out = mock.Mock(), []
        self.assertEqual(bw.dispatch(environ=ON, api=api, now=NOW, out=out.append), 0)
        api.assert_not_called()
        self.assertIn("the hourly tick owns the boards", out[0])

    def test_the_gate_and_dispatch_decide_as_before_while_the_windows_own_them(self):
        api = mock.Mock(return_value={"workflow_runs": []})
        out = []
        bw.dispatch(environ=OFF, api=api, now=NOW, out=out.append)
        self.assertEqual(api.call_args_list[-1].args[:2], ("actions/workflows/boards.yml/dispatches", "POST"))


class TickStage(unittest.TestCase):
    def test_the_windows_owning_the_boards_makes_the_tick_step_a_no_op_that_opens_nothing(self):
        with mock.patch.object(boards, "run", side_effect=AssertionError("must not read")):
            counts = boards.tick(
                1800, True, environ=OFF, running=mock.Mock(side_effect=AssertionError("must not look"))
            )
        self.assertEqual((counts["owner"], counts["skipped"], counts["why"]), ("windows", True, "windows_own"))

    def test_a_queued_or_running_window_keeps_the_tick_off_the_boards(self):
        with mock.patch.object(boards, "run", side_effect=AssertionError("must not read")):
            counts = boards.tick(1800, True, environ=ON, running=lambda: True)
        self.assertEqual((counts["owner"], counts["skipped"], counts["why"]), ("tick", True, "window_running"))

    def test_with_the_tick_as_owner_it_reads_the_due_boards_with_the_window_caps_and_a_soft_deadline(self):
        seen = {}

        def fake(limit, live, now=None, **kw):
            seen.update(limit=limit, live=live, env=kw["environ"])
            return {"pass": "employer boards", "complete": 3, "failed": 0}

        with mock.patch.object(boards, "run", fake):
            counts = boards.tick(5000, True, environ=ON, running=lambda: False)
        self.assertEqual((seen["limit"], seen["live"]), (1800, True))  # never above the window cap
        self.assertEqual((seen["env"]["BOARDS_DEADLINE_MINUTES"], seen["env"]["BOARDS_MODE"]), ("4", "window"))
        self.assertEqual((counts["owner"], counts["complete"]), ("tick", 3))

    def test_the_overlap_guard_reads_boards_yml_and_fails_closed(self):
        self.assertTrue(
            bw.window_running(lambda path: {"workflow_runs": [{"status": "completed"}, {"status": "in_progress"}]})
        )
        self.assertTrue(bw.window_running(lambda path: {"workflow_runs": [{"status": "queued"}]}))
        self.assertFalse(bw.window_running(lambda path: {"workflow_runs": [{"status": "completed"}]}))
        self.assertTrue(
            bw.window_running(mock.Mock(side_effect=OSError("down")))
        )  # unreadable: a crawler may be running

    def test_the_tick_step_is_registered_hourly_in_the_ats_group_before_publish(self):
        step = next(s for s in source_registry.SOURCES if s.name == "boards-tick")
        self.assertEqual(
            (step.tick["budget"], step.tick["limit"], tuple(step.tick["providers"]), step.tick.get("bulk", False)),
            (300, 1800, ("ats",), False),
        )


class Rollback(unittest.TestCase):
    """Flipping the switch back: the windows resume from the same state tables (the pass reads the oldest due boards
    first), so a window the tick era left.
    """

    def test_after_the_tick_era_the_next_window_is_a_catch_up_when_the_last_one_did_not_complete(self):
        def run(rid, started, outcome):
            stamp = started.strftime("%Y-%m-%dT%H:%M:%SZ")
            return {
                "id": rid,
                "event": "schedule",
                "status": "completed",
                "display_title": "",
                "run_started_at": stamp,
                "created_at": stamp,
                "_job": outcome,
            }

        slot = bw.current_slot(NOW)
        runs = [run(1, bw.previous_slot(bw.previous_slot(slot)), "success")]  # the windows stopped
        plan = bw.decide(
            NOW, runs, lambda item: [{"name": "boards", "conclusion": item["_job"], "status": "completed"}]
        )
        self.assertEqual((plan["run"], plan["mode"]), (True, "catchup"))
        self.assertEqual(plan["limit"], 2900)

    def test_the_stage_reads_the_same_state_either_way(self):
        import inspect

        self.assertIn('select="known"', inspect.getsource(boards.run))  # one reader
        self.assertIn("run(", inspect.getsource(boards.tick))


if __name__ == "__main__":
    unittest.main()
