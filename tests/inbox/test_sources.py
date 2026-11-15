"""LIFE-708 (synthetic): Jira and Calendar sources are read-only, scoped per workspace, fail alone, and never reach a live provider."""
import ast
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from lifeos.inbox import inbox, policy, sources
from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.jira import Jira
from tests.inbox import fixtures as F

BASE = "https://jira.example.test"
WRITES = ("post", "request", "insert", "update", "delete", "create_sprint", "set_sprint_state", "move_issues")


def issue(key="LIFE-1", status="Human UAT", assignee="acct-1", due=None):
    return {"key": key, "fields": {"summary": f"{F.SAME_TITLE} {F.CANARY}", "status": {"name": status}, "assignee": {"accountId": assignee}, "duedate": due, "updated": "2026-11-14T13:30:00.000-0600"}}


def event(id="e1", status="confirmed"):
    return {"id": id, "htmlLink": f"https://cal.example.test/{id}", "summary": F.SAME_TITLE, "status": status, "updated": "2026-11-14T19:30:00.000Z",
            "start": {"dateTime": "2026-11-14T16:00:00-06:00", "timeZone": "America/Chicago"}}


def opener(rows, error=None):
    def open_reader(credential_ref):
        def read(*args):
            if error:
                raise error
            return rows
        return read
    return open_reader


def jira_src(workspace, rows=None, error=None, **kw):
    return sources.JiraSource(workspace, f"jim-{workspace}", "jim", 7, BASE, open_reader=opener([issue()] if rows is None else rows, error), **kw)


def cal_src(workspace, rows=None, error=None):
    return sources.CalendarSource(workspace, f"jim-{workspace}", "jim", F.NOW, open_reader=opener([event()] if rows is None else rows, error))


def run(srcs, **kw):
    return inbox.build_inbox(srcs, F.principal(sources=("jira", "calendar"), **kw), F.NOW)


class Reads(unittest.TestCase):
    def test_both_workspaces_give_normalized_records_with_ids_links_owner_status_and_zone(self):
        result = run([jira_src("personal"), jira_src("bytalos"), cal_src("personal"), cal_src("bytalos")])
        self.assertEqual([h.state for h in result.health], ["ok"] * 4)
        ids = sorted(r.record_id for r in result.records)
        self.assertEqual(ids, ["calendar:bytalos:jim-bytalos:e1", "calendar:personal:jim-personal:e1", "jira:bytalos:jim-bytalos:LIFE-1", "jira:personal:jim-personal:LIFE-1"])
        jira = F.record_for(result, "jira", "personal")
        self.assertEqual((jira.url, jira.assignee, jira.source_status, jira.kind), (f"{BASE}/browse/LIFE-1", "acct-1", "Human UAT", "human_uat"))
        calendar = F.record_for(result, "calendar", "bytalos")
        self.assertEqual((calendar.url, calendar.assignee, calendar.due_tz, calendar.presentation), ("https://cal.example.test/e1", "jim", "America/Chicago", "upcoming"))
        self.assertEqual(result.records[0].retrieved_at, F.NOW)                                             # fresh: retrieved now

    def test_the_revision_follows_the_source_status(self):
        a = run([jira_src("personal", [issue(status="Human UAT")])]).records[0]
        b = run([jira_src("personal", [issue(status="Human UAT")])]).records[0]
        c = run([jira_src("personal", [issue(status="Done")])]).records[0]
        self.assertEqual(a.revision, b.revision)
        self.assertNotEqual(a.revision, c.revision)

    def test_an_all_day_event_and_a_date_only_due_get_a_zone(self):
        day = {**event(), "start": {"date": "2026-11-15"}}
        self.assertEqual(run([cal_src("personal", [day])]).records[0].due_tz, "America/Chicago")
        self.assertEqual(run([jira_src("personal", [issue(due="2026-11-20")])]).records[0].due_tz, "America/Chicago")

    def test_tgp_is_refused_before_any_read(self):
        tgp = jira_src("tgp")
        calls = []
        tgp._open = lambda ref: calls.append(ref)
        result = run([tgp, jira_src("personal")])
        self.assertEqual(calls, [])
        self.assertIn(("tgp", "refused", "workspace_not_released"), [(h.workspace, h.state, h.reason) for h in result.health])
        self.assertEqual([r.workspace for r in result.records], ["personal"])


