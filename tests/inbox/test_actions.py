"""Phase 3 and 5: the approved action matrix, protected-action gates, duplicate prevention, read-back truth and log hygiene (all synthetic; the executor is a counter)."""
import unittest
from dataclasses import replace
from datetime import timedelta

from lifeos.inbox import inbox, policy
from lifeos.inbox.actions import ActionService
from lifeos.inbox.model import InboxError
from tests.inbox import fixtures as F

EVERY_ACTION = sorted({a for actions in policy.MATRIX.values() for a in actions} | {"edit", "assign", "transition", "delete", "create", "reschedule", "rsvp", "bulk_complete", "comment"})
# what the product matrix lets a fully granted principal do on the fixture record of each source (workspace personal; Jira is in Human UAT, Todoist is a personal task, Concord is pending approval)
EXPECTED = {
    "jira": {"view", "open", "accept_uat", "reject_uat"},
    "todoist": {"view", "open", "complete_task"},
    "concord": {"view", "open", "approve", "reject", "request_changes"},                    # resume_work is refused until the work is approved and eligible
    "followup": {"view", "open", "confirm_resolution", "update_checkpoint"},
    "calendar": {"view", "open"},
    "asana": {"view", "open"},
}


def build(who=None, workspaces=("personal", "bytalos")):
    return inbox.build_inbox(F.all_sources(workspaces), who or F.principal(workspaces=workspaces), F.NOW)


def widest(sources=None):
    """A principal whose grants LIST every action, including forbidden ones: a grant may only narrow the matrix."""
    every = frozenset(EVERY_ACTION)
    return F.principal(actions={s: every for s in policy.MATRIX})


def service(**kw):
    executor = F.Executor(**kw)
    return ActionService(F.KEY, executor, F.memory_ledger(), synthetic=True), executor


def authorize(who, record, action, nonce="n1", now=F.NOW):
    return policy.mint(F.KEY, who, record, action, nonce, now)


class Matrix(unittest.TestCase):
    def test_every_permitted_and_forbidden_operation_on_every_source(self):
        who = widest()
        result = build(who)
        for source, expected in EXPECTED.items():
            record = F.record_for(result, source, "personal")
            got = {a for a in EVERY_ACTION if policy.decide(who, record, a, F.NOW)[0]}
            self.assertEqual(got, expected, source)
            for action in set(EVERY_ACTION) - expected:
                self.assertFalse(policy.decide(who, record, action, F.NOW)[0], f"{source} {action}")
        self.assertEqual(policy.permitted_actions(who, F.record_for(result, "asana", "personal"), F.NOW), ("open", "view"))

    def test_calendar_and_asana_mutations_are_rejected_even_when_a_grant_lists_them(self):                 # check 7
        who, result = widest(), build(widest())
        for source in ("calendar", "asana"):
            for action in ("edit", "create", "rsvp", "reschedule", "delete", "complete_task", "approve", "accept_uat"):
                for workspace in ("personal", "bytalos"):
                    self.assertEqual(policy.decide(who, F.record_for(result, source, workspace), action, F.NOW), (False, "action_not_permitted"), (source, action))
        svc, executor = service()
        record = F.record_for(result, "calendar", "personal")
        self.assertEqual(svc.request(who, record, "edit", None, "k1", F.NOW).reason, "action_not_permitted")
        with self.assertRaises(InboxError):
            policy.mint(F.KEY, who, record, "edit", "n", F.NOW)                                            # no authorization can even be minted for it
        self.assertEqual(executor.calls, [])

    def test_jira_human_uat_actions_cannot_be_applied_to_arbitrary_tasks(self):                            # check 8
        who = widest()
        jira = F.raw("jira", "personal")
        jira["status"] = "In Progress"
        ordinary = inbox.build_inbox([F.FakeSource("jira", "personal", rows=[jira])], who, F.NOW).records[0]
        self.assertEqual((ordinary.kind, ordinary.approval_required), ("task", False))
        for action in ("accept_uat", "reject_uat"):
            self.assertEqual(policy.decide(who, ordinary, action, F.NOW), (False, "not_human_uat"))
        for action in ("transition", "assign", "edit", "approve", "complete_task"):
            self.assertFalse(policy.decide(who, ordinary, action, F.NOW)[0], action)
        uat = F.record_for(build(who), "jira", "personal")
        self.assertEqual(policy.decide(who, uat, "accept_uat", F.NOW), (True, "ok"))
        self.assertEqual(policy.decide(who, replace(uat, source="concord"), "accept_uat", F.NOW)[0], False)

    def test_todoist_completes_personal_tasks_only(self):
        who, result = widest(), build(widest())
        self.assertTrue(policy.decide(who, F.record_for(result, "todoist", "personal"), "complete_task", F.NOW)[0])
        self.assertEqual(policy.decide(who, F.record_for(result, "todoist", "bytalos"), "complete_task", F.NOW), (False, "not_personal_task"))

    def test_concord_approval_and_resume_are_two_separate_operations(self):
        who = F.principal()
        pending = F.record_for(build(who), "concord", "personal")
        self.assertEqual(policy.decide(who, pending, "resume_work", F.NOW), (False, "not_resumable"))   # an approval request is not resumable work
        approved = F.raw("concord", "personal")
        approved.update(status="approved", resume_eligible=True)
        resumable = inbox.build_inbox([F.FakeSource("concord", "personal", rows=[approved])], who, F.NOW).records[0]
        self.assertEqual((resumable.kind, resumable.approval_required, "resumable" in resumable.flags), ("agent_work", False, True))
        self.assertEqual(policy.decide(who, resumable, "approve", F.NOW), (False, "no_pending_approval"))  # nothing left to approve
        no_resume = F.principal(actions={"concord": frozenset({"view", "open", "approve", "reject", "request_changes"})})
        self.assertEqual(policy.decide(no_resume, resumable, "resume_work", F.NOW), (False, "action_not_granted"))     # approving rights never imply resuming
        no_approve = F.principal(actions={"concord": frozenset({"view", "open", "resume_work"})})
        self.assertEqual(policy.decide(no_approve, pending, "approve", F.NOW), (False, "action_not_granted"))
        self.assertTrue(policy.decide(F.principal(), resumable, "resume_work", F.NOW)[0])

    def test_an_approval_does_not_bypass_authorization_for_the_underlying_execution(self):
        who = F.principal(workspaces=("personal",))
        bytalos_pending = F.record_for(build(), "concord", "bytalos")
        svc, executor = service()
        with self.assertRaises(InboxError):
            authorize(who, bytalos_pending, "approve")                                                   # no principal can mint an authorization for a scope it has no grant in
        wide = F.principal()
        auth = authorize(wide, bytalos_pending, "approve")
        self.assertEqual(svc.request(who, bytalos_pending, "approve", auth, "k", F.NOW).reason, "no_grant")     # a valid authorization in the wrong hands still does nothing
        self.assertEqual(executor.calls, [])

    def test_followup_changes_go_through_the_canonical_authority_only(self):
        who, result = widest(), build(widest())
        record = F.record_for(result, "followup", "personal")
        self.assertEqual({a for a in EVERY_ACTION if policy.decide(who, record, a, F.NOW)[0]}, {"view", "open", "confirm_resolution", "update_checkpoint"})
        for action in ("edit", "create", "reschedule", "assign", "delete"):
            self.assertFalse(policy.decide(who, record, action, F.NOW)[0], action)


