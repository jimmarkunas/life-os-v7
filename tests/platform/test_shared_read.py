import json
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.platform import access_policy as A
from lifeos.platform import shared_read as S

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
CC, CX, OTHER = "5d77" + "0" * 60, "c0de" + "0" * 60, "ee" * 32
POLICY = A.load(json.dumps({"version": 1, "default": {}, "agents": {CC: {"jira": "read", "github": "read"}, CX: {"jira": "read"}, OTHER: {"jira": "off"}}}))
ISSUE = {"key": "LIFE-1", "summary": "a title", "status": "To Do", "updated": "2026-11-16", "assignee": "must not leave", "description": "nor this"}


def grant(client, principal, tenant="personal", **kw):
    return S.Grant(**{"client": client, "principal": principal, "tenant": tenant, "account": "acct-1", "tool": "jira.get_issue", "credential_ref": "bws-jira-read-" + client,
                      "expires": NOW + timedelta(hours=1), **kw})


class Harness:
    def __init__(self, grants=None, at=NOW, data=ISSUE, boom=False):
        self.grants = tuple(grants if grants is not None else (grant("claude-code", CC), grant("codex", CX)))
        self.receipts, self.fetched, self.at, self.data, self.boom = [], [], at, data, boom

    def fetch(self, ref, tool, args):
        self.fetched.append((ref, tool, args))
        if self.boom:
            raise RuntimeError("secret detail that must not appear")
        return {"data": self.data, "request_id": "jira-req-" + str(len(self.fetched)), "fetched_at": self.at}

    def call(self, client="claude-code", principal=CC, tenant="personal", tool="jira.get_issue", account="acct-1", trusted=True, policy=POLICY, audit=None, rid="r1"):
        return S.read(S.Session(client, principal, tenant, trusted), tool, account, {"key": "LIFE-1"}, self.grants, policy, self.fetch, audit or self.receipts.append, NOW, rid)


class Read(unittest.TestCase):
    def test_two_clients_read_the_same_source_with_separate_attribution_and_minimal_data(self):
        h = Harness()
        a, b = h.call(), h.call("codex", CX, rid="r2")
        self.assertEqual((a.decision, a.reason, b.reason), ("allow", "ok", "ok"))
        self.assertEqual(set(a.data), set(S.FIELDS))                                           # assignee and description never leave
        self.assertEqual([r["client"] for r in h.receipts if r["outcome"] == "ok"], ["claude-code", "codex"])
        self.assertNotEqual(h.receipts[0]["principal_fp"], h.receipts[2]["principal_fp"])
        self.assertEqual([f[0] for f in h.fetched], ["bws-jira-read-claude-code", "bws-jira-read-codex"])   # each client uses its own credential NAME

    def test_receipts_hold_fixed_words_ids_and_fingerprints_only(self):
        h = Harness()
        h.call()
        text = json.dumps(h.receipts)
        for forbidden in (CC, "LIFE-1", "a title", "assignee", "bws-jira"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(h.receipts[-1]), {"t", "client", "principal_fp", "tenant", "tool", "request_id", "source_request_id", "outcome", "reason", "age_s"})
        self.assertEqual(h.receipts[-1]["source_request_id"], "jira-req-1")

    def test_no_receipt_no_read(self):
        h = Harness()

        def broken(_):
            raise OSError("disk full")
        with self.assertRaises(OSError):
            h.call(audit=broken)
        self.assertEqual(h.fetched, [])


