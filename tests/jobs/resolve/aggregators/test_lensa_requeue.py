import unittest
from datetime import datetime

from lifeos.jobs.resolve.aggregators import lensa
from tests.kit.db import FakeCursor


def _run(rows):
    seen = []
    cursor = FakeCursor(handler=lambda sql, args, cur: seen.append((sql, args)))
    cursor.fetchall = lambda: rows
    return lensa.requeue_held(cursor), seen


class RequeueTests(unittest.TestCase):
    def test_only_decorated_titles_are_requeued(self):
        n, seen = _run([(1, "Product Manager (Fully Remote)"), (2, "Product Manager"), (3, "Client PM | FirstPoint | USA (Remote)")])
        self.assertEqual(n, 2)
        self.assertEqual([a[0] for s, a in seen if s.startswith("UPDATE")], [1, 3])

    def test_the_selection_is_bounded_by_the_cutoff_and_to_definite_no_matches(self):
        _, seen = _run([])
        sql, args = seen[0]
        self.assertIn("LIKE 'no_match%", sql)
        self.assertEqual(args, (lensa.TITLE_FIX_AT,))

    def test_the_new_reason_is_not_a_no_match_so_a_second_failure_is_stamped_after_the_cutoff(self):
        _, seen = _run([(1, "Product Manager (Fully Remote)")])
        update = [s for s, a in seen if s.startswith("UPDATE")][0]
        self.assertIn("'requeued_titles'", update)
        self.assertIn("updated_at=UTC_TIMESTAMP()", update)                       # now: after the cutoff, so never selected again
        self.assertGreater(datetime(2026, 10, 4), lensa.TITLE_FIX_AT)


if __name__ == "__main__":
    unittest.main()
