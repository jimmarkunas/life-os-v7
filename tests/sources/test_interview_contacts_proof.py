"""LIFE-88 E10 synthetic QA (lifeos/sources/interview_contacts_proof.py), run exactly as the manual interview-proof dispatch runs it (confirm = E10), on the production-shaped fakes:
the Interview pages on the journey's Notion fake and the sandbox Job Ledger as a Notion data source on the same client."""
import copy
import json
import unittest
from datetime import datetime

from lifeos.interview.ui_probe import LEDGER_ROW, QA_PARENT
from lifeos.platform.notion_client import NotionError
from lifeos.sources import interview_contacts_proof as proof, interview_evidence as ev
from tests.interview.test_notion import ENV
from tests.sources.test_m1m2_proof import QAWorkspace

NOW = datetime(2026, 10, 25, 22, 0, tzinfo=ev.TZ)
E10 = {**ENV, "INTERVIEW_JOURNEY_PROOF": "E10"}
SMITH = proof.SMITH_PAGE


def para(text, block_id):
    return {"id": block_id, "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "plain_text": text, "text": {"content": text}}]}}


def heading(text, block_id):
    return {"id": block_id, "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "plain_text": text, "text": {"content": text}}]}}


def bullet(text, block_id):
    return {"id": block_id, "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "plain_text": text, "text": {"content": text}}]}}


class E10Workspace(QAWorkspace):
    """The QA workspace plus what Notion does for a Ledger row: it is a page with a body. The production Walmart row and Smith page exist (read only)."""
    def __init__(self, refuse_databases=False, with_production=True):
        super().__init__(refuse_databases)
        rich = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}                                  # noqa: E731
        if with_production:
            self.pages[LEDGER_ROW] = {"id": LEDGER_ROW, "properties": {"Company": rich("ExampleMart"), "Job": {"type": "title", "title": [{"plain_text": "Senior Manager, Operations"}]},
                                                                     "Stable Job Key": rich("examplemart-ops-1")}}
            self.blocks[LEDGER_ROW] = [para("v7-jd:1 | key=examplemart-ops-1", "w0"), para("Lead the operations and implementation teams.", "w1"), para("Own delivery across stores.", "w2")]
            self.pages[SMITH] = {"id": SMITH, "properties": {}}
            self.blocks[SMITH] = [heading("Job Description — Original Posting", "s0"), para("Not connected.", "s1"), heading("Job Description — Recovered Source-Grounded Summary", "s2"),
                                  bullet("Own the entire project lifecycle.", "s3"), bullet("Manage the SOW and schedule.", "s4"), heading("Interview Brief", "s5"), para("Not job description.", "s6")]

    def call(self, method, path, body=None):
        out = super().call(method, path, body)
        if method == "POST" and path == "/pages" and body["parent"].get("type") == "data_source_id":
            self.blocks.setdefault(out["id"], [])
        if method == "PATCH" and path.startswith("/blocks/") and not path.endswith("/children") and "to_do" in (body or {}):
            self.find(path.split("/")[2])["to_do"]["checked"] = bool(body["to_do"].get("checked"))                     # as Notion keeps a ticked box
        return out


def run_case(**kwargs):
    client = E10Workspace(**kwargs)
    production = copy.deepcopy((client.pages[LEDGER_ROW], client.blocks[LEDGER_ROW], client.pages[SMITH], client.blocks[SMITH])) if LEDGER_ROW in client.pages else None
    counts = proof.run(E10, False, now=NOW, client=client, reader=client)
    return counts, client, production


class E10Proof(unittest.TestCase):
    def test_every_assertion_passes_and_production_is_only_read(self):
        counts, client, production = run_case()
        self.assertEqual((counts["proof"], counts["synthetic"]), ("e10", True))
        self.assertEqual(counts["failed"], 0, json.dumps(counts["assertions"], indent=1))
        self.assertEqual(counts["passed"], 19)
        self.assertEqual(counts["pages"], 5)
        self.assertEqual(production, copy.deepcopy((client.pages[LEDGER_ROW], client.blocks[LEDGER_ROW], client.pages[SMITH], client.blocks[SMITH])))        # read only
        (container,) = [b for b in client.blocks[QA_PARENT] if b.get("type") == "child_page"]
        self.assertTrue(container["child_page"]["title"].endswith("(SYNTHETIC, QA-only)"))                                                            # one fresh sandbox, labelled
        outside = {p["parent"].get("page_id") for p in client.posts} - {QA_PARENT}
        self.assertNotIn(LEDGER_ROW, outside)
        self.assertNotIn(SMITH, outside)

    def test_the_three_fictional_contacts_and_their_relevance_are_what_the_table_shows(self):
        counts, client, _ = run_case()
        text = json.dumps(client.blocks)
        for name in ("Avery Synthetic", "Blake Synthetic", "Casey Synthetic"):
            self.assertIn(name, text)
        self.assertIn("verify first", text)                                                                                       # the stale one says so
        self.assertEqual(counts["assertions"]["2a positive: the LinkedIn Contacts table shows exactly the three fictional contacts with their relevance lines"], "PASS")
        self.assertEqual(counts["assertions"]["7c positive: the refreshed table shows only the two undismissed contacts"], "PASS")

    def test_counts_only_never_names_ids_links_or_people(self):
        counts, *_ = run_case()
        text = json.dumps(counts)
        for private in ("Synthetic", "Avery", "careers.walmart.com", "indeed.com", "examplemart", "created-", QA_PARENT, LEDGER_ROW, SMITH):
            if private != "Synthetic":
                self.assertNotIn(private, text)
        self.assertNotIn("Avery Synthetic", text)

    def test_a_missing_production_description_is_a_failed_assertion_never_a_silent_pass(self):
        client = E10Workspace()
        client.blocks[SMITH] = [heading("Something else", "x")]
        counts = proof.run(E10, False, now=NOW, client=client, reader=client)
        self.assertEqual(counts["assertions"]["0a the Walmart and Smith job descriptions are read (read only) from their production pages"], "FAIL")
        self.assertGreater(counts["failed"], 0)

    def test_a_sandbox_that_cannot_be_built_fails_closed_and_claims_nothing(self):
        counts, *_ = run_case(refuse_databases=True)
        self.assertEqual((counts["proof"], counts["failed"], counts["code"]), ("blocked", 1, "sandbox:NOTION_HTTP_403"))
        self.assertNotIn("assertions", counts)

    def test_it_refuses_without_its_own_confirmation_or_with_actually_write_ticked(self):
        client = E10Workspace()
        self.assertEqual(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, False, client=client)["code"], "proof_confirm_missing")
        self.assertEqual(proof.run(E10, True, client=client)["code"], "proof_requires_dry_dispatch")

    def test_the_interview_step_routes_e10_to_this_proof_only(self):
        client = E10Workspace(refuse_databases=True)
        self.assertEqual(ev.run(20, False, environ=E10, client=client, now=NOW)["proof"], "blocked")
        self.assertEqual(ev.run(20, False, environ={**E10, "INTERVIEW_JOURNEY_PROOF": "M1M2"}, client=client, now=NOW)["proof"], "blocked")


class Reader(unittest.TestCase):
    def test_an_unreadable_page_raises_the_injected_fault_only_for_its_blocks(self):
        client = E10Workspace()
        client.blocks["page-x"] = []
        fault = proof.Unreadable(client, "page-x")
        with self.assertRaises(NotionError):
            fault.call("GET", "/blocks/page-x/children?page_size=100")
        self.assertEqual(fault.call("GET", f"/blocks/{LEDGER_ROW}/children?page_size=100")["results"][0]["id"], "w0")


if __name__ == "__main__":
    unittest.main()
