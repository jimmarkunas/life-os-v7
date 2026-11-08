import unittest

from lifeos.jobs import hiring_pipeline as hp
from lifeos.platform.notion_client import NotionError


def page(pid, title, children=False):
    return {"id": pid, "type": "child_page", "child_page": {"title": title}, "has_children": children}


def heading(text):
    return {"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": text}]}, "has_children": False}


class Fake:
    def __init__(self, tree, fail=False):
        self.tree, self.fail, self.calls = tree, fail, 0

    def call(self, method, path, body=None):
        self.calls += 1
        if self.fail:
            raise NotionError("NOTION_HTTP_404")
        block = path.split("/blocks/")[1].split("/")[0]
        return {"results": self.tree.get(block, []), "has_more": False}


TREE = {
    "root": [page("act", "Active Opportunities", True), page("ret", "Retired Opportunities", True)],
    "act": [page("o1", "Smartsheet — Technical Program Manager", True), page("o2", "Smith — Senior Project Manager", True)],
    "ret": [page("o3", "Acme — Product Manager", True)],
    "o1": [page("r1", "Round 1 - Recruiter screen")],
    "o2": [],
    "o3": [page("r2", "Round 1")],
}


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.opps = hp.read(Fake(TREE), "root")

    def test_reads_sections_and_rounds(self):
        got = {o.page_id: (o.section, o.rounds, o.company, o.role) for o in self.opps}
        self.assertEqual(got["o1"], (hp.ACTIVE, 1, "Smartsheet", "Technical Program Manager"))
        self.assertEqual(got["o2"][:2], (hp.ACTIVE, 0))
        self.assertEqual(got["o3"][:2], (hp.RETIRED, 1))

    def test_without_rounds_the_same_opportunities_are_read_with_one_call_fewer_per_opportunity(self):
        full, lean = Fake(TREE), Fake(TREE)
        with_rounds, without = hp.read(full, "root"), hp.read(lean, "root", rounds=False)
        self.assertEqual([(o.page_id, o.section, o.company, o.role) for o in without], [(o.page_id, o.section, o.company, o.role) for o in with_rounds])
        self.assertEqual({o.rounds for o in without}, {0})
        self.assertEqual(full.calls - lean.calls, len(with_rounds))                               # the S2 closure skips exactly the interview-count reads

    def test_a_round_less_page_is_a_lead_only_when_its_render_line_names_a_recruiter_key(self):
        def fold(line):
            return {"id": "t", "type": "toggle", "toggle": {"rich_text": [{"plain_text": "LIFE OS"}]}, "has_children": True}, {"id": "l", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": line}]}}
        for line, want in (("v7-render:1;job=recruiter-acme-program-manager;source=0123456789abcdef;page=pending", True),
                           ("v7-render:1;job=acme-r-1;source=0123456789abcdef;page=pending", False)):
            toggle, text = fold(line)
            tree = {"root": [page("act", "Active Opportunities", True)], "act": [page("o1", "Acme — Program Manager", True)], "o1": [toggle], "t": [text]}
            (found,) = hp.read(Fake(tree), "root")
            self.assertEqual((found.rounds, found.lead), (0, want), line)
        tree = {"root": [page("act", "Active Opportunities", True)], "act": [page("o1", "Acme — Program Manager", True)], "o1": [page("r1", "Interview 1")]}
        self.assertFalse(hp.read(Fake(tree), "root")[0].lead)                          # a page with a round is never a lead

    def test_a_lead_is_replied_only_by_a_done_line_for_its_own_reply_action(self):
        def para(i, line):
            return {"id": i, "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": line}]}}
        render, act = "v7-render:1;job=recruiter-acme-program-manager;source=0123456789abcdef;page=pending", "v7-action:1;key=d68334d240ba7eda;text=Reply to the recruiter's message, or close this lead."
        for done, want in (("v7-done:1;key=d68334d240ba7eda;on=2026-10-24", "2026-10-24"), ("v7-done:1;key=0000000000000000;on=2026-10-24", ""), (None, "")):
            lines = [para("l1", render), para("l2", act)] + ([para("l3", done)] if done else [])
            tree = {"root": [page("act", "Active Opportunities", True)], "act": [page("o1", "Acme — Program Manager", True)],
                    "o1": [{"id": "t", "type": "toggle", "toggle": {"rich_text": [{"plain_text": "LIFE OS"}]}, "has_children": True}], "t": lines}
            (found,) = hp.read(Fake(tree), "root")
            self.assertEqual((found.lead, found.replied), (True, want), done)

    def test_the_lead_line_formats_are_the_writer_and_close_service_formats(self):
        from lifeos.interview import template
        from lifeos.sources import close_app
        self.assertEqual(hp.DONE_LINE.pattern, template.DONE_LINE.pattern)
        self.assertTrue(hp.LEAD_ACTION.fullmatch(template.action_line(close_app.LEAD_ACTION + ".", "ctx")))

    def test_an_unreadable_fold_is_simply_not_a_lead(self):
        tree = {"root": [page("act", "Active Opportunities", True)], "act": [page("o1", "Acme — Program Manager", True)],
                "o1": [{"id": "t", "type": "toggle", "toggle": {"rich_text": []}, "has_children": True}]}
        class Down(Fake):
            def call(self, method, path, body=None):
                if "/blocks/t/" in path:
                    raise NotionError("NOTION_HTTP_404")
                return super().call(method, path, body)
        self.assertFalse(hp.read(Down(tree), "root")[0].lead)

    def test_company_and_role_must_both_match(self):
        h = hp.handoff_for("Smartsheet Inc.", "Technical Programme Manager", self.opps)       # legal suffix and British spelling
        self.assertEqual((h.protected, h.progression_state, h.interview_progression), (True, hp.INTERVIEW_ACTIVE, True))
        self.assertEqual(h.evidence, [{"type": "hiring_pipeline_opportunity", "source_id": "o1"}])
        self.assertEqual(hp.handoff_for("Smith", "Senior Project Manager", self.opps).progression_state, hp.HIRING_ACTIVE)
        self.assertFalse(hp.handoff_for("Smartsheet", "Software Engineer", self.opps).protected)       # company alone is not evidence
        self.assertFalse(hp.handoff_for("Smith & Wesson", "Senior Project Manager", self.opps).protected)
        self.assertFalse(hp.handoff_for("Acme", "Product Manager", self.opps).protected)               # retired: no protection from this source

    def test_a_walmart_subsidiary_prefix_matches_but_a_substring_does_not(self):
        self.assertTrue(hp.same_company("Walmart", "Walmart Global Tech"))
        self.assertFalse(hp.same_company("Wal", "Walmart"))

    def test_unreadable_page_is_reported_not_raised(self):
        self.assertEqual(hp.snapshot(Fake(TREE, fail=True), "root"), ([], "unreadable"))

    def test_headings_and_toggles_also_set_the_section(self):
        tree = {"root": [heading("Active"), page("o1", "Acme — Program Manager"), heading("Retired"), page("o2", "Beta — Program Manager")],
                "o1": [], "o2": []}
        got = {o.page_id: o.section for o in hp.read(Fake(tree), "root")}
        self.assertEqual(got, {"o1": hp.ACTIVE, "o2": hp.RETIRED})


