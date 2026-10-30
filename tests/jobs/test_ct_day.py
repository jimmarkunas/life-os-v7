"""LIFE-170 (Jim, Oct 30 2026): the Job Ledger's First Surfaced and Last Seen are Jim's calendar day in US Central, so the "Today" view turns over at
midnight CT, not at 7 PM CT (midnight UTC). Jobs OS stores naive UTC instants. Pure: no Notion, no database."""
import unittest
from datetime import date, datetime, timezone

from lifeos.jobs import ledger, repost


class CentralDay(unittest.TestCase):
    def test_an_evening_job_is_stamped_on_jims_day_not_the_utc_day(self):
        self.assertEqual(ledger.ct_day(datetime(2026, 10, 30, 1, 6)), date(2026, 10, 29))            # 8:06 PM CT Oct 29 (CDT)
        self.assertEqual(ledger.ct_day(datetime(2026, 10, 30, 5, 0)), date(2026, 10, 30))            # midnight CT
        self.assertEqual(ledger.ct_day(datetime(2026, 11, 3, 5, 30)), date(2026, 11, 2))             # 11:30 PM CT Nov 2 (CST, after the switch)
        self.assertEqual(ledger.ct_day(datetime(2026, 10, 30, 1, 6, tzinfo=timezone.utc)), date(2026, 10, 29))

    def test_a_date_or_nothing_passes_through(self):
        self.assertEqual(ledger.ct_day(date(2026, 10, 30)), date(2026, 10, 30))
        self.assertIsNone(ledger.ct_day(None))

    def test_last_seen_uses_the_central_day(self):
        self.assertEqual(repost.sync_props(datetime(2026, 10, 30, 2, 0), False)["Last Seen"], {"date": {"start": "2026-10-29"}})


if __name__ == "__main__":
    unittest.main()
