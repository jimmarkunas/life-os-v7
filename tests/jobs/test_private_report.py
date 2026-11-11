import unittest
from contextlib import contextmanager
from unittest import mock

from lifeos.jobs import private_report as pr
from lifeos.platform import snapshot_store, stored_pages
from lifeos.platform.notion_client import NotionError
from tests.jobs.support import FakeClient, FakeStore, PARENT, connect









HEADER = ["company", "link"]
ROWS = [["ExampleCo", "https://example.com/1"], ["OtherCo", "https://example.com/2"]]


class Private(unittest.TestCase):
    def test_a_dry_run_counts_and_writes_nothing(self):
        client = FakeClient()
        self.assertEqual(pr.publish(client, "T", HEADER, ROWS, False), {"rows": 2, "page": "dry", "failed": 0, "why": {}})
        self.assertEqual(client.writes, [])

    def test_a_live_run_writes_one_page_under_the_ledgers_parent_and_reads_the_rows_back(self):
        client = FakeClient()
        counts = pr.publish(client, "T", HEADER, ROWS, True, links=(1,))
        self.assertEqual((counts["page"], counts["failed"]), ("new", 0))
        method, path, body = client.writes[0]
        self.assertEqual((method, path, body["parent"]), ("POST", "/pages", {"type": "page_id", "page_id": PARENT}))
        cells = body["children"][0]["table"]["children"][1]["table_row"]["cells"]
        self.assertEqual(cells[1][0]["text"]["link"]["url"], "https://example.com/1")

    def test_a_keyed_report_is_remembered_by_page_id_and_left_alone_next_time(self):
        with mock.patch.object(stored_pages, "STORE", FakeStore()) as store:
            first = FakeClient()
            self.assertEqual(pr.publish(first, "T", HEADER, ROWS, True, key="k", connect=connect)["page"], "new")
            self.assertEqual(store.rows["k"]["page_id"], "page1")
            second = FakeClient()
            self.assertEqual(pr.publish(second, "T", HEADER, ROWS, True, key="k", connect=connect)["page"], "present")
            self.assertEqual(second.writes, [])

    def test_a_remembered_page_that_was_archived_is_written_afresh(self):
        with mock.patch.object(stored_pages, "STORE", FakeStore()) as store:
            store.rows["k"] = {"page_id": "gone"}
            client = FakeClient(existing={"gone": {"archived": True}})
            self.assertEqual(pr.publish(client, "T", HEADER, ROWS, True, key="k", connect=connect)["page"], "new")
            self.assertEqual(store.rows["k"]["page_id"], "page1")

    def test_a_dry_run_with_a_key_reads_but_never_writes(self):
        with mock.patch.object(stored_pages, "STORE", FakeStore()) as store:
            client = FakeClient()
            self.assertEqual(pr.publish(client, "T", HEADER, ROWS, False, key="k", connect=connect)["page"], "dry")
            self.assertEqual((client.writes, store.rows), ([], {}))

    def test_an_unkeyed_dated_report_needs_no_lookup_and_no_store(self):
        client = FakeClient()
        self.assertEqual(pr.publish(client, "T dated", HEADER, ROWS, True)["page"], "new")

    def test_the_module_has_no_title_search(self):
        import inspect
        source = inspect.getsource(pr)
        self.assertNotIn('"/search"', source)

    def test_a_live_report_with_no_title_is_refused_before_anything_is_written(self):
        client = FakeClient()
        self.assertEqual(pr.publish(client, "  ", HEADER, ROWS, True)["why"], {"PRIVATE_REPORT_EMPTY_TITLE": 1})
        self.assertEqual(pr.publish(client, "", HEADER, ROWS, False)["page"], "dry")

    def test_a_notion_error_is_a_fixed_code(self):
        self.assertEqual(pr.publish(FakeClient(fail=True), "T", HEADER, ROWS, True)["why"], {"PRIVATE_REPORT_NOTION_FAILED": 1})

    def test_rows_that_do_not_read_back_are_reported(self):
        self.assertEqual(pr.publish(FakeClient(lose_rows=1), "T", HEADER, ROWS, True)["why"], {"PRIVATE_REPORT_READBACK_MISMATCH": 1})

    def test_long_reports_are_split_into_tables_each_with_the_header(self):
        blocks = pr.tables(HEADER, [["c", "l"]] * 200)
        self.assertEqual([len(b["table"]["children"]) for b in blocks], [91, 91, 21])


if __name__ == "__main__":
    unittest.main()


class SharedHelpers(unittest.TestCase):
    """LIFE-526: private_report no longer carries its own copy of the stored-page helpers; it uses lifeos/platform/stored_pages. Schema-neutral: the same table and key column as before."""

    def test_the_table_definition_is_the_one_private_report_always_used(self):
        original = snapshot_store.Store("v7_private_reports", key_column="report_key", key_type="VARCHAR(96)")      # exactly what private_report.STORE was before LIFE-526
        self.assertEqual((stored_pages.STORE.table, stored_pages.STORE.key_column, stored_pages.STORE.schema), (original.table, original.key_column, original.schema))

    def test_private_report_calls_the_shared_helpers_and_defines_none_of_its_own(self):
        self.assertFalse(hasattr(pr, "_existing") or hasattr(pr, "_remember") or hasattr(pr, "STORE"))
        self.assertIs(pr.stored_pages, stored_pages)

    def test_an_old_record_comes_back_as_the_same_page_id(self):
        class OldRow:
            def ensure(self, connection): pass
            def load(self, connection, key): return {"taken_at": "2026-11-01T00:00:00+00:00", "schema": 1, "page_id": "page-old"}

        class Conn:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Client:
            def call(self, method, path): return {"archived": False}
        with mock.patch.object(stored_pages, "STORE", OldRow()):
            self.assertEqual(stored_pages.find(Client(), Conn, "dash:key", True), "page-old")

