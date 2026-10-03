import json
import unittest
from datetime import date, datetime, timezone

from lifeos.bills import paid, snapshot, state
from lifeos.platform import notion_client
from tests.kit.db import BillsPaidDB, BillsSnapshotDB
from tests.kit.notion import DataSourcePages, PaidBillTracker


TODAY = date(2026, 4, 2)


def page(name="Example Cable", paid=False, status="Active", cycle="Monthly", due="2026-04-01", next_due="2026-04-02"):
    return {"id": "synthetic-page", "properties": {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Status": {"type": "status", "status": {"name": status}},
        "Cycle": {"type": "select", "select": {"name": cycle} if cycle is not None else None},
        "Paid": {"type": "checkbox", "checkbox": paid},
        "Due Date": {"type": "date", "date": {"start": due} if due else None},
        "Next Due": {"type": "formula", "formula": {"type": "date", "date": {"start": next_due} if next_due else None}},
        "Last Paid": {"type": "date", "date": None},
        "Costs per Cycle": {"type": "number", "number": 12.34},
        "Last Observed Amount": {"type": "number", "number": 12.34},
    }}


def payload_rows(rows):
    return {"schema": snapshot.SCHEMA_V, "taken_at": "2026-04-02T00:00:00-05:00", "timezone": "America/Chicago", "rows": rows}


class BillSnapshotTests(unittest.TestCase):
    def test_paginates_and_reads_evaluated_formula_next_due(self):
        first = page()
        second = page(name="Example Systems")
        client = DataSourcePages({"results": [first], "has_more": True, "next_cursor": "cursor-1"},
                                 {"results": [second], "has_more": False})
        rows = snapshot.query_all(client)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Next Due"], "2026-04-02")
        self.assertEqual(client.requests[1][1]["start_cursor"], "cursor-1")

    def test_incomplete_page_raises_and_previous_snapshot_survives(self):
        old = payload_rows([{"page_id": "old-private-row", "Name": "Example Cable"}])
        database = BillsSnapshotDB(old)
        client = DataSourcePages({"results": [page()], "has_more": True, "next_cursor": None})
        with self.assertRaises(snapshot.BillsError) as caught:
            snapshot.run(1, True, client=client, now=datetime(2026, 4, 2, tzinfo=timezone.utc), connect=lambda: database)
        self.assertEqual(str(caught.exception), "BILLS_PAGE_INCOMPLETE")
        self.assertEqual(json.loads(database.payload), old)
        self.assertEqual(database.sql, [])

    def test_errors_never_include_private_property_content(self):
        bad = page(name="Private Synthetic Name")
        del bad["properties"]["Paid"]
        with self.assertRaises(snapshot.BillsError) as caught:
            snapshot.compact(bad)
        self.assertEqual(str(caught.exception), "BILLS_PROPERTY_MISSING")
        self.assertNotIn("Private Synthetic Name", str(caught.exception))

    def test_unexpected_read_error_is_replaced_with_a_fixed_code(self):
        client = DataSourcePages(RuntimeError("Private Synthetic Name 12.34"))
        with self.assertRaises(snapshot.BillsError) as caught:
            snapshot.query_all(client)
        self.assertEqual(str(caught.exception), "BILLS_READ_FAILED")

    def test_live_save_reads_back_private_snapshot_and_dry_run_only_returns_counts(self):
        client = DataSourcePages({"results": [page()], "has_more": False})
        database = BillsSnapshotDB()
        counts = snapshot.run(1, True, client=client,
                              now=datetime(2026, 4, 3, 0, 30, tzinfo=timezone.utc), connect=lambda: database)
        self.assertEqual(counts, {"rows": 1, "active": 1, "paid": 0, "stale_due": 1,
                                  "due_today": 1, "due_7d": 0, "saved": 1})
        self.assertEqual(snapshot.load(database)["rows"][0]["Next Due"], "2026-04-02")
        dry_counts = snapshot.run(1, False, client=DataSourcePages({"results": [page()], "has_more": False}),
                                  now=datetime(2026, 4, 2, tzinfo=timezone.utc))
        self.assertEqual(set(dry_counts), {"rows", "active", "paid", "stale_due", "due_today", "due_7d", "saved"})
        self.assertNotIn("Example Cable", repr(dry_counts))
        self.assertNotIn("12.34", repr(dry_counts))


