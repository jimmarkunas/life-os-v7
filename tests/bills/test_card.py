"""D116: the Bills card renders the saved snapshot into the one 'Bills: This Week' callout."""
import copy
import inspect
import pathlib
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.bills import card, paid, snapshot
from lifeos.platform import router
from tests.bills.test_bills import page
from tests.kit.db import BillsSnapshotDB
from tests.kit.notion import BillsRegions

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 4, 12, 30, tzinfo=TZ)
ENV = {"BILLS_CARD_BLOCK_ID": "bills-callout", "CALENDAR_CARD_BLOCK_ID": "calendar-callout", "JIRA_CARD_BLOCK_ID": "jira-callout"}


def row(name, due, next_due, *, paid=False, status="Active", cycle="Monthly", amount=30.0):
    return {"page_id": f"p-{name}", "Name": name, "Status": status, "Cycle": cycle, "Paid": paid, "Due Date": due, "Next Due": next_due,
            "Last Paid": None, "Costs per Cycle": amount, "Last Observed Amount": amount}


# The four rows left in the live Bill Tracker on 2026-10-04: never marked Paid, so legitimately overdue, plus two current bills.
STALE_FOUR = [row("Best Buy Card", "2026-09-09", "2026-10-09"), row("Google Store", "2026-09-10", "2026-10-10"),
              row("IKEA Project Card", "2026-09-11", "2026-10-11", amount=None), row("HEB Visa", "2026-09-13", "2026-10-13")]


def saved(rows, taken="2026-10-04T12:10:00-05:00"):
    return {"schema": snapshot.SCHEMA_V, "taken_at": taken, "timezone": "America/Chicago", "rows": rows}


def lines(blocks):
    return [card._plain(b) for b in blocks]


