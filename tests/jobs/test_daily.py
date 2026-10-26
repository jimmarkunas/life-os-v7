"""LIFE-74 step 2: the daily counts (board reads, search calls, the HOLD pile) and the monitors that read them."""
import datetime as dt
import unittest
from unittest import mock

from lifeos import run
from lifeos.jobs import alerts as jobs_alerts, daily
from lifeos.platform import alerts
from tests.kit.jobsdb import JobsDB

NOW = dt.datetime(2026, 10, 26, 12, 0)
TODAY = NOW.date()
QUIET = {"published_24h": 5, "sources_failing": 0, "stuck_jobs": 0}


def iso(back):
    return (TODAY - dt.timedelta(days=back)).isoformat()


class Deltas(unittest.TestCase):
    def test_board_stages_count_reads_and_failures_other_than_not_found(self):
        counts = {"complete": 250, "failed": 12, "failed_why": {"not_found": 9, "rate_limited_429": 3}}
        for stage in ("boards", "boards-backfill", "web", "web-scale-up"):
            self.assertEqual(daily.deltas(stage, counts), {"board_reads": 262, "board_failed": 3})

    def test_search_stages_count_calls_and_failures(self):
        self.assertEqual(daily.deltas("discover-jobs", {"searched": 10, "search_errors": 2}), {"search_calls": 12, "search_failed": 2})
        self.assertEqual(daily.deltas("web-titlewatch", {"queries": 40, "errors": 5}), {"search_calls": 40, "search_failed": 5})

    def test_other_stages_and_odd_counts_give_nothing(self):
        self.assertEqual(daily.deltas("publish", {"created": 5}), {})
        self.assertEqual(daily.deltas("boards", None), {})
        self.assertEqual(daily.deltas("boards", {"complete": "x", "failed": None, "failed_why": []}), {"board_reads": 0, "board_failed": 0})