class FailClosed(unittest.TestCase):
    def test_a_missing_revoked_or_expired_grant_reads_nothing_and_the_others_still_show(self):
        for name, kw in (("missing", {"workspaces": ("personal",)}), ("revoked", {"revoked": (("jira", "bytalos"),)}), ("expired", {"expires": F.NOW - timedelta(minutes=1)})):
            with self.subTest(name):
                bytalos = jira_src("bytalos")
                calls = []
                bytalos._open = lambda ref, calls=calls: calls.append(ref)
                result = run([cal_src("personal"), bytalos], **kw)
                self.assertEqual(calls, [])
                self.assertEqual(next(h.state for h in result.health if h.source == "jira"), "no_grant")
                if name == "missing":
                    self.assertEqual([r.source for r in result.records], ["calendar"])                       # the healthy source is kept

    def test_an_erroring_source_is_unavailable_by_type_only_and_the_others_survive(self):
        result = run([jira_src("personal", error=TimeoutError(F.ERROR_CANARY)), cal_src("personal")])
        health = {h.source: (h.state, h.reason) for h in result.health}
        self.assertEqual(health["jira"], ("unavailable", "timeouterror"))
        self.assertEqual([r.source for r in result.records], ["calendar"])
        self.assertNotIn(F.ERROR_CANARY, repr(result))

    def test_an_incomplete_listing_is_an_error_not_a_smaller_list(self):
        result = run([jira_src("personal", rows={"issues": []}), cal_src("personal")])
        self.assertEqual(next(h for h in result.health if h.source == "jira").reason, "inboxerror")
        self.assertEqual(len(result.records), 1)

    def test_a_malformed_row_degrades_the_source_and_keeps_the_good_rows(self):
        result = run([jira_src("personal", [issue(), {"key": "LIFE-2"}])])
        self.assertEqual([(h.state, h.rejected) for h in result.health], [("degraded", 1)])
        self.assertEqual(len(result.records), 1)


class ReadOnly(unittest.TestCase):
    def test_the_real_clients_are_only_ever_called_with_get(self):
        calls = []
        jira = Jira(BASE, "agent@example.com", "synthetic-token")
        jira.request = lambda method, path, params=None, payload=None, retry=None: (calls.append(method), {"issues": [issue()], "total": 1})[1]
        calendar = GoogleCalendar.__new__(GoogleCalendar)
        calendar.request = lambda method, path, params=None, body=None, ok=(200,): (calls.append(method), {"items": [event()]})[1]
        result = run([sources.JiraSource("personal", "jim-personal", "jim", 7, BASE, open_reader=lambda ref: jira.board_issues),
                      sources.CalendarSource("personal", "jim-personal", "jim", F.NOW, open_reader=lambda ref: calendar.list_events)])
        self.assertEqual(len(result.records), 2)
        self.assertEqual(set(calls), {"GET"})

    def test_a_source_holds_a_read_function_not_a_client(self):
        jira = jira_src("personal")
        self.assertFalse([name for name in vars(jira) if any(w in name for w in WRITES)])
        self.assertFalse(hasattr(jira, "client"))

    def test_the_module_names_no_write_or_client_construction(self):
        tree = ast.parse(Path(sources.__file__).read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertEqual(names & set(WRITES + ("Jira", "GoogleCalendar", "from_env")), set())


class NoLiveRead(unittest.TestCase):
    def test_without_an_injected_reader_nothing_is_fetched_and_no_client_is_built(self):
        with mock.patch.object(Jira, "from_env", side_effect=AssertionError), mock.patch.object(GoogleCalendar, "from_env", side_effect=AssertionError):
            result = run([sources.JiraSource("personal", "jim-personal", "jim", 7, BASE), sources.CalendarSource("personal", "jim-personal", "jim", F.NOW)])
        self.assertEqual([(h.state, h.reason) for h in result.health], [("unavailable", "inboxerror")] * 2)
        self.assertEqual(result.records, ())
        self.assertFalse(sources.LIVE_READS_ENABLED)

    def test_the_refusal_word_is_fixed(self):
        with self.assertRaises(Exception) as caught:
            sources.refuse_live("cred:x")
        self.assertEqual(str(caught.exception), "live_reads_not_authorized")
        self.assertEqual(policy.LIVE_WRITES_ENABLED, False)


if __name__ == "__main__":
    unittest.main()
