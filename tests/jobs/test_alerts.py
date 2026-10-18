import datetime as dt
import hashlib
import json
import os
import unittest
from unittest import mock

from lifeos.jobs import alerts as jobs_alerts, alertstate, store
from lifeos.platform import alerts
from lifeos.sources.newsletters import finalize
from tests.kit.jobsdb import JobsDB

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
                return [("dice", 0, 700), ("jobright", 5, 300), ("quiet-board", 0, 6), ("brand-new", 0, None), ("web:braze", 0, 400), ("web:figma", 0, 400), ("web:examplefeed", 0, 3400),
                        ("lensa", 0, 700), ("web:openjobs", 0, 3400)]                     # D201: retired sources, busy before and silent now

        class Conn:
            def cursor(self):
                return Cur()
        return jobs_alerts.gather(Conn(), NOW)["stopped_sources"]

    def test_gather_flags_only_a_busy_source_that_went_quiet(self):
        self.assertEqual(self.gather_stopped([("examplefeed",)]), [("dice", 50.0), ("web:figma", 28.6)])

    def test_a_feed_read_without_error_is_idle_but_a_degraded_feed_still_pages(self):
        self.assertNotIn("web:examplefeed", [name for name, _ in self.gather_stopped([("examplefeed",)])])
        self.assertIn(("web:examplefeed", 242.9), self.gather_stopped([]))                      # no OK feed row: the read failed (DEGRADED) or never ran

    def test_a_retired_source_never_pages_even_with_no_feed_row(self):
        stopped = [name for name, _ in self.gather_stopped([])]
        self.assertFalse({"lensa", "web:openjobs"} & set(stopped), stopped)                     # D201: Lensa retired, Open Jobs ended 10 Oct 2026
        self.assertTrue(all(reason for reason in jobs_alerts.RETIRED_SOURCES.values()))          # each says why, specifically


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


MSG_A, MSG_B = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"
REF_A, REF_B = (hashlib.sha256(m.encode()).hexdigest() for m in (MSG_A, MSG_B))
SEEN = dt.datetime(2026, 10, 13, 15, 30)