class ProtectedActions(unittest.TestCase):
    def setUp(self):
        self.who = F.principal()
        self.record = F.record_for(build(self.who), "jira", "personal")

    def test_a_protected_action_needs_an_exact_authorization(self):
        svc, executor = service()
        self.assertEqual(svc.request(self.who, self.record, "accept_uat", None, "k", F.NOW).reason, "authorization_missing")
        good = authorize(self.who, self.record, "accept_uat")
        for label, auth, reason in (("forged", replace(good, mac="0" * 64), "authorization_forged"),
                                    ("other action", authorize(self.who, self.record, "reject_uat"), "authorization_mismatch"),
                                    ("other record", authorize(self.who, F.record_for(build(self.who), "jira", "bytalos"), "accept_uat"), "authorization_mismatch"),
                                    ("other revision", authorize(self.who, replace(self.record, revision="old"), "accept_uat"), "authorization_mismatch"),
                                    ("other principal", policy.sign(F.KEY, "mallory", self.record, "accept_uat", "n9", F.NOW + timedelta(minutes=5)), "authorization_mismatch"),
                                    ("other workspace", replace(good, workspace="bytalos"), "authorization_forged"),                                 # source, workspace and account are signed too
                                    ("other account", replace(good, account="jim-bytalos"), "authorization_forged"),
                                    ("expired", policy.sign(F.KEY, "jim", self.record, "accept_uat", "n8", F.NOW), "authorization_expired")):
            self.assertEqual(svc.request(self.who, self.record, "accept_uat", auth, f"k-{label}", F.NOW).reason, reason, label)
        self.assertEqual(executor.calls, [])
        done = svc.request(self.who, self.record, "accept_uat", good, "k-good", F.NOW)
        self.assertEqual((done.status, done.reason), ("completed", "read_back_verified"))
        self.assertEqual(executor.calls, [(self.record.record_id, "accept_uat")])

    def test_repeated_requests_cannot_execute_the_same_protected_action_twice(self):                       # check 9
        svc, executor = service()
        auth = authorize(self.who, self.record, "accept_uat")
        first = svc.request(self.who, self.record, "accept_uat", auth, "k1", F.NOW)
        again = svc.request(self.who, self.record, "accept_uat", auth, "k1", F.NOW)                          # a retry of the same request
        self.assertEqual((first.status, again.status, again.replayed), ("completed", "completed", True))
        self.assertEqual(svc.request(self.who, self.record, "accept_uat", auth, "k2", F.NOW).reason, "authorization_used")                # a new key, the spent authorization
        fresh = authorize(self.who, self.record, "accept_uat", nonce="n2")
        self.assertEqual(svc.request(self.who, self.record, "accept_uat", fresh, "k3", F.NOW).reason, "already_applied")                  # a new authorization, the same revision
        self.assertEqual(svc.request(self.who, self.record, "reject_uat", authorize(self.who, self.record, "reject_uat", "n3"), "k1", F.NOW).reason, "idempotency_key_reused")
        self.assertEqual(len(executor.calls), 1)

    def test_a_stale_record_cannot_produce_a_completion_claim(self):                                        # check 10
        svc, executor = service()
        later = F.NOW + timedelta(minutes=16)
        auth = policy.mint(F.KEY, self.who, self.record, "accept_uat", "n1", later)
        self.assertEqual(svc.request(self.who, self.record, "accept_uat", auth, "k", later).reason, "record_stale")
        self.assertEqual(executor.calls, [])

    def test_completion_is_claimed_only_from_the_source_read_back(self):                                   # check 10
        for label, kw, status, reason in (("wrong status", {"status": "In Review"}, "uncertain", "read_back_not_proven"),
                                          ("same revision", {"revision": self.record.revision}, "uncertain", "read_back_not_proven"),
                                          ("executor raised", {"raises": RuntimeError("boom")}, "uncertain", "executor_outcome_unknown"),
                                          ("verified", {}, "completed", "read_back_verified")):
            svc, executor = service(**kw)
            out = svc.request(self.who, self.record, "accept_uat", authorize(self.who, self.record, "accept_uat"), "k", F.NOW)
            self.assertEqual((out.status, out.reason), (status, reason), label)
            self.assertEqual(len(executor.calls), 1, label)
        svc, _ = service(status="In Review")
        out = svc.request(self.who, self.record, "accept_uat", authorize(self.who, self.record, "accept_uat"), "k", F.NOW)
        retry = svc.request(self.who, self.record, "accept_uat", authorize(self.who, self.record, "accept_uat", "n2"), "k", F.NOW)
        self.assertEqual((retry.status, retry.replayed), ("uncertain", True))                                 # an unproven result is never retried into a different answer

    def test_agent_written_text_is_never_read_as_completion(self):
        done_text = replace(self.record, description="DONE. Completed and verified by the agent.", source_status="Human UAT")
        self.assertEqual((done_text.source_status, done_text.presentation), ("Human UAT", "approvals"))      # the record still says what the source says
        svc, _ = service(status="Human UAT")
        out = svc.request(self.who, done_text, "accept_uat", authorize(self.who, done_text, "accept_uat"), "k", F.NOW)
        self.assertEqual(out.status, "uncertain")

    def test_read_actions_need_no_authorization_but_still_need_a_grant(self):
        svc, executor = service()
        self.assertEqual(svc.request(self.who, self.record, "open", None, "k", F.NOW).status, "completed")
        self.assertEqual(svc.request(F.principal(workspaces=()), self.record, "open", None, "k2", F.NOW).reason, "no_grant")
        self.assertEqual(executor.calls, [])


