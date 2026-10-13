import unittest
from datetime import datetime, timedelta
from unittest import mock

from lifeos.jobs import alerts as jobs_alerts, hold, store
from lifeos.jobs.resolve import stage as resolve_stage
from lifeos.platform import alerts, limits
from tests.kit.db import FakeConn
from tests.kit.jobsdb import JobsDB

NOW = datetime(2026, 10, 10, 12, 0)


def conn(due=3, no_reason=1, holding=9, updated=2):
    seen = []

    def handler(sql, args, cursor):
        seen.append((sql, args))
        if sql.startswith("SELECT COUNT(*), COALESCE"):
            cursor.row = (due, no_reason)
        elif sql.startswith("SELECT COUNT(*) FROM v7_jobs WHERE status='HOLD'"):
            cursor.row = (holding,)
        elif sql.startswith("SELECT COALESCE(unresolved_reason"):
            cursor.row = ("jd_thin", "lensa", holding)
        elif sql.startswith("UPDATE"):
            cursor.rowcount = updated

    return FakeConn(handler=handler), seen


class HoldExpiryTests(unittest.TestCase):
    def go(self, live, **kw):
        c, seen = conn(**kw)
        with mock.patch.object(hold.store, "connect", return_value=c), mock.patch.object(hold.store, "ensure_schema"):
            return hold.run(0, live, now=NOW), seen

    def test_dry_run_counts_what_is_due_and_writes_nothing(self):
        out, seen = self.go(False)
        self.assertEqual(out, {"on_hold": 9, "due": 3, "excluded": 0, "by_reason": {"jd_thin": 9}, "by_source": {"lensa": 9}, "by_source_reason": {"lensa": {"jd_thin": 9}}, "saved": False,
                               "recovery_candidates": 0, "recovered": 0})
        self.assertFalse([s for s, _ in seen if s.startswith("UPDATE")])

    def test_live_excludes_only_hold_jobs_older_than_seven_days_as_a_terminal_status(self):
        out, seen = self.go(True)
        self.assertEqual((out["due"], out["excluded"]), (3, 2))
        sql, args = [(s, a) for s, a in seen if s.startswith("UPDATE")][0]
        self.assertIn("WHERE status='HOLD' AND (updated_at < %s OR recovered_at IS NOT NULL)", sql)
        self.assertEqual(args[0], "EXCLUDED_UNRESOLVED")
        self.assertEqual(args[4], datetime(2026, 10, 3, 12, 0))                   # exactly seven days before now
        self.assertTrue(args[0].startswith("EXCLUDED_"))                         # the finalize stage treats EXCLUDED_* as an outcome

    def test_nothing_due_means_no_write_even_when_live(self):
        out, seen = self.go(True, due=0, no_reason=0)
        self.assertEqual(out["excluded"], 0)
        self.assertFalse([s for s, _ in seen if s.startswith("UPDATE")])

    def test_the_stage_is_registered_and_the_status_fits_the_column(self):
        from lifeos import run
        self.assertIn("expire-holds", run.STAGES)
        self.assertLessEqual(len(hold.EXCLUDED), 24)                              # v7_jobs.status is VARCHAR(24)


# D185: recovery, proved on real SQL (sqlite in MySQL dialect) through the production writers; no title, company or link leaves the store.
LINK = "https://boards.example.com/acme/jobs/4711"


