"""a test file may not call the real clock (`datetime.now`, `datetime.utcnow`, `datetime.today`, `date.today`,
`time.time`) unless.
"""

import ast
import pathlib
import re
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DOC = ROOT / "docs" / "platform" / "scheduled-checks.md"
METHODS = {"now", "utcnow", "today"}
EXEMPT = {"tests/kit/clock.py"}  # the fixture itself


def _receiver(node):
    """The last name before the method: `datetime`, `dt.datetime`, `datetime.datetime`, `date`, `dt.date`, `time`."""
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def real_clock_calls(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver, method = _receiver(node.func.value), node.func.attr
            if (receiver in ("datetime", "date") and method in METHODS) or (receiver == "time" and method == "time"):
                found.append((node.lineno, f"{receiver}.{method}()"))
    return found


def allowlist():
    section = DOC.read_text(encoding="utf-8").split("### Allowlist", 1)[1]
    return {m.group(1) for m in re.finditer(r"^- `(tests/[^`]+\.py)` — \S", section, re.M)}


class FrozenClockContract(unittest.TestCase):
    def files(self):
        return sorted(
            p
            for p in TESTS.rglob("*.py")
            if "__pycache__" not in p.parts
            and "contracts" not in p.parts
            and p.relative_to(ROOT).as_posix() not in EXEMPT
        )

    def test_no_test_file_reads_the_real_clock_unless_listed(self):
        listed = allowlist()
        bad = []
        for path in self.files():
            rel = path.relative_to(ROOT).as_posix()
            calls = real_clock_calls(path)
            if calls and rel not in listed:
                bad.append(f"{rel}:{calls[0][0]} calls {calls[0][1]}")
        self.assertFalse(
            bad,
            "use tests.kit.clock (or list the file with a reason in docs/platform/scheduled-checks.md):\n"
            + "\n".join(bad),
        )

    def test_the_allowlist_only_holds_files_that_still_read_the_real_clock(self):
        stale = [rel for rel in sorted(allowlist()) if not (ROOT / rel).exists() or not real_clock_calls(ROOT / rel)]
        self.assertFalse(stale, "remove from the allowlist in docs/platform/scheduled-checks.md:\n" + "\n".join(stale))

    def test_the_detector_sees_each_form_and_ignores_the_fixture(self):
        source = (
            "import datetime, time\nfrom datetime import date\nfrom tests.kit import clock\n"
            "a = datetime.datetime.now()\nb = datetime.datetime.utcnow()\nc = date.today()\nd = time.time()\ne = datetime.datetime.today()\n"
            "ok = (clock.now(), clock.today(), clock.time())\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(source)
        try:
            self.assertEqual(
                [c for _, c in real_clock_calls(handle.name)],
                ["datetime.now()", "datetime.utcnow()", "date.today()", "time.time()", "datetime.today()"],
            )
        finally:
            pathlib.Path(handle.name).unlink()


if __name__ == "__main__":
    unittest.main()
