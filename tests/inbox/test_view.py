"""LIFE-711: the Inbox view's row projection and its idempotent reconciliation plan, on synthetic data only (no Notion call, no write, no credential)."""
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from lifeos.inbox import followup, inbox, sources, view_plan, view_rows
from lifeos.inbox.model import FRESH_FOR, InboxError
from tests.inbox import fixtures as F


def build(srcs=None, who=None):
    return inbox.build_inbox(srcs if srcs is not None else F.all_sources(), who or F.principal(), F.NOW)


def rows_for(srcs=None, who=None, **kw):
    return view_rows.project_rows(build(srcs, who), F.NOW, **kw)


def by_id(rows):
    return {r["Record ID"]: r for r in rows}


def health(rows, source, workspace):
    return by_id(rows)[f"health:{source}:{workspace}"]


class Projection(unittest.TestCase):
    def test_partial_synthetic_data_shows_jira_and_calendar_and_the_rest_as_not_provisioned(self):
        only = [s for s in F.all_sources() if s.source in ("jira", "calendar")]
        rows = rows_for(only)
        items = [r for r in rows if r["Kind"] == "item"]
        self.assertEqual(sorted({r["Source"] for r in items}), ["Calendar", "Jira"])
        for source, workspace in view_rows.EXPECTED:
            row = health(rows, source, workspace)
            self.assertEqual(row["Source health"], "OK" if source in ("jira", "calendar") else "Unavailable: not provisioned", (source, workspace))
        self.assertEqual(len([r for r in rows if r["Kind"] == "health"]), len(view_rows.EXPECTED))

    def test_every_item_row_has_a_link_a_workspace_label_and_a_bucket(self):
        rows = rows_for()
        items = [r for r in rows if r["Kind"] == "item"]
        self.assertEqual(len(items), 12)
        for r in items:
            self.assertTrue(r["Link"].startswith("https://"))
            self.assertIn(r["Workspace"], ("Personal", "Bytalos"))
            self.assertIn(r["Bucket"], view_rows.BUCKETS.values())
            self.assertIn(r["Freshness"], ("fresh", "stale"))
        self.assertEqual({r["Workspace"] for r in items}, {"Personal", "Bytalos"})
        self.assertEqual(len({r["Record ID"] for r in rows}), len(rows))                       # one row per key
        self.assertEqual([r["Record ID"] for r in rows], sorted(r["Record ID"] for r in rows))
        self.assertEqual(rows_for(), rows_for())                                                 # deterministic

    def test_the_all_personal_and_bytalos_filters_are_the_workspace_property(self):
        rows = [r for r in rows_for() if r["Kind"] == "item"]
        personal = [r for r in rows if r["Workspace"] == "Personal"]
        bytalos = [r for r in rows if r["Workspace"] == "Bytalos"]
        self.assertEqual((len(personal), len(bytalos), len(personal) + len(bytalos)), (6, 6, len(rows)))     # All is the union; no row belongs to neither

    def test_a_failing_source_keeps_the_other_five_and_is_shown_as_unavailable(self):
        srcs = [F.FakeSource(s.source, s.workspace, error=RuntimeError(F.ERROR_CANARY) if (s.source, s.workspace) == ("jira", "personal") else None) for s in F.all_sources()]
        rows = rows_for(srcs)
        self.assertEqual(health(rows, "jira", "personal")["Source health"], "Unavailable: runtimeerror")
        self.assertNotIn(F.ERROR_CANARY, repr(rows))                                           # the exception message never reaches a row
        self.assertEqual({r["Source"] for r in rows if r["Kind"] == "item" and r["Workspace"] == "Personal"}, {"Todoist", "Concord", "Followup", "Calendar", "Asana"})
        self.assertEqual(len([r for r in rows if r["Kind"] == "item"]), 11)

    def test_tgp_never_reaches_a_row(self):
        tgp = F.FakeSource("jira", "tgp")
        rows = rows_for(F.all_sources() + [tgp])
        self.assertEqual(tgp.calls, [])                                                          # not even fetched
        self.assertFalse([r for r in rows if "tgp" in r["Record ID"].lower() or r["Workspace"] == "Tgp"])
        record = build().records[0]
        with self.assertRaises(InboxError):
            view_rows.project_rows(inbox.Inbox((replace(record, workspace="tgp"),), ()), F.NOW)
        with self.assertRaises(InboxError):
            view_rows.project_rows(build(), F.NOW, review=[("tgp", "acct", {"id": "x", "url": "https://example.com/x"})])

    def test_personal_and_bytalos_never_share_a_grant_or_cache(self):
        srcs = F.all_sources()
        rows = rows_for(srcs, F.principal(workspaces=("personal",)))
        self.assertTrue(all(r["Workspace"] == "Personal" for r in rows if r["Kind"] == "item"))
        for s in srcs:
            self.assertEqual(s.calls, [f"cred:{s.source}:personal"] if s.workspace == "personal" else [])       # each workspace is read with its own grant's credential name only
        self.assertTrue(health(rows, "jira", "bytalos")["Source health"].startswith("Unavailable: "))

    def test_no_description_or_body_text_is_copied_into_a_row(self):
        rows = rows_for()
        for r in rows:
            if r["Kind"] == "item" and r["Source"] != "Jira":                                    # only the Jira fixture puts the canary in the title, which is shown
                self.assertNotIn(F.CANARY, repr(r), r["Record ID"])

    def test_a_row_older_than_the_freshness_window_is_labeled_stale(self):
        result = build()
        later = view_rows.project_rows(result, F.NOW + FRESH_FOR + timedelta(minutes=1))
        self.assertEqual({r["Freshness"] for r in later if r["Kind"] == "item"}, {"stale"})
        self.assertEqual({r["Freshness"] for r in view_rows.project_rows(result, F.NOW) if r["Kind"] == "item"}, {"fresh"})


