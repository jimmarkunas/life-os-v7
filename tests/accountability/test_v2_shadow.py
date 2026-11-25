"""LIFE-918 AC4/AC5 (synthetic, no network, no provider write): the v2 quarterly preview inside the existing Quarterly Goals & Review blocks (Jim, comment 14216: no separate block).

Off by default and exactly the old two-column table when off; on, the table is upgraded in place to Lane, Progress, Done / all, New count (preview), Status; JFM and Matt alike show NEEDS Q1 MEMBER LIST
with the shipped empty membership, the v2 count with an approved fixture list, DATA MISSING when the list and Jira disagree; legacy figures stay official; the second run writes nothing."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lifeos.accountability import common, goals, index, preview, read, region, render
from lifeos.platform.jira import JiraError
from tests.accountability import fakes as F
from tests.accountability.treefake import TreeNotion

NOW = F.NOW
ON = {"V2_QUARTER_SHADOW": "on", "JIRA_SITE_URL": "https://jira.example.com"}
EPICS = [F.epic("JFM-9", "Career Q4 2026"), F.epic("MEJ-2", "Creative Q4 2026")]


def approved(tasks, ref="SYNTHETIC-TEST-ONLY"):
    return {"schema_version": 1, "quarters": {"2026-q4": {"approved": True, "baseline_id": "SYNTHETIC-Q4", "approval_ref": ref, "tasks": tasks}}}


def approved_both(mej, jfm, ref="SYNTHETIC-TEST-ONLY"):
    return {"schema_version": 1, "quarters": {"2026-q4": {"boards": {name: {"approved": True, "baseline_id": "SYNTHETIC-Q4", "approval_ref": ref, "tasks": tasks} for name, tasks in (("MEJ", mej), ("JFM", jfm))}}}}


def kid(key, parent, done=False, kind="Task"):
    return {"key": key, "fields": {"parent": {"key": parent}, "issuetype": {"name": kind, "subtask": kind == "Sub-task"},
                                   "status": {"id": "10015" if done else "10014", "name": "Done" if done else "To Do", "statusCategory": {"key": "done" if done else "new"}},
                                   "resolutiondate": "2026-11-10T10:00:00.000-0600" if done else None}}


class Jira:
    """JQL answered from fixtures; a status change into Done on 2026-11-10 for every done Task."""

    def __init__(self, kids):
        self.kids, self.queries = kids, []

    def get(self, path, params=None):
        if path.endswith("/changelog"):
            return {"values": [{"created": "2026-11-10T10:00:00.000-0600", "items": [{"field": "status", "fieldId": "status", "toString": "Done", "to": "10015"}]}], "isLast": True, "total": 1}
        jql = params["jql"]
        self.queries.append(jql)
        if "issuetype = Epic AND labels" in jql and "mej-project" not in jql:
            return {"issues": EPICS, "isLast": True}
        if "mej-project" in jql:
            return {"issues": [], "isLast": True}
        if "parent in (" in jql:
            parents = set(jql.split("parent in (", 1)[1].split(")", 1)[0].split(","))
            return {"issues": [k for k in self.kids if k["fields"]["parent"]["key"] in parents], "isLast": True}
        return {"issues": [], "isLast": True}


def board():
    """Two Quarterly Goals & Review callouts as they stand today: the two-column table (Lane, Done / all), the note and the Quarterly Reviews toggle, plus a fixed block that must never be written."""
    notion = TreeNotion()
    for who, lane in (("jim", "Career"), ("matt", "Creative")):
        notion.callout(f"b-{who}-goals", f"Quarterly Goals & Review — {who}", "Not updated yet", [notion.table(f"t-{who}", [(lane, "–")]), F.para(f"note-{who}", "old note"), notion.toggle(f"tg-{who}", "x")], f"pg-{who}", legacy=False)
        notion.blocks[f"tg-{who}"]["toggle"]["rich_text"] = F.rich("Quarterly Reviews")
    notion.callout("fixed", "Fixed block", "Not updated yet", [F.para("fx-body", "keep me")], "pg-fixed")
    return notion


def run_goals(kids, environ=ON, live=True, notion=None, membership=None):
    notion = notion or board()
    jira = Jira(kids)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(region, "address", lambda _n, key: f"b-{key.split('.')[0]}-goals"))
        stack.enter_context(mock.patch.object(index, "reviews", lambda _n, person: [("Q4 2026 (current)", "https://example.invalid/q4")]))
        if membership is not None:
            path = Path(tempfile.mkdtemp()) / "m.json"
            path.write_text(json.dumps(membership))
            stack.enter_context(mock.patch.object(read, "MEJ_MEMBERSHIP", path))
        out = goals.run(0, live, environ=environ, jira=jira, notion=notion, now=NOW)
    return out, notion, jira


def table(notion, who):
    """[[cell text]] of the person's table rows, header first."""
    block = next(k for k in notion.kids[f"b-{who}-goals"] if notion.blocks[k]["type"] == "table")
    return [["".join(p["plain_text"] for p in cell) for cell in notion.blocks[row]["table_row"]["cells"]] for row in notion.kids[block]]


