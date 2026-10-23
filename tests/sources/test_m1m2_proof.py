"""LIFE-78 M1/M2 synthetic exit (lifeos/sources/m1m2_proof.py), run exactly as the manual interview-proof dispatch runs it, on production-shaped fakes:
the Interview pages on the journey's Notion fake, the sandbox Ledger and Recruiters as Notion data sources, the Daily Report region on the block-tree fake."""
import copy
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from lifeos.hiring import models as HM
from lifeos.platform.notion_client import NotionError
from lifeos.sources import interview_evidence as ev, m1m2_proof as proof
from lifeos.interview.ui_probe import QA_PARENT
from tests.interview.test_notion import ENV, page
from tests.kit.tree import Tree
from tests.sources.test_interview_journey import NotionLike
from tests.sources.test_interview_journey_proof import qa_workspace

NOW = datetime(2026, 10, 23, 13, tzinfo=ev.TZ)
M1M2 = {**ENV, "INTERVIEW_JOURNEY_PROOF": "M1M2"}


def _plain(runs):
    return "".join(r.get("plain_text") or (r.get("text") or {}).get("content", "") for r in runs or [])


class Sources:
    """Notion data sources as the API answers them: create a database, read its schema, create / read / update its pages, filtered queries."""
    def __init__(self, base, refuse_databases=False):
        self.base, self.sources, self.rows, self.refuse = base, {}, {}, refuse_databases

    def database(self, body):
        if self.refuse:
            raise NotionError("NOTION_HTTP_403")
        ds = f"{len(self.sources) + 1:032x}"
        self.sources[ds] = {n: (next(iter(spec)), next(iter(spec.values()))) for n, spec in body["initial_data_source"]["properties"].items()}
        return {"id": f"db{ds[2:]}", "data_sources": [{"id": ds}]}

    def schema(self, ds):
        if ds not in self.sources:
            raise NotionError("NOTION_HTTP_404")
        return {"properties": {n: {"type": k, k: copy.deepcopy(cfg)} for n, (k, cfg) in self.sources[ds].items()}}

    def create(self, ds, props):
        if ds not in self.sources:
            raise NotionError("NOTION_HTTP_404")
        pid = f"{0xD000 + len(self.rows) + 1:032x}"
        stored = {}
        for name, (kind, _) in self.sources[ds].items():
            value = copy.deepcopy((props.get(name) or {}).get(kind, [] if kind in ("title", "rich_text", "multi_select", "relation") else False if kind == "checkbox" else None))
            if kind in ("title", "rich_text"):
                value = [{"type": "text", "plain_text": _plain([r]), "text": r.get("text", {})} for r in value]
            stored[name] = {"type": kind, kind: value}
        unknown = set(props) - set(self.sources[ds])
        if unknown:
            raise NotionError("NOTION_HTTP_400")
        self.rows[pid] = {"id": pid, "parent": {"type": "data_source_id", "data_source_id": ds}, "properties": stored}
        return copy.deepcopy(self.rows[pid])

    def update(self, pid, props):
        page = self.rows[pid]
        for name, value in props.items():
            kind = next(iter(value))
            page["properties"][name] = {"type": kind, kind: copy.deepcopy(value[kind])}
        return copy.deepcopy(page)

    def query(self, ds, body):
        if ds not in self.sources:
            raise NotionError("NOTION_HTTP_404")
        rows = [p for p in self.rows.values() if p["parent"]["data_source_id"] == ds and self.match(p, (body or {}).get("filter"))]
        return {"results": copy.deepcopy(rows), "has_more": False, "next_cursor": None}

    def match(self, page, where):
        if not where:
            return True
        if "or" in where:
            return any(self.match(page, w) for w in where["or"])
        prop = page["properties"].get(where["property"]) or {}
        kind = prop.get("type")
        value = prop.get(kind)
        if "rich_text" in where or "title" in where:
            return _plain(value) == (where.get("rich_text") or where.get("title"))["equals"]
        if "url" in where:
            return value == where["url"]["equals"]
        if "checkbox" in where:
            return bool(value) is where["checkbox"]["equals"]
        if "select" in where:
            return (value or {}).get("name") == where["select"]["equals"]
        if "date" in where:
            return ((value or {}).get("start") or "")[:10] == where["date"]["equals"]
        if "relation" in where:
            return where["relation"]["contains"] in [r["id"] for r in value or []]
        raise AssertionError(where)


