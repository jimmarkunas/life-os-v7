"""LIFE-854: the contract of .github/workflows/boards.yml, the four daily employer-board windows: schedule, caps and timeouts, credentials, the one stage, and the places that list it."""
import json
import pathlib
import re
import unittest

try:
    import yaml
except ImportError:                                           # the contract tests skip without PyYAML, as the others do
    yaml = None

from lifeos.platform import boards_window, limits

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOARDS = ROOT / ".github/workflows/boards.yml"


@unittest.skipIf(yaml is None, "PyYAML not installed")
class BoardsWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(BOARDS.read_text())
        cls.hourly = yaml.safe_load((ROOT / ".github/workflows/hourly.yml").read_text())
        cls.steps = {s.get("id"): s for s in cls.doc["jobs"]["boards"]["steps"] if s.get("id")}

    def test_four_windows_with_two_crons_each_and_manual_runs_are_dry_unless_live(self):
        crons = [c["cron"] for c in self.doc[True]["schedule"]]
        self.assertEqual(len(crons), 8)
        self.assertEqual(sorted(int(c.split()[1]) for c in crons), [0, 1, 6, 7, 12, 13, 18, 19])      # 7 PM, 1 AM, 7 AM, 1 PM Central in daylight and standard time
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertEqual((inputs["live"]["default"], inputs["mode"]["default"]), (False, "window"))

    def test_runs_take_turns_and_a_running_window_is_never_cancelled(self):
        self.assertEqual(self.doc["concurrency"], {"group": "life-os-v7-boards", "cancel-in-progress": False})
        self.assertEqual(self.doc["permissions"], {"contents": "read"})

    def test_the_gate_job_decides_and_the_crawl_job_follows_it(self):
        jobs = self.doc["jobs"]
        self.assertEqual(sorted(jobs), ["boards", "window"])
        self.assertEqual(jobs["boards"]["needs"], "window")
        self.assertIn("needs.window.outputs.run == 'true'", jobs["boards"]["if"])
        gate = next(s for s in jobs["window"]["steps"] if s.get("id") == "gate")
        self.assertEqual(gate["run"], "python3 -m lifeos.platform.boards_window")
        self.assertEqual(boards_window.CRAWL_JOB, "boards")                                            # the gate reads this job's conclusion in the history

    def test_a_window_and_a_catch_up_have_the_approved_caps_and_timeouts(self):
        window, catchup = self.steps["crawl_window"], self.steps["crawl_catchup"]
        self.assertEqual((window["timeout-minutes"], catchup["timeout-minutes"]), (35, 60))
        self.assertEqual((window["env"]["BOARDS_MODE"], catchup["env"]["BOARDS_MODE"]), ("window", "catchup"))
        self.assertIn("needs.window.outputs.mode == 'window'", window["if"])
        self.assertIn("needs.window.outputs.mode == 'catchup'", catchup["if"])
        for step in (window, catchup):
            self.assertIn("python -m lifeos.run boards --limit ${{ needs.window.outputs.limit }}", step["run"])
            self.assertIn("$LIVE", step["run"])
            self.assertNotIn("continue-on-error", step)                                               # a failed window turns the run red: the next window is a catch-up and the watchdog pages
        self.assertEqual((limits.BOARDS_PER_RUN, limits.BOARDS_CATCHUP_PER_RUN), (1800, 2900))
        self.assertEqual((boards_window.WINDOW_TIMEOUT_MINUTES, boards_window.CATCHUP_TIMEOUT_MINUTES), (35, 60))
        self.assertGreater(self.doc["jobs"]["boards"]["timeout-minutes"], 60)

    def test_no_new_secret_name_and_only_the_store_and_tailnet_credentials(self):
        text = BOARDS.read_text()
        loaded = re.findall(r"^\s+([0-9a-f-]{36}) > (\w+)$", text, re.M)
        hourly = dict((u, n) for u, n in re.findall(r"^\s+([0-9a-f-]{36}) > (\w+)$", (ROOT / ".github/workflows/hourly.yml").read_text(), re.M))
        self.assertEqual(len(loaded), 12)
        for uuid, name in loaded:
            self.assertEqual(hourly.get(uuid), name, name)                                           # the same ids and names the hourly workflow loads
        self.assertEqual(re.findall(r"secrets\.(\w+)", text), ["LIFEOS_BWS_RUNTIME_TOKEN", "LIFEOS_BWS_RUNTIME_TOKEN"])      # the one bootstrap token, for the loader and the switches reader
        self.assertNotIn("NOTION", text.replace("Notion", ""))

    def test_the_hourly_tick_no_longer_runs_the_boards_stage_but_keeps_the_backfill(self):
        from lifeos.jobs import tick
        names = [s.name for s in tick.PLAN]
        self.assertNotIn("boards", names)
        self.assertIn("boards-backfill", names)
        self.assertNotIn("lifeos.run boards --limit", (ROOT / ".github/workflows/hourly.yml").read_text())

    def test_the_table_the_production_contract_reads_lists_the_workflow_cadence_cap_and_timeout(self):
        row = json.loads((ROOT / "lifeos/platform/due_table.json").read_text())["workflows"]["boards.yml"]
        self.assertEqual(row["windows_ct"], list(f"{h:02d}:00" for h in (1, 7, 13, 19)))
        self.assertEqual((row["cap"], row["timeout_minutes"], row["catchup_cap"], row["catchup_timeout_minutes"]), (limits.BOARDS_PER_RUN, 35, limits.BOARDS_CATCHUP_PER_RUN, 60))
        self.assertEqual(sorted(row["windows_ct"]), row["windows_ct"])
        slots = [f"{h:02d}:{m:02d}" for h, m in boards_window.SLOTS_CT]
        self.assertEqual(slots, row["windows_ct"])
        self.assertTrue(row["owner"])


if __name__ == "__main__":
    unittest.main()
