"""the bounded Followup classifier."""
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.inbox import followup, followup_classify as C, followup_stage as S
from lifeos.platform import followup_readers as R
from tests.inbox.test_followup_stage import ACCOUNT, BASE, Rig, T0

UTC = timezone.utc


def item(source="todoist", title="Waiting on Dana for the signed quote", due="2026-11-20", link=None):
    out = {"source": source, "native_id": "n1", "link": link or f"{BASE}/{source}/n1", "kind": R.KINDS[source], "observed_at": "2026-11-16T18:00:00+00:00", "title": title}
    if due:
        out["due"] = due
    return out


class Markers(unittest.TestCase):
    def test_each_explicit_marker_makes_its_kind_and_owner(self):
        cases = {"Waiting on Dana for the signed quote": ("waiting_on", "external"), "waiting for the permit": ("waiting_on", "external"), "WFR: vendor reply": ("waiting_on", "external"),
                 "Follow up with Sam about the lease": ("jim_capture", "jim"), "Follow-up: insurance claim": ("jim_capture", "jim"), "FU: tax form": ("jim_capture", "jim"),
                 "I owe Pat the revised quote": ("promise", "jim"), "Promised the draft to Lee": ("promise", "jim"), "Promise: send photos": ("promise", "jim"),
                 "Deliverable: Q4 plan": ("deliverable", "jim"), "Value-add: intro to Kim": ("value_add", "jim")}
        for title, (kind, owner) in cases.items():
            with self.subTest(title=title):
                found = C.classify(item(title=title))
                self.assertEqual((found["kind"], found["owner"]), (kind, owner))
                self.assertIn(found["kind"], followup.CREATES_LOOP)

    def test_the_outcome_is_the_text_after_the_marker(self):
        self.assertEqual(C.classify(item(title="Waiting on   Dana for the signed quote. "))["outcome_key"], "Dana for the signed quote")
        self.assertEqual(C.classify(item(title="Follow up: the lease"))["outcome_key"], "the lease")

    def test_titles_without_an_explicit_marker_are_observation_only(self):
        for title in ("Send the budget summary", "Waiting room supplies", "Fu Manchu dinner", "Promising startups to read", "Owen's birthday", "Follower count report", "Review the follow up policy", ""):
            with self.subTest(title=title):
                self.assertIsNone(C.classify(item(title=title)))

    def test_a_marker_with_no_outcome_is_not_a_loop(self):
        for title in ("Waiting on", "Follow up:", "Deliverable:  ", "Follow up " + "x" * 130):
            with self.subTest(title=title):
                self.assertIsNone(C.classify(item(title=title)))

    def test_mail_and_calendar_never_classify_even_with_a_marker(self):
        for source in ("gmail", "outlook", "calendar"):
            with self.subTest(source=source):
                self.assertIsNone(C.classify(item(source=source, title="Waiting on Dana for the signed quote")))

    def test_the_sources_due_date_is_the_checkpoint_and_no_due_date_is_only_suggested(self):
        dated, bare = C.classify(item(due="2026-11-20")), C.classify(item(due=None))
        self.assertEqual((dated["checkpoint"], dated["checkpoint_source"]), ("2026-11-20", "source"))
        self.assertEqual((bare["checkpoint"], bare["checkpoint_source"]), (None, "suggested"))

    def test_it_never_emits_a_closing_or_intermediate_kind(self):
        kinds = {C.classify(item(title=title))["kind"] for title in ("Waiting on abc", "Follow up abc", "Owe abc", "Deliverable: abc", "Value-add: abc")}
        self.assertFalse(kinds & (followup.OUTCOME_ENDED | followup.INTERMEDIATE | followup.NEVER_ALONE))


class Projection(unittest.TestCase):
    def project(self, items, now=datetime(2026, 11, 17, 12, 0, tzinfo=UTC)):
        evidence = [{**C.classify(i), "workspace": "personal"} for i in items if C.classify(i)]
        return followup.project(evidence, now, "personal")

    def test_a_dated_waiting_loop_is_active_and_counted(self):
        result = self.project([item()])
        self.assertEqual(result.counts, {"needs_action_today": 0, "waiting_aging": 1, "upcoming": 0})
        self.assertEqual(result.review, [])

    def test_a_dated_owed_loop_is_upcoming_then_needs_action_by_time(self):
        loop = item(title="I owe Pat the revised quote", due="2026-11-20")
        self.assertEqual(self.project([loop]).counts["upcoming"], 1)
        self.assertEqual(self.project([loop], datetime(2026, 11, 21, 12, 0, tzinfo=UTC)).counts["needs_action_today"], 1)

    def test_an_undated_loop_is_review_only_and_never_counted(self):
        result = self.project([item(due=None)])
        self.assertEqual((sum(result.counts.values()), len(result.review)), (0, 1))

    def test_the_same_outcome_in_two_sources_is_one_loop_with_both_links(self):
        result = self.project([item("todoist", "Waiting on Dana for the signed quote"), item("jira", "Waiting for Dana for the signed quote", link=f"{BASE}/jira/n2")])
        self.assertEqual(sum(result.counts.values()), 1)
        self.assertIn("evidence 2", result.native[0]["notes"])

    def test_the_same_person_with_a_different_outcome_stays_a_different_loop(self):
        result = self.project([item(title="Waiting on Dana for the signed quote"), item(title="Waiting on Dana for the permit")])
        self.assertEqual(sum(result.counts.values()), 2)

    def test_a_plain_task_a_ticket_or_a_finished_meeting_makes_no_loop(self):
        result = self.project([item(title="Pay the electric bill"), item("jira", "LIFE-5 fix the thing"), item("calendar", "Planning call (ended)")])
        self.assertEqual((sum(result.counts.values()), result.review), (0, []))