def thread(id_, status="open", checkpoint="2026-11-14T10:00:00", notes="checkpoint source-backed; evidence 1", owner="jim"):
    return {"id": id_, "url": f"https://example.com/followup/{id_}", "title": f"Outcome {id_}", "notes": notes, "owner": owner, "status": status, "next_checkpoint": checkpoint,
            "tz": "America/Chicago", "updated": "2026-11-14T13:30:00"}


def payload(threads, state="PASS", workspace="personal", account="jim-personal"):
    return {"component": "followup", "state": state, "workspace": workspace, "account": account, "generated_at": F.NOW.isoformat(), "last_accepted": F.NOW.isoformat(), "threads": threads}


def followup_source(body, workspace="personal"):
    return followup.FollowupSource(workspace, f"jim-{workspace}", "jim", open_reader=followup.payload_reader(lambda ref: body), now=F.NOW)


class FollowupRows(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(sources, "LIVE_READS_ENABLED", True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_followup_states_map_to_the_inbox_buckets_with_owner_cue_and_checkpoint_kind(self):
        body = payload([thread("a"), thread("b", status="waiting", checkpoint="2026-11-20T10:00:00", notes="checkpoint suggested; evidence 2", owner="external"),
                        thread("c", checkpoint="2026-12-01T10:00:00")])
        rows = by_id(rows_for([followup_source(body)]))
        a, b, c = (rows[f"followup:personal:jim-personal:{k}"] for k in "abc")
        self.assertEqual((a["Bucket"], a["Owner cue"], a["Checkpoint"]), ("Needs me", "You", "Source-backed"))            # due now: needs action today
        self.assertEqual((b["Bucket"], b["Owner cue"], b["Checkpoint"]), ("Waiting", "Them", "Suggested"))               # an external owner: waiting
        self.assertEqual((c["Bucket"], c["Owner cue"]), ("Upcoming", "You"))
        self.assertEqual(health(rows.values(), "followup", "personal")["Source health"], "OK")

    def test_review_needed_rows_are_separate_and_never_counted(self):
        review = [("personal", "jim-personal", {"id": "r1", "url": "https://example.com/followup/r1", "title": "Maybe a promise", "notes": "checkpoint suggested", "next_checkpoint": "2026-11-18"})]
        rows = rows_for([followup_source(payload([thread("a")]))], review=review)
        row = by_id(rows)["review:followup:personal:jim-personal:r1"]
        self.assertEqual((row["Kind"], row["Bucket"], row["Counted"], row["Checkpoint"]), ("review", "Review needed", False, "Suggested"))
        counts = view_rows.summary(rows)["personal"]
        self.assertEqual(sum(c["count"] or 0 for c in counts.values()), 1)                      # only the one active loop; the review row is not in any count
        self.assertNotIn("Review needed", counts)

    def test_a_degraded_payload_keeps_its_threads_visibly_degraded(self):
        rows = rows_for([followup_source(payload([thread("a")], state="DEGRADED"))])
        row = by_id(rows)["followup:personal:jim-personal:a"]
        self.assertTrue(row["Source health"].startswith("DEGRADED, last accepted "), row["Source health"])
        self.assertEqual(health(rows, "followup", "personal")["Health state"], "degraded")

    def test_followup_with_no_live_reader_says_not_provisioned_and_never_an_empty_list(self):
        patch = mock.patch.object(sources, "LIVE_READS_ENABLED", False)
        patch.start()
        self.addCleanup(patch.stop)
        rows = rows_for([followup.FollowupSource("personal", "jim-personal", "jim")])         # the default reader refuses
        self.assertEqual(health(rows, "followup", "personal")["Source health"], "Unavailable: not provisioned")
        self.assertFalse([r for r in rows if r["Kind"] == "item"])
        counts = view_rows.summary(rows)["personal"]
        self.assertTrue(all(c["count"] is None and c["complete"] is False for c in counts.values()))   # unknown, never an ungrounded 0

    def test_one_failed_followup_input_does_not_hide_five_healthy_sources(self):
        srcs = [s for s in F.all_sources() if s.source != "followup"] + [followup_source(None)]       # the payload is missing: the Followup read fails on its own
        rows = rows_for(srcs)
        self.assertEqual(health(rows, "followup", "personal")["Source health"], "Unavailable: not provisioned")
        self.assertEqual({r["Source"] for r in rows if r["Kind"] == "item" and r["Workspace"] == "Personal"}, {"Jira", "Todoist", "Concord", "Calendar", "Asana"})

    def test_counts_are_complete_only_when_every_expected_source_is_ok(self):
        full = view_rows.summary(rows_for(F.all_sources()))
        self.assertTrue(full["personal"]["Needs me"]["complete"])
        partial = view_rows.summary(rows_for([s for s in F.all_sources() if s.source == "jira"]))
        self.assertFalse(partial["personal"]["Waiting"]["complete"])
        self.assertIsNone(partial["personal"]["Waiting"]["count"])                               # nothing in Waiting is "unknown" while sources are missing
        self.assertEqual(partial["personal"]["Approvals"]["count"], 1)                           # a positive count is still shown, as at least this many


class Reconciliation(unittest.TestCase):
    def test_the_first_run_creates_every_row_and_a_second_run_plans_nothing(self):
        desired = rows_for()
        first = view_plan.plan((), desired)
        self.assertEqual({op for op, _, _ in first}, {"create"})
        self.assertEqual(len(first), len(desired))
        after = view_plan.apply_plan((), first)
        self.assertEqual(view_plan.plan(after, desired), ())
        self.assertEqual(view_plan.plan(view_plan.apply_plan(after, view_plan.plan(after, desired)), desired), ())

    def test_a_changed_source_status_updates_only_the_changed_properties(self):
        desired = rows_for()
        current = view_plan.apply_plan((), view_plan.plan((), desired))
        moved = [dict(r, **{"Source status": "Done"}) if r["Record ID"].startswith("jira:personal") else r for r in desired]
        ops = view_plan.plan(current, moved)
        self.assertEqual([(op, key, payload) for op, key, payload in ops], [("update", "jira:personal:jim-personal:LIFE-1", {"Source status": "Done"})])

    def test_the_plan_is_deterministic_and_ordered(self):
        desired = rows_for()
        self.assertEqual(view_plan.plan((), desired), view_plan.plan((), tuple(reversed(desired))))
        keys = [(op, key) for op, key, _ in view_plan.plan((), desired)]
        self.assertEqual(keys, sorted(keys))

    def test_a_vanished_record_is_archived_only_when_its_source_read_cleanly(self):
        current = view_plan.apply_plan((), view_plan.plan((), rows_for()))
        gone = "jira:personal:jim-personal:LIFE-1"
        clean = rows_for([F.FakeSource(s.source, s.workspace, rows=[] if (s.source, s.workspace) == ("jira", "personal") else None) for s in F.all_sources()])
        self.assertIn(("archive", gone, None), view_plan.plan(current, clean))                   # the read succeeded and the record is no longer there
        failed = rows_for([F.FakeSource(s.source, s.workspace, error=RuntimeError("x") if (s.source, s.workspace) == ("jira", "personal") else None) for s in F.all_sources()])
        self.assertNotIn(gone, [key for op, key, _ in view_plan.plan(current, failed) if op == "archive"])     # a failed read keeps the last accepted row
        missing = rows_for([s for s in F.all_sources() if s.source != "jira"])
        self.assertEqual([k for op, k, _ in view_plan.plan(current, missing) if op == "archive"], [])      # not provisioned: nothing archived either

    def test_an_archived_row_that_returns_is_restored_by_an_update(self):
        desired = rows_for()
        current = list(view_plan.apply_plan((), view_plan.plan((), desired)))
        current[0] = dict(current[0], Archived=True)
        restore = view_plan.plan(current, desired)
        self.assertEqual(restore[0], ("update", current[0]["Record ID"], {"Archived": False}))

    def test_a_health_row_is_never_archived(self):
        desired = rows_for()
        extra = {"Record ID": "health:jira:personal", "Kind": "health", "Source health": "OK"}
        gone = [r for r in desired if r["Record ID"] != "health:jira:personal"]
        current = view_plan.apply_plan((), view_plan.plan((), desired))
        self.assertEqual([k for op, k, _ in view_plan.plan(list(current) + [extra], gone) if op == "archive" and k.startswith("health:")], [])

    def test_a_row_that_is_not_ours_is_left_alone(self):
        stranger = {"Record ID": "something-else", "Kind": "item"}
        self.assertEqual(view_plan.plan([stranger], rows_for()), view_plan.plan((), rows_for()))

    def test_the_default_writer_refuses_and_nothing_is_written(self):
        ops = view_plan.plan((), rows_for())
        with self.assertRaises(InboxError) as caught:
            view_plan.execute(ops)
        self.assertEqual(caught.exception.reason, "notion_writes_not_authorized")
        self.assertEqual(view_plan.execute(()), 0)                                               # nothing to write: nothing refused
        seen = []
        self.assertEqual(view_plan.execute(ops, seen.append), len(ops))                          # an injected writer receives every operation in plan order
        self.assertEqual(seen, list(ops))

    def test_last_accepted_time_survives_a_failed_run_through_the_health_row(self):
        ok_rows = rows_for()
        current = view_plan.apply_plan((), view_plan.plan((), ok_rows))
        remembered = view_rows.last_ok_from(current)
        failed = rows_for([F.FakeSource(s.source, s.workspace, error=RuntimeError("x") if (s.source, s.workspace) == ("jira", "personal") else None) for s in F.all_sources()], last_ok=remembered)
        text = health(failed, "jira", "personal")["Source health"]
        self.assertEqual(text, "Unavailable: runtimeerror")
        degraded = rows_for([F.FakeSource(s.source, s.workspace, rows=[{"key": "BAD"}] if (s.source, s.workspace) == ("jira", "personal") else None) for s in F.all_sources()], last_ok=remembered)
        self.assertTrue(health(degraded, "jira", "personal")["Source health"].startswith("DEGRADED, last accepted "))


if __name__ == "__main__":
    unittest.main()
