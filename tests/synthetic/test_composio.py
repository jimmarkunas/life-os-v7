"""LIFE-380 / GWT-T09-01 to GWT-T09-05: Composio bounded hybrid custody, simulated. Fake data only: no Composio project, no key, no network, no account."""
import json
import unittest
from datetime import timedelta

from lifeos.synthetic import composio as C
from lifeos.synthetic.authz_fixture import NOW

SECRETS = (C.ADMIN_KEY, "fake-vendor-token-", "fake-manager-key-")


def everything(world, *outputs):
    """Every place a secret could leak to: the results, the receipts, the Buzz outbox and the vendor log."""
    return json.dumps([outputs, world.buzz_outbox, world.vendor_log], default=str)


def no_secret(test, world, *outputs):
    text = everything(world, *outputs)
    for secret in SECRETS:
        test.assertNotIn(secret, text)


class ExactAccountSelection(unittest.TestCase):
    def test_the_exact_connected_account_is_used_and_no_credential_appears(self):
        w = C.ComposioWorld()
        r1, out1 = C.execute(w, "agent-a", "acct-a-gmail")
        r2, out2 = C.execute(w, "agent-a", "acct-a-drive", nonce="n2", source_id="DOC-1")
        self.assertEqual((r1["decision_code"], out1["connected_account_id"], out1["provider"]), ("ALLOW", "ca-a1", "gmail"))
        self.assertEqual((r2["decision_code"], out2["connected_account_id"], out2["provider"]), ("ALLOW", "ca-a2", "drive"))
        self.assertEqual(w.provider_calls, 2)
        no_secret(self, w, out1, out2, r1, r2)
        self.assertEqual(set(out1), {"connected_account_id", "account", "provider", "text"})                      # nothing that looks like a credential field

    def test_no_default_and_no_most_recent_fallback(self):
        for alias in (None, "", "default", "latest", "most-recent", "acct-zz", "acct-a-jira"):          # the last is a real account with no connection
            with self.subTest(alias=alias):
                w = C.ComposioWorld()
                receipt, out = C.execute(w, "agent-a", alias)
                self.assertEqual((receipt["decision_code"], out, w.provider_calls, receipt["read_handler_calls"]), ("DENY_ACCOUNT_AMBIGUOUS", None, 0, 0))

    def test_a_crossed_tenant_or_provider_is_refused_before_the_provider(self):
        w = C.ComposioWorld()
        receipt, out = C.execute(w, "agent-a", "acct-b-gmail")
        self.assertEqual((receipt["decision_code"], out, w.provider_calls), ("DENY_TENANT", None, 0))
        receipt, out = C.execute(w, "agent-a", "acct-a-gmail", source_id="DOC-1", nonce="n3")                # a drive document asked through the gmail account
        self.assertEqual((receipt["decision_code"], out, w.provider_calls), ("DENY_ACCOUNT_AMBIGUOUS", None, 0))


def upkeep(w, runner):
    """A signed upkeep run with a fresh nonce."""
    return C.maintain(w, C.upkeep_command(w, runner, f"u{len(w.manager_nonces)}"))


