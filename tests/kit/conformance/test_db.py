"""LIFE-549: the db fakes in tests/kit conform to the real pymysql cursor API and to MySQL's documented results."""
import unittest

from tests.kit.conformance import db_subjects as subjects
from tests.kit.conformance import runner

SPEC = runner.load("db")


class DbConformance(unittest.TestCase):
    @unittest.skipIf(subjects._real is None, "pymysql not installed")
    def test_the_real_pymysql_cursor_has_the_api_the_cases_expect(self):
        self.assertEqual(runner.problems(SPEC, "pymysql", subjects.observe_pymysql), [])

    def test_the_cursor_fake_conforms_apart_from_its_documented_gaps(self):
        self.assertEqual(runner.problems(SPEC, "fake_cursor", subjects.observe_fake_cursor), [])

    def test_the_mysql_over_sqlite_fake_conforms_apart_from_its_documented_gaps(self):
        self.assertEqual(runner.problems(SPEC, "mysqlite", subjects.observe_mysqlite), [])

    def test_negative_control_a_fake_that_drifts_is_caught_and_a_stale_gap_is_caught(self):
        def drifted(case):
            out = subjects.observe_mysqlite(case)
            if "dup" in out:
                out["dup"] = 1                                              # INSERT IGNORE on a duplicate must affect 0 rows
            return out
        self.assertTrue(any("insert_ignore" in p for p in runner.problems(SPEC, "mysqlite", drifted)))
        healed = dict(SPEC, gaps={"mysqlite": {"api_core_names": "pretend gap that does not differ"}})
        self.assertTrue(any("no longer differs" in p for p in runner.problems(healed, "mysqlite", subjects.observe_mysqlite)))


if __name__ == "__main__":
    unittest.main()