class MessageTraceTests(unittest.TestCase):
    """LIFE-59: ALERTS_MESSAGE_REF (the SHA-256 of one Gmail message id) turns alerts-open into a read-only, allowlisted, per-vacancy trace of that message, over the real v7_job_sources / v7_jobs SQL."""

    def setUp(self):
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)
        self.db.raw.create_function("SHA2", 2, lambda value, bits: hashlib.sha256(str(value).encode()).hexdigest())          # MySQL's SHA2(x, 256), which the query uses to match the id without holding it
        self.n = 0

    def vacancy(self, message, status, reason=None, page=None, verified=None, source="jobright", title="Secret Title"):
        self.n += 1
        self.db.insert("v7_jobs", id=self.n, dedupe_key=f"secretkey{self.n}", status=status, title=title, company="Secret Co", source_url=f"https://secret.example/{self.n}", unresolved_reason=reason,
                       notion_page_id=page, verified_at=verified, first_seen=SEEN, last_seen=SEEN, updated_at=SEEN)
        self.association(message, self.n, source)

    def association(self, message, job_id, source="jobright"):
        self.n += 1
        self.db.insert("v7_job_sources", job_id=job_id, source=source, source_url_hash=f"hash{self.n}", gmail_message_id=message, seen_at=SEEN)

    def seed(self):
        for status, reason, page, verified in (("PUBLISHED", None, "page-1", SEEN), ("PUBLISHED", None, "page-2", None), ("HOLD", "http_503", None, None), ("EXCLUDED_FIT", "Fit 67 below 68", None, None),
                                               ("NEW", None, None, None), ("DUPLICATE", "dup_of_final_url", None, None), ("WEIRD", None, None, None)):
            self.vacancy(MSG_A, status, reason, page, verified)
        self.association(MSG_A, 999)                                                    # a source row whose job row is missing
        self.vacancy(MSG_B, "PUBLISHED", None, "page-9", SEEN)
        self.vacancy(MSG_B, "HOLD", "link_mismatch")

    def trace(self, ref, now=None):
        with mock.patch.object(store, "connect", lambda: self.db):
            return alertstate.trace(ref, now)

    def test_each_vacancy_in_saved_order_with_status_reason_ledger_linkage_verification_and_outcome(self):
        self.seed()
        got = self.trace(REF_A, dt.datetime(2026, 10, 17, 7, 30, 5, tzinfo=dt.timezone.utc))
        self.assertEqual(got["observed_at"], "2026-10-17T07:30:05Z")
        body = got["message_trace"]
        self.assertEqual([(v["n"], v["status"], v["reason"], v["outcome"], v["ledger_page"], v["verified"]) for v in body["vacancies"]], [
            (1, "PUBLISHED", "none", "terminal", True, True), (2, "PUBLISHED", "none", "unverified", True, False), (3, "HOLD", "http_N", "hold", False, False),
            (4, "EXCLUDED_FIT", "Fit_N_below_N", "terminal", False, False), (5, "NEW", "none", "in_flight", False, False), (6, "DUPLICATE", "dup_of_final_url", "terminal", False, False),
            (7, "other", "none", "other", False, False), (8, "none", "none", "unlinked", False, False)])
        self.assertEqual((body["associations"], body["terminal"], body["blockers"]), (8, 3, {"hold": 1, "in_flight": 1, "other": 1, "unlinked": 1, "unverified": 1}))
        self.assertEqual({v["source"] for v in body["vacancies"]}, {"jobright"})

    def test_only_the_exact_message_is_traced_and_a_near_miss_finds_nothing(self):
        self.seed()
        b = self.trace(REF_B)["message_trace"]
        self.assertEqual((b["associations"], b["terminal"], b["blockers"]), (2, 1, {"hold": 1}))
        for near in (MSG_A[:-1], "a" + MSG_A, "aaaaaaaa", MSG_A[:-1] + "b"):
            self.assertEqual(self.trace(hashlib.sha256(near.encode()).hexdigest())["message_trace"], {"associations": 0, "vacancies": [], "terminal": 0, "blockers": {}})

    def test_every_job_status_gets_the_outcome_finalize_gives_its_mail(self):
        self.assertEqual((alertstate.IN_FLIGHT, alertstate.TERMINAL), (finalize.IN_FLIGHT, finalize.TERMINAL))               # the copies jobs keeps (it cannot import sources) stay equal
        for status, reason, page, verified in (("NEW", None, None, None), ("RESOLVED", None, None, None), ("READY", None, None, None), ("HOLD", "x", None, None), ("PUBLISHED", None, "p", SEEN),
                                               ("PUBLISHED", None, "p", None), ("CLOSED", None, None, None), ("DUPLICATE", None, None, None), ("PURGED", None, None, None),
                                               ("EXCLUDED_FIT", None, None, None), ("EXCLUDED_UNRESOLVED", None, None, None), ("MYSTERY", None, None, None)):
            message = f"{self.n + 1:016x}"
            self.vacancy(message, status, reason, page, verified)
            outcome = self.trace(hashlib.sha256(message.encode()).hexdigest())["message_trace"]["vacancies"][0]["outcome"]
            self.assertEqual(outcome, finalize.blockers(self.db, [message]).get(message, "terminal"), (status, verified))

    def test_an_unlisted_source_status_or_reason_prints_as_other_and_never_as_its_own_text(self):
        self.vacancy(MSG_A, "SECRET_STATUS", "Secret free text 99 about Secret Co", None, None, source="secret-provider")
        self.vacancy(MSG_A, "HOLD", "closed: Secret Co said so", None, None, source="jobright")
        self.vacancy(MSG_A, "HOLD", "no_match_Secret+no_result", None, None, source="lensa")
        self.vacancy(MSG_A, "HOLD", "no_match_no_board+no_result", None, None, source="linkedin-alerts")
        got = self.trace(REF_A)["message_trace"]["vacancies"]
        self.assertEqual([(v["source"], v["status"], v["reason"]) for v in got], [("other", "other", "other"), ("jobright", "HOLD", "other"), ("lensa", "HOLD", "other"),
                                                                                  ("linkedin-alerts", "HOLD", "no_match_no_board+no_result")])
        for private in ("secret", "Secret"):
            self.assertNotIn(private, json.dumps(got))

    def test_it_cannot_write_and_leaves_every_table_as_it_was(self):
        self.seed()
        dump = lambda: {t: self.db.raw.execute(f"SELECT * FROM {t}").fetchall() for t in store.TABLES}      # noqa: E731
        before = dump()
        self.db.raw.execute("PRAGMA query_only = ON")                                                  # the engine itself refuses a write from here on
        self.trace(REF_A)
        self.db.raw.execute("PRAGMA query_only = OFF")
        self.assertEqual(dump(), before)
        seen = []

        class Cur:
            def execute(self, sql, args=None):
                seen.append(sql)

            def fetchall(self):
                return []
        for sql in ("UPDATE v7_jobs SET status='x'", "  delete FROM v7_jobs", "INSERT INTO v7_jobs VALUES (1)", "REPLACE INTO v7_jobs VALUES (1)", "SET SESSION x=1", "DROP TABLE v7_jobs"):
            with self.assertRaises(store.StoreError) as ctx:
                alertstate._select(Cur(), sql, ())
            self.assertEqual(str(ctx.exception), "ALERT_STATE_NOT_READ_ONLY")
        alertstate._select(Cur(), "select 1", ())
        self.assertEqual(seen, ["select 1"])                                                             # only the SELECT ever reached the database

    def test_the_output_carries_no_job_identity_message_id_or_selector(self):
        self.seed()
        text = json.dumps(self.trace(REF_A))
        for private in ("Secret", "secretkey", "secret.example", "hash", MSG_A, REF_A, "page-1", "page-2"):
            self.assertNotIn(private, text)

    def test_a_raw_gmail_id_or_any_malformed_selector_is_refused_before_the_database_is_touched(self):
        with mock.patch.object(store, "connect", side_effect=AssertionError("must not connect")):
            for bad in ("", MSG_A, "1a135d44a5da038f", REF_A.upper(), REF_A[:-1], REF_A + "0", REF_A[:-1] + "%", "a" * 63 + "'", REF_A[:32] + " " + REF_A[33:], REF_A + "\n"):
                with self.assertRaises(store.StoreError) as ctx:
                    alertstate.trace(bad)
                self.assertEqual(str(ctx.exception), "ALERT_STATE_MESSAGE_REF_INVALID", bad)

    def test_a_failed_or_unreadable_store_gives_a_fixed_code_and_never_driver_text(self):
        with mock.patch.object(store, "connect", side_effect=store.StoreError("STORE_SSH_TCP_TIMEOUT")):
            with self.assertRaises(store.StoreError) as ctx:
                alertstate.trace(REF_A)
            self.assertEqual(str(ctx.exception), "STORE_SSH_TCP_TIMEOUT")
        self.seed()
        self.db.fail_on = ("FROM v7_job_sources", 1)
        with self.assertRaises(store.StoreError) as ctx:
            self.trace(REF_A)
        self.assertEqual(str(ctx.exception), "ALERT_STATE_QUERY_FAILED")
        with mock.patch.object(store, "connect", side_effect=Exception("secret driver text")):
            with self.assertRaises(store.StoreError) as ctx:
                alertstate.trace(REF_A)
        self.assertEqual(str(ctx.exception), "ALERT_STATE_QUERY_FAILED")

    def test_malformed_rows_are_blockers_not_a_crash_or_a_false_terminal(self):
        rows = [("jobright", 1, None, None, 0, 0), ("secret", 1, 7, 7, 1, 1), ("jobright", 0, None, None, 0, 0)]

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return self

            def execute(self, sql, args=None):
                pass

            def fetchall(self):
                return rows
        with mock.patch.object(store, "connect", Conn):
            body = alertstate.trace(REF_A)["message_trace"]
        self.assertEqual([v["outcome"] for v in body["vacancies"]], ["malformed", "malformed", "unlinked"])
        self.assertEqual((body["terminal"], body["blockers"]), (0, {"malformed": 2, "unlinked": 1}))
        self.assertEqual([v["source"] for v in body["vacancies"]], ["jobright", "other", "jobright"])

    def test_the_stage_returns_only_the_trace_when_the_selector_is_set_and_the_old_report_otherwise(self):
        self.seed()
        with mock.patch.dict(os.environ, {"ALERTS_MESSAGE_REF": f" {REF_B} "}), mock.patch.object(store, "connect", lambda: self.db):
            got = alertstate.run(0, False)
        self.assertEqual(sorted(got), ["message_trace", "observed_at"])
        self.assertEqual(got["message_trace"]["associations"], 2)
        with mock.patch.dict(os.environ, {"ALERTS_MESSAGE_REF": ""}), mock.patch.object(store, "connect", side_effect=store.StoreError("STORE_CONFIG_MISSING:X")):
            with self.assertRaises(store.StoreError):
                alertstate.run(0, False)                                                                   # empty selector: the original report path, which opens the store as before


if __name__ == "__main__":
    unittest.main()
