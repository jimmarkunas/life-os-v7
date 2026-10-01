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
