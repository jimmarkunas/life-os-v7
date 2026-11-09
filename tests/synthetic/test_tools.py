"""LIFE-374 / GWT-T05-01 and GWT-T05-02: the dispatcher is the only way a fake tool runs; a refusal has zero provider side effects; a receipt is attributable and redacted."""
import ast
import json
import unittest
from pathlib import Path

from lifeos.synthetic import authz, tools as T
from lifeos.synthetic.authz_fixture import CANARY, AuthzWorld, TENANT_A, TENANT_B
from tests.synthetic.test_authz import approved, for_tool, req

RECEIPT_FIELDS = {"fixture_case_id", "policy_revision", "fake_actor_id", "fake_host_id", "fake_session_id", "fake_task_id", "tenant_id", "provider", "exact_account_id",
                  "action_class", "grant_or_approval_reference_hash", "request_nonce_hash", "decision_code", "reason", "decision_step", "decision_stage", "source_failure_kind",
                  "checked_at_utc", "read_handler_calls", "mutation_calls", "commits", "provider_state_hash_before", "provider_state_hash_after", "policy_ledger_hash_before",
                  "policy_ledger_hash_after", "outcome_label", "audit"}


class PermittedCall(unittest.TestCase):
    def test_a_permitted_read_runs_once_and_returns_an_attributable_redacted_receipt(self):
        w = AuthzWorld()
        receipt, results = authz.dispatch(w, req(case="gwt-01-read"))
        self.assertEqual(set(receipt), RECEIPT_FIELDS)
        self.assertEqual((receipt["decision_code"], receipt["decision_step"], receipt["read_handler_calls"], receipt["mutation_calls"], receipt["commits"]), ("ALLOW", 21, 1, 0, 0))
        self.assertEqual([r["object_id"] for r in results], ["P-MSG-1"])
        self.assertEqual((receipt["fake_actor_id"], receipt["fake_host_id"], receipt["fake_session_id"], receipt["fake_task_id"], receipt["tenant_id"], receipt["exact_account_id"]),
                         ("agent-a", "fake-mac", "sess-a", "task-a", TENANT_A, "acct-a-gmail"))
        text = json.dumps(receipt)
        for private in ("fake content", "n1\"", "P-MSG-1"):
            self.assertNotIn(private, text)                                  # no content, no raw nonce, no object id
        self.assertEqual(receipt["provider_state_hash_before"], receipt["provider_state_hash_after"])
        self.assertNotEqual(receipt["policy_ledger_hash_before"], receipt["policy_ledger_hash_after"])      # only the nonce was consumed

    def test_a_protected_send_with_an_exact_approval_commits_once_and_names_jim(self):
        w = AuthzWorld()
        receipt, results = authz.dispatch(w, approved(for_tool("gmail_send_message", "write", case="gwt-01-send")))
        self.assertEqual((receipt["decision_code"], receipt["mutation_calls"], receipt["commits"], len(w.outbox)), ("ALLOW", 1, 1, 1))
        self.assertEqual(receipt["audit"]["approver"], "jim")
        self.assertTrue(receipt["audit"]["t"].endswith("-06:00"))              # the audit line is Central (winter offset)
        self.assertEqual(receipt["checked_at_utc"], "2026-11-09T12:00:00+00:00")
        self.assertNotEqual(receipt["provider_state_hash_before"], receipt["provider_state_hash_after"])

    def test_the_same_request_cannot_run_twice_and_a_used_approval_is_dead(self):
        w = AuthzWorld()
        send = approved(for_tool("gmail_send_message", "write"))
        self.assertEqual(authz.dispatch(w, send)[0]["decision_code"], "ALLOW")
        again, results = authz.dispatch(w, send)
        self.assertEqual((again["decision_code"], again["commits"], results, len(w.outbox)), ("DENY_REPLAY_OR_WRITER", 0, None, 1))
        reuse, _ = authz.dispatch(w, {**send, "nonce": "n2"})                  # N16: a new request nonce but the same, already used approval
        self.assertEqual((reuse["decision_code"], reuse["commits"], len(w.outbox)), ("DENY_REPLAY_OR_WRITER", 0, 1))

    def test_a_delete_waits_for_jim_then_runs_once(self):
        w = AuthzWorld()
        delete = for_tool("notion_delete_page", "write")
        held, results = authz.dispatch(w, delete)
        self.assertEqual((held["decision_code"], held["reason"], held["decision_stage"], held["read_handler_calls"], held["mutation_calls"], held["commits"], results),
                         ("PENDING", "delete_needs_approval", "held", 0, 0, 0, None))
        self.assertEqual(w.outbox, [])
        done, _ = authz.dispatch(w, approved(delete, nonce="n9"))
        self.assertEqual((done["decision_code"], done["commits"], len(w.outbox)), ("ALLOW", 1, 1))


