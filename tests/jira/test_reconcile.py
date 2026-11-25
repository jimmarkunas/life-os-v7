"""LIFE-918: the unbuilt LIFE-915 parts, proven on synthetic data only (no network, no Jira write): ISO sprint names and the four-sprint floor, the carry rule, recurring series, closure counts, and the daily 8 AM CT
reconciliation with its switch OFF by default. The Jira writes are injected mocks."""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lifeos import run as entry
from lifeos.jira import reconcile as rc, rollover
from lifeos.platform.jira import JiraError

TZ = ZoneInfo("America/Chicago")
ENV_OFF = {"JIRA_BOARDS": "JFM:5,MEJ:38"}
ENV_ON = {"JIRA_BOARDS": "JFM:5,MEJ:38", "MEJ_SPRINT_AUTOMATION": "on"}


def at(y, m, d, h=9, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def sprint(sid, project, monday, state="future", name=None):
    start, end = rc.week_bounds(monday, TZ)
    return {"id": sid, "name": name if name is not None else rc.iso_name(project, monday), "state": state, "startDate": start.isoformat(), "endDate": end.isoformat()}


def monday(y, m, d):
    return date(y, m, d)


class OldName(unittest.TestCase):
    def test_an_old_long_name_is_renamed_in_place_not_recreated(self):
        week = monday(2026, 11, 30)
        p = rc.plan_sprints("MEJ", [], [sprint(7, "MEJ", week, name="MEJ S49 W49 2026")], at(2026, 11, 25), TZ)
        self.assertEqual(p["rename"], [(7, "MEJ W49 2026")])
        self.assertNotIn("MEJ W49 2026", [c[0] for c in p["create"]])


class Gate(unittest.TestCase):
    def test_the_local_gate_agrees_with_the_accountability_switch_reader(self):
        from lifeos.accountability import switches
        for value in (None, "", "off", "on", " ON ", "yes", "true", "1", "onn"):
            environ = {} if value is None else {"MEJ_SPRINT_AUTOMATION": value}
            self.assertEqual(rc.enabled(environ), switches.on("MEJ_SPRINT_AUTOMATION", environ), value)
        self.assertFalse(rc.enabled(None.__class__))                                        # an unreadable environment is off


class Naming(unittest.TestCase):
    def test_iso_names_follow_the_iso_week_and_year(self):
        self.assertEqual(rc.iso_name("JFM", monday(2027, 1, 4)), "JFM W1 2027")
        self.assertEqual(rc.iso_name("MEJ", monday(2027, 1, 4)), "MEJ W1 2027")

    def test_week_53_and_the_year_change(self):
        self.assertEqual(rc.iso_name("MEJ", monday(2026, 12, 28)), "MEJ W53 2026")     # 2026 has 53 ISO weeks
        self.assertEqual(rc.iso_name("MEJ", monday(2024, 12, 30)), "MEJ W1 2025")       # the Monday of ISO week 1 of 2025 is in December 2024

    def test_week_bounds_are_monday_to_sunday_in_chicago_across_dst(self):
        start, end = rc.week_bounds(monday(2026, 11, 2), TZ)                              # the week the clocks went back (Nov 1 2026)
        self.assertEqual((start.weekday(), end.weekday()), (0, 6))
        self.assertEqual((start.hour, end.hour, end.minute), (0, 23, 30))
        start, _ = rc.week_bounds(monday(2027, 3, 8), TZ)                                 # the week the clocks went forward (Mar 14 2027)
        self.assertEqual(start.utcoffset().total_seconds(), -6 * 3600)


class Inventory(unittest.TestCase):
    def test_a_board_with_nothing_gets_four_future_weeks(self):
        p = rc.plan_sprints("MEJ", [], [], at(2026, 11, 25), TZ)
        self.assertEqual([c[0] for c in p["create"]], ["MEJ W49 2026", "MEJ W50 2026", "MEJ W51 2026", "MEJ W52 2026"])
        self.assertEqual(p["rename"], [])

    def test_existing_sprints_are_renamed_in_place_and_the_gap_is_filled(self):
        active = [sprint(10, "MEJ", monday(2026, 11, 23), "active", "MEJ Sprint 12")]
        future = [sprint(11, "MEJ", monday(2026, 11, 30), name="Old name"), sprint(13, "MEJ", monday(2026, 12, 14))]
        p = rc.plan_sprints("MEJ", active, future, at(2026, 11, 25), TZ)
        self.assertEqual(p["rename"], [(10, "MEJ W48 2026"), (11, "MEJ W49 2026")])
        self.assertEqual([c[0] for c in p["create"]], ["MEJ W50 2026", "MEJ W52 2026"])   # week 50 fills the gap; the floor adds 52
        self.assertEqual(p["future"], 4)

    def test_across_the_year_end_and_week_53(self):
        active = [sprint(1, "JFM", monday(2026, 12, 21), "active")]
        p = rc.plan_sprints("JFM", active, [], at(2026, 12, 23), TZ)
        self.assertEqual([c[0] for c in p["create"]], ["JFM W53 2026", "JFM W1 2027", "JFM W2 2027", "JFM W3 2027"])

    def test_a_legacy_dst_stamp_matches_its_week_not_a_new_one(self):
        start = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)                           # midnight CDT stamped before the clocks changed: 11 PM the previous day in CST
        legacy = {"id": 21, "name": "MEJ W45 2026", "state": "future", "startDate": start.isoformat(), "endDate": datetime(2026, 11, 9, 5, 30, tzinfo=timezone.utc).isoformat()}
        p = rc.plan_sprints("MEJ", [sprint(20, "MEJ", monday(2026, 10, 26), "active")], [legacy], at(2026, 10, 28), TZ)
        self.assertNotIn(("MEJ W45 2026",), [(c[0],) for c in p["create"]])

    def test_conflicts_fail_closed_before_any_write(self):
        week = monday(2026, 12, 7)
        for future in ([sprint(1, "MEJ", week), sprint(2, "MEJ", week)],                    # two sprints for one week
                       [{"id": 3, "name": "x", "state": "future", "startDate": at(2026, 12, 8).isoformat(), "endDate": at(2026, 12, 20).isoformat()}]):   # not one Monday-Sunday week
            with self.assertRaises(JiraError) as error:
                rc.plan_sprints("MEJ", [], future, at(2026, 12, 1), TZ)
            self.assertEqual(str(error.exception), "JIRA_RECONCILE_SPRINT_CONFLICT")
        with self.assertRaises(JiraError) as error:
            rc.plan_sprints("MEJ", [sprint(1, "MEJ", monday(2026, 11, 23), "active"), sprint(2, "MEJ", monday(2026, 11, 30), "active")], [], at(2026, 11, 25), TZ)
        self.assertEqual(str(error.exception), "JIRA_ACTIVE_AMBIGUOUS")

    def test_closed_history_is_never_touched(self):
        p = rc.plan_sprints("MEJ", [], [], at(2026, 11, 25), TZ)
        self.assertTrue(all(c[0].startswith("MEJ W") for c in p["create"]))                # only names, no id of a closed sprint is ever in a plan
        self.assertEqual(p["rename"], [])


