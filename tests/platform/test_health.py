"""LIFE-690: the health model: close on 3 passes with exactly one recovered push, a permanent known state is WARN and silent until one PAGE at day 7, and a push needs a state key."""
import datetime as dt
import json
import unittest

from lifeos.platform import alerts, gate, health

NOW = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.timezone.utc)


def tick(hours_ago, conclusion, title="tick"):
    when = (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": int(hours_ago * 10), "display_title": title, "conclusion": conclusion, "created_at": when, "updated_at": when}


class Recovered(unittest.TestCase):
    def test_three_passes_in_a_row_recover_and_anything_less_does_not(self):
        self.assertTrue(health.recovered(["success"] * 3))
        self.assertTrue(health.recovered(["success"] * 5 + ["failure"]))
        self.assertFalse(health.recovered(["success", "success"]))
        self.assertFalse(health.recovered(["success", "failure", "success"]))
        self.assertFalse(health.recovered([]))

    def test_the_stalled_incident_closes_only_after_three_passes(self):
        """A success after a stall used to close it at once; now one lucky run holds it open (None), three close it (False), and a real stall opens it (True)."""
        stall = [tick(5, "failure"), tick(6, "failure")]
        self.assertIs(gate.stalled(stall, NOW), True)
        one = [tick(1, "success"), tick(2, "failure"), tick(3, "failure")]
        self.assertIsNone(gate.stalled(one, NOW))
        three = [tick(1, "success"), tick(2, "success"), tick(3, "success"), tick(4, "failure")]
        self.assertIs(gate.stalled(three, NOW), False)
        self.assertIsNone(gate.stalled([tick(1, "success"), tick(1.5, "success", "manual"), tick(2, "cancelled"), tick(3, "failure")], NOW))      # manual and cancelled runs count for nothing


class FakeGitHub:
    """One incident issue per label: open, closed, with the comment times that restart the reminder clock."""

    def __init__(self):
        self.issue, self.now = None, NOW

    def __call__(self, path, method="GET", body=None):
        if method == "GET" and path.startswith("issues?"):
            return [self.issue] if self.issue and self.issue["state"] == "open" else []
        if method == "POST" and path == "issues":
            stamp = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.issue = {"number": 1, "state": "open", "labels": [{"name": n} for n in body["labels"]], "created_at": stamp, "updated_at": stamp}
        elif method == "POST" and path.endswith("/comments"):
            self.issue["updated_at"] = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif method == "PATCH":
            self.issue["state"] = body["state"]
        return {}


class IncidentLifecycle(unittest.TestCase):
    def test_an_incident_opens_once_reminds_no_more_than_every_three_hours_and_closes_with_exactly_one_recovered_push(self):
        api, pushes, history = FakeGitHub(), [], []
        old, gate._api = gate._api, api
        try:
            def step(hour, conclusion):
                api.now = NOW + dt.timedelta(hours=hour)
                history.insert(0, tick(-hour, conclusion))
                signal = gate.failing(history, api.now)
                return gate.sync_incident("pipeline-failing", "t", "b", signal, ("opened", "b", "page", "pipeline_failing"), ("recovered", "", "info", "pipeline_failing_recovered"),
                                          push=lambda title, body, severity, key, hour=hour: pushes.append((hour, severity, key)) or True, now=api.now, remind=not gate.last_tick_passed(history))
            for hour in range(0, 3):
                step(hour, "failure")                                          # 3 failed ticks: the incident opens
            for hour in range(3, 12):
                step(hour, "failure")                                          # still failing for 9 more hours
            for hour in range(12, 18):
                step(hour, "success")                                          # recovers
        finally:
            gate._api = old
        opened = [p for p in pushes if p[2] == "pipeline_failing" and p[1] == "page"]
        recovered = [p for p in pushes if p[2] == "pipeline_failing_recovered"]
        self.assertEqual(len(recovered), 1)                                   # exactly one recovered push
        self.assertEqual(recovered[0][0], 14)                                 # the third passing tick (hours 12, 13, 14), not the first
        self.assertEqual(api.issue["state"], "closed")
        self.assertEqual(opened[0][0], 2)                                     # opened once, at the third failure
        gaps = [b[0] - a[0] for a, b in zip(opened, opened[1:], strict=False)]
        self.assertTrue(all(gap >= 3 for gap in gaps), gaps)                  # a reminder never comes sooner than 3 hours after the last page
        self.assertFalse([p for p in pushes if p[0] > 14])                    # nothing after it closed


class KnownStateRules(unittest.TestCase):
    def runs(self, hours, code="STORE_SSH_UNREACHABLE", item="interview:thread-7"):
        store, observations = {}, []
        states = health.KnownStates(store)
        for hour in range(hours):
            observations.append(states.observe({item: code}, NOW + dt.timedelta(hours=hour)))
        return store, observations

    def test_the_same_item_with_the_same_code_on_24_runs_never_pushes_after_the_first_and_shows_warn(self):
        _, observations = self.runs(24)
        self.assertEqual(observations[0].new, ["interview:thread-7"])         # run 1: the normal failure path
        for observation in observations[1:]:
            self.assertEqual((observation.new, observation.escalate, len(observation.known)), ([], [], 1))      # runs 2 to 24: WARN, no push
        lines = health.card_lines(observations[-1].known, NOW + dt.timedelta(hours=23))
        self.assertEqual(lines, ["WARN interview:thread-7: STORE_SSH_UNREACHABLE, failing on 24 runs over 0 days"])

    def test_no_push_from_run_2_to_run_168_then_exactly_one_page_at_day_7(self):
        sent = []
        states = health.KnownStates({})
        for hour in range(0, 24 * 9):                                          # nine days of hourly runs
            observation = states.observe({"hiring:board-3": "HIRING_PAGE_WALK"}, NOW + dt.timedelta(hours=hour))
            health.escalate(observation, "hiring", lambda key, title, body, hour=hour: sent.append((hour, key)) or True)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], (168, "known_state_expired_hiring"))        # exactly one, at 7 days, under a registered family key
        self.assertIsNotNone(alerts.alert_registry.lookup(sent[0][1]))
        self.assertEqual(alerts.alert_registry.severity(sent[0][1]), "page")

    def test_a_new_code_or_a_passing_run_resets_the_state(self):
        states = health.KnownStates({})
        states.observe({"x": "A"}, NOW)
        self.assertEqual(states.observe({"x": "A"}, NOW + dt.timedelta(hours=1)).known[0].runs, 2)
        changed = states.observe({"x": "B"}, NOW + dt.timedelta(hours=2))
        self.assertEqual((changed.new, changed.known), (["x"], []))           # a different code is a new failure again
        cleared = states.observe({}, NOW + dt.timedelta(hours=3))
        self.assertEqual(cleared.cleared, ["x"])
        self.assertEqual(states.observe({"x": "B"}, NOW + dt.timedelta(hours=4)).new, ["x"])

    def test_the_store_is_json_safe_so_a_stage_can_keep_it_anywhere(self):
        store, _ = self.runs(5)
        self.assertEqual(json.loads(json.dumps(store)), store)