class RefusedCalls(unittest.TestCase):
    CASES = [("wrong tenant", req(tenant=TENANT_B), None), ("hermes default connector", req(channel="hermes_default_connector"), None),
             ("forged session", req(session_id="sess-b"), None), ("forged host", req(host_id="unknown-host"), None), ("unregistered tool", req(tool="no_such_tool"), None),
             ("revoked grant", req(), "revoke"), ("overdue grant", req(), "overdue"), ("unknown risk", for_tool("mystery_tool", "write"), None),
             ("multi risk", for_tool("multi_risk_tool", "write"), None), ("missing approval", for_tool("gmail_send_message", "write"), None),
             ("read grant sends", for_tool("gmail_send_message", "read"), None), ("stale source", req(source_id="SYN-1", source_revision=1), None),
             ("secret in request", req(input_text=CANARY), None), ("management", for_tool("composio_admin", "write"), None),
             ("mixed batch", req(batch=["gmail_read_message", "gmail_send_message"]), None), ("bad account", req(account_id="latest"), None)]

    def test_each_refusal_happens_before_any_fake_provider_side_effect(self):
        for name, request, tweak in self.CASES:
            with self.subTest(name):
                w = AuthzWorld()
                if tweak == "revoke":
                    w.revoke_grant("g-acct-a-gmail-read")
                if tweak == "overdue":
                    w.grants["g-acct-a-gmail-read"]["review_due"] = authz.NOW
                receipt, results = authz.dispatch(w, request)
                self.assertTrue(receipt["decision_code"].startswith(("DENY_", "NEEDS_")), receipt["decision_code"])
                self.assertEqual((receipt["read_handler_calls"], receipt["mutation_calls"], receipt["commits"], results, w.outbox), (0, 0, 0, None, []))
                self.assertEqual((receipt["provider_state_hash_before"], receipt["policy_ledger_hash_before"]), (receipt["provider_state_hash_after"], receipt["policy_ledger_hash_after"]))
                self.assertEqual(receipt["decision_stage"], "preflight")
                self.assertNotIn(CANARY, json.dumps(receipt))

    def test_a_receipt_never_names_another_tenant_or_the_request_text(self):
        w = AuthzWorld()
        receipt, _ = authz.dispatch(w, req(tenant=TENANT_B, input_text="secret plan for the decoy", object_id="B-OBJ"))
        text = json.dumps(receipt)
        for private in ("secret plan", "B-OBJ", "DECOY"):
            self.assertNotIn(private, text)


class AfterTheRead(unittest.TestCase):
    def test_a_timeout_after_the_read_is_refused_with_no_commit_and_the_nonce_stays_unused(self):
        w = AuthzWorld()
        receipt, results = authz.dispatch(w, req(object_id="P-MSG-TIMEOUT"))
        self.assertEqual((receipt["decision_code"], receipt["decision_step"], receipt["source_failure_kind"], receipt["decision_stage"], receipt["read_handler_calls"], receipt["commits"], results),
                         ("DENY_STALE_SOURCE", 19, "SOURCE_TIMEOUT", "post_read", 1, 0, None))
        self.assertEqual(w.req_nonces, set())

    def test_a_secret_in_the_output_is_suppressed(self):
        w = AuthzWorld()
        receipt, results = authz.dispatch(w, req(object_id="P-MSG-CANARY"))
        self.assertEqual((receipt["decision_code"], receipt["decision_step"], receipt["read_handler_calls"], receipt["mutation_calls"], receipt["commits"], results),
                         ("DENY_SECRET_EXPOSURE", 20, 1, 0, 0, None))
        self.assertNotIn(CANARY, json.dumps(receipt))


class EmergencyRevoke(unittest.TestCase):
    def test_a_revoke_changes_only_the_fake_ledger_and_the_next_call_is_denied(self):
        w = AuthzWorld()
        first, _ = authz.dispatch(w, req())
        self.assertEqual(first["decision_code"], "ALLOW")
        provider = w.provider_hash()
        ledger = w.ledger_hash()
        w.revoke_grant("g-acct-a-gmail-read")
        self.assertEqual(w.provider_hash(), provider)
        self.assertNotEqual(w.ledger_hash(), ledger)
        second, results = authz.dispatch(w, req(nonce="n2"))
        self.assertEqual((second["decision_code"], second["commits"], results), ("DENY_GRANT", 0, None))


class ItIsNotAGateway(unittest.TestCase):
    def test_the_synthetic_package_imports_nothing_that_could_reach_a_network_or_start_a_process(self):
        allowed = {"hashlib", "json", "datetime", "lifeos"}
        root = Path(authz.__file__).parent
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"{path.name} imports {name}")

    def test_every_registered_tool_is_reachable_only_through_dispatch(self):
        w = AuthzWorld()
        for name, tool in T.TOOLS.items():
            if tool["actions"][0] == T.READ and not tool["unsupported"]:
                receipt, _ = authz.dispatch(w, for_tool(name, nonce=f"n-{name}"))
                self.assertEqual(receipt["read_handler_calls"], 1, name)


if __name__ == "__main__":
    unittest.main()
