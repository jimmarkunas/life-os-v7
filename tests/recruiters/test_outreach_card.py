"""LIFE-20: the Daily Report's one Recruiter Outreach table (lifeos/recruiters/outreach_card.py), against the synthetic block tree and a fake Recruiters data source.
Synthetic names and example.test links only."""
import unittest
from copy import deepcopy
from datetime import datetime, timezone

from lifeos import run as entry
from lifeos.platform import report_region as R, router
from lifeos.platform.notion_client import NotionError
from lifeos.recruiters import notion as store, outreach_card as card
from tests.kit.tree import Tree

HIRING_T = "0000000000000000000000000000a003"
ENV = {"HIRING_TABLE_BLOCK_ID": HIRING_T, "RECRUITER_OUTREACH": "on", "RECRUITERS_STAGE_OUTCOME": "success"}
NOW = datetime(2026, 10, 23, 14, 30, tzinfo=timezone.utc)                    # 9:30 AM CT, Friday Oct 23
OWN = ("ro-h", "ro-t")


def page(n, person, action="Needs Action", follow_up=None, last="2026-10-20T10:00:00Z", company="", role="", medium="Email", source="", contact="", done=False, active=True):
    text = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}] if v else []}
    return {"id": f"p{n}", "properties": {
        "Recruiter / Person": {"type": "title", "title": [{"plain_text": person}]}, "Recruiter Company": text(company), "Client": text(""), "Role": text(role),
        "Medium": text(medium), "What they want": text(""), "Last Contact": {"date": {"start": last}}, "Week Ending": {"date": {"start": "2026-10-25"}},
        "Done": {"checkbox": done}, "Active": {"checkbox": active}, "Needs review": {"checkbox": False}, "Source URL": {"url": source or None},
        "Action Status": {"select": {"name": action} if action else None}, "Follow-up Date": {"date": {"start": follow_up} if follow_up else None},
        "Job": {"relation": []}, "Contact": {"type": "formula", "formula": {"type": "string", "string": contact}}}}


class Source:
    """The Recruiters data source: its schema and one filtered query, in pages of `size`."""
    source = "src"

    def __init__(self, pages, size=100, fail=False, broken=False):
        self.pages, self.size, self.fail, self.broken, self.bodies = pages, size, fail, broken, []

    def call(self, method, path, body=None):
        assert (method, path) == ("GET", "/data_sources/src"), (method, path)
        return {"properties": {name: {"type": kind} for name, kind in store.SCHEMA.items()}}

    def query_data_source(self, source_id=None, body=None):
        self.bodies.append(deepcopy(body))
        if self.fail:
            raise NotionError("NOTION_HTTP_503")
        start = int(body.get("start_cursor") or 0)
        chunk = self.pages[start:start + self.size]
        more = start + self.size < len(self.pages)
        if self.broken and more:
            return {"results": chunk, "has_more": True, "next_cursor": None}
        return {"results": chunk, "has_more": more, "next_cursor": str(start + self.size) if more else None}


def report(old_rows=1, heading="🤝 Recruiter Outreach (Updated xxx)", headers=card.HEADERS, place="after_hiring", second_table=False, duplicate=False):
    """The right column of the Daily Report: the Hiring Pipeline, the Recruiter Outreach heading, its table and the records link, then the Jira callout;
    the DCC callout above, and the Historical Snapshot toggle at the bottom of the page."""
    t = Tree()
    t.add("dcc", "callout", "page-1")
    t.add("dcc-h", "heading_3", "dcc", text=router.DCC_REGION)
    t.add("col", "column", "page-1")
    t.add("0000000000000000000000000000a001", "heading_3", "col", text="📈 Hiring Pipeline (Updated 9:22 AM CT)")
    t.table(HIRING_T, "col", [["Company — Role", "Stage", "Next step · Due", "Notes"], ["Acme — PM", "Interviewing", "Prepare", "Notes"]])
    if place == "after_hiring":
        t.add("ro-h", "heading_3", "col", text=heading)
    else:
        t.add("gap", "paragraph", "col", text="something in between")
        t.add("ro-h", "heading_3", "col", text=heading)
    t.table("ro-t", "col", [list(headers)] + [[f"old {n}", "x", "y", "z"] for n in range(old_rows)])
    if second_table:
        t.table("ro-t2", "col", [list(headers)])
    t.add("ro-link", "paragraph", "col", text="All recruiter records ↗")
    t.add("jira", "callout", "col")
    t.add("jira-h", "heading_4", "jira", text="JIRA Execution (Updated 9:12 AM CT)")
    t.add("jira-p", "paragraph", "jira", text="3 open")
    if duplicate:
        t.add("ro-h2", "heading_3", "page-1", text="🤝 Recruiter Outreach")
    t.add("hist", "heading_3", "page-1", text="Historical Snapshot", is_toggleable=True)
    t.table("hist-t", "hist", [["Person — Employer", "Channel · Role", "Initial outreach · Conversation", "Next step · Due"], ["Old Person", "Gmail", "Oct 15", "Review"]])
    return t


