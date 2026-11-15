"""LIFE-726: tools/regen.py finds nothing to add or drop (the registries are current), and it does add the rows a new stage needs."""
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

    def test_a_new_lazy_stage_is_reported_in_all_three_stage_tables(self):
        with mock.patch.dict(run.STAGES, {"brand-new": run.lazy("lifeos.accountability.changelog")}):
            code, out = self.check()
        self.assertEqual(code, 1)
        for table in ("stage_budgets.json: added brand-new", "timeouts.md: added brand-new", "stage-isolation.md: added brand-new"):
            self.assertIn(table, out)


if __name__ == "__main__":
    unittest.main()
