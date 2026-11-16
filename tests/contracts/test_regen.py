"""LIFE-726 / LIFE-765: tools/regen.py finds nothing to add or drop (the registries are current), adds the rows a new stage needs, and with --commit (what preflight runs) commits exactly those files."""
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import regen  # noqa: E402

from lifeos import run  # noqa: E402


class Regen(unittest.TestCase):
    def check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = regen.main(["--check"])
        return code, out.getvalue()

    def test_the_registries_are_current(self):
        code, out = self.check()
        self.assertEqual((code, out.strip()), (0, "registries are current"), "run python tools/regen.py")

    def test_a_new_lazy_stage_adds_one_manifest_entry_and_is_rendered_into_all_three_stage_files(self):
        with mock.patch.dict(run.STAGES, {"brand-new": run.lazy("lifeos.accountability.changelog")}):
            code, out = self.check()
        self.assertEqual(code, 1)
        for table in ("stages.json: added brand-new", "stage_budgets.json: rendered from stages.json", "timeouts.md: added brand-new", "stage-isolation.md: added brand-new"):
            self.assertIn(table, out)

    def test_commit_mode_refuses_a_dirty_tree_and_a_check_run(self):
        dirty = mock.Mock(stdout=" M a.txt\n", stderr="", returncode=0)
        with mock.patch.object(regen, "git", return_value=dirty), redirect_stdout(io.StringIO()):
            self.assertEqual(regen.main(["--commit"]), 2)
        clean = mock.Mock(stdout="", stderr="", returncode=0)
        with mock.patch.object(regen, "git", return_value=clean), redirect_stdout(io.StringIO()):
            self.assertEqual(regen.main(["--commit", "--check"]), 2)

    def test_the_commit_adds_the_generated_files_by_name_and_nothing_else(self):
        calls = []

        def fake_git(*args):
            calls.append(args)
            return mock.Mock(stdout="life/LIFE-9/x\n", stderr="", returncode=0)

        with mock.patch.object(regen, "git", fake_git), redirect_stdout(io.StringIO()):
            self.assertEqual(regen.commit_generated(["stages.json: added x"]), 0)
        wanted = [str(path.relative_to(regen.ROOT)) for path in regen.targets()]
        added = next(call for call in calls if call[0] == "add")
        committed = next(call for call in calls if call[0] == "commit")
        self.assertEqual(list(added[2:]), wanted)
        self.assertEqual(list(committed[-len(wanted):]), wanted)
        self.assertIn("[LIFE-9]", committed[3])


if __name__ == "__main__":
    unittest.main()