class RoutineUpkeepNeedsNoHuman(unittest.TestCase):
    def test_expiring_tokens_are_refreshed_and_health_is_reported_with_no_login(self):
        w = C.ComposioWorld()
        before = w.connections["ca-a1"]["expires"]
        out = upkeep(w, "oracle-admin")
        self.assertEqual((out["outcome"], out["counts"], out["human_logins"], w.human_logins), ("done", {"refreshed": 1, "healthy": 1, "needs_reconsent": 2, "disabled": 0}, 0, 0))
        self.assertGreater(w.connections["ca-a1"]["expires"], before)
        self.assertEqual((w.connections["ca-a1"]["refreshes"], w.connections["ca-a2"]["refreshes"]), (1, 0))
        self.assertEqual(upkeep(w, "github-admin")["counts"]["refreshed"], 0)                    # a second run has nothing left to refresh
        self.assertEqual(w.human_logins, 0)

    def test_buzz_carries_only_scoped_status_and_authorized_approval_requests(self):
        w = C.ComposioWorld()
        upkeep(w, "oracle-admin")
        upkeep(w, "github-admin")
        kinds = [n["kind"] for n in w.buzz_outbox]
        self.assertEqual(sorted(set(kinds)), ["approval_request", "status"])
        self.assertEqual(kinds.count("approval_request"), 2)                                          # one per account that needs the owner, not one per run
        for notice in w.buzz_outbox:
            self.assertLessEqual(set(notice), set(C.BUZZ_FIELDS))
        no_secret(self, w)
        with self.assertRaises(ValueError):
            C._notice(w, kind="status", token="x")                                                   # a notice cannot even be built with a credential field

    def test_only_a_runner_proven_by_a_signed_command_runs_the_upkeep(self):
        """GWT-T11-02: a bare name, an unregistered sender, a forged key, a changed command and a replay are all refused, and nothing changes."""
        w = C.ComposioWorld()
        good = C.upkeep_command(w, "oracle-admin", "u-good")
        changed = {**good, "nonce": "u-other"}                                                        # edited after signing
        wrong_project = C.upkeep_command(w, "oracle-admin", "u-p", project="proj-other")
        wrong_action = C.maintain(w, {**C.upkeep_command(w, "oracle-admin", "u-a"), "action": "disable"})
        bare = [("oracle-admin", "DENY_INPUT"), ("", "DENY_INPUT"), (None, "DENY_INPUT")]
        cases = [(name, C.maintain(w, name)["code"]) for name, _ in bare]
        self.assertEqual(cases, bare)                                                                 # a string, even a registered runner's name, proves nothing
        for cmd, code in ((C.upkeep_command(w, "agent-a", "u1"), "DENY_PRINCIPAL"), (C.upkeep_command(w, "agent-rogue", "u2"), "DENY_PRINCIPAL"),
                          (C.upkeep_command(w, "oracle-admin", "u3", key="fake-manager-key-github"), "DENY_PRINCIPAL"), (changed, "DENY_PRINCIPAL"),
                          (wrong_project, "DENY_TENANT"), ({k: v for k, v in good.items() if k != "sig"}, "DENY_INPUT")):
            self.assertEqual(C.maintain(w, cmd), {"outcome": "refused", "code": code})
        self.assertEqual(wrong_action["code"], "DENY_PRINCIPAL")                                       # the action is part of what was signed
        self.assertEqual((w.admin_calls, w.buzz_outbox, w.manager_nonces, w.human_logins), (0, [], set(), 0))
        self.assertEqual(C.maintain(w, good)["outcome"], "done")
        calls, notices = w.admin_calls, len(w.buzz_outbox)
        self.assertEqual(C.maintain(w, good), {"outcome": "refused", "code": "DENY_REPLAY_OR_WRITER"})   # the same command twice is a replay
        self.assertEqual((w.admin_calls, len(w.buzz_outbox)), (calls, notices))

    def test_a_correctly_signed_command_for_another_action_is_not_upkeep(self):
        w = C.ComposioWorld()
        body = {"sender": "oracle-admin", "project": w.project, "action": "disable", "nonce": "u1"}
        cmd = {**body, "sig": C.sign(C.MANAGER_KEYS["oracle-admin"], body)}
        self.assertEqual(C.maintain(w, cmd), {"outcome": "refused", "code": "DENY_MANAGEMENT_SCOPE"})
        self.assertEqual((w.admin_calls, w.manager_nonces), (0, set()))

    def test_a_refused_runner_changes_nothing_in_the_world(self):
        w = C.ComposioWorld()
        before = (w.ledger_hash(), w.provider_hash(), {c: dict(v) for c, v in w.connections.items()}, w.admin_calls, list(w.buzz_outbox), set(w.notified))
        for cmd in ("oracle-admin", C.upkeep_command(w, "agent-rogue", "u1"), C.upkeep_command(w, "oracle-admin", "u2", key="wrong")):
            C.maintain(w, cmd)
        self.assertEqual((w.ledger_hash(), w.provider_hash(), w.connections, w.admin_calls, w.buzz_outbox, w.notified), before)

    def test_the_registered_oracle_and_github_runners_still_refresh_with_no_login(self):
        w = C.ComposioWorld()
        out = C.maintain(w, C.upkeep_command(w, "github-admin", "u1"))
        self.assertEqual((out["runner"], out["counts"]["refreshed"], out["human_logins"], w.human_logins), ("github-admin", 1, 0, 0))
        self.assertEqual(C.maintain(w, C.upkeep_command(w, "oracle-admin", "u1"))["outcome"], "done")  # a nonce is per sender


