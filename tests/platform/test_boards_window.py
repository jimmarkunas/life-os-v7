"""LIFE-854: the four employer-board windows. Clock and fixture tests only: no network, no real clock.

Windows are 1:00 AM, 7:00 AM, 1:00 PM and 7:00 PM Central. The workflow carries two UTC cron lines per window (one for standard time, one for daylight time); this gate decides which firing owns the window, so a duplicate
firing is skipped, a missed or failed window makes the next one a catch-up, and an unreadable history never stops a window."""
import datetime as dt
import pathlib
import re
import unittest

from lifeos.platform import boards_window as bw, limits

ROOT = pathlib.Path(__file__).resolve().parents[2]
UTC = dt.timezone.utc


def utc(*parts):
    return dt.datetime(*parts, tzinfo=UTC)


def run(rid, started, outcome="success", event="schedule", job="boards", title="", status="completed"):
    """One workflow run of boards.yml and the conclusion of its crawl job (None: the job never ran)."""
    return {"id": rid, "event": event, "status": status, "display_title": title, "run_started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "created_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "_job": outcome, "_name": job}


def jobs_of(item):
    return [{"name": item["_name"], "conclusion": item["_job"], "status": "completed"}]


class Slots(unittest.TestCase):
    def test_the_four_windows_in_winter_and_summer(self):
        winter = [bw.current_slot(utc(2026, 11, 22, h, 5)) for h in (7, 13, 19, 1)]                # CST = UTC-6
        self.assertEqual(winter, [utc(2026, 11, 22, 7), utc(2026, 11, 22, 13), utc(2026, 11, 22, 19), utc(2026, 11, 22, 1)])
        summer = [bw.current_slot(utc(2026, 7, 22, h, 5)) for h in (6, 12, 18, 0)]                  # CDT = UTC-5
        self.assertEqual(summer, [utc(2026, 7, 22, 6), utc(2026, 7, 22, 12), utc(2026, 7, 22, 18), utc(2026, 7, 22, 0)])

    def test_a_moment_belongs_to_the_latest_window_that_has_opened(self):
        self.assertEqual(bw.current_slot(utc(2026, 11, 22, 6, 59)), utc(2026, 11, 22, 1))          # 12:59 AM CT: the 7 PM window of Nov 21 (01:00 UTC)
        self.assertEqual(bw.current_slot(utc(2026, 11, 22, 7, 0)), utc(2026, 11, 22, 7))
        self.assertEqual(bw.previous_slot(utc(2026, 11, 22, 7)), utc(2026, 11, 22, 1))

    def test_labels_are_central_twelve_hour(self):
        self.assertEqual(bw.label(utc(2026, 11, 22, 19)), "1:00 PM CT")
        self.assertEqual(bw.label(utc(2026, 11, 22, 1)), "7:00 PM CT")

    def test_every_cron_pair_has_exactly_one_firing_on_the_window_boundary_on_every_day_of_the_year(self):
        crons = cron_hours()
        self.assertEqual(len(crons), 8)
        for start in (dt.date(2026, 1, 1), dt.date(2027, 1, 1)):
            for offset in range(365):
                day = start + dt.timedelta(days=offset)
                on_boundary = 0
                for hour in crons:
                    moment = dt.datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)
                    on_boundary += bw.current_slot(moment) == moment
                self.assertEqual(on_boundary, 4, day)                                              # one firing per window opens it; the other is an hour off


def cron_hours():
    """The eight UTC hours of the cron lines of .github/workflows/boards.yml."""
    text = (ROOT / ".github/workflows/boards.yml").read_text()
    return sorted(int(m.group(1)) for m in re.finditer(r'^\s+- cron: "0 (\d+) \* \* \*"', text, re.M))


