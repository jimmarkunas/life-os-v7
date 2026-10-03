"""Every pipeline stage resolves to a real function; the registry is the only place a stage is declared."""
import importlib
import inspect
import unittest
from unittest.mock import patch

from lifeos import run


class RegistryTests(unittest.TestCase):
    def test_every_lazy_stage_points_at_a_function_taking_limit_and_live(self):
        lazies = {name: fn for name, fn in run.STAGES.items() if hasattr(fn, "target")}
        self.assertGreater(len(lazies), 15)
        for name, fn in lazies.items():
            module, attr = fn.target
            target = getattr(importlib.import_module(module), attr, None)
            self.assertTrue(callable(target), name)
            self.assertGreaterEqual(len(inspect.signature(target).parameters), 2, name)

    def test_lazy_passes_limit_floor_live_and_fixed_arguments(self):
        calls = []
        fake = type("M", (), {"go": staticmethod(lambda limit, live, **kw: calls.append((limit, live, kw)) or {"n": 1})})
        with patch("importlib.import_module", return_value=fake):
            self.assertEqual(run.lazy("m", "go", floor=500, lane="X")(40, True), {"n": 1})
        self.assertEqual(calls, [(500, True, {"lane": "X"})])

    def test_a_failing_stage_prints_a_fixed_code_and_returns_1(self):
        with patch.dict(run.STAGES, {"probe": lambda limit, live: (_ for _ in ()).throw(run.JiraError("JIRA_NETWORK"))}):
            self.assertEqual(run.main(["probe"]), 1)


if __name__ == "__main__":
    unittest.main()
