"""every dependency CI installs comes from a lockfile (exact versions and sha256 hashes)"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAIRS = (
    ("requirements.txt", "requirements.lock"),
    ("requirements-browser.txt", "requirements-browser.lock"),
    ("requirements-fit.txt", "requirements-fit.lock"),
    ("requirements-test.txt", "requirements-test.lock"),
)


def lock_entries(name):
    """-> {package: (version, hash count)} from a lock written as `name==version \\ --hash=sha256:...`."""
    text = (ROOT / name).read_text()
    out = {}
    for block in re.split(r"\n(?=[A-Za-z0-9_.-]+==)", "\n" + re.sub(r"^#.*\n", "", text, flags=re.M)):
        m = re.match(r"\s*([A-Za-z0-9_.-]+)==(\S+)", block)
        if m:
            out[m.group(1).lower().replace("_", "-")] = (
                m.group(2),
                len(re.findall(r"--hash=sha256:[0-9a-f]{64}", block)),
            )
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
                if re.search(
                    r"\b(pip3? install|npm (install|i)\b|pnpm install|yarn install)", line
                ) and not line.lstrip().startswith("#"):
                    found.append((path.relative_to(ROOT).as_posix(), number, line.strip()))
        self.assertTrue(found)
        for path, number, line in found:
            self.assertRegex(
                line,
                r"pip install --require-hashes -r requirements[\w-]*\.lock",
                "%s:%d is not installed from a lock" % (path, number),
            )

    def test_the_composite_actions_are_valid_yaml_and_the_python_action_installs_the_runtime_lock(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for action in (ROOT / ".github/actions").glob("*/action.yml"):
            data = yaml.safe_load(action.read_text())
            self.assertIn("runs", data, action.as_posix())
        steps = yaml.safe_load((ROOT / ".github/actions/python/action.yml").read_text())["runs"]["steps"]
        self.assertTrue(any(s.get("run") == "pip install --require-hashes -r requirements.lock" for s in steps))

    def test_every_node_package_json_has_a_committed_lockfile(self):
        for package in ROOT.rglob("package.json"):
            if "node_modules" in package.parts:
                continue
            self.assertTrue(
                (package.parent / "package-lock.json").is_file(), package.parent.relative_to(ROOT).as_posix()
            )
            json.loads((package.parent / "package-lock.json").read_text())


class Trims(unittest.TestCase):
    def test_the_pip_caches_are_keyed_on_locks(self):
        action = (ROOT / ".github/actions/python/action.yml").read_text()
        self.assertIn("path: ~/.cache/pip", action)
        self.assertIn("hashFiles('requirements.lock')", action)
        self.assertLess(action.index("Pip download cache"), action.index("Install dependencies"))
        finish = (ROOT / ".github/workflows-src/hourly/060-finish.yml").read_text()
        self.assertIn("hashFiles('requirements-fit.lock')", finish)
        self.assertLess(
            finish.index("Pip cache for the semantic layer"), finish.index("Install semantic layer")
        )

    def test_profile_sync_follows_only_the_hourly_runs_that_started_in_the_11_and_23_utc_hours(self):
        text = (ROOT / ".github/workflows/profile-sync.yml").read_text()
        self.assertIn("contains('11 23', substring(github.event.workflow_run.run_started_at, 11, 2))", text)
        # the same test in Python: of the 24 hours of a day exactly two pass, and a manual dispatch has no such condition
        started = ["2026-11-27T%02d:00:11Z" % hour for hour in range(24)]
        passing = [stamp for stamp in started if stamp[11:13] in "11 23".split(" ") or stamp[11:13] in "11 23"]
        self.assertEqual(sorted(s[11:13] for s in passing), ["11", "23"])
        dispatch_job = text.split("  sync:")[1] if "  sync:" in text else text
        self.assertIn("github.event_name == 'workflow_dispatch'", dispatch_job)


if __name__ == "__main__":
    unittest.main()
