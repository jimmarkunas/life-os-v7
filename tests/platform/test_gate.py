import datetime as dt
import unittest

from lifeos.platform import gate

NOW = dt.datetime(2026, 10, 1, 17, 7, tzinfo=dt.timezone.utc)


def run(rid, started, conclusion="success", updated=None):
    return {"id": rid, "run_started_at": started, "created_at": started, "conclusion": conclusion, "updated_at": updated or started}


class Gate(unittest.TestCase):
    def test_recent_run_means_skip(self):
        self.assertEqual(gate.decide([run(1, "2026-10-01T16:47:00Z")], NOW, 9), "skip")

    def test_old_run_means_run(self):
        self.assertEqual(gate.decide([run(1, "2026-10-01T16:07:00Z")], NOW, 9), "run")

    def test_cancelled_runs_and_itself_do_not_count(self):
        self.assertEqual(gate.decide([run(9, "2026-10-01T17:07:00Z", None), run(2, "2026-10-01T16:57:00Z", "cancelled")], NOW, 9), "run")

    def test_a_dropped_slot_is_covered_by_the_next(self):
        later = NOW + dt.timedelta(minutes=20)
        self.assertEqual(gate.decide([run(1, "2026-10-01T16:07:00Z")], later, 9), "run")

    def test_stale_after_150_minutes_without_success(self):
        self.assertTrue(gate.stale([run(1, "2026-10-01T13:00:00Z", updated="2026-10-01T13:50:00Z")], NOW, 9))
        self.assertFalse(gate.stale([run(1, "2026-10-01T16:00:00Z", updated="2026-10-01T16:10:00Z")], NOW, 9))
        self.assertTrue(gate.stale([run(1, "2026-10-01T16:00:00Z", "failure")], NOW, 9))


class Tick(unittest.TestCase):
    def test_workflow_accepts_a_timer_tick_with_the_same_gate(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        text = (root / ".github/workflows/hourly.yml").read_text()
        self.assertIn("      tick:", text)
        self.assertIn("inputs.tick", text.split("PIPELINE:")[1].split("\n")[0])
        self.assertIn("TICK: ${{ inputs.tick }}", text)
        self.assertIn('os.environ.get("TICK")', (root / "lifeos/platform/gate.py").read_text())


if __name__ == "__main__":
    unittest.main()