class Switches(unittest.TestCase):
    def test_off_by_default_is_exactly_the_stage_as_it_was(self):
        for environ in ({}, {"V2_QUARTER_SHADOW": "off"}, {"V2_QUARTER_SHADOW": "true"}):
            with mock.patch.object(common, "run_region", return_value={"jim": {"epics": 1}}) as base:
                out = goals.run(0, False, environ=environ, jira="J", notion="N", now=NOW)
            self.assertEqual(out, {"jim": {"epics": 1}})
            base.assert_called_once_with("goals", goals._build, environ, "J", "N", NOW, False)

    def test_on_uses_the_shadow_builder_and_adds_nothing_else(self):
        with mock.patch.object(common, "run_region", return_value={"jim": {"epics": 1}}) as base:
            out = goals.run(0, True, environ=ON, jira="J", notion="N", now=NOW)
        self.assertEqual(out, {"jim": {"epics": 1}})
        base.assert_called_once_with("goals", goals._build_shadow, ON, "J", "N", NOW, True)

    def test_off_keeps_the_two_column_table(self):
        out, notion, _ = run_goals([kid("JFM-20", "JFM-9", True)], environ={"JIRA_SITE_URL": "https://jira.example.com"})
        self.assertEqual(table(notion, "jim"), [["Lane", "Done / all"], ["Career", "1 / 1"]])
        self.assertEqual(notion.blocks["t-jim"]["table"]["table_width"], 2)

    def test_the_old_preview_block_row_is_gone(self):
        from lifeos.accountability import switches
        self.assertFalse(hasattr(switches, "preview_block"))
        self.assertNotIn("preview_block", switches.snapshot({}))


