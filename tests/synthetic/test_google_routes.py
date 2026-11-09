"""LIFE-379 / GWT-T08-01, GWT-T08-02 and GWT-T08-04: four exact Google routes, a fail-closed router, and native ChatGPT connections that stay separate. Fake data only."""
import json
import unittest

from lifeos.synthetic import google_routes as G
from lifeos.synthetic.authz_fixture import TENANT_A, TENANT_B


class PermittedReads(unittest.TestCase):
    CASES = [("agent-a", "acct-a-gmail", "P-MSG-1", "a-gmail", TENANT_A, "Fake tenant A message."), ("agent-a", "acct-a-drive", "P-DOC-1", "a-drive", TENANT_A, "Fake tenant A document."),
             ("agent-b", "acct-b-gmail", "B-MSG-1", "b-gmail", TENANT_B, "Fake tenant B message."), ("agent-b", "acct-b-drive", "B-DOC-1", "b-drive", TENANT_B, "Fake tenant B document.")]

    def test_each_route_returns_only_its_own_account_s_source_with_attribution(self):
        for who, account, obj, route, tenant, text in self.CASES:
            with self.subTest(route=route):
                w = G.RouteWorld()
                receipt, rec = G.route_read(w, who, account, obj)
                self.assertEqual((receipt["decision_code"], receipt["read_handler_calls"], receipt["commits"]), ("ALLOW", 1, 0))
                self.assertEqual((rec["route"], rec["tenant"], rec["account"], rec["object_id"], rec["text"], rec["attribution"]),
                                 (route, tenant, account, obj, text, f"{rec['provider']}/{account}"))
                other = "Fake tenant B" if tenant == TENANT_A else "Fake tenant A"
                self.assertNotIn(other, json.dumps(rec))

    def test_a_route_never_returns_another_account_s_source(self):
        """The same four fake owners, every wrong pairing: nothing comes back and no handler runs."""
        w = G.RouteWorld()
        wrong = [("agent-a", "acct-a-gmail", "B-MSG-1", "DENY_TENANT"), ("agent-b", "acct-b-gmail", "P-MSG-1", "DENY_TENANT"), ("agent-a", "acct-a-drive", "B-DOC-1", "DENY_TENANT"),
                 ("agent-a", "acct-a-gmail", "P-DOC-1", "DENY_ACCOUNT_AMBIGUOUS"),        # a document needs the drive account, not the gmail one
                 ("agent-a", "acct-a-drive", "P-MSG-1", "DENY_ACCOUNT_AMBIGUOUS"), ("agent-b", "acct-b-drive", "B-MSG-1", "DENY_ACCOUNT_AMBIGUOUS")]
        for n, (who, account, obj, code) in enumerate(wrong):
            with self.subTest(who=who, account=account, obj=obj):
                receipt, rec = G.route_read(w, who, account, obj, nonce=f"w{n}")
                self.assertEqual((receipt["decision_code"], rec, receipt["read_handler_calls"]), (code, None, 0))


class FailClosed(unittest.TestCase):
    def refused(self, receipt, rec, code):
        self.assertEqual((receipt["decision_code"], rec, receipt["read_handler_calls"], receipt["mutation_calls"], receipt["commits"]), (code, None, 0, 0, 0))
        self.assertEqual((receipt["provider_state_hash_before"], receipt["policy_ledger_hash_before"]), (receipt["provider_state_hash_after"], receipt["policy_ledger_hash_after"]))
        self.assertEqual((receipt["audit"]["decision"], receipt["audit"]["reason"]), ("deny", code))                    # the denial is auditable

    def test_a_missing_ambiguous_default_or_most_recent_account_never_reaches_a_handler(self):
        for alias in (None, "", "personal", "work", "default", "latest", "most-recent", "me", "acct-zz"):
            with self.subTest(alias=alias):
                w = G.RouteWorld()
                receipt, rec = G.route_read(w, "agent-a", alias, "P-MSG-1")
                self.refused(receipt, rec, "DENY_ACCOUNT_AMBIGUOUS")
                d_receipt, tools = G.discover(w, "agent-a", alias)                                                      # discovery fails the same way
                self.assertEqual((d_receipt["decision_code"], tools, d_receipt["read_handler_calls"]), ("DENY_ACCOUNT_AMBIGUOUS", [], 0))

    def test_a_crossed_user_is_refused_for_reads_and_for_discovery(self):
        w = G.RouteWorld()
        receipt, rec = G.route_read(w, "agent-a", "acct-b-gmail", "B-MSG-1")
        self.refused(receipt, rec, "DENY_TENANT")
        receipt, rec = G.route_read(w, "agent-b", "acct-a-drive", "P-DOC-1")
        self.refused(receipt, rec, "DENY_TENANT")
        for who, account in (("agent-a", "acct-b-gmail"), ("agent-b", "acct-a-drive")):
            d_receipt, tools = G.discover(w, who, account)
            self.assertEqual((d_receipt["decision_code"], tools, d_receipt["read_handler_calls"]), ("DENY_TENANT", [], 0))

    def test_an_unknown_caller_is_refused(self):
        w = G.RouteWorld()
        receipt, rec = G.route_read(w, "agent-rogue", "acct-a-gmail", "P-MSG-1")
        self.refused(receipt, rec, "DENY_PRINCIPAL")

    def test_discovery_of_an_exact_own_account_lists_only_that_provider_s_tools(self):
        w = G.RouteWorld()
        receipt, tools = G.discover(w, "agent-a", "acct-a-gmail")
        self.assertEqual((receipt["decision_code"], receipt["read_handler_calls"]), ("ALLOW", 0))
        self.assertEqual(tools, ["gmail_read_message", "gmail_send_message", "multi_risk_tool", "mystery_tool"])
        self.assertTrue(all(name.startswith(("gmail", "multi", "mystery")) for name in tools))

    def test_a_caller_sees_only_its_own_tenant_s_routes(self):
        w = G.RouteWorld()
        self.assertEqual(G.visible_routes(w, "agent-a"), ["a-drive", "a-gmail"])
        self.assertEqual(G.visible_routes(w, "agent-b"), ["b-drive", "b-gmail"])
        self.assertEqual(G.visible_routes(w, "agent-rogue"), [])


class FourDistinctRoutesAndNativeConnections(unittest.TestCase):
    def test_the_four_routes_are_distinct_and_exact(self):
        self.assertEqual(sorted(G.ROUTES), ["a-drive", "a-gmail", "b-drive", "b-gmail"])
        self.assertEqual(len({r["account"] for r in G.ROUTES.values()}), 4)
        self.assertEqual(len({(r["tenant"], r["provider"]) for r in G.ROUTES.values()}), 4)
        w = G.RouteWorld()
        for rid, r in G.ROUTES.items():
            self.assertEqual(w.accounts[r["account"]], (r["tenant"], r["provider"]), rid)

    def test_native_chatgpt_connections_stay_separate_and_no_token_is_copied(self):
        w = G.RouteWorld()
        route_accounts = {r["account"] for r in G.ROUTES.values()}
        for provider, native in G.NATIVE_CHATGPT.items():
            self.assertEqual((native["managed_by"], native["token_copied"], native["token_reused"]), ("chatgpt", False, False))
            self.assertNotIn(native["account_label"], route_accounts | set(w.accounts))                                 # a native account is never one of the four routes
        for text in (json.dumps(G.ROUTES), json.dumps(w.grants, default=str)):
            self.assertNotIn("refresh", text.lower())
            self.assertNotIn("secret", text.lower())


if __name__ == "__main__":
    unittest.main()
