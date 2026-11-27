"""Phase 2 and 5: the six-source normalized Inbox, workspace behavior, isolation, failure independence, freshness and
determinism (all synthetic).
"""

import unittest
from dataclasses import replace
from datetime import timedelta

from lifeos.inbox import adapters, inbox, policy
from lifeos.inbox.model import FRESH_FOR, InboxError, record_id, validate
from tests.inbox import fixtures as F


def build(sources=None, who=None, now=F.NOW):
    return inbox.build_inbox(sources if sources is not None else F.all_sources(), who or F.principal(), now)


class SixSources(unittest.TestCase):
    def test_each_source_populates_the_model_on_its_own(self):  # check 1
        for source in policy.MATRIX:
            only = [s for s in F.all_sources() if s.source == source]
            result = build(only)
            self.assertEqual(sorted(r.workspace for r in result.records), ["bytalos", "personal"], source)
            for r in result.records:
                self.assertEqual((r.source, r.health, r.url.startswith("https://example.test/")), (source, "ok", True))
                self.assertTrue(r.record_id.startswith(f"{source}:{r.workspace}:"))

    def test_every_field_of_the_contract_is_present_and_typed(self):
        r = F_record("jira", "personal")
        self.assertEqual(
            (r.record_id, r.account, r.native_id, r.source_status, r.assignee),
            ("jira:personal:jim-personal:LIFE-1", "jim-personal", "LIFE-1", "Human UAT", "jim"),
        )
        self.assertEqual(
            (r.presentation, r.kind, r.approval_required, r.approval_evidence),
            ("approvals", "human_uat", True, ("https://example.test/pr/personal",)),
        )
        self.assertEqual((r.due, r.source_updated_at, r.retrieved_at), (None, r.source_updated_at, F.NOW))
        self.assertIsNotNone(r.source_updated_at.tzinfo)
        c = F_record("concord", "bytalos")
        self.assertEqual((c.correlation_id, c.presentation, c.approval_required), ("corr-bytalos", "approvals", True))
        t = F_record("todoist", "personal")
        self.assertEqual(
            (str(t.due_tz), t.due.tzinfo is not None, t.presentation), ("America/Chicago", True, "needs_me")
        )
        self.assertEqual(F_record("calendar", "personal").presentation, "upcoming")
        self.assertEqual(F_record("followup", "personal").presentation, "needs_me")  # the checkpoint (10:00) has passed
        self.assertEqual(F_record("asana", "personal").presentation, "needs_me")
        self.assertEqual(
            {r.presentation for r in build().records} <= {"needs_me", "waiting", "approvals", "upcoming"}, True
        )

    def test_a_record_needs_a_timezone_aware_time_and_an_https_source_url(self):
        r = F_record("todoist", "personal")
        for bad in (
            replace(r, due=r.due.replace(tzinfo=None)),
            replace(r, url="http://example.test/x"),
            replace(r, retrieved_at=None),
        ):
            with self.assertRaises(InboxError):
                validate(bad)
        raw = F.raw("todoist", "personal")
        raw.pop("tz")
        with self.assertRaises(InboxError):
            adapters.normalize("todoist", "personal", "jim-personal", raw, "jim", F.NOW, F.NOW)

    def test_one_combined_list_is_labeled_and_filtered_by_workspace(self):
        result = build()
        self.assertEqual(len(result.records), 12)
        self.assertEqual({r.label for r in result.records}, {"Personal", "Bytalos"})
        self.assertEqual(len(inbox.view(result, "all")), 12)
        self.assertEqual({r.workspace for r in inbox.view(result, "personal")}, {"personal"})
        self.assertEqual({r.workspace for r in inbox.view(result, "bytalos")}, {"bytalos"})
        with self.assertRaises(InboxError):
            inbox.view(result, "tgp")

    def test_the_all_view_never_merges_credentials_or_grants(self):
        sources = F.all_sources()
        build(sources)
        for s in sources:
            self.assertEqual(s.calls, [f"cred:{s.source}:{s.workspace}"])

    def test_duplicate_titles_from_different_sources_and_workspaces_stay_distinct(self):  # check 3
        records = build().records
        same = [r for r in records if r.title.startswith(F.SAME_TITLE)]
        self.assertEqual(len(same), 12)
        self.assertEqual(len({r.record_id for r in same}), 12)
        self.assertNotEqual(
            record_id("todoist", "personal", "jim-personal", "t1"), record_id("todoist", "bytalos", "jim-bytalos", "t1")
        )


