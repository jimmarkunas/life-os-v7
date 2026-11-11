"""LIFE-577: the receipt time stamp is Central, 12-hour, labelled CT, never UTC."""
import re
import subprocess
import sys
import unittest
from datetime import datetime, timezone

from lifeos.platform import ct_stamp


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


class Stamp(unittest.TestCase):
    def test_a_winter_time_is_central_standard_and_twelve_hour(self):
        self.assertEqual(ct_stamp.stamp(utc(2026, 11, 11, 19, 5)), "Nov 11 2026, 1:05 PM CT")           # 19:05 UTC is 1:05 PM CST

    def test_a_summer_time_is_central_daylight_and_still_reads_ct(self):
        self.assertEqual(ct_stamp.stamp(utc(2026, 7, 4, 16, 30)), "Jul 4 2026, 11:30 AM CT")           # 16:30 UTC is 11:30 AM CDT

    def test_midnight_and_noon_read_twelve(self):
        self.assertEqual(ct_stamp.stamp(utc(2026, 11, 11, 6, 0)), "Nov 11 2026, 12:00 AM CT")
        self.assertEqual(ct_stamp.stamp(utc(2026, 11, 11, 18, 9)), "Nov 11 2026, 12:09 PM CT")

    def test_the_date_follows_central_not_utc(self):
        self.assertEqual(ct_stamp.stamp(utc(2026, 11, 12, 3, 48)), "Nov 11 2026, 9:48 PM CT")          # 03:48 UTC on the 12th is still the 11th in Chicago

    def test_the_command_prints_one_line_in_that_format(self):
        out = subprocess.run([sys.executable, "-m", "lifeos.platform.ct_stamp"], capture_output=True, text=True, check=True).stdout
        self.assertRegex(out.strip(), r"^[A-Z][a-z]{2} \d{1,2} \d{4}, (1[0-2]|[1-9]):[0-5]\d (AM|PM) CT$")
        self.assertNotIn("UTC", out)


if __name__ == "__main__":
    unittest.main()
