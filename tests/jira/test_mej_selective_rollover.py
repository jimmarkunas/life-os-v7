"""LIFE-915: the gated MEJ selective rollover, synthetic only. No live Jira call exists in this file or in the code under test: the backlog primitive is a mock, the gate is off by default,
and every refusal below must leave the source sprint open (no `closed` in the write log)."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lifeos.jira import rollover
from lifeos.platform.jira import Jira, JiraError
from tests.jira.test_jira import FakeJira, at, sprint, week
from tests.kit.http import Response

TZ = ZoneInfo("America/Chicago")
ROOT = Path(__file__).resolve().parents[2]
ENV = {"JIRA_BASE_URL": "https://example.invalid", "JIRA_EMAIL": "synthetic-user", "JIRA_API_TOKEN": "synthetic-token", "JIRA_BOARDS": "MEJ:38"}


class MejFake(FakeJira):
    """FakeJira with issue types, a backlog and the failure knobs the cases need."""

    KNOBS = ("backlog_lag", "backlog_fail", "read_fail", "drop", "leave_child", "sprint_field", "sprint_state")

    def __init__(self, sprints, issues=(), subtasks=None, types=None, **kw):
        knobs = {k: kw.pop(k) for k in self.KNOBS if k in kw}
        super().__init__(sprints, issues, subtasks=subtasks, **kw)
        self.types = dict(types or {})                           # key -> "Task" | "Story" | "Epic"
        self.backlog = set()
        self.backlog_lag = 0                                      # reads that hide the moved keys (eventual consistency)
        self.backlog_fail = None                                  # exception to raise from the move
        self.read_fail = None                                     # exception to raise from the backlog read
        self.drop = set()                                         # keys the backlog silently fails to take (partial)
        self.leave_child = False                                  # a child that stays in the sprint when its parent is backlogged
        self.sprint_field = None                                  # what the issue reads claim as their sprint id
        self.sprint_state = None                                  # override of client.sprint()["state"]
        for name, value in knobs.items():
            setattr(self, name, value)

    def sprint(self, sprint_id):
        out = super().sprint(sprint_id)
        if self.sprint_state and sprint_id == 1:
            out["state"] = self.sprint_state
        return out

    def sprint_issues(self, sprint_id, jql, fields):
        done = "!= Done" in jql
        rows = []
        for key, status in self.members.get(sprint_id, {}).items():
            if status == "Done" and done:
                continue
            sub = key in self.subtasks
            f = {"issuetype": {"name": "Sub-task" if sub else self.types.get(key, "Task"), "subtask": sub}}
            if sub:
                parent = self.subtasks[key]
                held = {k: s for items in self.members.values() for k, s in items.items()}          # a parent that left for the backlog has no known status
                f["parent"] = ({"key": parent, "fields": {"status": {"name": held[parent]}}} if parent in held else {"key": parent}) if parent else None
            if self.sprint_field is not None:
                f["sprint"] = {"id": self.sprint_field}
            rows.append({"key": key, "fields": f})
        return rows

    def move_to_backlog(self, keys):
        self.log.append("backlog")
        if self.backlog_fail:
            raise self.backlog_fail
        moving = [k for k in list(keys) + [c for c, parent in self.subtasks.items() if parent in keys] if not (self.leave_child and k in self.subtasks)]
        for items in self.members.values():
            for key in moving:
                if key in items and key not in self.drop:
                    items.pop(key)
                    self.backlog.add(key)

    def backlog_issues(self, board_id, jql, fields):
        if self.read_fail:
            raise self.read_fail
        if self.backlog_lag > 0:
            self.backlog_lag -= 1
            return []
        return [{"key": k} for k in sorted(self.backlog)]


def config(roster, finite=(), consent="matt-roster-1", backlog="client"):
    return {"roster": list(roster), "finite": list(finite), "consent": consent, "backlog": backlog}


def run(client, live, now, selective="unset", sleep=None, env=ENV, auto=False):
    cfg = None if selective in (None, "unset") else dict(selective)
    if cfg and cfg.get("backlog") == "client":
        cfg["backlog"] = client
    slept = sleep if sleep is not None else (lambda s: None)
    if selective == "unset":
        return rollover.run(1, live, environ=env, client=client, now=now, sleep=slept, auto=auto)
    return rollover.run(1, live, environ=env, client=client, now=now, sleep=slept, auto=auto, mej_selective=cfg)


def sprint_week():
    return week()


def fresh(types=None, subtasks=None, extra=(), **kw):
    """Source sprint 1 with an approved Task (M-1) and its child (M-2), an unapproved Task (M-3) and its child (M-4), a finite weekly occurrence (M-5) and a stranded child of a Done parent (M-6)."""
    issues = [("MEJ-1", 1, "To Do"), ("MEJ-2", 1, "To Do"), ("MEJ-3", 1, "In Progress"), ("MEJ-4", 1, "To Do"), ("MEJ-5", 1, "To Do"),
              ("MEJ-6", 1, "To Do"), ("MEJ-7", 1, "Done"), *extra]
    subs = {"MEJ-2": "MEJ-1", "MEJ-4": "MEJ-3", "MEJ-6": "MEJ-7"}
    subs.update(subtasks or {})
    return MejFake(week(), issues, subtasks=subs, types=types, **kw)


class GateOffByDefault(unittest.TestCase):
    def test_the_gate_is_off_unless_a_caller_passes_it_and_no_caller_does(self):
        users = [str(p.relative_to(ROOT)) for p in (ROOT / "lifeos").rglob("*.py") if "mej_selective" in p.read_text()]
        self.assertEqual(users, ["lifeos/jira/rollover.py"])             # nothing else (not run.py, not a workflow) passes it
        self.assertEqual([], [str(p) for p in (ROOT / ".github").rglob("*") if p.is_file() and "mej_selective" in p.read_text(errors="ignore")])

    def test_mej_with_the_gate_off_carries_everything_unfinished_exactly_as_main_does(self):
        a, b = fresh(), fresh()
        explicit_none = rollover.run(1, True, environ=ENV, client=a, now=at(5, 6), sleep=lambda s: None, mej_selective=None)
        default = rollover.run(1, True, environ=ENV, client=b, now=at(5, 6), sleep=lambda s: None)
        self.assertEqual(explicit_none, default)
        self.assertEqual(a.log, b.log)
        self.assertEqual(a.log, ["create", "move", "closed", "active"])
        self.assertNotIn("backlog", a.log)
        self.assertEqual(sorted(a.members[a.next_id]), ["MEJ-1", "MEJ-2", "MEJ-3", "MEJ-4", "MEJ-5"])      # everything unfinished moves; the Done parent's child is stranded (D181)
        self.assertEqual(default["stranded"], 1)

    def test_the_same_gate_on_another_project_changes_nothing(self):
        env = {**ENV, "JIRA_BOARDS": "AAA:11"}
        a, b = fresh(), fresh()
        with_gate = rollover.run(1, True, environ=env, client=a, now=at(5, 6), sleep=lambda s: None, mej_selective=config(["MEJ-1"], backlog=None))
        without = rollover.run(1, True, environ=env, client=b, now=at(5, 6), sleep=lambda s: None)
        self.assertEqual(with_gate, without)
        self.assertEqual(a.log, b.log)

    def test_dry_run_with_the_gate_off_has_main_keys_only(self):
        out = rollover.run(1, False, environ=ENV, client=fresh(), now=at(5, 6), sleep=lambda s: None)
        self.assertNotIn("would_backlog", out)
        self.assertEqual(out["would_carry"], 5)


class SelectiveCarry(unittest.TestCase):
    def go(self, client, roster=("MEJ-1",), finite=(), live=True, **kw):
        return run(client, live, at(5, 6), config(roster, finite, **kw))

    def test_an_approved_task_and_its_child_are_carried_once_and_the_rest_placed_in_the_backlog(self):
        client = fresh()
        out = self.go(client, finite=["MEJ-5"])
        self.assertEqual(client.log, ["create", "move", "backlog", "closed", "active"])         # one carry move, one backlog move, then close and start
        target = client.members[client.next_id]
        self.assertEqual(sorted(target), ["MEJ-1", "MEJ-2"])                                   # the approved Task with its child, once
        self.assertEqual(client.backlog, {"MEJ-3", "MEJ-4", "MEJ-5"})                         # the unapproved Task and child, and the finite occurrence
        self.assertEqual((out["carried"], out["backlogged"], out["stranded"], out["finite_missed"], out["closed"]), (2, 3, 1, 1, 1))
        self.assertEqual(client.sprints_by_id[1]["state"], "closed")

    def test_a_finite_weekly_occurrence_is_never_copied_never_carried_and_never_marked_done(self):
        client = fresh()
        self.go(client, finite=["MEJ-5"])
        statuses = {k: v for items in client.members.values() for k, v in items.items()}
        self.assertEqual(client.log.count("create"), 1)                                          # only the next sprint; no clone of the occurrence
        self.assertNotIn("MEJ-5", client.members[client.next_id])
        self.assertNotIn("MEJ-5", statuses)                                                      # it left the sprint for the backlog, still unfinished: its status was never written
        self.assertEqual(client.members[1].get("MEJ-7"), "Done")                                 # history preserved
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_FINITE_NOT_CARRY"):
            self.go(fresh(), roster=["MEJ-1", "MEJ-5"], finite=["MEJ-5"])

    def test_the_dry_run_previews_the_split_and_writes_nothing(self):
        client = fresh()
        out = self.go(client, finite=["MEJ-5"], live=False)
        self.assertEqual((out["would_carry"], out["would_backlog"], out["would_strand"], out["selective"], out["writes"]), (2, 3, 1, 1, 0))
        self.assertEqual(client.log, [])

    def test_an_empty_backlog_need_does_not_need_the_primitive(self):
        client = MejFake(week(), [("MEJ-1", 1, "To Do"), ("MEJ-2", 1, "To Do")], subtasks={"MEJ-2": "MEJ-1"})
        out = run(client, True, at(5, 6), config(["MEJ-1"], backlog=None))
        self.assertEqual((out["carried"], out["closed"]), (2, 1))
        self.assertNotIn("backlog", client.log)


class RosterAndInventory(unittest.TestCase):
    def refuses(self, client, code, cfg, now=None):
        with self.assertRaisesRegex(JiraError, code):
            run(client, True, now or at(5, 6), cfg)
        self.assertNotIn("closed", client.log)
        self.assertEqual([entry for entry in client.log if entry in ("closed", "active", "backlog", "move")], [])

    def test_an_incomplete_or_ambiguous_roster_fails_closed_before_any_write(self):
        for cfg in (config(["MEJ-1"], consent=""), config(["MEJ-1"], consent=None), config(["MEJ-1", "MEJ-1"]), config(["MEJ-99"]),
                    config(["JFM-1"]), config(["MEJ-2"]), config(["MEJ-%d" % n for n in range(1, 60)]), {"roster": "MEJ-1", "consent": "x"}, {"consent": "x"}):
            self.refuses(fresh(), "JIRA_MEJ_ROSTER_INVALID", cfg)

    def test_unapproved_work_with_no_proven_backlog_refuses_close(self):
        client = fresh()
        self.refuses(client, "JIRA_MEJ_BACKLOG_UNVERIFIED", config(["MEJ-1"], backlog=None))
        self.assertEqual(client.log, [])

    def test_a_malformed_inventory_fails_closed(self):
        client = fresh(types={"MEJ-3": ""})
        self.refuses(client, "JIRA_MEJ_INVENTORY_INVALID", config(["MEJ-1"]))

    def test_stale_sprint_membership_and_a_closed_source_fail_closed(self):
        self.refuses(fresh(sprint_field=77), "JIRA_MEJ_SPRINT_STALE", config(["MEJ-1"]))
        self.refuses(fresh(sprint_state="closed"), "JIRA_MEJ_SPRINT_STALE", config(["MEJ-1"]))

    def test_two_active_sprints_fail_closed(self):
        client = fresh()
        client.sprints_by_id[9] = sprint(9, "active", at(28), at(4))
        self.refuses(client, "JIRA_ACTIVE_AMBIGUOUS", config(["MEJ-1"]))

    def test_a_child_with_no_parent_fails_closed(self):
        client = fresh(subtasks={"MEJ-8": None}, extra=[("MEJ-8", 1, "To Do")])
        self.refuses(client, "JIRA_MEJ_INVENTORY_INVALID", config(["MEJ-1"]))

    def test_a_child_whose_parent_is_in_another_destination_fails_closed_after_the_move(self):
        client = fresh(leave_child=True)                                                         # the backlog takes MEJ-3 but the child MEJ-4 stays in the sprint
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_DESTINATION_MISMATCH"):                  # first pass: the readback lacks the child (partial), the re-plan sees the orphan
            run(client, True, at(5, 6), config(["MEJ-1"], finite=["MEJ-5"]))
        self.assertNotIn("closed", client.log)

    def test_a_carried_key_found_in_the_backlog_is_a_destination_mismatch(self):
        client = fresh()
        client.backlog.add("MEJ-1")                                                               # the approved Task is also read as backlog
        with self.assertRaisesRegex(JiraError, "JIRA_MEJ_DESTINATION_MISMATCH"):
            run(client, True, at(5, 6), config(["MEJ-1"], finite=["MEJ-5"]))
        self.assertNotIn("closed", client.log)


class BacklogFailures(unittest.TestCase):
    def refuses(self, client, code, finite=("MEJ-5",)):
        slept = []
        with self.assertRaisesRegex(JiraError, code):
            run(client, True, at(5, 6), config(["MEJ-1"], finite), sleep=slept.append)
        self.assertNotIn("closed", client.log)
        self.assertNotIn("active", client.log[client.log.index("move") + 1:] if "move" in client.log else [])
        return slept

    def test_a_403_429_or_timeout_on_the_move_fails_closed(self):
        for failure in (JiraError("JIRA_HTTP_403"), JiraError("JIRA_HTTP_429"), TimeoutError("synthetic"), OSError("synthetic")):
            client = fresh(backlog_fail=failure)
            self.refuses(client, "JIRA_MEJ_BACKLOG_FAILED")
            self.assertEqual(client.backlog, set())

    def test_a_failing_or_unreadable_backlog_readback_fails_closed(self):
        for failure in (JiraError("JIRA_HTTP_403"), JiraError("JIRA_HTTP_429"), TimeoutError("synthetic"), ValueError("shape")):
            self.refuses(fresh(read_fail=failure), "JIRA_MEJ_BACKLOG_FAILED")

    def test_a_partial_result_is_retried_once_then_refuses_close(self):
        client = fresh(drop={"MEJ-3"})
        slept = self.refuses(client, "JIRA_MEJ_BACKLOG_PARTIAL")
        self.assertEqual(slept, [15])                                                           # exactly one bounded re-plan
        self.assertEqual(client.log.count("backlog"), 2)

    def test_one_bounded_consistency_retry_recovers_a_lagging_readback(self):
        client = fresh(backlog_lag=1)
        slept = []
        out = run(client, True, at(5, 6), config(["MEJ-1"], ["MEJ-5"]), sleep=slept.append)
        self.assertEqual(slept, [15])
        self.assertEqual(out["closed"], 1)
        self.assertEqual(sorted(client.members[client.next_id]), ["MEJ-1", "MEJ-2"])           # carried once even though the plan ran twice
        self.assertEqual(client.log.count("closed"), 1)


class RestartRecovery(unittest.TestCase):
    def test_a_run_interrupted_after_the_carry_resumes_from_the_actual_state(self):
        client = fresh()
        sprint_2 = sprint(2, "future", at(5), datetime(2026, 10, 11, 23, 59, 59, tzinfo=TZ))
        client.sprints_by_id[2] = sprint_2
        client.members[2] = {"MEJ-1": "To Do", "MEJ-2": "To Do"}                                  # the earlier run already moved the approved Task and its child
        client.members[1].pop("MEJ-1")
        client.members[1].pop("MEJ-2")
        out = run(client, True, at(5, 6), config(["MEJ-1"], ["MEJ-5"]))
        self.assertEqual(client.log, ["backlog", "closed", "active"])                            # no second create, no second move of the carried work
        self.assertEqual((out["carried"], out["backlogged"], out["closed"]), (0, 3, 1))
        self.assertEqual(sorted(client.members[2]), ["MEJ-1", "MEJ-2"])

    def test_a_run_interrupted_after_the_backlog_move_is_not_repeated(self):
        client = fresh()
        client.members[1].pop("MEJ-3")
        client.members[1].pop("MEJ-4")
        client.members[1].pop("MEJ-5")
        client.backlog.update({"MEJ-3", "MEJ-4", "MEJ-5"})
        out = run(client, True, at(5, 6), config(["MEJ-1"], ["MEJ-5"]))
        self.assertNotIn("backlog", client.log)
        self.assertEqual((out["carried"], out["backlogged"], out["closed"]), (2, 0, 1))


class CadenceAndBoundaries(unittest.TestCase):
    def test_a_14_day_sprint_still_needs_its_staged_target_with_the_gate_on(self):
        long_sprint = sprint(1, "active", datetime(2026, 9, 21, tzinfo=TZ), datetime(2026, 10, 4, 23, 59, 59, tzinfo=TZ))
        client = MejFake([long_sprint], [("MEJ-1", 1, "To Do")])
        with self.assertRaisesRegex(JiraError, "JIRA_TARGET_NOT_STAGED"):
            run(client, True, at(5, 6), config(["MEJ-1"]))
        self.assertEqual(client.log, [])

    def test_a_7_day_week_is_created_with_the_same_name_and_dates_as_with_the_gate_off(self):
        on, off = fresh(), fresh()
        run(on, True, at(5, 6), config(["MEJ-1"], ["MEJ-5"]))
        rollover.run(1, True, environ=ENV, client=off, now=at(5, 6), sleep=lambda s: None)
        made_on = [s for i, s in on.sprints_by_id.items() if i not in (1,)][0]
        made_off = [s for i, s in off.sprints_by_id.items() if i not in (1,)][0]
        self.assertEqual({k: made_on[k] for k in ("name", "startDate", "endDate")}, {k: made_off[k] for k in ("name", "startDate", "endDate")})

    def test_the_clock_change_reuses_the_staged_sprint_and_never_duplicates_it(self):
        first = sprint(1, "active", datetime(2026, 10, 26, tzinfo=TZ), datetime(2026, 11, 1, 23, 30, tzinfo=TZ), "MEJ Sprint 1")
        for start, end in (("2026-11-02T05:00:00.000Z", "2026-11-09T04:30:00.000Z"), ("2026-11-02T06:00:00.000Z", "2026-11-09T05:30:00.000Z")):
            client = MejFake([first], [("MEJ-1", 1, "To Do")])
            client.sprints_by_id[2] = {"id": 2, "state": "future", "name": "MEJ Sprint 2", "startDate": start, "endDate": end}
            out = rollover.run(1, True, environ=ENV, client=client, now=datetime(2026, 11, 2, 0, 5, tzinfo=TZ), sleep=lambda s: None, auto=True,
                               mej_selective=config(["MEJ-1"], backlog=client))
            self.assertNotIn("create", client.log)
            self.assertEqual((out["closed"], out["carried"]), (1, 1))
            self.assertEqual(sum(1 for s in client.sprints_by_id.values() if s["state"] != "closed"), 1)

    def test_an_early_run_never_closes_with_the_gate_on(self):
        client = fresh()
        with self.assertRaisesRegex(JiraError, "JIRA_EARLY_ROLLOVER"):
            run(client, True, datetime(2026, 10, 4, 12, 0, tzinfo=TZ), config(["MEJ-1"], ["MEJ-5"]))
        self.assertNotIn("closed", client.log)


class Primitive(unittest.TestCase):
    def test_the_backlog_methods_exist_on_the_client_but_nothing_in_run_uses_them_live(self):
        calls = []

        def fake(request, timeout=0):
            calls.append((request.method, request.full_url.split("example.invalid")[1], request.data))
            return Response(b'{"issues": [], "isLast": true, "total": 0, "values": []}')
        client = Jira("https://example.invalid", "e", "t", sleep=lambda s: None)
        with patch("urllib.request.urlopen", side_effect=fake):
            client.move_to_backlog(["MEJ-%d" % n for n in range(1, 61)])
            client.backlog_issues(38, "statusCategory != Done", "summary")
        self.assertEqual([c[0:2] for c in calls[:2]], [("POST", "/rest/agile/1.0/backlog/issue")] * 2)           # 60 keys go in two bounded batches
        self.assertTrue(calls[2][1].startswith("/rest/agile/1.0/board/38/backlog"))
        sources = [str(p.relative_to(ROOT)) for p in (ROOT / "lifeos").rglob("*.py") if "move_to_backlog" in p.read_text()]
        self.assertEqual(sorted(sources), ["lifeos/jira/rollover.py", "lifeos/platform/jira.py"])


if __name__ == "__main__":
    unittest.main()
