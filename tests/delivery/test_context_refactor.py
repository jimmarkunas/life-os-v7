"""LIFE-79: the Jira/Notion context-refactor planner reads snapshots, proposes only safe moves, and its approval, revision and read-back checks hold."""
import contextlib
import copy
import io
import json
import os
import re
import tempfile
import unittest

from lifeos.delivery import context_refactor as cr

DETAIL = "\n\n".join("The relay stores room history in a volume with a nightly export, a read-only mirror and a port map for each host (part %d)." % i for i in range(8))
DESC = ("## Outcome\n\nBuild the pilot room.\n\n## Acceptance\n\n**Given** a room, **when** a message is posted, **then** it is retrievable.\n\n"
        "## Architecture and requirements\n\n" + DETAIL + "\n\n## Notes\n\nAn old plan used T3 for remote sessions.\n\n## GitHub result\n\nPR #5 merged, squash commit recorded.\n")
PAGE = {"id": "p1", "url": "https://notion.example/p1"}
LINKS = [{"type": "Blocks", "direction": "outward", "key": "LIFE-2"}]


def issue(key="LIFE-1", desc=DESC, **extra):
    return dict({"key": key, "updated": "r1", "status": "To Do", "description": desc, "links": LINKS, "comment_count": 3, "notion_pages": [PAGE]}, **extra)


def snapshot(*issues, page_text="# Product page\n\nExisting content.\n"):
    return {"issues": list(issues), "notion": {"p1": {"last_edited": "n1", "text": page_text}}}


CONFIG = {"writer": "claude-code", "retired_terms": ["T3"]}
APPROVAL = lambda plan, targets, **kw: dict({"approved_by": "Jim Markunas", "source": "chat, Nov 1", "plan_digest": plan["digest"], "targets": targets, "max_batch": 5}, **kw)


def live_of(snap, **kw):
    return {"issues": {i["key"]: i for i in snap["issues"]}, "notion": copy.deepcopy(snap["notion"]), "writer_claims": kw.get("claims", {})}


def run_ops(live, ops):
    """A stand-in for the authorized agent's connectors."""
    for op in ops:
        if op["op"] == "notion_append":
            live["notion"][op["page"]]["text"] += "\n" + op["text"]
        elif op["op"] == "jira_edit_description":
            live["issues"][op["key"]] = dict(live["issues"][op["key"]], description=op["description"], updated="r2")
    return live


class Sections(unittest.TestCase):
    def test_splitting_is_lossless_and_classes_are_proposals_only(self):
        self.assertEqual("".join(cr.split_sections(DESC)), DESC)
        self.assertEqual("".join(cr.split_sections("no heading at all")), "no heading at all")
        by = {s.split("\n", 1)[0]: cr.classify(s, ["T3"])[0] for s in cr.split_sections(DESC)}
        self.assertEqual(by["## Outcome"], "CURRENT_SCOPE")
        self.assertEqual(by["## Acceptance"], "CURRENT_SCOPE")
        self.assertEqual(by["## Architecture and requirements"], "DETAILED_REQUIREMENT")
        self.assertEqual(by["## Notes"], "CONFLICT")                       # a retired tool presented as live
        self.assertEqual(by["## GitHub result"], "HISTORICAL_EVIDENCE")
        self.assertEqual(cr.classify("## Jim decision\n\nApproved by Jim.")[0], "DECISION")
        self.assertEqual(cr.classify("## Misc\n\nshort")[0], "UNCLASSIFIED")

    def test_a_retired_term_in_history_is_not_a_conflict(self):
        self.assertEqual(cr.classify("## Old\n\nHISTORICAL — SUPERSEDED: T3 was tried.", ["T3"])[0], "HISTORICAL_EVIDENCE")