class Decide(unittest.TestCase):
    NOW = utc(2026, 11, 22, 7, 2)                                   # 1:02 AM CT, the 1 AM window

    def decide(self, runs, now=None):
        return bw.decide(now or self.NOW, runs, jobs_of)

    def test_a_clean_window_runs_with_the_window_cap_and_timeout(self):
        prev = run(1, utc(2026, 11, 22, 1, 3))                       # last night's 7 PM window ran
        got = self.decide([prev])
        self.assertEqual((got["run"], got["mode"], got["limit"], got["timeout_minutes"]), (True, "window", limits.BOARDS_PER_RUN, 15))
        self.assertEqual(got["late_reason"], "")

    def test_the_first_window_ever_has_no_history_and_is_a_normal_window(self):
        got = self.decide([])
        self.assertEqual((got["run"], got["mode"], got["late_reason"]), (True, "window", ""))

    def test_the_other_cron_of_the_same_window_is_a_duplicate_and_is_skipped(self):
        first = run(2, utc(2026, 11, 22, 7, 1))
        got = self.decide([first, run(1, utc(2026, 11, 22, 1, 3))], now=utc(2026, 11, 22, 8, 4))   # 2:04 AM CT, an hour on
        self.assertEqual((got["run"], got["why"]), (False, "window_already_ran"))

    def test_a_firing_before_the_window_opens_is_a_duplicate_of_the_window_that_ran(self):
        got = self.decide([run(1, utc(2026, 11, 22, 1, 3))], now=utc(2026, 11, 22, 6, 1))       # 12:01 AM CT in winter: still the 7 PM window, which ran
        self.assertEqual((got["run"], got["why"]), (False, "window_already_ran"))

    def test_a_missed_window_makes_the_next_one_a_catch_up_with_leftovers_first_caps(self):
        got = self.decide([run(1, utc(2026, 11, 20, 19, 2))], now=utc(2026, 11, 21, 13, 2))        # the 7 AM and 1 PM... history stops the day before: the previous window never ran
        self.assertEqual((got["mode"], got["limit"], got["timeout_minutes"]), ("catchup", limits.BOARDS_CATCHUP_PER_RUN, 25))
        self.assertIn("previous_window_missed", got["late_reason"])

    def test_a_failed_or_timed_out_previous_window_also_makes_a_catch_up(self):
        for outcome in ("failure", "timed_out"):
            got = self.decide([run(1, utc(2026, 11, 22, 1, 3), outcome)])
            self.assertEqual(got["mode"], "catchup", outcome)
            self.assertIn("previous_window_incomplete", got["late_reason"])

    def test_a_window_whose_crawl_job_never_ran_does_not_count_as_having_run(self):
        skipped = run(1, utc(2026, 11, 22, 1, 3), outcome="skipped")
        older = run(5, utc(2026, 11, 20, 19, 3))
        got = self.decide([skipped, older])
        self.assertEqual((got["run"], got["mode"]), (True, "catchup"))                             # the skipped run did not hold its window

    def test_cancelled_and_manual_runs_never_hold_a_window(self):
        manual = run(3, utc(2026, 11, 22, 7, 1), event="workflow_dispatch")
        cancelled = run(2, utc(2026, 11, 22, 7, 1), outcome="cancelled")
        got = self.decide([manual, cancelled, run(1, utc(2026, 11, 22, 1, 3))])
        self.assertEqual((got["run"], got["mode"]), (True, "window"))

    def test_a_late_start_is_named(self):
        got = self.decide([run(1, utc(2026, 11, 22, 1, 3))], now=utc(2026, 11, 22, 8, 10))          # the window's first cron never fired: 2:10 AM CT
        self.assertEqual((got["run"], got["mode"]), (True, "window"))
        self.assertIn("started_70_min_after_the_window_opened", got["late_reason"])

    def test_an_unreadable_history_runs_a_normal_window_and_says_so(self):
        def broken(_item):
            raise OSError("down")
        got = bw.decide(self.NOW, [run(1, utc(2026, 11, 22, 1, 3))], broken)
        self.assertEqual((got["run"], got["mode"], got["late_reason"]), (True, "window", "history_unreadable"))

    def test_the_deadline_leaves_room_for_the_receipt_inside_the_step_timeout(self):
        for mode, timeout in (("window", 15), ("catchup", 25)):
            got = bw.plan(utc(2026, 11, 22, 7), mode, "")
            self.assertEqual(got["timeout_minutes"], timeout)
            self.assertLess(got["deadline_minutes"], timeout)
            self.assertGreaterEqual(timeout - got["deadline_minutes"], 3)

    def test_duplicate_and_catch_up_caps_are_the_approved_numbers(self):
        self.assertEqual((limits.BOARDS_PER_RUN, limits.BOARDS_CATCHUP_PER_RUN), (1800, 2900))


