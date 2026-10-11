import datetime as dt
import unittest

from lifeos.platform import gate

NOW = dt.datetime(2026, 10, 1, 17, 7, tzinfo=dt.timezone.utc)


def run(rid, started, conclusion="success", updated=None, title="tick"):
    return {"id": rid, "display_title": title, "run_started_at": started, "created_at": started, "conclusion": conclusion, "updated_at": updated or started}


class Gate(unittest.TestCase):
    def test_recent_run_means_skip(self):
        self.assertEqual(gate.decide([run(1, "2026-10-01T16:47:00Z")], NOW, 9), "skip")

    def test_old_run_means_run(self):
        self.assertEqual(gate.decide([run(1, "2026-10-01T16:07:00Z")], NOW, 9), "run")

    def test_cancelled_runs_and_itself_do_not_count(self):
        self.assertEqual(gate.decide([run(9, "2026-10-01T17:07:00Z", None), run(2, "2026-10-01T16:57:00Z", "cancelled")], NOW, 9), "run")

    def test_manual_runs_never_block_a_tick(self):
        self.assertEqual(gate.decide([run(1, "2026-10-01T17:00:00Z", title="manual"), run(2, "2026-10-01T16:50:00Z", title="hourly")], NOW, 9), "run")

    def test_active_lists_only_running_runs_of_that_kind(self):
        runs = [dict(run(1, "2026-10-01T17:00:00Z", None, title="manual"), status="in_progress"), dict(run(2, "2026-10-01T16:50:00Z", None), status="completed"),
                dict(run(3, "2026-10-01T17:05:00Z", None), status="queued"), dict(run(9, "2026-10-01T17:07:00Z", None, title="manual"), status="in_progress")]
        self.assertEqual([r["id"] for r in gate.active(runs, 9, "manual")], [1])
        self.assertEqual([r["id"] for r in gate.active(runs, 9, "tick")], [3])

    def test_a_dropped_slot_is_covered_by_the_next(self):
        later = NOW + dt.timedelta(minutes=20)
        self.assertEqual(gate.decide([run(1, "2026-10-01T16:07:00Z")], later, 9), "run")

    def test_stale_after_150_minutes_without_success(self):
        self.assertTrue(gate.stale([run(1, "2026-10-01T13:00:00Z", updated="2026-10-01T13:50:00Z")], NOW, 9))
        self.assertFalse(gate.stale([run(1, "2026-10-01T16:00:00Z", updated="2026-10-01T16:10:00Z")], NOW, 9))
        self.assertTrue(gate.stale([run(1, "2026-10-01T16:00:00Z", "failure")], NOW, 9))


class Tick(unittest.TestCase):
    def test_workflow_accepts_a_timer_tick_with_the_same_gate(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        text = (root / ".github/workflows/hourly.yml").read_text()
        self.assertIn("      tick:", text)
        self.assertIn("inputs.tick", text.split("PIPELINE:")[1].split("\n")[0])
        self.assertIn("TICK: ${{ inputs.tick }}", text)
        self.assertIn('os.environ.get("TICK")', (root / "lifeos/platform/gate.py").read_text())



def ago(hours, conclusion="failure", title="tick", rid=1):
    when = (NOW - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": rid, "display_title": title, "conclusion": conclusion, "created_at": when, "updated_at": when}


class Failing(unittest.TestCase):
    def test_three_failed_ticks_in_a_day_open_the_incident(self):
        self.assertIs(gate.failing([ago(1), ago(9), ago(23)], NOW), True)

    def test_one_or_two_failures_leave_it_unchanged_and_none_closes_it(self):
        self.assertIsNone(gate.failing([ago(1), ago(9)], NOW))
        self.assertIs(gate.failing([ago(1, "success"), ago(30)], NOW), False)

    def test_manual_cancelled_and_old_runs_never_count(self):
        runs = [ago(1, title="manual"), ago(2, "cancelled"), ago(3, "cancelled"), ago(25), ago(26), ago(27)]
        self.assertIs(gate.failing(runs, NOW), False)

    def test_two_missed_hourly_runs_are_detected(self):
        self.assertTrue(gate.stale([ago(3, "success")], NOW, 0))                  # 180 min since the last success
        self.assertFalse(gate.stale([ago(2.05, "success")], NOW, 0))             # the longest gap seen in a bad week (124 min) is not a stall


class FakeGitHub:
    """Records issue API calls; `issues` is what the open-issue query returns."""
    def __init__(self, issues=()):
        self.issues, self.calls = list(issues), []

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path.split("?")[0], body))
        return self.issues if method == "GET" else {}

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m != "GET"]