class Planning(unittest.TestCase):
    def setUp(self):
        self.snap = snapshot(issue())
        self.plan = cr.build_plan(self.snap, CONFIG)
        self.p = self.plan["issues"][0]

    def test_only_the_clean_detailed_requirement_moves_to_the_one_existing_page(self):
        self.assertEqual(self.p["status"], "PLANNED")
        self.assertEqual([s["destination"] for s in self.p["sections"]], ["JIRA", "JIRA", "NOTION", "JIRA", "JIRA"])
        self.assertEqual(self.p["notion_page"], {"id": "p1", "revision": "n1"})                  # an existing page, never a new one
        self.assertIn(DETAIL, self.p["notion_append"])                                           # verbatim
        self.assertIn("Migrated from LIFE-1 (Jira revision r1)", self.p["notion_append"])
        self.assertIn(cr.POINTER, self.p["proposed_description"])
        self.assertNotIn(DETAIL, self.p["proposed_description"])
        for kept in ("Build the pilot room.", "**Given** a room", "PR #5 merged", "An old plan used T3"):
            self.assertIn(kept, self.p["proposed_description"])                                  # scope, acceptance, evidence and the conflict stay put
        self.assertEqual([e["conflicts"] for e in self.p["exceptions"]], [["T3"]])               # surfaced for the owner, never auto-edited
        self.assertIn("-" + DETAIL.splitlines()[0], self.p["jira_diff"])                         # the reviewable diff shows the removed text

    def test_the_map_accounts_for_every_old_section_and_the_digest_is_stable(self):
        self.assertEqual({m["sha"] for m in self.p["mapping"]}, {s["sha"] for s in self.p["sections"]})
        self.assertEqual(len(self.p["mapping"]), len(cr.split_sections(DESC)))
        self.assertTrue(any(m["to"].startswith("NOTION:p1#") for m in self.p["mapping"]))
        self.assertEqual(cr.build_plan(copy.deepcopy(self.snap), CONFIG)["digest"], self.plan["digest"])
        self.assertIn("Dry-run migration map", cr.report(self.plan))

    def test_rerunning_after_the_move_is_a_no_op(self):
        moved = run_ops(live_of(self.snap), cr.validate(self.plan, APPROVAL(self.plan, ["LIFE-1"]), live_of(self.snap))["operations"])
        again = cr.build_plan({"issues": list(moved["issues"].values()), "notion": moved["notion"]}, CONFIG)
        self.assertEqual(again["issues"][0]["status"], "NOOP")

    def test_text_already_in_notion_is_pointed_to_not_duplicated(self):
        plan = cr.build_plan(snapshot(issue(), page_text="# Page\n\n## Architecture and requirements\n\n" + DETAIL + "\n\n"), CONFIG)
        p = plan["issues"][0]
        self.assertEqual((p["status"], p["notion_append"]), ("PLANNED", ""))
        self.assertEqual(cr.validate(plan, APPROVAL(plan, ["LIFE-1"]), live_of(snapshot(issue(), page_text="# Page\n\n## Architecture and requirements\n\n" + DETAIL + "\n\n")))["problems"], [])

    def test_exclusions_and_blockers_stay_untouched(self):
        cases = {
            "LOS-70": issue("LOS-70"),
            "LIFE-3": issue("LIFE-3", DESC + "\n## Discovery log — Tranche 1 (2026-11-01)\n\nQ1 ..."),
            "LIFE-4": issue("LIFE-4", active_discovery=True),
            "LIFE-5": issue("LIFE-5", DESC + "\napi_key = abcd1234efgh5678"),
            "LIFE-6": issue("LIFE-6"),
        }
        plan = cr.build_plan(snapshot(*cases.values()), dict(CONFIG, writer_claims={"LIFE-6": "someone-else"}))
        got = {p["key"]: (p["status"], p["reasons"][0]) for p in plan["issues"]}
        self.assertEqual(got["LOS-70"], ("EXCLUDED", "EXCLUDED_BY_CONFIG"))
        self.assertEqual(got["LIFE-3"], ("EXCLUDED", "ACTIVE_DISCOVERY"))
        self.assertEqual(got["LIFE-4"], ("EXCLUDED", "ACTIVE_DISCOVERY"))
        self.assertEqual(got["LIFE-5"], ("BLOCKED", "SECRET_LIKE_VALUE"))
        self.assertEqual(got["LIFE-6"], ("BLOCKED", "CLAIMED_BY_OTHER_WRITER"))

    def test_no_or_ambiguous_notion_owner_keeps_everything_in_jira(self):
        none = cr.build_plan(snapshot(issue(notion_pages=[])), CONFIG)["issues"][0]
        two = cr.build_plan(snapshot(issue(notion_pages=[PAGE, {"id": "p2", "url": "u"}])), CONFIG)["issues"][0]
        for p, reason in ((none, "NO_NOTION_OWNER"), (two, "AMBIGUOUS_NOTION_OWNER")):
            self.assertEqual((p["status"], p["reasons"][0]), ("NOOP", reason))
            self.assertEqual(p["proposed_description"], DESC)


