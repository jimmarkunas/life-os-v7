"""LIFE-363 (REQ-6): `python -m lifeos.readiness issue-mcp FILE KEY` checks the unmodified Atlassian MCP getJiraIssue result with the same validator as issue-json.
Offline, synthetic envelope (invented key LIFE-1, no real ids or people), proven through the real entrypoint."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lifeos.jira import readiness_record as rr
from tests.jira.test_readiness_record import DESC

ROOT = Path(__file__).resolve().parents[2]
DONE = {"type": {"name": "Blocks"}, "inwardIssue": {"key": "LIFE-2", "fields": {"status": {"name": "Done"}}}}
OPEN = {"type": {"name": "Blocks"}, "inwardIssue": {"key": "LIFE-3", "fields": {"status": {"name": "To Do"}}}}


def envelope(links=(), desc=DESC, key="LIFE-1"):
    fields = {"summary": "Example", "status": {"name": "Agent Go"}, "labels": ["lane-career-v7"], "parent": {"key": "LIFE-900"}, "components": [{"name": "Example Component"}],
              "issuelinks": list(links), "description": desc, "updated": "2030-01-01T12:00:00.000-0600"}
    return {"issues": {"nodes": [{"id": "1", "key": key, "fields": fields}]}, "context": {"toolName": "getJiraIssue"}}


def run(data, key="LIFE-1", age=0):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "read.json"
        path.write_text(json.dumps(data))
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        done = subprocess.run([sys.executable, "-m", "lifeos.readiness", "issue-mcp", str(path), key], cwd=ROOT, capture_output=True, text=True)
        return done.returncode, done.stdout, done.stderr


class IssueMcp(unittest.TestCase):
    def json_run(self, record):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.json"
            path.write_text(json.dumps(record))
            done = subprocess.run([sys.executable, "-m", "lifeos.readiness", "issue-json", str(path)], cwd=ROOT, capture_output=True, text=True)
            return done.returncode, done.stdout, done.stderr

    def test_a_ready_ticket_prints_ready_with_the_source_and_fields_and_matches_issue_json(self):
        code, out, err = run(envelope([DONE]))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ready v1", out)
        self.assertIn("SOURCE mcp-read", out)
        self.assertIn("FIELDS key=LIFE-1 status=Agent Go updated=2030-01-01T12:00:00.000-0600 blockers=LIFE-2=Done", out)
        self.assertNotIn("live", out.lower())
        self.assertEqual(self.json_run(rr.from_mcp(envelope([DONE]), "LIFE-1"))[:2], (0, "ready v1\n"))       # issue-json on the same unwrapped record says the same

    def test_a_key_mismatch_is_refused(self):
        code, out, err = run(envelope(), key="LIFE-9")
        self.assertEqual(code, 2)
        self.assertIn("QUEUE_NOT_READY — KEY_MISMATCH", err)
        self.assertIn("SOURCE mcp-read", out)

    def test_a_stale_file_is_a_warning_not_a_refusal(self):
        code, out, err = run(envelope(), age=16 * 60)
        self.assertEqual(code, 0)                                   # delivery flow v3: older than 15 minutes only warns
        self.assertIn("warn v1 stale_read file", err)
        self.assertIn("ready v1", out)
        self.assertEqual(run(envelope(), age=14 * 60)[::2], (0, ""))

    def test_a_missing_field_names_it(self):
        for name in ("description", "issuelinks", "parent", "components", "labels", "status", "summary"):
            data = envelope()
            del data["issues"]["nodes"][0]["fields"][name]
            code, _, err = run(data)
            self.assertEqual(code, 2)
            self.assertIn(f"QUEUE_NOT_READY — INCOMPLETE:{name}", err)

    def test_a_bad_envelope_is_never_ready(self):
        for data in ({}, {"issues": {"nodes": []}}, {"issues": {"nodes": [envelope()["issues"]["nodes"][0]] * 2}}, []):
            code, out, err = run(data)
            self.assertEqual(code, 2)
            self.assertNotIn("ready v1", out)
            self.assertIn("QUEUE_NOT_READY", err)

    def test_an_open_blocker_is_refused_with_the_same_code_as_issue_key(self):
        code, _, err = run(envelope([OPEN]))
        self.assertEqual(code, 2)
        self.assertIn("blocker_not_done", err)
        self.assertIn("blockers=LIFE-3=To Do", run(envelope([OPEN]))[1])
        code, _, err2 = self.json_run(rr.from_mcp(envelope([OPEN]), "LIFE-1"))                                  # the same record through issue-json
        self.assertEqual(code, 2)
        self.assertIn("blocker_not_done", err2)

    def test_the_envelope_is_not_mutated(self):
        data = envelope([DONE])
        before = copy.deepcopy(data)
        rr.from_mcp(data, "LIFE-1")
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
