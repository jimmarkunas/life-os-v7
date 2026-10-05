import unittest
from datetime import datetime, timezone

from lifeos.amazon import backfill, stage

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class BackfillTests(unittest.TestCase):
    def test_months_cover_the_range_exactly_once(self):
        got = list(backfill.months(datetime(2025, 11, 15, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc)))
        self.assertEqual([(a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")) for a, b in got],
                         [("2025-11-15", "2025-12-01"), ("2025-12-01", "2026-01-01"), ("2026-01-01", "2026-02-01"), ("2026-02-01", "2026-02-10")])

    def test_each_month_is_one_bounded_ingest_and_counts_add_up(self):
        seen = []

        def ingest(limit, live, **kw):
            seen.append((kw["since"].strftime("%Y-%m"), kw["until"].strftime("%Y-%m"), live))
            return {"listed": 3, "orders_new": 2, "filed": 1, "review_why": {"ORDER_ID_MISSING": 1}}
        total = backfill.run(0, False, environ={"AMAZON_SINCE": "2026-08-20"}, now=NOW, ingest=ingest)
        self.assertEqual([s[:2] for s in seen], [("2026-08", "2026-09"), ("2026-09", "2026-10"), ("2026-10", "2026-10")])
        self.assertTrue(all(live is False for *_, live in seen))
        self.assertEqual((total["months"], total["listed"], total["orders_new"], total["filed"]), (3, 9, 6, 3))
        self.assertEqual(total["review_why"], {"ORDER_ID_MISSING": 3})

    def test_a_failed_month_is_reported_and_the_rest_continue(self):
        def ingest(limit, live, **kw):
            if kw["since"].month == 9:
                raise stage.AmazonError("AMAZON_NOTION_QUERY_FAILED")
            return {"listed": 1}
        total = backfill.run(0, True, environ={"AMAZON_SINCE": "2026-08-01"}, now=NOW, ingest=ingest)
        self.assertEqual((total["months"], total["months_failed"], total["listed"]), (3, 1, 2))
        self.assertTrue(total["first_failed"].startswith("2026-09:"))

    def test_the_gmail_search_closes_the_window(self):
        query = stage.window_query(None, NOW, datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertIn("after:", query)
        self.assertIn("before:", query)
