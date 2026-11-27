"""(synthetic): an agent promotes a ready ticket itself, waits for Jim only on a protected step, and readiness treats
both promotions the same.
"""

import unittest

from lifeos.jira import promote, release
from lifeos.sources.devtools import readiness


def doc(*lines):
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines],
    }


READY = ("Outcome", "Add a pure helper.", "Jim must do first: None", "Done when", "* Given X, then Y")


def issue(*lines, status="To Do", labels=("ready",), blockers=(), kind="Task", epic=None):
    links = [
        {
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": b, "fields": {"status": {"statusCategory": {"key": "done" if ok else "new"}}}},
        }
        for b, ok in blockers
    ]
    return {
        "key": "LIFE-1",
        "fields": {
            "description": doc(*(lines or READY)),
            "status": {"name": status},
            "labels": list(labels),
            "issuelinks": links,
            "issuetype": {"name": kind},
            **({"parent": {"key": "LIFE-10", "fields": {"status": {"name": epic}}}} if epic else {}),
        },
    }


class JimMustDoFirst(unittest.TestCase):
    def test_an_unchecked_item_waits_for_jim_and_a_cleared_list_promotes(self):
        waiting = issue(
            "Outcome",
            "Add a pure helper.",
            "Jim must do first",
            "[ ] Store the API key in Bitwarden",
            "Done when",
            "* Given X, then Y",
        )
        self.assertEqual(promote.decide(waiting), ("wait", ["jim_first"]))
        self.assertEqual(promote.act(waiting)["label"], "needs-jim")
        cleared = issue(
            "Outcome",
            "Add a pure helper.",
            "Jim must do first",
            "[x] Store the API key in Bitwarden",
            "Done when",
            "* Given X, then Y",
        )
        self.assertEqual(promote.decide(cleared), ("promote", []))

    def test_the_section_ends_at_the_next_paragraph_or_heading(self):
        """A ticked list followed by other text is clear: the text after the list is not an item."""

        def para(text, bold=False):
            mark = [{"type": "strong"}] if bold else []
            return {"type": "paragraph", "content": [{"type": "text", "text": text, "marks": mark}]}

        def bullets(*items):
            return {"type": "bulletList", "content": [{"type": "listItem", "content": [para(i)]} for i in items]}

        ticked = {
            "type": "doc",
            "content": [
                para("Jim must do first:", True),
                bullets("[x] Choose freshness", "[x] Approve the schedule"),
                para("Canonical detailed specification (REQ-53): https://example.com/spec"),
                {"type": "heading", "content": [{"type": "text", "text": "Exact boundary"}]},
                para("Prose"),
            ],
        }
        self.assertEqual(release.jim_first(ticked), (True, []))
        open_one = {
            "type": "doc",
            "content": [
                para("Jim must do first:", True),
                bullets("[x] Choose freshness", "[ ] Approve the schedule"),
                para("More text"),
            ],
        }
        self.assertEqual(release.jim_first(open_one), (True, ["- [ ] Approve the schedule"]))
        self.assertEqual(
            release.jim_first(
                "## Jim must do first\n\nNone (code-only scope)\n\n## Exact implementation boundary\n\nText"
            ),
            (True, []),
        )
        self.assertEqual(
            release.jim_first("**Jim must do first:**\n\n- Sign in to the provider\n\n**Done when:**"),
            (True, ["- Sign in to the provider"]),
        )  # a plain bullet is a need

    def test_a_ticket_without_the_section_is_not_promoted(self):
        self.assertEqual(
            promote.decide(issue("Outcome", "Add a pure helper.", "Done when", "* Given X, then Y")),
            ("skip", ["no_jim_first"]),
        )

    def test_jira_task_items_read_their_checked_state(self):
        def task(state, text):
            return {"type": "taskItem", "attrs": {"state": state}, "content": [{"type": "text", "text": text}]}

        body = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Jim must do first"}]},
                {
                    "type": "taskList",
                    "content": [task("DONE", "Approve the Notion wireframe"), task("TODO", "Sign in to the provider")],
                },
            ],
        }
        self.assertEqual(release.jim_first(body), (True, ["- [ ] Sign in to the provider"]))


