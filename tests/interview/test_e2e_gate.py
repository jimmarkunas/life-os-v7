"""LIFE-88 E01-E16 release gate (comment 10895). e2e_gate.json is the machine-readable status: every cited test must exist and not be skipped,
a PASS needs tests and no gap, anything short of PASS names its gap, and CI never sets a live state."""
import importlib
import json
import pathlib
import unittest

GATE = json.loads((pathlib.Path(__file__).parent / "e2e_gate.json").read_text())["outcomes"]


def resolve(ref):
    module, cls, method = ref.rsplit(".", 2)
    case = getattr(importlib.import_module(module), cls)
    return case, getattr(case, method)


class Gate(unittest.TestCase):
    def test_all_sixteen_outcomes_are_listed_once(self):
        self.assertEqual(list(GATE), [f"E{n:02d}" for n in range(1, 17)])

    def test_every_cited_assertion_exists_and_runs(self):
        for key, item in GATE.items():
            for ref in item["tests"]:
                with self.subTest(key=key, ref=ref):
                    case, method = resolve(ref)
                    self.assertTrue(issubclass(case, unittest.TestCase))
                    self.assertFalse(getattr(case, "__unittest_skip__", False) or getattr(method, "__unittest_skip__", False))

    def test_a_pass_is_backed_and_every_gap_is_named(self):
        for key, item in GATE.items():
            with self.subTest(key=key):
                self.assertIn(item["machine"], ("PASS", "PARTIAL", "PENDING", "DEGRADED"))
                self.assertEqual(item["machine"] == "PASS", bool(item["tests"]) and not item["missing"])
                self.assertEqual(item["live"], "PENDING")          # set only from a real run, read-back and Jim's review in LIFE-88, never here

    def test_full_end_to_end_is_not_claimed_while_any_outcome_is_short(self):
        if any(item["machine"] != "PASS" for key, item in GATE.items() if key != "E16"):
            self.assertNotEqual(GATE["E16"]["machine"], "PASS")


if __name__ == "__main__":
    unittest.main()
