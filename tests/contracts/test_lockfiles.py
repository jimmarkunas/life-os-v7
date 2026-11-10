"""LIFE-536: every dependency CI installs comes from a lockfile (exact versions and sha256 hashes); nothing is installed unpinned."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAIRS = (("requirements.txt", "requirements.lock"), ("requirements-browser.txt", "requirements-browser.lock"), ("requirements-fit.txt", "requirements-fit.lock"), ("requirements-test.txt", "requirements-test.lock"))


def lock_entries(name):
    """-> {package: (version, hash count)} from a lock written as `name==version \\ --hash=sha256:...`."""
    text = (ROOT / name).read_text()
    out = {}
    for block in re.split(r"\n(?=[A-Za-z0-9_.-]+==)", "\n" + re.sub(r"^#.*\n", "", text, flags=re.M)):
        m = re.match(r"\s*([A-Za-z0-9_.-]+)==(\S+)", block)
        if m:
            out[m.group(1).lower().replace("_", "-")] = (m.group(2), len(re.findall(r"--hash=sha256:[0-9a-f]{64}", block)))
    return out


class Locks(unittest.TestCase):
    def test_every_requirements_file_has_a_lock_with_hashes_that_keeps_its_direct_pins(self):
        for source, lock in PAIRS:
            self.assertTrue((ROOT / lock).is_file(), lock)
            entries = lock_entries(lock)
            self.assertTrue(entries, lock)
            for package, (version, hashes) in entries.items():
                self.assertGreaterEqual(hashes, 1, "%s: %s has no hash" % (lock, package))
            for line in (ROOT / source).read_text().splitlines():
                m = re.match(r"\s*([A-Za-z0-9_.-]+)==(\S+)", line)
                self.assertIsNotNone(m, "%s: every direct requirement is pinned exactly: %r" % (source, line))
                name = m.group(1).lower().replace("_", "-")
                self.assertEqual(entries[name][0], m.group(2), "%s: lock version differs from %s" % (lock, source))

    def test_no_install_line_in_the_workflows_is_unpinned(self):
        found = []
        for path in list((ROOT / ".github").rglob("*.yml")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"\b(pip3? install|npm (install|i)\b|pnpm install|yarn install)", line) and not line.lstrip().startswith("#"):
                    found.append((path.relative_to(ROOT).as_posix(), number, line.strip()))
        self.assertTrue(found)
        for path, number, line in found:
            self.assertRegex(line, r"pip install --require-hashes -r requirements[\w-]*\.lock", "%s:%d is not installed from a lock" % (path, number))

    def test_every_node_package_json_has_a_committed_lockfile(self):
        for package in ROOT.rglob("package.json"):
            if "node_modules" in package.parts:
                continue
            self.assertTrue((package.parent / "package-lock.json").is_file(), package.parent.relative_to(ROOT).as_posix())
            json.loads((package.parent / "package-lock.json").read_text())


if __name__ == "__main__":
    unittest.main()