def writes(tree):
    return [e for e in tree.log if e[0] != "GET"]


def outside(tree):
    """Every block that is not the owned heading, the owned table or one of its rows."""
    return {k: v for k, v in tree.snapshot().items() if k not in OWN and v["parent"].get("block_id") != "ro-t"}


def shown(tree):
    """The data rows as [[(text, link, style)] per cell]."""
    return [R.live_view(tree.blocks[i]) for i in tree.kids["ro-t"][1:]]


def go(tree, source, env=ENV, live=True, now=NOW):
    return card.run(0, live, environ=env, notion=source, client=tree, now=now)


MIXED = [page(1, "Wanda Waiting", "Waiting", follow_up="2026-10-27", company="Beta Staffing", role="TPM", source="https://mail.google.com/mail/u/0/#all/b1"),
         page(2, "Nora Nodue", company="Gamma Inc", role="PM", source="https://mail.google.com/mail/u/0/#all/c1"),
         page(3, "Oscar Overdue", follow_up="2026-10-21", company="Delta", role="Director", medium="LinkedIn", contact="https://www.linkedin.com/in/oscar-example"),
         page(4, "Tess Today", follow_up="2026-10-23", source="https://outlook.office.com/mail/id/t1", medium="Email"),
         page(5, "Carl Completed", "Completed", follow_up="2026-10-01"),
         page(6, "Una Unclassified", None),
         page(7, "Dora Done", follow_up="2026-10-01", done=True),
         page(8, "Ivan Inactive", follow_up="2026-10-01", active=False),
         page(9, "Will Waiting-Nodue", "Waiting", last="2026-10-22T10:00:00Z")]


class RenderTests(unittest.TestCase):
    def test_the_four_exact_headers_are_kept_and_one_table_is_written(self):
        t = report()
        done = go(t, Source(MIXED))
        self.assertTrue(done["verified"])
        self.assertEqual(R.cells_of(t.blocks[t.kids["ro-t"][0]]), list(card.HEADERS))
        self.assertEqual(card.HEADERS, ("Recruiter · Employer", "Channel · Role", "Original Contact", "Next Step · Due"))
        self.assertEqual([b for b in t.kids["col"] if t.blocks[b]["type"] == "table"], [HIRING_T, "ro-t"])                     # no second table, no view
        self.assertEqual(R.plain(t.blocks["ro-h"]), "🤝 Recruiter Outreach (Updated 9:30 AM CT)")

    def test_each_cell_is_a_bold_first_line_and_a_gray_second_line_with_only_recorded_links(self):
        t = report()
        go(t, Source(MIXED))
        rows = {r[0][0][0]: r for r in shown(t)}
        self.assertEqual(rows["Nora Nodue"], [[("Nora Nodue", "https://mail.google.com/mail/u/0/#all/c1", "bold"), ("\nGamma Inc", None, "gray")],
                                              [("Gmail ↗", "https://mail.google.com/mail/u/0/#all/c1", "bold"), ("\nPM", None, "gray")],
                                              [("Not verified", None, None)],
                                              [("Needs Action", None, "bold"), ("\nDue date not set", None, "gray")]])
        oscar = rows["Oscar Overdue"]                                                       # no message link: the name links to the database's Contact link, the channel is plain
        self.assertEqual(oscar[0][0], ("Oscar Overdue", "https://www.linkedin.com/in/oscar-example", "bold"))
        self.assertEqual(oscar[1], [("LinkedIn", None, "bold"), ("\nDirector", None, "gray")])
        tess = rows["Tess Today"]
        self.assertEqual(tess[1][0], ("Outlook ↗", "https://outlook.office.com/mail/id/t1", "bold"))
        self.assertEqual(tess[0][1], ("\nCompany not recorded", None, "gray"))
        self.assertEqual(tess[1][1], ("\nRole not recorded", None, "gray"))
        will = rows["Will Waiting-Nodue"]                                                   # nothing recorded: nothing linked, nothing invented
        self.assertEqual(will[0][0], ("Will Waiting-Nodue", None, "bold"))
        self.assertEqual(will[1][0], ("Email", None, "bold"))

    def test_a_link_that_is_not_https_is_never_used(self):
        row = store.read(page(1, "Pat", contact="javascript:alert(1)", source="http://mail.example.test/x"))
        got = card.cells(row, NOW.date())
        self.assertEqual(got[0][0], ("Pat", None, "bold"))
        self.assertEqual(got[1][0], ("Email", None, "bold"))

    def test_original_contact_is_never_inferred_from_last_contact(self):
        t = report()
        go(t, Source(MIXED))
        self.assertEqual({r[2][0][0] for r in shown(t)}, {"Not verified"})                  # the source has no first-contact date; Last Contact is the latest message

    def test_status_and_due_date_mapping(self):
        today = NOW.date()
        due = lambda **kw: card.cells(store.read(page(1, "P", **kw)), today)[3]
        self.assertEqual(due(follow_up="2026-10-21"), [("Needs Action", None, "bold"), ("\nOct 21, 2026 · overdue", None, "gray")])
        self.assertEqual(due(follow_up="2026-10-23")[1][0], "\nOct 23, 2026 · due today")
        self.assertEqual(due(action="Waiting", follow_up="2026-10-27"), [("Waiting", None, "bold"), ("\nOct 27, 2026", None, "gray")])
        self.assertEqual(due(follow_up=None)[1][0], "\nDue date not set")
        self.assertEqual(due(follow_up="not-a-date")[1][0], "\nDue date not set")

    def test_needs_action_comes_before_waiting_by_due_date_and_nothing_else_is_shown(self):
        t = report()
        done = go(t, Source(MIXED))
        self.assertEqual([r[0][0][0] for r in shown(t)], ["Oscar Overdue", "Tess Today", "Nora Nodue", "Wanda Waiting", "Will Waiting-Nodue"])
        self.assertEqual((done["rows"], done["needs_action"], done["waiting"]), (5, 3, 2))  # Completed, blank, Done and inactive rows are not action items

    def test_the_source_is_read_completely_with_one_filter_for_the_two_queues(self):
        source = Source(MIXED, size=2)
        t = report()
        go(t, source)
        self.assertEqual(len(source.bodies), 5)
        self.assertEqual(source.bodies[0]["filter"], {"or": [{"property": "Action Status", "select": {"equals": "Needs Action"}}, {"property": "Action Status", "select": {"equals": "Waiting"}}]})
        self.assertEqual(len(shown(t)), 5)


