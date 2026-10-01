import unittest

from pipeline import budget, linkchain


class FakeConn:
    def __init__(self, used=0):
        self.used, self.inserted = used, 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        if sql.startswith("SELECT COALESCE"):
            self._row = (self.used,)
        elif sql.startswith("INSERT INTO v7_spend"):
            self.used += args[1]
            self.inserted += 1

    def fetchone(self):
        return self._row


class BudgetTests(unittest.TestCase):
    def test_default_cap_is_zero_and_bad_values_are_zero(self):
        for env in ({}, {"V7_BROWSER_CAP_USD": ""}, {"V7_BROWSER_CAP_USD": "abc"}, {"V7_BROWSER_CAP_USD": "-5"}):
            self.assertEqual(budget.browser_cap_usd(env), 0.0)
        self.assertEqual(budget.browser_cap_usd({"V7_BROWSER_CAP_USD": " 5 "}), 5.0)

    def test_cap_math_five_dollars_is_2500_minutes(self):
        self.assertEqual(budget.cap_seconds(5.0), 150000)

    def test_reserve_refuses_when_it_would_exceed_cap(self):
        conn = FakeConn(used=150000 - 89)
        self.assertFalse(budget.reserve_browser(conn, 5.0))        # 90 s would not fit
        self.assertEqual(conn.inserted, 0)
        conn = FakeConn(used=150000 - 90)
        self.assertTrue(budget.reserve_browser(conn, 5.0))
        self.assertFalse(budget.reserve_browser(conn, 5.0))        # now full: hard stop

    def test_zero_cap_reserves_nothing(self):
        conn = FakeConn()
        self.assertFalse(budget.reserve_browser(conn, 0))


class ResolveGuardTests(unittest.TestCase):
    def test_no_paid_session_without_a_cap(self):
        conn = FakeConn()
        self.assertEqual(linkchain.resolve(conn, "https://example.com/job", {"TINYFISH_API_KEY": "k" * 30}),
                         {"outcome": "paid_browser_disabled"})
        self.assertEqual(conn.inserted, 0)


class DecisionTests(unittest.TestCase):
    def test_apply_controls(self):
        for label in ("Apply", "Apply now", "Apply on company site", "  Easy   Apply "):
            self.assertTrue(linkchain.is_apply_control(label), label)
        for label in ("", "Application tips and interview guides for this role and many more", "Save", "Share"):
            self.assertFalse(linkchain.is_apply_control(label), label)

    def test_outcomes_follow_the_ranking(self):
        job = "https://www.linkedin.com/jobs/view/1"
        self.assertEqual(linkchain.decide("Easy Apply", job, job), ("easy_apply", job))
        self.assertEqual(linkchain.decide("Apply", "https://boards.greenhouse.io/acme/jobs/9", job)[0], "ats")
        self.assertEqual(linkchain.decide("Apply", "https://careers.acme.example/j/9", job)[0], "employer")
        self.assertEqual(linkchain.decide("Apply", "https://www.linkedin.com/jobs/view/1", job)[0], "aggregator")


class ScopeTests(unittest.TestCase):
    def test_browser_api_is_referenced_only_by_linkchain(self):
        import pathlib
        root = pathlib.Path(linkchain.__file__).parent
        offenders = [p.name for p in root.glob("*.py")
                     if p.name != "linkchain.py" and "api.browser.tinyfish.ai" in p.read_text()]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
