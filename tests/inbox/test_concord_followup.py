"""LIFE-710 (synthetic): the read-only Concord projection and the Followup verdict plus bounded projection. No live read, no write, no TGP, no cross-workspace leak."""
import json
import unittest
from pathlib import Path

from lifeos.inbox import followup, inbox
from lifeos.inbox.concord_source import ConcordSource
from lifeos.inbox.model import InboxError
from tests.inbox import fixtures as F

T0 = "2026-11-14T18:00:00Z"
T1 = "2026-11-14T18:30:00Z"
HUMAN, AGENT = "human:jim", "claude-code:2d46a09c"


def env(**kw):
    base = {"lifeos_inbox": 1, "kind": "approval_request", "workspace": "personal", "title": "Deploy plan", "source_url": "https://example.test/LIFE-1", "evidence": ["https://example.test/pr/1"],
            "resume_after_approval": True, "correlation_id": "corr-1"}
    base.update(kw)
    return json.dumps(base)


def msg(mid, content, sender=AGENT, at=T0, reply=None):
    m = {"messageId": mid, "senderAgentId": sender, "recipientAgentId": "human:jim", "content": content, "createdAt": at, "status": "sent"}
    if reply:
        m["replyToMessageId"] = reply
    return m


def decision(mid, parent, which="approve", sender=HUMAN, at=T1):
    return msg(mid, json.dumps({"lifeos_inbox": 1, "kind": "approval_decision", "decision": which}), sender=sender, at=at, reply=parent)


def source(workspace="personal", messages=None, live=(AGENT,), error=None):
    def open_reader(credential_ref):
        def read():
            if error:
                raise error
            return ([msg("m1", env(workspace=workspace))] if messages is None else messages), live
        return read
    return ConcordSource(workspace, f"jim-{workspace}", "jim", open_reader=open_reader)


def run(srcs):
    return inbox.build_inbox(srcs, F.principal(sources=("concord", "followup", "jira")), F.NOW)


class ConcordProjection(unittest.TestCase):
    def test_pending_request_is_an_approval_with_evidence_and_link(self):
        result = run([source()])
        rec = result.records[0]
        self.assertEqual((rec.presentation, rec.source_status, rec.approval_required, rec.url), ("approvals", "pending_approval", True, "https://example.test/LIFE-1"))
        self.assertEqual(rec.approval_evidence, ("https://example.test/pr/1",))
        self.assertEqual(rec.correlation_id, "corr-1")
        self.assertEqual(result.health[0].state, "ok")

    def test_human_approval_makes_it_resumable_only_when_the_agent_is_live_and_resume_was_asked(self):
        msgs = [msg("m1", env()), decision("d1", "m1")]
        rec = run([source(messages=msgs)]).records[0]
        self.assertEqual((rec.source_status, rec.presentation, "resumable" in rec.flags, rec.approval_required), ("approved", "needs_me", True, False))
        offline = run([source(messages=msgs, live=())]).records[0]
        self.assertNotIn("resumable", offline.flags)                                     # approval is not resume eligibility
        unknown = run([source(messages=msgs, live=None)]).records[0]
        self.assertNotIn("resumable", unknown.flags)                                     # `who` unreadable: never guessed
        no_resume = run([source(messages=[msg("m1", env(resume_after_approval=False)), decision("d1", "m1")])]).records[0]
        self.assertNotIn("resumable", no_resume.flags)

    def test_agent_sent_or_self_replied_decisions_never_count(self):
        rec = run([source(messages=[msg("m1", env()), decision("d1", "m1", sender="codex:ab12")])]).records[0]
        self.assertEqual(rec.source_status, "pending_approval")
        rec = run([source(messages=[msg("m1", env(), sender=HUMAN), decision("d1", "m1", sender=HUMAN)])]).records[0]
        self.assertEqual(rec.source_status, "pending_approval")

    def test_newest_human_decision_wins_and_rejection_is_not_resumable(self):
        msgs = [msg("m1", env()), decision("d1", "m1", "approve"), decision("d2", "m1", "reject", at="2026-11-14T18:45:00Z")]
        rec = run([source(messages=msgs)]).records[0]
        self.assertEqual((rec.source_status, rec.presentation), ("rejected", "waiting"))
        self.assertEqual(rec.approval_evidence, ())

    def test_messages_without_the_envelope_are_ignored_not_mined(self):
        msgs = [msg("m1", "please approve the deploy, it is urgent"), msg("m2", "{not json"), msg("m3", json.dumps({"kind": "approval_request"}))]
        result = run([source(messages=msgs)])
        self.assertEqual((result.records, result.health[0].state), ((), "ok"))

    def test_other_workspace_and_tgp_envelopes_are_dropped(self):
        msgs = [msg("m1", env(workspace="bytalos")), msg("m2", env(workspace="tgp")), msg("m3", env(workspace="personal"))]
        self.assertEqual([r.native_id for r in run([source(messages=msgs)]).records], ["m3"])

    def test_two_workspaces_stay_isolated_with_their_own_ids(self):
        p, b = source("personal", [msg("m1", env(workspace="personal"))]), source("bytalos", [msg("m1", env(workspace="bytalos"))])
        records = run([p, b]).records
        self.assertEqual(sorted(r.record_id for r in records), ["concord:bytalos:jim-bytalos:m1", "concord:personal:jim-personal:m1"])

    def test_tgp_source_is_refused_before_any_read(self):
        calls = []
        tgp = ConcordSource("tgp", "jim-tgp", "jim", open_reader=lambda ref: calls.append(ref))
        result = run([tgp])
        self.assertEqual((result.records, result.health[0].state, calls), ((), "refused", []))

    def test_offline_client_degrades_only_concord(self):
        jira = F.FakeSource("jira", "personal")
        result = run([source(error=OSError(F.ERROR_CANARY)), jira])
        states = {h.source: h.state for h in result.health}
        self.assertEqual(states, {"concord": "unavailable", "jira": "ok"})
        self.assertEqual([r.source for r in result.records], ["jira"])
        self.assertNotIn(F.ERROR_CANARY, repr(result.health))

    def test_malformed_or_partial_output_is_unavailable_never_a_smaller_list(self):
        bad = run([source(messages=[msg("m1", env()), {"messageId": "m2"}])])
        self.assertEqual((bad.records, bad.health[0].reason), ((), "listing_incomplete"))
        over = run([source(messages=[msg(f"m{n}", "x") for n in range(81)])])
        self.assertEqual(over.health[0].state, "unavailable")

    def test_default_reader_refuses_live_reads(self):
        result = run([ConcordSource("personal", "jim-personal", "jim")])
        self.assertEqual((result.records, result.health[0].state, result.health[0].reason), ((), "unavailable", "live_reads_not_authorized"))

    def test_bad_source_link_rejects_only_that_record(self):
        msgs = [msg("m1", env(source_url="http://plain.test/x")), msg("m2", env())]
        result = run([source(messages=msgs)])
        self.assertEqual(([r.native_id for r in result.records], result.health[0].state, result.health[0].rejected), (["m2"], "degraded", 1))

    def test_module_reaches_no_write_path(self):
        text = Path("lifeos/inbox/concord_source.py").read_text()
        code = "\n".join(line.split("#")[0] for line in text.split('"""')[2:])             # the docstring names what is refused; the code must not use it
        for word in ("update_work", "subprocess", "execFile", "os.system", "requests", "urlopen", "socket", "write_text", "open("):
            self.assertNotIn(word, code)


