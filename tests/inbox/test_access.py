"""(synthetic): two separately scoped fake agents read the same fake source through the existing grant check"""

import os
import tempfile
import unittest
from datetime import timedelta

from lifeos.inbox import access, policy
from lifeos.inbox.ledger import Ledger, LedgerError
from tests.inbox import fixtures as F


def agent(name, **kw):
    return policy.Principal(name, F.grants(**kw))


class Access(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "ledger.db")
        self.ledger = F.file_ledger(self.path)
        self.source = F.FakeSource("jira", "personal")
        self.personal = agent("agent-personal", workspaces=("personal",), sources=("jira",))
        self.bytalos = agent("agent-bytalos", workspaces=("bytalos",), sources=("jira",))

    def read(self, who, request_id, source=None, ledger=None, now=F.NOW):
        return access.read_source(ledger or self.ledger, who, source or self.source, now, request_id)

    def test_the_allowed_agent_reads_and_is_named_on_the_receipt(self):
        result = self.read(self.personal, "r1")
        self.assertEqual((result.decision, result.reason, len(result.rows)), ("allow", "ok", 1))
        self.assertEqual(self.source.calls, ["cred:jira:personal"])
        receipt = self.ledger.access_receipts("agent-personal")[0]
        self.assertEqual(
            (
                receipt["principal_id"],
                receipt["source"],
                receipt["workspace"],
                receipt["account"],
                receipt["decision"],
                receipt["outcome"],
            ),
            ("agent-personal", "jira", "personal", "jim-personal", "allow", "ok"),
        )

    def test_the_other_agent_is_denied_before_any_read_and_still_gets_a_receipt(self):
        self.read(self.personal, "r1")
        denied = self.read(self.bytalos, "r2")
        self.assertEqual((denied.decision, denied.reason, denied.rows), ("deny", "no_grant", ()))
        self.assertEqual(len(self.source.calls), 1)  # only the first agent's read reached the source
        self.assertEqual(
            [(r["principal_id"], r["decision"], r["reason"]) for r in self.ledger.access_receipts("agent-bytalos")],
            [("agent-bytalos", "deny", "no_grant")],
        )
        self.assertEqual(len(self.ledger.access_receipts("agent-personal")), 1)  # receipts are per principal

    def test_wrong_workspace_wrong_account_revoked_expired_and_wrong_action_all_deny_without_reading(self):
        cases = {
            "wrong_workspace": (self.personal, F.FakeSource("jira", "bytalos"), "no_grant"),
            "wrong_account": (self.personal, F.FakeSource("jira", "personal", account="other-account"), "no_grant"),
            "revoked": (
                agent("a-rev", workspaces=("personal",), sources=("jira",), revoked=(("jira", "personal"),)),
                self.source,
                "grant_revoked",
            ),
            "expired": (
                agent("a-exp", workspaces=("personal",), sources=("jira",), expires=F.NOW - timedelta(minutes=1)),
                self.source,
                "grant_expired",
            ),
            "view_not_granted": (
                agent("a-act", workspaces=("personal",), sources=("jira",), actions={"jira": {"open"}}),
                self.source,
                "action_not_granted",
            ),
            "tgp": (self.personal, F.FakeSource("jira", "tgp"), "workspace_not_released"),
            "unknown": (self.personal, F.FakeSource("jira", "elsewhere"), "unknown_scope"),
        }
        for name, (who, source, reason) in cases.items():
            with self.subTest(name):
                result = self.read(who, f"deny-{name}", source=source)
                self.assertEqual((result.decision, result.reason, result.rows), ("deny", reason, ()))
                self.assertEqual(source.calls, [])
                self.assertEqual(
                    {r["request_id"]: r["reason"] for r in self.ledger.access_receipts(who.principal_id)}[
                        f"deny-{name}"
                    ],
                    reason,
                )

    def test_a_repeated_request_id_is_a_replay_and_never_reads_again_even_after_a_restart(self):
        self.read(self.personal, "r1")
        restarted = F.file_ledger(self.path, create=False)  # a new process: nothing in memory
        again = self.read(self.personal, "r1", ledger=restarted)
        self.assertEqual((again.reason, again.replayed, again.rows), ("replayed", True, ()))
        self.assertEqual(len(self.source.calls), 1)
        self.assertEqual(len(restarted.access_receipts("agent-personal")), 1)  # receipts survive the restart

    def test_a_failed_read_is_recorded_as_its_type_only_and_hides_the_message(self):
        broken = F.FakeSource("jira", "personal", error=TimeoutError(F.ERROR_CANARY))
        result = self.read(self.personal, "r1", source=broken)
        self.assertEqual((result.decision, result.rows), ("allow", ()))
        receipt = self.ledger.access_receipts("agent-personal")[0]
        self.assertEqual(receipt["outcome"], "timeouterror")
        self.assertNotIn(F.ERROR_CANARY, repr(receipt))

    def test_if_the_receipt_cannot_be_written_nothing_is_read(self):
        def unavailable():
            raise OSError("down")

        with self.assertRaises(LedgerError):
            access.read_source(Ledger(unavailable), self.personal, self.source, F.NOW, "r1")
        self.assertEqual(self.source.calls, [])

    def test_receipts_hold_ids_and_fixed_words_only(self):
        self.read(self.personal, "r1")
        text = repr(self.ledger.access_receipts("agent-personal"))
        for forbidden in (F.CANARY, "cred:", "https://", "@"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
