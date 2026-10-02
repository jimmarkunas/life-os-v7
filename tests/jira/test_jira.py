import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lifeos.jira import rollover, snapshot, store
from lifeos.jira.boards import boards
from lifeos.platform.jira import Jira, JiraError

TZ = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parents[2]
ENV = {"JIRA_BASE_URL": "https://example.invalid", "JIRA_EMAIL": "synthetic-user", "JIRA_API_TOKEN": "synthetic-token",
       "JIRA_BOARDS": "AAA:11"}


def at(day, hour=0):
    return datetime(2026, 10, day, hour, 0, tzinfo=TZ) if day >= 1 else datetime(2026, 9, 30 + day, hour, 0, tzinfo=TZ)


def sprint(sid, state, start, end, name="Synthetic Sprint"):
    return {"id": sid, "state": state, "name": name, "startDate": start.isoformat(), "endDate": end.isoformat()}


class Response:
    def __init__(self, body=b"{}"):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeJira:
    """A tiny Jira: sprints, issues per sprint, and a log of every mutating call."""

    def __init__(self, sprints, issues=(), scrum=True, lag=0):
        self.sprints_by_id = {s["id"]: dict(s) for s in sprints}
        self.members = {}
        for key, sid, status in issues:
            self.members.setdefault(sid, {})[key] = status
        self.scrum, self.lag, self.log, self.next_id = scrum, lag, [], 900

    def board(self, board_id):
        return {"type": "scrum" if self.scrum else "kanban", "name": "Synthetic Board"}

    def sprints(self, board_id, state):
        return [dict(s) for s in self.sprints_by_id.values() if s["state"] == state]

    def sprint(self, sprint_id):
        return dict(self.sprints_by_id[sprint_id])

    def sprint_issues(self, sprint_id, jql, fields):
        done = "!= Done" in jql
        return [{"key": k, "fields": {}} for k, st in self.members.get(sprint_id, {}).items() if (st != "Done") or not done]

    def create_sprint(self, board_id, name, start, end):
        self.next_id += 1
        self.sprints_by_id[self.next_id] = {"id": self.next_id, "state": "future", "name": name,
                                            "startDate": start.isoformat(), "endDate": end.isoformat()}
        self.log.append("create")
        return {"id": self.next_id}

    def move_issues(self, sprint_id, keys):
        self.log.append("move")
        if self.lag > 0:
            self.lag -= 1
            return
        moving = {}
        for sid, items in self.members.items():
            for key in keys:
                if key in items and sid != sprint_id:
                    moving[key] = items.pop(key)
        self.members.setdefault(sprint_id, {}).update(moving)

    def set_sprint_state(self, sprint_id, state):
        self.log.append(state)
        self.sprints_by_id[sprint_id]["state"] = state


def week(extra=()):
    return [sprint(1, "active", datetime(2026, 9, 28, tzinfo=TZ), datetime(2026, 10, 4, 23, 59, 59, tzinfo=TZ)), *extra]


def run(client, live, now, sleep=lambda s: None):
    return rollover.run(1, live, environ=ENV, client=client, now=now, sleep=sleep)


