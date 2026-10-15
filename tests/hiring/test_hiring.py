"""HIRE-1.2: Job Ledger Applied + Hiring Pipeline pages + Calendar -> deterministic compiler -> one private snapshot row -> the floating Daily Report region."""
import copy
import inspect
import json
import pathlib
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from lifeos.hiring import card, compile as C, models as M, render, snapshot
from lifeos.jobs import hiring_pipeline, ledger
from lifeos.platform import report_region as R, router
from lifeos.sources import hiring as stage
from tests.kit.tree import Tree, rich

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
        self.assertEqual(cells(got["rows"][0])[1:3], ["Submitted", "Awaiting response · follow up Oct 16"])

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
        self.assertIn("held today", cells(dict(row, event_start=at(0, 9)))[2])                 # 9 AM has started by 2 PM

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
        self.assertIn("showing last accepted state (Oct 6, 2:00 PM CT)", text)
        self.assertIn("(last accepted)", table[0][1][0])
        vanished = compile_([applied("Acme", "Senior Program Manager")], prior=prior)               # Calendar read fine, the future event is gone
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
        self.assertEqual((table, text.startswith("Updated")), ([], True))

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
    if status == "between":                                       # the retired status paragraph back under the heading
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
    if status == "orphan":                                        # the live incident state: the restored paragraph at the bottom of the right column
        t.add("0000000000000000000000000000a002", "paragraph", "right", text="old status")
    return t


WANT = [[("Acme — Senior Program Manager", None), ("Submitted · Applied Oct 2", None), ("Awaiting response", None), ("Opportunity Notes", "https://www.notion.so/abc")],
        [("Globex — Director", None), ("Interviewing · Interview Tomorrow 10:00 AM CT", None), ("Prepare", None), ("Needs Notes", None)]]


def writes(tree):
    return [e for e in tree.log if e[0] != "GET"]


def table_state(tree, table="0000000000000000000000000000a003"):
    out = []
    for row_id in tree.kids[table][1:]:
        row = tree.blocks[row_id]
        out.append([(R.plain({"type": "x", "x": {"rich_text": cell}}), (cell[0]["text"].get("link") or {}).get("url")) for cell in row["table_row"]["cells"]])
    return out


class LifecycleTests(unittest.TestCase):
    """D189: a Retired page closes the pursuit, and a role that has waited two weeks says so."""
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

    def test_waiting_on_an_outcome_says_so_after_fourteen_days_and_not_before(self):
        row = {"stage": "Interviewing", "company": "Acme", "role": "PM", "event_kind": "interview", "event_start": at(-13), "parent": None, "parent_state": "none", "rounds": 0}
        self.assertEqual(cells(row)[2], "Awaiting outcome of the interview held Sep 23 · follow up Oct 7")
        row["event_start"] = at(-14)
        self.assertEqual(cells(row)[2], "No news in 14 days · follow up (due Oct 6)")
        row["event_start"] = at(-30)
        self.assertEqual(cells(row)[2], "No news in 30 days · follow up (due Sep 20)")
        self.assertEqual(cells(row)[1], "Interviewing")                                         # the stage is unchanged

    def test_a_submitted_role_waiting_fourteen_days_asks_for_a_follow_up(self):
        row = {"stage": "Submitted", "company": "Acme", "role": "PM", "applied_on": (NOW - timedelta(days=13)).date().isoformat(), "event_start": None, "parent": None, "parent_state": "none", "rounds": 0}
        self.assertEqual(cells(row)[2], "Awaiting response · follow up Oct 7")
        row["applied_on"] = (NOW - timedelta(days=14)).date().isoformat()
        self.assertEqual(cells(row)[2], "No response in 14 days · follow up (due Oct 6)")


