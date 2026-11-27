"""The promotion check's 24-hour window is one row per fingerprint in the existing rollover table."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import promotion_call
from lifeos.jira import reconcile_stage as stage

CT = ZoneInfo("America/Chicago")


class Cursor:
    def __init__(self, rows):
        self.rows, self.row = rows, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        if sql.startswith("SELECT last_ok, last_notified"):
            self.row = self.rows.get(params[0])
        elif sql.startswith("INSERT INTO v7_jira_rollover"):
            self.rows[params[0]] = (params[1], params[2])
        elif sql == "DELETE FROM v7_jira_rollover WHERE k LIKE %s" and params == ("pr%",):
            for key in [k for k in self.rows if k.startswith("pr")]:
                del self.rows[key]
        elif not sql.startswith("CREATE TABLE"):
            raise AssertionError(sql)

    def fetchone(self):
        return self.row


class Db:
    def __init__(self):
        self.rows = {"reconcile": "untouched", "rc1234": "untouched"}

    def cursor(self):
        return Cursor(self.rows)


class Window(unittest.TestCase):
    FIRST = datetime(2027, 1, 6, 8, 5, tzinfo=CT)
    LAST = datetime(2027, 1, 7, 8, 5, tzinfo=CT)

    def test_a_live_pass_is_saved_and_read_back_in_central_time(self):
        db = Db()
        window = stage.Stability(db, True)
        self.assertIsNone(window.load("abc"))
        window.save("abc", self.FIRST, self.LAST)
        self.assertEqual(window.load("abc"), {"first": self.FIRST, "last": self.LAST})
        self.assertIn("prabc", db.rows)

    def test_reset_clears_only_the_promotion_rows(self):
        db = Db()
        window = stage.Stability(db, True)
        window.save("abc", self.FIRST, self.FIRST)
        window.reset()
        self.assertEqual(sorted(db.rows), ["rc1234", "reconcile"])

    def test_a_dry_run_reads_but_never_writes(self):
        db = Db()
        window = stage.Stability(db, False)
        window.save("abc", self.FIRST, self.FIRST)
        window.reset()
        self.assertEqual(sorted(db.rows), ["rc1234", "reconcile"])


class Hook(unittest.TestCase):
    def test_the_hook_off_by_default_reads_nothing(self):
        out = promotion_call.promotion({}, datetime(2027, 1, 6, 9, tzinfo=CT), True, lambda *a: True, client=object())
        self.assertEqual(out, {"status": "not_enabled"})

    def test_before_jan_6_nothing_is_read_or_written(self):
        class Boom:
            def get(self, *a, **k):
                raise AssertionError("read before the boundary")

        class Notion:
            def call(self, *a, **k):
                raise AssertionError("touched Notion before the boundary")

        out = promotion_call.promotion(
            {"V2_PROMOTION": "on"},
            datetime(2027, 1, 5, 9, tzinfo=CT),
            True,
            lambda *a: True,
            client=Boom(),
            stability=None,
            client_factory=lambda environ: Notion(),
        )
        self.assertEqual(out, {"status": "not_due"})

    def test_the_stage_hands_the_jira_client_and_the_window_to_the_hook(self):
        seen = {}

        def hook(environ, now, live, alert, client=None, stability=None):
            seen.update(client=client, stability=stability)
            return {"status": "not_due"}

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor({})

        from unittest.mock import patch

        client = object()
        with (
            patch("lifeos.jira.reconcile.run", return_value={"x": 1}),
            patch("lifeos.jira.reconcile_stage.save_summary"),
            patch("lifeos.jira.recurrence.summary", return_value="line"),
            patch("lifeos.jira.recurrence.extra"),
            patch("lifeos.jira.reconcile.enabled", return_value=True),
            patch("lifeos.jira.writers.live_writers", return_value=object()),
        ):
            stage.run(
                1, True, environ={}, client=client, now=datetime(2027, 1, 6, 9, tzinfo=CT), connect=Conn, after=hook
            )
        self.assertIs(seen["client"], client)
        self.assertIsInstance(seen["stability"], stage.Stability)
        self.assertTrue(seen["stability"].live)


if __name__ == "__main__":
    unittest.main()
