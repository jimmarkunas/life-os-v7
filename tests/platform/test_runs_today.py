"""LIFE-975 step 4b: the Runs OK today count from GitHub's run history (fake GitHub, no network)."""
import io
import json
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.platform import runs_today

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 25, 3, 30, tzinfo=TZ)                    # 09:30 UTC; Chicago midnight was 06:00 UTC
ENV = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}


def run(i, at, title="tick", conclusion="success", **more):
    return {"id": i, "created_at": at, "display_title": title, "conclusion": conclusion, "html_url": f"https://github.com/o/r/actions/runs/{i}", **more}


class Github:
    """Answers the three kinds of read the module makes."""

    def __init__(self, ticks=(), domains=(), recruiters=(), capacity=(), state="active", broken=False):
        self.by = {"hourly.yml": list(ticks), "domains.yml": list(domains), "recruiters.yml": list(recruiters), "capacity-card.yml": list(capacity)}
        self.state, self.broken, self.urls = state, broken, []

    def __call__(self, request, timeout=0):
        url = request.full_url
        self.urls.append(url)
        if self.broken:
            raise OSError("down")
        name = url.split("/workflows/")[1].split("/")[0].split("?")[0]
        body = {"state": self.state} if "/runs" not in url else {"workflow_runs": self.by[name]}
        return io.BytesIO(json.dumps(body).encode())


def read(github, now=NOW):
    return runs_today.read(ENV, now, github)


class RunsToday(unittest.TestCase):
    def test_every_tick_ok(self):
        out = read(Github([run(i, f"2026-11-25T0{i}:07:00Z") for i in range(6, 9)]))
        self.assertEqual((out["state"], out["ok"], out["total"], out["failed"]), ("ok", 3, 3, []))

    def test_dispatched_and_scheduled_ticks_both_count_but_manual_runs_cancelled_and_unfinished_do_not(self):
        runs = [run(1, "2026-11-25T06:07:00Z"), run(2, "2026-11-25T07:07:00Z", title="tick", event="workflow_dispatch"), run(3, "2026-11-25T07:27:00Z", title="manual", conclusion="failure"),
                run(4, "2026-11-25T08:07:00Z", conclusion="cancelled"), run(5, "2026-11-25T08:27:00Z", conclusion=None), run(6, "2026-11-25T09:07:00Z", title="test dispatch", conclusion="failure")]
        out = read(Github(runs))
        self.assertEqual((out["ok"], out["total"], out["state"]), (2, 2, "ok"))

    def test_the_chicago_midnight_is_the_reset(self):
        before = run(1, "2026-11-25T05:59:59Z")                    # 11:59:59 PM CT yesterday
        after = run(2, "2026-11-25T06:00:00Z")                     # midnight CT
        out = read(Github([before, after]))
        self.assertEqual((out["ok"], out["total"]), (1, 1))
        git = Github([after])
        read(git)
        self.assertTrue(all("created=%3E%3D2026-11-25T06%3A00%3A00Z" in u for u in git.urls if "/runs" in u))      # the query starts at midnight Chicago (06:00 UTC in winter), never at UTC midnight

    def test_no_runs_yet_today_is_zero_of_zero(self):
        out = read(Github([run(1, "2026-11-24T20:00:00Z")]))
        self.assertEqual((out["state"], out["ok"], out["total"]), ("none", 0, 0))

    def test_a_failed_tick_among_ok_ones_counts_failed_and_links_the_run(self):
        out = read(Github([run(1, "2026-11-25T06:07:00Z"), run(2, "2026-11-25T07:07:00Z", conclusion="failure"), run(3, "2026-11-25T08:07:00Z")]))
        self.assertEqual((out["state"], out["ok"], out["total"], out["failed"]), ("failed", 2, 3, ["https://github.com/o/r/actions/runs/2"]))

    def test_the_latest_tick_failing_with_no_success_since_is_last_run_failed(self):
        out = read(Github([run(1, "2026-11-25T06:07:00Z"), run(2, "2026-11-25T07:07:00Z", conclusion="timed_out")]))
        self.assertEqual((out["state"], out["ok"], out["total"]), ("latest_failed", 1, 2))

    def test_a_failed_follow_on_run_fails_the_tick_that_started_it(self):
        ticks = [run(1, "2026-11-25T06:07:00Z"), run(2, "2026-11-25T07:07:00Z"), run(3, "2026-11-25T08:07:00Z")]
        domains = [run(10, "2026-11-25T07:12:00Z", title="domains (after tick)", conclusion="failure")]
        out = read(Github(ticks, domains))
        self.assertEqual((out["state"], out["ok"], out["total"], out["failed"]), ("failed", 2, 3, ["https://github.com/o/r/actions/runs/10"]))
        for follow in ({"recruiters": [run(11, "2026-11-25T07:20:00Z", title="recruiters", conclusion="failure")]}, {"capacity": [run(12, "2026-11-25T07:20:00Z", title="capacity-card", conclusion="failure")]}):
            self.assertEqual(read(Github(ticks, **follow))["ok"], 2)
        self.assertEqual(read(Github(ticks, [run(13, "2026-11-25T07:12:00Z", title="domains (manual)", conclusion="failure")]))["ok"], 3)           # another title is not a follow-on
        self.assertEqual(read(Github(ticks, [run(14, "2026-11-25T07:12:00Z", title="domains (after tick)", conclusion="success")]))["ok"], 3)

    def test_a_follow_on_of_the_last_tick_makes_it_the_latest_failure(self):
        ticks = [run(1, "2026-11-25T06:07:00Z"), run(2, "2026-11-25T07:07:00Z")]
        out = read(Github(ticks, [run(10, "2026-11-25T07:12:00Z", title="domains (after tick)", conclusion="failure")]))
        self.assertEqual(out["state"], "latest_failed")

    def test_two_failures_in_one_tick_count_once(self):
        ticks = [run(1, "2026-11-25T06:07:00Z", conclusion="failure"), run(2, "2026-11-25T07:07:00Z")]
        domains = [run(10, "2026-11-25T06:12:00Z", title="domains (after tick)", conclusion="failure")]
        self.assertEqual(read(Github(ticks, domains))["failed"], ["https://github.com/o/r/actions/runs/1"])

    def test_a_disabled_hourly_workflow_is_paused_and_keeps_the_count(self):
        out = read(Github([run(1, "2026-11-25T06:07:00Z")], state="disabled_manually"))
        self.assertEqual((out["state"], out["ok"], out["total"]), ("paused", 1, 1))

    def test_unreadable_history_is_unverified_never_a_healthy_count(self):
        self.assertEqual(read(Github(broken=True))["state"], "unverified")
        self.assertEqual(runs_today.read({}, NOW, Github())["why"], "RUNS_NO_TOKEN")

    def test_a_list_too_long_to_read_in_full_is_unverified(self):
        many = [run(i, "2026-11-25T06:07:00Z") for i in range(100)]
        self.assertEqual(read(Github(many))["why"], "RUNS_TOO_MANY")


if __name__ == "__main__":
    unittest.main()
