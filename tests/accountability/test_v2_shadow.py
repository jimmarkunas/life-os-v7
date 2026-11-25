"""LIFE-918 AC4/AC5 (synthetic, no network, no provider write): the v2 quarterly shadow and the V3 preview block.

Off by default and byte-identical to main when off; on with no preview block does nothing; on with the shipped empty membership reads DEGRADED and invents nothing; on with approved fixture membership shows
official legacy figures beside preview v2 figures; an ambiguous mapping is DEGRADED; the write stays inside the one preview callout and never touches the fixed blocks; v1 figures are the unchanged reader's."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lifeos.accountability import common, goals, preview, read, region, render
from lifeos.platform.jira import JiraError
from tests.accountability import fakes as F

NOW = F.NOW
PREVIEW = "abcdef0123456789" * 2
ON = {"V2_QUARTER_SHADOW": "on", "V3_PREVIEW_BLOCK_ID": PREVIEW}
EPICS = [F.epic("JFM-9", "Career Q4 2026"), F.epic("MEJ-2", "Creative Q4 2026")]


def approved(tasks, ref="SYNTHETIC-TEST-ONLY"):
    return {"schema_version": 1, "quarters": {"2026-q4": {"approved": True, "baseline_id": "SYNTHETIC-Q4", "approval_ref": ref, "tasks": tasks}}}


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
    """A fake Notion with the preview callout (heading, stamp, one body paragraph) and a fixed block that must never be written."""
    notion = F.Notion()
    notion.callout(PREVIEW, "V3 preview", "Not updated yet", [F.para("pv-body", "Nothing yet.")], "unused", legacy=False)
    notion.callout("fixed", "Quarterly goals Jim", "Not updated yet", [F.para("fx-body", "keep me")], "pg-fixed")
    return notion


def run_shadow(kids, environ=ON, live=True, notion=None, membership=None, page="Jim & Matt V3"):
    notion = notion or board()
    jira = Jira(kids)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(region, "_page_of", return_value=page))
        if membership is not None:
            path = Path(tempfile.mkdtemp()) / "m.json"
            path.write_text(json.dumps(membership))
            stack.enter_context(mock.patch.object(read, "MEJ_MEMBERSHIP", path))
        out = preview.shadow(environ, jira, notion, NOW, live)
    return out, notion, jira


class Switches(unittest.TestCase):
    def test_off_by_default_reads_and_writes_nothing(self):
        for environ in ({}, {"V3_PREVIEW_BLOCK_ID": PREVIEW}, {"V2_QUARTER_SHADOW": "off", "V3_PREVIEW_BLOCK_ID": PREVIEW}, {"V2_QUARTER_SHADOW": "true", "V3_PREVIEW_BLOCK_ID": PREVIEW}):
            out, notion, jira = run_shadow([], environ)
            self.assertIsNone(out)
            self.assertEqual((notion.writes, jira.queries), ([], []))

    def test_goals_stage_with_the_switch_off_is_exactly_the_stage_as_it_was(self):
        for environ in ({}, {"V3_PREVIEW_BLOCK_ID": PREVIEW}):
            with mock.patch.object(common, "run_region", return_value={"jim": {"epics": 1}}) as base, mock.patch.object(preview, "shadow") as shadow:
                out = goals.run(0, False, environ=environ, jira="J", notion="N", now=NOW)
            self.assertEqual(out, {"jim": {"epics": 1}})
            base.assert_called_once_with("goals", goals._build, environ, "J", "N", NOW, False)
            shadow.assert_not_called()

    def test_goals_stage_with_the_switch_on_adds_only_a_preview_count(self):
        with mock.patch.object(common, "run_region", return_value={"jim": {"epics": 1}}), mock.patch.object(preview, "shadow", return_value={"preview": "written"}):
            out = goals.run(0, True, environ=ON, jira="J", notion="N", now=NOW)
        self.assertEqual(out, {"jim": {"epics": 1}, "preview": {"preview": "written"}})

    def test_a_failing_preview_never_fails_the_goals_stage(self):
        with mock.patch.object(common, "run_region", return_value={"jim": {}}), mock.patch.object(preview, "figures", side_effect=JiraError("JIRA_NETWORK")), \
                mock.patch.object(region, "_page_of", return_value="Jim & Matt V3"):
            out = goals.run(0, True, environ=ON, jira="J", notion=board(), now=NOW)
        self.assertEqual(out["preview"], {"preview": "failed", "code": "JIRA_NETWORK"})

    def test_no_preview_block_configured_writes_nothing(self):
        for environ in ({"V2_QUARTER_SHADOW": "on"}, {"V2_QUARTER_SHADOW": "on", "V3_PREVIEW_BLOCK_ID": "not-an-id"}):
            out, notion, jira = run_shadow([], environ)
            self.assertEqual(out, {"preview": "no_block"})
            self.assertEqual((notion.writes, jira.queries), ([], []))

    def test_not_live_plans_and_writes_nothing(self):
        out, notion, _ = run_shadow([kid("JFM-20", "JFM-9", True)], live=False)
        self.assertEqual(out["preview"], "planned")
        self.assertEqual(notion.writes, [])


class Figures(unittest.TestCase):
    def test_empty_shipped_membership_is_degraded_and_nothing_is_invented(self):
        out, notion, _ = run_shadow([kid("JFM-20", "JFM-9", True), kid("JFM-21", "JFM-9"), kid("MEJ-20", "MEJ-2", True)])
        self.assertEqual((out["preview"], out["degraded"]), ("written", 1))
        body = notion.text("pv-body")
        self.assertIn("Career: Official (legacy) 1 / 2 · Preview (v2) no v2 for JFM (stays on legacy)", body)
        self.assertIn("Creative: Official (legacy) 1 / 1 · Preview (v2) DEGRADED (ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED)", body)
        self.assertNotIn("v2) 1 / 1", body)

    def test_approved_fixture_membership_shows_official_and_preview_side_by_side(self):
        frozen = approved({"MEJ-20": {"parent": "MEJ-2", "goal": "MEJ-2"}, "MEJ-21": {"parent": "MEJ-2", "goal": "MEJ-2"}})
        out, notion, _ = run_shadow([kid("MEJ-20", "MEJ-2", True), kid("MEJ-21", "MEJ-2"), kid("MEJ-22", "MEJ-2", True)], membership=frozen)
        self.assertEqual(out["degraded"], 0)
        self.assertIn("Creative: Official (legacy) 2 / 3 · Preview (v2) 1 / 2", notion.text("pv-body"))

    def test_an_ambiguous_or_drifted_mapping_is_degraded(self):
        frozen = approved({"MEJ-20": {"parent": "MEJ-2", "goal": "MEJ-99"}})
        out, notion, _ = run_shadow([kid("MEJ-20", "MEJ-2", True)], membership=frozen)
        self.assertEqual(out["degraded"], 1)
        self.assertIn("Preview (v2) DEGRADED (ACCOUNTABILITY_MEJ_MEMBERSHIP_DRIFT)", notion.text("pv-body"))

    def test_official_legacy_figures_are_the_unchanged_reader(self):
        kids = [kid("JFM-20", "JFM-9", True), kid("JFM-21", "JFM-9"), kid("MEJ-20", "MEJ-2", True), kid("MEJ-21", "MEJ-2")]
        jira = Jira(kids)
        year, n, rows = preview.figures(jira, NOW)
        first, last = read.quarter_bounds(year, n)
        direct = read.tally(Jira(kids), [{"key": "JFM-9", "person": "Jim", "lane": "Career"}], first, last)
        self.assertEqual(next(r["legacy"] for r in rows if r["key"] == "JFM-9"), tuple(direct["JFM-9"]))
        self.assertTrue(all(r["v2"] is None for r in rows))

    def test_a_legacy_read_that_fails_is_not_hidden(self):
        with mock.patch.object(read, "tally", side_effect=JiraError("JIRA_NETWORK")):
            out, notion, _ = run_shadow([kid("JFM-20", "JFM-9")])
        self.assertEqual(out, {"preview": "failed", "code": "JIRA_NETWORK"})
        self.assertEqual(notion.writes, [])


class Write(unittest.TestCase):
    def test_the_write_stays_inside_the_preview_callout(self):
        out, notion, _ = run_shadow([kid("JFM-20", "JFM-9", True)])
        self.assertEqual(out["preview"], "written")
        self.assertEqual(set(notion.writes), {"pv-body", PREVIEW + "-st"})
        self.assertEqual(notion.text("fx-body"), "keep me")
        self.assertEqual(notion.text("fixed-st"), "Not updated yet")
        self.assertTrue(notion.text(PREVIEW + "-st").startswith("Last refreshed"))

    def test_a_second_identical_run_writes_nothing(self):
        kids = [kid("JFM-20", "JFM-9", True)]
        _, notion, _ = run_shadow(kids)
        notion.writes.clear()
        out, notion, _ = run_shadow(kids, notion=notion)
        self.assertEqual((out["preview"], notion.writes), ("unchanged", []))

    def test_a_block_that_is_not_on_the_v3_page_is_refused(self):
        out, notion, _ = run_shadow([], page="Some other page")
        self.assertEqual(out, {"preview": "failed", "code": "ACCOUNTABILITY_TARGET_NOT_FOUND"})
        self.assertEqual(notion.writes, [])

    def test_a_wrong_shaped_callout_is_refused_and_nothing_is_written(self):
        notion = F.Notion()
        notion.callout(PREVIEW, "V3 preview", "stamp", [], "unused", legacy=False)
        out, notion, _ = run_shadow([], notion=notion)
        self.assertEqual(out, {"preview": "failed", "code": "ACCOUNTABILITY_SHAPE"})
        self.assertEqual(notion.writes, [])

    def test_the_scoped_client_refuses_a_fixed_block(self):
        client = region.scoped(board(), PREVIEW)
        with self.assertRaises(region.AccountabilityError) as caught:
            client.call_once("PATCH", "/blocks/fx-body", {"paragraph": {"rich_text": []}})
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_WRITE_OUT_OF_SCOPE")


class Render(unittest.TestCase):
    def test_render_is_pure_and_labels_both_sides(self):
        rows = [{"person": "Matt", "lane": "Career", "key": "MEJ-1", "legacy": (1, 2), "v2": (1, 1), "code": None, "applies": True}]
        text = preview.plain(preview.render_preview(2026, 4, rows))
        self.assertIn("legacy figures are official", text)
        self.assertIn("Career: Official (legacy) 1 / 2 · Preview (v2) 1 / 1", text)
        self.assertEqual(preview.render_preview(2026, 4, rows), preview.render_preview(2026, 4, rows))

    def test_the_synthetic_command_prints_without_any_network_or_write(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(preview.main(["--synthetic"]), 0)
        text = out.getvalue()
        self.assertIn("Last refreshed 12/1 · 9:00 AM CT", text)
        self.assertIn("Official (legacy) 2 / 6 · Preview (v2) 2 / 5", text)
        self.assertIn("DEGRADED (ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED)", text)

    def test_any_other_argument_is_refused(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(preview.main([]), 2)
            self.assertEqual(preview.main(["--live"]), 2)


if __name__ == "__main__":
    unittest.main()
