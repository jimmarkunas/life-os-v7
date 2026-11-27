"""HIRE-1.2: Job Ledger Applied + Hiring Pipeline pages + Calendar -> deterministic compiler -> one private snapshot row
-> the floating Daily Report.
"""
import json
import pathlib
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from lifeos.hiring import compile as C, models as M, render, snapshot
from lifeos.jobs import hiring_pipeline, ledger
from lifeos.platform import report_region as R, router
from lifeos.sources.pipeline import hiring as stage
from tests.kit.tree import Tree, rich
from tests.hiring.support import FakeStore, writes

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 6, 14, 0, tzinfo=CT)
OK = {"ledger": True, "parents": True, "calendar": True}
ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV = {"HIRING_CARD_BLOCK_ID": "0000000000000000000000000000a001", "HIRING_TABLE_BLOCK_ID": "0000000000000000000000000000a003", "JIRA_CARD_BLOCK_ID": "0000000000000000000000000000a004", "NOTION_JIRA_TOKEN": "t"}


def at(days=0, hour=10):
    return (NOW.replace(hour=hour, minute=0) + timedelta(days=days)).isoformat()


def applied(company, role, on="2026-10-02"):
    return {"company": company, "role": role, "applied_on": on}


def parent(page_id, company, role, rounds=0):
    return {"id": page_id, "company": company, "role": role, "rounds": rounds}


def compile_(applied_rows=(), parents=(), events=(), prior=None, ok=OK, now=NOW):
    return C.compile_rows(list(applied_rows), list(parents), list(events), prior, ok, now)


def cells(row, now=NOW):
    return [t for t, _ in render.row_cells(row, now)]