class OwnerReconsent(unittest.TestCase):
    def test_an_expired_or_revoked_grant_denies_the_provider_and_names_the_owner_step(self):
        w = C.ComposioWorld()
        upkeep(w, "oracle-admin")
        for account in ("acct-b-gmail", "acct-b-drive"):
            receipt, out = C.execute(w, "agent-b", account, nonce=f"n-{account}")
            self.assertEqual((receipt["decision_code"], receipt["decision_step"], out, receipt["read_handler_calls"]), ("NEEDS_OWNER_RECONSENT", 13, None, 0))
        self.assertEqual(w.provider_calls, 0)
        asks = [n for n in w.buzz_outbox if n["kind"] == "approval_request"]
        self.assertEqual({(n["account"], n["state"], n["needs"]) for n in asks}, {("acct-b-gmail", "EXPIRED", "owner_reconsent"), ("acct-b-drive", "REVOKED", "owner_reconsent")})

    def test_the_denial_holds_even_without_a_maintenance_run_and_automation_cannot_clear_it(self):
        w = C.ComposioWorld()
        receipt, _ = C.execute(w, "agent-b", "acct-b-gmail")
        self.assertEqual(receipt["decision_code"], "NEEDS_OWNER_RECONSENT")
        for _ in range(3):
            upkeep(w, "oracle-admin")
            C.manage(w, C.command(w, "oracle-admin", "acct-b-gmail", "refresh", f"r{_}"))
        self.assertEqual((w.connections["ca-b1"]["state"], w.connections["ca-b1"]["refreshes"], w.human_logins), ("EXPIRED", 0, 0))
        self.assertEqual(C.execute(w, "agent-b", "acct-b-gmail", nonce="n9")[0]["decision_code"], "NEEDS_OWNER_RECONSENT")

    def test_only_the_owner_s_reconsent_restores_it(self):
        w = C.ComposioWorld()
        C.owner_reconsent(w, "ca-b1")
        receipt, out = C.execute(w, "agent-b", "acct-b-gmail")
        self.assertEqual((receipt["decision_code"], out["connected_account_id"], w.human_logins), ("ALLOW", "ca-b1", 1))
        self.assertEqual(C.execute(w, "agent-b", "acct-b-drive", nonce="n2")[0]["decision_code"], "NEEDS_OWNER_RECONSENT")     # the other account still needs its owner


class ManagementInterface(unittest.TestCase):
    def refused(self, w, cmd, code):
        calls = (w.admin_calls, len(w.buzz_outbox), len(w.vendor_log))
        out = C.manage(w, cmd)
        self.assertEqual((out["outcome"], out["code"]), ("refused", code), out)
        self.assertEqual((w.admin_calls, len(w.buzz_outbox), len(w.vendor_log)), calls)                  # nothing ran and nothing was posted
        no_secret(self, w, out)

    def test_forged_changed_and_rogue_commands(self):
        w = C.ComposioWorld()
        good = C.command(w, "oracle-admin", "acct-a-gmail", "health", "c1")
        self.refused(w, {**good, "action": "refresh"}, "DENY_PRINCIPAL")                              # changed after signing
        self.refused(w, {**good, "sig": "0" * 64}, "DENY_PRINCIPAL")
        self.refused(w, C.command(w, "oracle-admin", "acct-a-gmail", "health", "c2", key="the-wrong-key"), "DENY_PRINCIPAL")
        self.refused(w, C.command(w, "agent-rogue", "acct-a-gmail", "health", "c3", key="rogue-key"), "DENY_PRINCIPAL")      # a rogue agent, even with a signature of its own
        self.refused(w, {k: v for k, v in good.items() if k != "nonce"}, "DENY_INPUT")
        self.refused(w, "not a dict", "DENY_INPUT")

    def test_wrong_project_tenant_or_account(self):
        w = C.ComposioWorld()
        self.refused(w, C.command(w, "oracle-admin", "acct-a-gmail", "health", "c1", project="proj-other"), "DENY_TENANT")
        self.refused(w, C.command(w, "oracle-admin", "acct-a-gmail", "health", "c2", tenant="TENANT-B-TEST"), "DENY_TENANT")
        self.refused(w, C.command(w, "oracle-admin", "acct-zz", "health", "c3"), "DENY_ACCOUNT_AMBIGUOUS")
        self.refused(w, C.command(w, "oracle-admin", "acct-a-jira", "health", "c4"), "DENY_ACCOUNT_AMBIGUOUS")      # a real account with no connection

    def test_unauthorized_management_actions_never_reach_the_vendor(self):
        w = C.ComposioWorld()
        for n, action in enumerate(("reveal_key", "export_token", "create_project", "delete_account", "set_admin_key", "proxy_execute", "")):
            if action:
                self.refused(w, C.command(w, "oracle-admin", "acct-a-gmail", action, f"x{n}"), "DENY_MANAGEMENT_SCOPE")

    def test_a_replayed_command_runs_once(self):
        w = C.ComposioWorld()
        cmd = C.command(w, "github-admin", "acct-a-gmail", "refresh", "c1")
        self.assertEqual(C.manage(w, cmd)["outcome"], "done")
        calls = w.admin_calls
        self.refused(w, cmd, "DENY_REPLAY_OR_WRITER")
        self.assertEqual(w.admin_calls, calls)

    def test_the_routine_actions_work_and_leak_nothing(self):
        w = C.ComposioWorld()
        health = C.manage(w, C.command(w, "oracle-admin", "acct-a-drive", "health", "c1"))
        refresh = C.manage(w, C.command(w, "github-admin", "acct-a-gmail", "refresh", "c2"))
        self.assertEqual((health, refresh["refreshed"]), ({"outcome": "done", "code": None, "action": "health", "account": "acct-a-drive", "state": "ACTIVE"}, True))
        no_secret(self, w, health, refresh)
        self.assertEqual(w.human_logins, 0)

    def test_disable_revokes_the_grants_so_the_next_call_is_denied(self):
        w = C.ComposioWorld()
        out = C.manage(w, C.command(w, "oracle-admin", "acct-a-drive", "disable", "c1"))
        self.assertEqual((out["state"], w.connections["ca-a2"]["state"]), ("DISABLED", "DISABLED"))
        receipt, result = C.execute(w, "agent-a", "acct-a-drive", source_id="DOC-1")
        self.assertEqual((receipt["decision_code"], result, w.provider_calls), ("DENY_GRANT", None, 0))
        self.assertEqual(upkeep(w, "oracle-admin")["counts"]["disabled"], 1)