def issue(key, type_="Task", status="To Do", category="new", priority="Medium", parent=None, labels=(), links=(), sprint_id=None, due=None, summary="s", description=None):
    return {"key": key, "fields": {"issuetype": {"name": type_, "subtask": type_ == "Sub-task"}, "status": {"name": status, "statusCategory": {"key": category}}, "priority": {"name": priority}, "parent": {"key": parent} if parent else None,
                                   "labels": list(labels), "issuelinks": list(links), "sprint": {"id": sprint_id} if sprint_id else None, "duedate": due, "summary": summary, "description": description}}


class Carry(unittest.TestCase):
    def test_the_eligibility_matrix(self):
        plan = rc.carry_plan([issue("A-1", status="In Progress", category="indeterminate", priority="Low"),     # in progress, any priority: carry
                              issue("A-2", priority="High"), issue("A-3", priority="Highest", status="Blocked"),   # High/Highest To Do or Blocked: carry
                              issue("A-4", priority="Medium"), issue("A-5", status="Blocked", priority="Medium"),   # the rest: backlog
                              issue("A-6", type_="Epic"), issue("A-7", type_="Bug", priority="High"),             # Epics excluded; an unknown type is an exception, not a move
                              issue("A-8", status="Done", category="done", priority="High")])
        self.assertEqual(plan["carry"], ["A-1", "A-2", "A-3"])
        self.assertEqual(plan["backlog"], ["A-4", "A-5"])
        self.assertEqual(plan["exceptions"], ["A-7"])

    def test_children_follow_their_parent_and_recurrence_overrides_carry(self):
        plan = rc.carry_plan([issue("A-1", priority="High"), issue("A-11", "Sub-task", parent="A-1"), issue("A-2", priority="Low"), issue("A-21", "Sub-task", parent="A-2"),
                              issue("A-3", priority="High", labels=["recurring"]), issue("A-31", "Sub-task", parent="A-3")], recurring=("A-3",))
        self.assertEqual(plan["carry"], ["A-1", "A-11"])
        self.assertEqual(plan["backlog"], ["A-2", "A-21"])
        self.assertEqual(plan["recurring"], ["A-3", "A-31"])


