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
    ("requirements-yaml.txt", "requirements-yaml.lock"),
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
        self.assertLess(finish.index("Pip cache for the semantic layer"), finish.index("Install semantic layer"))

    def test_the_whole_environment_cache_is_opt_in_keyed_on_both_locks_and_never_skips_the_hash_check(self):
        action = (ROOT / ".github/actions/python/action.yml").read_text()
        self.assertIn('default: "false"', action)  # every other workflow keeps the plain install
        self.assertIn("hashFiles('requirements.lock', 'requirements-test.lock')", action)
        self.assertIn("path: ${{ runner.temp }}/venv", action)
        self.assertIn("pip install --require-hashes -r requirements-test.lock", action)
        self.assertNotIn("pip install -r", action)
        workflow = (ROOT / ".github/workflows/pr-tests.yml").read_text()
        self.assertGreaterEqual(workflow.count('test-lock: "true"'), 2)  # unit-tests and the second-date job

    def test_the_yaml_only_lock_keeps_the_pin_and_hash_of_the_full_test_lock(self):
        full, small = lock_entries("requirements-test.lock"), lock_entries("requirements-yaml.lock")
        self.assertEqual(small, {"pyyaml": full["pyyaml"]})
        pattern = r"PyYAML==[^\n]*\n\s+--hash=sha256:[0-9a-f]{64}"
        self.assertEqual(
            re.search(pattern, (ROOT / "requirements-yaml.lock").read_text()).group(0),
            re.search(pattern, (ROOT / "requirements-test.lock").read_text()).group(0),
        )

    def test_the_hourly_unit_test_step_installs_only_the_yaml_lock(self):
        hourly = (ROOT / ".github/workflows-src/hourly/020-unit-tests.yml").read_text()
        self.assertIn("pip install --require-hashes -r requirements-yaml.lock", hourly)
        self.assertNotIn("requirements-test.lock", hourly)

    def test_profile_sync_has_no_workflow_run_trigger_and_the_tick_starts_it_once_a_day(self):
        text = (ROOT / ".github/workflows/profile-sync.yml").read_text()
        self.assertNotIn("workflow_run", text)  # a workflow_run trigger lists one skipped run per hourly run
        self.assertNotIn("schedule:", text)
        self.assertIn("inputs.tick", text)
        prep = (ROOT / ".github/workflows-src/hourly/010b-prep.yml").read_text()
        self.assertIn("workflows/profile-sync.yml/dispatches", prep)
        self.assertIn('"$(date -u +%H)" = 11', prep)  # the 11:xx UTC tick, 5 to 6 AM Central
        self.assertIn("-ge 72000", prep)  # and not twice inside 20 hours
        self.assertIn("profile-sync.yml/runs?event=workflow_dispatch", prep)  # the skipped runs the old trigger left are not its last run


if __name__ == "__main__":
    unittest.main()
