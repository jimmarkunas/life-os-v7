"""the credit window (5th 6:00 PM CT to the next 5th 6:00 PM CT) has one rule in three files (meter, report reader, Daily
Report)
"""
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.edge.anthropic_report import reader
from lifeos.edge.honcho import meter_proxy
from lifeos.platform import credit_window as cw

UTC = timezone.utc


class Agreement(unittest.TestCase):
    def moments(self):
        start = datetime(2026, 10, 1, tzinfo=UTC)
        edges = [start + timedelta(hours=h) for h in range(0, 24 * 540, 7)]
        for year, month in ((2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 11)):
            boundary = datetime(year, month, 5, 18, tzinfo=cw.CENTRAL)
            edges += [boundary - timedelta(seconds=1), boundary, boundary + timedelta(seconds=1)]
        return edges

    def test_the_three_implementations_agree_everywhere(self):
        for moment in self.moments():
            start, end = cw.epochs(moment)
            self.assertEqual(meter_proxy.window(int(moment.timestamp())), (start, end), moment)
            self.assertEqual(reader.since_now(moment), cw.since(moment), moment)
            self.assertTrue(start <= int(moment.timestamp()) < end, moment)

    def test_the_boundary_is_the_fifth_at_six_pm_central_and_follows_daylight_time(self):
        start, end = cw.window(datetime(2026, 11, 23, 12, tzinfo=cw.CENTRAL))
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-11-05T18:00:00-06:00", "2026-12-05T18:00:00-06:00"))
        start, end = cw.window(datetime(2027, 3, 20, 12, tzinfo=cw.CENTRAL))
        self.assertEqual((start.isoformat(), end.isoformat()), ("2027-03-05T18:00:00-06:00", "2027-04-05T18:00:00-05:00"))
        self.assertEqual(cw.since(datetime(2026, 12, 5, 23, 59, tzinfo=UTC)), "2026-11-06T00:00:00Z")
        self.assertEqual(cw.since(datetime(2026, 12, 6, 0, 0, tzinfo=UTC)), "2026-12-06T00:00:00Z")  # the first second of the next window
        self.assertEqual(cw.window(datetime(2027, 1, 2, tzinfo=UTC))[0].isoformat(), "2026-12-05T18:00:00-06:00")  # across the new year


if __name__ == "__main__":
    unittest.main()
