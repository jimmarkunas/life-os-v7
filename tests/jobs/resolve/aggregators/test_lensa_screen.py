import unittest
from datetime import datetime

from lifeos.jobs.resolve.aggregators import lensa

NOW = datetime(2026, 10, 4, 12, 0)


class Cursor:
    def __init__(self, new, elsewhere):
        self.sets, self.sql = [new, elsewhere], []

    def execute(self, sql, args=()):
        self.sql.append((sql, args))
        if sql.startswith("SELECT"):
            self.rows = self.sets.pop(0)

    def fetchall(self):
        return self.rows


def go(new, elsewhere, live=True):
    cursor = Cursor(new, elsewhere)
    return lensa.screen(cursor, live, now=NOW), cursor.sql


class ScreenTests(unittest.TestCase):
    def test_low_explicit_dollar_pay_is_excluded_without_a_search(self):
        counts, sql = go([(1, "Acme", "Senior Program Manager", "$60K/yr - $70K/yr"), (2, "Acme", "Program Lead", "$90K/yr"),
                          (3, "Acme", "Senior Analyst", "£20,000 a year"), (4, "Acme", "Senior Planner", None)], [])
        self.assertEqual(counts, {"screened_pay": 1, "screened_dup": 0})                      # pounds are not compared against the US floor; no pay is allowed
        update = [(s, a) for s, a in sql if s.startswith("UPDATE")]
        self.assertEqual([a[1] for _, a in update], [1])
        self.assertIn("'screen_pay'", update[0][0])

    def test_an_opening_another_producer_already_settled_is_a_duplicate_of_it(self):
        counts, sql = go([(1, "Stripe, Inc.", "Senior Product Manager (Remote)", None), (2, "Stripe, Inc.", "Product Manager", None)],
                         [(90, "Stripe, Inc.", "Senior Product Manager"), (91, "Stripe, Inc.", "Product Manager")])
        self.assertEqual(counts["screened_dup"], 1)                                            # a two-word title is too generic: never screened
        update = [a for s, a in sql if s.startswith("UPDATE")]
        self.assertEqual(update, [(90, NOW, 1)])

    def test_a_dry_run_counts_and_writes_nothing(self):
        counts, sql = go([(1, "Acme", "Senior Program Manager", "$60K/yr")], [], live=False)
        self.assertEqual(counts["screened_pay"], 1)
        self.assertFalse([s for s, _ in sql if s.startswith("UPDATE")])

    def test_only_settled_states_from_other_producers_count(self):
        _, sql = go([], [])
        select = [s for s, _ in sql if s.startswith("SELECT")][1]
        self.assertIn("source<>'lensa'", select)
        self.assertIn("status IN", select)


if __name__ == "__main__":
    unittest.main()
