import json
import unittest
from unittest import mock

from lifeos.jobs import safety


class Cur:
    def __init__(self, errors):
        self.errors, self.seen = errors, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.seen.append((sql, params))
        for needle, error in self.errors.items():
            if needle in sql:
                raise error

    def fetchall(self):
        return []


class Conn:
    def __init__(self, errors=None):
        self.cur = Cur(errors or {})

    def cursor(self):
        return self.cur


class SqlSmokeTests(unittest.TestCase):
    def test_the_repository_statements_are_found_and_every_one_survives_driver_formatting(self):
        found = safety.statements()
        self.assertGreater(len(found), 100)
        for path, line, sql in found:
            marks = safety._placeholders(sql)
            sql % tuple(1 for _ in range(marks))                          # a stray '%' (the 5 PM crash) fails here, before any database
            self.assertEqual(len(safety._params(sql)), marks)

    def test_explain_runs_each_statement_with_one_value_per_placeholder(self):
        conn = Conn()
        counts = safety.sql_smoke(conn, [("m.py", 1, "SELECT * FROM v7_jobs WHERE id = %s AND x LIKE 'a%%' LIMIT %s")])
        sql, params = conn.cur.seen[0]
        self.assertEqual((sql.startswith("EXPLAIN SELECT"), params, counts["ok"]), (True, (1, 1), 1))

    def test_in_placeholders_take_a_tuple_and_runtime_fragments_are_skipped(self):
        conn = Conn()
        counts = safety.sql_smoke(conn, [("a.py", 1, "SELECT id FROM v7_jobs WHERE status IN %s AND x = %s"), ("b.py", 2, "SELECT id FROM v7_jobs WHERE k IN (")])
        self.assertEqual(conn.cur.seen[0][1], ((1,), 1))
        self.assertEqual((counts["ok"], counts["fragments"], len(conn.cur.seen)), (1, 1, 1))

    def test_a_driver_error_is_reported_by_location_and_code_never_by_text(self):
        conn = Conn({"BAD": Exception(1054, "Unknown column 'secret_name'")})
        counts = safety.sql_smoke(conn, [("a.py", 7, "SELECT BAD FROM v7_jobs"), ("b.py", 9, "SELECT 1 FROM v7_jobs")])
        self.assertEqual((counts["ok"], counts["failed"], counts["failures"]), (1, 1, ["a.py:7:1054"]))
        self.assertNotIn("secret_name", json.dumps(counts))

    def test_a_table_the_first_live_run_has_not_created_is_not_a_failure(self):
        counts = safety.sql_smoke(Conn({"v7_later": Exception(1146, "no such table")}), [("a.py", 1, "SELECT 1 FROM v7_later")])
        self.assertEqual((counts["missing_table"], counts["failed"]), (1, 0))


class FitGoldenTests(unittest.TestCase):
    SAVED = {"today": "2026-10-05", "jobs": {"1": ["Go", "ADMIT"], "2": ["No-Go", "EXCLUDE"]}}

    def run_with(self, now):
        with mock.patch.object(safety, "_decisions", return_value=now):
            return safety.run_fit_golden(0, False, rows=[("row",)], golden=self.SAVED)

    def test_same_decisions_pass(self):
        counts = self.run_with({"1": ["Go", "ADMIT"], "2": ["No-Go", "EXCLUDE"]})
        self.assertEqual((counts["saved"], counts["same"], counts["changed"], counts["missing"]), (2, 2, 0, 0))

    def test_a_flipped_decision_fails_with_the_flip_and_the_count(self):
        with self.assertRaises(safety.SafetyError) as caught:
            self.run_with({"1": ["No-Go", "EXCLUDE"], "2": ["No-Go", "EXCLUDE"]})
        self.assertIn("FIT_GOLDEN_CHANGED:1of2:Go/ADMIT->No-Go/EXCLUDE", str(caught.exception))

    def test_an_empty_saved_set_is_an_error_not_a_pass(self):
        with self.assertRaises(safety.SafetyError):
            safety.run_fit_golden(0, False, rows=[], golden={"today": "2026-10-05", "jobs": {}})

    def test_the_saved_file_is_valid_once_captured(self):
        if safety.GOLDEN.exists():
            saved = json.loads(safety.GOLDEN.read_text())
            self.assertTrue(saved["jobs"])
            for decision, lane in saved["jobs"].values():
                self.assertIn(decision, {"Go", "No-Go", "Unscorable"})


if __name__ == "__main__":
    unittest.main()
