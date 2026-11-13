"""LIFE-519 step 7: the protected-file check blocks a change to a privacy file unless the approval label is on the pull request."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("protected_files", ROOT / ".github" / "protected_files.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)


class Check(unittest.TestCase):
    def test_the_three_privacy_files_are_the_protected_set(self):
        self.assertEqual(sorted(pf.PROTECTED), [".claude/settings.json", "docs/PRIVACY.md", "tests/contracts/test_privacy.py"])

    def test_an_ordinary_change_passes(self):
        self.assertTrue(pf.check(["lifeos/run.py", "docs/CODEMAP.md"], [])[0])

    def test_each_protected_file_blocks_without_the_label(self):
        for name in pf.PROTECTED:
            ok, message = pf.check(["lifeos/run.py", name], ["bug"])
            self.assertFalse(ok, name)
            self.assertIn(name, message)                                       # the file is named so a human can see why
            self.assertIn(pf.LABEL, message)

    def test_the_approval_label_lets_it_through(self):
        for name in pf.PROTECTED:
            self.assertTrue(pf.check([name], [pf.LABEL])[0], name)

    def test_a_look_alike_file_is_not_protected(self):
        self.assertTrue(pf.check(["docs/PRIVACY.md.bak", "tests/test_privacy.py", "docs/privacy.md"], [])[0])

    def test_the_required_check_runs_it_on_pull_requests_with_label_triggers(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        doc = yaml.safe_load((ROOT / ".github/workflows/pr-tests.yml").read_text())
        self.assertEqual(doc[True]["pull_request"]["types"], ["opened", "synchronize", "reopened", "labeled", "unlabeled"])
        steps = doc["jobs"]["unit-tests"]["steps"]
        guard = [s for s in steps if s.get("run") == "python .github/protected_files.py"]
        self.assertEqual(len(guard), 1)
        self.assertEqual(guard[0]["if"], "${{ github.event_name == 'pull_request' }}")      # on main pushes there is nothing to compare
        self.assertEqual(guard[0]["env"], {"PR_LABELS": "${{ join(github.event.pull_request.labels.*.name, ',') }}"})
        self.assertIn("fetch -q --no-tags --depth=2 origin", steps[0]["run"])          # LIFE-638: the merge commit and its parent (the checkout step is our own git fetch)


if __name__ == "__main__":
    unittest.main()
