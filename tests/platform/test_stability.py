"""LIFE-694: the stability clock counts completed clean America/Chicago days from GitHub's own history (failed ticks and domains runs, platform incident issues), cap 7, from the day it merged; anything it cannot read is `unverified`."""
import datetime as dt
import io
import json
import unittest
import urllib.error
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from lifeos.platform import gate, stability

TZ = ZoneInfo("America/Chicago")
D = dt.date
START = D(2026, 11, 15)
ENV = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "owner/repo"}


def at(day, hour=12, minute=0):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def utc(day, hour=12, minute=0):
    return at(day, hour, minute).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(day, conclusion="failure", title=gate.TICK_TITLE, hour=12):
    return {"display_title": title, "conclusion": conclusion, "updated_at": utc(day, hour), "created_at": utc(day, hour)}


def issue(opened, closed=None, label="pipeline-failing", pull=False):
    return {"labels": [{"name": label}], "created_at": utc(opened), "closed_at": utc(closed) if closed else None, **({"pull_request": {}} if pull else {})}


class Github:
    """A fake GitHub: runs per workflow file, issues per (label, state); serves pages of 100 like the real API."""

    def __init__(self, runs=None, issues=None, error=None):
        self.runs, self.issues, self.error, self.calls = runs or {}, issues or {}, error, []

    def __call__(self, request, timeout=0):
        assert timeout, "every call carries a timeout"
        url = urlparse(request.full_url)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls.append(unquote(request.full_url))
        if self.error:
            raise self.error
        page = int(query.get("page", "1"))
        if "/actions/workflows/" in url.path:
            workflow = url.path.rsplit("/runs", 1)[0].rsplit("/", 1)[1]
            body = {"workflow_runs": self.runs.get(workflow, [])[(page - 1) * 100:page * 100]}
        else:
            body = self.issues.get((query["labels"], query["state"]), [])[(page - 1) * 100:page * 100]
        return _Response(body)


class _Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Counting(unittest.TestCase):
    def test_a_day_counts_only_when_it_is_complete_and_clean_and_never_before_the_start(self):
        self.assertEqual(stability.count(set(), START, START), (0, "counting"))                        # the start day itself is not credited
        self.assertEqual(stability.count(set(), START + dt.timedelta(days=1), START), (1, "counting"))
        self.assertEqual(stability.count(set(), START + dt.timedelta(days=4), START), (4, "counting"))
        self.assertEqual(stability.count(set(), START - dt.timedelta(days=3), START), (0, "counting"))  # never started: no invented past days
        self.assertEqual(stability.count({START - dt.timedelta(days=2)}, START + dt.timedelta(days=2), START), (2, "counting"))   # a failure before the start is not counted

    def test_a_failure_resets_at_once_and_clean_days_count_again_after_it(self):
        today = START + dt.timedelta(days=5)
        self.assertEqual(stability.count({today}, today, START), (0, "reset"))                          # a failure today resets now
        self.assertEqual(stability.count({today - dt.timedelta(days=1)}, today, START), (0, "reset"))    # yesterday was not clean
        self.assertEqual(stability.count({today - dt.timedelta(days=2)}, today, START), (1, "counting"))  # one clean day since it
        self.assertEqual(stability.count({START + dt.timedelta(days=2)}, today, START), (2, "counting"))

    def test_seven_is_the_cap_and_stays_visible(self):
        today = START + dt.timedelta(days=10)
        self.assertEqual(stability.count(set(), today, START), (7, "complete"))
        self.assertEqual(stability.count(set(), today + dt.timedelta(days=30), START), (7, "complete"))
        self.assertEqual(stability.count({today - dt.timedelta(days=8)}, today, START), (7, "complete"))  # a failure older than the seven days no longer matters
        self.assertEqual(stability.count({today - dt.timedelta(days=7)}, today, START), (6, "counting"))

    def test_the_same_failure_observed_twice_is_one_day(self):
        today = START + dt.timedelta(days=5)
        self.assertEqual(stability.count({today - dt.timedelta(days=3)}, today, START), stability.count([today - dt.timedelta(days=3)] * 5, today, START))


