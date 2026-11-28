"""the test-index build is kept on purpose (Jim, Nov 28 2026): off every main run, but not deletable by a dead-code scan."""

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "test-index.yml"
PR_TESTS = ROOT / ".github" / "workflows" / "pr-tests.yml"


class KeptOnPurpose(unittest.TestCase):
    def test_the_workflow_exists_and_runs_only_when_dispatched(self):
        self.assertTrue(
            WORKFLOW.is_file(),
            "keep .github/workflows/test-index.yml: the test-index build (docs/platform/test-index.md)",
        )
        doc = yaml.safe_load(WORKFLOW.read_text())
        self.assertEqual(list(doc[True]), ["workflow_dispatch"])  # no schedule, push, pull_request or workflow_run
        self.assertEqual(doc["permissions"], {"contents": "read"})
        for word in ("secrets.", "schedule:", "workflow_run:", "bws"):
            self.assertNotIn(word, WORKFLOW.read_text())

    def test_it_still_builds_checks_freshness_and_saves_with_the_pinned_cache_action(self):
        steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["test-index"]["steps"]
        runs = [s.get("run", "") for s in steps]
        self.assertIn("pip install --require-hashes -r requirements-test.lock", runs)
        self.assertTrue(any("tests.testindex check --max-age-days 1" in r for r in runs))
        self.assertIn("python -m tests.testindex build", runs)
        save = next(s for s in steps if str(s.get("uses", "")).startswith("actions/cache/save@"))
        pick = yaml.safe_load(PR_TESTS.read_text())["jobs"]["pick"]["steps"]
        restore = next(s for s in pick if "cache/restore@" in str(s.get("uses", "")))
        self.assertEqual(save["with"]["key"], restore["with"]["key"])  # what it saves is what a pull request restores
        self.assertEqual(save["uses"].split("@")[1], restore["uses"].split("@")[1])  # the same pinned action commit

    def test_the_index_tool_keeps_its_build_and_check_commands(self):
        text = (ROOT / "tests" / "testindex.py").read_text()
        for command in ("build", "check"):
            self.assertIn(f'add_parser("{command}")', text, command)

    def test_the_doc_says_it_is_intentionally_not_on_any_trigger(self):
        text = (ROOT / "docs" / "platform" / "test-index.md").read_text()
        self.assertIn("gh workflow run test-index.yml", text)
        self.assertIn("intentionally", text)


if __name__ == "__main__":
    unittest.main()