class ClientTests(unittest.TestCase):
    def test_config_errors_are_fixed_codes(self):
        with self.assertRaises(JiraError) as error:
            Jira.from_env({"JIRA_EMAIL": "x"})
        self.assertEqual(str(error.exception), "JIRA_CONFIG_MISSING:JIRA_BASE_URL,JIRA_API_TOKEN")
        for base in ("", "http://insecure.invalid"):
            with self.assertRaises(JiraError):
                Jira(base, "e", "t")

    def test_reads_retry_transient_failures_and_errors_never_leak_url_or_body(self):
        import urllib.error
        calls = []
        def fake(request, timeout=0):
            calls.append(request.method)
            if len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 503, "secret body text", {}, None)
            return Response(b'{"ok": 1}')
        client = Jira("https://example.invalid", "e", "t", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(client.get("/x"), {"ok": 1})
        self.assertEqual(len(calls), 3)
        def always(request, timeout=0):
            raise urllib.error.HTTPError(request.full_url, 403, "secret body text", {}, None)
        with patch("urllib.request.urlopen", side_effect=always):
            with self.assertRaises(JiraError) as error:
                client.get("/rest/secret/path")
        self.assertEqual(str(error.exception), "JIRA_HTTP_403")

    def test_writes_do_not_retry_unless_idempotent(self):
        import urllib.error
        calls = []
        def fake(request, timeout=0):
            calls.append(request.method)
            raise urllib.error.HTTPError(request.full_url, 503, "x", {}, None)
        client = Jira("https://example.invalid", "e", "t", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=fake):
            with self.assertRaises(JiraError):
                client.create_sprint(5, "Name", at(5), at(11))
            self.assertEqual(len(calls), 1)
            calls.clear()
            with self.assertRaises(JiraError):
                client.set_sprint_state(3, "closed")
            self.assertEqual(len(calls), 4)

    def test_paging_is_bounded_and_needs_progress(self):
        client = Jira("https://example.invalid", "e", "t")
        pages = iter([{"values": [1, 2], "isLast": False}, {"values": [3], "isLast": True}])
        with patch.object(client, "get", side_effect=lambda *a, **k: next(pages)):
            self.assertEqual(client.pages("/p"), [1, 2, 3])
        with patch.object(client, "get", return_value={"values": [1], "isLast": False}):
            with self.assertRaises(JiraError) as error:
                client.pages("/p")
        self.assertEqual(str(error.exception), "JIRA_TOO_MANY_PAGES")
        with patch.object(client, "get", return_value={"nope": 1}):
            with self.assertRaises(JiraError):
                client.pages("/p")

    def test_boards_config_is_strict(self):
        self.assertEqual(boards({"JIRA_BOARDS": "AAA:11, BBB:22:all"}), (("AAA", 11, False, False), ("BBB", 22, True, False)))
        for bad in ("", "AAA", "AAA:x", "AAA:1:wide", "AAA:1:all:all", "AAA:1,AAA:2", "A-A:1"):
            with self.assertRaises(JiraError):
                boards({"JIRA_BOARDS": bad})


class RolloverTests(unittest.TestCase):
    def test_dry_run_plans_and_writes_nothing(self):
        client = FakeJira(week(), [("K-1", 1, "To Do"), ("K-2", 1, "Done")])
        result = run(client, False, at(5, 7))
        self.assertEqual((result["would_create"], result["would_carry"], result["writes"], result["failed"]), (1, 1, 0, 0))
        self.assertEqual(client.log, [])

    def test_dry_run_reuses_an_existing_future_sprint_for_the_target_week(self):
        client = FakeJira(week([sprint(2, "future", at(5), datetime(2026, 10, 11, 23, 59, 59, tzinfo=TZ))]))
        result = run(client, False, at(5, 7))
        self.assertEqual((result["reuse"], result["would_create"]), (1, 0))

    def test_live_rollover_carries_forward_then_closes_then_starts_and_verifies(self):
        client = FakeJira(week(), [("K-1", 1, "To Do"), ("K-2", 1, "In Progress"), ("K-3", 1, "Done")])
        result = run(client, True, at(5, 6))
        self.assertEqual(client.log, ["create", "move", "closed", "active"])
        self.assertEqual((result["created"], result["carried"], result["closed"], result["started"], result["writes"]), (1, 2, 1, 1, 3))
        states = {s["id"]: s["state"] for s in client.sprints_by_id.values()}
        self.assertEqual(states[1], "closed")
        self.assertEqual([s for i, s in states.items() if i != 1], ["active"])
        self.assertEqual(sorted(client.members[client.next_id]), ["K-1", "K-2"])

    def test_live_refuses_to_close_a_sprint_before_it_ends(self):
        client = FakeJira(week(), [("K-1", 1, "To Do")])
        with self.assertRaises(JiraError) as error:
            run(client, True, datetime(2026, 10, 4, 12, 0, tzinfo=TZ))
        self.assertIn("JIRA_EARLY_ROLLOVER", str(error.exception))
        self.assertEqual(client.log, [])

    def test_ambiguous_or_unexpected_boards_fail_closed_with_no_writes(self):
        cases = (FakeJira(week([sprint(2, "active", at(28), at(4))])),                 # two active sprints
                 FakeJira([], []),                                                     # nothing to roll
                 FakeJira(week(), scrum=False),                                        # not a scrum board
                 FakeJira([sprint(1, "active", at(29), datetime(2026, 10, 7, 23, 59, 59, tzinfo=TZ))]),   # ends Wednesday
                 FakeJira(week([sprint(2, "future", at(5), datetime(2026, 10, 11, 23, 59, 59, tzinfo=TZ)),
                                sprint(3, "future", at(5), datetime(2026, 10, 11, 23, 59, 59, tzinfo=TZ))])))
        for client in cases:
            with self.assertRaises(JiraError):
                run(client, True, at(5, 7))
            self.assertEqual(client.log, [])

    def test_catch_up_activates_the_just_ended_future_sprint_before_closing(self):
        client = FakeJira([sprint(1, "future", datetime(2026, 9, 28, tzinfo=TZ), datetime(2026, 10, 4, 23, 59, 59, tzinfo=TZ))], [("K-1", 1, "To Do")])
        result = run(client, True, at(5, 6))
        self.assertEqual(client.log, ["create", "active", "move", "closed", "active"])
        self.assertEqual(result["closed"], 1)

    def test_carry_forward_lag_replans_once_and_never_closes_before_it_is_verified(self):
        client = FakeJira(week(), [("K-1", 1, "To Do")], lag=1)
        slept = []
        result = run(client, True, at(5, 6), sleep=slept.append)
        self.assertEqual(slept, [15])
        self.assertLess(client.log.index("move"), client.log.index("closed"))
        self.assertEqual(result["closed"], 1)
        stuck = FakeJira(week(), [("K-1", 1, "To Do")], lag=5)
        with self.assertRaises(JiraError) as error:
            run(stuck, True, at(5, 6))
        self.assertIn("JIRA_CARRY_VERIFY_FAILED", str(error.exception))
        self.assertNotIn("closed", stuck.log)

    def test_output_is_counts_and_codes_only(self):
        client = FakeJira(week(), [("PRIVATE-1", 1, "To Do")])
        client.sprints_by_id[1]["name"] = "Private Sprint Name"
        text = json.dumps(run(client, False, at(5, 7)))
        for private in ("PRIVATE-1", "Private Sprint Name", "AAA"):
            self.assertNotIn(private, text)

    def test_one_failing_project_does_not_stop_the_others(self):
        class Two(FakeJira):
            def board(self, board_id):
                if board_id == 22:
                    raise JiraError("JIRA_HTTP_404")
                return super().board(board_id)
        client = Two(week(), [("K-1", 1, "To Do")])
        env = {**ENV, "JIRA_BOARDS": "AAA:11,BBB:22"}
        with self.assertRaises(JiraError) as error:
            rollover.run(1, True, environ=env, client=client, now=at(5, 6), sleep=lambda s: None)
        self.assertEqual(str(error.exception), "JIRA_ROLLOVER_FAILED:1of2:JIRA_HTTP_404@2")
        self.assertIn("closed", client.log)            # the healthy project still rolled


class ScheduledRolloverTests(unittest.TestCase):
    """Scheduled runs act only once the sprint has ended and it is Monday 6 AM local; every other hour is a quiet no-op."""

    def go(self, now, client=None, live=True):
        client = client or FakeJira(week(), issues=[("AAA-1", 1, "To Do"), ("AAA-2", 1, "Done")])
        return rollover.run(1, live, environ=ENV, client=client, now=now, sleep=lambda s: None, auto=True), client

    def test_mid_week_and_sunday_night_do_nothing_and_do_not_fail(self):
        for now in (at(2, 12), datetime(2026, 10, 4, 23, 59, tzinfo=TZ), datetime(2026, 10, 5, 5, 59, tzinfo=TZ)):
            out, client = self.go(now)
            self.assertEqual((out["not_due"], out["writes"], client.log), (1, 0, []))

    def test_monday_morning_rolls_over_once_and_the_next_hour_is_quiet(self):
        out, client = self.go(datetime(2026, 10, 5, 6, 7, tzinfo=TZ))
        self.assertEqual((out["created"], out["carried"], out["closed"], out["started"]), (1, 1, 1, 1))
        again, _ = self.go(datetime(2026, 10, 5, 7, 7, tzinfo=TZ), client)
        self.assertEqual((again["not_due"], again["writes"]), (1, 0))

    def test_scheduled_dry_run_writes_nothing(self):
        out, client = self.go(datetime(2026, 10, 5, 6, 7, tzinfo=TZ), live=False)
        self.assertEqual((out["would_create"], client.log), (1, []))


class SharedBoardTests(unittest.TestCase):
    """A board whose filter spans projects lists the other project's sprint too; cross-assigned work rides along."""
    ENV2 = {"JIRA_BOARDS": "AAA:11,BBB:22:readonly"}

    def shared(self):
        mine = sprint(1, "active", datetime(2026, 9, 28, tzinfo=TZ), datetime(2026, 10, 4, 23, 59, 59, tzinfo=TZ), "AAA Sprint")
        theirs = sprint(7, "active", datetime(2026, 9, 28, tzinfo=TZ), datetime(2026, 10, 4, 23, 59, 59, tzinfo=TZ), "BBB Sprint")
        return FakeJira([mine, theirs], issues=[("AAA-1", 1, "To Do"), ("BBB-9", 1, "To Do"), ("BBB-2", 7, "To Do")])

    def test_own_sprint_is_chosen_and_cross_assigned_items_are_carried(self):
        client = self.shared()
        env = {"JIRA_BOARDS": "AAA:11,BBB:22:readonly"}
        out = rollover.run(1, True, environ=env, client=client, now=datetime(2026, 10, 5, 0, 5, tzinfo=TZ))
        self.assertEqual((out["carried"], out["skipped_readonly"], out["closed"]), (2, 1, 1))
        self.assertEqual(client.sprints_by_id[7]["state"], "active")          # the other project's sprint is untouched
        self.assertEqual(client.members[7], {"BBB-2": "To Do"})
        self.assertEqual(client.sprints_by_id[1]["state"], "closed")

    def test_readonly_boards_are_never_rolled_over(self):
        client = self.shared()
        out = rollover.run(1, True, environ={"JIRA_BOARDS": "BBB:22:readonly"}, client=client, now=datetime(2026, 10, 5, 0, 5, tzinfo=TZ))
        self.assertEqual((out["skipped_readonly"], out["writes"], client.log), (1, 0, []))

    def test_unlabelled_extra_sprint_still_fails_closed(self):
        client = FakeJira(week([sprint(5, "active", at(0), at(6), "Other")]))
        with self.assertRaises(JiraError):
            run(client, False, at(7, 8))


class SnapshotTests(unittest.TestCase):
    class Board(FakeJira):
        def board_issues(self, board_id, jql, fields):
            def issue(key, status="To Do", category="new", parent=None):
                return {"key": key, "fields": {"summary": "Private summary", "status": {"name": status, "statusCategory": {"key": category}},
                                               "issuetype": {"name": "Task"}, "priority": {"name": "High"}, "duedate": None,
                                               "parent": {"key": parent} if parent else None}}
            if "status = blocked" in jql:
                return [issue("B-1", "Blocked")]
            if "duedate <" in jql:
                return [issue("O-1"), issue("O-2")]
            if "sprint is EMPTY" in jql:
                return [issue("T-1")]
            if "statusCategory = Done" in jql:
                return [issue("D-1", "Done", "done")]
            return [issue("C-1"), issue("C-2")]

    def test_snapshot_shape_and_counts_only_output(self):
        client = self.Board(week([sprint(2, "future", at(5), datetime(2026, 10, 11, 23, 59, 59, tzinfo=TZ))]))
        snap = snapshot.project_snapshot(client, "AAA", 11, False, at(5, 7))
        self.assertEqual((snap["schema"], len(snap["current_tasks"]), len(snap["next_tasks"]), len(snap["overdue"]),
                          len(snap["blocked"]), len(snap["triage"]), len(snap["done"])), (5, 2, 2, 2, 1, 1, 1))
        self.assertEqual(snap["current_sprint"]["id"], 1)
        self.assertEqual(snap["next_sprint"]["id"], 2)
        counts = snapshot.run(1, False, environ=ENV, client=client, now=at(5, 7))
        text = json.dumps(counts)
        for private in ("Private summary", "C-1", "AAA"):
            self.assertNotIn(private, text)
        self.assertEqual((counts["projects"], counts["ok"], counts["saved"], counts["current"], counts["blocked"]), (1, 1, 0, 2, 1))

    def test_gtv_work_is_collected_only_for_the_epics_own_project(self):
        client = self.Board(week())
        mine = snapshot.project_snapshot(client, "AAA", 11, False, at(5, 7), (), "AAA-9")
        other = snapshot.project_snapshot(client, "AAA", 11, False, at(5, 7), (), "ZZZ-9")
        none = snapshot.project_snapshot(client, "AAA", 11, False, at(5, 7))
        self.assertEqual(len(mine["gtv"]), 4)            # tasks under the epic plus their sub-tasks
        self.assertIsNone(other["gtv"])
        self.assertIsNone(none["gtv"])

    def test_dry_run_never_touches_the_database_and_live_saves_one_row_per_project(self):
        client = self.Board(week())
        def no_db():
            raise AssertionError("dry run connected to the database")
        snapshot.run(1, False, environ=ENV, client=client, now=at(5, 7), connect=no_db)
        executed = []
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *e): return False
            def execute(self, sql, args=None): executed.append((sql.split()[0], args))
        class Conn:
            def cursor(self): return Cursor()
        class Ctx:
            def __enter__(self): return Conn()
            def __exit__(self, *e): return False
        counts = snapshot.run(1, True, environ=ENV, client=client, now=at(5, 7), connect=Ctx)
        self.assertEqual(counts["saved"], 1)
        inserts = [a for verb, a in executed if verb == "INSERT"]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(json.loads(inserts[0][3])["project"], "AAA")

    def test_failed_board_is_reported_and_nothing_is_saved_for_it(self):
        client = self.Board(week(), scrum=False)
        with self.assertRaises(JiraError) as error:
            snapshot.run(1, False, environ=ENV, client=client, now=at(5, 7))
        self.assertEqual(str(error.exception), "JIRA_SNAPSHOT_FAILED:1of1:JIRA_BOARD_NOT_SCRUM@1")

    def test_probe_reports_layout_by_position_without_names(self):
        class Shared(self.Board):
            def sprint_issues(self, sprint_id, jql, fields):
                return [{"key": "X-1"}] * (2 if "project = AAA" in jql else 1)
        client = Shared(week([sprint(2, "active", datetime(2026, 9, 28, tzinfo=TZ), datetime(2026, 10, 4, tzinfo=TZ), "BBB Sprint")]))
        out = snapshot.probe(1, False, environ={**ENV, "JIRA_BOARDS": "AAA:11,BBB:22"}, client=client)
        entry = out["layout"]["1"]
        self.assertEqual((entry["scrum"], entry["active"], len(entry["sprints"])), (1, 2, 2))
        self.assertEqual(entry["sprints"][1]["name_has_key"], [2])
        self.assertEqual(entry["sprints"][0]["issues_by_project"], {"1": 2, "2": 1})
        text = json.dumps(out)
        for private in ("AAA", "BBB", "X-1", "Synthetic"):
            self.assertNotIn(private, text)

    def test_store_schema_is_one_row_per_project(self):
        self.assertIn("project_key VARCHAR(16) NOT NULL PRIMARY KEY", store.SCHEMA[0])