class FailClosed(unittest.TestCase):
    def refused(self, reason, **kw):
        h = Harness(**kw.pop("harness", {}))
        answer = h.call(**kw)
        self.assertEqual((answer.decision, answer.reason, h.fetched), ("deny", reason, []))
        self.assertEqual(h.receipts[-1]["outcome"], "deny")                                    # a refusal has a receipt and no read

    def test_unknown_tool_and_any_write(self):
        self.refused("unknown_tool", tool="jira.delete_issue")
        self.refused("unknown_tool", tool="jira.create_issue")
        self.refused("unknown_tool", tool="*")

    def test_the_write_path_is_closed_even_if_a_write_tool_is_registered(self):
        S.TOOLS["jira.edit_issue"] = ("jira", "write")
        try:
            self.refused("write_refused", tool="jira.edit_issue")
        finally:
            del S.TOOLS["jira.edit_issue"]

    def test_tenants(self):
        self.refused("tenant_excluded", tenant="tgp")
        self.refused("tenant_excluded", tenant="TGP")
        self.refused("unknown_tenant", tenant="unknown")
        self.refused("unknown_tenant", tenant="")

    def test_untrusted_session(self):
        self.refused("untrusted_session", trusted=False)

    def test_policy_off_unknown_agent_and_unreadable_policy(self):
        self.refused("policy_denied", client="codex", principal=OTHER, harness={"grants": [grant("codex", OTHER)]})
        self.refused("policy_denied", principal="not-hex")
        self.refused("policy_denied", policy=None)

    def test_wrong_account_wrong_tenant_and_borrowed_grant(self):
        self.refused("no_grant", account="acct-2")
        self.refused("no_grant", tenant="bytalos")                                              # a Personal grant is not a Bytalos grant
        self.refused("no_grant", client="codex")                                                # Codex with the Claude Code principal borrows nothing
        self.refused("no_grant", client="claude-code", principal=CX)

    def test_revoked_and_expired(self):
        self.refused("grant_revoked", harness={"grants": [grant("claude-code", CC, revoked=True)]})
        self.refused("grant_expired", harness={"grants": [grant("claude-code", CC, expires=NOW)]})
        self.refused("grant_expired", harness={"grants": [grant("claude-code", CC, expires=NOW - timedelta(seconds=1))]})

    def test_stale_and_future_dated_responses_are_not_returned(self):
        for at in (NOW - timedelta(seconds=301), NOW + timedelta(seconds=61)):
            h = Harness(at=at)
            answer = h.call()
            self.assertEqual((answer.reason, answer.data), ("stale_response", None))
            self.assertEqual(h.receipts[-1]["outcome"], "stale")
        self.assertEqual(Harness(at=NOW - timedelta(seconds=300)).call().reason, "ok")

    def test_connector_failure_hides_the_error_text(self):
        h = Harness(boom=True)
        answer = h.call()
        self.assertEqual((answer.decision, answer.reason, answer.data), ("allow", "fetch_failed", None))
        self.assertNotIn("secret detail", json.dumps(h.receipts))

    def test_a_secret_in_the_response_is_refused(self):
        h = Harness(data={**ISSUE, "summary": "token = abcdefgh12345678"})
        answer = h.call()
        self.assertEqual((answer.reason, answer.data), ("output_has_secret", None))


class Isolation(unittest.TestCase):
    def test_personal_and_bytalos_do_not_share_grants_identity_or_cache(self):
        h = Harness(grants=[grant("claude-code", CC, "personal"), grant("claude-code", CC, "bytalos", account="acct-b", credential_ref="bws-jira-read-bytalos")])
        self.assertEqual(h.call(tenant="personal").reason, "ok")
        self.assertEqual(h.call(tenant="bytalos").reason, "no_grant")                           # wrong account for the Bytalos grant
        self.assertEqual(h.call(tenant="bytalos", account="acct-b").reason, "ok")
        self.assertEqual(h.call(tenant="personal", account="acct-b").reason, "no_grant")
        self.assertEqual(len(h.fetched), 2)                                                     # every allowed read goes to the source: there is no cache to share
        self.assertFalse([n for n in vars(S) if n.lower() in ("cache", "_cache", "store")])

    def test_revoking_one_client_leaves_the_other_and_other_sources_alone(self):
        h = Harness()
        h.grants = S.revoke(h.grants + (grant("claude-code", CC, tool="jira.get_issue", account="acct-2"),), "claude-code")
        self.assertEqual(h.call().reason, "grant_revoked")
        self.assertEqual(h.call("codex", CX).reason, "ok")
        self.assertEqual(S.revoke((), "codex"), ())
        self.assertEqual(A.decide(POLICY, CC, "github", "read")[0], "allow")                    # the policy for other services did not change
        self.assertEqual(A.decide(POLICY, CC, "notion", "read"), ("deny", "service_off"))       # and nothing new became implicitly allowed

    def test_the_module_imports_no_network_or_credential_code(self):
        import pathlib
        text = pathlib.Path(S.__file__).read_text()
        for word in ("urllib", "requests", "socket", "subprocess", "os.environ", "open("):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
