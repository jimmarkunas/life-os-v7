"""a selected run is fanned out over 1 to 4 test jobs sized by measured seconds; a FULL pick over 8 jobs of shards."""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from tests import shard, splitsel

WEIGHTS = {"tests.a.test_big": 30.0, "tests.a.test_mid": 9.0, "tests.a.test_small": 0.5, "tests.b": 4.0}


class Plan(unittest.TestCase):
    def test_a_full_pick_is_eight_jobs_of_shards_and_no_groups(self):
        self.assertEqual(splitsel.plan(["FULL"], WEIGHTS), (list(range(1, 9)), []))
        self.assertEqual(splitsel.plan([], WEIGHTS), (list(range(1, 9)), []))

    def test_a_small_selection_is_one_job(self):
        self.assertEqual(
            splitsel.plan(["tests.a.test_mid", "tests.a.test_small"], WEIGHTS),
            ([1], ["tests.a.test_mid tests.a.test_small"]),
        )

    def test_a_heavy_selection_gets_up_to_four_jobs_and_every_target_exactly_once(self):
        targets = [
            "tests.a.test_big",
            "tests.a.test_mid",
            "tests.a.test_small",
            "tests.b.test_x",
            "tests.c.test_unknown",
        ]
        jobs, groups = splitsel.plan(targets, WEIGHTS)
        self.assertEqual(jobs, list(range(1, len(groups) + 1)))
        self.assertTrue(1 < len(groups) <= 4)
        self.assertEqual(sorted(" ".join(groups).split()), sorted(targets))
        self.assertEqual(splitsel.weight("tests.b.test_x", WEIGHTS), 4.0)  # the longest listed prefix
        self.assertEqual(splitsel.weight("tests.c.test_unknown", WEIGHTS), splitsel.UNKNOWN)

    def test_never_more_jobs_than_targets_or_the_cap(self):
        many = ["tests.a.test_big"] * 1 + ["tests.m%d.test_x" % i for i in range(20)]
        self.assertLessEqual(len(splitsel.plan(many, WEIGHTS)[0]), splitsel.MAX_JOBS)
        self.assertEqual(splitsel.plan(["tests.a.test_big"], WEIGHTS)[0], [1])

    def test_the_command_prints_the_two_output_lines_and_skips_the_reason_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "selected.txt")
            path.write_text("FULL\nFULL because a lock file changed\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(splitsel.main([str(path)]), 0)
            lines = out.getvalue().splitlines()
            rows = json.loads(lines[0].split("=", 1)[1])["include"]
            self.assertEqual(len(lines), 1)
            self.assertEqual([r["job"] for r in rows], list(range(1, 9)))
            self.assertEqual({r["group"] for r in rows}, {""})
            path.write_text("tests.workflows.test_workflow\ntests.edge.test_x\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                splitsel.main([str(path)])
            rows = json.loads(out.getvalue().split("=", 1)[1])["include"]
            jobs, groups = [r["job"] for r in rows], [r["group"] for r in rows]
            self.assertEqual(sorted(" ".join(groups).split()), ["tests.workflows.test_workflow", "tests.edge.test_x"])
            self.assertEqual(len(jobs), len(groups))

    def test_the_real_contracts_selection_fits_the_cap(self):
        contracts = sorted(m for m in shard.load_weights() if m.startswith("tests.contracts."))
        jobs, groups = splitsel.plan(contracts, shard.load_weights())
        self.assertEqual((len(jobs), len(groups)), (splitsel.MAX_JOBS,) * 2)  # about 50 s of contracts: 4 jobs


if __name__ == "__main__":
    unittest.main()