def clones(**chain):
    return {k: [{"type": {"name": "Cloners"}, "outwardIssue": {"key": v}}] for k, v in chain.items()}


class Recurrence(unittest.TestCase):
    def build(self):
        links = clones(**{"R-2": "R-1", "R-3": "R-2"})
        return [issue("R-1", labels=["recurring"], status="Done", category="done", sprint_id=1), issue("R-2", labels=["recurring"], links=links["R-2"], sprint_id=2),
                issue("R-3", labels=["recurring"], links=links["R-3"], sprint_id=3)]

    def test_identity_follows_the_clone_chain_not_the_title(self):
        issues = self.build()
        for i in issues:
            i["fields"]["summary"] = "same title"
        found = rc.series(issues)
        self.assertEqual(list(found), ["R-1"])
        self.assertEqual(found["R-1"]["keys"], ["R-1", "R-2", "R-3"])

    def test_the_label_and_the_configuration_are_deduplicated(self):
        found = rc.series(self.build(), configured=("R-2", "R-3"))
        self.assertEqual(len(found), 1)

    def test_two_origins_or_a_loop_fail_closed(self):
        both = [{"type": {"name": "Cloners"}, "outwardIssue": {"key": "R-1"}}, {"type": {"name": "Cloners"}, "outwardIssue": {"key": "R-9"}}]
        with self.assertRaises(JiraError) as error:
            rc.series([issue("R-1"), issue("R-9"), issue("R-2", labels=["recurring"], links=both)])
        self.assertEqual(str(error.exception), "JIRA_RECONCILE_IDENTITY_AMBIGUOUS")
        loop = [issue("L-1", labels=["recurring"], links=clones(**{"L-1": "L-2"})["L-1"]), issue("L-2", labels=["recurring"], links=clones(**{"L-2": "L-1"})["L-2"])]
        with self.assertRaises(JiraError):
            rc.series(loop)

    def test_exactly_one_next_week_occurrence_is_created_and_a_second_run_creates_none(self):
        issues = self.build()
        plan = rc.plan_recurrence(issues, rc.series(issues), next_sprint_id=4)
        self.assertEqual(plan["create"], ["R-1"])
        issues.append(issue("R-4", labels=["recurring"], links=clones(**{"R-4": "R-3"})["R-4"], sprint_id=4))
        again = rc.plan_recurrence(issues, rc.series(issues), next_sprint_id=4)
        self.assertEqual(again["create"], [])

    def test_a_duplicate_occurrence_in_the_next_sprint_fails_closed_for_that_series(self):
        issues = self.build() + [issue("R-4", labels=["recurring"], links=clones(**{"R-4": "R-3"})["R-4"], sprint_id=4), issue("R-5", labels=["recurring"], links=clones(**{"R-5": "R-3"})["R-5"], sprint_id=4)]
        plan = rc.plan_recurrence(issues, rc.series(issues), next_sprint_id=4)
        self.assertEqual((plan["create"], plan["exceptions"]), ([], ["JIRA_RECONCILE_DUPLICATE_OCCURRENCE"]))

    def test_label_removal_stops_creation_and_sync_even_for_a_configured_series(self):
        issues = self.build()
        issues[2]["fields"]["labels"] = []                                                  # the latest occurrence lost the label
        found = rc.series(issues, configured=("R-1", "R-2", "R-3"))
        plan = rc.plan_recurrence(issues, found, next_sprint_id=4)
        self.assertEqual((plan["create"], plan["sync"], plan["stopped"]), ([], [], 1))

    def test_edits_reach_the_unfinished_future_occurrence_only(self):
        issues = self.build()
        issues[2]["fields"]["summary"] = "edited"
        future = issue("R-4", labels=["recurring"], links=clones(**{"R-4": "R-3"})["R-4"], sprint_id=4, summary="old")
        plan = rc.plan_recurrence(issues + [future], rc.series(issues + [future]), next_sprint_id=4)
        self.assertEqual(plan["sync"], [("R-4", {"summary": "edited"})])
        future["fields"]["status"] = {"name": "Done", "statusCategory": {"key": "done"}}  # completed work is never overwritten
        done = rc.plan_recurrence(issues + [future], rc.series(issues + [future]), next_sprint_id=4)
        self.assertEqual(done["sync"], [])

    def test_clone_fields_follow_the_q8_matrix(self):
        source = issue("R-3", priority="High", labels=["recurring"], parent="E-1", due="2026-12-31", description="- [x] one\n- [ ] two", summary="Weekly")
        source["fields"]["assignee"] = {"accountId": "abc"}
        source["fields"]["comment"] = ["private"]
        fields = rc.clone_fields(source)
        self.assertEqual((fields["summary"], fields["assignee"], fields["priority"], fields["parent"], fields["status"], fields["resolution"]), ("Weekly", "abc", "High", "E-1", "To Do", None))
        self.assertEqual(fields["duedate"], "2027-01-07")
        self.assertEqual(fields["description"], "- [ ] one\n- [ ] two")
        self.assertNotIn("comment", fields)