class CompilerTests(unittest.TestCase):
    def test_an_applied_role_is_a_deterministic_submitted_candidate(self):
        got = compile_([applied("Acme Inc", "Senior Program Manager")])
        self.assertEqual([(r["stage"], r["company"]) for r in got["rows"]], [("Submitted", "Acme Inc")])
        self.assertEqual(got["reasons"], [])
        self.assertEqual(cells(got["rows"][0])[1:3], ["Submitted", "Awaiting response · follow up Oct 9"])

    def test_two_ledger_rows_for_one_company_and_role_are_one_opportunity(self):
        got = compile_([applied("Acme", "Senior Program Manager", "2026-10-01"), applied("ACME Inc.", "senior program manager", "2026-10-03")])
        self.assertEqual(len(got["rows"]), 1)

    def test_unique_calendar_evidence_advances_only_the_opportunity_it_names(self):
        got = compile_([applied("Acme", "Senior Program Manager"), applied("Globex", "Program Director")],
                       events=[{"id": "e1", "title": "Recruiter screen - Globex", "start": at(2)}])
        stages = {r["company"]: r["stage"] for r in got["rows"]}
        self.assertEqual(stages, {"Acme": "Submitted", "Globex": "Recruiter Screen"})

    def test_a_staffing_partner_in_the_title_does_not_create_a_second_opportunity(self):
        got = compile_([applied("Acme", "Senior Program Manager")],
                       events=[{"id": "e1", "title": "Interview: Talentbridge for Acme - Senior Program Manager", "start": at(1)}])
        self.assertEqual([(r["company"], r["stage"]) for r in got["rows"]], [("Acme", "Interviewing")])

    def test_a_title_that_fits_two_roles_at_one_company_is_never_guessed(self):
        got = compile_([applied("Acme", "Senior Program Manager"), applied("Acme", "Data Engineer")],
                       events=[{"id": "e1", "title": "Interview with Acme", "start": at(1)}])
        self.assertEqual({r["stage"] for r in got["rows"]}, {"Submitted"})
        self.assertIn("CALENDAR_AMBIGUOUS", got["reasons"])
        narrowed = compile_([applied("Acme", "Senior Program Manager"), applied("Acme", "Data Engineer")],
                            events=[{"id": "e1", "title": "Interview with Acme data engineer", "start": at(1)}])
        self.assertEqual({r["role"]: r["stage"] for r in narrowed["rows"]}, {"Senior Program Manager": "Submitted", "Data Engineer": "Interviewing"})

    def test_an_unsupported_or_unmatched_event_establishes_nothing(self):
        got = compile_([applied("Acme", "Senior Program Manager")], events=[{"id": "e1", "title": "Lunch with Acme", "start": at(1)},
                                                                            {"id": "e2", "title": "Interview with Initech", "start": at(1)}])
        self.assertEqual([r["stage"] for r in got["rows"]], ["Submitted"])
        self.assertEqual((got["unmatched_events"], got["reasons"]), (1, []))

    def test_a_past_event_never_reads_today_tomorrow_or_upcoming_and_is_never_closed(self):
        row = compile_([applied("Acme", "Senior Program Manager")], events=[{"id": "e1", "title": "Interview Acme", "start": at(-3)}])["rows"][0]
        text = " ".join(cells(row))
        self.assertIn("held Oct 3", text)
        for word in ("Today", "Tomorrow", "Upcoming", "Rejected", "Closed"):
            self.assertNotIn(word, text)
        later = datetime(2026, 11, 20, 9, 0, tzinfo=CT)
        self.assertNotIn("Upcoming", " ".join(cells(row, later)))
        self.assertNotIn("Closed", " ".join(cells(row, later)))

    def test_a_scheduled_event_reads_today_tomorrow_then_upcoming(self):
        row = {"stage": "Interviewing", "company": "Acme", "role": "R", "event_kind": "interview", "event_start": at(0, 16), "rounds": 0, "parent_state": "none", "parent": None}
        self.assertIn("Today 4:00 PM CT", cells(row)[2])
        self.assertIn("Tomorrow 10:00 AM CT", cells(dict(row, event_start=at(1, 10)))[2])
        self.assertIn("Upcoming Oct 9, 10:00 AM CT", cells(dict(row, event_start=at(3, 10)))[2])
        self.assertIn("held today", cells(dict(row, event_start=at(0, 9)))[2])  # 9 AM has started by 2 PM

    def test_a_hiring_manager_interview_is_scheduled_until_it_has_happened(self):
        events = [{"id": "e1", "title": "Hiring manager interview - Acme", "start": at(2)}]
        self.assertEqual(compile_([applied("Acme", "Senior Program Manager")], events=events)["rows"][0]["stage"], M.HM_SCHEDULED)
        later = NOW + timedelta(days=3)
        self.assertEqual(C.compile_rows([applied("Acme", "Senior Program Manager")], [], events, None, OK, later)["rows"][0]["stage"], M.INTERVIEWING)

    def test_a_unique_parent_gives_one_notes_link_and_anything_else_says_needs_notes(self):
        one = compile_([applied("Acme", "Senior Program Manager")], [parent("1234abcd-0000-4000-8000-000000000001", "Acme", "Senior Program Manager")])["rows"][0]
        self.assertEqual(render.row_cells(one, NOW)[3], ("Opportunity Page ↗", "https://www.notion.so/1234abcd000040008000000000000001"))
        none = compile_([applied("Acme", "Senior Program Manager")])["rows"][0]
        self.assertEqual(render.row_cells(none, NOW)[3], ("Needs Notes", None))
        two = compile_([applied("Acme", "Senior Program Manager")], [parent("p1", "Acme", "Senior Program Manager"), parent("p2", "Acme Inc", "Senior Program Manager")])
        self.assertEqual(render.row_cells(two["rows"][0], NOW)[3], ("Needs Notes", None))
        self.assertIn("IDENTITY_AMBIGUOUS", two["reasons"])

    def test_a_notes_page_with_rounds_is_interviewing_and_one_without_support_is_not_claimed(self):
        got = compile_([], [parent("p1", "Acme", "Senior Program Manager", rounds=2), parent("p2", "Globex", "Director")])
        self.assertEqual([(r["company"], r["stage"]) for r in got["rows"]], [("Acme", "Interviewing")])
        self.assertEqual((got["unsupported"], got["reasons"]), (1, []))

    def test_a_recruiter_lead_page_is_listed_with_its_own_next_step_and_a_plain_notes_page_still_is_not(self):
        got = compile_([], [dict(parent("p1", "Acme", "Program Manager"), lead=True), parent("p2", "Globex", "Director")])
        self.assertEqual([(r["company"], r["stage"]) for r in got["rows"]], [("Acme", "Recruiter lead")])
        self.assertEqual(got["unsupported"], 1)
        self.assertEqual(render.next_step(got["rows"][0], NOW)[1], "Recruiter outreach · reply, or close it")
        self.assertEqual(render.row_cells(got["rows"][0], NOW, "https://close.example.test/?o=x")[3][-1], ("Close ↗", "https://close.example.test/?o=x"))

    def test_a_lead_jim_marked_replied_waits_on_the_recruiter_from_the_reply_day(self):
        got = compile_([], [dict(parent("p1", "Acme", "Program Manager"), lead=True, replied="2026-10-05")])
        self.assertEqual((got["rows"][0]["stage"], got["rows"][0]["replied"]), ("Recruiter lead", "2026-10-05"))
        due = (datetime(2026, 10, 5) + timedelta(days=M.AWAIT_DAYS)).strftime("%b %-d")
        self.assertEqual(render.next_step(got["rows"][0], NOW)[:2], ("🟡", f"Replied Oct 5 · awaiting the recruiter · follow up {due}"))
        later = NOW + timedelta(days=M.AWAIT_DAYS + 1)
        self.assertTrue(render.next_step(got["rows"][0], later)[1].startswith(f"Stale · no reply in {M.AWAIT_DAYS + 2} days"))

    def test_a_lead_that_becomes_an_application_or_gets_a_round_is_no_longer_a_lead(self):
        applied_lead = compile_([applied("Acme", "Program Manager")], [dict(parent("p1", "Acme", "Program Manager"), lead=True)])
        self.assertEqual(applied_lead["rows"][0]["stage"], "Submitted")
        with_round = compile_([], [dict(parent("p1", "Acme", "Program Manager", rounds=1), lead=True)])
        self.assertEqual(with_round["rows"][0]["stage"], "Interviewing")

    def test_a_notes_page_without_a_company_and_role_title_is_ignored_not_degraded(self):
        got = compile_([applied("Acme", "Senior Program Manager")], [parent("p1", "Babylist", "")])
        self.assertEqual((got["reasons"], got["untitled_pages"], [r["company"] for r in got["rows"]]), ([], 1, ["Acme"]))

    def test_prior_state_carries_only_as_degraded_and_never_rolls_a_stage_back(self):
        first = compile_([applied("Acme", "Senior Program Manager")], events=[{"id": "e1", "title": "Interview Acme", "start": at(2)}])
        prior = snapshot.build(first, None, NOW)
        down = compile_([applied("Acme", "Senior Program Manager")], prior=prior, ok={**OK, "calendar": False})
        row = down["rows"][0]
        self.assertEqual((row["stage"], row["carried"]), ("Interviewing", True))
        self.assertIn("CALENDAR_UNAVAILABLE", down["reasons"])
        text, table, counts = render.render(down, prior, NOW + timedelta(hours=1))
        self.assertTrue(text.startswith("DEGRADED · Hiring Pipeline Calendar unreadable"))
        self.assertIn("showing last accepted state (10/6 · 2:00 PM CT)", text)
        self.assertIn("(last accepted)", table[0][1][0])
        vanished = compile_([applied("Acme", "Senior Program Manager")], prior=prior)  # Calendar read fine, the future event is gone
        self.assertEqual(vanished["rows"][0]["stage"], "Interviewing")
        self.assertIn("EVENT_NOT_ON_CALENDAR", vanished["reasons"])

    def test_an_unreadable_ledger_or_pages_carries_the_whole_prior_state_or_is_unavailable(self):
        prior = snapshot.build(compile_([applied("Acme", "Senior Program Manager")]), None, NOW)
        for down in ({"ledger": False}, {"parents": False}):
            got = compile_([], prior=prior, ok={**OK, **down})
            self.assertEqual([r["company"] for r in got["rows"]], ["Acme"])
            self.assertTrue(got["reasons"])
        none = compile_([], prior=None, ok={**OK, "ledger": False})
        self.assertIsNone(none["rows"])
        text, table, counts = render.render(none, None, NOW)
        self.assertTrue(text.startswith("DEGRADED · Hiring Pipeline unavailable"))
        self.assertIsNone(table)
        self.assertEqual(counts["status"], "unavailable")
        for false_empty in ("none", " 0 ", "No submitted"):
            self.assertNotIn(false_empty, text)

    def test_nothing_computed_with_incomplete_evidence_is_never_a_trustworthy_empty_table(self):
        got = compile_([], ok={**OK, "calendar": False})
        self.assertIsNone(got["rows"])
        complete = compile_([])
        self.assertEqual(complete["rows"], [])
        text, table, _ = render.render(complete, None, NOW)
        self.assertEqual((table, "Updated" in text, bool(re.match(r"\d{1,2}/\d{1,2} · \d{1,2}:\d{2} [AP]M CT", text))), ([], False, True))  # the stamp, no word Updated

    def test_the_same_inputs_compile_and_render_identically(self):
        args = ([applied("Acme", "Senior Program Manager")], [parent("p1", "Acme", "Senior Program Manager")], [{"id": "e", "title": "Interview Acme", "start": at(1)}])
        a, b = compile_(*args), compile_(*args)
        self.assertEqual(a, b)
        self.assertEqual(render.render(a, None, NOW), render.render(b, None, NOW))

    def test_submitted_roles_older_than_thirty_days_are_counted_not_dropped_silently(self):
        got = compile_([applied("Acme", "Senior Program Manager", "2026-08-01"), applied("Globex", "Program Director", "2026-10-01")])
        text, table, _ = render.render(got, None, NOW)
        self.assertEqual(len(table), 1)
        self.assertIn("1 older submitted not shown", text)