class ActiveTableTests(unittest.TestCase):
    """LIFE-47 Slice 1 (Jim, comments 10177 and the decision after 10179): the accepted Active Opportunities table, from existing data only."""
    def test_the_accepted_table_renders_attention_first_then_by_due_date(self):
        parents = [parent("1234abcd-0000-4000-8000-000000000001", "Globex", "Director", rounds=1)]
        found = {C._key("Hooli", "Program Manager"): {"kind": "REJECTED", "received": "2026-10-05T12:00:00+00:00", "match": "role"}}
        got = C.compile_rows([applied("Acme", "Senior Program Manager"), applied("Initech", "PM", "2026-09-20"), applied("Hooli", "Program Manager", "2026-09-25"), applied("Globex", "Director")],
                             parents, [{"id": "e1", "title": "Interview Globex", "start": at(1, 10)}], None, OK, NOW, outcomes=found)
        _, table, _ = render.render(got, None, NOW)
        self.assertEqual([[t for t, _ in row] for row in table], [
            ["🔴 Hooli — Program Manager", "Submitted", "Rejection email received Oct 5 · confirm (Applied stays as history)", "Needs Notes"],      # an outcome email needs attention now; its date is evidence, not a deadline
            ["🔴 Initech — PM", "Submitted", "No response in 16 days · follow up (due Oct 4)", "Needs Notes"],
            ["🟡 Acme — Senior Program Manager", "Submitted", "Awaiting response · follow up Oct 16", "Needs Notes"],
            ["🟢 Globex — Director", "Interviewing", "Prepare for the interview · Tomorrow 10:00 AM CT", "Opportunity Page ↗"]])
        self.assertEqual(table[3][3][1], "https://www.notion.so/1234abcd000040008000000000000001")

    def test_missing_stale_or_unverified_evidence_never_becomes_a_due_date_or_a_green_dot(self):
        base = {"stage": "Submitted", "company": "Acme", "role": "R", "applied_on": None, "event_start": None, "parent": None, "parent_state": "none", "rounds": 0}
        self.assertEqual(cells(base)[0::2], ["🟡 Acme — R", "Awaiting response"])                                   # no Applied date: no follow-up date invented
        self.assertEqual(cells(dict(base, stage="Interviewing", rounds=2))[0::2], ["🟡 Acme — R", "Awaiting next step · 2 round(s) logged in Notes"])
        carried = dict(base, stage="Interviewing", event_kind="interview", event_start=at(2), carried=True)
        self.assertEqual(cells(carried)[:2], ["🟡 Acme — R", "Interviewing (last accepted)"])                       # a future event the Calendar no longer confirms is not verified activity
        self.assertEqual(cells(dict(carried, carried=False))[0], "🟢 Acme — R")

    def test_a_possible_or_confirmed_offer_is_a_next_action_not_a_stage(self):
        row = {"stage": "Interviewing", "company": "Acme", "role": "R", "event_kind": "interview", "event_start": at(-3), "parent": None, "parent_state": "none", "rounds": 0}
        for match, said in (("role", "Offer email received Oct 5 · review and respond"), ("company", "Possible offer email received Oct 5 · review and respond")):
            got = cells(dict(row, outcome={"kind": "OFFER", "received": "2026-10-05T12:00:00+00:00", "match": match}))
            self.assertEqual((got[0], got[1], got[2]), ("🔴 Acme — R", "Interviewing", said))


class HeaderMigrationTests(unittest.TestCase):
    """The header row moves to the accepted text once, in the same fail-closed write; nothing else is touched and a replay changes nothing."""
    HEAD = "0000000000000000000000000000a003-r0"

    def test_a_dry_run_on_the_old_header_validates_writes_nothing_and_says_a_migration_is_pending(self):
        t = page(extra_rows=1, headers=M.OLD_HEADERS)
        done = card.present(t, ENV, "x", WANT, False)
        self.assertEqual((writes(t), done["verified"], done["header"]), ([], False, "old"))

    def test_the_live_write_migrates_the_header_with_the_rows_and_proves_the_page_unchanged(self):
        t = page(extra_rows=1, headers=M.OLD_HEADERS)
        done = card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        self.assertEqual((done["verified"], done["header"]), (True, "migrated"))
        self.assertEqual(R.cells_of(t.blocks[self.HEAD]), list(M.HEADERS))
        self.assertEqual(table_state(t), [[(a, b) for a, b in row] for row in WANT])
        t.log.clear()
        again = card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        self.assertEqual((again["header"], again["rows_changed"], writes(t)), ("new", 0, []))               # idempotent

    def test_any_other_header_is_refused_before_a_single_write(self):
        t = page(extra_rows=1, headers=("Company / Role", "Stage", "Action Needed", "Source"))
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, ENV, "x", WANT, True)
        self.assertEqual((str(error.exception), writes(t)), ("HIRING_HEADERS_CHANGED", []))

    def test_a_failed_header_write_is_honest_keeps_the_rows_and_the_next_run_finishes_it(self):
        t = page(extra_rows=1, headers=M.OLD_HEADERS)
        t.fail_write = self.HEAD
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, ENV, "Updated 2:00 PM CT", WANT, True)
        self.assertEqual(str(error.exception), "HIRING_WRITE_FAILED")
        self.assertEqual(R.cells_of(t.blocks[self.HEAD]), list(M.OLD_HEADERS))                              # the old header is intact, never half-written
        self.assertEqual(R.plain(t.blocks["0000000000000000000000000000a001"]), "📈 Hiring Pipeline (DEGRADED)")                   # the heading says the table may be incomplete
        t.fail_write = None
        done = card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        self.assertEqual((done["verified"], done["header"], R.cells_of(t.blocks[self.HEAD])), (True, "migrated", list(M.HEADERS)))