class Reading(unittest.TestCase):
    NOW = at(START + dt.timedelta(days=6), 9)

    def clock(self, github, now=None, start=START):
        return stability.clock(ENV, now or self.NOW, opener=github, start=start)

    def test_a_clean_history_counts_the_completed_days(self):
        self.assertEqual(self.clock(Github()), {"state": "counting", "days": 6, "why": ""})                # started Nov 15; on Nov 21 the days Nov 15 to Nov 20 are complete
        self.assertEqual(self.clock(Github(), now=at(START + dt.timedelta(days=5), 9))["days"], 5)
        self.assertEqual(self.clock(Github(), now=at(START, 9))["days"], 0)                                 # on the start day nothing is credited yet

    def test_a_failed_tick_or_domains_run_resets_but_a_manual_run_or_a_pass_does_not(self):
        today = START + dt.timedelta(days=6)
        github = Github(runs={"hourly.yml": [run(today - dt.timedelta(days=2), "failure"), run(today - dt.timedelta(days=1), "success"), run(today - dt.timedelta(days=1), "failure", title="manual")],
                              "domains.yml": [run(today - dt.timedelta(days=5), "failure", title=gate.DOMAINS_TITLE)]})
        self.assertEqual(self.clock(github), {"state": "counting", "days": 1, "why": ""})                 # the tick failure two days ago; yesterday was clean
        today_failure = Github(runs={"domains.yml": [run(today, "timed_out", title=gate.DOMAINS_TITLE)]})
        self.assertEqual(self.clock(today_failure), {"state": "reset", "days": 0, "why": ""})

    def test_a_platform_incident_issue_makes_every_day_it_is_open_not_clean(self):
        today = START + dt.timedelta(days=6)
        open_since = Github(issues={("pipeline-failing", "open"): [issue(today - dt.timedelta(days=3))]})
        self.assertEqual(self.clock(open_since), {"state": "reset", "days": 0, "why": ""})                # still open today
        closed = Github(issues={("pipeline-stale", "closed"): [issue(today - dt.timedelta(days=4), today - dt.timedelta(days=2), "pipeline-stale")]})
        self.assertEqual(self.clock(closed)["days"], 1)                                                     # open days -4..-2, clean day -1
        scheduled = Github(issues={("scheduled-red-safety", "open"): [issue(today - dt.timedelta(days=1), None, "scheduled-red-safety")]})
        self.assertEqual(self.clock(scheduled)["state"], "reset")

    def test_outside_events_and_pull_requests_never_reset(self):
        today = START + dt.timedelta(days=6)
        github = Github(issues={("deploy-drift", "open"): [issue(today - dt.timedelta(days=1), None, "deploy-drift")], ("pipeline-failing", "open"): [issue(today - dt.timedelta(days=1), pull=True)]})
        self.assertEqual(self.clock(github), {"state": "counting", "days": 6, "why": ""})
        self.assertNotIn("deploy-drift", stability.counted_labels())
        self.assertNotIn("deploy_drift", " ".join(stability.counted_labels()))

    def test_the_chicago_midnight_decides_the_day_not_utc(self):
        today = START + dt.timedelta(days=6)
        late = {"display_title": gate.TICK_TITLE, "conclusion": "failure", "updated_at": "2026-11-18T05:59:00Z"}       # 11:59 PM CST on Nov 17
        early = {"display_title": gate.TICK_TITLE, "conclusion": "failure", "updated_at": "2026-11-18T06:00:00Z"}      # 12:00 AM CST on Nov 18
        first = self.clock(Github(runs={"hourly.yml": [late]}), now=at(today, 9))
        second = self.clock(Github(runs={"hourly.yml": [early]}), now=at(today, 9))
        self.assertEqual((first["days"], second["days"]), (stability.count({D(2026, 11, 17)}, today, START)[0], stability.count({D(2026, 11, 18)}, today, START)[0]))
        self.assertNotEqual(first["days"], second["days"])

    def test_daylight_saving_changes_nothing_about_the_chicago_day(self):
        """US time springs forward on Mar 8 2026 at 2 AM: 11:59 PM CST/CDT stays Mar 8 and midnight is Mar 9 on both sides."""
        start = D(2026, 3, 1)
        today = D(2026, 3, 12)
        for stamp, day in (("2026-03-09T04:59:59Z", D(2026, 3, 8)), ("2026-03-09T05:00:00Z", D(2026, 3, 9)), ("2026-03-08T07:59:00Z", D(2026, 3, 8)), ("2026-03-08T08:00:00Z", D(2026, 3, 8))):
            result = stability.clock(ENV, at(today, 9), opener=Github(runs={"hourly.yml": [{"display_title": gate.TICK_TITLE, "conclusion": "failure", "updated_at": stamp}]}), start=start)
            self.assertEqual(result["days"], (today - dt.timedelta(days=1) - day).days, stamp)
        self.assertEqual(stability.count(set(), D(2026, 3, 9), D(2026, 3, 7)), (2, "counting"))               # the 23-hour Mar 8 is still one day
        fall = stability.count(set(), D(2026, 11, 3), D(2026, 11, 1))                                         # Nov 1 2026 has 25 hours and is still one day
        self.assertEqual(fall, (2, "counting"))

    def test_anything_unreadable_is_unverified_never_a_healthy_zero(self):
        self.assertEqual(stability.clock({}, self.NOW, opener=Github())["why"], "STABILITY_NO_TOKEN")
        for error in (urllib.error.URLError("down"), urllib.error.HTTPError("u", 403, "forbidden", {}, None), TimeoutError()):
            result = self.clock(Github(error=error))
            self.assertEqual((result["state"], result["days"], result["why"]), ("unverified", 0, "STABILITY_GITHUB_UNREADABLE"))
        crowded = Github(runs={"hourly.yml": [run(START, "success")] * 600})
        self.assertEqual(self.clock(crowded)["why"], "STABILITY_TOO_MANY")                                    # a list longer than it will page is not guessed at

    def test_it_reads_only_and_every_call_has_a_timeout(self):
        github = Github()
        self.clock(github)
        self.assertTrue(github.calls)
        self.assertTrue(all("api.github.com/repos/owner/repo/" in c for c in github.calls))
        self.assertTrue(all(("/runs?" in c) or ("/issues?" in c) for c in github.calls))                       # list calls only: no POST, no write endpoint