class Incident(unittest.TestCase):
    OPEN, CLOSE = ("T", "B", "page"), ("R", "", "info")

    def sync(self, api, signal, push):
        old = gate._api
        gate._api = api
        try:
            return gate.sync_incident("lbl", "title", "body", signal, self.OPEN, self.CLOSE, push=push)
        finally:
            gate._api = old

    def test_a_new_incident_pushes_first_then_opens_the_issue(self):
        order, api = [], FakeGitHub()
        inner = api.__call__
        result = self.sync(lambda path, method="GET", body=None: (order.append(f"{method} {path.split('?')[0]}"), inner(path, method, body))[1], True,
                           lambda *a: (order.append("push"), True)[1])
        self.assertEqual((result, order.index("push") < order.index("POST issues")), ("opened", True))
        self.assertEqual(api.calls[-1][2]["labels"], ["lbl"])

    def test_a_push_that_is_not_accepted_leaves_the_incident_open_and_retryable(self):
        api = FakeGitHub()
        self.assertEqual(self.sync(api, True, lambda *a: False), "opened")
        self.assertEqual(api.calls[-1][2]["labels"], ["lbl", gate.PENDING])

    def test_a_pending_incident_is_pushed_again_and_the_label_cleared_only_when_accepted(self):
        pending = {"number": 7, "labels": [{"name": "lbl"}, {"name": gate.PENDING}]}
        failed, api = [], FakeGitHub([pending])
        self.assertEqual(self.sync(api, True, lambda *a: False), "retried")
        self.assertEqual(api.writes(), [])                                            # still pending: the next run tries again
        api = FakeGitHub([pending])
        self.assertEqual(self.sync(api, True, lambda *a: True), "retried")
        self.assertEqual(api.writes(), [("DELETE", "issues/7/labels/push-pending")])

    def test_an_open_incident_is_not_pushed_again(self):
        pushed, api = [], FakeGitHub([{"number": 7, "labels": [{"name": "lbl"}]}])
        self.assertIsNone(self.sync(api, True, lambda *a: pushed.append(a) or True))
        self.assertEqual((pushed, api.writes()), ([], []))                           # dedupe: one push per incident, not per run

    def test_recovery_pushes_once_and_closes_the_issue(self):
        pushed, api = [], FakeGitHub([{"number": 7, "labels": [{"name": "lbl"}]}])
        self.assertEqual(self.sync(api, False, lambda *a: pushed.append(a) or True), "closed")
        self.assertEqual((pushed, api.writes()), ([self.CLOSE], [("PATCH", "issues/7")]))

    def test_recovery_with_nothing_open_and_an_unchanged_signal_do_nothing(self):
        pushed = []
        for signal, issues in ((False, []), (None, [{"number": 7, "labels": []}])):
            api = FakeGitHub(issues)
            self.assertIsNone(self.sync(api, signal, lambda *a: pushed.append(a) or True))
            self.assertEqual(api.writes(), [])
        self.assertEqual(pushed, [])

    def test_watch_pages_on_repeated_failures_even_when_other_runs_succeed(self):
        pushed, api = [], FakeGitHub()
        runs = [ago(1, "success", rid=1), ago(2, rid=2), ago(9, rid=3), ago(20, rid=4)]
        old = (gate._api, gate._push)
        gate._api = lambda path, *a, **k: {"workflow_runs": runs} if "workflows/hourly" in path else api(path, *a, **k)
        gate._push = lambda *a: pushed.append(a[0]) or True
        try:
            self.assertEqual(gate.watch(NOW), 0)
        finally:
            gate._api, gate._push = old
        self.assertEqual(pushed, ["V7 hourly runs failing repeatedly"])