class BillStateTests(unittest.TestCase):
    def test_stale_due_definition_and_every_exclusion(self):
        overdue = {"Status": "Active", "Paid": False, "Cycle": "Monthly", "Due Date": "2026-04-01"}
        excluded = [
            {**overdue, "Paid": True}, {**overdue, "Status": "Inactive"},
            {**overdue, "Cycle": "Lifetime"}, {**overdue, "Cycle": "One Time"},
            {**overdue, "Cycle": None}, {**overdue, "Due Date": None}, {**overdue, "Cycle": "4 Years"}, {**overdue, "Cycle": "Not A Cycle"},
            {**overdue, "Due Date": "2026-04-02"},
        ]
        buckets = state.classify([overdue, *excluded], TODAY)
        self.assertEqual(buckets["stale_due"], [overdue])
        self.assertEqual(len(buckets["paid"]), 1)

    def test_due_today_and_next_seven_days_use_chicago_date(self):
        today = {"Status": "Active", "Paid": False, "Cycle": "Monthly", "Due Date": None, "Next Due": "2026-04-02"}
        seventh = {**today, "Next Due": "2026-04-09"}
        eighth = {**today, "Next Due": "2026-04-10"}
        buckets = state.classify([today, seventh, eighth], TODAY)
        self.assertEqual(buckets["due_today"], [today])
        self.assertEqual(buckets["due_next_7_days"], [seventh])

    def test_current_reads_only_saved_snapshot(self):
        rows = [{"Status": "Active", "Paid": False, "Cycle": "Monthly", "Due Date": "2026-04-01",
                 "Next Due": "2026-04-02"}]
        database = BillsSnapshotDB(payload_rows(rows))
        self.assertEqual(state.current(database, TODAY)["due_today"], rows)


class NotionClientBillsTests(unittest.TestCase):
    def test_separate_credentials_and_query_helper_use_common_call_path(self):
        client = notion_client.Client({"NOTION_BILLS_TOKEN": "synthetic-token", "NOTION_BILLS_DATA_SOURCE_ID": "abc-123"},
                                      token_name="NOTION_BILLS_TOKEN", source_name="NOTION_BILLS_DATA_SOURCE_ID")
        calls = []
        client.call = lambda method, path, body=None: calls.append((method, path, body)) or {"results": [], "has_more": False}
        client.query_data_source(body={"page_size": 100})
        self.assertEqual(calls, [("POST", "/data_sources/abc-123/query", {"page_size": 100})])


if __name__ == "__main__":
    unittest.main()


class DisplayFieldTests(unittest.TestCase):
    def page(self, **extra):
        props = {"Name": {"type": "title", "title": [{"plain_text": "Example Bill"}]},
                 "Status": {"type": "select", "select": {"name": "Active"}},
                 "Cycle": {"type": "select", "select": {"name": "Monthly"}},
                 "Paid": {"type": "checkbox", "checkbox": False},
                 "Due Date": {"type": "date", "date": {"start": "2026-10-01"}},
                 "Next Due": {"type": "formula", "formula": {"type": "date", "date": {"start": "2026-11-01"}}}}
        props.update(extra)
        return {"id": "p1", "properties": props}

    def test_an_unfamiliar_display_field_shape_is_none_not_a_failed_snapshot(self):
        row = snapshot.compact(self.page(**{"Costs per Cycle": {"type": "rollup", "rollup": {}}}))
        self.assertIsNone(row["Costs per Cycle"])
        self.assertIsNone(row["Last Paid"])                                   # missing display property

    def test_an_unfamiliar_critical_field_still_fails_closed(self):
        with self.assertRaises(snapshot.BillsError):
            snapshot.compact(self.page(Cycle={"type": "rollup", "rollup": {}}))


def paid_page(*, page_id="11111111-1111-4111-8111-111111111111", cycle="Monthly", paid=True,
              status="Active", next_due="2026-10-10"):
    return {"id": page_id, "parent": {"type": "data_source_id", "data_source_id": PaidBillTracker.SOURCE},
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Example Systems"}]},
                "Status": {"type": "select", "select": {"name": status} if status else None},
                "Cycle": {"type": "select", "select": {"name": cycle} if cycle else None},
                "Paid": {"type": "checkbox", "checkbox": paid},
                "Due Date": {"type": "date", "date": {"start": "2026-10-01"}},
                "Next Due": {"type": "formula", "formula": {"type": "date",
                                                              "date": {"start": next_due} if next_due else None}},
                "Last Paid": {"type": "date", "date": None},
                "Costs per Cycle": {"type": "number", "number": 12.34},
                "Last Observed Amount": {"type": "number", "number": 12.34},
            }}


