"""LIFE-918 wiring: the daily reconcile stage, the rollover boundary with automation on, the live writers' gate, the contract check and throwaway verification, the Daily Report line and the promotion call site.
Everything is synthetic: fake Jira, fake database, mocked writers; no network, no Jira or Notion write. With every switch off the old paths are untouched (the existing rollover and reconcile suites pin that too)."""
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lifeos import run as entry
from lifeos.jira import card, reconcile as rc, reconcile_stage as stage, recurrence, rollover, writers as jw
from lifeos.platform.jira import JiraError

TZ = ZoneInfo("America/Chicago")
OFF = {"JIRA_BOARDS": "JFM:5"}
ON = {"JIRA_BOARDS": "JFM:5", "MEJ_SPRINT_AUTOMATION": "on"}
VERIFIED = {**ON, "JIRA_WRITERS_VERIFIED": "yes"}


def at(y, m, d, h=9, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def monday(y, m, d):
    return date(y, m, d)


def sprint_row(sid, project, week, state):
    start, end = rc.week_bounds(week, TZ)
    return {"id": sid, "name": rc.iso_name(project, week), "state": state, "startDate": start.isoformat(), "endDate": end.isoformat()}


def issue(key, type_="Task", status="To Do", category="new", priority="Medium", labels=(), sprint_id=None, links=(), due=None, parent=None):
    return {"key": key, "fields": {"issuetype": {"name": type_, "subtask": type_ == "Sub-task"}, "status": {"name": status, "statusCategory": {"key": category}}, "priority": {"name": priority},
                                   "labels": list(labels), "issuelinks": list(links), "sprint": {"id": sprint_id} if sprint_id else None, "duedate": due, "summary": f"s {key}", "description": None,
                                   "parent": {"key": parent} if parent else None, "subtasks": [], "assignee": None}}


class World:
    """A tiny Jira board: sprints, issues (their sprint in fields.sprint), a backlog, and a log of every mutating call."""

    def __init__(self, sprints, issues):
        self.sprints_by_id = {s["id"]: dict(s) for s in sprints}
        self.issues = {i["key"]: i for i in issues}
        self.log, self.next = [], 900

    def board(self, board_id):
        return {"type": "scrum"}

    def sprints(self, board_id, state):
        return [dict(s) for s in self.sprints_by_id.values() if s["state"] == state]

    def sprint(self, sprint_id):
        return dict(self.sprints_by_id[sprint_id])

    def _in(self, sprint_id):
        return [i for i in self.issues.values() if ((i["fields"].get("sprint") or {}).get("id")) == sprint_id]

    def sprint_issues(self, sprint_id, jql, fields):
        rows = self._in(sprint_id)
        if "!= Done" in jql:
            rows = [i for i in rows if i["fields"]["status"]["statusCategory"]["key"] != "done"]
        if "labels = recurring" in jql:
            rows = [i for i in rows if "recurring" in i["fields"]["labels"]]
        return [dict(i) for i in rows]

    def board_issues(self, board_id, jql, fields):
        rows = list(self.issues.values())
        if "labels = recurring" in jql:
            rows = [i for i in rows if "recurring" in i["fields"]["labels"]]
        if "statusCategory = Done" in jql:
            rows = [i for i in rows if i["fields"]["status"]["statusCategory"]["key"] == "done"]
        return [dict(i) for i in rows]

    def backlog_issues(self, board_id, jql, fields):
        return [{"key": k} for k, i in self.issues.items() if not i["fields"].get("sprint") and i["fields"]["status"]["statusCategory"]["key"] != "done"]

    def move_issues(self, sprint_id, keys):
        self.log.append(("move", sprint_id, tuple(keys)))
        for key in keys:
            self.issues[key]["fields"]["sprint"] = {"id": sprint_id}

    def set_sprint_state(self, sprint_id, state):
        self.log.append(("state", sprint_id, state))
        self.sprints_by_id[sprint_id]["state"] = state

    def writers(self):
        def create_sprint(board_id, name, start, end):
            self.next += 1
            self.sprints_by_id[self.next] = {"id": self.next, "name": name, "state": "future", "startDate": start.isoformat(), "endDate": end.isoformat()}
            self.log.append(("create_sprint", name))
            return {"id": self.next}

        def rename_sprint(sprint_id, name):
            self.sprints_by_id[sprint_id]["name"] = name
            self.log.append(("rename", name))

        def to_backlog(keys):
            self.log.append(("backlog", tuple(keys)))
            for key in keys:
                self.issues[key]["fields"]["sprint"] = None

        def create_clone(root, fields, sprint_id=None, origin=None):
            self.next += 1
            key = f"JFM-{self.next}"
            made = issue(key, labels=fields["labels"], sprint_id=sprint_id, links=[{"type": {"name": "Cloners"}, "outwardIssue": {"key": origin or root}}], due=fields["duedate"])
            made["fields"]["summary"] = fields["summary"]
            self.issues[key] = made
            self.log.append(("clone", origin or root, sprint_id))
            return key

        def close_done(key, run_id):
            f = self.issues[key]["fields"]
            f["labels"] = f["labels"] + ["auto-closed"]
            f["status"] = {"name": "Done", "statusCategory": {"key": "done"}}
            self.log.append(("close", key, run_id))

        def sync_fields(key, drift):
            self.log.append(("sync", key))
        return rc.Writers(create_sprint=create_sprint, rename_sprint=rename_sprint, move_to_backlog=to_backlog, create_clone=create_clone, close_done=close_done, sync_fields=sync_fields)


def boundary_world():
    old = sprint_row(1, "JFM", monday(2026, 11, 23), "active")
    issues = [issue("JFM-1", status="In Progress", category="indeterminate", priority="Low", sprint_id=1), issue("JFM-2", priority="Low", sprint_id=1), issue("JFM-3", priority="High", sprint_id=1),
              issue("JFM-4", labels=["recurring"], due="2026-11-30", sprint_id=1), issue("JFM-5", status="Done", category="done", sprint_id=1)]
    return World([old], issues)


class Boundary(unittest.TestCase):
    NOW = at(2026, 11, 30, 0, 5)

    def automation(self, world):
        return {"writers": world.writers(), "run_id": "run-7", "roster": None}

    def test_the_boundary_names_the_next_sprint_short_applies_the_rule_clones_once_and_closes_the_original_done(self):
        world = boundary_world()
        out = rollover.run(1, True, environ=ON, client=world, now=self.NOW, sleep=lambda s: None, auto=True, automation=self.automation(world))
        self.assertEqual((out["created"], out["carried"], out["backlogged"], out["cloned"], out["recurring_closed"]), (1, 2, 1, 1, 1))
        self.assertEqual({s["name"] for s in world.sprints_by_id.values()}, {"JFM W48 2026", "JFM W49 2026"})
        new = [s for s in world.sprints_by_id.values() if s["name"] == "JFM W49 2026"][0]
        self.assertEqual((world.sprints_by_id[1]["state"], new["state"]), ("closed", "active"))
        in_new = sorted(i["key"] for i in world._in(new["id"]))
        self.assertEqual(in_new[:2], ["JFM-1", "JFM-3"])
        self.assertEqual(len(in_new), 3)                                                              # the two carried issues and exactly one clone
        original = world.issues["JFM-4"]["fields"]
        self.assertEqual((original["status"]["statusCategory"]["key"], "auto-closed" in original["labels"]), ("done", True))
        self.assertIn(("close", "JFM-4", "run-7"), world.log)
        self.assertFalse(world.issues["JFM-2"]["fields"]["sprint"])                                   # backlog, not carried

    def test_a_crash_before_the_close_plans_from_the_actual_state_and_never_clones_twice(self):
        world = boundary_world()
        world.set_sprint_state(1, "active")
        auto = self.automation(world)
        rollover.run(1, True, environ=ON, client=world, now=self.NOW, sleep=lambda s: None, auto=True, automation=auto)
        clones = [e for e in world.log if e[0] == "clone"]
        self.assertEqual(len(clones), 1)

    def test_unverified_writers_fail_closed_before_any_write(self):
        world = boundary_world()
        with self.assertRaises(JiraError) as error:
            rollover.run(1, True, environ=ON, client=world, now=self.NOW, sleep=lambda s: None, auto=True, automation={"writers": rc.Writers(), "run_id": "r", "roster": None})
        self.assertIn("JIRA_RECONCILE_WRITER_UNVERIFIED", str(error.exception))
        self.assertEqual([e for e in world.log if e[0] in ("create_sprint", "backlog", "clone", "close", "state")], [])

    def test_dry_run_reports_counts_and_writes_nothing(self):
        world = boundary_world()
        out = rollover.run(1, False, environ=ON, client=world, now=self.NOW, sleep=lambda s: None, auto=True, automation=self.automation(world))
        self.assertEqual((out["would_carry"], out["would_backlog"], out["would_clone"], out["would_close_recurring"]), (2, 1, 1, 1))
        self.assertEqual(world.log, [])

    def test_the_switch_off_never_builds_writers_or_changes_the_path(self):
        with patch("lifeos.jira.writers.live_writers", side_effect=AssertionError("built writers")):
            self.assertFalse(rc.enabled(OFF))
            self.assertTrue(rc.enabled(ON))
        world = boundary_world()
        with patch("lifeos.jira.rollover.run_project", return_value={"not_due": 1}) as project:
            rollover.run(1, False, environ=OFF, client=world, now=self.NOW, auto=True)
        self.assertEqual(len(project.call_args.args), 9)                                           # exactly the pre-LIFE-918 call shape, no `automation`


class FakeCursor:
    def __init__(self, db):
        self.db, self.row = db, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        if sql.startswith("SELECT last_ok"):
            value = self.db.rows.get(params[0])
            self.row = (value["last_ok"],) if value else None
        elif sql.startswith("SELECT last_notified"):
            value = self.db.rows.get(params[0])
            self.row = (value["last_notified"],) if value and value.get("last_notified") else None
        elif sql.startswith("INSERT INTO v7_jira_rollover") and "VALUES (%s,%s,0,NULL)" in sql:
            self.db.rows[params[0]] = {"last_ok": params[1], "last_notified": None}
        elif sql.startswith("INSERT INTO v7_jira_rollover"):
            self.db.rows[params[0]] = {"last_ok": params[1], "last_notified": params[2]}

    def fetchone(self):
        return self.row


class FakeDb:
    def __init__(self):
        self.rows, self.snapshots = {}, {}

    def cursor(self):
        return FakeCursor(self)

    def connect(self):
        @contextmanager
        def go():
            yield self
        return go()


class Stage(unittest.TestCase):
    def world(self):
        week = monday(2026, 11, 23)
        return World([sprint_row(10, "JFM", week, "active")], [issue("R-1", labels=["recurring"], due="2026-11-23", sprint_id=10)])

    def run_stage(self, live, env, db, world, pushes, now=at(2026, 11, 25, 9)):
        with patch("lifeos.jira.reconcile_stage.save_summary", side_effect=lambda c, n, line, ok: db.snapshots.update({"line": line, "ok": ok})):
            return stage.run(5, live, environ=env, client=world, now=now, connect=db.connect, writers_=world.writers(), sleep=lambda s: None,
                             push=lambda key, title, body, topic: pushes.append((key, title, body)) or True)

    def test_off_by_default_nothing_is_built_or_read(self):
        with patch("lifeos.platform.jira.Jira.from_env", side_effect=AssertionError("built a client")), patch("lifeos.platform.db.connect", side_effect=AssertionError("opened the database")):
            self.assertEqual(stage.run(5, True, environ=OFF), {"enabled": 0})
        self.assertEqual(entry.STAGES["jira-reconcile-daily"].target, ("lifeos.jira.reconcile_stage", "run"))

    def test_dry_run_plans_the_sprints_and_the_clone_and_writes_nothing(self):
        db, world, pushes = FakeDb(), self.world(), []
        out = self.run_stage(False, ON, db, world, pushes)
        self.assertEqual((out["ok"], out["failed"], out["would_create"], out["would_clone"]), (1, 0, 4, 1))
        self.assertEqual(world.log, [])
        self.assertIn("would add", out["summary"])
        self.assertEqual((db.rows, db.snapshots), ({}, {}))                                           # a dry run marks nothing and saves nothing
        self.assertEqual(out["promotion"], {"status": "not_enabled"})

    def test_live_with_mock_writers_creates_the_four_sprints_and_the_clone_then_marks_the_day(self):
        db, world, pushes = FakeDb(), self.world(), []
        out = self.run_stage(True, ON, db, world, pushes)
        self.assertEqual((out["created"], out["cloned"], out["failed"]), (4, 1, 0))
        self.assertEqual(len([e for e in world.log if e[0] == "clone"]), 1)
        self.assertTrue(db.snapshots["ok"])
        self.assertIn("1 of 1 boards in order", db.snapshots["line"])
        again = self.run_stage(True, ON, db, world, pushes, now=at(2026, 11, 25, 15))
        self.assertEqual(again, {"enabled": 1, "not_due": 1})                                          # once per Chicago date

    def test_a_second_pass_on_the_next_day_creates_nothing_new(self):
        db, world, pushes = FakeDb(), self.world(), []
        self.run_stage(True, ON, db, world, pushes)
        before = len(world.log)
        out = self.run_stage(True, ON, db, world, pushes, now=at(2026, 11, 26, 9))
        self.assertEqual((out["created"], out["cloned"], out["renamed"]), (0, 0, 0))
        self.assertEqual(len(world.log), before)

    def test_unverified_live_writers_fail_closed_and_one_alert_per_code_per_day(self):
        db, world, pushes = FakeDb(), self.world(), []
        with patch("lifeos.jira.reconcile_stage.save_summary", side_effect=lambda c, n, line, ok: db.snapshots.update({"line": line, "ok": ok})):
            for _ in range(2):
                with self.assertRaises(JiraError) as error:
                    stage.run(5, True, environ=ON, client=world, now=at(2026, 11, 25, 9), connect=db.connect, sleep=lambda s: None,
                              push=lambda key, title, body, topic: pushes.append((key, title, body)) or True)
                self.assertIn("JIRA_RECONCILE_WRITER_UNVERIFIED", str(error.exception))
        self.assertEqual(len(pushes), 1)                                                              # the same failure the same day pushes once
        self.assertEqual(pushes[0][0], "jira_reconcile_failed")
        self.assertFalse(db.snapshots["ok"])
        self.assertTrue(db.snapshots["line"].startswith("Jira sprints: DEGRADED"))
        self.assertEqual([e for e in world.log if e[0] in ("create_sprint", "clone")], [])

    def test_the_live_writers_exist_only_behind_the_verified_row(self):
        client = object()
        for env in (None, {}, {"JIRA_WRITERS_VERIFIED": "no"}, {"JIRA_WRITERS_VERIFIED": "true"}, {"JIRA_WRITERS_VERIFIED": ""}):
            with self.assertRaises(JiraError) as error:
                jw.live_writers(client, env).need("create_sprint")
            self.assertEqual(str(error.exception), "JIRA_RECONCILE_WRITER_UNVERIFIED")
        self.assertTrue(callable(jw.live_writers(Writers.Client(), {"JIRA_WRITERS_VERIFIED": " YES "}).need("create_clone")))


class Counts(unittest.TestCase):
    def test_the_split_starts_at_activation_and_earlier_closures_count_only_in_the_total(self):
        def done(key, when, labels=()):
            made = issue(key, status="Done", category="done", labels=labels)
            made["fields"]["statuscategorychangedate"] = when.isoformat()
            return made
        issues = [done("A-1", at(2026, 11, 24, 10), ["auto-closed"]), done("A-2", at(2026, 11, 25, 16), ["auto-closed"]), done("A-3", at(2026, 11, 25, 17)), issue("A-4")]
        self.assertEqual(rc.closure_counts(issues), {"manual": None, "automatic": None, "total": 3, "split": False})
        self.assertEqual(rc.closure_counts(issues, at(2026, 11, 25, 14)), {"manual": 1, "automatic": 1, "total": 3, "split": True})
        self.assertEqual(rc.since({"MEJ_AUTOMATION_SINCE": "2026-11-25T14:00:00-06:00"}), at(2026, 11, 25, 14))
        for bad in (None, "", "yesterday", "2026-11-25T14:00:00"):
            self.assertIsNone(rc.since({"MEJ_AUTOMATION_SINCE": bad}))

    def test_the_summary_line_is_counts_only_and_shows_the_split_only_when_it_exists(self):
        total = {"ok": 2, "boards": 2, "created": 4, "renamed": 1, "cloned": 3, "closures_total": 6}
        self.assertEqual(recurrence.summary(total, True), "Jira sprints: 2 of 2 boards in order, 4 sprints added, 1 renamed, 3 recurring cloned · closed this week 6")
        self.assertIn("(2 automatic, 4 manual)", recurrence.summary({**total, "closures_automatic": 2, "closures_manual": 4}, True))


class Line(unittest.TestCase):
    def test_the_card_line_is_the_summary_or_degraded(self):
        now = at(2026, 11, 25, 9)
        good = {"ok": True, "line": "Jira sprints: 2 of 2 boards in order", "taken_at": (now - timedelta(hours=1)).isoformat()}
        self.assertEqual(card.reconcile_line(good, now), "Jira sprints: 2 of 2 boards in order")
        self.assertTrue(card.reconcile_line({**good, "taken_at": (now - timedelta(hours=40)).isoformat()}, now).startswith("Jira sprints: DEGRADED"))
        self.assertTrue(card.reconcile_line(None, now).startswith("Jira sprints: DEGRADED"))
        self.assertIn("JIRA_X", card.reconcile_line({"ok": False, "line": "Jira sprints: DEGRADED · JIRA_X", "taken_at": now.isoformat()}, now))


class Promotion(unittest.TestCase):
    BOUNDARY = datetime(2027, 1, 1, 8, 5, tzinfo=TZ)

    def call(self, env, now=None, readers=None):
        alerts, setter_calls = [], []

        class Setter:
            pass
        with patch("lifeos.accountability.official_setter.official_setter", return_value=lambda: setter_calls.append(1)):
            out = stage.promotion(env, now or self.BOUNDARY, True, lambda subject, details, key=None, once=False: alerts.append((key, once)), client_factory=lambda env: object(), readers=readers)
        return out, alerts, setter_calls

    def passing(self):
        board = {"frozen": ["A-1"], "counted": ["A-1"], "goals": {"A-1": ["G"]}, "completions": {"A-1": {"source": "changelog", "date": "2026-12-01"}},
                 "history": {"before": 1, "after": 1}, "recurring": {"manual": 1, "auto": 1, "total": 2}, "discrepancies": []}
        return lambda: {"quarter": "2027-Q1", "boards": {"JFM": board, "MEJ": board}}

    def test_the_kill_switch_off_does_nothing(self):
        out, alerts, setter = self.call({})
        self.assertEqual((out, alerts, setter), ({"status": "not_enabled"}, [], []))

    def test_a_pass_calls_the_setter_exactly_once_and_a_second_run_does_not(self):
        out, alerts, setter = self.call({"V2_PROMOTION": "on"}, readers=self.passing())
        self.assertEqual((out["status"], len(setter), alerts), ("promoted", 1, []))
        out, _, setter = self.call({"V2_PROMOTION": "on", "V2_OFFICIAL": "on"}, readers=self.passing())
        self.assertEqual((out["status"], setter), ("already_official", []))

    def test_before_the_boundary_nothing_happens(self):
        out, alerts, setter = self.call({"V2_PROMOTION": "on"}, now=datetime(2026, 12, 31, 23, 0, tzinfo=TZ), readers=self.passing())
        self.assertEqual((out["status"], alerts, setter), ("not_due", [], []))

    def test_the_shipped_empty_membership_keeps_legacy_and_sends_one_deduped_alert(self):
        out, alerts, setter = self.call({"V2_PROMOTION": "on"})
        self.assertEqual((out["status"], setter, alerts), ("kept_legacy", [], [("v2_promotion_failed", True)]))


class Writers(unittest.TestCase):
    """The contract check is GET only; the throwaway verification refuses what it did not create and always cleans up."""

    class Client:
        def __init__(self, fail_at=None):
            self.calls, self.fail_at, self.clone, self.sprint_id, self.sprint, self.links = [], fail_at, None, None, None, []
            self.deleted = []

        def board(self, board_id):
            return {"type": "scrum"}

        def sprints(self, board_id, state):
            self.calls.append(("GET", "sprints"))
            return [self.sprint] if (self.sprint and state == "future") else []

        def backlog_issues(self, board_id, jql, fields):
            return [{"key": self.clone}] if getattr(self, "in_backlog", False) else []

        def link_types(self):
            return [{"name": "Cloners", "outward": "clones", "inward": "is cloned by"}]

        def field_list(self):
            return [{"name": n} for n in ("Sprint", "Labels", "Linked Issues", "Due date")]

        def create_sprint(self, board_id, name, start, end):
            self.calls.append(("POST", "sprint", name))
            self.sprint_id, self.sprint = 777, {"id": 777, "name": name}
            return {"id": 777}

        def rename_sprint(self, sprint_id, name):
            self.sprint["name"] = name

        def issue(self, key, fields):
            if key == self.clone:
                return {"key": key, "fields": {"summary": self.summary, "labels": self.labels, "status": {"statusCategory": {"key": self.category}},
                                               "issuelinks": [{"type": {"name": "Cloners"}, "outwardIssue": {"key": "JFM-1"}}]}}
            return {"key": key, "fields": {"summary": "s", "description": None, "assignee": None, "priority": {"name": "Medium"}, "labels": ["recurring"], "parent": None, "duedate": None,
                                           "issuetype": {"name": "Task"}, "project": {"key": "JFM"}, "issuelinks": [], "subtasks": []}}

        def create_issue(self, fields):
            self.clone, self.summary, self.labels, self.category = "JFM-900", fields["summary"], ["recurring"], "new"
            return {"key": "JFM-900"}

        def link_issues(self, kind, inward, outward):
            self.calls.append(("POST", "link", kind, inward, outward))

        def move_issues(self, sprint_id, keys):
            self.in_backlog, self.held = False, set(keys)

        def move_to_backlog(self, keys):
            self.in_backlog, self.held = True, set()

        def sprint_issues(self, sprint_id, jql, fields):
            return [{"key": k} for k in getattr(self, "held", ())]

        def edit_issue(self, key, fields=None, update=None):
            if fields and "summary" in fields:
                self.summary = fields["summary"]
            if update:
                self.labels = self.labels + ["auto-closed"]

        def add_comment(self, key, text):
            self.calls.append(("POST", "comment", text))

        def transitions(self, key):
            return [{"id": "31", "to": {"statusCategory": {"key": "done"}}}]

        def transition(self, key, transition_id):
            self.category = "done"

        def delete_issue(self, key):
            self.deleted.append(("issue", key))

        def delete_sprint(self, sprint_id):
            self.deleted.append(("sprint", sprint_id))

    BOARDS = (("JFM", 5, False, False),)

    def test_the_contract_check_reads_only(self):
        client = self.Client()
        out = jw.contract_check(client, self.BOARDS)
        self.assertTrue(out["ok"])
        self.assertTrue(all(call[0] == "GET" for call in client.calls))

    def test_a_dry_run_verification_writes_nothing(self):
        client = self.Client()
        out = jw.verify(client, self.BOARDS, "JFM", "JFM-1", False)
        self.assertEqual((out["mode"], out["ok"]), ("dry_run", True))
        self.assertEqual([c for c in client.calls if c[0] == "POST"], [])

    def test_bad_arguments_are_refused_before_anything_runs(self):
        for board, key in (("MEJ", "JFM-1"), ("JFM", "jfm-1"), ("JFM", "JFM-1; DROP"), ("JFM", "")):
            with self.assertRaises(JiraError) as error:
                jw.verify(self.Client(), self.BOARDS, board, key, True)
            self.assertEqual(str(error.exception), "JIRA_VERIFY_ARGUMENTS_INVALID")

    def test_the_live_verification_passes_every_step_and_removes_only_what_it_made(self):
        client = self.Client()
        out = jw.verify(client, self.BOARDS, "JFM", "JFM-1", True, now=at(2026, 11, 25, 9))
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(out["steps"]), 8)
        self.assertTrue(client.sprint["name"].startswith(jw.TEST_PREFIX))
        self.assertEqual(client.deleted, [("issue", "JFM-900"), ("sprint", 777)])
        self.assertIn(("POST", "link", "Cloners", "JFM-1", "JFM-900"), client.calls)

    def test_a_failing_step_still_cleans_up(self):
        client = self.Client()
        client.transitions = lambda key: []                                                            # no Done transition: close fails
        with self.assertRaises(JiraError):
            jw.verify(client, self.BOARDS, "JFM", "JFM-1", True, now=at(2026, 11, 25, 9))
        self.assertEqual(client.deleted, [("issue", "JFM-900"), ("sprint", 777)])


if __name__ == "__main__":
    unittest.main()
