"""LIFE-88: Interview OS's normal path composes only verified evidence, fails each gap visibly and alone, and writes only on Jim's switch."""
import unittest
from unittest.mock import patch

from lifeos.interview import create, notion
from lifeos.interview.models import Child, Ownership, Parent, Scan
from lifeos.run import STAGES
from lifeos.sources import interview_evidence as ev

URL = "https://example.test/thread/1"
OTHER = "https://example.test/thread/2"
ENV = {"NOTION_INTERVIEW_TOKEN": "synthetic", "HIRING_PIPELINE_PAGE_ID": "root"}
ROW = {"page_id": "ledger-1", "company": "ExampleCo", "role": "Program Manager", "stable_job_key": "exampleco-r-1", "requisition": None}


class Calendar:
    def __init__(self, *events):
        self.events = list(events)

    def list_events(self, start, end):
        return self.events


def event(title, start="2026-10-28T10:00:00-05:00", **extra):
    return {"id": title, "summary": title, "status": "confirmed", "start": {"dateTime": start}, **extra}


def child(id="c1", date=None, who=None, ordinal=1):
    return Child(id, "p1", date, who, ordinal, True)


class Run:
    """ev.run with the Notion reads replaced by plain data; `stage.run` is captured, never executed."""

    def __init__(self, parents=(("p1", "ExampleCo — Program Manager"),), facts=None, kids=None, rows=(ROW,), calendar=None, environ=ENV, live=True):
        self.parents = [Parent(i, t) for i, t in parents]
        self.facts = facts if facts is not None else {"p1": ("exampleco-r-1", URL)}
        self.kids = kids if kids is not None else {"p1": (child(),)}
        self.rows, self.calendar, self.environ, self.live = rows, calendar if calendar is not None else Calendar(), environ, live

    def __call__(self):
        captured = {}

        def fake_stage(limit, live, environ, client, evidence, context):
            captured.update(evidence=list(evidence), live=live)
            return {"blocked": 0, "why": {}}

        with patch.object(notion, "parent_scan", return_value=Scan(tuple(self.parents), True)), \
             patch.object(notion, "ownership", return_value=Ownership.MACHINE), \
             patch.object(ev, "facts", side_effect=lambda client, page_id, context: self.facts.get(page_id)), \
             patch.object(notion, "child_scan", side_effect=lambda client, page_id, context=None: Scan(self.kids.get(page_id, ()), True)), \
             patch.object(ev.stage, "run", side_effect=fake_stage):
            counts = ev.run(20, self.live, environ=self.environ, client=object(), calendar=self.calendar, ledger_rows=lambda company: list(self.rows))
        return counts, captured


class Ordinal(unittest.TestCase):
    def test_only_an_explicit_single_round_number_counts(self):
        for title, want in (("ExampleCo Interview 2", 2), ("Round 3 - ExampleCo", 3), ("ExampleCo 2nd interview", 2), ("ExampleCo interview", None),
                            ("Interview 2 and Round 3", None), ("Interview 2026", None), ("", None), (None, None)):
            self.assertEqual(ev.ordinal(title), want, title)


class Refresh(unittest.TestCase):
    def test_an_existing_scheduling_pending_page_is_refreshed_from_its_own_facts(self):
        counts, got = Run()()
        (parent, query), = got["evidence"]
        self.assertEqual((parent.company, parent.role), ("ExampleCo", "Program Manager"))
        self.assertEqual((query.interview_date, query.ordinal, query.source, query.stable_job_key, query.ledger_page_id), (None, 1, URL, "exampleco-r-1", "ledger-1"))
        self.assertEqual(counts["failed"], 0)

    def test_a_dated_round_keeps_the_job_and_the_request_the_progression_drops(self):
        _, got = Run(kids={"p1": (child("c2", "2026-10-01", "Pat Example", 2),)})()
        (_, query), = got["evidence"]
        self.assertEqual((query.interview_date, query.interviewer, query.ordinal, query.stable_job_key, query.source), ("2026-10-01", "Pat Example", 2, "exampleco-r-1", URL))

    def test_nothing_is_written_without_jims_switch(self):
        self.assertFalse(Run()()[1]["live"])
        self.assertFalse(Run(environ={**ENV, "INTERVIEW_OS_WRITER": "on"})()[1]["live"])
        self.assertTrue(Run(environ={**ENV, "INTERVIEW_OS_WRITER": "v7"})()[1]["live"])
        self.assertFalse(Run(environ={**ENV, "INTERVIEW_OS_WRITER": "v7"}, live=False)()[1]["live"])

    def test_wrong_or_missing_evidence_fails_that_page_visibly_and_writes_nothing(self):
        for label, run in (("no request message", Run(facts={"p1": ("exampleco-r-1", None)})),
                           ("ledger has another job", Run(rows=({**ROW, "stable_job_key": "exampleco-r-9"},))),
                           ("ledger empty", Run(rows=())),
                           ("page names no job", Run(facts={"p1": (None, URL)}))):
            counts, got = run()
            self.assertEqual(got["evidence"], [], label)
            self.assertEqual(counts["failed"], 1, label)

    def test_a_ui3b_page_waiting_for_migration_is_reported_not_failed(self):
        counts, got = Run(facts={"p1": None})()
        self.assertEqual((got["evidence"], counts["failed"], counts["why"]), ([], 0, {"parent_legacy_layout": 1}))

    def test_one_gap_does_not_stop_another_valid_page(self):
        run = Run(parents=(("p1", "ExampleCo — Program Manager"), ("p2", "OtherCo — Analyst")), facts={"p1": ("exampleco-r-1", URL), "p2": ("otherco-r-1", None)},
                  kids={"p1": (child(),), "p2": (child("c9"),)}, rows=(ROW, {**ROW, "page_id": "ledger-2", "company": "OtherCo", "role": "Analyst", "stable_job_key": "otherco-r-1"}))
        counts, got = run()
        self.assertEqual([p.company for p, _ in got["evidence"]], ["ExampleCo"])
        self.assertEqual(counts["failed"], 1)