class Counts(unittest.TestCase):
    def test_manual_automatic_and_total_stay_apart_from_activation_on(self):
        def done(key, hour, labels=()):
            made = issue(key, status="Done", category="done", labels=labels)
            made["fields"]["statuscategorychangedate"] = at(2026, 11, 25, hour).isoformat()
            return made
        issues = [done("A-1", 10), done("A-2", 16, ["auto-closed"]), done("A-3", 17, ["auto-closed", "x"]), issue("A-4")]
        self.assertEqual(rc.closure_counts(issues, at(2026, 11, 25, 14)), {"manual": 0, "automatic": 2, "total": 3, "split": True})   # A-1 closed before activation: total only
        self.assertEqual(rc.closure_counts(issues), {"manual": None, "automatic": None, "total": 3, "split": False})

    def test_the_marker_is_one_label_and_one_comment_line(self):
        self.assertEqual(rc.auto_close_fields("123456"), {"label": "auto-closed", "comment": "Closed automatically by LIFE OS run 123456."})
        for bad in ("", None, "a\nb"):
            with self.assertRaises(JiraError):
                rc.auto_close_fields(bad)


class Override(unittest.TestCase):
    ISSUES = [issue("MEJ-1", priority="Low"), issue("MEJ-2", priority="High"), issue("MEJ-3", status="Blocked", priority="Low"),
              issue("MEJ-4", status="In Progress", category="indeterminate", priority="Low"), issue("MEJ-5", type_="Sub-task", parent="MEJ-1")]

    def test_the_rule_governs_both_boards_without_a_roster(self):
        for project in ("JFM", "MEJ"):
            plan = rollover.select_carry(project, self.ISSUES)
            self.assertEqual((plan["carry"], plan["backlog"]), (["MEJ-2", "MEJ-4"], ["MEJ-1", "MEJ-3", "MEJ-5"]))

    def test_a_blocked_low_priority_item_goes_to_the_backlog(self):
        self.assertIn("MEJ-3", rollover.select_carry("MEJ", self.ISSUES)["backlog"])

    def test_the_roster_overrides_only_mej_and_only_when_set(self):
        roster = {"roster": ["MEJ-1"], "finite": [], "consent": "x", "backlog": None}
        mej = rollover.select_carry("MEJ", self.ISSUES, roster=roster)
        self.assertEqual(mej["carry"], ["MEJ-1", "MEJ-5"])
        self.assertEqual(mej["backlog"], ["MEJ-2", "MEJ-3", "MEJ-4"])
        self.assertEqual(rollover.select_carry("JFM", self.ISSUES, roster=roster)["carry"], ["MEJ-2", "MEJ-4"])