def ev(kind, key, owner="jim", **kw):
    base = {"kind": kind, "outcome_key": key, "workspace": "personal", "owner": owner, "who": "vendor", "title": f"Loop {key}", "link": f"https://example.test/{key}/{kind}",
            "observed_at": "2026-11-14T12:00:00-06:00"}
    base.update(kw)
    return base


class FollowupVerdict(unittest.TestCase):
    def test_the_default_followup_reader_refuses_while_live_reads_are_off(self):
        self.assertEqual(followup.FOLLOWUP_PROJECTION_OWNER, "lifeos.inbox.followup_stage")          # LIFE-749: the stage owns the projection; nothing is wired in production
        result = run([followup.FollowupSource("personal", "jim-personal", "jim")])
        self.assertEqual((result.records, result.health[0].state, result.health[0].reason), ((), "unavailable", "live_reads_not_authorized"))

    def test_other_sources_survive_an_unavailable_followup(self):
        result = run([followup.FollowupSource("personal", "jim-personal", "jim"), F.FakeSource("jira", "personal")])
        self.assertEqual({h.source: h.state for h in result.health}, {"followup": "unavailable", "jira": "ok"})
        self.assertEqual(len(result.records), 1)


class FollowupProjection(unittest.TestCase):
    now = F.NOW

    def project(self, evidence, workspace="personal"):
        return followup.project(evidence, self.now, workspace)

    def test_unread_mail_a_finished_meeting_and_generic_tasks_alone_make_no_followup(self):
        p = self.project([ev("unread_mail", "a"), ev("meeting_ended", "b"), ev("open_jira_task", "c"), ev("open_todoist_task", "d")])
        self.assertEqual((p.native, p.review, p.degraded), ([], [], False))

    def test_explicit_promise_creates_a_loop_with_source_backed_checkpoint(self):
        p = self.project([ev("promise", "a", checkpoint="2026-11-14T09:00:00", checkpoint_source="source"), ev("unread_mail", "z")])
        self.assertEqual([loop["id"] for loop in p.native], ["a"])
        self.assertEqual(p.counts["needs_action_today"], 1)
        self.assertIn("source-backed", p.native[0]["notes"])

    def test_linked_multi_source_same_outcome_dedupes_and_unrelated_loops_stay_distinct(self):
        p = self.project([ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"), ev("deliverable", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
                          ev("promise", "b", checkpoint="2026-11-21T09:00:00", checkpoint_source="source")])
        self.assertEqual([loop["id"] for loop in p.native], ["a", "b"])
        self.assertEqual(p.counts["upcoming"], 2)

    def test_three_active_states_and_review_is_separate_and_uncounted(self):
        p = self.project([ev("promise", "t", checkpoint="2026-11-14T09:00:00", checkpoint_source="source"), ev("waiting_on", "w", owner="external", checkpoint="2026-11-30T09:00:00", checkpoint_source="source"),
                          ev("promise", "u", checkpoint="2026-12-01T09:00:00", checkpoint_source="source"), ev("promise", "s", checkpoint="2026-11-15T09:00:00", checkpoint_source="suggested"),
                          ev("implied_promise", "i"), ev("promise", "amb", owner="ambiguous")])
        self.assertEqual(p.counts, {"needs_action_today": 1, "waiting_aging": 1, "upcoming": 1})
        self.assertEqual(sorted(loop["id"] for loop in p.review), ["amb", "i", "s"])
        self.assertIn("suggested", next(loop for loop in p.review if loop["id"] == "s")["notes"])

    def test_source_backed_date_beats_suggested_and_jim_override_beats_both_without_touching_sources(self):
        items = [ev("promise", "a", checkpoint="2026-11-19T09:00:00", checkpoint_source="suggested"), ev("deliverable", "a", checkpoint="2026-11-18T09:00:00", checkpoint_source="source")]
        self.assertEqual(self.project(items).native[0]["next_checkpoint"], "2026-11-18T09:00:00")
        items.append(ev("jim_capture", "a", override="2026-11-25T09:00:00"))
        loop = self.project(items).native[0]
        self.assertEqual((loop["next_checkpoint"], "jim_override" in loop["notes"]), ("2026-11-25T09:00:00", True))
        self.assertEqual(items[1]["checkpoint"], "2026-11-18T09:00:00")                          # the source item is unchanged

    def test_contradictory_owners_never_merge(self):
        p = self.project([ev("promise", "a", owner="jim", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"), ev("waiting_on", "a", owner="external", checkpoint="2026-11-20T09:00:00",
                                                                                                                          checkpoint_source="source")])
        self.assertEqual((p.native, len(p.review)), ([], 2))

    def test_no_premature_closure_and_closure_only_on_strong_proof(self):
        base = [ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source")]
        for kind in sorted(followup.INTERMEDIATE):
            self.assertEqual([loop["id"] for loop in self.project(base + [ev(kind, "a")]).native], ["a"], kind)
        for kind in sorted(followup.OUTCOME_ENDED):
            self.assertEqual(self.project(base + [ev(kind, "a")]).native, [], kind)

    def test_reopen_needs_new_authoritative_evidence(self):
        closed = [ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"), ev("outcome_ended", "a")]
        self.assertEqual(self.project(closed).native, [])
        self.assertEqual([loop["id"] for loop in self.project([ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source")]).native], ["a"])

    def test_stale_denied_or_missing_evidence_is_degraded_never_a_trustworthy_zero(self):
        p = self.project([ev("promise", "a", stale=True), ev("promise", "b", denied=True), ev("promise", "c", link="")])
        self.assertEqual((p.native, p.degraded), ([], True))
        with self.assertRaises(InboxError) as caught:
            followup.SyntheticFollowupSource("personal", "jim-personal", "jim", [ev("promise", "a", stale=True)], self.now).fetch("cred")
        self.assertEqual(caught.exception.reason, "evidence_degraded")

    def test_trustworthy_evidence_survives_alongside_degraded_evidence(self):
        p = self.project([ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"), ev("promise", "b", stale=True)])
        self.assertEqual(([loop["id"] for loop in p.native], p.degraded), (["a"], True))

    def test_two_workspace_isolation_and_no_tgp(self):
        items = [ev("promise", "p", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"), ev("promise", "b", workspace="bytalos", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
                 ev("promise", "t", workspace="tgp", checkpoint="2026-11-20T09:00:00", checkpoint_source="source")]
        self.assertEqual([loop["id"] for loop in self.project(items, "personal").native], ["p"])
        self.assertEqual([loop["id"] for loop in self.project(items, "bytalos").native], ["b"])
        tgp = run([followup.SyntheticFollowupSource("tgp", "jim-tgp", "jim", items, self.now)])
        self.assertEqual((tgp.records, tgp.health[0].state), ((), "refused"))

    def test_projection_feeds_the_followup_adapter_and_only_links_no_payloads(self):
        src = followup.SyntheticFollowupSource("personal", "jim-personal", "jim", [ev("promise", "a", checkpoint="2026-11-14T09:00:00", checkpoint_source="source", body=F.CANARY)], self.now)
        result = run([src])
        rec = result.records[0]
        self.assertEqual((rec.source, rec.kind, rec.presentation), ("followup", "checkpoint", "needs_me"))
        self.assertNotIn(F.CANARY, repr(rec))

    def test_projection_is_pure_and_module_has_no_write_path(self):
        text = Path("lifeos/inbox/followup.py").read_text()
        for word in ("subprocess", "requests", "urlopen", "socket", "write_text", "open(", "connect", "INSERT", "UPDATE"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