class Auto(unittest.TestCase):
    """The hourly tick starts the window (GitHub's own cron never fired for boards.yml): a run it started holds its window, a manual one does not, and a window being crawled is not started twice."""
    NOW = utc(2026, 11, 22, 13, 5)                                   # 7:05 AM CT

    def test_a_run_the_tick_started_holds_its_window_and_a_manual_run_holds_nothing(self):
        auto = run(2, utc(2026, 11, 22, 13, 1), event="workflow_dispatch", title="boards workflow_dispatch auto")
        manual = run(3, utc(2026, 11, 22, 13, 2), event="workflow_dispatch", title="boards workflow_dispatch")
        self.assertEqual(bw.decide(self.NOW, [auto, run(1, utc(2026, 11, 22, 7, 1))], jobs_of)["why"], "window_already_ran")
        self.assertTrue(bw.decide(self.NOW, [manual, run(1, utc(2026, 11, 22, 7, 1))], jobs_of)["run"])

    def test_a_window_still_being_crawled_is_not_started_twice(self):
        going = run(2, utc(2026, 11, 22, 13, 1), event="workflow_dispatch", title="boards workflow_dispatch auto", status="in_progress", outcome=None)
        self.assertFalse(bw.decide(self.NOW, [going], jobs_of)["run"])

    def test_dispatch_starts_the_window_with_the_decided_mode_and_says_so(self):
        posts, out = [], []

        def api(path, method="GET", body=None):
            if method == "POST":
                posts.append((path, body))
                return {}
            return {"jobs": [{"name": "boards", "conclusion": "success", "status": "completed"}]} if "/jobs" in path else {"workflow_runs": [run(1, utc(2026, 11, 22, 7, 1))]}
        self.assertEqual(bw.dispatch({"GITHUB_REF_NAME": "main"}, api=api, now=self.NOW, out=out.append), 0)
        self.assertEqual(posts, [("actions/workflows/boards.yml/dispatches", {"ref": "main", "inputs": {"auto": "true", "mode": "window"}})])
        self.assertIn("started the 7:00 AM CT window", out[0])

    def test_dispatch_starts_a_catch_up_after_a_missed_window(self):
        posts = []

        def api(path, method="GET", body=None):
            if method == "POST":
                posts.append(body)
                return {}
            return {"jobs": [{"name": "boards", "conclusion": "success", "status": "completed"}]} if "/jobs" in path else {"workflow_runs": [run(1, utc(2026, 11, 21, 19, 1))]}
        bw.dispatch({}, api=api, now=self.NOW, out=lambda _t: None)
        self.assertEqual(posts[0]["inputs"], {"auto": "true", "mode": "catchup"})

    def test_dispatch_starts_nothing_when_the_window_has_a_run(self):
        posts = []

        def api(path, method="GET", body=None):
            if method == "POST":
                posts.append(body)
            return {"jobs": [{"name": "boards", "conclusion": "success", "status": "completed"}]} if "/jobs" in path else {"workflow_runs": [run(2, utc(2026, 11, 22, 13, 1))]}
        bw.dispatch({}, api=api, now=self.NOW, out=lambda _t: None)
        self.assertEqual(posts, [])

    def test_a_failed_dispatch_never_fails_the_tick(self):
        def api(path, method="GET", body=None):
            if method == "POST":
                raise OSError("refused")
            return {"workflow_runs": []}
        out = []
        self.assertEqual(bw.dispatch({}, api=api, now=self.NOW, out=out.append), 0)
        self.assertIn("dispatch failed", out[0])

    def test_an_auto_run_decides_like_a_scheduled_one_and_a_manual_run_always_runs(self):
        text = (ROOT / ".github/workflows/boards.yml").read_text()
        self.assertIn("inputs.auto", text)
        self.assertIn("BOARDS_AUTO", text)


class Main(unittest.TestCase):
    def run_main(self, environ, runs):
        out, summary = [], []

        def api(path):
            if "/jobs" in path:
                return {"jobs": jobs_of(next(r for r in runs if f"/runs/{r['id']}/" in path))}
            return {"workflow_runs": runs}
        written = {}
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            environ = {**environ, "GITHUB_OUTPUT": f"{tmp}/out", "GITHUB_STEP_SUMMARY": f"{tmp}/sum"}
            code = bw.main(environ, api=api, now=utc(2026, 11, 22, 7, 2), out=out.append)
            written["out"] = pathlib.Path(f"{tmp}/out").read_text()
            written["sum"] = pathlib.Path(f"{tmp}/sum").read_text()
        return code, written, out

    def test_a_scheduled_run_writes_the_decision_to_the_step_outputs(self):
        code, written, _ = self.run_main({"BOARDS_EVENT": "schedule", "GITHUB_RUN_ID": "9"}, [run(1, utc(2026, 11, 22, 1, 3))])
        self.assertEqual(code, 0)
        for line in ("run=true", "mode=window", f"limit={limits.BOARDS_PER_RUN}", "timeout_minutes=15", "deadline_minutes=12"):
            self.assertIn(line, written["out"])
        self.assertIn("1:00 AM CT", written["sum"])

    def test_a_manual_run_always_runs_and_takes_its_mode_from_the_input(self):
        code, written, _ = self.run_main({"BOARDS_EVENT": "workflow_dispatch", "BOARDS_MODE_INPUT": "catchup", "GITHUB_RUN_ID": "9"}, [])
        self.assertEqual(code, 0)
        self.assertIn("run=true", written["out"])
        self.assertIn("mode=catchup", written["out"])
        self.assertIn(f"limit={limits.BOARDS_CATCHUP_PER_RUN}", written["out"])

    def test_an_auto_dispatch_is_decided_from_the_history_like_a_scheduled_run(self):
        env = {"BOARDS_EVENT": "workflow_dispatch", "BOARDS_AUTO": "true", "GITHUB_RUN_ID": "9"}
        _, written, _ = self.run_main(env, [run(2, utc(2026, 11, 22, 7, 1))])
        self.assertIn("run=false", written["out"])
        _, written, _ = self.run_main({**env, "BOARDS_AUTO": "false"}, [run(2, utc(2026, 11, 22, 7, 1))])
        self.assertIn("run=true", written["out"])                                          # a manual run always runs

    def test_a_duplicate_firing_writes_run_false(self):
        code, written, _ = self.run_main({"BOARDS_EVENT": "schedule", "GITHUB_RUN_ID": "9"}, [run(2, utc(2026, 11, 22, 7, 1))])
        self.assertIn("run=false", written["out"])


if __name__ == "__main__":
    unittest.main()
