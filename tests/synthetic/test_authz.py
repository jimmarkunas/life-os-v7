"""LIFE-374 / GWT-T05-02: the 21-step decision order of LIFE-372, one case per step and the dual-fault precedence cases. Fake data only; `preflight` never touches the world."""
import copy
import json
import unittest
from datetime import timedelta

from lifeos.platform import access_policy
from lifeos.synthetic import authz, tools as T
from lifeos.synthetic.authz_fixture import CANARY, KEY_A, NOW, AuthzWorld, TENANT_A, TENANT_B, TENANT_DECOY, approval_hash

BASE = {"case": "case", "tenant": TENANT_A, "agent_id": "agent-a", "host_id": "fake-mac", "session_id": "sess-a", "task_id": "task-a", "tool": "gmail_read_message",
        "nonce": "n1", "channel": "v7_adapter", "account_id": "acct-a-gmail", "grant_id": "g-acct-a-gmail-read", "object_id": "P-MSG-1"}


def req(**patch):
    out = dict(BASE)
    out.update(patch)
    return {k: v for k, v in out.items() if v is not ...}              # ... removes a field


def for_tool(tool, level="read", **patch):
    acct = "acct-a-" + T.TOOLS[tool]["service"]
    return req(tool=tool, account_id=acct, grant_id=f"g-{acct}-{level}", **patch)


def approved(r, **patch):
    names = r.get("batch") or [r["tool"]]
    a = {"issuer": "jim", "nonce": "ap-1", "host": r["host_id"], "task": r["task_id"], "account": r["account_id"],
         "action_hash": approval_hash(names, r["account_id"], r["tenant"], r["host_id"], r["task_id"])}
    a.update(patch)
    return {**r, "approval": a}


