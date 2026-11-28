"""(synthetic): the bounded Followup projection."""

import unittest
from pathlib import Path

from lifeos.inbox import followup
from lifeos.inbox.model import InboxError
from tests.inbox import fixtures as F


def ev(kind, key, owner="jim", **kw):
    base = {
        "kind": kind,
        "outcome_key": key,
        "workspace": "personal",
        "owner": owner,
        "who": "vendor",
        "title": f"Loop {key}",
        "link": f"https://example.test/{key}/{kind}",
        "observed_at": "2026-11-14T12:00:00-06:00",
    }
    base.update(kw)
    return base


class FollowupProjection(unittest.TestCase):
    now = F.NOW

    def project(self, evidence, workspace="personal"):
        return followup.project(evidence, self.now, workspace)

    def test_unread_mail_a_finished_meeting_and_generic_tasks_alone_make_no_followup(self):
        p = self.project(
            [ev("unread_mail", "a"), ev("meeting_ended", "b"), ev("open_jira_task", "c"), ev("open_todoist_task", "d")]
        )
        self.assertEqual((p.native, p.review, p.degraded), ([], [], False))

    def test_explicit_promise_creates_a_loop_with_source_backed_checkpoint(self):
        p = self.project(
            [ev("promise", "a", checkpoint="2026-11-14T09:00:00", checkpoint_source="source"), ev("unread_mail", "z")]
        )
        self.assertEqual([loop["id"] for loop in p.native], ["a"])
        self.assertEqual(p.counts["needs_action_today"], 1)
        self.assertIn("source-backed", p.native[0]["notes"])

    def test_linked_multi_source_same_outcome_dedupes_and_unrelated_loops_stay_distinct(self):
        p = self.project(
            [
                ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
                ev("deliverable", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
                ev("promise", "b", checkpoint="2026-11-21T09:00:00", checkpoint_source="source"),
            ]
        )
        self.assertEqual([loop["id"] for loop in p.native], ["a", "b"])
        self.assertEqual(p.counts["upcoming"], 2)

    def test_three_active_states_and_review_is_separate_and_uncounted(self):
        p = self.project(
            [
                ev("promise", "t", checkpoint="2026-11-14T09:00:00", checkpoint_source="source"),
                ev("waiting_on", "w", owner="external", checkpoint="2026-11-30T09:00:00", checkpoint_source="source"),
                ev("promise", "u", checkpoint="2026-12-01T09:00:00", checkpoint_source="source"),
                ev("promise", "s", checkpoint="2026-11-15T09:00:00", checkpoint_source="suggested"),
                ev("implied_promise", "i"),
                ev("promise", "amb", owner="ambiguous"),
            ]
        )
        self.assertEqual(p.counts, {"needs_action_today": 1, "waiting_aging": 1, "upcoming": 1})
        self.assertEqual(sorted(loop["id"] for loop in p.review), ["amb", "i", "s"])
        self.assertIn("suggested", next(loop for loop in p.review if loop["id"] == "s")["notes"])

    def test_source_backed_date_beats_suggested_and_jim_override_beats_both_without_touching_sources(self):
        items = [
            ev("promise", "a", checkpoint="2026-11-19T09:00:00", checkpoint_source="suggested"),
            ev("deliverable", "a", checkpoint="2026-11-18T09:00:00", checkpoint_source="source"),
        ]
        self.assertEqual(self.project(items).native[0]["next_checkpoint"], "2026-11-18T09:00:00")
        items.append(ev("jim_capture", "a", override="2026-11-25T09:00:00"))
        loop = self.project(items).native[0]
        self.assertEqual((loop["next_checkpoint"], "jim_override" in loop["notes"]), ("2026-11-25T09:00:00", True))
        self.assertEqual(items[1]["checkpoint"], "2026-11-18T09:00:00")  # the source item is unchanged

    def test_contradictory_owners_never_merge(self):
        p = self.project(
            [
                ev("promise", "a", owner="jim", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
                ev("waiting_on", "a", owner="external", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
            ]
        )
        self.assertEqual((p.native, len(p.review)), ([], 2))

    def test_no_premature_closure_and_closure_only_on_strong_proof(self):
        base = [ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source")]
        for kind in sorted(followup.INTERMEDIATE):
            self.assertEqual([loop["id"] for loop in self.project(base + [ev(kind, "a")]).native], ["a"], kind)
        for kind in sorted(followup.OUTCOME_ENDED):
            self.assertEqual(self.project(base + [ev(kind, "a")]).native, [], kind)

    def test_reopen_needs_new_authoritative_evidence(self):
        closed = [
            ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
            ev("outcome_ended", "a"),
        ]
        self.assertEqual(self.project(closed).native, [])
        self.assertEqual(
            [
                loop["id"]
                for loop in self.project(
                    [ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source")]
                ).native
            ],
            ["a"],
        )

    def test_stale_denied_or_missing_evidence_is_degraded_never_a_trustworthy_zero(self):
        p = self.project([ev("promise", "a", stale=True), ev("promise", "b", denied=True), ev("promise", "c", link="")])
        self.assertEqual((p.native, p.degraded), ([], True))
        with self.assertRaises(InboxError) as caught:
            followup.SyntheticFollowupSource(
                "personal", "jim-personal", "jim", [ev("promise", "a", stale=True)], self.now
            ).fetch("cred")
        self.assertEqual(caught.exception.reason, "evidence_degraded")

    def test_trustworthy_evidence_survives_alongside_degraded_evidence(self):
        p = self.project(
            [
                ev("promise", "a", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
                ev("promise", "b", stale=True),
            ]
        )
        self.assertEqual(([loop["id"] for loop in p.native], p.degraded), (["a"], True))

    def test_two_workspace_isolation_and_no_tgp(self):
        items = [
            ev("promise", "p", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
            ev("promise", "b", workspace="bytalos", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
            ev("promise", "t", workspace="tgp", checkpoint="2026-11-20T09:00:00", checkpoint_source="source"),
        ]
        self.assertEqual([loop["id"] for loop in self.project(items, "personal").native], ["p"])
        self.assertEqual([loop["id"] for loop in self.project(items, "bytalos").native], ["b"])

    def test_projection_is_pure_and_module_has_no_write_path(self):
        text = Path("lifeos/inbox/followup.py").read_text()
        for word in (
            "subprocess",
            "requests",
            "urlopen",
            "socket",
            "write_text",
            "open(",
            "connect",
            "INSERT",
            "UPDATE",
        ):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