class RenderTests(unittest.TestCase):
    def test_window_is_today_through_today_plus_seven_from_the_chicago_date_not_september(self):
        """Jim's rule: the same boundary as the Bills view's native 'one week from now' filter. For 2026-10-04 that is Oct 4 through Oct 11."""
        rows = [row("Today Bill", "2026-10-04", "2026-10-04"), row("Oct 10 Bill", "2026-10-10", "2026-10-10"), row("Oct 11 Bill", "2026-10-11", "2026-10-11"),
                row("Oct 12 Bill", "2026-10-12", "2026-10-12")]
        overdue, due = card._window(rows, NOW.date())
        self.assertEqual(sorted(r["Name"] for r in due), ["Oct 10 Bill", "Oct 11 Bill", "Today Bill"])
        self.assertNotIn("Oct 12 Bill", [r["Name"] for r in due])
        self.assertEqual(overdue, [])
        blocks, counts = card.render(saved(rows), now=NOW)
        self.assertIn("3 due through one week from now", lines(blocks)[0])
        self.assertEqual(counts["due_window"], 3)
        self.assertNotIn("due_7d", counts)                                                     # the card's metric no longer claims seven days
        later_overdue, later_due = card._window(rows, date(2026, 12, 20))                      # another day: the window moves, nothing is stored
        self.assertEqual(later_due, [])
        self.assertEqual(len(later_overdue), 4)

    def test_the_summary_never_says_a_number_of_days_and_counts_overdue_once(self):
        rows = STALE_FOUR + [row("Oct 11 Bill", "2026-10-11", "2026-10-11")]
        head = lines(card.render(saved(rows), now=NOW)[0])[0]
        self.assertEqual(head, "Updated 12:10 PM CT · 1 due through one week from now · $30 known · 4 overdue")
        self.assertNotRegex(head, r"next \d+ days")

    def test_the_four_unpaid_september_rows_stay_overdue_and_are_not_advanced(self):
        before = copy.deepcopy(STALE_FOUR)
        overdue, _ = card._window(STALE_FOUR, NOW.date())
        self.assertEqual([r["Name"] for r in overdue], ["Best Buy Card", "Google Store", "IKEA Project Card", "HEB Visa"])
        blocks, counts = card.render(saved(STALE_FOUR), now=NOW)
        self.assertIn("4 overdue", lines(blocks)[0])
        self.assertEqual(counts["overdue"], 4)
        self.assertEqual(STALE_FOUR, before)                                                   # the renderer never touches the rows

    def test_the_callout_text_is_one_summary_line_the_table_carries_the_rows(self):
        blocks, _ = card.render(saved(STALE_FOUR), now=NOW)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "paragraph")
        self.assertNotIn("Best Buy", lines(blocks)[0])

    def test_fresh_snapshot_shows_updated_time_and_the_current_window(self):
        rows = STALE_FOUR + [row("Water", "2026-10-06", "2026-10-06", amount=45.0), row("Power", "2026-10-08", "2026-10-08", amount=30.0)]
        blocks, counts = card.render(saved(rows), now=NOW)
        head = lines(blocks)[0]
        self.assertTrue(head.startswith("Updated 12:10 PM CT"))
        self.assertIn("2 due through one week from now", head)
        self.assertIn("$75 known", head)
        self.assertIn("4 overdue", head)
        self.assertEqual(counts["status"], "fresh")

    def test_stale_snapshot_is_labelled_stale_with_its_own_timestamp_never_current(self):
        blocks, counts = card.render(saved(STALE_FOUR, "2026-10-04T05:00:00-05:00"), stale=True, now=NOW)
        head = lines(blocks)[0]
        self.assertTrue(head.startswith("STALE · last accepted Oct 4 5:00 AM CT"))
        self.assertNotIn("Updated", head)
        self.assertEqual(counts["status"], "stale")

    def test_missing_or_incomplete_snapshot_fails_closed(self):
        for bad in (None, {}, saved([]), {"schema": 2, "taken_at": "2026-10-04T12:00:00-05:00", "rows": [row("A", None, None)]},
                    saved([row("A", None, None)], taken="garbage"), saved([{"Name": "A"}]), saved([dict(row("A", None, None), Paid="yes")])):
            with self.assertRaises(card.CardError, msg=str(bad)) as caught:
                card.render(bad, now=NOW)
            self.assertEqual(str(caught.exception), "BILLS_CARD_SNAPSHOT_INVALID")
        notion = BillsRegions()
        with self.assertRaises(card.CardError) as caught:
            card.run(0, True, environ=ENV, client=notion, now=NOW, connect=lambda: BillsSnapshotDB())
        self.assertEqual(str(caught.exception), "BILLS_CARD_SNAPSHOT_MISSING")
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE", "PATCH")], [])

    def test_missing_amount_is_said_never_zero(self):
        due, counts = card.render(saved([row("No Amount", "2026-10-05", "2026-10-05", amount=None)]), now=NOW)
        self.assertIn("1 amount missing", lines(due)[0])
        self.assertIn("$0 known", lines(due)[0])
        self.assertEqual(counts["amount_missing"], 1)
        self.assertIsNone(card._amount(row("X", None, None, amount=None)))

    def test_paid_overdue_and_unsupported_rows_are_not_counted(self):
        rows = [row("Paid Row", "2026-09-01", "2026-10-05", paid=True), row("Lifetime", "2026-09-01", "2026-10-05", cycle="Lifetime"),
                row("Inactive", "2026-09-01", "2026-10-05", status="Inactive"), row("Real", "2026-10-05", "2026-10-05")]
        overdue, due = card._window(rows, NOW.date())
        self.assertEqual((overdue, [r["Name"] for r in due]), ([], ["Real"]))


