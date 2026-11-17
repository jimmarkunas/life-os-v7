"""LIFE-711: the real Jira, Calendar and Todoist items reach the Action Inbox list: read-only, receipted before every read, each source fails alone, TGP never read, a count only when every source read OK."""
import contextlib
import dataclasses
import io
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from lifeos.inbox import followup_stage as stage
from lifeos.inbox import page_items, page_rows, page_view
from lifeos.platform.notion_client import NotionError
from lifeos.platform import followup_fetchers as F
from lifeos.platform import followup_readers as readers

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)                                # Nov 16 8:00 PM CT
EXPIRES = NOW + timedelta(days=30)
JIRA = "https://sample.atlassian.net"


def jira_native(key, status="In Progress", due=None, tz="America/Chicago"):
    url = f"{JIRA}/browse/{key}"
    return {"key": key, "url": url, "summary": f"Ticket {key}", "description": "", "assignee": "acct", "status": status, "updated": "2026-11-16T12:00:00+00:00", "tz": tz, "due": due,
            "evidence": [url] if status == "Human UAT" else []}


def cal_native(event_id, start="2026-11-17T15:00:00+00:00"):
    return {"id": event_id, "url": f"https://calendar.google.com/event?eid={event_id}", "summary": f"Event {event_id}", "description": "", "status": "confirmed", "updated": "2026-11-16T12:00:00+00:00",
            "tz": "America/Chicago", "start": start}


def todo_native(task_id, due="2026-11-16T15:00:00+00:00"):
    return {"id": task_id, "url": f"https://app.todoist.com/app/task/{task_id}", "content": f"Task {task_id}", "description": "", "assignee": "", "status": "open", "updated": "2026-11-16T12:00:00+00:00",
            "tz": "America/Chicago", "due": due, "project_scope": "personal"}


class Wire:
    """A fixture wiring: scopes, grants, item fetchers; records every receipt and every fetch, in order."""

    def __init__(self, natives, scopes=None, grants=None, fail=(), expired=()):
        self.log = []
        scopes = scopes or (stage.Scope("personal", "jira-personal", ("jira",)), stage.Scope("personal", "personal calendar", ("calendar",)), stage.Scope("personal", "personal todoist", ("todoist",)),
                            stage.Scope("bytalos", "jira-bytalos", ("jira",)), stage.Scope("bytalos", "bytalos calendar", ("calendar",)))
        grants = grants if grants is not None else tuple(readers.Grant(src, ws, acct, f"{src.upper()}_{ws}", NOW - timedelta(days=1) if (src, ws) in expired else EXPIRES)
                                                         for ws, acct, src in (("personal", "jira-personal", "jira"), ("personal", "personal calendar", "calendar"), ("personal", "personal todoist", "todoist"),
                                                                              ("bytalos", "jira-bytalos", "jira"), ("bytalos", "bytalos calendar", "calendar")))

        def fetcher(source):
            def fetch(ref):
                self.log.append(("fetch", source, ref))
                workspace = ref.rsplit("_", 1)[1]
                if (source, workspace) in fail:
                    raise RuntimeError("boom with a secret-looking token=abcdef123456")
                return list(natives.get((source, workspace), ()))
            return fetch

        self.wiring = stage.Wiring(store=None, scopes=scopes, grants=grants, fetchers={}, audit=lambda r: self.log.append(("receipt", r["source"], r["tenant"], r["outcome"], r["reason"])), sink=None,
                                   items={s: fetcher(s) for s in ("jira", "calendar", "todoist")})


def full():
    return {("jira", "personal"): [jira_native("LIFE-1"), jira_native("LIFE-2", "Human UAT", due="2026-11-20"), jira_native("LIFE-3", "blocked")], ("calendar", "personal"): [cal_native("e1")],
            ("todoist", "personal"): [todo_native("t1"), todo_native("t2", due="2026-11-18T15:00:00+00:00")], ("jira", "bytalos"): [jira_native("BAD-1")], ("calendar", "bytalos"): []}