class Execution(unittest.TestCase):
    def setUp(self):
        self.snap = snapshot(issue())
        self.plan = cr.build_plan(self.snap, CONFIG)

    def check(self, **kw):
        approval = kw.pop("approval", None) or APPROVAL(self.plan, ["LIFE-1"])
        return cr.validate(self.plan, approval, kw.pop("live", None) or live_of(self.snap, **kw))

    def test_an_exact_approval_yields_ordered_operations(self):
        r = self.check()
        self.assertTrue(r["ok"], r)
        self.assertEqual([o["op"] for o in r["operations"]], ["notion_append", "notion_readback", "jira_edit_description", "jira_readback"])   # Notion first, so nothing is lost
        self.assertEqual(r["operations"][2]["expected_revision"], "r1")

    def test_unapproved_or_inexact_requests_are_refused(self):
        bad = {"APPROVAL_MISSING_ATTRIBUTION": dict(APPROVAL(self.plan, ["LIFE-1"]), approved_by=""), "PLAN_DIGEST_MISMATCH": dict(APPROVAL(self.plan, ["LIFE-1"]), plan_digest="x"),
               "TARGETS_NOT_EXACT": APPROVAL(self.plan, []), "BATCH_TOO_LARGE": dict(APPROVAL(self.plan, ["LIFE-1"]), max_batch=0)}
        for problem, approval in bad.items():
            r = self.check(approval=approval)
            self.assertIn(problem, r["problems"])
            self.assertEqual(r["operations"], [])
        self.assertIn("TARGETS_NOT_EXACT", self.check(approval=APPROVAL(self.plan, ["LIFE-1", "LIFE-9"]))["problems"])
        self.assertEqual(cr.HARD_BATCH_CAP, 10)

    def test_changed_revisions_and_foreign_writers_stop_the_run(self):
        live = live_of(self.snap)
        live["issues"]["LIFE-1"]["updated"] = "r9"
        self.assertIn("JIRA_REVISION_CHANGED:LIFE-1", self.check(live=live)["problems"])
        live = live_of(self.snap)
        live["notion"]["p1"]["last_edited"] = "n9"
        self.assertIn("NOTION_REVISION_CHANGED:LIFE-1", self.check(live=live)["problems"])
        self.assertIn("CLAIMED_BY_OTHER_WRITER:LIFE-1", self.check(claims={"LIFE-1": "someone-else"})["problems"])

    def test_it_is_idempotent_and_resumes_a_half_finished_run(self):
        live = live_of(self.snap)
        run_ops(live, self.check()["operations"])
        again = self.check(live=live)
        self.assertEqual((again["ok"], again["operations"]), (True, []))                          # already applied: nothing to do
        half = live_of(self.snap)
        half["notion"]["p1"]["text"] += "\n" + self.plan["issues"][0]["notion_append"]
        half["notion"]["p1"]["last_edited"] = "n2"                                                 # our own earlier append moved the revision
        r = self.check(live=half)
        self.assertEqual([o["op"] for o in r["operations"]], ["jira_edit_description", "jira_readback"])

    def test_independent_readback_passes_only_when_nothing_is_lost_or_changed(self):
        live = live_of(self.snap)
        run_ops(live, self.check()["operations"])
        self.assertTrue(cr.verify(self.plan, live)["ok"])
        for mutate, check in ((lambda l: l["issues"]["LIFE-1"].update(status="Done"), "key_status_unchanged"),
                              (lambda l: l["issues"]["LIFE-1"].update(links=[]), "links_unchanged"),
                              (lambda l: l["issues"]["LIFE-1"].update(comment_count=0), "comments_unchanged"),
                              (lambda l: l["issues"]["LIFE-1"].update(description="gone"), "description_exact")):
            broken = copy.deepcopy(live)
            mutate(broken)
            r = cr.verify(self.plan, broken)
            self.assertFalse(r["ok"])
            self.assertFalse(r["results"][0]["checks"][check])
        lost = copy.deepcopy(live)
        lost["notion"]["p1"]["text"] = "# Product page\n"                                         # the moved text never arrived
        r = cr.verify(self.plan, lost)
        self.assertFalse(r["ok"])
        self.assertFalse(r["results"][0]["checks"]["nothing_lost"])
        self.assertEqual(r["results"][0]["lost"], ["LIFE-1#2"])


class Boundaries(unittest.TestCase):
    def test_the_module_has_no_network_process_or_datastore(self):
        src = open(cr.__file__).read()
        for word in ("import requests", "urllib", "socket", "subprocess", "sqlite", "import os", "scheduler.", "cron", "open(path, \"w\""):
            self.assertNotIn(word, src)

    def test_cli_plans_reports_and_validates_from_files(self):
        with tempfile.TemporaryDirectory() as d:
            paths = {n: os.path.join(d, n + ".json") for n in ("snap", "cfg", "plan")}
            json.dump(snapshot(issue()), open(paths["snap"], "w"))
            json.dump(CONFIG, open(paths["cfg"], "w"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cr.main(["plan", paths["snap"], paths["cfg"]]), 0)
            plan = json.loads(out.getvalue())
            self.assertEqual(plan["counts"]["PLANNED"], 1)
            json.dump(plan, open(paths["plan"], "w"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cr.main(["report", paths["plan"]])
            self.assertIn("LIFE-1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