def page(extra_rows=0, wrapped=False, headers=M.HEADERS, duplicate=False, status=None, heading="📈 Hiring Pipeline", with_table=True, after="callout"):
    t = Tree()
    t.add("cols", "column_list", "page-1")
    t.add("left", "column", "cols")
    t.add("right", "column", "cols")
    t.add("cal-card", "callout", "left")
    t.add("cal-h", "heading_3", "cal-card", text="Calendar")
    t.add("cal-p", "paragraph", "cal-card", text="9:00 AM standup")
    parent_id = "right"
    if wrapped:
        t.add("wrap", "callout", "right")
        parent_id = "wrap"
    t.add("0000000000000000000000000000a001", "heading_3", parent_id, text=heading)
    if status == "between":
        t.add("0000000000000000000000000000a002", "paragraph", parent_id, text="old status")
    if with_table:
        t.table("0000000000000000000000000000a003", parent_id, [list(headers)] + [[f"old {n}", "s", "a", "Needs Notes"] for n in range(extra_rows)])
    if duplicate:
        t.add("0000000000000000000000000000a001-2", "heading_3", parent_id, text=heading)
    if after == "callout":
        t.add("0000000000000000000000000000a004", "callout", "right")
        t.add("jira-h", "heading_3", "0000000000000000000000000000a004", text="JIRA Execution")
        t.add("jira-p", "paragraph", "0000000000000000000000000000a004", text="3 open")
        t.add("clients", "callout", "right")
        t.add("clients-h", "heading_3", "clients", text="Clients & Projects")
        t.add("mail-card", "callout", "right")
        t.add("mail-h", "heading_3", "mail-card", text="Mail Alerts")
        t.add("mail-p", "paragraph", "mail-card", text="2 waiting")
        t.add("dcc", "callout", "right")
        t.add("dcc-h", "heading_3", "dcc", text=router.DCC_REGION)
        t.add("dcc-p", "paragraph", "dcc", text="chatgpt text")
        t.add("notes", "paragraph", "page-1", text="Notes: keep out")
    if status == "orphan":
        t.add("0000000000000000000000000000a002", "paragraph", "right", text="old status")
    return t


WANT = [[("Acme — Senior Program Manager", None), ("Submitted · Applied Oct 2", None), ("Awaiting response", None), ("Opportunity Notes", "https://www.notion.so/abc")],
        [("Globex — Director", None), ("Interviewing · Interview Tomorrow 10:00 AM CT", None), ("Prepare", None), ("Needs Notes", None)]]




def table_state(tree, table="0000000000000000000000000000a003"):
    out = []
    for row_id in tree.kids[table][1:]:
        row = tree.blocks[row_id]
        out.append([(R.plain({"type": "x", "x": {"rich_text": cell}}), (cell[0]["text"].get("link") or {}).get("url")) for cell in row["table_row"]["cells"]])
    return out


LED, PAGE = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa", "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
BASE = "https://oracle.tail1234.ts.net:47443"