def rt(text):
    return [{"plain_text": text}]


def row(rid, company, role, stage, decision=None):
    return {"id": rid, "properties": {"Company": {"rich_text": rt(company)}, "Job": {"title": rt(role)}, "Stage": {"select": {"name": stage} if stage else None},
                                      "Hiring Decision": {"select": {"name": decision} if decision else None}}}


class FakeLedgerClient:
    """query_data_source pages (list of lists) and block children by row id."""
    def __init__(self, pages, children=None, bad=None):
        self.pages, self.children, self.bad, self.bodies = pages, children or {}, bad, []

    def query_data_source(self, source_id=None, body=None):
        self.bodies.append(body)
        if self.bad:
            return self.bad
        index = int(body.get("start_cursor", "0"))
        more = index + 1 < len(self.pages)
        return {"results": self.pages[index], "has_more": more, **({"next_cursor": str(index + 1)} if more else {})}

    def call(self, method, path, body=None):
        return {"results": self.children.get(path.split("/")[2], []), "has_more": False}


class LedgerFirst(unittest.TestCase):
    """LIFE-352 (REQ-5, LOS-88): pursuits come from Ledger rows with Stage set; the page tree fills in only what the Ledger does not hold."""

    def test_stage_and_decision_set_the_section_and_child_pages_are_the_rounds(self):
        kids = {"r1": [{"type": "child_page", "child_page": {"title": "Interview 1"}, "id": "c1"}]}
        got = {o.page_id: o for o in hp.from_ledger(FakeLedgerClient([[row("r1", "Acme", "PM", "Interviewing"), row("r2", "Beta", "PM", "Applied", "Rejected"),
                                                                       row("r3", "Cora", "PM", "Ended"), row("r4", "", "PM", "Applied")]], kids))}
        self.assertEqual({k: (v.section, v.rounds) for k, v in got.items()}, {"r1": (hp.ACTIVE, 1), "r2": (hp.RETIRED, 0), "r3": (hp.RETIRED, 0)})   # r4 is unnamed: not a pursuit
        self.assertTrue(all(o.from_row for o in got.values()))

    def test_the_query_asks_only_for_rows_with_a_stage(self):
        client = FakeLedgerClient([[]])
        hp.from_ledger(client)
        self.assertEqual(client.bodies[0]["filter"], {"property": "Stage", "select": {"is_not_empty": True}})

    def test_several_ledger_pages_are_all_read(self):
        client = FakeLedgerClient([[row("r1", "Acme", "PM", "Applied")], [row("r2", "Beta", "PM", "Applied")]])
        self.assertEqual([o.page_id for o in hp.from_ledger(client)], ["r1", "r2"])

    def test_an_incomplete_ledger_read_raises_never_a_partial_list(self):
        for bad in ({"results": []}, {"results": [], "has_more": True, "next_cursor": ""}, "oops"):
            with self.assertRaises(NotionError):
                hp.from_ledger(FakeLedgerClient([[]], bad=bad))

    def test_ledger_pursuits_win_and_the_tree_fills_in_the_rest(self):
        ledger = hp.from_ledger(FakeLedgerClient([[row("r1", "Acme", "Program Manager", "Interviewing")]]))
        tree = [hp.Opportunity("t1", "Acme — Program Manager", hp.RETIRED, 0, "Acme", "Program Manager"), hp.Opportunity("t2", "Beta — Analyst", hp.ACTIVE, 0, "Beta", "Analyst")]
        merged = hp.merge(ledger, tree)
        self.assertEqual([(o.page_id, o.section, o.tree_id) for o in merged], [("r1", hp.ACTIVE, "t1"), ("t2", hp.ACTIVE, "")])    # GWT-RD-2: Beta (no Stage row) is read from the tree

    def test_snapshot_with_a_ledger_is_unreadable_if_either_read_fails(self):
        tree = {"root": [page("o1", "Acme — PM")], "o1": []}
        good = FakeLedgerClient([[row("r1", "Beta", "PM", "Applied")]])
        got, status = hp.snapshot(Fake(tree), "root", ledger=good)
        self.assertEqual((status, sorted(o.page_id for o in got)), ("ok", ["o1", "r1"]))
        self.assertEqual(hp.snapshot(Fake(tree), "root", ledger=FakeLedgerClient([[]], bad={"results": []})), ([], "unreadable"))     # GWT-RD-3
        self.assertEqual(hp.snapshot(Fake(tree), "root")[1], "ok")                                                                       # no ledger: the page tree alone, as before

    def test_a_ledger_pursuit_keeps_its_tree_page_id_for_the_writers(self):
        from lifeos.sources import hiring as stage
        tree = {"root": [page("o1", "Acme — Program Manager")], "o1": []}
        out = stage.read_parents(Fake(tree), "root", FakeLedgerClient([[row("r1", "Acme", "Program Manager", "Applied"), row("r2", "Beta", "Analyst", "Applied")]]))
        self.assertEqual({p["company"]: (p["id"], p["row_id"]) for p in out}, {"Acme": ("o1", "r1"), "Beta": ("r2", "r2")})      # LIFE-353: the decision writers act on row_id
