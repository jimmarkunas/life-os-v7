import datetime as dt
import unittest
from unittest import mock

from lifeos.jobs import retention
from tests.kit.db import FakeConn
from tests.kit.notion import LedgerWithFilterLimit, filter_depth

NOW = dt.datetime(2026, 10, 11, 3, 10)


def page(page_id, posted=None, first=None):
    date = lambda value: {"date": {"start": value}} if value else {"date": None}
    return {"id": page_id, "properties": {"Posting Date": date(posted), "First Surfaced": date(first)}}


def database(rows):
    """store.connect() stand-in: the lane query returns one lane, the per-page lookup returns the stored job row."""
    def handler(sql, args, cursor):
        if sql.startswith("SELECT DISTINCT lane"):
            cursor.fetchall = lambda: [("US Remote",)]
        elif sql.startswith("SELECT id, dedupe_key"):
            cursor.row = rows.get(args[0])
    return mock.patch.object(retention.store, "connect", lambda: FakeConn(handler=handler))


class FilterShape(unittest.TestCase):
    def test_the_weekly_query_filter_is_no_deeper_than_notion_allows(self):
        # Notion refuses a compound filter nested past two levels with HTTP 400. This is what failed every Sunday purge.
        self.assertLessEqual(filter_depth(retention.query_filter(NOW, "US Remote")), 2)

    def test_the_fake_ledger_refuses_what_notion_refuses(self):
        three_deep = {"and": [{"or": [{"and": [{"property": "x", "checkbox": {"equals": True}}]}]}]}
        self.assertEqual(filter_depth(three_deep), 3)
        with self.assertRaises(retention.notion_client.NotionError):
            LedgerWithFilterLimit().call("POST", "/data_sources/ledger/query", {"filter": three_deep})

    def test_the_filter_still_asks_for_every_protection(self):
        text = str(retention.query_filter(NOW - dt.timedelta(days=retention.RETIRE_DAYS), "US Remote"))
        for part in ("'Applied'", "'Applied On'", "'Saturn Decision'", "'Visible Lane'", "'US Remote'", "'Posting Date'", "'First Surfaced'", "2026-09-11"):
            self.assertIn(part, text)


class Purge(unittest.TestCase):
    def run_purge(self, pages, rows, live):
        client = LedgerWithFilterLimit(pages)
        with database(rows), mock.patch.object(retention.notion_client, "Client", lambda environ=None: client):
            return client, retention.purge_notion(live, NOW, environ={"HIRING_PIPELINE_PAGE_ID": ""})

    def test_a_dry_run_reaches_the_candidates_instead_of_failing_with_http_400(self):
        client, counts = self.run_purge([page("p1", "2026-08-01")], {"p1": (7, "k", "f", "Acme", "Role")}, live=False)
        self.assertEqual((counts["target"], counts["candidates"], counts["ours"], counts["retired"]), ("ok", 1, 1, 0))
        self.assertEqual(len(client.queries), 1)

    def test_only_a_stale_job_is_retired_even_when_the_query_over_returns(self):
        pages = [page("old", "2026-08-01"), page("repost", "2026-10-05", "2026-07-01"), page("undated-old", None, "2026-08-01"), page("undated-new", None, "2026-10-01")]
        rows = {pid: (i, f"k{i}", f"f{i}", "Acme", "Role") for i, pid in enumerate(("old", "repost", "undated-old", "undated-new"), 1)}
        with mock.patch.object(retention.tombstone, "write"):
            client, counts = self.run_purge(pages, rows, live=True)
        trashed = [path for method, path in client.log if method == "PATCH"]
        self.assertEqual(trashed, ["/pages/old", "/pages/undated-old"])                # a recent Posting Date keeps a reposted job; a recent First Surfaced keeps an undated one
        self.assertEqual((counts["retired"], counts["failed"]), (2, 0))


if __name__ == "__main__":
    unittest.main()