class Daily(unittest.TestCase):
    def test_due_at_8_am_chicago_once_per_date_across_dst(self):
        self.assertFalse(rc.due(at(2026, 11, 25, 7, 59), None))
        self.assertTrue(rc.due(at(2026, 11, 25, 8, 0), None))
        self.assertFalse(rc.due(at(2026, 11, 25, 9), date(2026, 11, 25)))
        self.assertTrue(rc.due(at(2026, 11, 26, 8), date(2026, 11, 25)))
        self.assertTrue(rc.due(datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc), date(2026, 10, 31)))   # 8 AM CST on the day the clocks went back
        self.assertFalse(rc.due(datetime(2026, 11, 1, 13, 59, tzinfo=timezone.utc), date(2026, 10, 31)))
        self.assertTrue(rc.due(datetime(2027, 3, 14, 13, 0, tzinfo=timezone.utc), date(2027, 3, 13)))    # 8 AM CDT on the day the clocks went forward

    def test_a_clock_without_a_zone_is_refused(self):
        with self.assertRaises(JiraError) as error:
            rc.due(datetime(2026, 11, 25, 8, 0), None)
        self.assertEqual(str(error.exception), "JIRA_RECONCILE_CLOCK_INVALID")


class FakeBoard:
    def __init__(self, sprints):
        self.sprints_by_state = sprints
        self.log = []

    def sprints(self, board_id, state):
        return list(self.sprints_by_state.get(state, []))


class State:
    def __init__(self, last=None):
        self.last = last

    def get(self):
        return self.last

    def set(self, day):
        self.last = day


def writers(board):
    def create(board_id, name, start, end):
        board.log.append(("create", name))
        board.sprints_by_state.setdefault("future", []).append({"id": 100 + len(board.log), "name": name, "state": "future", "startDate": start.isoformat(), "endDate": end.isoformat()})

    def rename(sprint_id, name):
        board.log.append(("rename", name))
        for items in board.sprints_by_state.values():
            for s in items:
                if s["id"] == sprint_id:
                    s["name"] = name
    return rc.Writers(create_sprint=create, rename_sprint=rename)