class DailyCheckin(unittest.TestCase):
    def test_it_is_due_once_a_day_in_the_first_half_hour_only(self):
        at = lambda h, m: dt.datetime(2026, 10, 11, h, m, tzinfo=dt.timezone.utc)
        self.assertEqual([gate.digest_due(at(13, 17)), gate.digest_due(at(13, 47)), gate.digest_due(at(12, 17)), gate.digest_due(at(14, 17))], [True, False, False, False])

    def test_a_healthy_day_says_ok_with_counts_only(self):
        title, body = gate.health([ago(0.3, "success", rid=1), ago(5, "success", rid=2), ago(6, "cancelled", rid=3)], [ago(2, "success", title=gate.DOMAINS_TITLE)], NOW)
        self.assertEqual(title, "V7 daily check: OK")
        self.assertIn("2 hourly runs, 2 ok, 0 failed", body)

    def test_any_failed_tick_or_domain_run_or_a_stale_success_says_degraded(self):
        self.assertIn("DEGRADED", gate.health([ago(1, "success"), ago(3)], [], NOW)[0])
        self.assertIn("DEGRADED", gate.health([ago(1, "success")], [ago(4, "failure", title=gate.DOMAINS_TITLE)], NOW)[0])
        title, body = gate.health([ago(3, "success")], [], NOW)
        self.assertEqual((title, "180 min ago" in body), ("V7 daily check: DEGRADED", True))
        self.assertIn("no good run", gate.health([], [], NOW)[1])

    def test_watch_sends_the_checkin_at_the_hour_and_not_otherwise(self):
        for hour, expect in ((13, ["V7 daily check: OK"]), (9, [])):
            now, pushed = dt.datetime(2026, 10, 11, hour, 17, tzinfo=dt.timezone.utc), []
            fresh = lambda: {"workflow_runs": [{"id": 1, "display_title": "tick", "conclusion": "success", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                "updated_at": (now - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")}]}
            old = gate._api
            gate._api = lambda path, *a, **k: fresh() if "workflows/" in path else {}
            try:
                gate.watch(now, push=lambda *a: pushed.append(a[0]) or True)
            finally:
                gate._api = old
            self.assertEqual(pushed, expect)


class PurgeIsNamed(unittest.TestCase):
    """D166: the Sunday purge failed twice and the push only said 'finish'. The workflow now names the stage and pushes the purge result itself."""
    text = (__import__("pathlib").Path(__file__).resolve().parents[2] / ".github/workflows/hourly.yml").read_text()

    def test_the_lane_step_lists_purge_and_hands_the_stage_names_to_the_report_job(self):
        self.assertIn("purge=${{ steps.purge.outcome }}", self.text)
        self.assertIn("sync_seen=${{ steps.sync_seen.outcome }}", self.text)
        self.assertIn("failed: ${{ steps.lanes.outputs.failed }}", self.text)
        self.assertIn("STAGES: ${{ needs.finish.outputs.failed }}", self.text)

    def test_the_purge_step_pushes_success_and_failure(self):
        step = self.text.split("- id: purge")[1].split("- id: alerts")[0]
        self.assertIn('--title "V7 weekly purge FAILED"', step)
        self.assertIn('--title "V7 weekly purge done"', step)
        self.assertIn("exit 1", step)


if __name__ == "__main__":
    unittest.main()