class CloseLinkTests(unittest.TestCase):
    """S3: a second Notes-cell link to the private Close screen, only for the one Job Ledger row `decision.route` would
    close on, and only with a safely.
    """
    def ids(self, applied_rows, parents):
        rows = compile_(applied_rows, parents)["rows"]
        return rows, stage.close_ids(rows, applied_rows, parents)

    def test_the_url_is_built_only_from_a_bare_https_origin_and_one_hex_id(self):
        pid = LED.replace("-", "")
        self.assertEqual(render.close_url(BASE, pid), f"{BASE}/?o={pid}")
        self.assertEqual(render.close_url(BASE + "/", pid), f"{BASE}/?o={pid}")
        for bad in (None, "", "http://x.ts.net", "https://u:p" + "@x.ts.net", "https://x.ts.net/path", "https://x.ts.net/?a=1", "https://x.ts.net/#f", "//x.ts.net", "https://x.ts.net:99999999", "https://x y.ts.net"):
            self.assertIsNone(render.close_url(bad, pid), bad)
        for bad in (None, "", pid[:-1], pid.upper(), pid + "0", LED, pid[:-1] + "&", pid[:-3] + "?x="):
            self.assertIsNone(render.close_url(BASE, bad), bad)

    def test_the_notes_cell_keeps_the_opportunity_link_and_adds_close_and_without_configuration_nothing_changes(self):
        row = compile_([applied("Acme", "PM")], [parent(PAGE, "Acme", "PM")])["rows"][0]
        link = render.close_url(BASE, PAGE.replace("-", ""))
        plain = render.row_cells(row, NOW)
        both = render.row_cells(row, NOW, link)
        self.assertEqual(both[:3], plain[:3])
        self.assertEqual(both[3], [plain[3], (" · ", None), ("Close ↗", link)])
        self.assertEqual(R.want_view(both)[3], [("Opportunity Page ↗", plain[3][1], None), (" · ", None, None), ("Close ↗", link, None)])
        self.assertEqual(render.row_cells(row, NOW, None), plain)
        orphan = compile_([applied("Acme", "PM")])["rows"][0]
        self.assertEqual(R.want_view(render.row_cells(orphan, NOW, link))[3], [("Needs Notes · ", None, None), ("Close ↗", link, None)])  # a Close link needs no Notes link

class LifecycleTests(unittest.TestCase):
    """a Retired page closes the pursuit, and a role that has waited two weeks says so."""
    def retired(self, page_id, company, role):
        return {**parent(page_id, company, role, 1), "retired": True}

    def test_a_retired_page_closes_the_pursuit_it_names_and_only_that_one(self):
        got = compile_([applied("Walmart", "Senior Manager"), applied("Walmart", "Director"), applied("Acme", "PM")],
                       [self.retired("r1", "Walmart", "Senior Manager")])
        self.assertEqual(sorted((r["company"], r["role"]) for r in got["rows"]), [("Acme", "PM"), ("Walmart", "Director")])
        self.assertEqual(got["closed"], 1)

    def test_an_active_page_for_the_same_role_means_it_is_live_again_and_wins(self):
        got = compile_([applied("Walmart", "Senior Manager")], [self.retired("r1", "Walmart", "Senior Manager"), parent("a1", "Walmart", "Senior Manager", 1)])
        self.assertEqual([(r["company"], r["stage"]) for r in got["rows"]], [("Walmart", "Interviewing")])
        self.assertEqual(got["closed"], 0)

    def test_a_retired_page_with_no_matching_opportunity_changes_nothing(self):
        got = compile_([applied("Acme", "PM")], [self.retired("r1", "Old", "Role")])
        self.assertEqual((len(got["rows"]), got["closed"]), (1, 0))

    def test_a_retired_notes_only_pursuit_with_a_calendar_event_is_closed_and_the_event_is_not_unmatched(self):
        got = compile_([], [self.retired("r1", "Walmart", "Senior Manager")], events=[{"id": "e1", "title": "Interview - Walmart", "start": at(-20)}])
        self.assertEqual(got["rows"] or [], [])

    def test_waiting_on_an_outcome_is_stale_after_seven_days_and_not_before(self):
        row = {"stage": "Interviewing", "company": "Acme", "role": "PM", "event_kind": "interview", "event_start": at(-6), "parent": None, "parent_state": "none", "rounds": 0}
        self.assertEqual(cells(row)[2], "Awaiting outcome of the interview held Sep 30 · follow up Oct 7")
        row["event_start"] = at(-7)
        self.assertEqual(cells(row)[2], "Stale · no news in 7 days · follow up (due Oct 6)")  # 7 calendar days
        row["event_start"] = at(-30)
        self.assertEqual(cells(row)[2], "Stale · no news in 30 days · follow up (due Sep 13)")
        self.assertEqual(cells(row)[1], "Interviewing")

    def test_a_submitted_role_waiting_seven_days_is_stale(self):
        row = {"stage": "Submitted", "company": "Acme", "role": "PM", "applied_on": (NOW - timedelta(days=6)).date().isoformat(), "event_start": None, "parent": None, "parent_state": "none", "rounds": 0}
        self.assertEqual(cells(row)[2], "Awaiting response · follow up Oct 7")
        row["applied_on"] = (NOW - timedelta(days=7)).date().isoformat()
        self.assertEqual(cells(row)[2], "Stale · no response in 7 days · follow up (due Oct 6)")

    def test_a_direct_message_resets_the_stale_clock_and_an_older_one_does_not(self):
        row = {"stage": "Interviewing", "company": "Acme", "role": "PM", "event_kind": "interview", "event_start": at(-30), "parent": None, "parent_state": "none", "rounds": 0}
        row["contact"] = (NOW - timedelta(days=2)).isoformat()
        self.assertEqual(cells(row)[2], "Last contact Oct 4 · follow up Oct 11")
        row["contact"] = (NOW - timedelta(days=9)).isoformat()
        self.assertEqual(cells(row)[2], "Stale · no contact in 9 days · follow up (due Oct 4)")
        row["contact"] = (NOW - timedelta(days=40)).isoformat()
        self.assertEqual(cells(row)[2], "Stale · no news in 30 days · follow up (due Sep 13)")
        naive = dict(row, event_start="2026-09-06T10:00:00", contact=(NOW - timedelta(days=2)).isoformat())  # a time with no offset is Central, never a crash
        self.assertEqual(cells(naive)[2], "Last contact Oct 4 · follow up Oct 11")
        future = dict(row, event_start=at(3, 10), contact=None)
        self.assertTrue(cells(future)[2].startswith("Prepare for the interview"))  # a confirmed future interview protects it