class NoLeaks(unittest.TestCase):
    def test_secrets_and_record_contents_never_reach_audit_or_logs(self):                                  # check 11
        who = F.principal()
        sources = F.all_sources()
        sources[-1] = F.FakeSource("asana", "bytalos", error=RuntimeError(F.ERROR_CANARY))
        with self.assertLogs("lifeos.inbox", level="INFO") as logs:
            result = inbox.build_inbox(sources, who, F.NOW)
            svc, _ = service(raises=RuntimeError(F.ERROR_CANARY))
            record = F.record_for(result, "jira", "personal")
            svc.request(who, record, "accept_uat", authorize(who, record, "accept_uat"), "k1", F.NOW)
            svc.request(who, record, "edit", None, "k2", F.NOW)
            svc.request(F.principal(workspaces=()), record, "open", None, "k3", F.NOW)
        text = "\n".join(logs.output) + repr(svc.audit) + repr(result.health)
        for needle in (F.CANARY, F.ERROR_CANARY, "cred:", "example.test", F.KEY.decode(), F.SAME_TITLE):
            self.assertNotIn(needle, text, needle)
        self.assertEqual(sorted(svc.audit[0]), ["action", "principal", "reason", "record", "request", "status"])


if __name__ == "__main__":
    unittest.main()