class RegionWriterTests(unittest.TestCase):
    def test_the_exact_heading_table_shape_is_written_in_place_and_read_back(self):
        t = page(extra_rows=1)
        before = t.snapshot(skip={"0000000000000000000000000000a001", "0000000000000000000000000000a003", "0000000000000000000000000000a003-r0", "0000000000000000000000000000a003-r1"})
        done = card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        self.assertTrue(done["verified"])
        self.assertEqual((done["rows_changed"], done["rows_added"], done["rows_removed"]), (1, 1, 0))
        self.assertEqual(table_state(t), [[(a, b) for a, b in row] for row in WANT])
        self.assertEqual(t.snapshot(skip={"0000000000000000000000000000a001", "0000000000000000000000000000a003"} | set(t.kids["0000000000000000000000000000a003"]) | {k for k in t.blocks if k.startswith("new-")}), {k: v for k, v in before.items() if not k.startswith("0000000000000000000000000000a003-r")})
        self.assertEqual(R.plain(t.blocks["0000000000000000000000000000a001"]), "📈 Hiring Pipeline (Updated 2:00 PM CT)")

    def test_the_header_row_is_never_written_and_the_heading_only_ever_gets_its_stamp(self):
        t = page(extra_rows=3)
        card.present(t, ENV, "Updated 2:00 PM CT · 1 active", WANT[:1], True)
        patched = [path for method, path in writes(t) if method == "PATCH"]
        self.assertFalse(any("0000000000000000000000000000a003-r0" in p for p in patched))
        self.assertEqual(sum("0000000000000000000000000000a001" in p for p in patched), 1)                          # one PATCH of the heading: its title and the stamp
        self.assertEqual(t.blocks["0000000000000000000000000000a001"]["type"], "heading_3")
        self.assertEqual(len(t.kids["0000000000000000000000000000a003"]), 2)                                         # header + one row; the surplus rows were removed

    def test_a_dry_run_validates_and_writes_nothing(self):
        t = page(extra_rows=1)
        done = card.present(t, ENV, "x", WANT, False)
        self.assertEqual((writes(t), done["verified"]), ([], False))

    def test_an_identical_replay_changes_no_row(self):
        t = page(extra_rows=1)
        card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        t.log.clear()
        again = card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        self.assertEqual((again["rows_changed"], again["rows_added"], again["rows_removed"], writes(t)), (0, 0, 0, []))
        self.assertEqual(len(t.kids["0000000000000000000000000000a003"]), 3)

    def test_a_callout_wrapped_hiring_surface_is_rejected_without_a_write(self):
        t = page(wrapped=True)
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, ENV, "x", WANT, True)
        self.assertEqual(str(error.exception), "HIRING_FLOATING_WRAPPED_IN_CALLOUT")
        self.assertEqual(writes(t), [])

    def test_a_wrong_missing_or_duplicate_heading_is_rejected(self):
        for kwargs, code in (({"heading": "Hiring Pipeline"}, "FLOATING_HEADING_INVALID"), ({"duplicate": True}, "FLOATING_HEADING_AMBIGUOUS")):
            t = page(**kwargs)
            with self.assertRaises(snapshot.HiringError) as error:
                card.present(t, ENV, "x", WANT, True)
            self.assertEqual(str(error.exception), "HIRING_" + code)
            self.assertEqual(writes(t), [])
        t = page()
        with self.assertRaises(stage.NotionError):
            card.present(t, {**ENV, "HIRING_CARD_BLOCK_ID": "0000000000000000000000000000b003"}, "x", WANT, True)
        self.assertEqual(writes(t), [])
        with self.assertRaises(snapshot.HiringError):
            card.present(page(), {k: v for k, v in ENV.items() if k != "HIRING_CARD_BLOCK_ID"}, "x", WANT, True)
        with self.assertRaises(stage.NotionError):
            card.present(page(), {**ENV, "HIRING_CARD_BLOCK_ID": "0000000000000000000000000000b003"}, "x", WANT, True)
        t = page()
        t.blocks["0000000000000000000000000000a001"]["type"] = "heading_2"
        with self.assertRaises(snapshot.HiringError):
            card.present(t, ENV, "x", WANT, True)

    def test_both_blocks_are_pinned_and_a_different_block_under_the_heading_is_never_written(self):
        for missing in ("HIRING_TABLE_BLOCK_ID", "HIRING_CARD_BLOCK_ID"):
            t = page()
            with self.assertRaises(snapshot.HiringError) as error:
                card.present(t, {k: v for k, v in ENV.items() if k != missing}, "x", WANT, True)
            self.assertEqual(str(error.exception), "HIRING_NOT_CONFIGURED")
            self.assertEqual(writes(t), [])
        for wrong in ({"HIRING_TABLE_BLOCK_ID": "0000000000000000000000000000b002"},):
            t = page()
            with self.assertRaises(snapshot.HiringError) as error:
                card.present(t, {**ENV, **wrong}, "x", WANT, True)
            self.assertEqual(str(error.exception), "HIRING_PINNED_BLOCK_MISMATCH")
            self.assertEqual(writes(t), [])

    def test_a_copied_notion_link_is_accepted_as_the_block_id_everywhere(self):
        link = lambda block: f"https://example.test/Daily-Report-3c73c5a0592680e3898cc56025ab8678?source=copy_link#{block}"      # noqa: E731
        env = {**ENV, "HIRING_CARD_BLOCK_ID": link("0000000000000000000000000000a001"),
               "HIRING_TABLE_BLOCK_ID": link("0000000000000000000000000000a003")}
        self.assertTrue(card.present(page(extra_rows=1), env, "Updated", WANT, True)["verified"])
        self.assertEqual(R.clean_id("0000000000000000-0000-00000000A001"), "0000000000000000000000000000a001")
        self.assertEqual(R.clean_id("not an id"), "not an id")
        t = page()
        got = R.protected(t, {router.JIRA_REGION: "0000000000000000000000000000a004", router.HIRING_REGION: link("0000000000000000000000000000a001")}, lambda code: RuntimeError(code))
        self.assertEqual(set(got), {router.JIRA_REGION, router.HIRING_REGION})

    def test_a_pasted_link_or_stray_text_is_named_by_secret_not_value(self):
        t = page()
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, {**ENV, "HIRING_TABLE_BLOCK_ID": "https://example.test/page#abc"}, "x", WANT, True)
        self.assertRegex(str(error.exception), r"^HIRING_ID_INVALID:HIRING_TABLE_BLOCK_ID:len\d+$")
        self.assertNotIn("example", str(error.exception))
        self.assertEqual(writes(t), [])

    def test_a_changed_shape_is_rejected(self):
        for kwargs, code in (({"headers": ("Company", "Stage", "Action", "Source")}, "HEADERS_CHANGED"), ({"status": "between"}, "FLOATING_SHAPE_INVALID"), ({"with_table": False}, "FLOATING_SHAPE_INVALID")):
            t = page(**kwargs)
            with self.assertRaises(snapshot.HiringError) as error:
                card.present(t, ENV, "x", WANT, True)
            self.assertEqual(str(error.exception), "HIRING_" + code)
            self.assertEqual(writes(t), [])
        t = page()
        t.add("extra-p", "paragraph", "right", text="an unexpected paragraph", index=t.kids["right"].index("0000000000000000000000000000a004"))        # the region now runs on past its table
        with self.assertRaises(snapshot.HiringError):
            card.present(t, ENV, "x", WANT, True)
        t = page()
        t.blocks["0000000000000000000000000000a003"]["table"]["table_width"] = 5
        with self.assertRaises(snapshot.HiringError):
            card.present(t, ENV, "x", WANT, True)

    def test_the_retired_status_paragraph_is_not_ours_a_stale_secret_is_ignored_and_the_orphan_is_never_touched(self):
        t = page(extra_rows=1, status="orphan")
        orphan = dict(t.blocks["0000000000000000000000000000a002"])
        done = card.present(t, {**ENV, "HIRING_STATUS_BLOCK_ID": "not even an id"}, "Updated 2:00 PM CT · 2 active", WANT, True)     # the old secret may stay set: nothing reads it
        self.assertTrue(done["verified"])
        self.assertEqual((t.blocks["0000000000000000000000000000a002"], any("0000000000000000000000000000a002" in path for _, path in writes(t))), (orphan, False))
        for status in ("between",):                                                        # a paragraph back between the heading and the table is a wrong shape: nothing is written
            t = page(extra_rows=1, status=status)
            with self.assertRaises(snapshot.HiringError) as error:
                card.present(t, ENV, "x", WANT, True)
            self.assertEqual((str(error.exception), writes(t)), ("HIRING_FLOATING_SHAPE_INVALID", []))

    def test_a_region_on_another_page_than_the_report_is_rejected(self):
        t = page()
        t.blocks["cols"]["parent"] = {"type": "page_id", "page_id": "page-2"}                   # the Jira card now sits on a different page than the anchor's column
        t.blocks["left"]["parent"] = {"type": "block_id", "block_id": "cols"}
        t.blocks["0000000000000000000000000000a004"]["parent"] = {"type": "page_id", "page_id": "page-9"}
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, ENV, "x", WANT, True)
        self.assertEqual(str(error.exception), "HIRING_WRONG_PAGE")

    def test_every_other_region_and_the_notes_are_proven_unchanged_and_a_change_fails_closed(self):
        for victim in ("clients-h", "mail-p", "cal-p", "jira-p", "notes"):
            t = page()
            t.trip = ("0000000000000000000000000000a001", lambda tree, v=victim: tree.blocks[v][tree.blocks[v]["type"]].update({"rich_text": rich("changed by someone else")}))
            with self.assertRaises(snapshot.HiringError) as error:
                card.present(t, ENV, "x", WANT[:1], True)
            self.assertEqual(str(error.exception), "HIRING_PROTECTED_REGION_CHANGED", victim)
        t = page()                                                                              # a block that vanishes or a new one that appears is a change too
        t.trip = ("0000000000000000000000000000a001", lambda tree: tree.add("sneaky", "paragraph", "page-1", text="new"))
        with self.assertRaises(snapshot.HiringError):
            card.present(t, ENV, "x", WANT[:1], True)

    def test_the_stamp_follows_the_status_and_the_heading_keeps_its_block_title_and_place(self):
        t = page()
        for text, heading in (("Updated 2:00 PM CT · 3 active · 1 more not shown", "📈 Hiring Pipeline (Updated 2:00 PM CT)"),
                              ("DEGRADED · Hiring Pipeline Calendar unreadable at 3:00 PM CT · showing last accepted state (Oct 6, 2:00 PM CT) · 3 active",
                               "📈 Hiring Pipeline (DEGRADED at 3:00 PM CT)")):
            self.assertTrue(card.present(t, ENV, text, WANT[:1], True)["verified"])
            self.assertEqual(R.plain(t.blocks["0000000000000000000000000000a001"]), heading)
            self.assertEqual(t.kids["right"][:2], ["0000000000000000000000000000a001", "0000000000000000000000000000a003"])

    def test_split_status_covers_every_line_the_renderer_can_produce(self):
        self.assertEqual(M.split_status("Updated 2:00 PM CT · 5 active"), ("Updated 2:00 PM CT", "5 active"))
        self.assertEqual(M.split_status("Updated 2:00 PM CT · no submitted or interviewing opportunities"), ("Updated 2:00 PM CT", "no submitted or interviewing opportunities"))
        self.assertEqual(M.split_status("DEGRADED · Hiring Pipeline unavailable at 2:00 PM CT · Job Ledger unreadable · no accepted state yet"),
                         ("DEGRADED at 2:00 PM CT", "Hiring Pipeline unavailable · Job Ledger unreadable · no accepted state yet"))
        self.assertEqual(M.split_status("DEGRADED · something unexpected"), ("DEGRADED", "something unexpected"))
        self.assertEqual(M.heading_text(""), M.TITLE)

    def test_a_second_heading_with_the_title_or_a_stamped_title_is_a_second_region(self):
        for second in ("📈 Hiring Pipeline", "📈 Hiring Pipeline (Updated 1:00 PM CT)"):
            t = page()
            t.add("extra-h", "heading_3", "cols", text=second)
            with self.assertRaises(snapshot.HiringError):
                card.present(t, ENV, "Updated 2:00 PM CT · 1 active", WANT[:1], True)

    def test_the_command_center_reads_the_stamp_from_the_heading(self):
        from lifeos.dcc import card as dcc
        t = page()
        card.present(t, ENV, "Updated 2:00 PM CT · 2 active", WANT, True)
        status, _ = dcc._text_of(t, "0000000000000000000000000000a001", "floating", router.HIRING_REGION)
        self.assertEqual(status, "Updated 2:00 PM CT · 2 active")                       # the stamp from the heading, the count from the table rows
        self.assertEqual(dcc.classify({"status": status}, NOW)[0], "fresh")
        card.present(t, ENV, "DEGRADED · Hiring Pipeline Calendar unreadable at 2:05 PM CT · showing last accepted state (Oct 6, 2:00 PM CT) · 3 active", WANT[:1], True)
        status, _ = dcc._text_of(t, "0000000000000000000000000000a001", "floating", router.HIRING_REGION)
        self.assertEqual(dcc.classify({"status": status}, NOW)[0], "degraded")
        self.assertEqual(dcc._text_of(page(), "0000000000000000000000000000a001", "floating", router.HIRING_REGION)[0], "")      # a heading with no stamp yet (before the first write) reads as unknown, never as fresh
        with self.assertRaises(stage.NotionError):                                      # the retired shape (a paragraph under the heading) is unreadable, never guessed
            dcc._text_of(page(status="between"), "0000000000000000000000000000a001", "floating", router.HIRING_REGION)

    def test_the_region_chatgpt_writes_is_outside_the_proof_but_never_touched(self):
        t = page()
        t.trip = ("0000000000000000000000000000a001", lambda tree: tree.blocks["dcc-p"]["paragraph"].update({"rich_text": rich("chatgpt rewrote it")}))
        self.assertTrue(card.present(t, ENV, "x", WANT[:1], True)["verified"])
        self.assertFalse(any("dcc" in path for _, path in writes(t)))

    def test_a_failed_write_leaves_an_honest_degraded_heading_and_raises(self):
        t = page(extra_rows=1)
        t.fail_write = "0000000000000000000000000000a003-r1"
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, ENV, "Updated 2:00 PM CT", WANT, True)
        self.assertEqual(str(error.exception), "HIRING_WRITE_FAILED")
        self.assertEqual(R.plain(t.blocks["0000000000000000000000000000a001"]), "📈 Hiring Pipeline (DEGRADED)")

    def test_a_read_back_that_does_not_match_fails(self):
        t = page(extra_rows=1)
        original = t.call

        def lying(method, path, body=None):
            result = original(method, path, body)
            if method == "PATCH" and "0000000000000000000000000000a003-r1" in path:
                t.blocks["0000000000000000000000000000a003-r1"]["table_row"]["cells"][0][0]["plain_text"] = "something else"
            return result
        t.call = lying
        with self.assertRaises(snapshot.HiringError) as error:
            card.present(t, ENV, "x", WANT[:1] + [WANT[1]], True)
        self.assertEqual(str(error.exception), "HIRING_READBACK_MISMATCH")

    def test_unavailable_marks_the_heading_and_leaves_the_table_alone(self):
        t = page(extra_rows=2)
        rows_before = table_state(t)
        card.present(t, ENV, "DEGRADED · Hiring Pipeline unavailable at 2:00 PM CT", None, True)
        self.assertEqual(table_state(t), rows_before)
        self.assertEqual(R.plain(t.blocks["0000000000000000000000000000a001"]), "📈 Hiring Pipeline (DEGRADED at 2:00 PM CT)")