class ReplayTests(unittest.TestCase):
    def test_a_replay_writes_nothing_and_rows_are_never_duplicated_or_lost(self):
        t = report(old_rows=7)
        first = go(t, Source(MIXED))
        self.assertEqual((first["rows_changed"], first["rows_added"], first["rows_removed"]), (5, 0, 2))
        before, log = deepcopy(t.blocks), len(t.log)
        second = go(t, Source(MIXED))
        self.assertEqual([e for e in t.log[log:] if e[0] != "GET"], [])
        self.assertEqual(t.blocks, before)
        self.assertTrue(second["verified"])
        self.assertEqual((second["rows_changed"], second["rows_added"], second["rows_removed"]), (0, 0, 0))
        names = [r[0][0][0] for r in shown(t)]
        self.assertEqual(len(names), len(set(names)))

    def test_more_rows_than_the_table_has_are_appended_in_order(self):
        t = report(old_rows=1)
        done = go(t, Source(MIXED))
        self.assertEqual((done["rows_changed"], done["rows_added"], done["rows_removed"]), (1, 4, 0))
        self.assertEqual(len(t.kids["ro-t"]), 6)

    def test_an_empty_queue_keeps_only_the_header_row(self):
        t = report(old_rows=3)
        done = go(t, Source([page(1, "Carl", "Completed")]))
        self.assertEqual(t.kids["ro-t"], ["ro-t-r0"])
        self.assertEqual((done["rows"], done["rows_removed"], done["verified"]), (0, 3, True))


class DegradedTests(unittest.TestCase):
    def degraded(self, tree, source, env=ENV, code="SOURCE_UNREADABLE"):
        rows = shown(tree)
        with self.assertRaises(card.OutreachError) as caught:
            go(tree, source, env=env)
        self.assertEqual(str(caught.exception), f"RECRUITER_OUTREACH_DEGRADED:{code}")
        self.assertEqual(shown(tree), rows)                                                    # the last accepted rows stay
        self.assertEqual(R.plain(tree.blocks["ro-h"]), "🤝 Recruiter Outreach (DEGRADED at 9:30 AM CT)")

    def test_an_unreadable_source_keeps_the_rows_and_says_degraded(self):
        self.degraded(report(old_rows=2), Source(MIXED, fail=True))

    def test_a_truncated_read_is_degraded_never_a_shorter_table(self):
        self.degraded(report(old_rows=2), Source(MIXED, size=2, broken=True))

    def test_a_failed_recruiters_stage_this_run_is_degraded(self):
        self.degraded(report(old_rows=2), Source(MIXED), env={**ENV, "RECRUITERS_STAGE_OUTCOME": "failure"}, code="RECRUITERS_STAGE_FAILED")

    def test_a_page_read_twice_is_degraded(self):
        self.degraded(report(old_rows=2), Source(MIXED + [MIXED[1]]))

    def test_too_many_rows_is_degraded_never_truncated(self):
        self.degraded(report(), Source([page(n, f"P{n}") for n in range(card.MAX_ROWS + 1)]), code="SOURCE_TOO_MANY")