class Isolation(unittest.TestCase):
    def test_a_personal_only_principal_sees_and_does_nothing_in_bytalos(self):  # check 4
        full = build()
        sources = F.all_sources()
        result = build(sources, F.principal(workspaces=("personal",)))
        self.assertEqual({r.workspace for r in result.records}, {"personal"})
        self.assertEqual(
            {(h.workspace, h.state, h.reason) for h in result.health if h.workspace == "bytalos"},
            {("bytalos", "no_grant", "no_grant")},
        )
        self.assertEqual([s.calls for s in sources if s.workspace == "bytalos"], [[]] * 6)  # not even fetched
        bytalos = F_record("todoist", "bytalos", full)
        self.assertEqual(
            policy.decide(F.principal(workspaces=("personal",)), bytalos, "view", F.NOW), (False, "no_grant")
        )

    def test_a_grant_for_one_account_never_works_for_another(self):
        wrong = policy.Principal("jim", tuple(replace(g, account="someone-else") for g in F.grants()))
        self.assertEqual(policy.decide(wrong, F_record("jira", "personal"), "view", F.NOW), (False, "no_grant"))

    def test_tgp_is_never_loaded_or_acted_on(self):  # check 5
        tgp = F.FakeSource("jira", "tgp", account="jim-tgp")
        grants = F.grants() + (
            policy.Grant(
                "jira", "tgp", "jim-tgp", frozenset(policy.MATRIX["jira"]), "cred:jira:tgp", F.NOW + timedelta(days=1)
            ),
        )  # even a grant for it does not help
        result = build([tgp] + F.all_sources(), policy.Principal("jim", grants))
        self.assertEqual(tgp.calls, [])
        self.assertEqual(
            [(h.state, h.reason) for h in result.health if h.workspace == "tgp"],
            [("refused", "workspace_not_released")],
        )
        self.assertFalse([r for r in result.records if r.workspace == "tgp"])
        with self.assertRaises(InboxError) as raised:
            adapters.normalize("jira", "tgp", "jim-tgp", F.raw("jira", "personal"), "jim", F.NOW, F.NOW)
        self.assertEqual(raised.exception.reason, "workspace_not_released")
        forged = replace(F_record("jira", "personal"), workspace="tgp")
        self.assertEqual(
            policy.decide(policy.Principal("jim", grants), forged, "view", F.NOW), (False, "workspace_not_released")
        )

    def test_a_missing_expired_or_revoked_grant_fails_closed(self):  # check 6
        for label, who, reason in (
            ("missing", F.principal(workspaces=()), "no_grant"),
            ("revoked", F.principal(revoked=(("jira", "personal"),)), "grant_revoked"),
            ("expired", F.principal(expires=F.NOW - timedelta(seconds=1)), "grant_expired"),
        ):
            sources = F.all_sources(workspaces=("personal",))
            result = build(sources, who)
            jira = next(s for s in sources if s.source == "jira")
            self.assertEqual(jira.calls, [], label)
            self.assertEqual(next(h for h in result.health if h.source == "jira").reason, reason, label)
            self.assertFalse([r for r in result.records if r.source == "jira"], label)
        revoked = F.principal(revoked=(("jira", "personal"),))
        self.assertEqual(
            policy.decide(revoked, F_record("jira", "personal"), "accept_uat", F.NOW), (False, "grant_revoked")
        )


class Failures(unittest.TestCase):
    def test_one_failed_connector_does_not_hide_the_other_five(self):  # check 2
        sources = F.all_sources(workspaces=("personal",))
        sources[2] = F.FakeSource("concord", "personal", error=RuntimeError(F.ERROR_CANARY))
        result = build(sources, F.principal(workspaces=("personal",)))
        self.assertEqual(sorted(r.source for r in result.records), ["asana", "calendar", "followup", "jira", "todoist"])
        down = next(h for h in result.health if h.source == "concord")
        self.assertEqual((down.state, down.reason), ("unavailable", "runtimeerror"))
        self.assertNotIn(F.ERROR_CANARY, repr(result.health))  # an exception's message never reaches the Inbox
        self.assertEqual(sum(h.state == "ok" for h in result.health), 5)

    def test_a_malformed_native_record_is_left_out_and_the_source_is_marked_degraded(self):
        bad = F.raw("todoist", "personal")
        bad.pop("status")
        sources = [F.FakeSource("todoist", "personal", rows=[F.raw("todoist", "personal"), bad])]
        result = build(sources, F.principal(workspaces=("personal",)))
        self.assertEqual(len(result.records), 1)
        self.assertEqual((result.health[0].state, result.health[0].rejected), ("degraded", 1))

    def test_a_stale_record_gets_no_action_and_no_completion_claim(
        self,
    ):  # check 10 (the action side is in test_actions)
        r = F_record("jira", "personal")
        self.assertEqual(
            (r.freshness(F.NOW), r.freshness(F.NOW + FRESH_FOR), r.freshness(F.NOW + FRESH_FOR + timedelta(seconds=1))),
            ("fresh", "fresh", "stale"),
        )

    def test_reruns_are_deterministic_whatever_order_the_sources_arrive_in(self):  # check 12
        first = build()
        for sources in (
            F.all_sources(),
            list(reversed(F.all_sources())),
            sorted(F.all_sources(), key=lambda s: s.account + s.source),
        ):
            again = build(sources)
            self.assertEqual(again, first)
        self.assertEqual(
            [r.presentation for r in first.records],
            sorted((r.presentation for r in first.records), key=("needs_me", "waiting", "approvals", "upcoming").index),
        )


def F_record(source, workspace, result=None):
    return F.record_for(result or build(), source, workspace)


if __name__ == "__main__":
    unittest.main()