class ReciprocalProtectionTests(unittest.TestCase):
    def test_the_floating_region_is_one_router_owner_and_not_a_callout(self):
        self.assertEqual(router.OWNERS[router.HIRING_REGION], "v7-hiring")
        self.assertEqual(router.HIRING_REGION, M.TITLE)
        self.assertEqual(len(set(router.OWNERS.values())), len(router.OWNERS))
        self.assertFalse(router.is_callout(router.HIRING_REGION))
        self.assertTrue(all(router.is_callout(h) for h in router.OWNERS if h != router.HIRING_REGION))
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
            R.protected(t, {router.JIRA_REGION: "", router.HIRING_REGION: "0000000000000000000000000000a001"}, lambda code: RuntimeError(code))      # a missing mandatory id still fails

    def test_every_existing_card_that_proves_its_neighbours_also_proves_the_hiring_region(self):
        from lifeos.amazon import card as amazon
        from lifeos.attention import card as attention
        from lifeos.physmail import card as mail
        for module in (amazon, attention, mail):
            self.assertIn(("HIRING_CARD_BLOCK_ID", router.HIRING_REGION), module.PROTECTED, module.__name__)
        for module in ("lifeos/bills/card.py", "lifeos/agenda/card.py"):
            self.assertIn("HIRING_CARD_BLOCK_ID", (ROOT / module).read_text())


