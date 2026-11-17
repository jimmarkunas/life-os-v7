"""LIFE-742 (synthetic): an agent promotes a ready ticket itself, waits for Jim only on a protected step, and readiness treats both promotions the same."""
import unittest

from lifeos.jira import promote
from lifeos.sources.devtools import readiness


def doc(*lines):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines]}


READY = ("Outcome", "Add a pure helper.", "Done when", "* Given X, then Y")


def issue(*lines, status="To Do", labels=(), blockers=(), kind="Task"):
    links = [{"type": {"name": "Blocks"}, "inwardIssue": {"key": b, "fields": {"status": {"statusCategory": {"key": "done" if ok else "new"}}}}} for b, ok in blockers]
    return {"key": "LIFE-1", "fields": {"description": doc(*(lines or READY)), "status": {"name": status}, "labels": list(labels), "issuelinks": links, "issuetype": {"name": kind}}}


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
        text = (*READY, "Not in scope", "No credentials, no schema change, no public endpoint.", "STOP: no live access.")
        self.assertEqual(promote.decide(issue(*text)), ("promote", []))

    def test_a_need_after_a_refusal_section_is_still_found(self):
        text = (*READY, "Not in scope", "No credentials.", "Steps", "Create a new table first.")
        self.assertEqual(promote.decide(issue(*text))[0], "wait")

    def test_unready_tickets_are_skipped_without_a_change(self):
        self.assertEqual(promote.decide(issue(blockers=[("LIFE-2", False)])), ("skip", ["blocked"]))
        self.assertEqual(promote.decide(issue("Outcome", "Just do it.")), ("skip", ["no_done_when"]))
        self.assertEqual(promote.decide(issue(status="Agent Go")), ("skip", ["not_to_do"]))
        self.assertEqual(promote.decide(issue(kind="Epic")), ("skip", ["epic"]))
        self.assertEqual(promote.decide(issue(labels=["needs-jim"])), ("skip", ["skip_label"]))

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
        return {"key": "LIFE-1", "blockers": [], "acceptance": [{"done_when": "Given X, then Y"}], "authorization": {"approved_by": approved_by, "provenance": "chat"}}

    def test_readiness_does_not_tell_an_agent_promotion_from_jims(self):
        jim, agent = readiness.check_issue(self.record("Jim")), readiness.check_issue(self.record("agent-claude-code"))
        self.assertEqual(jim, agent)
        self.assertEqual(agent[0], [])


if __name__ == "__main__":
    unittest.main()