def payload(workspace, state="PASS"):
    return {"component": "followup", "state": state, "workspace": workspace, "account": f"{workspace} outlook", "generated_at": NOW.isoformat(), "last_accepted": None, "counts": {}, "threads": [], "review": [],
            "sources": [{"source": s, "status": "ok", "reason": "ok", "last_accepted": None} for s in ("jira", "calendar", "gmail", "todoist", "notion", "interview_os")], "provenance": {}}


class CollectTests(unittest.TestCase):
    def test_every_read_is_receipted_before_it_reads_and_the_records_are_the_governed_ones(self):
        wire = Wire(full())
        items = page_items.collect(wire.wiring, NOW)
        order = [entry[0] for entry in wire.log]
        self.assertEqual(order[0], "receipt")
        for n, entry in enumerate(wire.log):
            if entry[0] == "fetch":
                self.assertEqual(wire.log[n - 1][:2], ("receipt", entry[1]))                  # the allow receipt sits directly before its own read
                self.assertEqual(wire.log[n - 1][3], "allow")
        self.assertEqual(len(items["records"]), 3 + 1 + 2 + 1)
        self.assertEqual({r.workspace for r in items["records"]}, {"personal", "bytalos"})
        self.assertEqual({e["status"] for e in items["sources"]}, {"ok"})
        by_id = {r.native_id: r for r in items["records"]}
        self.assertEqual(by_id["LIFE-2"].presentation, "approvals")                          # Human UAT is an approval; the adapter decides, not this module
        self.assertEqual(by_id["LIFE-3"].presentation, "waiting")
        self.assertEqual(by_id["t2"].presentation, "upcoming")

    def test_a_failed_source_is_degraded_for_that_workspace_only_and_the_others_still_read(self):
        wire = Wire(full(), fail={("jira", "personal")})
        items = page_items.collect(wire.wiring, NOW)
        state = {(e["workspace"], e["source"]): (e["status"], e["reason"]) for e in items["sources"]}
        self.assertEqual(state[("personal", "jira")], ("degraded", "fetch_failed"))
        self.assertEqual(state[("bytalos", "jira")], ("ok", "ok"))
        self.assertEqual(state[("personal", "todoist")], ("ok", "ok"))
        self.assertNotIn("Ticket LIFE-1", [r.title for r in items["records"]])
        self.assertNotIn("abcdef123456", repr(items) + repr(wire.log))                      # a connector's message never leaves

    def test_an_expired_or_missing_grant_is_refused_with_a_receipt_and_no_read(self):
        wire = Wire(full(), expired={("calendar", "personal")})
        items = page_items.collect(wire.wiring, NOW)
        self.assertNotIn(("fetch", "calendar", "CALENDAR_personal"), wire.log)
        self.assertIn(("receipt", "calendar", "personal", "deny", "grant_expired"), wire.log)
        self.assertEqual({(e["workspace"], e["source"]): e["status"] for e in items["sources"]}[("personal", "calendar")], "degraded")
        none = Wire(full(), grants=())
        page_items.collect(none.wiring, NOW)
        self.assertFalse([e for e in none.log if e[0] == "fetch"])

    def test_tgp_and_unknown_workspaces_and_todoist_for_bytalos_are_never_read(self):
        scopes = (stage.Scope("tgp", "tgp-jira", ("jira",)), stage.Scope("personal", "tgp" + "international.com", ("jira",)), stage.Scope("bytalos", "bytalos todoist", ("todoist",)),
                  stage.Scope("nowhere", "x", ("jira",)))
        wire = Wire(full(), scopes=scopes)
        items = page_items.collect(wire.wiring, NOW)
        self.assertEqual((items["sources"], items["records"], wire.log), ([], [], []))

    def test_a_bad_row_degrades_the_source_but_the_good_rows_stay(self):
        natives = {("jira", "personal"): [jira_native("LIFE-1"), {"key": "LIFE-9"}]}
        items = page_items.collect(Wire(natives, scopes=(stage.Scope("personal", "jira-personal", ("jira",)),)).wiring, NOW)
        self.assertEqual((items["sources"][0]["status"], items["sources"][0]["reason"]), ("degraded", "records_rejected"))
        self.assertEqual([r.native_id for r in items["records"]], ["LIFE-1"])

    def test_no_item_readers_means_none_and_the_log_lines_are_fixed_words_and_counts(self):
        wire = Wire(full())
        self.assertIsNone(page_items.collect(stage.Wiring(store=None, scopes=(), grants=(), fetchers={}, audit=lambda r: None, sink=None), NOW))
        lines = page_items.lines(page_items.collect(wire.wiring, NOW))
        self.assertIn("inbox-items personal jira ok ok n=3", lines)
        self.assertTrue(all(len(line.split()) == 6 for line in lines))
        self.assertFalse([line for line in lines if "Ticket" in line or "http" in line])


