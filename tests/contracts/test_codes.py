import functools
import unittest
from pathlib import Path

from lifeos.jobs.fit import edge
from lifeos.platform import codes

ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def emitted(root=ROOT / "lifeos"):
    """Every code-shaped string the code can emit: {code: 'path:line'}."""
    found = {}
    for path in sorted(root.rglob("*.py")):
        for code, line in codes.candidates(path.read_text()).items():
            found.setdefault(code, f"{path.relative_to(root.parent)}:{line}")
    return found


def unregistered(found):
    return {code: where for code, where in found.items() if code not in codes.CODES and code not in codes.NOT_CODES}


class RegistryShape(unittest.TestCase):
    def test_every_entry_is_complete_and_valid(self):
        for code, entry in codes.CODES.items():
            with self.subTest(code=code):
                self.assertRegex(code, codes.CANDIDATE)
                self.assertIn(entry.klass, codes.CLASSES)
                self.assertIn(entry.severity, codes.SEVERITIES)
                self.assertIsInstance(entry.attempt, bool)
                self.assertTrue(entry.stage and entry.meaning.strip() and entry.action.strip())

    def test_a_code_is_never_both_registered_and_a_config_name(self):
        self.assertFalse(set(codes.CODES) & codes.NOT_CODES)

    def test_no_code_alerts_until_jim_turns_one_on(self):
        self.assertEqual({c for c, e in codes.CODES.items() if e.severity == "page"}, {"FIT_EDGE_REPLAY_ACCEPTED"})


class EveryCodeIsRegistered(unittest.TestCase):
    def test_every_emitted_code_is_registered_or_a_config_name(self):
        missing = unregistered(emitted())
        self.assertEqual(missing, {}, "register each new code in lifeos/platform/codes.py (stage, class, attempt, meaning, action, severity); a config or environment name goes in NOT_CODES")

    def test_the_check_turns_red_for_an_unregistered_code(self):
        found = {c: "x.py:1" for c in codes.candidates('def f():\n    raise Error("BRAND_NEW_UNREGISTERED_CODE")\n')}
        self.assertEqual(set(unregistered(found)), {"BRAND_NEW_UNREGISTERED_CODE"})
        self.assertEqual(unregistered({"FIT_EDGE_TIMEOUT": "x.py:1"}), {})

    def test_docstrings_are_not_codes(self):
        self.assertEqual(codes.candidates('"""Raises SOME_FAKE_CODE."""\nx = "REAL_CODE"\n'), {"REAL_CODE": 2})


class FitFamiliesBuiltFromStrings(unittest.TestCase):
    def test_every_edge_status_and_refusal_reason_fit_reports_is_registered(self):
        for reason in edge.REASONS:
            self.assertIn("FIT_EDGE_REFUSED_" + reason, codes.CODES)
        for status in edge.STATUSES:
            self.assertIn("FIT_EDGE_" + status, codes.CODES)
        self.assertIn("FIT_EDGE_OFF_CONTRACT", codes.CODES)


if __name__ == "__main__":
    unittest.main()