class Doc(unittest.TestCase):
    TEXT = (__import__("pathlib").Path(__file__).resolve().parents[2] / "docs/platform/stability-clock.md").read_text()

    def test_the_doc_states_the_rule_in_one_line_and_lists_every_counted_label(self):
        self.assertIn("**A clean day means no failed run and no platform incident issue.**", self.TEXT)
        for label in stability.PLATFORM_LABELS:
            self.assertIn(f"`{label}`", self.TEXT, label)
        self.assertIn("scheduled-red-<workflow>", self.TEXT)
        self.assertEqual(stability.counted_labels()[len(stability.PLATFORM_LABELS):], tuple("scheduled-red-" + name.removesuffix(".yml") for name in gate.SCHEDULED_WORKFLOWS))
        self.assertIn("deploy-drift", self.TEXT.split("| no |")[0] + self.TEXT)                              # the excluded INFO type is named with its reason
        self.assertIn(START.strftime("%b") + " " + str(START.day) + " " + str(START.year), self.TEXT)       # the start day

    def test_the_doc_says_who_posts_at_seven_of_seven_and_that_nothing_is_pushed(self):
        self.assertIn("epic lead", self.TEXT)
        self.assertIn("No phone push and no Jira call", self.TEXT)
        for read_only in ("contents: read", "actions: read", "issues: read"):
            self.assertIn(read_only, self.TEXT)
        self.assertNotIn(": write", self.TEXT)


if __name__ == "__main__":
    unittest.main()