class RefusedCallsChangeNothing(unittest.TestCase):
    def test_a_wrong_tenant_call_against_a_dead_grant_leaves_the_ledger_alone_and_the_receipt_is_true(self):
        w = C.ComposioWorld()
        before = w.ledger_hash()
        receipt, out = C.execute(w, "agent-a", "acct-b-gmail")                 # tenant A asks for tenant B's expired account
        self.assertEqual((receipt["decision_code"], out, w.consent_needed, w.ledger_hash()), ("DENY_TENANT", None, set(), before))
        self.assertEqual((receipt["policy_ledger_hash_before"], receipt["policy_ledger_hash_after"]), (before, before))

    def test_the_right_tenant_denial_also_leaves_nothing_behind(self):
        w = C.ComposioWorld()
        before = w.ledger_hash()
        receipt, _ = C.execute(w, "agent-b", "acct-b-gmail")
        self.assertEqual((receipt["decision_code"], w.consent_needed, w.ledger_hash(), receipt["policy_ledger_hash_after"]), ("NEEDS_OWNER_RECONSENT", set(), before, before))

    def test_a_disabled_connection_is_not_refreshed(self):
        w = C.ComposioWorld()
        C.manage(w, C.command(w, "oracle-admin", "acct-a-drive", "disable", "c1"))
        expires, calls, notices = w.connections["ca-a2"]["expires"], w.admin_calls, len(w.buzz_outbox)
        out = C.manage(w, C.command(w, "oracle-admin", "acct-a-drive", "refresh", "c2"))
        self.assertEqual((out["outcome"], out["code"]), ("refused", "DENY_GRANT"))
        self.assertEqual((w.connections["ca-a2"]["state"], w.connections["ca-a2"]["expires"], w.connections["ca-a2"]["refreshes"], w.admin_calls, len(w.buzz_outbox)),
                         ("DISABLED", expires, 0, calls, notices))
        upkeep(w, "oracle-admin")
        self.assertEqual((w.connections["ca-a2"]["state"], w.connections["ca-a2"]["refreshes"]), ("DISABLED", 0))


class CustodyException(unittest.TestCase):
    """GWT-T09-05: the exception is written down exactly, and planning consent is not live authorization."""

    def test_the_record_is_complete(self):
        r = C.CUSTODY_EXCEPTION
        for key in ("exception", "scope", "v7_keeps", "unchanged_paths", "vendor_scope_and_cost", "rollback", "protected_approval_prerequisites", "routine_login_free_operation"):
            self.assertTrue(r[key], key)
        self.assertEqual(r["scope"], "future_managed_accounts_only")
        self.assertIn("existing V7 and Bitwarden direct credentials", r["unchanged_paths"])
        self.assertIn("native ChatGPT connections", r["unchanged_paths"])
        self.assertTrue({"principal", "tenant", "account", "action"} <= set(r["v7_keeps"]))

    def test_nothing_unverified_is_presented_as_proven_and_planning_consent_is_not_live_authorization(self):
        r = C.CUSTODY_EXCEPTION
        self.assertIs(r["planning_consent_is_live_authorization"], False)
        self.assertEqual((r["vendor_scope_and_cost"], r["managed_oauth_rollout"]), ("unverified", "blocked"))
        self.assertTrue(r["routine_login_free_operation"].startswith("simulated_only"))
        self.assertGreaterEqual(len(r["protected_approval_prerequisites"]), 3)
        self.assertGreaterEqual(len(r["rollback"]), 2)


if __name__ == "__main__":
    unittest.main()
