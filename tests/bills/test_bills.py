import json
import unittest
from datetime import date, datetime, timezone

from lifeos.bills import snapshot, state
from lifeos.platform import notion_client
from tests.kit.db import BillsSnapshotDB
from tests.kit.notion import DataSourcePages


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
