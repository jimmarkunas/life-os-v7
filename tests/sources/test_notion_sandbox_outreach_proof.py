"""LIFE-10 §2: the synthetic sandbox proof of the job-page outreach writer, against a fake Notion tree. Every name and id is invented; nothing real is reached."""
import contextlib
import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from lifeos import run as runner
from lifeos.platform import report_region as R
from lifeos.platform.notion_client import NotionError
from lifeos.sources import job_page_outreach as J
from lifeos.sources import notion_sandbox_outreach_proof as P
from lifeos.sources.notion_sandbox_access import SANDBOX_SOURCE
from tests.kit.tree import Tree

ON = {"PROOF_CONFIRM": "SYNTHETIC"}
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=J.CT)


class Sandbox(Tree):
    """The sandbox data source and its pages on top of the block tree: a title query, page reads, and the one page create."""
    source = SANDBOX_SOURCE

    def __init__(self):
        super().__init__()
        self.pages, self.rows, self.queries = set(), {}, []

    def query_data_source(self, source_id, body):
        self.queries.append((source_id, body))
        assert source_id == SANDBOX_SOURCE and body["filter"] == {"property": "Job", "title": {"equals": P.TITLE}}
        return {"results": [{"id": pid} for pid, title in self.rows.items() if title == P.TITLE], "has_more": False}

    def get_page(self, page_id):
        if page_id not in self.rows:
            raise NotionError("NOTION_HTTP_404")
        return {"id": page_id, "parent": {"type": "data_source_id", "data_source_id": SANDBOX_SOURCE}, "archived": False}

    def call_once(self, method, path, body=None, *rest):
        if (method, path) == ("POST", "/pages"):
            self.log.append((method, path))
            assert body["parent"] == {"type": "data_source_id", "data_source_id": SANDBOX_SOURCE}
            pid = f"{0xfeed0000 + len(self.rows):032x}"                 # never collides with the tree's own block ids
            self.pages.add(pid)
            self.rows[pid] = body["properties"]["Job"]["title"][0]["text"]["content"]
            for child in body["children"]:
                self._make(pid, child)
            return {"id": pid}
        return super().call_once(method, path, body)

    def writes(self):
        return [entry for entry in self.log if entry[0] != "GET"]


class OutreachProof(unittest.TestCase):
    def test_one_live_run_creates_the_page_then_proves_create_replay_update_replay(self):
        box = Sandbox()
        done = P.run(0, True, environ=ON, client=box, now=NOW)
        self.assertEqual(done, {"notion_sandbox_outreach_proof": "live", "page": "created", "steps": [{"created": 1}, {"unchanged": 1}, {"updated": 1}, {"unchanged": 1}],
                                "region_kept": True, "needs_action_first": True, "writes": len(box.writes()), "failed": 0})
        (pid,) = box.rows
        top = box.kids[pid]
        kinds = [box.blocks[b]["type"] for b in top]
        self.assertEqual(kinds, ["paragraph", "paragraph", "toggle", "heading_3", "table", "paragraph"])     # the region sits right after Network Leads, the notes stay last
        heading, table = top[3], top[4]
        self.assertEqual(R.plain(box.blocks[heading]), "🤝 Outreach (Updated 10/20 @ 9:01 AM CT)")
        self.assertEqual([R.cells_of(box.blocks[r])[0].split("\n")[0] for r in box.kids[table]], ["Person — Employer", "Blake Synthetic", "Avery Synthetic"])
        self.assertTrue(all(path.startswith(("/pages", f"/blocks/{pid}/children", f"/blocks/{heading}", f"/blocks/{table}", "/blocks/new-")) for _, path in box.writes()))

    def test_a_second_run_finds_the_region_and_refuses_before_writing(self):
        box = Sandbox()
        P.run(0, True, environ=ON, client=box, now=NOW)
        before = len(box.writes())
        with self.assertRaises(NotionError) as caught:
            P.run(0, True, environ=ON, client=box, now=NOW)
        self.assertEqual((str(caught.exception), len(box.writes())), ("NOTION_SANDBOX_PROOF_ALREADY_RUN", before))

    def test_an_existing_page_without_a_region_is_reused_and_two_pages_are_refused(self):
        box = Sandbox()
        box.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": SANDBOX_SOURCE}, "properties": P.page_properties(), "children": P.page_body()})
        self.assertEqual(P.run(0, True, environ=ON, client=box, now=NOW)["page"], "reused")
        box = Sandbox()
        for _ in range(2):
            box.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": SANDBOX_SOURCE}, "properties": P.page_properties(), "children": P.page_body()})
        with self.assertRaises(NotionError) as caught:
            P.run(0, True, environ=ON, client=box, now=NOW)
        self.assertEqual(str(caught.exception), "NOTION_SANDBOX_PROOF_PAGE_NOT_UNIQUE")

    def test_without_live_it_only_reports_and_without_the_confirmation_it_refuses(self):
        box = Sandbox()
        self.assertEqual(P.run(0, False, environ=ON, client=box, now=NOW)["page"], "would_create")
        self.assertEqual(box.writes(), [])
        with self.assertRaises(NotionError) as caught:
            P.run(0, True, environ={"PROOF_CONFIRM": "yes"}, client=box, now=NOW)
        self.assertEqual((str(caught.exception), box.writes()), ("NOTION_SANDBOX_PROOF_NOT_CONFIRMED", []))

    def test_a_client_on_any_other_data_source_is_refused(self):
        box = Sandbox()
        box.source = "9" * 32
        with self.assertRaises(NotionError) as caught:
            P.run(0, True, environ=ON, client=box, now=NOW)
        self.assertEqual((str(caught.exception), box.writes()), ("NOTION_SANDBOX_PROOF_NOT_SANDBOX", []))

    def test_the_conversations_never_reach_the_recruiters_store(self):
        talks = P.Conversations()
        self.assertFalse(hasattr(talks, "call_once") or hasattr(talks, "update_page_properties"))
        with self.assertRaises(NotionError):
            talks.query_data_source("production-recruiters", {"filter": {"property": "Job"}})

    def test_the_default_client_uses_the_vs_code_token_on_the_sandbox_source(self):
        made = {}
        def capture(environ, **kw):
            made.update(environ=dict(environ), **kw)
            raise NotionError("STOP")
        with patch.object(P, "Client", side_effect=capture), self.assertRaises(NotionError):
            P.run(0, True, environ={**ON, "NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "production"})
        self.assertEqual(made, {"environ": {"NOTION_API_TOKEN": "t", "SANDBOX_SOURCE": SANDBOX_SOURCE}, "source_name": "SANDBOX_SOURCE"})

    def test_a_failed_expectation_fails_the_step_with_a_fixed_code(self):
        out, err = io.StringIO(), io.StringIO()
        counts = {"notion_sandbox_outreach_proof": "live", "steps": [{"created": 1}], "failed": 1}
        with patch.dict(runner.STAGES, {"notion-sandbox-outreach-proof": lambda limit, live: counts}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main(["notion-sandbox-outreach-proof", "--limit", "0", "--live"])
        self.assertEqual((code, err.getvalue()), (1, "NOTION-SANDBOX-OUTREACH-PROOF FAILED: NOTION_SANDBOX_PROOF_FAILED\n"))
        self.assertEqual(json.loads(out.getvalue().split(": ", 1)[1])["failed"], 1)


if __name__ == "__main__":
    unittest.main()