class ActiveTableTests(unittest.TestCase):
    """Slice 1: the accepted Active Opportunities table, from existing data only."""
    def test_the_accepted_table_renders_attention_first_then_by_due_date(self):
        parents = [parent("1234abcd-0000-4000-8000-000000000001", "Globex", "Director", rounds=1)]
        found = {C._key("Hooli", "Program Manager"): {"kind": "REJECTED", "received": "2026-10-05T12:00:00+00:00", "match": "role"}}
        got = C.compile_rows([applied("Acme", "Senior Program Manager"), applied("Initech", "PM", "2026-09-20"), applied("Hooli", "Program Manager", "2026-09-25"), applied("Globex", "Director")],
                             parents, [{"id": "e1", "title": "Interview Globex", "start": at(1, 10)}], None, OK, NOW, outcomes=found)
        _, table, _ = render.render(got, None, NOW)
        self.assertEqual([[t for t, _ in row] for row in table], [
            ["Hooli — Program Manager", "Submitted", "Rejection email received Oct 5 · confirm (Applied stays as history)", "Needs Notes"],  # an outcome email needs attention now
            ["Initech — PM", "Submitted", "Stale · no response in 16 days · follow up (due Sep 27)", "Needs Notes"],
            ["Acme — Senior Program Manager", "Submitted", "Awaiting response · follow up Oct 9", "Needs Notes"],
            ["Globex — Director", "Interviewing", "Prepare for the interview · Tomorrow 10:00 AM CT", "Opportunity Page ↗"]])
        self.assertEqual(table[3][3][1], "https://www.notion.so/1234abcd000040008000000000000001")

    def test_missing_stale_or_unverified_evidence_never_becomes_a_due_date_or_a_green_dot(self):
        base = {"stage": "Submitted", "company": "Acme", "role": "R", "applied_on": None, "event_start": None, "parent": None, "parent_state": "none", "rounds": 0}
        self.assertEqual(cells(base)[0::2], ["Acme — R", "Awaiting response"])  # no Applied date: no follow-up date invented
        self.assertEqual(cells(dict(base, stage="Interviewing", rounds=2))[0::2], ["Acme — R", "Awaiting next step · 2 round(s) logged in Notes"])
        carried = dict(base, stage="Interviewing", event_kind="interview", event_start=at(2), carried=True)
        self.assertEqual(cells(carried)[:2], ["Acme — R", "Interviewing (last accepted)"])
        self.assertEqual(cells(dict(carried, carried=False))[0], "Acme — R")

    def test_the_status_dot_still_sorts_and_classifies_every_row_but_is_never_printed_beside_the_name(self):
        base = {"stage": "Submitted", "company": "Acme", "role": "R", "applied_on": "2026-09-20", "event_start": None, "parent": None, "parent_state": "none", "rounds": 0}
        rows = {"red": dict(base), "yellow": dict(base, applied_on="2026-10-10"), "green": dict(base, stage="Interviewing", event_kind="interview", event_start=at(1, 10))}
        self.assertEqual({k: render.next_step(r, NOW)[0] for k, r in rows.items()}, {"red": render.RED, "yellow": render.YELLOW, "green": render.GREEN})
        for r in rows.values():
            name = cells(r)[0]
            self.assertEqual(name, "Acme — R")
            self.assertFalse(any(dot in str(cells(r)) for dot in (render.RED, render.YELLOW, render.GREEN)))

    def test_a_possible_or_confirmed_offer_is_a_next_action_not_a_stage(self):
        row = {"stage": "Interviewing", "company": "Acme", "role": "R", "event_kind": "interview", "event_start": at(-3), "parent": None, "parent_state": "none", "rounds": 0}
        for match, said in (("role", "Offer email received Oct 5 · review and respond"), ("company", "Possible offer email received Oct 5 · review and respond")):
            got = cells(dict(row, outcome={"kind": "OFFER", "received": "2026-10-05T12:00:00+00:00", "match": match}))
            self.assertEqual((got[0], got[1], got[2]), ("Acme — R", "Interviewing", said))


class HeaderMigrationTests(unittest.TestCase):
    """The header row moves to the accepted text once, in the same fail-closed write"""
    HEAD = "0000000000000000000000000000a003-r0"

class RegionWriterTests(unittest.TestCase):

    def test_split_status_reads_the_dated_stamp_and_still_the_time_only_one(self):
        """`11/13 · 2:00 PM CT · 5 active` holds a middle dot of its own: the stamp is the whole `11/13 · 2:00 PM CT`,
        not the text up to the first dot.
        """
        self.assertEqual(M.split_status("11/13 · 2:00 PM CT · 5 active"), ("11/13 · 2:00 PM CT", "5 active"))
        self.assertEqual(M.split_status("11/13 · 2:00 PM CT"), ("11/13 · 2:00 PM CT", ""))
        self.assertEqual(M.split_status("11/13 · 2:00 PM CT · 5 active · 1 paused or waiting"), ("11/13 · 2:00 PM CT", "5 active · 1 paused or waiting"))
        self.assertEqual(M.split_status("Updated 2:00 PM CT · 5 active"), ("Updated 2:00 PM CT", "5 active"))
        self.assertEqual(M.split_status("Updated Nov 13 · 2:00 PM CT · 5 active"), ("Updated Nov 13 · 2:00 PM CT", "5 active"))  # the older dated form is still read
        self.assertEqual(M.split_status("DEGRADED · Hiring Pipeline unavailable at Nov 13 · 2:00 PM CT · Job Ledger unreadable"), ("DEGRADED at Nov 13 · 2:00 PM CT", "Hiring Pipeline unavailable · Job Ledger unreadable"))
        self.assertEqual(M.split_status("DEGRADED · Hiring Pipeline unavailable at 11/13 · 2:00 PM CT · Job Ledger unreadable · no accepted state yet"),
                         ("DEGRADED at 11/13 · 2:00 PM CT", "Hiring Pipeline unavailable · Job Ledger unreadable · no accepted state yet"))
        self.assertEqual(M.split_status("DEGRADED · Hiring Pipeline Calendar unreadable at 11/13 · 2:00 PM CT · showing last accepted state (11/12 · 1:35 PM CT) · 4 active"),
                         ("DEGRADED at 11/13 · 2:00 PM CT", "Hiring Pipeline Calendar unreadable · showing last accepted state (11/12 · 1:35 PM CT) · 4 active"))

    def test_split_status_covers_every_line_the_renderer_can_produce(self):
        self.assertEqual(M.split_status("Updated 2:00 PM CT · 5 active"), ("Updated 2:00 PM CT", "5 active"))
        self.assertEqual(M.split_status("Updated 2:00 PM CT · no submitted or interviewing opportunities"), ("Updated 2:00 PM CT", "no submitted or interviewing opportunities"))
        self.assertEqual(M.split_status("DEGRADED · Hiring Pipeline unavailable at 2:00 PM CT · Job Ledger unreadable · no accepted state yet"),
                         ("DEGRADED at 2:00 PM CT", "Hiring Pipeline unavailable · Job Ledger unreadable · no accepted state yet"))
        self.assertEqual(M.split_status("DEGRADED · something unexpected"), ("DEGRADED", "something unexpected"))
        self.assertEqual(M.heading_text(""), M.TITLE)