class FakeStore:
    def __init__(self, row=None, fail=False):
        self.row, self.saves, self.fail = row, 0, fail

    def ensure(self, connection):
        pass

    def load(self, connection, key):
        return copy.deepcopy(self.row)

    def save_verified(self, connection, key, snap, fail):
        if self.fail:
            raise fail("READBACK_MISMATCH")
        self.saves += 1
        self.row = copy.deepcopy(snap)


class FakeLedger:
    source = "src"

    def __init__(self, rows, broken=False):
        self.rows, self.broken = rows, broken

    def call(self, method, path, body=None):
        return {"properties": {name: {"type": kind} for name, kind in ledger.REQUIRED.items()}}

    def query_data_source(self, source_id=None, body=None):
        if self.broken:
            raise stage.NotionError("NOTION_HTTP_500")
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
RUN_ENV = {**ENV, "HIRING_PIPELINE_PAGE_ID": "root"}


def run(tree, store, live=True, rows=(("Acme", "Senior Program Manager", "2026-10-02"),), events=(), parents=PARENTS, ledger_broken=False, calendar_broken=False, pages_ok=True, now=NOW):
    pages = (lambda client, root: (list(parents), "ok")) if pages_ok else (lambda client, root: ([], "unreadable"))
    with mock.patch.object(snapshot, "STORE", store), mock.patch.object(stage.hiring_pipeline, "snapshot", pages):
        return stage.run(0, live, RUN_ENV, ledger_client=FakeLedger(list(rows), ledger_broken), pipeline_client=object(), calendar=FakeCalendar(events, calendar_broken),
                         connection=object(), client=tree, now=now)