class StateKeys(unittest.TestCase):
    def test_a_push_without_a_state_key_is_never_sent(self):
        sent = []
        post = lambda *a, **k: sent.append(a) or True      # noqa: E731
        for key in (None, "", 7, "not_registered_anywhere"):
            self.assertFalse(alerts.push(key, "t", "b", "topic", post=post), key)
        self.assertEqual(sent, [])

    def test_a_state_key_is_a_registry_family_plus_the_item_and_malformed_ones_are_refused(self):
        self.assertEqual(health.state_key("source_stopped", "dice"), "source_stopped_dice")
        self.assertEqual(health.state_key("known_state_expired", "hiring"), "known_state_expired_hiring")
        for bad in ((" ",), ("", ""), ("Upper",), ("has space", "x"), ("a" * 70,)):
            with self.assertRaises(ValueError):
                health.state_key(*bad)

    def test_reminders_are_never_more_often_than_every_three_hours_and_incident_style_info_never_repeats(self):
        """PAGE reminds every 6 h in the watchers and every 3 quiet hours in the pipeline incidents; the daily INFO reminder is the approved LIFE-129 backup behavior (documented, open question for Jim)."""
        page = alerts.Alert("expiry_x", alerts.PAGE, "t")
        for hours, expect in ((2, 0), (5, 0), (6, 1)):
            plan = alerts.reconcile([page], {"expiry_x": {"severity": alerts.PAGE, "last_notified": NOW - dt.timedelta(hours=hours)}}, NOW)
            self.assertEqual(len(plan.notify), expect, hours)
        self.assertGreaterEqual(alerts.REMIND_HOURS[alerts.PAGE], 3)
        self.assertGreaterEqual(gate.REMIND_HOURS, 3)
        self.assertEqual(alerts.REMIND_HOURS[alerts.INFO], 24)


if __name__ == "__main__":
    unittest.main()