class WorkflowTests(unittest.TestCase):
    text = (ROOT / ".github/workflows/hourly.yml").read_text()

    def job(self):
        start = self.text.index("\n  jira:\n")
        return self.text[start:self.text.index("\n  report:\n")]

    def test_workflow_stays_within_githubs_25_dispatch_inputs(self):
        block = self.text[self.text.index("  workflow_dispatch:\n    inputs:\n"):self.text.index("\nenv:\n")]
        self.assertLessEqual(len(re.findall(r"^      [a-z_]+:$", block, re.M)), 25)       # no yaml dependency in CI

    def test_scheduled_runs_refresh_snapshot_and_card_but_never_roll_over_or_probe(self):
        job = self.job()
        self.assertRegex(self.text, r'jira:\n(?:.*\n)*?\s+default: "none"')
        auto = "github.event_name != 'workflow_dispatch' || inputs.tick"
        self.assertIn(auto, job.split("runs-on")[0])
        for step_id, nxt in (("jrollauto", "jsnap"), ("jsnap", "jcard"), ("jcard", "jprobe")):
            step = job[job.index(f"id: {step_id}"):job.index(f"id: {nxt}")]
            self.assertIn(auto, step.split("run:")[0], step_id)
        for step_id, nxt in (("jprobe", "jroll"), ("jroll", "Jira warning")):
            step = job[job.index(f"id: {step_id}\n"):job.index(nxt if nxt != "jroll" else "id: jroll\n")]
            self.assertNotIn(auto, step, step_id)

    def test_failures_are_warnings_and_never_fail_the_run(self):
        job = self.job()
        self.assertNotIn("\n    continue-on-error:", job)
        self.assertEqual(job.count("continue-on-error: true"), 5)
        self.assertIn("::warning", job)

    def test_other_credentials_are_blank_and_database_secrets_reach_only_the_snapshot_step(self):
        job = self.job()
        head, steps = job.split("    steps:")
        for name in ("NOTION_API_TOKEN", "GMAIL_OAUTH_REFRESH_TOKEN", "TINYFISH_API_KEY", "FIT_PROFILE_JSON",
                     "LIFEOS_ACQ_DB_PASSWORD", "LIFEOS_ACQ_SSH_PRIVATE_KEY", "HIRING_PIPELINE_PAGE_ID"):
            self.assertIn(f'{name}: ""', head, name)
        self.assertNotIn("secrets.LIFEOS_ACQ", head)
        snap = steps[steps.index("id: jsnap"):steps.index("id: jcard")]
        roll = steps[steps.index("id: jroll\n"):steps.index("Jira warning")]
        self.assertNotIn("NOTION_JIRA_TOKEN", steps[steps.index("id: jsnap"):steps.index("id: jcard")])
        self.assertIn("secrets.LIFEOS_ACQ_DB_PASSWORD", snap)
        self.assertNotIn("secrets.", roll)
        self.assertEqual(sorted(re.findall(r"secrets\.(\w+)", head)), ["JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_BOARDS", "JIRA_EMAIL", "JIRA_GTV_EPIC"])

    def test_no_jira_value_appears_anywhere_in_tracked_workflow_or_code(self):
        self.assertNotRegex(self.text, r"atlassian\.net|api\.atlassian\.com")
        for path in (ROOT / "lifeos" / "jira").glob("*.py"):
            self.assertNotRegex(path.read_text(), r"atlassian\.net|api\.atlassian\.com|[0-9a-f]{8}-[0-9a-f]{4}-")


if __name__ == "__main__":
    unittest.main()
