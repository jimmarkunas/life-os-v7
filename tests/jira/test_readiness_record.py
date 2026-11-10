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

    def test_an_open_need_or_decision_is_only_a_warning(self):
        code, out, err = self.check(raw(DESC.replace("- [x] Parent", "- [ ] Parent")))
        self.assertEqual((code, out), (0, "ready v1\n"))
        self.assertIn("warn v1 prerequisite_open needs item 1", err)
        code, _, err = self.check(raw(DESC.replace("None open.", "\n\n- [ ] Pick one (proof: none; status: open).")))
        self.assertEqual(code, 0)
        self.assertIn("warn v1 decision_open decisions item 1", err)

    def test_a_task_numbered_gwt_id_parses(self):
        desc = DESC.replace("GWT-EX-01", "GWT-T04-01")
        self.assertEqual(rr.normalize(raw(desc))["acceptance"], [{"given": "a ready issue", "when": "the check runs", "then": "it reports ready."}])
        self.assertEqual(self.check(raw(desc))[:2], (0, "ready v1\n"))

    def test_any_gwt_id_or_none_parses_and_a_done_when_bullet_counts(self):
        """Delivery flow v3: the id is optional and free-form; a bullet under Done when is a Done-when line."""
        for row in ("GWT-1 Given a, when b, then c", "**GWT-S62-01** Given a, when b, then c", "Given a, when b, then c", "1. Given a, when b, then c"):
            with self.subTest(row=row):
                self.assertEqual(rr.normalize(raw(f"## Done when\n\n* {row}"))["acceptance"], [{"given": "a", "when": "b", "then": "c"}])
        bullet = rr.normalize(raw("## Outcome\n\nx\n\n## Done when\n\n* \\[ \\] the cell holds the text\n\n## Never\n\nnothing"))
        self.assertEqual(bullet["acceptance"], [{"done_when": "the cell holds the text"}])
        self.assertEqual(self.check(raw("## Done when\n\n* the cell holds the text"))[:2], (0, "ready v1\n"))

    def test_only_a_missing_done_when_or_an_open_blocker_refuses(self):
        code, out, err = self.check(raw("## Outcome\n\nNo checks here."))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("refused v1 gwt_missing acceptance", err)
        blocker = [{"type": {"name": "Blocks"}, "inwardIssue": {"key": "LIFE-899", "fields": {"status": {"name": "To Do"}}}}]
        code, _, err = self.check(raw(links=blocker))
        self.assertEqual(code, 2)
        self.assertIn("blocker_not_done blockers LIFE-899=To Do", err)

    def test_every_old_gap_is_a_warning_and_the_ticket_stays_ready(self):
        """Delivery flow v3 (d): a ticket with only a Done-when bullet and no other field is ready."""
        code, out, err = self.check(raw("## Done when\n\n* the cell holds the text", labels=(), components=()))
        self.assertEqual((code, out), (0, "ready v1\n"))
        for warned in ("lane_missing", "component_missing", "origin_missing", "authorization_missing", "permissions_missing"):
            self.assertIn(f"warn v1 {warned}", err)
        self.assertNotIn("refused", err)
        self.assertIn("warn v1 authorization_missing", self.check(raw(DESC.replace("**Origin / approval:**", "**Origin:**")))[2])

    def test_a_long_life_description_warns(self):
        code, _, err = self.check(raw(DESC + "\n\n" + "x" * 4000))
        self.assertEqual(code, 0)
        self.assertIn("warn v1 description_long description", err)

    def test_adf_and_text_descriptions_read_the_same(self):
        para = lambda t: {"type": "paragraph", "content": [{"type": "text", "text": t}]}
        adf = {"type": "doc", "content": [para(x) for x in rr.lines(DESC)]}
        a, b = rr.normalize(raw(adf)), rr.normalize(raw(DESC))
        a.pop("description_chars"), b.pop("description_chars")                 # the raw text length exists only for a text description
        self.assertEqual(a, b)

    def test_unreadable_jira_data_is_queue_not_ready_never_ready(self):
        for data in ({}, {"key": "LIFE-901", "fields": {"description": None}}):
            code, out, err = self.check(data)
            self.assertEqual((code, out), (2, ""))
            self.assertTrue(err.startswith("QUEUE_NOT_READY — INCOMPLETE:"), err)

    def test_a_los_origin_without_its_notion_requirement_page_only_warns(self):
        """LIFE-319, relaxed by delivery flow v3: requirement_page_missing is a warn line, not a refusal."""
        from_los = DESC.replace("chat: example.", "chat: example, from LOS-900.")
        page = "\n\n**Requirement:** REQ-7 https://app.notion.com/p/0123456789abcdef0123456789abcdef"
        code, out, err = self.check(raw(from_los))
        self.assertEqual((code, out), (0, "ready v1\n"))
        self.assertIn("warn v1 requirement_page_missing requirement LOS-900", err)
        self.assertEqual(self.check(raw(from_los + page))[:2], (0, "ready v1\n"))
        self.assertNotIn("requirement_page_missing", self.check(raw(from_los + page))[2])
        self.assertEqual(self.check(raw())[:2], (0, "ready v1\n"))                                           # no LOS origin: the check is skipped
        self.assertEqual(rr.normalize(raw())["requirement"], {"los": "", "pages": []})

    def test_a_bare_notion_link_or_a_los_key_outside_the_origin_line_does_not_count(self):
        from_los = DESC.replace("chat: example.", "chat: example, from LOS-900.")
        self.assertIn("requirement_page_missing", self.check(raw(from_los + "\n\nSee https://app.notion.com/p/0123456789abcdef0123456789abcdef"))[2])      # no REQ-n on the line
        self.assertIn("requirement_page_missing", self.check(raw(from_los + "\n\nREQ-7 has no link yet"))[2])
        self.assertEqual(self.check(raw(DESC + "\n\nBackground: LOS-900 was scoped earlier."))[:2], (0, "ready v1\n"))

    def test_a_malformed_requirement_record_only_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "record.json"
            data = rr.normalize(raw())
            data["requirement"] = "yes"
            record.write_text(json.dumps(data))
            code, _, err = entry("issue", record)
        self.assertEqual(code, 0)
        self.assertIn("warn v1 requirement_unreadable", err)

    def test_live_read_without_credentials_names_the_settings_only(self):
        done = subprocess.run([sys.executable, "-m", "lifeos.readiness", "issue-key", "LIFE-901"], cwd=ROOT, capture_output=True, text=True, env={"PATH": ""})
        self.assertEqual(done.returncode, 2)
        first, hint = done.stderr.strip().splitlines()
        self.assertEqual(first, "QUEUE_NOT_READY — JIRA_CONFIG_MISSING:JIRA_BASE_URL,JIRA_EMAIL,JIRA_API_TOKEN")
        self.assertIn("issue-mcp", hint)                                              # LIFE-363: the one-line hint to the MCP route

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
