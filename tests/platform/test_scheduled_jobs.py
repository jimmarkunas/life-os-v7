"""LIFE-627: the scheduled-jobs view. Recorded input (tests/fixtures/scheduled_jobs_runs.json: real run history of four workflows, trimmed to six runs and the fields used, Nov 12 2026) is replayed."""
import datetime as dt
import json
import re
import unittest
from pathlib import Path

from lifeos.platform import gate, scheduled_jobs as sj

ROOT = Path(__file__).resolve().parents[2]
RECORDED = json.loads((ROOT / "tests/fixtures/scheduled_jobs_runs.json").read_text())
NOW = dt.datetime(2026, 11, 13, 0, 30, tzinfo=dt.timezone.utc)           # 6:30 PM CT, five minutes after the newest recorded watchdog run
JOBS = {j["job"]: j for j in sj.load()["jobs"]}


def row(now, runs=RECORDED):
    return {r["job"]: r for r in sj.evaluate(now, runs)}


def at(minutes_ago, **extra):
    return {"created_at": (NOW - dt.timedelta(minutes=minutes_ago)).isoformat(), "updated_at": (NOW - dt.timedelta(minutes=minutes_ago - 5)).isoformat(), "status": "completed", "conclusion": "success", "display_title": "tick", **extra}


class Recorded(unittest.TestCase):
    def test_the_recorded_history_reads_on_time_with_the_unreadable_jobs_unknown(self):
        rows = row(NOW)
        self.assertEqual({k: v["state"] for k, v in rows.items()}, {
            "hourly run": sj.ON_TIME, "watchdog": sj.ON_TIME, "weekly safety check": sj.ON_TIME, "domain jobs": sj.ON_TIME, "ticker watch": sj.UNKNOWN,
            "off-site backup": sj.UNKNOWN, "Close release": sj.UNKNOWN, "iMessage attention": sj.UNKNOWN, "iMessage relay": sj.UNKNOWN})
        self.assertEqual(sj.late_names(list(rows.values())), [])

    def test_the_same_history_two_hours_on_reads_the_hourly_run_late_then_missing_and_the_watchdog_late(self):
        later = row(NOW + dt.timedelta(minutes=80))                                  # newest tick 6:17 PM CT, now 7:50 PM CT: 93 minutes
        self.assertEqual((later["hourly run"]["state"], later["watchdog"]["state"]), (sj.LATE, sj.ON_TIME))
        later = row(NOW + dt.timedelta(minutes=140))
        self.assertEqual((later["hourly run"]["state"], later["watchdog"]["state"]), (sj.MISSING, sj.LATE))
        self.assertEqual(sj.late_names(list(later.values())), ["hourly run", "watchdog"])

    def test_times_print_in_central_time_and_the_weekly_slot_follows_the_clock_change(self):
        text = sj.render(sj.evaluate(NOW, RECORDED))
        self.assertIn("Nov 12, 6:17 PM CT", text)
        self.assertIn("Monday 7:17 AM CST", text)                                    # November 9: standard time
        self.assertIn("Monday 8:17 AM CDT", row(dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc))["weekly safety check"]["schedule"])


class Rules(unittest.TestCase):
    def test_weekly_not_due_late_missing_and_on_time(self):
        job = [JOBS["weekly safety check"]]
        monday = dt.datetime(2026, 11, 9, 13, 17, tzinfo=dt.timezone.utc)
        for hours, last, want in ((1, None, sj.NOT_DUE), (13, None, sj.LATE), (25, None, sj.MISSING), (13, "2026-11-09T14:00:00+00:00", sj.ON_TIME), (2, "2026-11-02T14:00:00+00:00", sj.NOT_DUE)):
            runs = {"safety.yml": [{"created_at": last}] if last else []}
            self.assertEqual(sj.evaluate(monday + dt.timedelta(hours=hours), runs, job)[0]["state"], want, hours)

    def test_domains_are_not_due_for_30_minutes_after_a_tick_then_late_and_ignore_cancelled_ticks(self):
        job = [JOBS["domain jobs"]]
        tick = at(20)
        self.assertEqual(sj.evaluate(NOW, {"hourly.yml": [tick], "domains.yml": []}, job)[0]["state"], sj.NOT_DUE)
        self.assertEqual(sj.evaluate(NOW, {"hourly.yml": [at(40)], "domains.yml": []}, job)[0]["state"], sj.LATE)
        self.assertEqual(sj.evaluate(NOW, {"hourly.yml": [at(40)], "domains.yml": [at(30)]}, job)[0]["state"], sj.ON_TIME)
        self.assertEqual(sj.evaluate(NOW, {"hourly.yml": [at(40, conclusion="cancelled")], "domains.yml": []}, job)[0]["state"], sj.UNKNOWN)

    def test_no_run_at_all_is_missing_and_an_unreadable_history_is_unknown_never_a_guess(self):
        self.assertEqual(sj.evaluate(NOW, {"watchdog.yml": []}, [JOBS["watchdog"]])[0]["state"], sj.MISSING)
        self.assertEqual(sj.evaluate(NOW, {"watchdog.yml": None}, [JOBS["watchdog"]])[0]["state"], sj.UNKNOWN)

    def test_fetch_reads_each_workflow_once_and_marks_a_failed_read_unreadable(self):
        seen = []

        def api(path):
            seen.append(path)
            if "safety" in path:
                raise OSError("down")
            return {"workflow_runs": [1]}
        runs = sj.fetch(api)
        self.assertEqual(sorted(runs), ["domains.yml", "hourly.yml", "safety.yml", "watchdog.yml"])
        self.assertIsNone(runs["safety.yml"])
        self.assertEqual(len(seen), 4)

    def test_the_daily_check_names_late_jobs_and_says_nothing_when_none_are(self):
        stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
        runs = [{"created_at": stamp, "updated_at": stamp, "display_title": gate.TICK_TITLE, "conclusion": "success"}]
        self.assertNotIn("Late", gate.health(runs, [], NOW)[1])
        self.assertIn("Late or missing scheduled jobs (2): hourly run, watchdog.", gate.health(runs, [], NOW, late=["hourly run", "watchdog"])[1])


class Coverage(unittest.TestCase):
    """A job that starts on a clock and has no row fails here."""

    def test_every_scheduled_workflow_and_every_timer_or_launchd_unit_has_a_row(self):
        jobs = sj.load()["jobs"]
        sources = {j.get("source") for j in jobs}
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            if re.search(r"^\s+schedule:", path.read_text(), re.M):
                self.assertIn(path.name, sources, path.name)
        units = {j.get("unit") for j in jobs}
        wanted = {p.name for p in (ROOT / "lifeos").rglob("*.timer")} | {m["unit"] for m in json.loads((ROOT / "lifeos/platform/runtime.json").read_text())["modules"] if m.get("unit", "").endswith(".timer") or ("mac" in m["host"] and "unit" in m)}
        self.assertEqual(wanted - units, set(), "a scheduled unit has no row in lifeos/platform/scheduled_jobs.json")

    def test_every_row_has_a_known_rule_and_host_and_the_tolerances_it_needs(self):
        need = {"gap": ("source", "late", "missing"), "weekly": ("source", "weekday", "utc", "late", "missing"), "after": ("source", "after", "late"), "unknown": ("unit",)}
        for job in sj.load()["jobs"]:
            self.assertIn(job["rule"], need, job["job"])
            self.assertTrue(set(need[job["rule"]]) <= set(job), job["job"])
            self.assertTrue(set(job["host"].replace(" ", "").split(",")) <= {"github", "oracle", "mac"}, job["job"])


if __name__ == "__main__":
    unittest.main()