class RecoveryDB(JobsDB):
    def job(self, job_id, status="HOLD", days_held=6.5, reason="no_match_title", lane="Newsletter", age_days=8, link=None, source="lensa", recovered=None):
        first = NOW - timedelta(days=age_days)
        self.insert("v7_jobs", id=job_id, dedupe_key=f"k{job_id}", status=status, title="Product Manager", company="Acme", source_url=f"https://aggregator.example.com/j/{job_id}",
                    final_apply_url=link, unresolved_reason=reason, first_seen=first, last_seen=first, updated_at=NOW - timedelta(days=days_held), lane=lane, source=source,
                    resolve_attempts=limits.RESOLVE_MAX_ATTEMPTS, recovered_at=recovered)

    def row(self, job_id):
        return self.raw.execute("SELECT status, unresolved_reason, final_apply_url, notion_page_id, resolve_attempts, recovered_at, dedupe_key FROM v7_jobs WHERE id=?", (job_id,)).fetchone()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.db = RecoveryDB()
        self.addCleanup(self.db.raw.close)

    def go(self, now=NOW, live=True):
        with mock.patch.object(hold.store, "connect", return_value=self.db), mock.patch.object(hold.store, "ensure_schema"):
            return hold.run(0, live, now=now)

    def test_an_unresolved_job_is_recovered_through_the_resolver_and_keeps_its_identity(self):
        self.db.job(1)
        self.db.job(2, reason="no_match_title")                                  # a sibling vacancy of the same employer that must not be touched by job 1's result
        self.db.raw.execute("UPDATE v7_jobs SET updated_at=? WHERE id=2", (NOW - timedelta(days=2),))
        out = self.go()
        self.assertEqual((out["recovery_candidates"], out["recovered"]), (1, 1))
        status, reason, final, page, attempts, recovered, key = self.db.row(1)
        self.assertEqual((status, final, page, recovered, key), ("NEW", None, None, NOW, "k1"))
        self.assertEqual(attempts, limits.RESOLVE_MAX_ATTEMPTS - 1)               # exactly one attempt left
        self.assertEqual([row[0] for row in resolve_stage.pick(self.db, "lensa", 10)], [1])      # the existing resolver picks it; the sibling is still on HOLD
        resolve_stage.apply_result(self.db, 1, {"outcome": "landed", "url": LINK, "kind": "ats"})
        self.assertEqual(self.db.row(1)[:3], ("RESOLVED", None, LINK))            # the normal path: Enrich still has to read the page and the JD before it can be READY
        self.assertEqual(self.db.row(2)[:3], ("HOLD", "no_match_title", None))

    def test_a_job_that_cannot_be_recovered_is_excluded_and_never_published(self):
        self.db.job(1)
        self.go()
        self.db.raw.execute("UPDATE v7_jobs SET status='HOLD', resolve_attempts=?, unresolved_reason='no_match_title', updated_at=? WHERE id=1",       # the production outcome of the failed attempt: the cap is reached, HOLD again
                            (limits.RESOLVE_MAX_ATTEMPTS, NOW + timedelta(hours=1)))
        out = self.go(NOW + timedelta(hours=1, minutes=5))
        self.assertEqual((out["excluded"], out["recovered"]), (1, 0))
        self.assertEqual(self.db.row(1)[:4], ("EXCLUDED_UNRESOLVED", "unresolved_after_recovery", None, None))
        self.assertEqual(self.db.raw.execute("SELECT COUNT(*) FROM v7_jobs WHERE status IN ('READY', 'PUBLISHED')").fetchone()[0], 0)

    def test_a_repeated_run_recovers_nothing_twice_and_a_failed_recovery_alerts_once(self):
        self.db.job(1)
        self.assertEqual(self.go()["recovered"], 1)
        self.assertEqual(self.go(NOW + timedelta(minutes=5))["recovered"], 0)      # status is NEW now, and recovered_at is set
        self.db.raw.execute("UPDATE v7_jobs SET status='HOLD', updated_at=? WHERE id=1", (NOW + timedelta(hours=1),))
        later = NOW + timedelta(hours=2)
        self.assertEqual(self.go(later)["recovered"], 0)                           # a HOLD job that was recovered once is never requeued again
        self.assertEqual(self.db.row(1)[0], "EXCLUDED_UNRESOLVED")
        counts = jobs_alerts.gather(self.db, later)
        self.assertEqual(counts["unresolved_terminal"], 1)
        active = jobs_alerts.detect(counts, later)
        first = alerts.reconcile(active, jobs_alerts.load_state(self.db), later)
        self.assertEqual([(kind, item.key) for kind, item in first.notify if item.key == "unresolved_terminal"], [("open", "unresolved_terminal")])
        self.assertEqual([a.detail[:19] for a in first.opened if a.key == "unresolved_terminal"], ["1 worthwhile job(s)"])   # a count, never a title, company, link or id
        self.db.raw.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES ('unresolved_terminal', 'info', 'OPEN', ?, ?, ?)", (later, later, later))
        repeat_at = later + timedelta(minutes=30)
        self.go(repeat_at)
        again = alerts.reconcile(jobs_alerts.detect(jobs_alerts.gather(self.db, repeat_at), repeat_at), jobs_alerts.load_state(self.db), repeat_at)
        self.assertEqual([item.key for _, item in again.notify if hasattr(item, 'key') and item.key == 'unresolved_terminal'], [])                                         # the same state on the next run is neither a new alert nor a reminder

    def test_closed_stale_early_and_late_holds_are_not_recovered_and_the_seven_day_rule_is_unchanged(self):
        self.db.job(1, reason="closed_by_employer")                                # an explicit closed outcome
        self.db.job(2, age_days=20)                                                # past the US Remote 14-day window: Fit would reject it anyway
        self.db.job(3, days_held=5)                                                # not yet day 6
        self.db.job(4, days_held=8)                                                # already past the seven days: excluded exactly as before
        self.db.job(5, lane="Nowhere")                                             # an unknown lane is not worthwhile
        self.db.job(7, source="web:su-example")                                    # no link and no resolve stage reads this source: a NEW job would wait for nothing, so it is not requeued
        out = self.go()
        self.assertEqual(out["recovered"], 0)
        self.assertEqual([self.db.row(i)[0] for i in (1, 2, 3, 4, 5, 7)], ["HOLD", "HOLD", "HOLD", "EXCLUDED_UNRESOLVED", "HOLD", "HOLD"])
        self.assertEqual(self.db.row(4)[1], "hold_expired")
        self.assertIsNone(self.db.row(1)[5])

    def test_a_job_with_a_link_is_recovered_whatever_its_source_and_every_resolved_source_has_a_resolve_stage(self):
        self.db.job(1, source="web:su-example", link=LINK, reason="jd_thin")      # the page was unreadable: Enrich reads every RESOLVED job, whatever its source
        self.assertEqual(self.go()["recovered"], 1)
        self.assertEqual(self.db.row(1)[0], "RESOLVED")
        import inspect
        from lifeos import run
        from lifeos.jobs.resolve.aggregators import dice
        stages = inspect.getsource(run) + inspect.getsource(dice)
        for source in hold.RESOLVED_BY:                                           # the guard names exactly the sources whose NEW jobs a resolve stage reads
            self.assertIn(f'"{source}"', stages)

    def test_recovery_is_bounded_per_run(self):
        for job_id in range(1, hold.RECOVER_PER_RUN + 6):
            self.db.job(job_id)
        self.assertEqual(self.go()["recovered"], hold.RECOVER_PER_RUN)
        self.assertEqual(self.go()["recovered"], 5)                                # the rest on the next tick

    def test_recovery_drains_a_large_window_at_the_bounded_rate_and_stale_holds_do_not_crowd_it(self):
        for job_id in range(1, 301):
            self.db.job(job_id, age_days=30, days_held=6.9)                          # 300 stale holds first in line (the oldest updated_at): never worthwhile
        for job_id in range(301, 361):
            self.db.job(job_id, days_held=6.5)                                       # 60 worthwhile holds behind them
        got = [self.go(NOW + timedelta(minutes=5 * tick))["recovered"] for tick in range(4)]
        self.assertEqual(got, [25, 25, 10, 0])                                       # bounded per tick, each job once, drained in three ticks
        recovered = self.db.raw.execute("SELECT COUNT(*), MIN(id) FROM v7_jobs WHERE recovered_at IS NOT NULL").fetchone()
        self.assertEqual(recovered, (60, 301))

    def test_the_production_entry_points_recover_exclude_and_alert_over_the_real_store(self):
        """`python -m lifeos.run expire-holds --live` twice (a failed attempt in between), then the alerts stage: the same composition production runs, counts only."""
        import contextlib
        import io
        import json
        from lifeos import run
        from lifeos.jobs import retention
        self.db.job(1)

        def stage(now):
            out = io.StringIO()
            with mock.patch.object(hold.store, "connect", return_value=self.db), mock.patch.object(hold.store, "ensure_schema"), \
                    mock.patch.object(retention, "_now", return_value=now), contextlib.redirect_stdout(out):
                self.assertEqual(run.main(["expire-holds", "--live"]), 0)
            self.assertFalse([word for word in ("Acme", "Product Manager", "example.com") if word in out.getvalue()])      # counts only: no company, title or link in the log
            return json.loads(out.getvalue().split(":", 1)[1])

        self.assertEqual(stage(NOW)["recovered"], 1)
        self.assertEqual(self.db.row(1)[0], "NEW")
        self.db.raw.execute("UPDATE v7_jobs SET status='HOLD', updated_at=? WHERE id=1", (NOW + timedelta(hours=1),))        # the failed attempt, as the resolver leaves it
        later = NOW + timedelta(hours=2)
        counts = stage(later)
        self.assertEqual((counts["recovered"], counts["excluded"]), (0, 1))
        with mock.patch.object(hold.store, "connect", return_value=self.db), mock.patch.object(hold.store, "ensure_schema"):
            report = jobs_alerts.run(0, False, now=later)
        self.assertEqual(report["active"], 2)                                      # quiet_24h and unresolved_terminal in an otherwise empty store


if __name__ == "__main__":
    unittest.main()