class ReciprocalProtectionTests(unittest.TestCase):
    def test_the_floating_region_is_one_router_owner_and_not_a_callout(self):
        self.assertEqual(router.OWNERS[router.HIRING_REGION], "v7-hiring")
        self.assertEqual(router.HIRING_REGION, M.TITLE)
        self.assertEqual(len(set(router.OWNERS.values())), len(router.OWNERS))
        self.assertIn(router.HIRING_REGION, router.FLOATING)
        self.assertIn(router.RECRUITER_OUTREACH_REGION, router.FLOATING)  # the second floating region, right under this one
        router.check_write("v7-hiring", [router.HIRING_REGION])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-mail-alerts", [router.HIRING_REGION])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-hiring", [router.MAIL_ALERTS_REGION])

    def test_other_writers_digest_the_hiring_region_and_notice_a_change_to_it(self):
        t = page(extra_rows=1)
        fail = lambda code: RuntimeError(code)                                                 # noqa: E731
        ids = {router.JIRA_REGION: "0000000000000000000000000000a004", router.HIRING_REGION: "0000000000000000000000000000a001"}
        before = R.protected(t, ids, fail)
        self.assertEqual(set(before), set(ids))
        self.assertTrue(router.protected_intact("v7-mail-alerts", before, R.protected(t, ids, fail)))
        t.blocks["0000000000000000000000000000a003-r1"]["table_row"]["cells"][0][0]["plain_text"] = "edited"
        self.assertFalse(router.protected_intact("v7-mail-alerts", before, R.protected(t, ids, fail)))
        before = R.protected(t, ids, fail)
        t.blocks["clients-h"]["heading_3"]["rich_text"] = rich("the block after the table is not part of the region")
        self.assertTrue(router.protected_intact("v7-mail-alerts", before, R.protected(t, ids, fail)))
        t.blocks["0000000000000000000000000000a001"]["heading_3"]["rich_text"] = rich("another heading")
        self.assertFalse(router.protected_intact("v7-mail-alerts", before, R.protected(t, ids, fail)))

    def test_an_unconfigured_hiring_anchor_does_not_break_the_other_cards(self):
        t = page()
        got = R.protected(t, {router.JIRA_REGION: "0000000000000000000000000000a004", router.HIRING_REGION: ""}, lambda code: RuntimeError(code))
        self.assertEqual(set(got), {router.JIRA_REGION})
        with self.assertRaises(RuntimeError):
            R.protected(t, {router.JIRA_REGION: "", router.HIRING_REGION: "0000000000000000000000000000a001"}, lambda code: RuntimeError(code))  # a missing mandatory id still fails

class FakeLedger:
    source = "src"

    def __init__(self, rows, broken=False, stage_rows=()):
        self.rows, self.broken, self.stage_rows = rows, broken, list(stage_rows)

    def call(self, method, path, body=None):
        if method == "GET" and path.startswith("/blocks/"):
            return {"results": [], "has_more": False}
        return {"properties": {name: {"type": kind} for name, kind in ledger.REQUIRED.items()}}

    def query_data_source(self, source_id=None, body=None):
        if self.broken:
            raise stage.NotionError("NOTION_HTTP_500")
        if body["filter"] == {"property": "Hiring Decision", "select": {"is_not_empty": True}}:
            return {"results": [], "has_more": False}
        if body["filter"] == {"property": "Stage", "select": {"is_not_empty": True}}:  # the Ledger-first read
            return {"results": [{"id": i, "properties": {"Company": {"rich_text": rich(c)}, "Job": {"title": rich(r)}, "Stage": {"select": {"name": st}},
                                 "Hiring Decision": {"select": {"name": d} if d else None}}} for i, c, r, st, d in self.stage_rows], "has_more": False}
        assert body["filter"] == {"property": "Applied", "checkbox": {"equals": True}}
        return {"results": [{"properties": {"Company": {"rich_text": rich(c)}, "Job": {"title": rich(r)}, "Applied On": {"date": {"start": d} if d else None}}} for c, r, d in self.rows],
                "has_more": False}


class FakeCalendar:
    def __init__(self, events=(), broken=False):
        self.events, self.broken, self.windows = list(events), broken, []

    def list_events(self, lo, hi, private_property=None):
        self.windows.append((lo, hi))
        if self.broken:
            raise RuntimeError("calendar down")
        return self.events


