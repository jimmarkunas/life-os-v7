"""LIFE-313: a Jira issue becomes the readiness record, proven through the real entrypoint (`python -m lifeos.readiness issue-json|issue-key`).
Offline fixtures only, invented keys; the live read uses a fake client."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeos.jira import readiness_record as rr

ROOT = Path(__file__).resolve().parents[2]
DESC = """**Origin / approval:** Example Owner, Jan 1, 2030, chat: example.

**Done when:**

* \\[ \\] **GWT-EX-01** Given a ready issue, when the check runs, then it reports ready.

**Needs before development:**

- [x] Parent named: LIFE-900 (proof: Parent field set).

**Decisions:** None open.

**Build:** code and tests, one PR."""


def raw(desc=DESC, labels=("lane-career-v7",), components=({"name": "Example Component"},), links=()):
    return {"key": "LIFE-901", "fields": {"description": desc, "labels": list(labels), "parent": {"key": "LIFE-900"}, "components": list(components), "issuelinks": list(links)}}


def entry(*args):
    done = subprocess.run([sys.executable, "-m", "lifeos.readiness", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


class RecordTests(unittest.TestCase):
    def check(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.json"
            path.write_text(json.dumps(data))
            return entry("issue-json", path)

    def test_a_complete_issue_is_ready(self):
        self.assertEqual(self.check(raw())[:2], (0, "ready v1\n"))

    def test_an_open_need_or_decision_is_refused(self):
        code, _, err = self.check(raw(DESC.replace("- [x] Parent", "- [ ] Parent")))
        self.assertEqual(code, 2)
        self.assertIn("prerequisite_open needs item 1", err)
        code, _, err = self.check(raw(DESC.replace("None open.", "\n\n- [ ] Pick one (proof: none; status: open).")))
        self.assertIn("decision_open decisions item 1", err)

    def test_each_gap_is_its_own_refusal(self):
        self.assertIn("lane_missing", self.check(raw(labels=()))[2])
        self.assertIn("component_missing", self.check(raw(components=()))[2])
        self.assertIn("gwt_missing", self.check(raw(DESC.replace("Given a ready issue", "A ready issue")))[2])
        self.assertIn("authorization_missing", self.check(raw(DESC.replace("**Origin / approval:**", "**Origin:**")))[2])
        self.assertIn("blocker_not_done", self.check(raw(links=[{"type": {"name": "Blocks"}, "inwardIssue": {"key": "LIFE-899", "fields": {"status": {"name": "To Do"}}}}]))[2])

    def test_adf_and_text_descriptions_read_the_same(self):
        para = lambda t: {"type": "paragraph", "content": [{"type": "text", "text": t}]}
        adf = {"type": "doc", "content": [para(x) for x in rr.lines(DESC)]}
        self.assertEqual(rr.normalize(raw(adf)), rr.normalize(raw(DESC)))

    def test_unreadable_jira_data_is_queue_not_ready_never_ready(self):
        for data in ({}, {"key": "LIFE-901", "fields": {"description": None}}):
            code, out, err = self.check(data)
            self.assertEqual((code, out), (2, ""))
            self.assertTrue(err.startswith("QUEUE_NOT_READY — INCOMPLETE:"), err)

    def test_live_read_without_credentials_names_the_settings_only(self):
        done = subprocess.run([sys.executable, "-m", "lifeos.readiness", "issue-key", "LIFE-901"], cwd=ROOT, capture_output=True, text=True, env={"PATH": ""})
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stderr.strip(), "QUEUE_NOT_READY — JIRA_CONFIG_MISSING:JIRA_BASE_URL,JIRA_EMAIL,JIRA_API_TOKEN")

    def test_fetch_is_one_read_only_get(self):
        calls = []

        class Fake:
            def get(self, path, params=None):
                calls.append((path, params))
                return raw()
        self.assertEqual(rr.fetch("LIFE-901", Fake())["key"], "LIFE-901")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("/issue/LIFE-901"))


if __name__ == "__main__":
    unittest.main()