class Matrix(unittest.TestCase):
    def check(self, request, code, step, world=None, reason=None):
        w = world or AuthzWorld()
        before = copy.deepcopy((w.provider_hash(), w.ledger_hash()))
        got = authz.preflight(w, request)
        self.assertEqual((got["code"], got["step"]), (code, step), got)
        if reason:
            self.assertEqual(got["reason"], reason)
        self.assertEqual((w.provider_hash(), w.ledger_hash()), before)       # preflight never changes anything
        return got

    def test_the_happy_path_is_cleared_for_the_handler(self):
        self.check(req(), "ALLOW", 21)

    def test_step_1_malformed(self):
        for field in ("tenant", "agent_id", "tool", "nonce", "channel"):
            self.check(req(**{field: ...}), "DENY_INPUT", 1)
        self.check(req(batch="not-a-list"), "DENY_INPUT", 1)
        self.check("not a dict", "DENY_INPUT", 1)

    def test_step_2_principal_and_the_default_connector_bypass(self):
        for patch in ({"agent_id": "agent-rogue"}, {"session_id": "sess-b"}, {"session_id": "sess-a-old"}, {"session_id": "no-session"}, {"host_id": "unknown-host"},
                      {"channel": "hermes_default_connector"}):
            with self.subTest(patch=patch):
                self.check(req(**patch), "DENY_PRINCIPAL", 2)

    def test_step_3_task(self):
        for task in ("task-a-blocked", "task-b", "no-task"):
            self.check(req(task_id=task), "DENY_TASK_SCOPE", 3)

    def test_step_4_replay_or_conflicting_writer(self):
        w = AuthzWorld()
        w.req_nonces.add("n1")
        self.check(req(), "DENY_REPLAY_OR_WRITER", 4, w)
        w2 = AuthzWorld()
        w2.writer_claims["topic-1"] = "claim-1"
        self.check(req(resource="topic-1", claim_id="claim-2"), "DENY_REPLAY_OR_WRITER", 4, w2)
        self.check(req(resource="topic-1", claim_id="claim-1"), "ALLOW", 21, w2)
        w3 = AuthzWorld()
        w3.approval_nonces.add("ap-1")
        self.check(approved(for_tool("gmail_send_message", "write")), "DENY_REPLAY_OR_WRITER", 4, w3)       # N16: a valid approval that was already used

    def test_step_5_revoked_source_of_the_callers_own_tenant(self):
        w = AuthzWorld()
        w.revoke("DOC-1")
        self.check(for_tool("drive_read_file", source_id="DOC-1", source_revision=5), "DENY_SOURCE_REVOKED", 5, w)

    def test_step_6_tenant_wins_over_revoked_so_a_stranger_learns_nothing(self):
        w = AuthzWorld()
        w.revoke("B-DOC-1")
        for patch in ({"tenant": TENANT_B}, {"account_id": "acct-b-gmail", "grant_id": "g-acct-b-gmail-read"}, {"source_id": "DECOY-DOC-1", "source_revision": 1},
                      {"source_id": "B-DOC-1", "source_revision": 1}, {"source_id": "NO-SUCH-SOURCE", "source_revision": 1}):
            with self.subTest(patch=patch):
                self.check(req(**patch), "DENY_TENANT", 6, w)

    def test_step_7_account(self):
        for patch in ({"account_id": None}, {"account_id": ""}, {"account_id": "latest"}, {"account_id": "default"}, {"account_id": "acct-zz"}, {"account_id": ...},
                      {"account_id": "acct-a-drive", "grant_id": "g-acct-a-drive-read"}):
            with self.subTest(patch=patch):
                self.check(req(**patch), "DENY_ACCOUNT_AMBIGUOUS", 7)

    def test_step_8_untrusted_authority(self):
        self.check(req(authority_claim="source_text"), "DENY_UNTRUSTED", 8)

    def test_step_9_unsupported(self):
        self.check(req(tool="no_such_tool"), "DENY_UNSUPPORTED_ACTION", 9)
        self.check(for_tool("composio_sandbox_run"), "DENY_UNSUPPORTED_ACTION", 9)             # N17

    def test_step_10_management_scope(self):
        for tool in ("composio_admin", "buzz_proxy_execute"):
            self.check(for_tool(tool, "write"), "DENY_MANAGEMENT_SCOPE", 10)
        w = AuthzWorld()
        w.management_agents.add("agent-a")
        self.check(for_tool("composio_admin", "write"), "DENY_PROTECTED", 16, w)                # a scope alone is not enough: it still needs the exact approval
        self.check(approved(for_tool("composio_admin", "write")), "ALLOW", 21, w)

    def test_step_11_unknown_or_multi_risk(self):
        for tool in ("multi_risk_tool", "mystery_tool"):
            self.check(for_tool(tool, "write"), "DENY_PROTECTED", 11, reason="unknown_or_multi_risk")

    def test_step_12_batch(self):
        self.check(req(batch=["gmail_read_message", "gmail_send_message"]), "DENY_BATCH_POLICY", 12)
        eleven = for_tool("notion_write_page", "write", batch=["notion_write_page"] * 11)
        self.check(eleven, "PENDING", 12, reason="bulk_needs_approval")
        self.check(for_tool("notion_write_page", "write", batch=["notion_write_page"] * 10), "ALLOW", 21)
        self.check(approved(eleven), "ALLOW", 21)                                              # Jim says yes

    def test_step_13_owner_reconsent(self):
        w = AuthzWorld()
        w.consent_needed.add("acct-a-gmail")
        self.check(req(), "NEEDS_OWNER_RECONSENT", 13, w)

    def test_step_14_grant(self):
        w = AuthzWorld()
        self.check(req(grant_id=None), "DENY_GRANT", 14)
        self.check(req(grant_id="g-acct-a-drive-read"), "DENY_GRANT", 14)
        self.check(req(grant_id="g-acct-b-gmail-read"), "DENY_GRANT", 14)
        for key, delta in (("expires", -timedelta(seconds=1)), ("review_due", -timedelta(days=1))):
            w2 = AuthzWorld()
            w2.grants["g-acct-a-gmail-read"][key] = NOW + delta
            self.check(req(), "DENY_GRANT", 14, w2)
        w.revoke_grant("g-acct-a-gmail-read")
        self.check(req(), "DENY_GRANT", 14, w)

    def test_step_14_reuses_the_access_policy_for_the_v7_services(self):
        self.check(for_tool("jira_read_issue"), "ALLOW", 21)
        self.check(for_tool("jira_write_issue", "write"), "DENY_GRANT", 14, reason="service_read_only_in_v1")         # Jira is read only in v1
        self.check(for_tool("notion_write_page", "write"), "ALLOW", 21)
        self.check(for_tool("notion_delete_page", "write"), "PENDING", 14, reason="delete_needs_approval")             # a delete waits for Jim

    def test_step_15_stale_source(self):
        got = self.check(req(source_id="SYN-1", source_revision=2), "DENY_STALE_SOURCE", 15)
        self.assertEqual(got["kind"], "SOURCE_STALE")
        self.check(req(source_id="SYN-1", source_revision=3), "ALLOW", 21)

    def test_step_16_protected_needs_a_write_grant_and_an_exact_approval(self):
        for tool in T.TOOLS:
            if len(T.TOOLS[tool]["actions"]) == 1 and T.TOOLS[tool]["actions"][0] in T.PROTECTED:
                with self.subTest(tool=tool):
                    self.check(for_tool(tool, "read"), "DENY_PROTECTED", 16)                   # N05: a read grant cannot send, share, book, publish or move money
                    self.check(for_tool(tool, "write"), "DENY_PROTECTED", 16)                  # a write grant without approval
                    self.check(approved(for_tool(tool, "read")), "DENY_PROTECTED", 16)         # approval does not make up for a read grant
                    self.check(approved(for_tool(tool, "write")), "ALLOW", 21)

    def test_step_17_approval_mismatch(self):
        good = approved(for_tool("gmail_send_message", "write"))["approval"]
        for patch in ({"issuer": "bot"}, {"host": "other-host"}, {"task": "task-b"}, {"account": "acct-a-drive"}, {"action_hash": "0" * 12}):
            with self.subTest(patch=patch):
                self.check({**for_tool("gmail_send_message", "write"), "approval": {**good, **patch}}, "DENY_APPROVAL_MISMATCH", 17)
        self.check({**for_tool("gmail_send_message", "write"), "approval": "yes"}, "DENY_APPROVAL_MISMATCH", 17)

    def test_step_18_secret_in_the_request(self):
        self.check(req(input_text=f"please use {CANARY}"), "DENY_SECRET_EXPOSURE", 18)

    def test_dual_faults_the_earliest_step_wins(self):
        w = AuthzWorld()
        w.grants["g-acct-a-gmail-read"]["expires"] = NOW - timedelta(days=1)
        self.check(req(tenant=TENANT_B), "DENY_TENANT", 6, w)                                  # wrong tenant beats an expired grant
        w.consent_needed.add("acct-a-gmail")
        self.check(req(), "NEEDS_OWNER_RECONSENT", 13, w)                                      # owner re-consent beats the generic grant denial
        self.check(req(authority_claim="source_text", tool="mystery_tool"), "DENY_UNTRUSTED", 8)   # untrusted text beats the generic risk denial
        self.check(for_tool("composio_admin", "read"), "DENY_MANAGEMENT_SCOPE", 10)            # management beats the generic protected denial
        eleven = for_tool("notion_write_page", "write", batch=["notion_write_page"] * 11)
        w2 = AuthzWorld()
        w2.grants["g-acct-a-notion-write"]["revoked"] = True
        self.check(eleven, "DENY_GRANT", 14, w2)                                               # (a) a held wait never hides a deny: 11 actions plus a revoked grant
        self.check(for_tool("notion_delete_page", "write", input_text=CANARY), "DENY_SECRET_EXPOSURE", 18)   # (b) delete without approval plus a secret
        self.check(req(tenant=TENANT_B, input_text=CANARY, authority_claim="x"), "DENY_TENANT", 6)

    def test_a_held_wait_is_pending_only_when_nothing_denies_it(self):
        delete = for_tool("notion_delete_page", "write")
        self.check(delete, "PENDING", 14, reason="delete_needs_approval")
        self.check(approved(delete), "ALLOW", 21)
        self.check(approved(delete, action_hash="bad"), "DENY_APPROVAL_MISMATCH", 17)


class ContractParity(unittest.TestCase):
    def test_the_mirror_matches_the_real_access_policy(self):
        """The synthetic services use mirror_decide; for notion (cap: write) the real decide() must give the same answer on every level, kind and count."""
        for level in access_policy.LEVELS:
            doc = {"version": 1, "bulk_limit": 10, "default": {}, "agents": {KEY_A: {"notion": level}}}
            policy = access_policy.load(json.dumps(doc))
            for kind in access_policy.KINDS:
                for count in (1, 10, 11):
                    with self.subTest(level=level, kind=kind, count=count):
                        self.assertEqual(authz.mirror_decide(level, kind, count), access_policy.decide(policy, KEY_A, "notion", kind, count))


if __name__ == "__main__":
    unittest.main()
