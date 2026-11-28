"""tests/second-date-tests.txt names every test module whose result can depend on today's date: the pull
request run of `unit-tests-second-date` runs exactly those, the full suite on a second date runs on merge to main only.
The list may be longer than the rule below, never shorter."""

import pathlib
import re
import unittest

from tests.contracts.test_frozen_clock import allowlist

ROOT = pathlib.Path(__file__).resolve().parents[2]
LIST = ROOT / "tests" / "second-date-tests.txt"
CLOCK_IMPORT = re.compile(r"from tests\.kit import [^\n]*\bclock\b|tests\.kit\.clock|LIFEOS_TEST_NOW")


def listed():
    return {
        line.strip()
        for line in LIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def modules():
    return [p for p in (ROOT / "tests").rglob("test_*.py") if "contracts" not in p.parts]


def required():
    """Test modules that import the clock fixture, read the real clock (the allowlist of
    docs/platform/scheduled-checks.md), or import a helper that does."""
    real = allowlist()
    helpers = [r[:-3].replace("/", ".") for r in real if not pathlib.PurePosixPath(r).name.startswith("test_")]
    need = set()
    for path in modules():
        rel, text = path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")
        if CLOCK_IMPORT.search(text) or rel in real or any(h in text for h in helpers):
            need.add(rel)
    return need


class SecondDateList(unittest.TestCase):
    def test_the_list_holds_every_module_that_reads_the_clock_fixture_or_the_real_clock(self):
        missing = sorted(required() - listed())
        self.assertFalse(missing, "add to tests/second-date-tests.txt:\n" + "\n".join(missing))

    def test_every_entry_is_an_existing_test_module_outside_contracts(self):
        bad = [
            r
            for r in sorted(listed())
            if not r.startswith("tests/")
            or "/contracts/" in r
            or not (ROOT / r).is_file()
            or not pathlib.PurePosixPath(r).name.startswith("test_")
        ]
        self.assertFalse(bad, "not a test module:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
