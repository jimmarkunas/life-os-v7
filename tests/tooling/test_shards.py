import io
import json
import os
import re
import unittest
from unittest import mock

from tests import shard


class ShardsPartitionTheSuite(unittest.TestCase):
    """The PR check runs `python -m tests.shard 1 2` and `2 2` side by side."""

    def ids(self, k, n):
        suite, total = shard.shard_suite(k, n)
        return [t.id() for t in shard.tests_of(suite)], total

    def test_every_test_runs_in_exactly_one_of_two_shards(self):
        first, total = self.ids(1, 2)
        second, _ = self.ids(2, 2)
        self.assertEqual(len(first) + len(second), total)
        self.assertEqual(set(first) & set(second), set())
        everything = {
            t.id() for t in shard.tests_of(unittest.defaultTestLoader.discover("tests", top_level_dir=str(shard.ROOT)))
        }
        self.assertEqual(set(first) | set(second), everything)

    def test_each_shard_is_substantial_and_this_file_is_in_one(self):
        first, total = self.ids(1, 2)
        second, _ = self.ids(2, 2)
        self.assertGreater(min(len(first), len(second)), total // 3)
        self.assertTrue(any("test_shards" in i for i in first + second))

    def test_a_module_is_never_split_across_shards(self):
        first, _ = self.ids(1, 2)
        second, _ = self.ids(2, 2)
        modules = lambda ids: {i.rsplit(".", 2)[0] for i in ids}
        self.assertEqual(modules(first) & modules(second), set())

    def test_loading_by_module_name_gives_the_same_split_and_every_test_exactly_once_in_32_shards(self):
        """CI runs shard K of 32 per process; each loads only its own modules, never the whole suite."""
        seen = []
        for k in range(1, 33):
            seen.extend(t.id() for t in shard.tests_of(shard.fast_suite(k, 32)))
        everything = [
            t.id() for t in shard.tests_of(unittest.defaultTestLoader.discover("tests", top_level_dir=str(shard.ROOT)))
        ]
        self.assertEqual(len(seen), len(set(seen)))  # no test twice
        self.assertEqual(set(seen), set(everything))  # none missing
        full, _ = self.ids(5, 32)
        self.assertEqual(sorted(full), sorted(t.id() for t in shard.tests_of(shard.fast_suite(5, 32))))

    def test_bad_arguments_are_refused(self):
        for argv in (["0", "2"], ["3", "2"]):
            with self.assertRaises(SystemExit):
                shard.main(argv)


@unittest.skipIf(
    os.environ.get("SHARD_MEASURING"), "the weights file is being rewritten by `python -m tests.shard --measure`"
)
class ShardWeights(unittest.TestCase):
    """the split is balanced by measured seconds (tests/shard-weights.json)."""

    def modules(self):
        tests = list(shard.tests_of(unittest.defaultTestLoader.discover("tests", top_level_dir=str(shard.ROOT))))
        return set(shard.modules_in(tests))

    def test_the_weights_file_lists_only_real_test_modules_with_real_times(self):
        weights = shard.load_weights()
        self.assertTrue(
            weights, "tests/shard-weights.json is missing: run `python -m tests.shard --measure` and commit it"
        )
        self.assertEqual(shard.check_weights(weights, self.modules()), [])

    def test_the_file_has_no_dates_and_no_paths_and_is_sorted(self):
        text = shard.WEIGHTS.read_text()
        data = json.loads(text)
        self.assertEqual(set(data), {"v", "seconds"})
        self.assertEqual(list(data["seconds"]), sorted(data["seconds"]))
        self.assertFalse(re.search(r"\d{4}-\d\d-\d\d|/Users/|/home/|/tmp/", text))

    def test_negative_control_a_module_that_does_not_exist_is_reported(self):
        problems = shard.check_weights(
            {"tests.nothing.test_gone": 3.0, "tests.tooling.test_shards": 1.0, "tests.contracts.test_x": -1},
            {"tests.tooling.test_shards", "tests.contracts.test_x"},
        )
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("test_gone" in p for p in problems) and any("bad time" in p for p in problems))

    def test_a_module_missing_from_the_weights_is_still_placed_exactly_once(self):
        modules = self.modules()
        weights = {m: v for m, v in shard.load_weights().items() if m != "tests.tooling.test_shards"}
        where = shard.assignment(modules, 2, weights)
        self.assertEqual(set(where), modules)
        self.assertIn(where["tests.tooling.test_shards"], (0, 1))
        self.assertEqual(where, shard.assignment(modules, 2, weights))  # deterministic for the same tree

    def test_without_a_weights_file_the_older_split_still_works(self):
        modules = self.modules()
        where = shard.assignment(modules, 2, weights={})
        with mock.patch.object(shard, "load_weights", return_value={}):
            self.assertEqual(shard.assignment(modules, 2), where)
        self.assertEqual(set(where), modules)

    def test_the_weights_balance_two_shards_within_15_percent(self):
        weights = shard.load_weights()
        modules = self.modules()
        where = shard.assignment(modules, 2)
        load = [0.0, 0.0]
        for name, i in where.items():
            load[i] += weights.get(name, 0.0)
        self.assertLess(abs(load[0] - load[1]) / max(load), 0.15, load)

    def test_measure_times_each_module_on_its_own_and_merge_keeps_the_fastest(self):
        class Quick(unittest.TestCase):
            def test_a(self):
                pass

        Quick.__module__ = "tests.fake.test_quick"
        ticks = iter([0, 2.0])
        first = shard.measure([Quick("test_a")], clock=lambda: next(ticks))
        self.assertEqual(first, {"tests.fake.test_quick": 2.0})
        self.assertEqual(
            shard.merge([first, {"tests.fake.test_quick": 0.5, "tests.fake.test_other": 1.234}]),
            {"tests.fake.test_other": 1.23, "tests.fake.test_quick": 0.5},
        )

    def test_a_shard_reports_seconds_per_module_and_weights_can_be_built_from_the_logs(self):
        """each CI shard prints its measured seconds per module, so the split can be balanced on CI time (not a
        laptop's).
        """

        class A(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass

        class B(unittest.TestCase):
            def test_c(self):
                pass

        A.__module__, B.__module__ = "tests.fake.test_a", "tests.fake.test_b"
        ticks = iter([10.0, 11.0, 14.0, 17.0])  # three test starts, then the end of the run
        with mock.patch.object(shard.time, "perf_counter", lambda: next(ticks)):
            result = shard.TimedResult(io.StringIO(), True, 0)
            result.startTestRun()
            for test in (A("test_a"), A("test_b"), B("test_c")):
                result.startTest(test)
                result.stopTest(test)
            result.stopTestRun()
        self.assertEqual(
            {m: round(v, 1) for m, v in result.module_seconds.items()},
            {"tests.fake.test_a": 4.0, "tests.fake.test_b": 3.0},
        )  # the span of each module
        lines = shard.timing_report(
            1, 3, {"tests.fake.test_a": 4.0, "tests.fake.test_b": 3.0, "tests.fake.test_tiny": 0.01}
        )
        self.assertIn("shard 1/3 took 7.0 s", lines[0])
        self.assertEqual(
            json.loads(lines[1][len("shard-seconds ") :]),
            {"k": 1, "n": 3, "rest": 0.01, "seconds": {"tests.fake.test_a": 4.0, "tests.fake.test_b": 3.0}},
        )
        weights = shard.weights_from_logs(
            [lines[1], lines[1].replace("4.0", "6.0")],
            {"tests.fake.test_a", "tests.fake.test_b", "tests.fake.test_tiny", "tests.fake.test_new"},
        )
        self.assertEqual(
            weights,
            {
                "tests.fake.test_a": 5.0,
                "tests.fake.test_b": 3.0,
                "tests.fake.test_tiny": 0.01,
                "tests.fake.test_new": 0.01,
            },
        )  # the mean over logs

    def test_a_failing_module_stops_the_measurement(self):
        class Broken(unittest.TestCase):
            def test_b(self):
                self.fail("x")

        Broken.__module__ = "tests.fake.test_broken"
        with self.assertRaises(SystemExit):
            shard.measure([Broken("test_b")])