class Decide(unittest.TestCase):
    def test_a_ready_ticket_is_promoted_even_if_the_agent_made_it(self):
        self.assertEqual(promote.decide(issue()), ("promote", []))

    def test_a_credential_need_waits_for_jim(self):
        self.assertEqual(promote.decide(issue(*READY, "Needs a read-only API key for Jira."))[0], "wait")
        self.assertIn("credential", promote.decide(issue(*READY, "Needs a read-only API key for Jira."))[1])

    def test_each_protected_step_is_named(self):
        cases = {
            "live_access": "Read live provider data once.",
            "host_install": "Install on the Mac and run sudo.",
            "spend": "Uses a paid model for the run.",
            "schema": "Create a new table in the store.",
            "delete": "Delete production rows afterwards.",
            "workflow_security": "Widen the workflow permissions block.",
            "ingress": "Open a public endpoint for the webhook.",
        }
        for name, line in cases.items():
            decision, needs = promote.decide(issue(*READY, line))
            self.assertEqual(decision, "wait", name)
            self.assertIn(name, needs)

    def test_a_refusal_section_does_not_count_as_a_need(self):
        text = (
            *READY,
            "Not in scope",
            "No credentials, no schema change, no public endpoint.",
            "STOP: no live access.",
        )
        self.assertEqual(promote.decide(issue(*text)), ("promote", []))

    def test_a_need_after_a_refusal_section_is_still_found(self):
        text = (*READY, "Not in scope", "No credentials.", "Steps", "Create a new table first.")
        self.assertEqual(promote.decide(issue(*text))[0], "wait")

    def test_unready_tickets_are_skipped_without_a_change(self):
        self.assertEqual(promote.decide(issue(blockers=[("LIFE-2", False)])), ("skip", ["blocked_by_open"]))
        self.assertEqual(promote.decide(issue("Outcome", "Just do it.")), ("skip", ["no_done_when"]))
        self.assertEqual(promote.decide(issue(status="Agent Go")), ("skip", ["not_to_do"]))
        self.assertEqual(promote.decide(issue(kind="Epic")), ("skip", ["epic"]))
        self.assertEqual(promote.decide(issue(labels=["needs-jim"])), ("skip", ["not_ready_label"]))

    def test_every_release_gate_reason_and_the_allowed_exceptions(self):
        """one case per fixed reason, the same ones release.eligible uses"""
        cases = (
            (issue(labels=()), "no_readiness_label"),
            (issue(labels=["lane-platform"]), "no_readiness_label"),
            (issue(labels=["ready", "needs-prep"]), "two_readiness_labels"),
            (issue(labels=["needs-prep"]), "not_ready_label"),
            (issue(labels=["needs-jim"]), "not_ready_label"),
            (issue(epic="Scoping"), "epic_scoping"),
            (issue(blockers=[("LIFE-2", False)]), "blocked_by_open"),
        )
        for case, reason in cases:
            self.assertEqual(promote.decide(case), ("skip", [reason]), reason)
        self.assertEqual(promote.decide(issue(labels=["ready", "research"], epic="Scoping")), ("promote", []))
        self.assertEqual(promote.decide(issue(labels=["ready", "verdict"], epic="Scoping")), ("promote", []))
        self.assertEqual(promote.decide(issue(epic="Ready")), ("promote", []))
        self.assertEqual(promote.decide(issue(epic="Ready", blockers=[("LIFE-2", True)])), ("promote", []))
        self.assertEqual(
            promote.decide({"key": "LIFE-1", "fields": {**issue()["fields"], "parent": {"key": "LIFE-10"}}}),
            ("skip", ["epic_status_unread"]),
        )  # the read did not return the Epic's status

    def test_a_done_blocker_does_not_block(self):
        self.assertEqual(promote.decide(issue(blockers=[("LIFE-2", True)])), ("promote", []))


class WaitingLabel(unittest.TestCase):
    def test_a_protected_step_gets_the_comment_and_the_label_in_one_step(self):
        out = promote.act(issue(*READY, "Needs a read-only API key for Jira."))
        self.assertEqual((out["decision"], out["label"]), ("wait", "needs-jim"))
        self.assertTrue(out["comment"].startswith("[waiting on] Jim: "))
        self.assertIn("credential", out["comment"])

    def test_a_promotable_or_skipped_ticket_gets_no_label_and_no_comment(self):
        for case in (issue(), issue(blockers=[("LIFE-2", False)]), issue(labels=["needs-jim"])):
            out = promote.act(case)
            self.assertEqual((out["label"], out["comment"]), (None, None))


class SamePath(unittest.TestCase):
    def record(self, approved_by):
        return {
            "key": "LIFE-1",
            "blockers": [],
            "acceptance": [{"done_when": "Given X, then Y"}],
            "authorization": {"approved_by": approved_by, "provenance": "chat"},
        }

    def test_readiness_does_not_tell_an_agent_promotion_from_jims(self):
        jim, agent = readiness.check_issue(self.record("Jim")), readiness.check_issue(self.record("agent-claude-code"))
        self.assertEqual(jim, agent)
        self.assertEqual(agent[0], [])


if __name__ == "__main__":
    unittest.main()