class RegionTests(unittest.TestCase):
    def refused(self, tree, code, env=ENV):
        before = deepcopy(tree.blocks)
        with self.assertRaises(card.OutreachError) as caught:
            go(tree, Source(MIXED), env=env)
        self.assertEqual(str(caught.exception), f"RECRUITER_OUTREACH_{code}")
        self.assertEqual(writes(tree), [])
        self.assertEqual(tree.blocks, before)

    def test_changed_headers_write_nothing(self):
        self.refused(report(headers=("Person — Employer", "Channel · Role", "Initial outreach · Conversation", "Next step · Due")), "HEADERS_CHANGED")

    def test_the_heading_must_sit_right_under_the_hiring_pipeline_table(self):
        self.refused(report(place="moved"), "HEADING_MISSING")
        self.refused(report(heading="Recruiters"), "HEADING_MISSING")

    def test_a_second_table_or_a_second_heading_writes_nothing(self):
        self.refused(report(second_table=True), "SHAPE_INVALID")
        self.refused(report(duplicate=True), "HEADING_NOT_UNIQUE")

    def test_an_unpinned_or_malformed_anchor_writes_nothing(self):
        self.refused(report(), "NOT_CONFIGURED", env={**ENV, "HIRING_TABLE_BLOCK_ID": ""})
        self.refused(report(), "ID_INVALID:HIRING_TABLE_BLOCK_ID:len6", env={**ENV, "HIRING_TABLE_BLOCK_ID": "abc-def"})

    def test_dry_run_or_switch_off_validates_and_writes_nothing(self):
        for live, env in ((False, ENV), (True, {**ENV, "RECRUITER_OUTREACH": ""})):
            t = report(old_rows=1)
            done = go(t, Source(MIXED), env=env, live=live)
            self.assertEqual(writes(t), [])
            self.assertEqual((done["live"], done["would_change"], done["verified"]), (False, 5, False))


class ProtectionTests(unittest.TestCase):
    def test_the_historical_snapshot_hiring_pipeline_jira_and_link_are_untouched(self):
        t = report(old_rows=2)
        before = outside(t)
        go(t, Source(MIXED))
        self.assertEqual(outside(t), before)
        self.assertEqual(t.kids["col"], ["0000000000000000000000000000a001", HIRING_T, "ro-h", "ro-t", "ro-link", "jira"])
        touched = {path.split("/")[2] for method, path in writes(t)}
        self.assertLessEqual(touched, {"ro-h", "ro-t"} | {i for i in t.kids["ro-t"]} | {f"ro-t-r{n}" for n in range(3)})

    def test_a_neighbour_changed_during_the_write_fails_closed(self):
        t = report(old_rows=2)
        t.trip = ("ro-t-r1", lambda tree: tree.blocks[f"{HIRING_T}-r1"]["table_row"]["cells"][1][0].update(plain_text="Rejected"))
        with self.assertRaises(card.OutreachError) as caught:
            go(t, Source(MIXED))
        self.assertEqual(str(caught.exception), "RECRUITER_OUTREACH_PROTECTED_REGION_CHANGED")

    def test_a_failed_write_marks_the_heading_degraded(self):
        t = report(old_rows=2)
        t.fail_write = "ro-t-r1"
        with self.assertRaises(card.OutreachError) as caught:
            go(t, Source(MIXED))
        self.assertEqual(str(caught.exception), "RECRUITER_OUTREACH_WRITE_FAILED")
        self.assertEqual(R.plain(t.blocks["ro-h"]), "🤝 Recruiter Outreach (DEGRADED)")

    def test_the_region_has_one_owner_and_is_not_a_callout(self):
        self.assertEqual(router.OWNERS[card.TITLE], "v7-recruiter-outreach")
        self.assertIn(card.TITLE, router.FLOATING)
        router.check_write("v7-recruiter-outreach", [card.TITLE])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-hiring", [card.TITLE])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-recruiter-outreach", [router.HIRING_REGION])

    def test_the_stage_is_registered(self):
        self.assertEqual(entry.STAGES["recruiter-outreach"].target, ("lifeos.recruiters.outreach_card", "run"))


if __name__ == "__main__":
    unittest.main()