class RowTests(unittest.TestCase):
    def test_rows_sort_by_bucket_then_earliest_due_and_show_central_time(self):
        items = page_items.collect(Wire(full()).wiring, NOW)
        rows = sorted((page_items.row_of(r) for r in items["records"]), key=lambda r: r["Rank"])
        self.assertEqual([r["Bucket"] for r in rows], ["Needs me", "Needs me", "Needs me", "Approvals", "Waiting", "Upcoming", "Upcoming"])
        needs = [r for r in rows if r["Bucket"] == "Needs me"]
        self.assertEqual([r["Outcome"] for r in needs][0], "Task t1")                         # due earlier than the Jira rows with no due
        self.assertEqual(rows[-2]["Outcome"], "Event e1")
        self.assertEqual(page_items.due_text(datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)), "Nov 20 · 9:00 AM CT")
        self.assertEqual(page_items.due_text(datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)), "Nov 20")      # a date with no time of day
        self.assertEqual(page_items.due_text(None), "")
        self.assertTrue(all(r["Link"].startswith("https://") and r["Freshness"] == "fresh" for r in rows))
        self.assertEqual({r["Owner"] for r in rows if r["Bucket"] == "Waiting"}, {"Them"})


class RenderTests(unittest.TestCase):
    def render(self, wire, payloads=None):
        items = page_items.collect(wire.wiring, NOW)
        return page_view.render(payloads or [payload("personal"), payload("bytalos")], NOW, items), items

    def test_with_every_source_read_the_tiles_are_real_counts_including_approvals(self):
        view, _ = self.render(Wire(full()))
        self.assertEqual(view["tiles"], ["3", "1", "1", "2"])
        self.assertEqual(len(view["rows"]), 7)
        self.assertEqual(view["healthy"], "4 of 6")
        by = {(r["Workspace"], r["Source"]): r for r in view["health"]}
        self.assertEqual((by[("Personal", "Jira")]["Health"], by[("Personal", "Jira")]["Items"]), ("🟢 ok", "3"))
        self.assertEqual(by[("Bytalos", "Calendar")]["Items"], "0")                         # a clean read that found nothing is a grounded zero
        self.assertEqual(sorted(view["clean"]), ["bytalos", "personal"])

    def test_one_failed_item_read_turns_the_tiles_to_dashes_and_keeps_its_rows_from_being_archived(self):
        view, _ = self.render(Wire(full(), fail={("jira", "bytalos")}))
        self.assertEqual(view["tiles"], ["—", "—", "—", "—"])                              # never a zero for what could not be read
        by = {(r["Workspace"], r["Source"]): r for r in view["health"]}
        self.assertEqual((by[("Bytalos", "Jira")]["Health"], by[("Bytalos", "Jira")]["Items"]), ("🟡 degraded", "—"))
        self.assertNotIn("bytalos", view["clean"])                                          # a workspace whose item read failed archives nothing
        self.assertIn("personal", view["clean"])
        self.assertEqual(view["state"][0], "Cannot say")

    def test_without_items_the_page_is_what_it_was(self):
        view = page_view.render([payload("personal"), payload("bytalos")], NOW)
        self.assertEqual(view["tiles"], ["0", "0", "—", "0"])
        self.assertEqual(view["rows"], [])

    def test_everything_read_and_nothing_to_do_is_the_truly_empty_state(self):
        view, _ = self.render(Wire({k: [] for k in full()}))
        self.assertEqual((view["state"][0], view["tiles"]), ("Truly empty", ["0", "0", "0", "0"]))


