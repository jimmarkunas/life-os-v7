import datetime as dt
import unittest
from urllib.parse import parse_qs

from lifeos.platform import alert_registry, gate

NOW = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)


def run(number, conclusion, event="schedule", hours_ago=0):
    when = (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": number, "event": event, "conclusion": conclusion, "status": "completed" if conclusion else "in_progress", "created_at": when}


class Hub:
    """A fake GitHub: workflow runs by workflow file, the jobs of a run, and a stateful open-issue list per label."""

    def __init__(self, runs, jobs=None, unreadable=()):
        self.runs, self.jobs, self.unreadable = dict(runs), dict(jobs or {}), set(unreadable)
        self.issues, self.calls, self.next = {}, [], 1

    def __call__(self, path, method="GET", body=None):
        route, _, query = path.partition("?")
        self.calls.append((method, route))
        if route.startswith("actions/workflows/"):
            workflow = route.split("/")[2]
            if workflow in self.unreadable:
                raise OSError("unreadable")
            return {"workflow_runs": self.runs.get(workflow, [])}
        if route.startswith("actions/runs/") and route.endswith("/jobs"):
            return {"jobs": self.jobs.get(int(route.split("/")[2]), [])}
        if route == "issues" and method == "GET":
            return list(self.issues.get(parse_qs(query).get("labels", [""])[0], []))
        if route == "issues" and method == "POST":
            self.issues.setdefault(body["labels"][0], []).append({"number": self.next, "labels": [{"name": name} for name in body["labels"]]})
            self.next += 1
            return {}
        if route.startswith("issues/") and method == "PATCH":
            number = int(route.split("/")[1])
            for label in self.issues:
                self.issues[label] = [issue for issue in self.issues[label] if issue["number"] != number]
            return {}
        return {}


def _check(hub, pushes):
    old = gate._api
    gate._api = hub
    try:
        return gate.scheduled_checks(NOW, push=lambda *a: (pushes.append(a), True)[1], api=hub)
    finally:
        gate._api = old


class Signal(unittest.TestCase):
    def test_a_red_newest_scheduled_run_opens_and_a_pass_after_it_recovers_by_the_workflows_own_count(self):
        self.assertIs(gate.scheduled_signal([run(2, "failure"), run(1, "success", hours_ago=5)], 1), True)
        self.assertIs(gate.scheduled_signal([run(3, "success"), run(2, "failure", hours_ago=5)], 1), False)  # the weekly check: one pass closes it
        self.assertIsNone(gate.scheduled_signal([run(3, "success"), run(2, "failure", hours_ago=5)], 3))  # the watchdog: three passes
        self.assertIs(gate.scheduled_signal([run(5, "success"), run(4, "success", hours_ago=1), run(3, "success", hours_ago=2)], 3), False)

    def test_cancelled_skipped_unfinished_and_manual_runs_never_count(self):
        runs = [run(9, "cancelled"), run(8, None), run(7, "skipped"), run(6, "failure", event="workflow_dispatch"), run(5, "success", hours_ago=9)]
        self.assertIsNone(gate.scheduled_signal(runs, 3))
        self.assertIs(gate.scheduled_signal(runs, 1), False)
        self.assertIsNone(gate.scheduled_signal([], 1))
        self.assertIs(gate.scheduled_signal([run(1, "timed_out")], 1), True)
        self.assertIs(gate.scheduled_signal([run(1, "startup_failure")], 1), True)


class Pages(unittest.TestCase):
    def test_one_page_when_red_none_for_the_second_red_run_one_recovered_push(self):
        pushes, hub = [], Hub({"safety.yml": [run(1, "failure", hours_ago=24 * 7)], "watchdog.yml": [run(50, "success")]})
        first = _check(hub, pushes)
        self.assertEqual(first["changes"]["safety.yml"], "opened")
        self.assertEqual([(p[2], p[3]) for p in pushes], [("page", "scheduled_red_safety")])
        self.assertIn("safety", pushes[0][0])  # the alert names the workflow
        hub.runs["safety.yml"] = [run(2, "failure"), run(1, "failure", hours_ago=24 * 7)]  # it fails again on the next scheduled run
        _check(hub, pushes)
        self.assertEqual(len(pushes), 1)  # no second push
        hub.runs["safety.yml"] = [run(3, "success"), run(2, "failure", hours_ago=24 * 7)]  # then it passes
        third = _check(hub, pushes)
        self.assertEqual(third["changes"]["safety.yml"], "closed")
        self.assertEqual([(p[2], p[3]) for p in pushes], [("page", "scheduled_red_safety"), ("info", "scheduled_recovered_safety")])
        _check(hub, pushes)
        self.assertEqual(len(pushes), 2)  # recovered stays recovered

    def test_the_watchdog_needs_three_passes_to_close(self):
        pushes, hub = [], Hub({"safety.yml": [run(1, "success")], "watchdog.yml": [run(9, "failure", hours_ago=2)]})
        _check(hub, pushes)
        self.assertEqual([p[3] for p in pushes], ["scheduled_red_watchdog"])
        hub.runs["watchdog.yml"] = [run(11, "success"), run(10, "success", hours_ago=1), run(9, "failure", hours_ago=2)]
        _check(hub, pushes)
        self.assertEqual(len(pushes), 1)  # two passes are not enough
        hub.runs["watchdog.yml"] = [run(12, "success"), run(11, "success", hours_ago=1), run(10, "success", hours_ago=2), run(9, "failure", hours_ago=3)]
        _check(hub, pushes)
        self.assertEqual([p[3] for p in pushes], ["scheduled_red_watchdog", "scheduled_recovered_watchdog"])

    def test_a_failed_run_whose_own_alert_already_pushed_is_not_paged_twice(self):
        steps = [{"name": "Phone alert", "conclusion": "success"}, {"name": "Fail the run when a check failed", "conclusion": "failure"}]
        pushes, hub = [], Hub({"safety.yml": [run(7, "failure")], "watchdog.yml": []}, jobs={7: [{"steps": steps}]})
        _check(hub, pushes)
        self.assertEqual((pushes, hub.issues), ([], {}))
        early = [{"name": "Load the database, Fit and push values from Bitwarden", "conclusion": "failure"}, {"name": "Phone alert", "conclusion": "skipped"}]
        pushes, hub = [], Hub({"safety.yml": [run(8, "failure")], "watchdog.yml": []}, jobs={8: [{"steps": early}]})
        _check(hub, pushes)
        self.assertEqual([p[3] for p in pushes], ["scheduled_red_safety"])

    def test_an_unreadable_workflow_is_a_gap_not_an_alert_and_the_others_are_still_checked(self):
        pushes, hub = [], Hub({"safety.yml": [run(1, "failure")], "watchdog.yml": []}, unreadable=("watchdog.yml",))
        result = _check(hub, pushes)
        self.assertEqual(result["unreadable"], ["watchdog.yml"])
        self.assertEqual([p[3] for p in pushes], ["scheduled_red_safety"])
        hub.unreadable = {"safety.yml", "watchdog.yml"}
        pushes.clear()
        result = _check(Hub({}, unreadable=("safety.yml", "watchdog.yml")), pushes)
        self.assertEqual((result["unreadable"], pushes), (["safety.yml", "watchdog.yml"], []))

    def test_one_list_call_per_workflow_and_the_hourly_runs_are_covered_elsewhere(self):
        pushes, hub = [], Hub({"safety.yml": [run(1, "success")], "watchdog.yml": [run(2, "success")]})
        _check(hub, pushes)
        listed = [route for method, route in hub.calls if route.startswith("actions/workflows/")]
        self.assertEqual(sorted(listed), ["actions/workflows/safety.yml/runs", "actions/workflows/watchdog.yml/runs"])
        self.assertNotIn("hourly.yml", gate.SCHEDULED_WORKFLOWS)
        self.assertIn("hourly.yml", gate.SCHEDULED_COVERED)

    def test_the_keys_are_registered_with_the_right_severity(self):
        self.assertEqual(alert_registry.severity("scheduled_red_safety"), "page")
        self.assertEqual(alert_registry.severity("scheduled_red_watchdog"), "page")
        self.assertEqual(alert_registry.severity("scheduled_recovered_safety"), "info")


if __name__ == "__main__":
    unittest.main()