def gcal(event_id, title, start, **extra):
    return {"id": event_id, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": start}, **extra}


PARENTS = [hiring_pipeline.Opportunity("aaaa1111-0000-4000-8000-000000000001", "Acme - Senior Program Manager", hiring_pipeline.ACTIVE, 0, "Acme", "Senior Program Manager")]
RUN_ENV = {**ENV, "HIRING_PIPELINE_PAGE_ID": "root", "HIRING_AUTO_CLOSE": "off"}  # tests choose the switch


def run(tree, store, live=True, rows=(("Acme", "Senior Program Manager", "2026-10-02"),), events=(), parents=PARENTS, ledger_broken=False, calendar_broken=False, pages_ok=True, now=NOW):
    pages = (lambda client, root, ledger=None: (list(parents), "ok")) if pages_ok else (lambda client, root, ledger=None: ([], "unreadable"))
    with mock.patch.object(snapshot, "STORE", store), mock.patch.object(stage.hiring_pipeline, "snapshot", pages):
        return stage.run(0, live, RUN_ENV, ledger_client=FakeLedger(list(rows), ledger_broken), pipeline_client=object(), calendar=FakeCalendar(events, calendar_broken),
                         connection=object(), client=tree, now=now)


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        fresh = (NOW - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
        patcher = mock.patch.object(stage.outcome_mail, "load", lambda conn: ({}, fresh))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_entry_point_is_a_registered_stage_and_the_region_job_waits_for_no_other_writer_but_dcc_follows_every_writer(self):
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["hiring"].target, ("lifeos.sources.pipeline.hiring", "run"))
        import yaml
        doc = yaml.safe_load((ROOT / ".github/workflows/domains.yml").read_text())
        self.assertEqual(doc["jobs"]["hiring"]["needs"], ["gate"])
        self.assertTrue({"jira", "agenda", "bills", "amazon", "mail", "hiring"} <= set(doc["jobs"]["dcc"]["needs"]))
        for name in ("outlook", "megibow"):  # the two that never write the Daily Report page
            self.assertNotIn("hiring", doc["jobs"][name].get("needs", []))
        self.assertIn("hiring", doc["jobs"]["recruiters"]["needs"])
        step = next(s for s in doc["jobs"]["hiring"]["steps"] if "lifeos.run hiring" in s.get("run", ""))  # a Bitwarden loader step precedes it
        self.assertIn("lifeos.run hiring", step["run"])
        self.assertTrue(step["continue-on-error"])
        self.assertIs(doc[True]["workflow_dispatch"]["inputs"]["hiring"]["default"], False)

    def test_dry_run_reads_everything_and_writes_nothing(self):
        t, store = page(extra_rows=1), FakeStore()
        counts = run(t, store, live=False, events=[gcal("e1", "Interview Acme", at(1))])
        self.assertEqual((writes(t), store.saves, counts["saved"], counts["blocks_written"]), ([], 0, 0, 0))
        self.assertEqual((counts["applied"], counts["opportunities"], counts["events"], counts["status"]), (1, 1, 1, "fresh"))

    def test_a_failed_snapshot_save_stops_before_the_page_is_touched(self):
        t = page(extra_rows=1)
        with self.assertRaises(snapshot.HiringError) as error:
            run(t, FakeStore(fail=True))
        self.assertEqual(str(error.exception), "HIRING_READBACK_MISMATCH")
        self.assertEqual(writes(t), [])

    def test_the_calendar_window_is_read_only_and_bounded(self):
        calendar = FakeCalendar()
        stage.read_events(calendar, NOW)
        lo, hi = calendar.windows[0]
        self.assertEqual((datetime.fromisoformat(hi) - datetime.fromisoformat(lo)).days, stage.PAST_DAYS + stage.FUTURE_DAYS)

    def test_ledger_and_pages_are_read_with_complete_pagination_or_not_at_all(self):
        ledger_client = FakeLedger([("A", "B", None)])
        ledger_client.query_data_source = lambda source_id=None, body=None: {"results": [], "has_more": True, "next_cursor": None}
        with self.assertRaises(stage.NotionError):
            stage.read_applied(ledger_client)
        with mock.patch.object(stage.hiring_pipeline, "snapshot", lambda client, root, ledger=None: ([], "unreadable")):
            with self.assertRaises(stage.NotionError):
                stage.read_parents(object(), "root")
        retired = [hiring_pipeline.Opportunity("r", "Old - Role", hiring_pipeline.RETIRED, 1, "Old", "Role")]
        with mock.patch.object(stage.hiring_pipeline, "snapshot", lambda client, root, ledger=None: (retired + PARENTS, "ok")):
            self.assertEqual([(p["company"], p["retired"]) for p in stage.read_parents(object(), "root")], [("Old", True), ("Acme", False)])


class OutcomeConsumerTests(unittest.TestCase):
    """end of the chain: what the outcome-mail stage stored reaches the Hiring Pipeline region as a tentative line, with
    every other block, the Applied.
    """
    KEY = "acme|manager|program|senior"
    FOUND = {KEY: {"kind": "REJECTED", "received": (NOW - timedelta(days=1)).astimezone(timezone.utc).isoformat(), "match": "role"}}
    SCANNED = (NOW - timedelta(hours=2)).astimezone(timezone.utc).isoformat()

    def go(self, loader, parents=PARENTS):
        t, store = page(extra_rows=0), FakeStore()
        with mock.patch.object(stage.outcome_mail, "load", loader):
            counts = run(t, store, parents=parents)
        return t, store, counts

    def test_the_ledger_is_only_ever_read_and_the_applied_state_is_never_written(self):
        class ReadOnlyLedger(FakeLedger):
            def __getattr__(self, name):
                raise AssertionError(f"ledger write attempted: {name}")
        t, store = page(extra_rows=0), FakeStore()
        with mock.patch.object(stage.outcome_mail, "load", lambda conn: (self.FOUND, self.SCANNED)), mock.patch.object(snapshot, "STORE", store), \
                mock.patch.object(stage.hiring_pipeline, "snapshot", lambda client, root, ledger=None: (list(PARENTS), "ok")):
            stage.run(0, True, RUN_ENV, ledger_client=ReadOnlyLedger([("Acme", "Senior Program Manager", "2026-10-02")]), pipeline_client=object(), calendar=FakeCalendar([]),
                      connection=object(), client=t, now=NOW)
        self.assertEqual([e for e in t.log if "Applied" in json.dumps(e)], [])

    def seeded(self, loader):
        """a first fresh run saves the accepted state"""
        t, store = page(extra_rows=0), FakeStore()
        with mock.patch.object(stage.outcome_mail, "load", lambda conn: ({}, self.SCANNED)):
            run(t, store)
        with mock.patch.object(stage.outcome_mail, "load", loader):
            counts = run(t, store)
        return t, store, counts

    def test_outcome_mail_not_saved_for_eighteen_hours_or_never_saved_is_degraded_and_a_fresh_save_clears_it(self):
        stale = (NOW - timedelta(hours=19)).astimezone(timezone.utc).isoformat()
        for scanned in (stale, None):
            with self.subTest(scanned=scanned):
                t, store, counts = self.seeded(lambda conn, scanned=scanned: (self.FOUND, scanned))
                self.assertEqual((counts["status"], counts["reasons"]), ("degraded", ["OUTCOME_MAIL_STALE"]))
                self.assertEqual(counts["mail_outcomes"], 1)
        t, store, counts = self.seeded(lambda conn: (self.FOUND, self.SCANNED))
        self.assertEqual((counts["status"], counts["reasons"]), ("fresh", []))

    def test_a_pipeline_with_no_accepted_state_yet_is_not_degraded_by_a_scan_that_has_nothing_to_look_for(self):
        t, store, counts = self.go(lambda conn: ({}, None))
        self.assertEqual(counts["status"], "fresh")


class AutoCloseTests(unittest.TestCase):
    FOUND = {OutcomeConsumerTests.KEY: {**OutcomeConsumerTests.FOUND[OutcomeConsumerTests.KEY], "exact": True}}
    TARGET = "aaaa1111-0000-4000-8000-0000000000ff"

    def go(self, found=None, switch="on", live=True, store=None, now=NOW, write=None, rows=(("Acme", "Senior Program Manager", "2026-10-02"),), calendar_broken=False):
        t, store = page(extra_rows=0), store or FakeStore()
        calls = []
        def confirm(client, request, live_, at):
            calls.append(request)
            return write(request) if write else {"status": "recorded", "verified": True, "entries": 1}
        pages = [{"id": f"{i:032x}", "properties": {"Company": {"rich_text": rich(c)}, "Job": {"title": rich(r)}}} for i, (c, r, _) in enumerate(rows, 1)]  # the Ledger's pursuit rows
        with mock.patch.object(stage.outcome_mail, "load", lambda conn: (self.FOUND if found is None else found, (now - timedelta(hours=2)).astimezone(timezone.utc).isoformat())), \
                mock.patch.dict(RUN_ENV, {"HIRING_AUTO_CLOSE": switch}), mock.patch.object(stage.auto_close.decision, "route", lambda r, w: {"id": self.TARGET}), \
                mock.patch.object(stage.auto_close.decision, "ledger_pages", lambda client: pages), \
                mock.patch.object(stage.auto_close.decision, "confirm_rejected", confirm):
            counts = run(t, store, live=live, now=now, rows=rows, calendar_broken=calendar_broken)
        return t, store, counts, calls

    def test_exact_role_needs_the_whole_title_with_no_seniority_or_level_touching_it(self):
        from lifeos.hiring import outcomes as O
        for text, want in (("Your application for Program Manager at Acme", True), ("Update on the Program Manager role", True), ("Senior Program Manager", False),
                           ("Program Manager II opening", False), ("Program and Product Manager", False), ("Lead Program Manager", False)):
            self.assertEqual(O.exact_role({"subject": text, "body": ""}, "Program Manager"), want, text)

class GuardrailTests(unittest.TestCase):
    SOURCES = ["lifeos/hiring/models.py", "lifeos/hiring/compile.py", "lifeos/hiring/render.py", "lifeos/hiring/snapshot.py", "lifeos/sources/pipeline/hiring.py"]

    def test_there_is_no_mail_calendar_jira_ledger_or_tinyfish_mutation_path(self):
        for name in self.SOURCES:
            text = (ROOT / name).read_text().lower()
            for banned in ("tinyfish", "gmail", "outlook", ".insert(", "update_event", "delete_event", "send_message", "update_page_properties", ".create(", "lifeos.jira", "lifeos.interview"):
                self.assertNotIn(banned, text, f"{name}: {banned}")
        self.assertEqual(re.findall(r"client\.call(?:_once)?\(\"(?:POST|PUT)\"", (ROOT / "lifeos/sources/pipeline/hiring.py").read_text()), [])

    def test_hourly_and_the_scheduler_are_untouched(self):
        hourly = (ROOT / ".github/workflows/hourly.yml").read_text()
        self.assertNotIn("lifeos.run hiring", hourly)
        self.assertNotIn("HIRING_CARD_BLOCK_ID", hourly)
        domains = (ROOT / ".github/workflows/domains.yml").read_text()
        self.assertNotIn("schedule:", domains)
        self.assertNotIn("cron", domains)

    def test_no_private_identifier_is_committed(self):
        import hashlib
        forbidden = {"d1725f63cf1ac7162fd64889fb4f9853f23dfcaa3c74fddc03a6e0444b589c9c", "a26243ba801992915efcdb9597e9f90579c3940b288e9c7cd0d355e04cf47bb5"}
        found = re.compile(r"\b[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\b")
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path.suffix not in (".py", ".md", ".yml", ".json", ".txt"):
                continue
            for match in found.findall(path.read_text(errors="ignore")):
                self.assertNotIn(hashlib.sha256(match.replace("-", "").lower().encode()).hexdigest(), forbidden, path.name)

    def test_the_stage_never_returns_names_or_ids(self):
        t, store = page(), FakeStore()
        out = json.dumps(run(t, store))
        for private in ("Acme", "Senior Program Manager", "aaaa1111", "0000000000000000000000000000a001"):
            self.assertNotIn(private, out)


if __name__ == "__main__":
    unittest.main()