class RuntimePathTests(unittest.TestCase):
    def test_entry_point_is_a_registered_stage_and_the_region_job_follows_every_other_writer(self):
        from lifeos import run as entry
        self.assertEqual(entry.STAGES["hiring"].target, ("lifeos.sources.hiring", "run"))
        import yaml
        doc = yaml.safe_load((ROOT / ".github/workflows/domains.yml").read_text())
        needs = set(doc["jobs"]["hiring"]["needs"])
        self.assertEqual(needs, set(doc["jobs"]) - {"hiring", "outlook", "megibow", "recruiters", "dcc", "report"})   # imessage writes the Communications and Oracle Cloud callouts (LIFE-34), so Hiring follows it too            # after every Daily Report writer, mail included (the failure-push job and the Recruiters ingest write nothing on the page)
        self.assertTrue({"jira", "agenda", "bills", "amazon", "attention", "mail"} <= needs)
        self.assertTrue({"jira", "agenda", "bills", "amazon", "attention", "mail", "hiring"} <= set(doc["jobs"]["dcc"]["needs"]))      # the Command Center runs after every writer, Hiring Pipeline included
        for name in ("outlook", "megibow", "recruiters"):                                          # the three that never write the Daily Report page
            self.assertNotIn("hiring", doc["jobs"][name].get("needs", []))
        step = next(s for s in doc["jobs"]["hiring"]["steps"] if "lifeos.run hiring" in s.get("run", ""))      # EDGE-1.3: a Bitwarden loader step precedes it
        self.assertIn("lifeos.run hiring", step["run"])
        self.assertTrue(step["continue-on-error"])
        self.assertIs(doc[True]["workflow_dispatch"]["inputs"]["hiring"]["default"], False)

    def test_dry_run_reads_everything_and_writes_nothing(self):
        t, store = page(extra_rows=1), FakeStore()
        counts = run(t, store, live=False, events=[gcal("e1", "Interview Acme", at(1))])
        self.assertEqual((writes(t), store.saves, counts["saved"], counts["blocks_written"]), ([], 0, 0, 0))
        self.assertEqual((counts["applied"], counts["opportunities"], counts["events"], counts["status"]), (1, 1, 1, "fresh"))

    def test_live_saves_with_read_back_writes_the_region_and_replays_idempotently(self):
        t, store = page(extra_rows=0), FakeStore()
        events = [gcal("e1", "Interview Acme", at(1, 10))]
        first = run(t, store, events=events)
        self.assertEqual((first["saved"], first["verified"], store.saves), (1, True, 1))
        self.assertEqual(table_state(t), [[("🟢 Acme — Senior Program Manager", None), ("Interviewing", None),
                                           ("Prepare for the interview · Tomorrow 10:00 AM CT", None), ("Opportunity Page ↗", "https://www.notion.so/aaaa1111000040008000000000000001")]])
        self.assertEqual(R.plain(t.blocks["0000000000000000000000000000a001"]), "📈 Hiring Pipeline (Updated 2:00 PM CT)")
        t.log.clear()
        again = run(t, store, events=events)
        self.assertEqual((again["rows_changed"], again["rows_added"], again["rows_removed"]), (0, 0, 0))
        self.assertEqual(len(table_state(t)), 1)
        self.assertEqual(json.loads(json.dumps(store.row))["rows"], store.row["rows"])
        self.assertEqual(store.row["accepted_at"], NOW.isoformat())

    def test_a_retired_page_drops_the_row_from_the_region_and_is_counted(self):
        t, store = page(extra_rows=1), FakeStore()
        retired = [hiring_pipeline.Opportunity("r", "Acme - Senior Program Manager", hiring_pipeline.RETIRED, 1, "Acme", "Senior Program Manager")]
        counts = run(t, store, parents=retired)
        self.assertEqual((counts["closed"], counts["retired_pages"], counts["opportunities"], counts["rows"]), (1, 1, 0, 0))
        self.assertEqual(table_state(t), [])
        self.assertIn("Updated", R.plain(t.blocks["0000000000000000000000000000a001"]))

    def test_a_failed_snapshot_save_stops_before_the_page_is_touched(self):
        t = page(extra_rows=1)
        with self.assertRaises(snapshot.HiringError) as error:
            run(t, FakeStore(fail=True))
        self.assertEqual(str(error.exception), "HIRING_READBACK_MISMATCH")
        self.assertEqual(writes(t), [])

    def test_an_unreadable_source_shows_degraded_with_the_last_accepted_state(self):
        t, store = page(extra_rows=0), FakeStore()
        run(t, store, events=[gcal("e1", "Interview Acme", at(1, 10))])
        for kwargs in ({"ledger_broken": True}, {"pages_ok": False}, {"calendar_broken": True}):
            counts = run(t, store, now=NOW + timedelta(hours=1), **kwargs)
            self.assertEqual(counts["status"], "degraded", kwargs)
            self.assertTrue(R.plain(t.blocks["0000000000000000000000000000a001"]).startswith("📈 Hiring Pipeline (DEGRADED at"), kwargs)
            self.assertIn("(last accepted)" if "calendar_broken" in kwargs else "Interviewing", " ".join(c[0] for c in table_state(t)[0]))
        self.assertEqual(store.row["accepted_at"], NOW.isoformat())                              # carried, never refreshed by a degraded run

    def test_no_accepted_state_and_an_unreadable_source_is_unavailable_not_empty(self):
        t, store = page(extra_rows=2), FakeStore()
        before = table_state(t)
        counts = run(t, store, ledger_broken=True)
        self.assertEqual(counts["status"], "unavailable")
        self.assertEqual((table_state(t), store.saves), (before, 0))
        self.assertIn("DEGRADED", R.plain(t.blocks["0000000000000000000000000000a001"]))

    def test_cancelled_declined_and_all_day_events_are_not_evidence(self):
        mine = [{"self": True, "responseStatus": "declined"}]
        events = [gcal("e1", "Interview Acme", at(1), status="cancelled"), gcal("e2", "Interview Acme", at(1), attendees=mine),
                  {"id": "e3", "summary": "Interview Acme", "start": {"date": "2026-10-08"}, "end": {"date": "2026-10-09"}}]
        t, store = page(), FakeStore()
        run(t, store, events=events)
        self.assertEqual(table_state(t)[0][1][0].split(" · ")[0], "Submitted")

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
        with mock.patch.object(stage.hiring_pipeline, "snapshot", lambda client, root: ([], "unreadable")):
            with self.assertRaises(stage.NotionError):
                stage.read_parents(object(), "root")
        retired = [hiring_pipeline.Opportunity("r", "Old - Role", hiring_pipeline.RETIRED, 1, "Old", "Role")]
        with mock.patch.object(stage.hiring_pipeline, "snapshot", lambda client, root: (retired + PARENTS, "ok")):
            self.assertEqual([(p["company"], p["retired"]) for p in stage.read_parents(object(), "root")], [("Old", True), ("Acme", False)])


