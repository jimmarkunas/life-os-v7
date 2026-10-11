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


def database(rows, lanes=("US Remote",), boards=None):
    """store.connect() stand-in: the lane query returns the given lanes, the per-page lookup returns the stored job row (id, keys, company, title,
    source), and the board lookup returns (items still CURRENT, items REMOVED) for a job id."""
    def handler(sql, args, cursor):
        if sql.startswith("SELECT DISTINCT lane"):
            cursor.fetchall = lambda: [(lane,) for lane in lanes]
        elif sql.startswith("SELECT id, dedupe_key"):
            cursor.row = rows.get(args[0])
        elif sql.startswith("SELECT SUM(state"):
            cursor.row = (boards or {}).get(args[0], (None, None))
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
    def run_purge(self, pages, rows, live, lanes=("US Remote",), client=None, boards=None):
        client = client or LedgerWithFilterLimit(pages)
        with database(rows, lanes, boards), mock.patch.object(retention.notion_client, "Client", lambda environ=None: client):
            return client, retention.purge_notion(live, NOW, environ={"HIRING_PIPELINE_PAGE_ID": ""})

    def test_a_dry_run_reaches_the_candidates_instead_of_failing_with_http_400(self):
        client, counts = self.run_purge([page("p1", "2026-08-01")], {"p1": (7, "k", "f", "Acme", "Role", "newsletter")}, live=False)
        self.assertEqual((counts["target"], counts["candidates"], counts["ours"], counts["retired"]), ("ok", 1, 1, 0))
        self.assertEqual(len(client.queries), 1)

    def test_only_a_stale_job_is_retired_even_when_the_query_over_returns(self):
        pages = [page("old", "2026-08-01"), page("repost", "2026-10-05", "2026-07-01"), page("undated-old", None, "2026-08-01"), page("undated-new", None, "2026-10-01")]
        rows = {pid: (i, f"k{i}", f"f{i}", "Acme", "Role", "newsletter") for i, pid in enumerate(("old", "repost", "undated-old", "undated-new"), 1)}
        with mock.patch.object(retention.tombstone, "write"):
            client, counts = self.run_purge(pages, rows, live=True)
        trashed = [path for method, path in client.log if method == "PATCH"]
        self.assertEqual(trashed, ["/pages/old", "/pages/undated-old"])                # a recent Posting Date keeps a reposted job; a recent First Surfaced keeps an undated one
        self.assertEqual((counts["retired"], counts["failed"]), (2, 0))

    def retire(self, source, board):
        """-> counts and the pages trashed, for one old, unapplied job read from `source`, whose board record is `board` = (CURRENT, REMOVED)."""
        with mock.patch.object(retention.tombstone, "write"):
            client, counts = self.run_purge([page("p1", "2026-08-01")], {"p1": (7, "k", "f", "Acme", "Role", source)}, True, boards={7: board})
        return counts, [path for method, path in client.log if method == "PATCH"]

    def test_a_role_still_listed_on_its_employers_board_is_kept_however_old(self):
        counts, trashed = self.retire("web:acme", (1, 0))                              # D111: open means open
        self.assertEqual((trashed, counts["kept_open"], counts["retired"]), ([], 1, 0))

    def test_a_role_with_no_board_record_is_kept_because_nothing_proves_it_closed(self):
        counts, trashed = self.retire("web:acme", (None, None))
        self.assertEqual((trashed, counts["kept_open"]), ([], 1))

    def test_a_role_the_board_no_longer_lists_is_retired_by_age(self):
        counts, trashed = self.retire("web:acme", (0, 1))                              # the web pass marked it REMOVED after a complete listing
        self.assertEqual((trashed, counts["kept_open"], counts["retired"]), (["/pages/p1"], 0, 1))

    def test_feed_aggregator_and_newsletter_roles_keep_the_posting_age_rule(self):
        for source in ("web:openjobs", "newsletter", "lensa"):
            counts, trashed = self.retire(source, (1, 0))
            self.assertEqual((source, trashed, counts["kept_open"]), (source, ["/pages/p1"], 0))

    def test_a_dry_run_counts_the_roles_it_would_keep_open(self):
        client, counts = self.run_purge([page("p1", "2026-08-01")], {"p1": (7, "k", "f", "Acme", "Role", "web:acme")}, False, boards={7: (1, 0)})
        self.assertEqual((counts["ours"], counts["kept_open"], counts["retired"]), (1, 1, 0))

    def test_the_ledgers_own_spelling_of_a_lane_is_asked_for_not_the_databases(self):
        # The database says `Scale-Up`, the Ledger option is `Scale-up`; Notion refused the former with HTTP 400 on both Sundays (run 38153372055).
        client, counts = self.run_purge([], {}, False, lanes=("Newsletter", "Scale-Up", "US Remote"))
        asked = sorted(f["and"][3]["select"]["equals"] for f in client.queries)
        self.assertEqual((asked, counts["lanes"]), (["Newsletter", "Scale-up", "US Remote"], 3))

    def test_a_lane_the_ledger_has_no_option_for_is_not_asked_about(self):
        client, counts = self.run_purge([], {}, False, lanes=("US Remote", "Dice", ""))
        self.assertEqual((len(client.queries), counts["lanes"]), (1, 1))

    def test_unreadable_lane_options_fail_closed_and_ask_nothing(self):
        client, counts = self.run_purge([], {}, False, client=LedgerWithFilterLimit(lane_options=()))
        self.assertEqual((counts["target"], client.queries), ("lane_options_unreadable", []))

    def test_a_refused_query_names_the_lane_and_what_notion_named_but_nothing_else(self):
        class Refuses(LedgerWithFilterLimit):
            def call(self, method, path, body=None):
                if method == "POST":
                    error = retention.notion_client.NotionError("NOTION_HTTP_400")
                    error.detail = "validation_error body.filter.and[3]"
                    raise error
                return super().call(method, path, body)
        client = Refuses()
        with database({}), mock.patch.object(retention.notion_client, "Client", lambda environ=None: client):
            with self.assertRaises(retention.notion_client.NotionError) as caught:
                retention.purge_notion(False, NOW, environ={"HIRING_PIPELINE_PAGE_ID": ""})
        self.assertEqual(str(caught.exception), "NOTION_HTTP_400 lane=US Remote validation_error body.filter.and[3]")


if __name__ == "__main__":
    unittest.main()
