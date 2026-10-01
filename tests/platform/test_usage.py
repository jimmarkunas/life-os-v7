import unittest

from lifeos.platform import usage


class BudgetTests(unittest.TestCase):
    def test_grant_never_exceeds_cap(self):
        self.assertEqual(usage.granted(0, 900, 30), 30)
        self.assertEqual(usage.granted(890, 900, 30), 10)
        self.assertEqual(usage.granted(900, 900, 30), 0)
        self.assertEqual(usage.granted(950, 900, 30), 0)

    def test_caps_leave_headroom_under_free_limits(self):
        self.assertLessEqual(usage.FETCH_DAILY_CAP, 900)                       # free limit is 1,000/day
        self.assertLessEqual(usage.FETCH_BATCH * 60 / usage.MIN_SECONDS_PER_BATCH, 120)   # free limit is 150/min

    def test_pacer_spaces_batches(self):
        now, slept = [0.0], []
        pacer = usage.Pacer(clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
        pacer.wait()
        now[0] += 1
        pacer.wait()
        self.assertEqual(round(slept[0]), usage.MIN_SECONDS_PER_BATCH - 1)


if __name__ == "__main__":
    unittest.main()