def subprocess_diff_hourly():
    """hourly.yml must equal what main had before this slice: compare with the base commit when git can answer, else vacuously fine."""
    import subprocess
    try:
        base = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "-q", "d3585198370a4f55f3a48c03dc84e8288839c67a"], capture_output=True, text=True).stdout.strip()
        if not base:
            return ""
        return subprocess.run(["git", "-C", str(ROOT), "diff", base, "--", ".github/workflows/hourly.yml"], capture_output=True, text=True).stdout
    except OSError:
        return ""


class OutcomeConsumerTests(unittest.TestCase):
    """D190 end of the chain: what the outcome-mail stage stored reaches the Hiring Pipeline region as a tentative line, with every other block, the Applied state and the Ledger untouched."""
    KEY = "acme|manager|program|senior"
    FOUND = {KEY: {"kind": "REJECTED", "received": (NOW - timedelta(days=1)).astimezone(timezone.utc).isoformat(), "match": "role"}}

    def go(self, loader, parents=PARENTS):
        t, store = page(extra_rows=0), FakeStore()
        with mock.patch.object(stage.outcome_mail, "load", loader):
            counts = run(t, store, parents=parents)
        return t, store, counts

    def test_stored_outcome_mail_is_shown_as_a_line_to_confirm_and_only_the_pinned_region_blocks_are_written(self):
        t, store, counts = self.go(lambda conn: (self.FOUND, "2026-10-06T00:00:00+00:00"))
        self.assertEqual((counts["mail_outcomes"], counts["status"]), (1, "fresh"))
        action = table_state(t)[0][2][0]
        self.assertEqual(action, "Rejection email received Oct 5 · confirm, then move the Notes page to Retired")
        self.assertEqual(len(table_state(t)), 1)                                                   # still listed: V7 closes nothing
        self.assertEqual({entry[1].split("/")[2] for entry in writes(t)}, {"0000000000000000000000000000a001", "0000000000000000000000000000a003"})
        self.assertEqual(R.plain(t.blocks["cal-p"]), "9:00 AM standup")                           # the neighbouring card is untouched

    def test_a_role_without_a_notes_page_keeps_applied_as_history(self):
        t, _, _ = self.go(lambda conn: ({self.KEY: {**self.FOUND[self.KEY], "match": "company"}}, None), parents=[])
        action = table_state(t)[0][2][0]
        self.assertEqual(action, "Possible rejection email received Oct 5 · confirm (Applied stays as history)")

    def test_the_ledger_is_only_ever_read_and_the_applied_state_is_never_written(self):
        class ReadOnlyLedger(FakeLedger):
            def __getattr__(self, name):
                raise AssertionError(f"ledger write attempted: {name}")
        t, store = page(extra_rows=0), FakeStore()
        with mock.patch.object(stage.outcome_mail, "load", lambda conn: (self.FOUND, None)), mock.patch.object(snapshot, "STORE", store), \
                mock.patch.object(stage.hiring_pipeline, "snapshot", lambda client, root: (list(PARENTS), "ok")):
            stage.run(0, True, RUN_ENV, ledger_client=ReadOnlyLedger([("Acme", "Senior Program Manager", "2026-10-02")]), pipeline_client=object(), calendar=FakeCalendar([]),
                      connection=object(), client=t, now=NOW)
        self.assertEqual([e for e in t.log if "Applied" in json.dumps(e)], [])

    def test_unavailable_outcome_mail_never_stops_the_pipeline_or_invents_an_outcome(self):
        def broken(conn):
            raise RuntimeError("store down")
        t, store, counts = self.go(broken)
        self.assertEqual((counts["mail_outcomes"], counts["status"]), (0, "fresh"))
        self.assertNotIn("email", table_state(t)[0][2][0].lower())