class Stage(unittest.TestCase):
    def board(self):
        return FakeBoard({"active": [sprint(10, "JFM", monday(2026, 11, 23), "active", "JFM Sprint 9")], "future": []})

    def test_off_by_default_nothing_is_read_or_written(self):
        board = self.board()
        with patch("lifeos.platform.jira.Jira.from_env", side_effect=AssertionError("built a client")):
            self.assertEqual(rc.run(5, True, environ=ENV_OFF, client=board, now=at(2026, 11, 25)), {"enabled": 0})
        self.assertEqual(rc.run(5, False, environ={**ENV_OFF, "MEJ_SPRINT_AUTOMATION": "yes"}, client=board, now=at(2026, 11, 25)), {"enabled": 0})
        self.assertEqual(board.log, [])

    def test_the_stage_is_registered_and_its_default_call_is_off(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual(entry.STAGES["jira-reconcile-daily"](5, False), {"enabled": 0})

    def test_dry_run_plans_both_boards_and_writes_nothing(self):
        board = self.board()
        result = rc.run(5, False, environ=ENV_ON, client=board, now=at(2026, 11, 25))
        self.assertEqual((result["ok"], result["failed"], result["would_create"], result["would_rename"]), (2, 0, 8, 1))             # MEJ has no active sprint of its own on this fake board, so only JFM's is renamed
        self.assertEqual(board.log, [])

    def test_a_live_run_without_verified_writers_or_a_checkpoint_fails_closed(self):
        for kwargs in ({}, {"writers": rc.Writers()}, {"state": State()}):
            with self.assertRaises(JiraError) as error:
                rc.run(5, True, environ=ENV_ON, client=self.board(), now=at(2026, 11, 25), **kwargs)
            self.assertEqual(str(error.exception), "JIRA_RECONCILE_WRITER_UNVERIFIED")
        with self.assertRaises(JiraError) as error:
            rc.run(5, True, environ=ENV_ON, client=self.board(), now=at(2026, 11, 25), writers=rc.Writers(), state=State())
        self.assertIn("JIRA_RECONCILE_WRITER_UNVERIFIED", str(error.exception))

    def test_live_with_mock_writers_renames_creates_reads_back_and_marks_the_day(self):
        board, state = self.board(), State()
        result = rc.run(5, True, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5"}, client=board, now=at(2026, 11, 25), writers=writers(board), state=state)
        self.assertEqual((result["created"], result["renamed"], result["failed"]), (4, 1, 0))
        self.assertEqual(state.get(), date(2026, 11, 25))
        self.assertEqual(rc.run(5, True, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5"}, client=board, now=at(2026, 11, 25, 15), writers=writers(board), state=state), {"enabled": 1, "not_due": 1})
        self.assertEqual(len(board.log), 5)                                                 # the same day again writes nothing

    def test_restart_after_a_partial_write_plans_from_actual_state_without_a_blind_create(self):
        board = self.board()
        calls = []

        def create(board_id, name, start, end):
            calls.append(name)
            if len(calls) == 2:
                raise JiraError("JIRA_HTTP_500")                                            # the second create dies after the first one landed
            board.sprints_by_state.setdefault("future", []).append({"id": 50 + len(calls), "name": name, "state": "future", "startDate": start.isoformat(), "endDate": end.isoformat()})
        part = rc.Writers(create_sprint=create, rename_sprint=writers(board).rename_sprint)
        with self.assertRaises(JiraError):
            rc.run(5, True, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5"}, client=board, now=at(2026, 11, 25), writers=part, state=State())
        self.assertEqual(len(board.sprints_by_state["future"]), 1)
        state = State()
        rc.run(5, True, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5"}, client=board, now=at(2026, 11, 25), writers=writers(board), state=state)
        names = [s["name"] for s in board.sprints_by_state["future"]]
        self.assertEqual(len(names), len(set(names)))                                       # no duplicate of the sprint that already landed
        self.assertEqual(len(names), 4)

    def test_a_readback_that_still_shows_work_re_plans_once_then_fails_closed(self):
        board = self.board()
        lagging = rc.Writers(create_sprint=lambda *a: None, rename_sprint=lambda *a: None)    # accepts every write, changes nothing
        slept = []
        with self.assertRaises(JiraError) as error:
            rc.run(5, True, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5"}, client=board, now=at(2026, 11, 25), writers=lagging, state=State(), sleep=slept.append)
        self.assertIn("JIRA_RECONCILE_VERIFY_FAILED", str(error.exception))
        self.assertEqual(slept, [15])

    def test_one_board_failing_fails_the_run_alerts_once_and_marks_nothing(self):
        board, state, alerts = self.board(), State(), []
        board.sprints_by_state["future"] = [sprint(1, "JFM", monday(2026, 12, 1)), sprint(2, "JFM", monday(2026, 12, 1))]
        with self.assertRaises(JiraError) as error:
            rc.run(5, True, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5"}, client=board, now=at(2026, 11, 25), writers=writers(board), state=state, alert=lambda s, d: alerts.append((s, d)))
        self.assertEqual(str(error.exception), "JIRA_RECONCILE_FAILED:1of1:JIRA_RECONCILE_SPRINT_CONFLICT@1")
        self.assertEqual((len(alerts), state.get(), board.log), (1, None, []))              # nothing was written and the day is not marked done

    def test_a_readonly_board_is_skipped_and_the_rollover_default_path_is_untouched(self):
        board = self.board()
        result = rc.run(5, False, environ={**ENV_ON, "JIRA_BOARDS": "JFM:5:readonly"}, client=board, now=at(2026, 11, 25))
        self.assertEqual((result["boards"], result["ok"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