class WriteTests(unittest.TestCase):
    def run_card(self, notion, rows=STALE_FOUR, **kwargs):
        return card.run(0, kwargs.pop("live", True), environ=ENV, client=notion, now=NOW, connect=lambda: BillsSnapshotDB(saved(rows)), **kwargs)

    def test_live_write_replaces_only_the_text_and_leaves_the_interactive_table_in_place(self):
        notion = BillsRegions()
        calendar_before, jira_before, dcc_before = (notion.full_tree(k) for k in ("calendar-callout", "jira-callout", "dcc-callout"))
        view_before = dict(next(k for k in notion.children["bills-callout"] if k["id"] == "bills-view-old"))
        counts = self.run_card(notion)
        kids = notion.children["bills-callout"]
        self.assertEqual([k["id"] for k in kids][0], "heading-bills-callout")                 # the heading block itself is kept
        self.assertEqual(card._plain(kids[0]), "Bills: This Week")
        self.assertTrue(card._plain(kids[1]).startswith("Updated 12:10 PM CT"))                  # the summary sits right under the heading
        self.assertEqual(kids[2]["id"], "bills-view-old")                                     # the Bills table is still there, below it, untouched
        self.assertEqual(kids[2], view_before)
        self.assertNotIn("last accepted 9/14", "\n".join(lines(kids)))
        self.assertEqual((counts["blocks_written"], counts["removed"]), (1, 1))
        self.assertEqual((notion.full_tree("calendar-callout"), notion.full_tree("jira-callout"), notion.full_tree("dcc-callout")),
                         (calendar_before, jira_before, dcc_before))
        ops = [m for m, _ in notion.log]
        self.assertLess(ops.index("APPEND"), ops.index("DELETE"))                             # new text first, so a failed write leaves the old text
        self.assertFalse(any(p == f"/blocks/{notion.page_id}/children" for _, p in notion.log))

    def test_nothing_but_the_old_status_text_is_deleted(self):
        notion = BillsRegions()
        self.run_card(notion)
        deleted = {p.split("/")[2] for m, p in notion.log if m == "DELETE"}
        self.assertEqual(deleted, {"bills-status-old"})
        touched = {p.split("/")[2] for m, p in notion.log if m in ("APPEND", "DELETE")}
        self.assertLessEqual(touched, {"bills-callout", "bills-status-old"})

    def test_dry_run_writes_nothing_and_missing_config_is_not_an_error(self):
        notion = BillsRegions()
        counts = self.run_card(notion, live=False)
        self.assertEqual(counts["status"], "fresh")
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(card.run(0, True, environ={}, client=notion, connect=lambda: BillsSnapshotDB(saved(STALE_FOUR)))["status"], "not_configured")

    def test_stale_snapshot_is_written_as_stale(self):
        notion = BillsRegions()
        card.run(0, True, environ=ENV, client=notion, now=NOW, connect=lambda: BillsSnapshotDB(saved(STALE_FOUR, "2026-10-04T05:00:00-05:00")))
        self.assertTrue(card._plain(notion.children["bills-callout"][1]).startswith("STALE · last accepted Oct 4 5:00 AM CT"))

    def test_tracker_link_is_preserved_and_wrong_target_refuses_before_writing(self):
        notion = BillsRegions()
        self.run_card(notion)
        self.assertEqual(card._plain(notion.children["bills-callout"][1]).count("Bill Tracker"), 1)
        for env in ({**ENV, "BILLS_CARD_BLOCK_ID": "calendar-callout"}, {**ENV, "BILLS_CARD_BLOCK_ID": "jira-callout"}):
            fresh = BillsRegions()
            with self.assertRaises(card.CardError) as caught:
                card.run(0, True, environ=env, client=fresh, now=NOW, connect=lambda: BillsSnapshotDB(saved(STALE_FOUR)))
            self.assertEqual(str(caught.exception), "BILLS_CARD_NOT_OWNED")
            self.assertEqual([m for m, _ in fresh.log if m in ("APPEND", "DELETE")], [])

    def test_a_database_view_a_database_or_a_page_in_the_callout_is_never_deleted_or_moved(self):
        for extra in ({"id": "real-db", "type": "child_database", "has_children": False, "child_database": {"title": "Bill Tracker"}},
                      {"id": "a-page", "type": "child_page", "has_children": False, "child_page": {"title": "Notes"}}):
            notion = BillsRegions(extra_kid=extra)
            self.run_card(notion)
            kept = [k["id"] for k in notion.children["bills-callout"][2:]]
            self.assertEqual(kept, ["bills-view-old", extra["id"]])
            self.assertNotIn(extra["id"], {p.split("/")[2] for m, p in notion.log if m == "DELETE"})

    def test_a_protected_region_changed_mid_write_fails_the_run(self):
        for op in ("APPEND", "DELETE"):
            for region in ("calendar-callout", "jira-callout"):
                notion = BillsRegions(change_on=op, change_region=region)
                with self.assertRaises(card.CardError) as caught:
                    self.run_card(notion)
                self.assertEqual(str(caught.exception), "BILLS_PROTECTED_REGION_CHANGED")

    def test_missing_protected_ids_refuse_a_live_write(self):
        for missing in ("CALENDAR_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID"):
            notion = BillsRegions()
            env = {k: v for k, v in ENV.items() if k != missing}
            with self.assertRaises(card.CardError) as caught:
                card.run(0, True, environ=env, client=notion, now=NOW, connect=lambda: BillsSnapshotDB(saved(STALE_FOUR)))
            self.assertEqual(str(caught.exception), "BILLS_PROTECTED_REGION_UNAVAILABLE")
            self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], [])