class CalendarRounds(unittest.TestCase):
    def test_an_event_that_states_its_round_adds_that_round_before_the_refresh(self):
        counts, got = Run(calendar=Calendar(event("ExampleCo Program Manager Interview 2")))()
        (_, new), (_, refresh) = got["evidence"]
        self.assertEqual((new.interview_date, new.ordinal, new.interviewer, new.stable_job_key, new.source), ("2026-10-28", 2, None, "exampleco-r-1", URL))
        self.assertEqual((refresh.interview_date, refresh.ordinal), (None, 1))
        self.assertEqual(counts["failed"], 0)

    def test_an_event_without_a_round_number_is_incomplete_and_never_numbered_from_the_pages(self):
        counts, got = Run(calendar=Calendar(event("ExampleCo Program Manager Interview")))()
        self.assertEqual(len(got["evidence"]), 1)                              # only the refresh of the existing page
        self.assertEqual((counts["why"], counts["failed"]), ({"round_identity_incomplete": 1}, 1))

    def test_the_same_round_number_on_another_date_conflicts_and_creates_nothing(self):
        counts, got = Run(kids={"p1": (child("c2", "2026-10-01", "Pat Example", 2),)}, calendar=Calendar(event("ExampleCo Program Manager Interview 2")))()
        self.assertEqual(len(got["evidence"]), 1)
        self.assertEqual((counts["why"], counts["failed"]), ({"round_conflict": 1}, 1))

    def test_an_event_for_a_round_page_that_exists_adds_nothing(self):
        counts, got = Run(kids={"p1": (child("c2", "2026-10-28", None, 2),)}, calendar=Calendar(event("ExampleCo Program Manager Interview 2")))()
        self.assertEqual((len(got["evidence"]), counts["failed"]), (1, 0))

    def test_events_that_are_not_interviews_or_not_this_job_or_not_accepted_are_ignored(self):
        declined = event("ExampleCo Program Manager Interview 3", attendees=[{"self": True, "responseStatus": "declined"}])
        cancelled = {**event("ExampleCo Program Manager Interview 4"), "status": "cancelled"}
        counts, got = Run(calendar=Calendar(event("ExampleCo Program Manager recruiter screen call"), event("Unrelated Corp Interview 2"), declined, cancelled))()
        self.assertEqual((len(got["evidence"]), counts["failed"], counts["why"]), (1, 0, {}))

    def test_an_unreadable_calendar_is_reported_and_the_refresh_still_runs(self):
        class Broken:
            def list_events(self, *_):
                raise RuntimeError("down")
        counts, got = Run(calendar=Broken())()
        self.assertEqual((len(got["evidence"]), counts["why"], counts["failed"]), (1, {"calendar_unavailable": 1}, 1))


class Facts(unittest.TestCase):
    def regions(self, request_links, renders=1):
        run = lambda text, link=None: {"plain_text": text, "text": {"content": text, **({"link": {"url": link}} if link else {})}}
        callout = {"id": "k", "type": "callout", "callout": {"rich_text": [run(ev.REQUEST, link) for link in request_links]}}
        lines = [{"id": "m", "type": "paragraph", "paragraph": {"rich_text": [run(notion.MARKERS["opportunity"])]}}] + [
            {"id": f"r{i}", "type": "paragraph", "paragraph": {"rich_text": [run("v7-render:1;job=exampleco-r-1;source=0123456789abcdef;page=pending")]}} for i in range(renders)]
        return [callout], lines

    def read(self, request_links, renders=1):
        with patch.object(create, "machine_region", return_value=(*self.regions(request_links, renders), [])):
            return ev.facts(object(), "p1", None)

    def test_the_page_names_its_job_and_its_one_request_message(self):
        self.assertEqual(self.read([URL]), ("exampleco-r-1", URL))

    def test_no_request_link_two_different_ones_or_two_render_lines_are_not_guessed(self):
        self.assertEqual(self.read([]), ("exampleco-r-1", None))
        self.assertEqual(self.read([URL, OTHER]), ("exampleco-r-1", None))
        self.assertEqual(self.read([URL], renders=2), None)
        self.assertEqual(self.read(["http://example.test/x"]), ("exampleco-r-1", None))


class Registered(unittest.TestCase):
    def test_the_interview_stage_is_this_path_and_the_manual_ones_are_untouched(self):
        with patch.object(ev, "run", return_value={"failed": 0}) as run:
            self.assertEqual(STAGES["interview"](7, False), {"failed": 0})
        run.assert_called_once_with(7, False)
        self.assertEqual(STAGES["interview-acceptance"].target, ("lifeos.interview.acceptance", "run"))
        self.assertEqual(STAGES["interview-probe"].target, ("lifeos.interview.stage", "probe"))


if __name__ == "__main__":
    unittest.main()
