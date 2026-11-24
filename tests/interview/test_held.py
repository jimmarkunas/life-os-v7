"""LIFE-958: a Held round never asks to verify scheduling; a recruiter screen is its own round type; a retired opportunity's rounds are Retired."""
import unittest

from lifeos.interview import held, notion, roster
from lifeos.interview.models import Child, Parent
from lifeos.interview.template import VERIFY_SCHEDULING
from lifeos.platform.runtime import RunContext
from tests.interview.test_roster import DB, ENVIRON, SCHEMA, SOURCE, Fake as ListFake, cell, text


def run_roster(client, rounds, live=True):
    counts = {"why": {}}
    code = roster.sync(client, ENVIRON, rounds, live, RunContext.start(60), counts, stamp="Nov 24, 2026, 9:00 AM CT", today="2026-11-24")
    return code, counts


def next_action(value):
    return {"id": "next", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "plain_text": "Next action: ", "text": {"content": "Next action: "}, "annotations": {"bold": True}},
                                                                     {"type": "text", "plain_text": value, "text": {"content": value}}]}}


class Pages(ListFake):
    """The Interviews list plus round pages that accept a block text edit."""
    def __init__(self, snapshot, after=(), **kwargs):
        super().__init__(**kwargs)
        self.blocks["snap"] = snapshot
        self.blocks["uber"] = [self.blocks["uber"][0], self.blocks["uber"][1], {"id": "cols", "type": "column_list", "has_children": True}, *self.blocks["uber"][2:]]
        self.blocks["cols"] = [{"id": "left", "type": "column", "has_children": True}, {"id": "col", "type": "column", "has_children": True}]
        self.blocks["left"] = [{"id": "tab", "type": "tab", "has_children": True}, text(notion.RAW_HEADING, "heading_3", "rawh"), text("Raw notes · open to write", "toggle", "rawt")]
        self.blocks["tab"] = [text(notion.LIVE_PANE, "paragraph", "live")]
        self.blocks["col"] = list(after)
        self.edits = []

    def call(self, method, path, body=None):
        if method == "PATCH" and path.startswith("/blocks/"):
            self.calls.append((method, path))
            block_id = path.split("/")[2]
            for blocks in self.blocks.values():
                for block in blocks:
                    if block["id"] == block_id:
                        kind = block["type"]
                        block[kind]["rich_text"] = [{**run, "plain_text": run["text"]["content"]} for run in body[kind]["rich_text"]]
                        self.edits.append((block_id, notion.text(block)))
            return {}
        return super().call(method, path, body)


HELD = text("Interviewer: Bethany Papenbrock, Hannah Hinz (from the old notes page) · Status: Held 2026-09-29 · Calendar not verified")
WHEN = text("When: 2026-09-29, 11:00–11:30 AM CT (from the old notes page) · Calendar event not verified")
UBER = [(Parent("p1", "Uber - Technical Project Manager"), Child("uber", "p1", "2026-09-29", None, 1, True))]
FOLLOW = [text("Recommended Follow-Up", "heading_3", "fh"), text("Action: Send Hannah a short note offering two times for the coordination call"), text("Timing: this week")]


class HeldNextAction(unittest.TestCase):
    def test_a_held_round_loses_the_verify_sentence_and_takes_the_recommended_follow_up(self):
        client = Pages([HELD, WHEN, next_action(VERIFY_SCHEDULING)], FOLLOW)
        code, counts = run_roster(client, UBER)
        self.assertEqual((code, counts["held_repaired"]), ("roster_synced", 1))
        self.assertEqual(client.edits, [("next", "Next action: Send Hannah a short note offering two times for the coordination call")])
        self.assertEqual(client.blocks["snap"][2]["paragraph"]["rich_text"][0]["annotations"], {"bold": True})            # the bold label is kept

    def test_no_follow_up_reads_none_and_the_owed_line_counts_only_when_it_holds_a_value(self):
        for after, want in (([], "None"), ([text("Follow-up owed / due: Unverified; no task or deadline is created from this page.")], "None"),
                            ([text("Follow-up owed / due: Thank-you note to Hannah by Friday")], "Thank-you note to Hannah by Friday")):
            client = Pages([HELD, WHEN, next_action(VERIFY_SCHEDULING)], after)
            run_roster(client, UBER)
            self.assertEqual(client.edits, [("next", f"Next action: {want}")])

    def test_the_next_run_writes_nothing_and_a_line_in_a_person_s_words_is_never_touched(self):
        client = Pages([HELD, WHEN, next_action(VERIFY_SCHEDULING)], FOLLOW)
        run_roster(client, UBER)
        before = len(client.edits)
        code, counts = run_roster(client, UBER)
        self.assertEqual((len(client.edits), counts.get("held_repaired", 0)), (before, 0))
        mine = Pages([HELD, WHEN, next_action("Call Hannah on Monday")], FOLLOW)
        run_roster(mine, UBER)
        self.assertEqual(mine.edits, [])

    def test_a_round_that_is_not_held_keeps_the_sentence_and_a_dry_run_writes_nothing(self):
        client = Pages([text("Interviewer: Not verified · Status: Scheduled 2026-12-01 · Calendar not verified"), WHEN, next_action(VERIFY_SCHEDULING)], FOLLOW)
        run_roster(client, [(UBER[0][0], Child("uber", "p1", "2026-12-01", None, 1, True))])
        self.assertEqual(client.edits, [])
        dry = Pages([HELD, WHEN, next_action(VERIFY_SCHEDULING)], FOLLOW)
        code, counts = run_roster(dry, UBER, live=False)
        self.assertEqual((dry.edits, counts["held_planned"], counts.get("held_repaired", 0)), ([], 1, 0))

    def test_the_writer_and_the_repair_share_one_sentence(self):
        from lifeos.interview import template
        self.assertEqual(held.NEXT + template.VERIFY_SCHEDULING, "Next action: Verify scheduling and interview details before treating this round as confirmed.")