class FetcherTests(unittest.TestCase):
    ENV = {"JIRA_BASE_URL": JIRA, "JIRA_EMAIL": "jim@example.com", F.CREDENTIAL_NAMES[("jira", "personal", 1)]: "t" * 20, F.CREDENTIAL_NAMES[("todoist", "personal", 1)]: "d" * 20,
           F.CREDENTIAL_NAMES[("calendar", "personal", 1)]: '{"client_email": "sa@example.com"}', "FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID": "cal@example.com"}

    def run_fetch(self, source, client, tenant="personal"):
        fetchers = F.build_item_fetchers(self.ENV, factories={source: lambda *args: client}, clock=lambda: NOW)
        return fetchers[source](F.GRANT_REFS[(source, tenant, 1)])

    def test_jira_reads_status_due_and_assignee_for_its_own_projects_only(self):
        class Jira:
            calls = []

            def get(self, path, params=None):
                Jira.calls.append((path, params))
                return {"issues": [{"key": "LIFE-5", "fields": {"summary": "S", "status": {"name": "Human UAT"}, "assignee": {"accountId": "a1"}, "duedate": "2026-11-20", "updated": "2026-11-16T12:00:00.000+0000"}},
                                   {"key": "BAD-1", "fields": {"summary": "other workspace", "status": {"name": "To Do"}, "updated": "2026-11-16T12:00:00.000+0000"}}], "isLast": True}

        rows = self.run_fetch("jira", Jira())
        self.assertEqual([r["key"] for r in rows], ["LIFE-5"])                              # BAD-1 belongs to the Bytalos workspace: never returned for Personal
        self.assertEqual((rows[0]["status"], rows[0]["due"], rows[0]["evidence"]), ("Human UAT", "2026-11-20", [f"{JIRA}/browse/LIFE-5"]))
        self.assertTrue(Jira.calls[0][0].startswith("/rest/api/3/search/jql"))
        self.assertIn("project in (JFM, LIFE, LOS)", Jira.calls[0][1]["jql"])

    def test_calendar_reads_the_next_fourteen_days_and_todoist_keeps_only_tasks_due_soon(self):
        class Cal:
            def list_events(self, lo, hi):
                self.window = (lo, hi)
                return [{"id": "e1", "htmlLink": "https://calendar.google.com/e1", "summary": "Call", "status": "confirmed", "updated": "2026-11-16T12:00:00.000Z", "start": {"dateTime": "2026-11-17T15:00:00-06:00"}}]

        cal = Cal()
        rows = self.run_fetch("calendar", cal)
        self.assertEqual((rows[0]["start"], datetime.fromisoformat(cal.window[1]) - datetime.fromisoformat(cal.window[0])), ("2026-11-17T15:00:00-06:00", timedelta(days=14)))

        class Todo:
            def tasks(self, limit):
                return [{"id": "a", "content": "due soon", "due": {"date": "2026-11-18"}}, {"id": "b", "content": "no due"}, {"id": "c", "content": "far", "due": {"date": "2027-03-01"}},
                        {"id": "d", "content": "overdue", "due": {"datetime": "2026-11-10T15:00:00Z"}}]

        self.assertEqual([r["id"] for r in self.run_fetch("todoist", Todo())], ["a", "d"])

    def test_more_than_the_cap_is_truncated_never_cut_and_the_wrong_credential_is_refused(self):
        class Many:
            def tasks(self, limit):
                return [{"id": str(n), "content": "x", "due": {"date": "2026-11-17"}} for n in range(F.MAX_ROWS + 1)]

        with self.assertRaises(F.FetchError) as caught:
            self.run_fetch("todoist", Many())
        self.assertEqual(str(caught.exception), "FOLLOWUP_TRUNCATED")
        fetchers = F.build_item_fetchers(self.ENV, clock=lambda: NOW)
        with self.assertRaises(F.FetchError) as wrong:
            fetchers["jira"](F.GRANT_REFS[("todoist", "personal", 1)])
        self.assertEqual(str(wrong.exception), "FOLLOWUP_CREDENTIAL_NOT_ALLOWED")

    def test_the_calendar_reader_hands_back_record_safe_ids(self):
        class Cal:
            def list_events(self, lo, hi):
                return [{"id": "a=b+c/d", "htmlLink": "https://calendar.google.com/x", "summary": "Odd id", "status": "confirmed", "updated": "2026-11-16T12:00:00.000Z", "start": {"date": "2026-11-18"}}]

        row = self.run_fetch("calendar", Cal())[0]
        self.assertRegex(row["id"], r"^[A-Za-z0-9_.@:-]{1,80}$")
        record = page_items.adapters.normalize("calendar", "personal", "personal-calendar", row, "x", NOW, NOW)
        self.assertEqual(record.native_id, row["id"])

    def test_the_item_readers_can_only_read(self):
        import inspect
        source = inspect.getsource(F)
        block = source[source.index("def _items_jira"):source.index("READ_ITEMS")]
        for word in ("post(", ".put(", ".patch(", ".delete(", "create_", "update_", "insert", "transition"):
            self.assertNotIn(word, block)


