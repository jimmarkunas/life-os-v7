"""LIFE-381 / GWT-T10-01 to GWT-T10-05: integrated synthetic Google/Composio authorization denials and replayable evidence. Fake data only; this is not live Google, Composio or Orca enforcement."""
import copy
import json
import unittest

from lifeos.synthetic import integration as I, tools as T
from lifeos.synthetic.authz_fixture import CANARY, TENANT_A, TENANT_B

EVIDENCE = I.run_all()
BY_ID = {s["id"]: s for s in EVIDENCE["scenarios"]}


def of(gwt):
    return [s for s in EVIDENCE["scenarios"] if s["gwt"] == gwt]


class Evidence(unittest.TestCase):
    """GWT-T10-05: the same inputs give the same evidence, a separate checker holds it to the rules, and nothing claims to be live."""

    def test_every_scenario_met_its_expected_code_and_the_checker_finds_no_problem(self):
        self.assertEqual(I.verify(EVIDENCE), [])
        self.assertGreaterEqual(len(EVIDENCE["scenarios"]), 60)
        for s in EVIDENCE["scenarios"]:
            self.assertEqual(s["outcome"]["code"], s["expect"], s["id"])

    def test_the_replay_is_deterministic_and_pinned(self):
        again = I.run_all()
        self.assertEqual((again["digest"], again["scenarios"]), (EVIDENCE["digest"], EVIDENCE["scenarios"]))
        self.assertRegex(EVIDENCE["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(EVIDENCE["revision"], I.REVISION)

    def test_nothing_is_presented_as_live(self):
        self.assertIs(EVIDENCE["live_enforcement"], False)
        self.assertEqual({s["mode"] for s in EVIDENCE["scenarios"]}, {"synthetic_in_process"})
        self.assertEqual(EVIDENCE["mode"], "synthetic_in_process")

    def test_every_gwt_has_scenarios_and_every_receipt_carries_the_fixed_fields(self):
        for gwt in I.GWTS[:4]:
            self.assertTrue(of(gwt), gwt)
        for s in EVIDENCE["scenarios"]:
            if s["receipt"]:
                self.assertEqual(s["receipt"]["decision_code"], s["outcome"]["code"])
                self.assertIn("provider_state_hash_before", s["receipt"])

    def test_the_checker_catches_tampering(self):
        def broken(change):
            ev = copy.deepcopy(EVIDENCE)
            change(ev)
            return I.verify(ev)
        deny = next(i for i, s in enumerate(EVIDENCE["scenarios"]) if s["id"] == "deny-cross-tenant")
        allow = next(i for i, s in enumerate(EVIDENCE["scenarios"]) if s["id"] == "allow-a-gmail")
        self.assertTrue(broken(lambda e: e["scenarios"][deny]["effects"].update(reads=1)))                     # a refusal that read something
        self.assertTrue(broken(lambda e: e["scenarios"][deny]["effects"].update(ledger_changed=True)))
        self.assertTrue(broken(lambda e: e["scenarios"][deny].update(returned={"text": "Fake tenant B document."})))     # a refusal that returned data
        self.assertTrue(broken(lambda e: e["scenarios"][deny]["outcome"].update(code="ALLOW")))
        self.assertTrue(broken(lambda e: e["scenarios"][allow].update(returned={"text": "Fake tenant B document."})))    # an allow carrying another tenant's text
        self.assertTrue(broken(lambda e: e["scenarios"][deny].update(returned={"leak": CANARY})))
        self.assertTrue(broken(lambda e: e.update(live_enforcement=True)))
        self.assertTrue(broken(lambda e: e.update(revision="abc")))
        self.assertTrue(broken(lambda e: e.update(scenarios=[s for s in e["scenarios"] if s["gwt"] != "GWT-T10-02"])))
        self.assertTrue(broken(lambda e: e["scenarios"][allow].update(id="deny-cross-tenant")))                # a duplicated scenario id
        first_life = next(i for i, s in enumerate(EVIDENCE["scenarios"]) if s["id"] == "life-oracle-refreshes-expiring")
        self.assertTrue(broken(lambda e: e["scenarios"][first_life].update(human_logins=1)))                  # a login before the owner step


class PermittedReads(unittest.TestCase):
    """GWT-T10-01"""

    def test_each_route_returns_only_its_exact_account_tenant_and_text(self):
        expect = {"allow-a-gmail": ("a-gmail", TENANT_A, "acct-a-gmail", "Fake tenant A message."), "allow-a-drive": ("a-drive", TENANT_A, "acct-a-drive", "Fake tenant A document."),
                  "allow-b-gmail": ("b-gmail", TENANT_B, "acct-b-gmail", "Fake tenant B message."), "allow-b-drive": ("b-drive", TENANT_B, "acct-b-drive", "Fake tenant B document.")}
        for sid, (route, tenant, account, text) in expect.items():
            got = BY_ID[sid]["returned"]
            self.assertEqual((got["route"], got["tenant"], got["account"], got["text"]), (route, tenant, account, text), sid)
            self.assertEqual((BY_ID[sid]["effects"]["reads"], BY_ID[sid]["effects"]["mutations"], BY_ID[sid]["effects"]["commits"]), (1, 0, 0))

    def test_the_composio_reads_use_the_exact_connected_account(self):
        self.assertEqual(BY_ID["allow-composio-a-gmail"]["returned"]["connected_account_id"], "ca-a1")
        self.assertEqual(BY_ID["allow-composio-a-drive"]["returned"]["connected_account_id"], "ca-a2")

    def test_the_cross_app_summary_keeps_its_provenance_and_stays_inside_one_tenant(self):
        for sid, tenant_text in (("allow-cited-summary-a", "tenant A"), ("allow-cited-summary-b", "tenant B")):
            summary = BY_ID[sid]["returned"]
            self.assertEqual(len(summary["citations"]), 2)
            self.assertEqual({c["provider"] for c in summary["citations"]}, {"gmail", "drive"})
            self.assertTrue(all(c["attribution"] == f"{c['provider']}/{c['account']}" and c["revision"] >= 1 for c in summary["citations"]))
            self.assertIn(tenant_text, summary["text"])
            self.assertNotIn("tenant B" if tenant_text == "tenant A" else "tenant A", summary["text"])

    def test_a_summary_that_would_cross_tenants_is_refused_before_anything_is_read_or_cached(self):
        s = BY_ID["deny-summary-crossing-tenants"]
        self.assertEqual((s["outcome"]["code"], s["returned"], s["effects"]["reads"], s["effects"]["derived"]), ("DENY_TENANT", None, 0, 0))
        w = I.IntegratedWorld()
        out = I.cited_summary(w, "agent-a", [("acct-a-gmail", "P-MSG-1"), ("acct-b-drive", "B-DOC-1")])
        self.assertEqual((out["summary"], w.derived, w.read_calls, w.req_nonces), (None, {}, 0, set()))         # the permitted first item was not even read

    def test_discovery_shows_only_the_callers_own_tenant(self):
        self.assertEqual(I.G.visible_routes(I.IntegratedWorld(), "agent-a"), ["a-drive", "a-gmail"])
        self.assertEqual(I.G.visible_routes(I.IntegratedWorld(), "agent-b"), ["b-drive", "b-gmail"])
        self.assertEqual(BY_ID["allow-discovery-a"]["returned"], ["drive_read_file", "drive_share_file"])        # the share tool is listed too: listing is not the control, the gate is


class Refusals(unittest.TestCase):
    """GWT-T10-02"""

    REQUIRED = ("missing-account", "wrong-account", "cross-tenant", "revoked-grant", "expired-grant", "forged-approval", "replayed-approval", "forged-buzz-command",
                "replayed-buzz-command", "prompt-injection", "disabled-host", "stale-source", "unclassified-risk", "discovery-missing-account", "discovery-cross-tenant")

    def test_every_listed_refusal_exists_with_a_named_machine_readable_code(self):
        for name in self.REQUIRED:
            s = BY_ID[f"deny-{name}"]
            self.assertRegex(s["outcome"]["code"], r"^(DENY_[A-Z_]+|NEEDS_OWNER_RECONSENT)$", name)
            self.assertEqual(s["outcome"]["code"], s["expect"])

    def test_each_refusal_has_zero_side_effects_and_unchanged_hashes(self):
        refusals = [s for s in EVIDENCE["scenarios"] if s["kind"] in ("deny", "hold")]
        self.assertGreaterEqual(len(refusals), 45)
        for s in refusals:
            self.assertFalse(any(s["effects"][k] for k in I.COUNTERS) or s["effects"]["provider_changed"] or s["effects"]["ledger_changed"], (s["id"], s["effects"]))
            self.assertIsNone(s["returned"], s["id"])
            if s["receipt"]:
                r = s["receipt"]
                self.assertEqual((r["read_handler_calls"], r["mutation_calls"], r["commits"], r["provider_state_hash_before"], r["policy_ledger_hash_before"]),
                                 (0, 0, 0, r["provider_state_hash_after"], r["policy_ledger_hash_after"]), s["id"])

    def test_no_refusal_leaks_a_source_text(self):
        w = I.IntegratedWorld()
        texts = [src["text"] for src in w.sources.values()] + [CANARY]
        for s in EVIDENCE["scenarios"]:
            if s["kind"] in ("deny", "hold"):
                blob = json.dumps(s, default=str)
                for t in texts:
                    self.assertNotIn(t, blob, s["id"])

    def test_the_derived_cache_is_held_to_the_same_rule(self):
        self.assertEqual(BY_ID["allow-cache-own-tenant"]["outcome"]["code"], "ALLOW")
        for sid, code in (("deny-cache-after-source-revoked", "DENY_SOURCE_REVOKED"), ("deny-cache-other-tenant", "DENY_TENANT"), ("deny-cache-never-built", "DENY_TENANT")):
            self.assertEqual((BY_ID[sid]["outcome"]["code"], BY_ID[sid]["returned"]), (code, None), sid)
        w = I.IntegratedWorld()
        I.cited_summary(w, "agent-a", I.SUMMARY_A)
        self.assertEqual(I.cache_lookup(w, "agent-b", I.SUMMARY_KEY)["code"], I.cache_lookup(w, "agent-b", ("nothing", 0))["code"])        # a foreign and a missing entry look the same
        w.sources["P-DOC-1"]["revision"] = 2
        self.assertEqual(I.cache_lookup(w, "agent-a", I.SUMMARY_KEY)["code"], "DENY_STALE_SOURCE")

    def test_a_wrong_tenant_call_against_a_dead_connection_changes_nothing(self):
        s = BY_ID["deny-wrong-tenant-on-dead-connection"]
        self.assertEqual((s["outcome"]["code"], s["effects"]["ledger_changed"]), ("DENY_TENANT", False))

    def test_a_replayed_approval_did_not_commit_a_second_send(self):
        w = I.IntegratedWorld()
        I._approved_send(w)
        self.assertEqual((len(w.outbox), w.commits), (1, 1))
        self.assertEqual(BY_ID["deny-replayed-approval"]["effects"]["outbox"], 0)


class ProtectedActions(unittest.TestCase):
    """GWT-T10-03"""

    def test_every_protected_and_management_action_is_refused_on_a_read_grant_and_on_a_write_grant_without_approval(self):
        for name in ("send", "share", "booking", "social-post", "financial"):
            for kind in ("read-grant", "write-grant-no-approval"):
                s = BY_ID[f"deny-{kind}-{name}"]
                self.assertEqual((s["outcome"]["code"], s["outcome"]["step"]), ("DENY_PROTECTED", 16), s["id"])
        for name in ("admin", "proxy-execute"):
            for kind in ("read-grant", "write-grant"):
                s = BY_ID[f"deny-{kind}-{name}"]
                self.assertEqual((s["outcome"]["code"], s["outcome"]["step"]), ("DENY_MANAGEMENT_SCOPE", 10), s["id"])

    def test_delete_is_held_for_jim_and_production_and_sandbox_actions_are_unsupported(self):
        self.assertEqual((BY_ID["hold-delete-needs-jim"]["outcome"]["code"], BY_ID["hold-delete-needs-jim"]["kind"]), ("PENDING", "hold"))
        self.assertEqual(BY_ID["deny-production-action"]["outcome"]["code"], "DENY_UNSUPPORTED_ACTION")
        self.assertEqual(BY_ID["deny-composio-sandbox"]["outcome"]["code"], "DENY_UNSUPPORTED_ACTION")

    def test_a_signed_admin_cannot_proxy_execute_or_reveal_a_key(self):
        for sid in ("deny-signed-proxy-execute", "deny-signed-reveal-key"):
            self.assertEqual(BY_ID[sid]["outcome"]["code"], "DENY_MANAGEMENT_SCOPE")

    def test_the_tools_are_listed_to_the_model_and_still_refused_by_the_gate(self):
        """Hiding a tool would prove nothing. These tools are all registered (visible); the refusal comes from the gate, before the handler."""
        for sid, tool in (("deny-read-grant-send", "gmail_send_message"), ("deny-read-grant-share", "drive_share_file"), ("deny-read-grant-booking", "calendar_book_slot"),
                          ("deny-read-grant-social-post", "social_publish_post"), ("deny-read-grant-financial", "finance_transfer"), ("deny-read-grant-admin", "composio_admin"),
                          ("deny-read-grant-proxy-execute", "buzz_proxy_execute")):
            self.assertIn(tool, T.TOOLS)
            r = BY_ID[sid]["receipt"]
            self.assertEqual((r["decision_stage"], r["read_handler_calls"], r["mutation_calls"], r["commits"]), ("preflight", 0, 0, 0), sid)


class Lifecycle(unittest.TestCase):
    """GWT-T10-04"""

    STEPS = [s for s in EVIDENCE["scenarios"] if s["gwt"] == "GWT-T10-04"]

    def test_normal_operation_needs_no_owner_login_and_only_the_owner_step_adds_one(self):
        owner = EVIDENCE["owner_step_index"]
        self.assertTrue(all(s["human_logins"] == 0 for s in self.STEPS[:owner + 1]))
        self.assertTrue(all(s["human_logins"] == 1 for s in self.STEPS[owner + 1:]))
        self.assertEqual(self.STEPS[0]["returned"]["refreshed"], 1)                                   # the Oracle role refreshed the expiring token
        self.assertEqual(BY_ID["life-read-after-refresh"]["outcome"]["code"], "ALLOW")

    def test_expiry_and_revocation_are_blocked_for_the_owner_and_automation_cannot_revive_them(self):
        for sid in ("life-expired-needs-owner", "life-revoked-needs-owner", "life-still-blocked-after-admin-refresh", "life-other-account-still-blocked"):
            self.assertEqual(BY_ID[sid]["outcome"]["code"], "NEEDS_OWNER_RECONSENT", sid)
            self.assertEqual(BY_ID[sid]["effects"]["provider_calls"], 0)
        self.assertEqual(BY_ID["life-after-owner-reconsent"]["outcome"]["code"], "ALLOW")

    def test_a_disabled_connection_is_denied_and_no_secret_reaches_buzz_the_vendor_log_or_the_results(self):
        self.assertEqual(BY_ID["life-disabled-is-denied"]["outcome"]["code"], "DENY_GRANT")
        text = json.dumps(EVIDENCE, default=str)
        for secret in ("fake-vendor-token-", "fake-manager-key-", I.C.ADMIN_KEY, CANARY):
            self.assertNotIn(secret, text)
        kinds = {n["kind"] for n in EVIDENCE["lifecycle_buzz"]}
        self.assertEqual(kinds, {"status", "approval_request"})
        for n in EVIDENCE["lifecycle_buzz"]:
            self.assertLessEqual(set(n), set(I.C.BUZZ_FIELDS))


if __name__ == "__main__":
    unittest.main()