class Record(unittest.TestCase):
    def setUp(self):
        self.db = JobsDB()
        self.addCleanup(self.db.raw.close)
        self.connect = mock.MagicMock(return_value=self.db)

    def rows(self):
        return {(str(d), n): v for d, n, v in self.db.raw.execute("SELECT day, name, value FROM v7_daily_counts").fetchall()}

    def test_two_stage_runs_add_up_in_one_row_per_name_and_day(self):
        now = dt.datetime(2026, 10, 26, 3, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(daily.record("boards", {"complete": 100, "failed": 10, "failed_why": {"not_found": 4}}, self.connect, now), {"board_reads": 110, "board_failed": 6})
        daily.record("boards-backfill", {"complete": 50, "failed": 0, "failed_why": {}}, self.connect, now)
        self.assertEqual(self.rows(), {("2026-10-26", "board_reads"): 160, ("2026-10-26", "board_failed"): 6})

    def test_a_stage_with_nothing_to_record_does_not_open_the_store(self):
        self.assertEqual(daily.record("publish", {"created": 3}, self.connect), {})
        self.connect.assert_not_called()

    def test_a_store_failure_never_raises(self):
        self.assertEqual(daily.record("boards", {"complete": 5, "failed": 1}, mock.Mock(side_effect=RuntimeError("down"))), {})

    def test_the_stage_runner_records_only_live_runs(self):
        with mock.patch.dict(run.STAGES, {"boards": lambda limit, live: {"complete": 9, "failed": 1, "failed_why": {}}}), mock.patch.object(daily, "record") as record:
            run.main(["boards"])
            record.assert_not_called()
            run.main(["boards", "--live"])
            record.assert_called_once_with("boards", {"complete": 9, "failed": 1, "failed_why": {}})

    def test_the_snapshot_keeps_today_and_drops_rows_past_the_keep_window(self):
        self.db.insert("v7_daily_counts", day=TODAY - dt.timedelta(days=20), name="board_reads", value=5)
        for n, age_hours in enumerate((1, 30, 200, 300)):
            self.db.insert("v7_jobs", id=n + 1, dedupe_key=f"k{n}", status="HOLD", title="x", company="y", first_seen=NOW, last_seen=NOW, updated_at=NOW - dt.timedelta(hours=age_hours))
        jobs_alerts.snapshot_hold(self.db, NOW)
        self.assertEqual(self.rows(), {(iso(0), "hold_total"): 4, (iso(0), "hold_touched_24h"): 1})
        jobs_alerts.snapshot_hold(self.db, NOW)                                             # the last run of the day wins; nothing doubles
        self.assertEqual(self.rows()[(iso(0), "hold_total")], 4)


class DailyMonitors(unittest.TestCase):
    def rows(self, **series):
        return {name: {iso(back): v for back, v in days.items()} for name, days in series.items()}

    def test_board_reads_failing_needs_100_reads_and_a_fifth_failed(self):
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(board_reads={0: 99}, board_failed={0: 99}), TODAY), {})
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(board_reads={0: 60, 1: 40}, board_failed={0: 10, 1: 9}), TODAY), {})          # 19%
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(board_reads={0: 60, 1: 40}, board_failed={0: 10, 1: 10}), TODAY), {"board_reads_failing": 0.2})

    def test_search_failing_needs_20_calls_and_a_fifth_failed(self):
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(search_calls={0: 19}, search_failed={0: 19}), TODAY), {})
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(search_calls={0: 20}, search_failed={0: 4}), TODAY), {"search_failing": 0.2})
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(search_calls={0: 20}, search_failed={0: 3}), TODAY), {})

    def test_hold_rising_needs_three_rising_days_a_fifth_more_and_25_jobs(self):
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(hold_total={2: 30, 1: 34, 0: 40}), TODAY), {"hold_rising": [30, 34, 40]})
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(hold_total={2: 30, 1: 30, 0: 40}), TODAY), {})            # not strictly rising
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(hold_total={2: 30, 1: 31, 0: 33}), TODAY), {})            # under a fifth more
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(hold_total={2: 10, 1: 14, 0: 20}), TODAY), {})            # under 25 jobs
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(hold_total={1: 34, 0: 40}), TODAY), {})                    # a day of evidence is missing

    def test_hold_not_retried_needs_a_large_growing_pile_and_nothing_touched(self):
        rows = self.rows(hold_total={2: 30, 1: 33, 0: 40}, hold_touched_24h={0: 0})
        self.assertEqual(jobs_alerts.daily_monitors(rows, TODAY)["hold_not_retried"], 40)
        rows["hold_touched_24h"] = {iso(0): 1}
        self.assertNotIn("hold_not_retried", jobs_alerts.daily_monitors(rows, TODAY))
        flat = self.rows(hold_total={2: 40, 1: 40, 0: 40}, hold_touched_24h={0: 0})
        self.assertNotIn("hold_not_retried", jobs_alerts.daily_monitors(flat, TODAY))                                      # not growing

    def test_the_hold_pile_measured_on_oct_25_trips_nothing_without_three_days_of_evidence(self):
        self.assertEqual(jobs_alerts.daily_monitors(self.rows(hold_total={0: 114}, hold_touched_24h={0: 0}), TODAY), {})

    def test_detect_raises_info_alerts_with_stable_keys(self):
        rows = self.rows(board_reads={0: 200}, board_failed={0: 80}, search_calls={0: 30}, search_failed={0: 15}, hold_total={2: 30, 1: 36, 0: 44}, hold_touched_24h={0: 0})
        got = {a.key: a for a in jobs_alerts.detect({**QUIET, "daily": rows}, NOW)}
        self.assertEqual(sorted(got), ["board_reads_failing", "hold_not_retried", "hold_rising", "search_failing"])
        self.assertTrue(all(a.severity == alerts.INFO for a in got.values()))

    def test_healthy_daily_counts_raise_nothing(self):
        rows = self.rows(board_reads={0: 900, 1: 2500}, board_failed={0: 20, 1: 50}, search_calls={0: 40, 1: 90}, search_failed={0: 2, 1: 3}, hold_total={2: 60, 1: 58, 0: 55}, hold_touched_24h={0: 12})
        self.assertEqual(jobs_alerts.detect({**QUIET, "daily": rows}, NOW), [])

    def test_a_replayed_failing_week_opens_each_alert_exactly_once(self):
        state, opened = {}, []
        for day in range(7):                                                                  # board failures climb from day 3; the HOLD pile grows from day 4
            now = NOW + dt.timedelta(days=day - 6)
            rows = {"board_reads": {(now.date() - dt.timedelta(days=b)).isoformat(): 300 for b in range(2)},
                    "board_failed": {(now.date() - dt.timedelta(days=b)).isoformat(): (90 if day >= 3 else 5) for b in range(2)},
                    "hold_total": {(now.date() - dt.timedelta(days=b)).isoformat(): 30 + 5 * (day - b) if day >= 4 and day - b >= 2 else 30 for b in range(3)}}
            plan = alerts.reconcile(jobs_alerts.detect({**QUIET, "daily": rows}, now), state, now)
            opened += [a.key for a in plan.opened]
            for alert in plan.opened:
                state[alert.key] = {"severity": alert.severity, "last_notified": now}
        self.assertEqual(opened.count("board_reads_failing"), 1)
        self.assertEqual(opened.count("search_failing"), 0)


class LiveStage(unittest.TestCase):
    def test_the_live_stage_snapshots_the_pile_reads_the_counts_and_opens_each_alert_once(self):
        db = JobsDB()
        self.addCleanup(db.raw.close)
        for back, n in ((1, 200), (0, 100)):
            db.insert("v7_daily_counts", day=TODAY - dt.timedelta(days=back), name="board_reads", value=n)
            db.insert("v7_daily_counts", day=TODAY - dt.timedelta(days=back), name="board_failed", value=n // 2)
        sent = []
        with mock.patch.object(jobs_alerts.store, "connect", return_value=db), mock.patch.object(jobs_alerts.store, "ensure_schema"):
            first = jobs_alerts.run(0, True, environ={"NTFY_TOPIC": "t"}, now=NOW, send=lambda *a: sent.append(a) or True)
            second = jobs_alerts.run(0, True, environ={"NTFY_TOPIC": "t"}, now=NOW, send=lambda *a: sent.append(a) or True)
        self.assertEqual(first["monitors"]["board_reads_failing"], 0.5)
        keys = {row[0] for row in db.raw.execute("SELECT alert_key FROM v7_alerts WHERE status='OPEN'").fetchall()}
        self.assertIn("board_reads_failing", keys)
        self.assertEqual(sum(1 for a in sent if a[1] == "Board reads failing"), 1)             # told once, not on the second run
        self.assertEqual(second["opened"], 0)
        self.assertEqual(db.raw.execute("SELECT value FROM v7_daily_counts WHERE name='hold_total'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
