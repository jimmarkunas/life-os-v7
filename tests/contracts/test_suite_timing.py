"""LIFE-705: the suite-timing report. Nearest-rank percentiles, the step seconds from the Actions job record, shard seconds from the log lines, and a failing verdict when any run reaches 120 s."""
import unittest

from tests import suite_timing as T


class SuiteTimingReport(unittest.TestCase):
    def test_stats_are_min_median_nearest_rank_p95_and_max(self):
        self.assertEqual(T.stats([100, 90, 110, 95, 105, 99, 101, 98, 97, 120]), {"runs": 10, "min": 90, "median": 99.5, "p95": 120, "max": 120})
        self.assertEqual(T.stats([88]), {"runs": 1, "min": 88, "median": 88, "p95": 88, "max": 88})
        self.assertEqual(T.percentile(list(range(1, 21)), 95), 19)

    def test_step_seconds_come_from_the_named_step_and_an_unfinished_step_is_none(self):
        job = {"steps": [{"name": "Checkout", "started_at": "2026-11-14T21:00:00Z", "completed_at": "2026-11-14T21:00:01Z"},
                         {"name": "Full suite (the shards pr-tests runs)", "started_at": "2026-11-14T21:00:10Z", "completed_at": "2026-11-14T21:01:42Z"}]}
        self.assertEqual(T.step_seconds(job), 92)
        self.assertIsNone(T.step_seconds({"steps": [{"name": "Full suite", "started_at": "2026-11-14T21:00:10Z", "completed_at": None}]}))
        self.assertIsNone(T.step_seconds({"steps": []}))

    def test_shard_seconds_are_read_from_log_lines_with_or_without_a_timestamp(self):
        log = "2026-11-14T21:00:00.1Z shard 1/3 took 61.2 s; slowest modules: a 5.0s\nshard 2/3 took 58.9 s; slowest modules: b 4.0s\nshard 1/3: 100 of 300 tests\nshard-seconds {}\n"
        self.assertEqual(T.shard_times(log), {1: 61.2, 2: 58.9})

    def test_the_report_fails_when_any_run_reaches_the_limit_and_says_what_it_measures(self):
        rows = [{"run": 1, "seconds": 95, "shards": {1: 93.0, 2: 80.0}}, {"run": 2, "seconds": 119, "shards": {1: 117.0, 2: 90.0}}]
        text, ok = T.report(rows)
        self.assertTrue(ok)
        self.assertIn("min 95, median 107, p95 119, max 119 (limit 120)", text)
        self.assertIn("runner-to-runner spread", text)
        self.assertIn("13, 27", text)                                                    # slowest minus fastest shard, per run
        text, ok = T.report(rows + [{"run": 3, "seconds": 120, "shards": {}}])
        self.assertFalse(ok)
        self.assertEqual(T.report([{"run": 1, "seconds": None, "shards": {}}])[1], False)


if __name__ == "__main__":
    unittest.main()