class PaidProcessorTests(unittest.TestCase):
    def setUp(self):
        from zoneinfo import ZoneInfo
        self.now = datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("America/Chicago"))

    def run_paid(self, client, database=None, *, live=True, limit=40):
        database = database or BillsPaidDB(client.event_log)
        counts = paid.run(limit, live, client=client, now=self.now, connect=lambda: database)
        return counts, database

    def test_recurring_paid_uses_captured_formula_date_and_clears_last(self):
        page = paid_page()
        events = []
        client = PaidBillTracker(page, event_log=events)
        counts, database = self.run_paid(client)
        props = client.pages[page["id"]]["properties"]
        self.assertEqual(counts["advanced"], 1)
        self.assertEqual(props["Due Date"]["date"]["start"], "2026-10-10")
        self.assertEqual(props["Last Paid"]["date"]["start"], "2026-10-03")
        self.assertGreater(props["Next Due"]["formula"]["date"]["start"], "2026-10-10")
        self.assertFalse(props["Paid"]["checkbox"])
        self.assertEqual(client.writes, [("Last Paid", "Due Date"), ("Paid",)])
        first_update = next(i for i, event in enumerate(events) if event[0] == "update")
        self.assertLess(next(i for i, event in enumerate(events) if event == ("ledger", "prepared")), first_update)
        self.assertEqual(json.loads(database.rows[page["id"]])["step"], "complete")

    def test_one_time_sets_inactive_without_advancing_due(self):
        page = paid_page(cycle="One Time")
        client = PaidBillTracker(page)
        counts, _ = self.run_paid(client)
        props = client.pages[page["id"]]["properties"]
        self.assertEqual(counts["one_time"], 1)
        self.assertEqual(props["Status"]["select"]["name"], "Inactive")
        self.assertEqual(props["Due Date"]["date"]["start"], "2026-10-01")
        self.assertEqual(props["Last Paid"]["date"]["start"], "2026-10-03")
        self.assertFalse(props["Paid"]["checkbox"])
        self.assertEqual(client.writes, [("Last Paid", "Status"), ("Paid",)])

    def test_unsupported_or_unknown_cycles_and_missing_next_due_stay_checked(self):
        pages = [paid_page(page_id=f"00000000-0000-4000-8000-{i:012d}", cycle=cycle)
                 for i, cycle in enumerate(("Lifetime", "4 Years", None), 1)]
        pages.append(paid_page(page_id="00000000-0000-4000-8000-000000000004", next_due=None))
        client = PaidBillTracker(*pages)
        counts, _ = self.run_paid(client)
        self.assertEqual(counts["review"], 4)
        self.assertEqual(client.writes, [])
        self.assertTrue(all(p["properties"]["Paid"]["checkbox"] for p in client.pages.values()))

    def test_dry_run_and_snapshot_never_update_notion_or_create_ledger_rows(self):
        page = paid_page()
        client = PaidBillTracker(page)
        counts = paid.run(40, False, client=client, now=self.now,
                          connect=lambda: self.fail("dry run must not open the ledger"))
        self.assertEqual(counts["writes"], 0)
        self.assertEqual(client.writes, [])
        self.assertEqual(BillsPaidDB().rows, {})
        snapshot.run(40, True, client=client, now=self.now, connect=BillsSnapshotDB)
        self.assertEqual(client.writes, [])

    def test_incomplete_listing_and_duplicate_ids_fail_before_writes(self):
        page = paid_page()
        client = PaidBillTracker(page, responses=[{"results": [page], "has_more": True, "next_cursor": None}])
        with self.assertRaises(paid.PaidError) as caught:
            paid.run(40, True, client=client, now=self.now, connect=BillsPaidDB)
        self.assertEqual(str(caught.exception), "BILLS_PAGE_INCOMPLETE")
        self.assertEqual(client.writes, [])
        client = PaidBillTracker(page, responses=[{"results": [page, page], "has_more": False}])
        with self.assertRaises(paid.PaidError) as caught:
            paid.run(40, True, client=client, now=self.now, connect=BillsPaidDB)
        self.assertEqual(str(caught.exception), "BILLS_DUPLICATE_PAGE_ID")
        self.assertEqual(client.writes, [])

    def test_complete_queue_paginates_before_any_row_write(self):
        first, second = paid_page(), paid_page(page_id="22222222-2222-4222-8222-222222222222")
        client = PaidBillTracker(first, second, responses=[
            {"results": [first], "has_more": True, "next_cursor": "cursor-example"},
            {"results": [second], "has_more": False},
        ])
        counts = paid.run(1, False, client=client, now=self.now)
        self.assertEqual(counts["paid_rows"], 2)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(client.requests[1][1]["start_cursor"], "cursor-example")
        self.assertEqual(client.writes, [])

    def test_one_malformed_row_is_skipped_and_counted_and_the_rest_still_process(self):
        good = paid_page()
        odd = paid_page(page_id="33333333-3333-4333-8333-333333333333")
        del odd["properties"]["Status"]
        client = PaidBillTracker(good, odd)
        counts, _ = self.run_paid(client)
        self.assertEqual((counts["invalid"], counts["advanced"]), (1, 1))
        self.assertFalse(client.pages[good["id"]]["properties"]["Paid"]["checkbox"])
        self.assertTrue(client.pages[odd["id"]]["properties"]["Paid"]["checkbox"])

    def test_wrong_source_and_incomplete_error_are_fixed_codes(self):
        page = paid_page()
        page["parent"]["data_source_id"] = "22222222-2222-4222-8222-222222222222"
        client = PaidBillTracker(page)
        with self.assertRaises(paid.PaidError) as caught:
            paid.run(40, True, client=client, now=self.now, connect=BillsPaidDB)
        self.assertEqual(str(caught.exception), "BILLS_SOURCE_MISMATCH")
        self.assertNotIn("Example Systems", str(caught.exception))

    def test_formula_readback_mismatch_and_write_failure_leave_paid_checked(self):
        page = paid_page()
        client = PaidBillTracker(page, recalculate_formula=False)
        counts, _ = self.run_paid(client)
        self.assertEqual(counts["failed"], 1)
        self.assertTrue(client.pages[page["id"]]["properties"]["Paid"]["checkbox"])
        page = paid_page()
        client = PaidBillTracker(page)
        def fail_write(*_args, **_kwargs):
            raise RuntimeError("Example Systems 12.34")
        client.update_page_properties = fail_write
        counts, _ = self.run_paid(client)
        self.assertEqual(counts["failed"], 1)
        self.assertTrue(client.pages[page["id"]]["properties"]["Paid"]["checkbox"])
        self.assertNotIn("Example Systems", repr(counts))
        self.assertNotIn("12.34", repr(counts))

    def test_crash_after_steps_three_four_and_five_resumes_without_advancing_twice(self):
        scenarios = (("after_step_3", None, True), ("after_step_4", "verified", False),
                     ("after_step_5", "complete", False))
        for label, fail_step, fail_read in scenarios:
            with self.subTest(step=label):
                page = paid_page()
                client = PaidBillTracker(page, fail_read_after_update=fail_read)
                database = BillsPaidDB(client.event_log, fail_step=fail_step)
                if fail_read:
                    counts, _ = self.run_paid(client, database)
                    self.assertEqual(counts["failed"], 1)
                else:
                    counts = paid.run(40, True, client=client, now=self.now, connect=lambda: database)
                    self.assertEqual(counts["failed"], 1)
                due_after_crash = client.pages[page["id"]]["properties"]["Due Date"]["date"]["start"]
                self.assertEqual(due_after_crash, "2026-10-10")
                counts = paid.run(40, True, client=client, now=self.now, connect=lambda: database)
                props = client.pages[page["id"]]["properties"]
                self.assertEqual(props["Due Date"]["date"]["start"], "2026-10-10")
                self.assertFalse(props["Paid"]["checkbox"])
                self.assertEqual(json.loads(database.rows[page["id"]])["step"], "complete")
                self.assertEqual(counts["failed"], 0)
                # A third pass is a no-op after acknowledgement.
                writes = len(client.writes)
                paid.run(40, True, client=client, now=self.now, connect=lambda: database)
                self.assertEqual(len(client.writes), writes)

    def test_per_run_cap_leaves_extra_commands_checked(self):
        pages = [paid_page(page_id=f"00000000-0000-4000-8000-{i:012d}") for i in range(1, 23)]
        client = PaidBillTracker(*pages)
        counts = paid.run(40, False, client=client, now=self.now)
        self.assertEqual(counts["paid_rows"], 22)
        self.assertEqual(counts["skipped"], 2)
        self.assertEqual(counts["advanced"], paid.MAX_LIMIT)
        self.assertEqual(client.writes, [])

    def test_unexpected_api_error_is_redacted(self):
        page = paid_page()
        client = PaidBillTracker(page, responses=[RuntimeError("Example Systems 12.34")])
        with self.assertRaises(paid.PaidError) as caught:
            paid.run(40, True, client=client, now=self.now, connect=BillsPaidDB)
        self.assertEqual(str(caught.exception), "BILLS_READ_FAILED")
        self.assertNotIn("Example Systems", str(caught.exception))
        self.assertNotIn("12.34", str(caught.exception))

    def test_page_update_helper_uses_single_non_retried_patch_and_only_requested_property(self):
        client = notion_client.Client({"NOTION_BILLS_TOKEN": "synthetic-token",
                                       "NOTION_BILLS_DATA_SOURCE_ID": PaidBillTracker.SOURCE},
                                      token_name="NOTION_BILLS_TOKEN", source_name="NOTION_BILLS_DATA_SOURCE_ID")
        calls = []
        client.call_once = lambda method, path, body=None: calls.append((method, path, body)) or {}
        client.update_page_properties("11111111-1111-4111-8111-111111111111", {"Paid": {"checkbox": False}})
        self.assertEqual(calls, [("PATCH", "/pages/11111111111141118111111111111111",
                                  {"properties": {"Paid": {"checkbox": False}}})])