class GuardrailTests(unittest.TestCase):
    SOURCES = ["lifeos/hiring/models.py", "lifeos/hiring/compile.py", "lifeos/hiring/render.py", "lifeos/hiring/snapshot.py", "lifeos/hiring/card.py", "lifeos/sources/hiring.py"]

    def test_there_is_no_mail_calendar_jira_ledger_or_tinyfish_mutation_path(self):
        for name in self.SOURCES:
            text = (ROOT / name).read_text().lower()
            for banned in ("tinyfish", "gmail", "outlook", ".insert(", "update_event", "delete_event", "send_message", "update_page_properties", ".create(", "lifeos.jira", "lifeos.interview"):
                self.assertNotIn(banned, text, f"{name}: {banned}")
        self.assertEqual(re.findall(r"client\.call(?:_once)?\(\"(?:POST|PUT)\"", (ROOT / "lifeos/sources/hiring.py").read_text()), [])

    def test_the_hiring_package_imports_platform_only_and_the_adapter_is_the_only_cross_domain_door(self):
        import ast
        for path in (ROOT / "lifeos/hiring").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                self.assertEqual([n for n in names if n.startswith("lifeos.") and n.split(".")[1] not in ("platform", "hiring")], [], path.name)

    def test_hourly_and_the_scheduler_are_untouched(self):
        hourly = (ROOT / ".github/workflows/hourly.yml").read_text()
        self.assertNotIn("lifeos.run hiring", hourly)
        self.assertNotIn("HIRING_CARD_BLOCK_ID", hourly)
        changed = [l for l in subprocess_diff_hourly().splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]   # context lines may name neighbouring keys
        self.assertNotIn("hiring", "\n".join(changed).lower())                      # later slices may change hourly.yml; the Hiring Pipeline never has
        domains = (ROOT / ".github/workflows/domains.yml").read_text()
        self.assertNotIn("schedule:", domains)
        self.assertNotIn("cron", domains)

    def test_no_private_identifier_is_committed(self):
        import hashlib
        forbidden = {"d1725f63cf1ac7162fd64889fb4f9853f23dfcaa3c74fddc03a6e0444b589c9c", "a26243ba801992915efcdb9597e9f90579c3940b288e9c7cd0d355e04cf47bb5"}                 # sha256 of the private block ids (the ids themselves never appear in the repository)
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