class StageTests(unittest.TestCase):
    def run_stage(self, switch, items=True):
        wire = Wire(full())
        sunk, seen = [], []
        fake = (payload("personal"), {"written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0})
        wiring = dataclasses.replace(wire.wiring, production=True, env={"ACTION_INBOX_VIEW": switch}, sink=sunk.append, items=wire.wiring.items if items else None)
        with mock.patch.object(stage, "reconcile", lambda *a, **k: fake), mock.patch.object(stage, "publish_page", lambda *a, **k: seen.append(k.get("items"))):
            stage.run(0, True, wiring=wiring)
        return wire, seen

    def test_the_stage_reads_the_items_only_when_the_page_switch_is_on_and_hands_them_to_the_writer(self):
        wire, seen = self.run_stage("on")
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]["records"]), 7)
        self.assertTrue([e for e in wire.log if e[0] == "fetch"])
        off_wire, off_seen = self.run_stage("off")
        self.assertEqual(off_seen, [None])
        self.assertFalse([e for e in off_wire.log if e[0] == "fetch"])                       # switch off: no item is read at all
        _, none = self.run_stage("on", items=False)
        self.assertEqual(none, [None])                                                       # a wiring with no item readers still publishes the Followup rows

    def test_an_item_read_that_blows_up_never_fails_the_stage(self):
        wire = Wire(full())
        wiring = dataclasses.replace(wire.wiring, production=True, env={"ACTION_INBOX_VIEW": "on"}, sink=lambda p: None, audit=lambda r: (_ for _ in ()).throw(RuntimeError("receipt store down")))
        fake = (payload("personal"), {"written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0})
        seen = []
        with mock.patch.object(stage, "reconcile", lambda *a, **k: fake), mock.patch.object(stage, "publish_page", lambda *a, **k: seen.append(k.get("items"))):
            stage.run(0, True, wiring=wiring)
        self.assertEqual(seen, [None])
        self.assertFalse([e for e in wire.log if e[0] == "fetch"])                           # no receipt, no read


class RowWriteTests(unittest.TestCase):
    """A row Notion refuses must not take the others down, and its log line is a shape, never content."""

    class Client:
        def __init__(self, refuse):
            self.refuse, self.calls = refuse, []

        def call_once(self, method, path, body=None):
            title = "".join(p["text"]["content"] for p in body["properties"]["Outcome"]["title"]) if method == "POST" else ""
            self.calls.append(title)
            if title in self.refuse:
                raise NotionError("NOTION_HTTP_400")
            return {"id": "x"}

    def rows(self, *titles):
        return [{"id": f"r{n}", "Outcome": t, "Workspace": "Personal", "Source": "Calendar", "Bucket": "Upcoming", "Rank": 4.5, "Owner": "You", "Next move": "Review the agenda", "Due": "Nov 20", "Freshness": "fresh",
                 "Link": "https://calendar.google.com/secret-looking-path"} for n, t in enumerate(titles)]

    def test_one_refused_row_is_counted_and_logged_by_shape_and_the_rest_are_written(self):
        client = self.Client({"bad"})
        ops = page_rows.plan([], self.rows("good one", "bad", "good two"), set())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            done = page_rows.apply(client, "src", ops)
        self.assertEqual((done["created"], done["failed"]), (2, 1))
        log = out.getvalue()
        self.assertIn("inbox-view-row-failed op=create code=notion_http_400 source=Calendar bucket=Upcoming", log)
        self.assertIn("outcome=3", log)                                                       # lengths, so an empty or oversize field shows
        for secret in ("bad", "calendar.google.com", "Review the agenda"):
            self.assertNotIn(secret, log.replace("notion_http_400", ""))

    def test_three_refusals_in_a_row_stop_the_run(self):
        client = self.Client({"a", "b", "c", "d"})
        ops = page_rows.plan([], self.rows("a", "b", "c", "d"), set())
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(NotionError):
            page_rows.apply(client, "src", ops)
        self.assertEqual(len(client.calls), 3)

    def test_an_empty_title_is_written_as_a_placeholder_not_refused(self):
        self.assertEqual(page_rows.props({"id": "x", "Outcome": "  ", "Workspace": "Personal", "Source": "Jira", "Bucket": "Needs me", "Rank": 1, "Owner": "You", "Next move": "", "Due": "", "Freshness": "fresh",
                                           "Link": ""})["Outcome"]["title"][0]["text"]["content"], "(no title)")


class IdTests(unittest.TestCase):
    def test_a_calendar_event_id_the_record_id_cannot_hold_is_made_safe_stable_and_distinct(self):
        self.assertEqual(F._safe_id("abc_20261117T150000Z"), "abc_20261117T150000Z")
        odd, other = F._safe_id("a=b+c/d"), F._safe_id("a=b+c/e")
        self.assertRegex(odd, r"^[A-Za-z0-9_.@:-]{1,80}$")
        self.assertEqual(odd, F._safe_id("a=b+c/d"))
        self.assertNotEqual(odd, other)
        self.assertRegex(F._safe_id("x" * 300), r"^[A-Za-z0-9_.@:-]{1,80}$")
        self.assertRegex(F._safe_id(None), r"^[A-Za-z0-9_.@:-]{1,80}$")

    def test_the_reasons_a_row_was_rejected_are_counted_in_fixed_words(self):
        bad = cal_native("e1")
        bad["url"] = "http://not-https.example.com/x"
        natives = {("calendar", "personal"): [cal_native("ok"), bad, {"id": "z"}]}
        wire = Wire(natives, scopes=(stage.Scope("personal", "personal calendar", ("calendar",)),))
        line = page_items.lines(page_items.collect(wire.wiring, NOW))[0]
        self.assertEqual(line, "inbox-items personal calendar degraded records_rejected n=1 record_incomplete=1 source_url_invalid=1")


if __name__ == "__main__":
    unittest.main()