class Stage(unittest.TestCase):
    def rig(self, rows=None):
        rows = rows if rows is not None else [{"id": "t1", "url": "https://app.todoist.com/app/task/t1", "updated_at": "2026-11-16T18:00:00Z", "content": "Waiting on Dana for the signed quote", "due": "2026-11-20"},
                                              {"id": "t2", "url": "https://app.todoist.com/app/task/t2", "updated_at": "2026-11-16T18:01:00Z", "content": "Pay the electric bill", "due": "2026-11-18"},
                                              {"id": "t3", "url": "https://app.todoist.com/app/task/t3", "updated_at": "2026-11-16T18:02:00Z", "content": "Follow up with Sam about the lease"}]
        return Rig(sources_=("todoist",), rows={"todoist": rows}, classify=C.classify)

    def test_a_live_run_turns_marked_todoist_tasks_into_loops_and_the_rest_into_nothing(self):
        rig = self.rig()
        counts = rig.run()
        (payload,) = rig.published
        self.assertEqual((counts["threads"], counts["review"], counts["written"]), (1, 1, 2))
        self.assertEqual([(t["title"], t["state"]) for t in payload["threads"]], [("Dana for the signed quote", "waiting_aging")])
        self.assertEqual(len(payload["review"]), 1)
        self.assertEqual(payload["counts"], {"needs_action_today": 0, "waiting_aging": 1, "upcoming": 0})

    def test_a_second_run_creates_no_duplicate_thread(self):
        rig = self.rig()
        rig.run()
        before = rig.store.threads("personal", ACCOUNT)
        counts = rig.run(at=T0 + timedelta(hours=1))
        self.assertEqual((counts["written"], counts["threads"]), (0, 1))
        self.assertEqual(rig.store.threads("personal", ACCOUNT), before)

    def test_completing_the_task_does_not_close_the_outcome(self):
        rig = self.rig()
        rig.run()
        rig.rows["todoist"] = []
        rig.run(at=T0 + timedelta(hours=1))
        self.assertEqual([t["resolution"] for t in rig.store.threads("personal", ACCOUNT)].count("open"), 2)

    def test_a_degraded_source_keeps_the_last_accepted_loops(self):
        rig = self.rig()
        rig.run()
        rig.rows["todoist"] = RuntimeError("x")
        rig.run(at=T0 + timedelta(hours=1))
        payload = rig.published[-1]
        self.assertEqual(payload["state"], "DEGRADED")
        self.assertEqual(payload["counts"]["waiting_aging"], 1)

    def test_production_wiring_uses_this_classifier(self):
        import inspect
        from lifeos.inbox import followup_wiring
        self.assertIn("classify=followup_classify.classify", inspect.getsource(followup_wiring.build))


class Reader(unittest.TestCase):
    def test_a_due_date_the_source_holds_rides_on_the_item_and_a_missing_or_odd_one_does_not(self):
        norm = R.NORMALIZE["todoist"]
        base = {"id": "t1", "url": "https://app.todoist.com/app/task/t1", "updated_at": "2026-11-16T18:00:00Z", "content": "x"}
        self.assertEqual(norm({**base, "due": "2026-11-20"})["due"], "2026-11-20")
        self.assertEqual(norm({**base, "due": "2026-11-20T15:00:00Z"})["due"], "2026-11-20T15:00:00Z")
        for odd in (None, "", "tomorrow", "soon 2026-11-20", 5):
            self.assertNotIn("due", norm({**base, "due": odd}))
        self.assertNotIn("due", norm(base))

    def test_jira_carries_its_duedate(self):
        out = R._jira({"key": "LIFE-1", "fields": {"updated": "2026-11-16T18:00:00Z", "summary": "Waiting on Dana", "duedate": "2026-11-20"}}, BASE)
        self.assertEqual(out["due"], "2026-11-20")


if __name__ == "__main__":
    unittest.main()