class RuntimePathTests(unittest.TestCase):
    """PRE_MERGE_RUNTIME_GATE: the production entry points actually reach the card, after the snapshot, and nothing else changed."""

    def test_the_cli_stage_points_at_the_card_and_the_whole_path_runs_from_the_saved_snapshot(self):
        from lifeos import run as runner
        self.assertEqual(runner.STAGES["bills-card"].target, ("lifeos.bills.card", "run"))
        notion = BillsRegions()
        database = BillsSnapshotDB(saved(STALE_FOUR))
        counts = card.run(0, True, environ=ENV, client=notion, now=NOW, connect=lambda: database)      # saved snapshot -> exact region -> read-back -> protected regions
        self.assertEqual(counts["status"], "fresh")
        self.assertEqual([c for c in database.sql if c.startswith(("INSERT", "UPDATE", "DELETE"))], [])  # the card never writes the snapshot either

    def test_the_bills_job_runs_paid_then_snapshot_then_card_and_waits_for_the_other_page_writers(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        doc = yaml.safe_load((pathlib.Path(card.__file__).resolve().parents[2] / ".github/workflows/domains.yml").read_text())
        job = doc["jobs"]["bills"]
        runs = [step.get("run", "") for step in job["steps"]]
        order = [next(i for i, r in enumerate(runs) if f"lifeos.run {stage}" in r) for stage in ("bills-paid", "bills-snapshot", "bills-card")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(set(job["needs"]), {"jira", "agenda"})
        self.assertTrue(next(s for s in job["steps"] if s.get("id") == "bcard")["continue-on-error"])
        self.assertNotIn("schedule", doc[True])                                                   # no new scheduler: the job still rides the tick's workflow_run


class OwnershipAndSourceTests(unittest.TestCase):
    def test_router_gives_the_region_one_owner_and_other_modules_cannot_write_it(self):
        self.assertEqual(router.BILLS_REGION, card.CARD_TITLE)
        self.assertEqual(router.OWNERS[router.BILLS_REGION], "v7-bills")
        self.assertEqual(len(set(router.OWNERS.values())), len(router.OWNERS))
        with self.assertRaises(router.RouterError):
            router.check_write("v7-calendar", [router.BILLS_REGION])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-bills", [router.CALENDAR_REGION])
        router.check_write("v7-bills", [router.BILLS_REGION])

    def test_no_production_code_contains_a_fixed_week_or_the_september_dates(self):
        root = pathlib.Path(card.__file__).resolve().parents[1]
        text = (root / "bills" / "card.py").read_text() + (root / "bills" / "state.py").read_text()
        for needle in ("2026-09", "09-07", "09-13", "Sep 7", "Sep 13", "2026-10"):
            self.assertNotIn(needle, text)
        self.assertNotIn("date(20", text)

    def test_the_card_never_writes_the_bill_tracker_or_advances_a_due_date(self):
        source = inspect.getsource(card)
        for banned in ("update_page_properties", "query_data_source", "NOTION_BILLS_TOKEN", "Due Date\":", "Last Paid\":"):
            self.assertNotIn(banned, source)

    def test_paid_processing_still_belongs_to_paid_py(self):
        self.assertTrue(hasattr(paid, "run"))
        self.assertNotIn("card", inspect.getsource(paid).split("import")[1][:200])


if __name__ == "__main__":
    unittest.main()
