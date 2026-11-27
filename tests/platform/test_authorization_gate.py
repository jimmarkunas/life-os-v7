"""(F02, synthetic): the central authorization gate."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.platform import access_policy as A
from lifeos.platform import authorization_gate as G
from lifeos.platform import capability_catalog as C

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
KEY = {"personal-agent": "a1" * 32, "bytalos-agent": "b2" * 32, "tgp-agent": "c3" * 32}
TENANT = {"personal-agent": "personal", "bytalos-agent": "bytalos", "tgp-agent": "tgp"}
HUMAN = {"jim-human"}
ACTIONS = ("read", "write", "send", "delete", "permission", "production")
FAKE_SECRET = "ghp_" + "A" * 24


def catalog_text(**patch):
    doc = {
        "version": 1,
        "principals": [{"id": p, "kind": "agent", "workspace": t} for p, t in TENANT.items()],
        "accounts": [{"alias": t + "-acct", "provider": "fakeprov", "tenant": t} for t in TENANT.values()],
        "capabilities": [
            {
                "principal": p,
                "tool": "notion.page",
                "action": a,
                "provider": "fakeprov",
                "account_alias": t + "-acct",
                "host": "mac-fixture",
                "source": "tests/fixture",
                "as_of": "2026-11-20",
                "max_age_days": 30,
                "status": "synthetic",
            }
            for p, t in TENANT.items()
            for a in ACTIONS
        ],
    }
    doc.update(patch)
    return json.dumps(doc)


CATALOG = C.load(catalog_text())
POLICY = A.load(json.dumps({"version": 1, "default": {}, "agents": {k: {"notion": "write"} for k in KEY.values()}}))


def grant(agent, **kw):
    t = TENANT[agent]
    return G.Grant(
        **{
            "id": "grant-" + agent,
            "principal": agent,
            "tenant": t,
            "account": t + "-acct",
            "tool": "notion.page",
            "purpose": "weekly-report",
            "actions": ("read", "write"),
            "credential_ref": "bws-fake-" + t,
            "expires": NOW + timedelta(hours=1),
            **kw,
        }
    )


def session(agent, **kw):
    return G.Session(**{"principal": agent, "policy_key": KEY[agent], "tenant": TENANT[agent], "trusted": True, **kw})


def request(agent, action="read", **kw):
    return G.Request(
        **{
            "tool": "notion.page",
            "action": action,
            "account": TENANT[agent] + "-acct",
            "purpose": "weekly-report",
            "service": "notion",
            "request_id": "req-1",
            **kw,
        }
    )


class Harness:
    def __init__(self, grants=None, approvals=(), catalog=CATALOG, policy=POLICY, result=None, boom=False):
        self.grants = tuple(grants if grants is not None else [grant(a) for a in KEY])
        self.approvals, self.catalog, self.policy, self.result, self.boom = (
            tuple(approvals),
            catalog,
            policy,
            result or {"ok": True},
            boom,
        )
        self.receipts, self.effects = [], []

    def effect(self):
        self.effects.append(1)
        if self.boom:
            raise RuntimeError("provider detail that must not appear")
        return self.result

    def run(self, agent="personal-agent", action="read", at=NOW, audit=None, excluded=(), sess=None, **rkw):
        return G.execute(
            sess or session(agent),
            request(agent, action, **rkw),
            self.catalog,
            self.policy,
            self.grants,
            self.approvals,
            HUMAN,
            self.effect,
            audit or self.receipts.append,
            at,
            excluded,
        )


def approval(agent, action, **kw):
    return G.Approval(
        **{
            "principal": agent,
            "tenant": TENANT[agent],
            "tool": "notion.page",
            "action": action,
            "request_id": "req-1",
            "approver": "jim-human",
            "approved_at": NOW - timedelta(minutes=1),
            **kw,
        }
    )


class GF02GWT01(unittest.TestCase):
    """Two fabricated agents with distinct grants: only the exact permitted operation proceeds and every deny precedes
    the side effect.
    """

    def test_each_agent_runs_only_its_own_granted_operation(self):
        h = Harness()
        for agent in KEY:
            out = h.run(agent)
            self.assertEqual(
                (out.decision, out.reason, out.executed, out.live_proven), ("allow", "ok", True, False), agent
            )
        self.assertEqual(len(h.effects), 3)
        self.assertEqual({r["tenant"] for r in h.receipts}, {"personal", "bytalos", "tgp"})
        self.assertTrue(all(r["mode"] == "synthetic" for r in h.receipts))

    def test_a_write_the_grant_covers_runs_and_one_it_does_not_cover_is_refused(self):
        h = Harness([grant("personal-agent", actions=("read",))])
        self.assertEqual(h.run("personal-agent", "read").reason, "ok")
        self.assertEqual(h.run("personal-agent", "write").reason, "action_not_granted")
        self.assertEqual(len(h.effects), 1)

    def test_wrong_tenant_is_refused_for_every_pair_and_before_the_effect(self):
        h = Harness()
        for agent in KEY:
            for other in KEY:
                if agent == other:
                    continue
                out = h.run(agent, sess=session(agent, tenant=TENANT[other]))
                self.assertEqual(out.decision, "deny", (agent, other))
                self.assertIn(out.reason, ("catalog_wrong_workspace", "tenant_mismatch"))
        self.assertEqual(h.effects, [])

    def test_an_agent_holding_only_another_tenants_grant_is_refused(self):
        wrong = G.Grant(**{**grant("personal-agent").__dict__, "tenant": "bytalos"})
        h = Harness([wrong])
        out = h.run("personal-agent")
        self.assertEqual((out.reason, h.effects), ("tenant_mismatch", []))

    def test_an_account_of_another_tenant_in_the_catalog_is_refused(self):
        swapped = C.load(
            catalog_text(accounts=[{"alias": "personal-acct", "provider": "fakeprov", "tenant": "bytalos"}])
        )
        self.assertEqual(Harness(catalog=swapped).run("personal-agent").reason, "tenant_mismatch")
        dup = C.load(
            catalog_text(
                accounts=[
                    {"alias": "personal-acct", "provider": "fakeprov", "tenant": t} for t in ("personal", "bytalos")
                ]
            )
        )
        self.assertEqual(Harness(catalog=dup).run("personal-agent").reason, "catalog_alias_duplicate")

    def test_a_grant_for_another_account_or_tool_is_no_grant(self):
        h = Harness()
        self.assertEqual(h.run("personal-agent", account="bytalos-acct").reason, "no_grant")
        self.assertEqual(h.run("personal-agent", tool="notion.other").reason, "catalog_unknown_capability")
        self.assertEqual(h.effects, [])

    def test_purpose_mismatch_is_refused(self):
        h = Harness()
        self.assertEqual(h.run("personal-agent", purpose="marketing-blast").reason, "purpose_mismatch")
        self.assertEqual(h.effects, [])

    def test_an_expired_grant_is_refused_exactly_at_expiry_and_after(self):
        h = Harness()
        self.assertEqual(h.run("personal-agent", at=NOW + timedelta(hours=1) - timedelta(seconds=1)).reason, "ok")
        self.assertEqual(h.run("personal-agent", at=NOW + timedelta(hours=1)).reason, "grant_expired")
        self.assertEqual(
            h.run("personal-agent", at=NOW + timedelta(days=40)).reason, "catalog_source_stale"
        )  # an old catalog row also refuses
        self.assertEqual(len(h.effects), 1)

    def test_a_naive_expiry_is_refused(self):
        h = Harness([grant("personal-agent", expires=datetime(2027, 1, 1))])
        self.assertEqual(h.run("personal-agent").reason, "grant_expired")

    def test_revoke_takes_effect_for_one_grant_and_leaves_the_others(self):
        h = Harness()
        self.assertEqual(h.run("personal-agent").reason, "ok")
        h.grants = G.revoke(h.grants, grant_id="grant-personal-agent")
        self.assertEqual(h.run("personal-agent").reason, "grant_revoked")
        self.assertEqual(h.run("bytalos-agent").reason, "ok")
        h.grants = G.revoke(h.grants, principal="bytalos-agent")
        self.assertEqual(h.run("bytalos-agent").reason, "grant_revoked")
        self.assertEqual(h.run("tgp-agent").reason, "ok")

    def test_an_unknown_untrusted_or_policy_denied_caller_is_refused(self):
        h = Harness()
        self.assertEqual(
            h.run("personal-agent", sess=session("personal-agent", principal="ghost-agent")).reason,
            "catalog_unknown_principal",
        )
        self.assertEqual(
            h.run("personal-agent", sess=session("personal-agent", trusted=False)).reason, "untrusted_session"
        )
        off = A.load(json.dumps({"version": 1, "default": {}, "agents": {}}))
        self.assertEqual(Harness(policy=off).run("personal-agent").reason, "policy_denied")
        self.assertEqual(Harness(policy=None).run("personal-agent").reason, "policy_denied")
        self.assertEqual(Harness(catalog=None).run("personal-agent").reason, "catalog_unreadable")
        self.assertEqual(
            h.run("personal-agent", sess=session("personal-agent", tenant="acme")).reason, "unknown_tenant"
        )
        self.assertEqual(h.run("personal-agent", action="erase-everything").reason, "unknown_action")
        self.assertEqual(h.effects, [])

    def test_a_tenant_the_caller_excludes_is_refused_even_with_a_grant(self):
        h = Harness()
        self.assertEqual(h.run("tgp-agent", excluded=("tgp",)).reason, "tenant_excluded")
        self.assertEqual(h.run("personal-agent", excluded=("tgp",)).reason, "ok")

    def test_a_missing_capability_row_never_runs(self):
        rows = json.loads(catalog_text())["capabilities"]
        rows[0]["status"] = "missing"
        h = Harness(catalog=C.load(catalog_text(capabilities=rows)))
        self.assertEqual(h.run("personal-agent").reason, "catalog_unavailable")
        self.assertEqual(h.effects, [])

    def test_every_deny_precedes_the_effect_and_is_recorded(self):
        h = Harness()
        h.run("personal-agent", purpose="x")
        h.run("personal-agent", account="bytalos-acct")
        h.grants = G.revoke(h.grants, principal="personal-agent")
        h.run("personal-agent")
        self.assertEqual(h.effects, [])
        self.assertEqual([r["outcome"] for r in h.receipts], ["deny"] * 3)

    def test_no_receipt_no_effect(self):
        h = Harness()

        def broken(_):
            raise OSError("disk full")

        with self.assertRaises(OSError):
            h.run("personal-agent", audit=broken)
        with self.assertRaises(OSError):
            h.run("personal-agent", purpose="x", audit=broken)
        self.assertEqual(h.effects, [])

    def test_a_malformed_request_is_rejected_without_an_effect(self):
        h = Harness()
        with self.assertRaises(ValueError):
            h.run("personal-agent", request_id="")
        with self.assertRaises(ValueError):
            h.run("personal-agent", at=datetime(2026, 11, 21))
        self.assertEqual(h.effects, [])


class GF02GWT02(unittest.TestCase):
    """A human-protected send, delete, permission or production operation without a current approval is refused, and an
    attributable redacted result is.
    """

    def protected_grants(self):
        return [grant(a, actions=ACTIONS) for a in KEY]

    def test_protected_actions_are_refused_without_an_approval(self):
        h = Harness(self.protected_grants())
        for action in G.PROTECTED:
            out = h.run("personal-agent", action)
            self.assertEqual((out.decision, out.reason, out.executed), ("deny", "approval_missing", False), action)
        self.assertEqual(h.effects, [])
        self.assertEqual([r["reason"] for r in h.receipts], ["approval_missing"] * 4)

    def test_a_current_human_approval_for_that_exact_request_lets_it_run(self):
        for action in G.PROTECTED:
            h = Harness(self.protected_grants(), [approval("personal-agent", action)])
            out = h.run("personal-agent", action)
            self.assertEqual((out.reason, out.executed), ("ok", True), action)
            self.assertEqual(h.receipts[-1]["approver_fp"], G.fingerprint("jim-human"))

    def test_a_stale_or_future_dated_approval_is_refused(self):
        for at in (NOW - G.APPROVAL_TTL - timedelta(seconds=1), NOW + timedelta(minutes=5)):
            h = Harness(self.protected_grants(), [approval("personal-agent", "delete", approved_at=at)])
            self.assertEqual(h.run("personal-agent", "delete").reason, "approval_stale")
            self.assertEqual(h.effects, [])
        edge = Harness(
            self.protected_grants(), [approval("personal-agent", "delete", approved_at=NOW - G.APPROVAL_TTL)]
        )
        self.assertEqual(edge.run("personal-agent", "delete").reason, "ok")

    def test_an_approval_for_another_principal_action_tool_tenant_or_request_does_not_count(self):
        other = [
            approval("bytalos-agent", "delete"),
            approval("personal-agent", "send"),
            approval("personal-agent", "delete", request_id="req-2"),
            approval("personal-agent", "delete", tool="notion.other"),
            approval("personal-agent", "delete", tenant="bytalos"),
        ]
        h = Harness(self.protected_grants(), other)
        self.assertEqual(h.run("personal-agent", "delete").reason, "approval_missing")
        self.assertEqual(h.effects, [])

    def test_an_agent_cannot_approve_itself_and_only_known_humans_count(self):
        for who in ("personal-agent", "some-other-agent"):
            h = Harness(self.protected_grants(), [approval("personal-agent", "delete", approver=who)])
            self.assertEqual(h.run("personal-agent", "delete").reason, "approval_not_human", who)
            self.assertEqual(h.effects, [])

    def test_an_approval_does_not_widen_the_grant(self):
        h = Harness([grant("personal-agent")], [approval("personal-agent", "delete")])
        self.assertEqual(h.run("personal-agent", "delete").reason, "action_not_granted")
        self.assertEqual(h.effects, [])

    def test_an_approval_does_not_beat_a_revoked_grant_or_a_policy_deny(self):
        h = Harness(self.protected_grants(), [approval("personal-agent", "delete")])
        h.grants = G.revoke(h.grants, principal="personal-agent")
        self.assertEqual(h.run("personal-agent", "delete").reason, "grant_revoked")
        ro = A.load(json.dumps({"version": 1, "default": {}, "agents": {KEY["personal-agent"]: {"notion": "read"}}}))
        self.assertEqual(
            Harness(self.protected_grants(), [approval("personal-agent", "delete")], policy=ro)
            .run("personal-agent", "delete")
            .reason,
            "policy_denied",
        )

    def test_the_protected_receipt_is_redacted_and_attributable(self):
        h = Harness(self.protected_grants())
        h.run("personal-agent", "delete")
        text = json.dumps(h.receipts)
        for forbidden in (KEY["personal-agent"], "grant-personal-agent", "bws-fake", "personal-acct", "jim-human"):
            self.assertNotIn(forbidden, text)
        rec = h.receipts[-1]
        self.assertEqual(
            set(rec),
            {
                "t",
                "mode",
                "principal_fp",
                "tenant",
                "tool",
                "action",
                "purpose",
                "request_id",
                "grant_fp",
                "approver_fp",
                "outcome",
                "reason",
            },
        )
        self.assertEqual(
            (rec["principal_fp"], rec["grant_fp"], rec["action"]),
            (G.fingerprint("personal-agent"), G.fingerprint("grant-personal-agent"), "delete"),
        )
        self.assertTrue(rec["t"].endswith(("-06:00", "-05:00")))  # Central time

    def test_a_provider_failure_and_a_secret_in_the_result_are_recorded_by_outcome_only(self):
        boom = Harness(boom=True)
        out = boom.run("personal-agent")
        self.assertEqual(out.reason, "effect_failed")
        self.assertNotIn("provider detail", json.dumps(boom.receipts))
        leak = Harness(result={"note": "token: " + FAKE_SECRET})
        out = leak.run("personal-agent")
        self.assertEqual((out.reason, out.result), ("output_has_secret", None))
        self.assertNotIn(FAKE_SECRET, json.dumps(leak.receipts))


class Honesty(unittest.TestCase):
    def test_nothing_here_claims_a_live_adapter(self):
        h = Harness()
        self.assertFalse(h.run("personal-agent").live_proven)
        self.assertFalse(h.run("personal-agent", purpose="x").live_proven)
        working = json.loads(catalog_text())["capabilities"]
        for row in working:
            row["status"] = "working"
        live = Harness(catalog=C.load(catalog_text(capabilities=working)))
        live.run("personal-agent")
        self.assertEqual(live.receipts[-1]["mode"], "live")  # the receipt follows the catalog row
        self.assertFalse(live.run("personal-agent").live_proven)


if __name__ == "__main__":
    unittest.main()
