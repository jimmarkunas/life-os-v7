import datetime as dt
import json
import unittest

from lifeos.jobs import alerts as jobs_alerts, alertstate
from lifeos.platform import alerts

NOW = dt.datetime(2026, 10, 25, 12, 0)
QUIET = {"published_24h": 5, "sources_failing": 0, "stuck_jobs": 0}


class Detect(unittest.TestCase):
    def keys(self, counts, expiries=None, now=NOW):
        return sorted(a.key for a in jobs_alerts.detect(counts, now, expiries))

    def test_healthy_counts_raise_nothing(self):
        self.assertEqual(self.keys(QUIET), [])

    def test_each_condition(self):
        self.assertEqual(self.keys({**QUIET, "published_24h": 0}), ["quiet_24h"])
        self.assertEqual(self.keys({**QUIET, "sources_failing": 2}), ["sources_failing"])
        self.assertEqual(self.keys({**QUIET, "stuck_jobs": 24}), [])
        self.assertEqual(self.keys({**QUIET, "stuck_jobs": 25}), ["stuck_jobs"])

    def test_a_source_that_stopped_delivering_pages_the_phone_once_per_source(self):
        got = jobs_alerts.detect({**QUIET, "stopped_sources": [("lensa", 41.2), ("jobright", 9.0)]}, NOW)
        self.assertEqual(sorted(a.key for a in got), ["source_stopped_jobright", "source_stopped_lensa"])
        self.assertTrue(all(a.severity == alerts.PAGE for a in got))
        self.assertIn("41.2 a day", [a for a in got if a.key.endswith("lensa")][0].detail)

    def gather_stopped(self, feed_rows):
        class Cur:
            def __init__(self):
                self.last = ""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                sql % tuple("x" for _ in params)                       # the statement must survive %-formatting
                self.last = sql

            def fetchone(self):
                return (1,)

            def fetchall(self):
                if "last_complete_at" in self.last:                     # boards read in full inside the window
                    return [("braze",)]
                if "v7_feed" in self.last:                              # the Open Jobs feed: OK when its last run read it without error
                    return feed_rows
                return [("lensa", 0, 700), ("jobright", 5, 300), ("quiet-board", 0, 6), ("brand-new", 0, None), ("web:braze", 0, 400), ("web:figma", 0, 400), ("web:openjobs", 0, 3400)]

        class Conn:
            def cursor(self):
                return Cur()
        return jobs_alerts.gather(Conn(), NOW)["stopped_sources"]

    def test_gather_flags_only_a_busy_source_that_went_quiet(self):
        self.assertEqual(self.gather_stopped([("openjobs",)]), [("lensa", 50.0), ("web:figma", 28.6)])

    def test_a_feed_read_without_error_is_idle_but_a_degraded_feed_still_pages(self):
        self.assertNotIn("web:openjobs", [name for name, _ in self.gather_stopped([("openjobs",)])])
        self.assertIn(("web:openjobs", 242.9), self.gather_stopped([]))                         # no OK feed row: the read failed (DEGRADED) or never ran


    def test_stuck_breakdown_groups_by_state_and_keeps_only_counts_and_codes(self):
        class Cur:
            def execute(self, sql, params=()):
                sql % tuple("x" for _ in params)                       # the statement must survive %-formatting
                assert "SELECT" in sql and "UPDATE" not in sql and "DELETE" not in sql

            def fetchall(self):
                old = NOW - dt.timedelta(hours=50)
                return [("READY", 1, 0, "none", None, 3, old), ("READY", 1, 0, "none", None, 2, NOW - dt.timedelta(hours=30)),
                        ("RESOLVED", 0, 0, "none", "no_job_id 12", 4, old)]
        got = alertstate.stuck(Cur(), NOW)
        self.assertEqual(got, {"READY|link=1|page=0|fit=none|none": [5, 50], "RESOLVED|link=0|page=0|fit=none|no_job_id N": [4, 50]})

    def test_hold_recovery_state_is_read_only_counts_and_codes_with_central_times(self):
        class Cur:
            asked = []

            def execute(self, sql, params=()):
                sql % tuple("x" for _ in params)
                assert sql.startswith("SELECT") and "UPDATE" not in sql and "DELETE" not in sql
                Cur.asked.append(sql)

            def fetchall(self):
                if len(Cur.asked) == 1:
                    return [("NEW", "recovery_retry", 2, dt.datetime(2026, 10, 13, 18, 4, 21), dt.datetime(2026, 10, 13, 19, 30, 0)),
                            ("EXCLUDED_UNRESOLVED", "unresolved_after_recovery", 1, dt.datetime(2026, 10, 13, 18, 4, 21), dt.datetime(2026, 10, 13, 18, 4, 21))]
                return [("hold_expired", 7), ("unresolved_after_recovery", 1)]
        got = alertstate.hold_recovery(Cur(), NOW)
        self.assertEqual(got["recovered"]["NEW|recovery_retry"], {"jobs": 2, "first": "2026-10-13 1:04 PM CT", "last": "2026-10-13 2:30 PM CT"})     # stored UTC, shown in Central
        self.assertEqual(got["excluded_48h"], {"hold_expired": 7, "unresolved_after_recovery": 1})

    def test_body_shape_counts_what_notion_can_refuse_and_never_returns_text(self):
        def block(kind, text):
            return {"object": "block", "type": kind, kind: {"rich_text": [{"type": "text", "text": {"content": text}}]}}
        too_long = "a" * 1996 + "\U0001F600" * 4                                       # 2000 characters, 2004 UTF-16 units (the Oct 12 publish failure)
        blocks = [block("paragraph", "marker"), block("heading_2", "Summary"), block("paragraph", too_long), block("paragraph", "bad\x01char and nul\x00 here")]
        got = alertstate.body_shape(blocks)
        self.assertEqual((got["over_2000_chars"], got["over_2000_units"]), (0, 1))
        self.assertEqual((got["control"], got["nul"], got["surrogate"]), (1, 1, 0))
        self.assertEqual(got["child2"], {"type": "paragraph", "items": 1, "chars": 2000, "units": 2004, "empty": False, "control": 0, "nul": 0, "surrogate": 0, "private_use": 0})
        self.assertNotIn("bad", json.dumps(got))                                            # counts only

    def test_publish_bodies_reads_only_and_falls_back_to_the_full_text(self):
        class Cur:
            sql = ""

            def execute(self, sql, params=()):
                sql % tuple("x" for _ in params)
                Cur.sql = sql

            def fetchall(self):
                return [(None, None, None, None, "A plain description line"), ("Short summary", None, None, None, "ignored")]
        got = alertstate.publish_bodies(Cur())
        self.assertEqual([g["blocks"] for g in got], [3, 3])                                   # marker, heading, one paragraph each
        self.assertTrue(Cur.sql.startswith("SELECT") and "UPDATE" not in Cur.sql and "DELETE" not in Cur.sql)

    def test_expiry_pages_a_week_before_and_after(self):
        exp = {"timer_token": {"label": "Timer token", "expires": "2026-10-31", "renew": "renew it"}}
        self.assertEqual(self.keys(QUIET, exp, dt.datetime(2026, 10, 23)), [])
        a = jobs_alerts.detect(QUIET, dt.datetime(2026, 10, 24), exp)[0]
        self.assertEqual((a.key, a.severity), ("expiry_timer_token", alerts.PAGE))
        self.assertIn("expired", jobs_alerts.detect(QUIET, dt.datetime(2026, 11, 2), exp)[0].title)

    def test_the_real_expiry_file_is_valid(self):
        for item in json.loads(jobs_alerts.EXPIRIES.read_text()).values():
            dt.date.fromisoformat(item["expires"])
            self.assertTrue(item["label"] and item["renew"])


if __name__ == "__main__":
    unittest.main()
