"""a Jira issue becomes the readiness record, proven through the real entrypoint (`python -m
lifeos.sources.devtools.readiness issue-json|issue-key`).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeos.jira import readiness_record as rr
from lifeos.sources.devtools import readiness

ROOT = Path(__file__).resolve().parents[2]
DESC = """**Origin / approval:** Example Owner, Jan 1, 2030, chat: example.

**Jim must do first:** None

**Done when:**

* \\[ \\] **GWT-EX-01** Given a ready issue, when the check runs, then it reports ready.

**Needs before development:**

- [x] Parent named: LIFE-900 (proof: Parent field set).

**Decisions:** None open.

**Build:** code and tests, one PR."""


def raw(
    desc=DESC, labels=("lane-career-v7", "ready"), components=({"name": "Example Component"},), links=(), epic="Ready"
):
    return {
        "key": "LIFE-901",
        "fields": {
            "description": desc,
            "labels": list(labels),
            "parent": {"key": "LIFE-900", "fields": {"status": {"name": epic}}},
            "components": list(components),
            "issuelinks": list(links),
        },
    }


def entry(*args):
    done = subprocess.run(
        [sys.executable, "-m", "lifeos.sources.devtools.readiness", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return done.returncode, done.stdout, done.stderr


class JimMustDoFirstAtStart(unittest.TestCase):
    def test_an_unchecked_item_refuses_and_a_missing_section_warns(self):
        from lifeos.sources.devtools import readiness

        record = rr.normalize(
            raw(
                DESC.replace(
                    "**Jim must do first:** None", "**Jim must do first:**\n\n- [ ] Store the token in Bitwarden"
                )
            )
        )
        refusals, _ = readiness.check_issue(record)
        self.assertIn("jim_first_open", [r["code"] for r in refusals])
        record = rr.normalize(raw(DESC.replace("**Jim must do first:** None\n\n", "")))
        refusals, warnings = readiness.check_issue(record)
        self.assertEqual([r["code"] for r in refusals], [])
        self.assertIn("jim_first_missing", [w["code"] for w in warnings])


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
        self.assertEqual(
            rr.normalize(raw(desc))["acceptance"],
            [{"given": "a ready issue", "when": "the check runs", "then": "it reports ready."}],
        )
        self.assertEqual(self.check(raw(desc))[:2], (0, "ready v1\n"))

    def test_any_gwt_id_or_none_parses_and_a_done_when_bullet_counts(self):
        """Delivery flow v3: the id is optional and free-form"""
        for row in (
            "GWT-1 Given a, when b, then c",
            "**GWT-S62-01** Given a, when b, then c",
            "Given a, when b, then c",
            "1. Given a, when b, then c",
        ):
            with self.subTest(row=row):
                self.assertEqual(
                    rr.normalize(raw(f"## Done when\n\n* {row}"))["acceptance"],
                    [{"given": "a", "when": "b", "then": "c"}],
                )
        bullet = rr.normalize(
            raw("## Outcome\n\nx\n\n## Done when\n\n* \\[ \\] the cell holds the text\n\n## Never\n\nnothing")
        )
        self.assertEqual(bullet["acceptance"], [{"done_when": "the cell holds the text"}])
        self.assertEqual(self.check(raw("## Done when\n\n* the cell holds the text"))[:2], (0, "ready v1\n"))

    def test_only_a_missing_done_when_or_an_open_blocker_refuses(self):
        code, out, err = self.check(raw("## Outcome\n\nNo checks here."))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("refused v1 gwt_missing acceptance", err)
        blocker = [
            {"type": {"name": "Blocks"}, "inwardIssue": {"key": "LIFE-899", "fields": {"status": {"name": "To Do"}}}}
        ]
        code, _, err = self.check(raw(links=blocker))
        self.assertEqual(code, 2)
        self.assertIn("blocked_by_open blockers LIFE-899=To Do", err)

    def test_every_old_gap_is_a_warning_and_the_ticket_stays_ready(self):
        """Delivery flow v3 (d): a ticket with only a Done-when bullet and no other field is ready."""
        code, out, err = self.check(raw("## Done when\n\n* the cell holds the text", labels=("ready",), components=()))
        self.assertEqual((code, out), (0, "ready v1\n"))
        for warned in (
            "lane_missing",
            "component_missing",
            "origin_missing",
            "authorization_missing",
            "permissions_missing",
        ):
            self.assertIn(f"warn v1 {warned}", err)
        self.assertNotIn("refused", err)
        self.assertIn(
            "warn v1 authorization_missing", self.check(raw(DESC.replace("**Origin / approval:**", "**Origin:**")))[2]
        )

    def test_every_release_gate_reason_refuses_at_start_and_the_exceptions_pass(self):
        """the release gate's fixed reasons are HARD here too (one case each)"""
        for kwargs, code in (
            ({"labels": ("lane-career-v7",)}, "no_readiness_label"),
            ({"labels": ("ready", "needs-jim")}, "two_readiness_labels"),
            ({"labels": ("needs-prep",)}, "not_ready_label"),
            ({"labels": ("needs-jim",)}, "not_ready_label"),
            ({"epic": "Scoping"}, "epic_scoping"),
            (
                {
                    "links": [
                        {
                            "type": {"name": "Blocks"},
                            "inwardIssue": {
                                "key": "LIFE-899",
                                "fields": {"status": {"name": "To Do", "statusCategory": {"key": "new"}}},
                            },
                        }
                    ]
                },
                "blocked_by_open",
            ),
        ):
            result, _, err = self.check(raw(**kwargs))
            self.assertEqual(result, 2, code)
            self.assertIn(f"refused v1 {code}", err)
        for kwargs in (
            {"labels": ("ready", "research"), "epic": "Scoping"},
            {"labels": ("ready", "verdict"), "epic": "Scoping"},
            {"epic": "Ready"},
        ):
            self.assertEqual(self.check(raw(**kwargs))[0], 0, kwargs)
        no_status = raw()
        no_status["fields"]["parent"] = {"key": "LIFE-900"}
        self.assertIn("refused v1 epic_status_unread", self.check(no_status)[2])

    def test_the_open_gaps_report_names_missing_parent_component_fix_version_or_readiness_label_and_writes_nothing(
        self,
    ):
        """reporting only (exit 0), from a saved Atlassian MCP search result"""
        full = {
            "key": "LIFE-1",
            "fields": {
                "parent": {"key": "LIFE-10"},
                "components": [{"name": "C"}],
                "fixVersions": [{"name": "C 1.0.0"}],
                "labels": ["ready"],
            },
        }
        bare = {
            "key": "LIFE-2",
            "fields": {"parent": None, "components": [], "fixVersions": [], "labels": ["lane-platform"]},
        }
        part = {
            "key": "LIFE-3",
            "fields": {
                "parent": {"key": "LIFE-10"},
                "components": [{"name": "C"}],
                "fixVersions": [],
                "labels": ["needs-prep"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp) / "search.json"
            saved.write_text(json.dumps({"issues": {"nodes": [full, bare, part]}}))
            code, out, err = entry("open-gaps-mcp", saved)
            self.assertEqual(
                (code, out),
                (
                    0,
                    "gaps v1 2 of 3 open issues\ngap LIFE-2 parent,component,fix_version,readiness_label\ngap LIFE-3 fix_version\n",
                ),
            )
            saved.write_text("{}")
            self.assertEqual(entry("open-gaps-mcp", saved)[0], 2)
        self.assertEqual(entry("open-gaps-mcp", "/nonexistent.json")[0], 2)

    def test_a_ready_epic_needs_a_current_pass_review_and_every_other_state_refuses(self):
        """epic_unreviewed (HARD) for a missing, failed or stale review"""

        def comment(text, created):
            return {"body": text, "created": created}

        def bundle(comments, children=(), status="Ready"):
            return {
                "key": "LIFE-846",
                "status": status,
                "comments": comments,
                "children": [{"key": f"LIFE-{i}", "updated": u} for i, u in enumerate(children, 1)],
            }

        passing = comment(
            "ChatGPT review: pass \u2014 children LIFE-849 to LIFE-851\n\n* LIFE-849: pass",
            "2026-11-22T01:45:26.935-0600",
        )
        adf = comment(
            {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "**ChatGPT review: pass**"}]}],
            },
            "2026-11-22T01:45:26.935-0600",
        )
        cases = (
            ("missing", bundle([])),
            (
                "missing",
                bundle(
                    [
                        comment("Governance verdict: GO", "2026-11-21T15:32:29.779-0600"),
                        comment("The ChatGPT review: pass is coming", "2026-11-22T01:45:26.935-0600"),
                    ]
                ),
            ),
            (
                "failed",
                bundle(
                    [
                        passing,
                        comment(
                            "ChatGPT review: changes\n\n* LIFE-851: add the checklist", "2026-11-22T02:00:00.000-0600"
                        ),
                    ]
                ),
            ),
            ("failed", bundle([comment("ChatGPT review: changes", "2026-11-22T01:00:00.000-0600")])),
            ("stale", bundle([passing], children=["2026-11-22T01:00:00.000-0600", "2026-11-22T03:00:00.000-0600"])),
        )
        for why, data in cases:
            self.assertEqual(readiness.epic_review(data), why, why)
        for data in (
            bundle([passing]),
            bundle([adf], children=["2026-11-22T01:00:00.000-0600"]),
            bundle(
                [comment("ChatGPT review: changes", "2026-11-21T01:00:00.000-0600"), passing],
                children=["2026-11-22T01:45:26.935-0600"],
            ),
            bundle([], status="Scoping"),
            bundle([], status="In Progress"),
        ):
            self.assertIsNone(readiness.epic_review(data))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "epic.json"
            path.write_text(json.dumps(cases[4][1]))
            code, out, err = entry("epic-review", path)
            self.assertEqual((code, out, err), (2, "", "refused v1 epic_unreviewed review LIFE-846 stale\n"))
            path.write_text(json.dumps(bundle([passing])))
            self.assertEqual(entry("epic-review", path)[:2], (0, "ready v1\n"))
        self.assertEqual(entry("epic-review", "/nonexistent.json")[0], 2)
        self.assertEqual(
            readiness.epic_review(bundle([comment("ChatGPT review: pass", "not a date")])), "missing"
        )  # an unreadable time never counts as a review

    def test_the_live_epic_bundle_reads_status_comments_and_child_updates_and_a_gap_is_a_fixed_code(self):
        class Fake:
            def __init__(self, comments=None, status="Ready"):
                self.comments, self.status = [] if comments is None else comments, status

            def get(self, path, params=None):
                if path.endswith("/search/jql"):
                    return {
                        "issues": [{"key": "LIFE-1", "fields": {"updated": "2026-11-22T01:00:00.000-0600"}}],
                        "isLast": True,
                    }
                if path.endswith("/comment"):
                    return {"comments": self.comments, "total": len(self.comments)}
                return {"fields": {"status": {"name": self.status}}}

        got = rr.epic_bundle(
            "LIFE-846", Fake([{"body": "ChatGPT review: pass", "created": "2026-11-22T01:45:26.935-0600"}])
        )
        self.assertEqual(
            (got["status"], len(got["comments"]), got["children"]),
            ("Ready", 1, [{"key": "LIFE-1", "updated": "2026-11-22T01:00:00.000-0600"}]),
        )
        self.assertIsNone(readiness.epic_review(got))
        with self.assertRaises(ValueError):
            rr.epic_bundle("LIFE-846", Fake(status=None))

    def test_a_long_life_description_warns(self):
        code, _, err = self.check(raw(DESC + "\n\n" + "x" * 4000))
        self.assertEqual(code, 0)
        self.assertIn("warn v1 description_long description", err)

    def test_adf_and_text_descriptions_read_the_same(self):
        para = lambda t: {"type": "paragraph", "content": [{"type": "text", "text": t}]}
        adf = {"type": "doc", "content": [para(x) for x in rr.lines(DESC)]}
        a, b = rr.normalize(raw(adf)), rr.normalize(raw(DESC))
        a.pop("description_chars"), b.pop("description_chars")
        self.assertEqual(a, b)

    def test_unreadable_jira_data_is_queue_not_ready_never_ready(self):
        for data in ({}, {"key": "LIFE-901", "fields": {"description": None}}):
            code, out, err = self.check(data)
            self.assertEqual((code, out), (2, ""))
            self.assertTrue(err.startswith("QUEUE_NOT_READY — INCOMPLETE:"), err)

    def test_a_los_key_is_no_longer_read_and_requirement_pages_stay_listed(self):
        """LOS is retired."""
        from_los = DESC.replace("chat: example.", "chat: example, from LOS-900.")
        page = "\n\n**Requirement:** REQ-7 https://app.notion.com/p/00000000000000000000000000000004"
        for text in (
            from_los,
            from_los + "\n\nREQ-7 has no link yet",
            from_los + "\n\nSee https://app.notion.com/p/00000000000000000000000000000004",
        ):
            code, out, err = self.check(raw(text))
            self.assertEqual((code, out), (0, "ready v1\n"))
            self.assertNotIn("requirement_page_missing", err)
            self.assertNotIn("LOS-900", err)
            self.assertEqual(rr.normalize(raw(text))["requirement"], {"pages": []})
        self.assertEqual(
            rr.normalize(raw(from_los + page))["requirement"],
            {"pages": ["https://app.notion.com/p/00000000000000000000000000000004"]},
        )

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
        done = subprocess.run(
            [sys.executable, "-m", "lifeos.sources.devtools.readiness", "issue-key", "LIFE-901"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={"PATH": ""},
        )
        self.assertEqual(done.returncode, 2)
        first, hint = done.stderr.strip().splitlines()
        self.assertEqual(first, "QUEUE_NOT_READY — JIRA_CONFIG_MISSING:JIRA_BASE_URL,JIRA_EMAIL,JIRA_API_TOKEN")
        self.assertIn("issue-mcp", hint)  # the one-line hint to the MCP route

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