class QAWorkspace(NotionLike):
    """The QA workspace: the journey's Notion fake for pages, plus data sources on the same client (as the real token reaches both). A copy aimed at
    another data source (the proof's _scoped) shares the same state, like a second client on the same workspace."""
    def __init__(self, refuse_databases=False):
        super().__init__()
        self.pages[QA_PARENT] = page("QA — Interview OS / Opportunity Parent")
        self.blocks[QA_PARENT] = []
        self.sources_fake, self.source = Sources(self, refuse_databases), ""

    def call(self, method, path, body=None):
        sources = self.sources_fake
        parts = path.split("?")[0].strip("/").split("/")
        if (method, path) == ("POST", "/databases"):
            return sources.database(body)
        if parts[0] == "data_sources":
            return sources.query(parts[1], body) if method == "POST" else sources.schema(parts[1])
        if parts[0] == "pages" and len(parts) == 2 and parts[1] in sources.rows:
            return sources.update(parts[1], body["properties"]) if method == "PATCH" else copy.deepcopy(sources.rows[parts[1]])
        if method == "POST" and path == "/pages" and body["parent"].get("type") == "data_source_id":
            return sources.create(body["parent"]["data_source_id"], body["properties"])
        return super().call(method, path, body)

    def call_once(self, method, path, body=None, version=None):
        return self.call(method, path, body)

    def query_data_source(self, source_id=None, body=None):
        return self.call("POST", f"/data_sources/{source_id or self.source}/query", body)

    def get_page(self, pid):
        return self.call("GET", f"/pages/{pid}")

    def update_page_properties(self, pid, props):
        return self.call("PATCH", f"/pages/{pid}", {"properties": props})

    def create(self, props, blocks):
        return self.sources_fake.create(self.source, props)["id"]


def workspace(refuse_databases=False):
    client = QAWorkspace(refuse_databases)
    return client, client.sources_fake


def report_tree():
    """The synthetic Daily Report page, on the block-tree fake the Hiring card is tested on."""
    tree = Tree()
    tree.add("0000000000000000000000000000c004", "callout", "page-1")
    tree.add("0000000000000000000000000000c001", "heading_3", "page-1", text=HM.TITLE)
    tree.table("0000000000000000000000000000c003", "page-1", [list(HM.HEADERS)])
    tree.add("0000000000000000000000000000c005", "divider", "page-1")
    pins = {"JIRA_CARD_BLOCK_ID": "0000000000000000000000000000c004", "HIRING_CARD_BLOCK_ID": "0000000000000000000000000000c001",
            "HIRING_TABLE_BLOCK_ID": "0000000000000000000000000000c003"}
    return tree, pins


class Scoped(unittest.TestCase):
    def run_case(self, refuse=False):
        client, sources = workspace(refuse)
        tree, pins = report_tree()
        real_pipeline = [dict(b) for b in client.blocks["root"]]
        with patch.object(proof, "report_page", lambda c, container: pins):
            counts = proof.run(M1M2, False, now=NOW, client=client, reader=client, report=tree)
        return counts, client, sources, tree, real_pipeline


class M1M2Proof(Scoped):
    def test_every_assertion_passes_on_native_sandbox_surfaces_and_nothing_outside_the_sandbox_is_written(self):
        counts, client, sources, tree, real_pipeline = self.run_case()
        self.assertEqual(counts["proof"], "m1m2")
        self.assertEqual(counts["failed"], 0, json.dumps(counts["assertions"], indent=1))
        self.assertEqual(counts["passed"], 33)
        self.assertEqual(client.blocks["root"], real_pipeline)                                 # the real Hiring Pipeline (here "root") is never touched
        self.assertNotIn("root", {p["parent"].get("page_id") for p in client.posts})
        self.assertEqual(len(sources.sources), 2)                                              # the sandbox's own Ledger and Recruiters, nothing else
        ledger_rows = [p for p in sources.rows.values() if p["parent"]["data_source_id"] == f"{1:032x}"]
        self.assertEqual(len(ledger_rows), 1)
        self.assertGreater(counts["hiring_writes"], 0)

    def test_a_sandbox_that_cannot_be_built_fails_closed_and_claims_nothing(self):
        counts, *_ = self.run_case(refuse=True)
        self.assertEqual((counts["proof"], counts["failed"], counts["code"]), ("blocked", 1, "sandbox:NOTION_HTTP_403"))
        self.assertNotIn("assertions", counts)

    def test_counts_only_never_names_ids_links_or_people(self):
        counts, *_ = self.run_case()
        text = json.dumps(counts)
        for private in ("example.test", "Riley", "Staffing Example", "created-", "SampleWorks"):
            self.assertNotIn(private, text)

    def test_the_interview_step_routes_m1m2_to_this_proof_only(self):
        client, _ = workspace(refuse_databases=True)
        self.assertEqual(ev.run(20, False, environ=M1M2, client=client, now=NOW)["proof"], "blocked")
        self.assertEqual(ev.run(20, False, environ={**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, client=qa_workspace())["proof"], "journey")

    def test_it_refuses_without_its_own_confirmation_or_with_actually_write_ticked(self):
        client = qa_workspace()
        self.assertEqual(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, False, client=client)["code"], "proof_confirm_missing")
        self.assertEqual(proof.run(M1M2, True, client=client)["code"], "proof_requires_dry_dispatch")
        self.assertEqual(client.posts, [])


if __name__ == "__main__":
    unittest.main()