class RecruiterScreen(unittest.TestCase):
    SNAP = [text("Interviewer: Mike, account director, staffing firm · Status: Held 2026-10-13 · Calendar not verified"), text("When: 2026-10-13"),
            text("Round type: Recruiter screen (staffing firm)")]
    ROUND = [(Parent("p1", "Becton Dickinson - Senior Technical Program Manager"), Child("uber", "p1", "2026-10-13", "Mike, account director, staffing firm", None, True))]

    def test_the_marker_line_titles_the_row_as_a_recruiter_screen_with_no_round_number(self):
        client = Pages(self.SNAP)
        code, counts = run_roster(client, self.ROUND)
        row = client.rows[0]
        self.assertEqual((code, cell(row, "Interview"), cell(row, "Round"), cell(row, "Status"), cell(row, "Interviewer")),
                         ("roster_synced", "Becton Dickinson · Recruiter screen (staffing firm)", None, "Held", "Mike, account director, staffing firm"))

    def test_a_screen_keeps_its_title_on_every_run_and_still_with_an_ordinal_in_its_identity(self):
        client = Pages(self.SNAP)
        run_roster(client, [(self.ROUND[0][0], Child("uber", "p1", "2026-10-13", "Mike, account director, staffing firm", 1, True))])
        code, counts = run_roster(client, [(self.ROUND[0][0], Child("uber", "p1", "2026-10-13", "Mike, account director, staffing firm", 1, True))])
        self.assertEqual((cell(client.rows[0], "Interview"), cell(client.rows[0], "Round"), counts.get("roster_updated", 0)), ("Becton Dickinson · Recruiter screen (staffing firm)", None, 0))

    def test_a_round_without_the_marker_is_still_a_numbered_round(self):
        client = Pages(self.SNAP[:2])
        run_roster(client, [(self.ROUND[0][0], Child("uber", "p1", "2026-10-13", None, 1, True))])
        self.assertEqual((cell(client.rows[0], "Interview"), cell(client.rows[0], "Round")), ("Becton Dickinson · Interview 1", 1))


class RetiredOpportunity(unittest.TestCase):
    def test_a_round_of_an_inactive_opportunity_is_retired_and_returns_when_it_is_active_again(self):
        client = Pages([HELD, WHEN])
        retired = [(Parent("p1", "Uber - Technical Project Manager", False), UBER[0][1])]
        run_roster(client, retired)
        self.assertEqual(cell(client.rows[0], "Status"), "Retired")
        self.assertEqual(client.edits, [])                                                       # a retired opportunity's page is not repaired
        run_roster(client, UBER)
        self.assertEqual(cell(client.rows[0], "Status"), "Held")

    def test_a_retired_round_with_no_status_text_is_retired_too(self):
        client = Pages([text("Interviewer: Not verified · Status: Scheduling Pending"), text("When: No verified Calendar event or meeting time")])
        run_roster(client, [(Parent("p1", "Walmart - Senior Manager", False, "Interviewing"), Child("uber", "p1", None, None, 1, True))])
        self.assertEqual(cell(client.rows[0], "Status"), "Retired")


if __name__ == "__main__":
    unittest.main()