class Table(unittest.TestCase):
    def test_on_upgrades_the_table_in_place_and_jfm_needs_its_list_like_matt(self):
        out, notion, jira = run_goals([kid("JFM-20", "JFM-9", True), kid("JFM-21", "JFM-9")])
        self.assertEqual(table(notion, "jim"), [["Lane", "Progress", "Done / all", "New count (preview)", "Status"], ["Career", "██████████", "1 / 2", "—", "NEEDS Q1 MEMBER LIST"]])
        order = notion.kids["b-jim-goals"]
        self.assertEqual([notion.blocks[k]["type"] for k in order[2:]], ["table", "paragraph", "toggle"])               # lanes, note and toggle kept; no second block anywhere
        self.assertEqual(notion.text("b-jim-goals-h"), "Quarterly Goals & Review — Jim · Q4 2026")
        self.assertEqual(notion.blocks[order[2]]["table"]["table_width"], 5)
        self.assertEqual(notion.text("fx-body"), "keep me")
        self.assertEqual(notion.text("fixed-st"), "Not updated yet")
        link = notion.blocks[notion.kids[order[2]][1]]["table_row"]["cells"][0][0]["href"]
        self.assertEqual(link, "https://jira.example.com/browse/JFM-9")                                                  # the lane stays linked to its Epic

    def test_both_blocks_render_the_same_columns_and_jfm_shows_its_real_v2_count_with_an_approved_list(self):
        frozen = approved_both({"MEJ-20": {"parent": "MEJ-2", "goal": "MEJ-2"}}, {"JFM-20": {"parent": "JFM-9", "goal": "JFM-9"}, "JFM-21": {"parent": "JFM-9", "goal": "JFM-9"}})
        _, notion, _ = run_goals([kid("JFM-20", "JFM-9", True), kid("JFM-21", "JFM-9"), kid("JFM-22", "JFM-9"), kid("MEJ-20", "MEJ-2", True)], membership=frozen)
        jim, matt = table(notion, "jim"), table(notion, "matt")
        self.assertEqual(jim[0], matt[0])
        self.assertEqual(jim[1], ["Career", "██████████", "1 / 3", "1 / 2", "OK"])
        self.assertEqual(matt[1][3:], ["1 / 1", "OK"])

    def test_jfm_without_its_own_list_reads_needs_q1_member_list_in_red_even_when_mej_is_approved(self):
        frozen = approved({"MEJ-20": {"parent": "MEJ-2", "goal": "MEJ-2"}})
        _, notion, _ = run_goals([kid("JFM-20", "JFM-9", True), kid("MEJ-20", "MEJ-2", True)], membership=frozen)
        self.assertEqual(table(notion, "jim")[1][3:], ["—", "NEEDS Q1 MEMBER LIST"])
        self.assertEqual(table(notion, "matt")[1][3:], ["1 / 1", "OK"])

    def test_matt_needs_the_q1_member_list_while_none_is_approved_and_legacy_stays_official(self):
        _, notion, _ = run_goals([kid("MEJ-20", "MEJ-2", True), kid("MEJ-21", "MEJ-2")])
        self.assertEqual(table(notion, "matt")[1], ["Creative", "██████████", "1 / 2", "—", "NEEDS Q1 MEMBER LIST"])
        status = notion.blocks[notion.kids[next(k for k in notion.kids["b-matt-goals"] if notion.blocks[k]["type"] == "table")][1]]["table_row"]["cells"][4][0]
        self.assertEqual(status["annotations"]["color"], "red")

    def test_an_approved_list_shows_the_v2_count_beside_the_official_legacy_count(self):
        frozen = approved({"MEJ-20": {"parent": "MEJ-2", "goal": "MEJ-2"}, "MEJ-21": {"parent": "MEJ-2", "goal": "MEJ-2"}})
        _, notion, _ = run_goals([kid("MEJ-20", "MEJ-2", True), kid("MEJ-21", "MEJ-2"), kid("MEJ-22", "MEJ-2", True)], membership=frozen)
        self.assertEqual(table(notion, "matt")[1], ["Creative", "██████████", "2 / 3", "1 / 2", "OK"])

    def test_a_drifted_list_reads_data_missing_in_red(self):
        frozen = approved({"MEJ-20": {"parent": "MEJ-2", "goal": "MEJ-99"}})
        _, notion, _ = run_goals([kid("MEJ-20", "MEJ-2", True)], membership=frozen)
        self.assertEqual(table(notion, "matt")[1][3:], ["—", "DATA MISSING"])

    def test_a_failing_v2_read_never_fails_the_stage_or_hides_the_legacy_figures(self):
        with mock.patch.object(read, "tally", side_effect=lambda jira, found, first, last, **kw: (_ for _ in ()).throw(JiraError("JIRA_NETWORK")) if kw else real_tally(jira, found, first, last)):
            out, notion, _ = run_goals([kid("MEJ-20", "MEJ-2", True)])
        self.assertEqual(table(notion, "matt")[1][2:], ["1 / 1", "—", "DATA MISSING"])

    def test_a_second_run_writes_nothing_and_a_dry_run_writes_nothing(self):
        kids = [kid("JFM-20", "JFM-9", True)]
        _, notion, _ = run_goals(kids)
        notion.writes.clear()
        run_goals(kids, notion=notion)
        self.assertEqual(notion.writes, [])
        fresh = board()
        out, fresh, _ = run_goals(kids, live=False, notion=fresh)
        self.assertEqual(fresh.writes, [])
        self.assertEqual(out["jim"]["changed"] > 0, True)

    def test_a_changed_figure_rewrites_only_that_row(self):
        _, notion, _ = run_goals([kid("JFM-20", "JFM-9", True)])
        notion.writes.clear()
        run_goals([kid("JFM-20", "JFM-9", True), kid("JFM-21", "JFM-9")], notion=notion)
        rows = {k for k in notion.writes if k.startswith("n") and notion.blocks[k]["type"] == "table_row"}
        self.assertEqual(len(rows), 1)

    def test_a_table_of_another_width_is_refused(self):
        notion = board()
        notion.blocks["t-jim"]["table"]["table_width"] = 3
        with self.assertRaises(region.AccountabilityError) as caught:
            run_goals([], notion=notion)
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_SHAPE")

    def test_the_scoped_client_refuses_a_fixed_block(self):
        client = region.scoped(board(), "b-jim-goals")
        with self.assertRaises(region.AccountabilityError) as caught:
            client.call_once("PATCH", "/blocks/fx-body", {"paragraph": {"rich_text": []}})
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_WRITE_OUT_OF_SCOPE")


class Bar(unittest.TestCase):
    def test_ten_blocks_filled_by_share_and_coloured_by_threshold(self):
        for done, total, filled, color in ((5, 26, 2, "red"), (3, 7, 4, "orange"), (6, 9, 7, "orange"), (7, 10, 7, "green"), (0, 6, 0, None), (10, 10, 10, "green"), (0, 0, 0, None)):
            pieces = render.bar(done, total)
            self.assertEqual(sum(len(p[0]) for p in pieces), 10)
            self.assertEqual(sum(len(p[0]) for p in pieces if p[3] != "gray"), filled)                         # filled cells keep their colour
            self.assertEqual(sum(len(p[0]) for p in pieces if p[3] == "gray"), 10 - filled)                     # empty cells are the same glyph in gray
            self.assertTrue(all(set(p[0]) == {"█"} for p in pieces))                                            # one glyph, so every bar is the same width (Jim, Nov 25 2026)
            if filled:
                self.assertEqual(pieces[0][3], color)


class Render(unittest.TestCase):
    def test_the_synthetic_command_prints_without_any_network_or_write(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(preview.main(["--synthetic"]), 0)
        text = out.getvalue()
        self.assertIn("██████████ · 5 / 26 · — · NEEDS Q1 MEMBER LIST", text)
        self.assertIn("██████████ · 12 / 29 · — · NEEDS Q1 MEMBER LIST", text)

    def test_any_other_argument_is_refused(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(preview.main(["--live"]), 2)


real_tally = read.tally

if __name__ == "__main__":
    unittest.main()
