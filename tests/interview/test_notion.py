import copy
import unittest
from lifeos.interview import notion
from lifeos.interview.models import Ownership
from lifeos.platform.notion_client import NotionError
from tests.interview.support import ENV, Fake, block, page









class NotionTests(unittest.TestCase):
    def test_token_isolation(self):
        self.assertEqual(notion.environment({"NOTION_API_TOKEN": "jobs"})["NOTION_API_TOKEN"], "")
        self.assertEqual(notion.make_client(ENV).token, "synthetic")
        with self.assertRaises(NotionError):
            notion.make_client({"NOTION_API_TOKEN": "jobs"})

    def test_targets(self):
        client = Fake()
        self.assertEqual(notion.target_check(client, ENV), "target_ok")
        for value in (page("Wrong"), page(archived=True), page(in_trash=True)):
            client.pages["root"] = value
            self.assertEqual(notion.target_check(client, ENV), "target_mismatch")
        client.fail.add("/pages/root")
        self.assertEqual(notion.target_check(client, ENV), "target_unreadable")
        self.assertEqual(notion.target_check(client, {}), "target_config_missing")

    def test_ownership_first_block_only(self):
        client = Fake()
        self.assertEqual(notion.ownership(client, "p", "opportunity"), Ownership.MACHINE)
        self.assertEqual(client.calls[-1][1], "/blocks/p/children?page_size=1")
        for marker, expected in (("human", Ownership.HUMAN), ("v7-interview:2;owner=machine", Ownership.UNKNOWN), (notion.MARKERS["round"], Ownership.UNKNOWN)):
            client.blocks["p"][0] = block(marker)
            self.assertEqual(notion.ownership(client, "p", "opportunity"), expected)
        client.fail.add("/blocks/p/children?page_size=1")
        self.assertEqual(notion.ownership(client, "p", "opportunity"), Ownership.UNKNOWN)

    def test_readback_protected_contract(self):
        client = Fake()
        before = notion.protected_snapshot(client, "p")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity", before), "readback_ok")
        client.blocks["p"][2] = block("changed", id="note")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity", before), "readback_protected_changed")
        self.assertEqual(notion.readback(client, "p", "elsewhere", "opportunity"), "readback_ok")           # LIFE-382 (D250-06): where the page sits is not checked; marker and protected content decide
        client.blocks["p"].pop()
        self.assertEqual(notion.readback(client, "p", "root", "opportunity"), "readback_protected_missing")
        client.blocks["p"][0] = block("human")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity"), "readback_marker_missing")
        self.assertEqual(notion.readback(client, "gone", "root", "opportunity"), "readback_page_gone")
        client.fail.add("/pages/p")
        self.assertEqual(notion.readback(client, "p", "root", "opportunity"), "readback_unreadable")

    def test_nested_protection_and_reordering(self):
        client = Fake()
        client.blocks["p"][1]["has_children"] = True
        client.blocks["live"] = [block("nested")]
        before = notion.protected_snapshot(client, "p")
        client.blocks["live"] = [block("nested change")]
        self.assertNotEqual(before, notion.protected_snapshot(client, "p"))
        before = notion.protected_snapshot(client, "p")
        client.blocks["p"] = [client.blocks["p"][0], client.blocks["p"][3], client.blocks["p"][1], client.blocks["p"][2]]
        self.assertNotEqual(before, notion.protected_snapshot(client, "p"))

    def nested(self):
        client = Fake()
        cp = lambda id, title: {"id": id, "type": "child_page", "child_page": {"title": title}}
        client.blocks["p"] = client.blocks["p"] + [{"id": "cols", "type": "column_list", "has_children": True}]
        client.blocks["cols"] = [{"id": "col", "type": "column", "has_children": True}]
        client.blocks["col"] = [cp("n1", "Interview 1: Jane Doe (9/10/26)"), cp("n2", "Meeting notes"), cp("n3", "Interview 2: Joe Roe (9/14/26)")]
        client.pages["n1"], client.pages["n2"], client.pages["n3"] = page(), page(), page()
        client.pages["n1"]["parent"], client.pages["n2"]["parent"], client.pages["n3"]["parent"] = {"block_id": "col"}, {"block_id": "col"}, {"block_id": "elsewhere"}
        for key in ("n1", "n2", "n3"):
            client.blocks[key] = [block("my own words")]
        return client

    def test_a_named_pursuits_hand_made_round_pages_in_jims_columns_count_by_title_only(self):
        client = self.nested()
        found = notion.child_scan(client, "p", legacy=True)
        self.assertTrue(found.complete)
        self.assertEqual([(c.page_id, c.ordinal, c.interview_date, c.legacy) for c in found.items], [("n1", 1, "2026-09-10", True)])    # n2 is not a round title; n3 sits under another block
        self.assertEqual(notion.child_scan(client, "p").items, ())               # an unnamed pursuit's nested pages are never read as rounds
        self.assertTrue(all(method == "GET" for method, _ in client.calls))

    def test_a_listed_child_whose_parent_is_not_the_listed_page_makes_the_scan_incomplete(self):
        """LIFE-382 (D250-04): the parent proof lives here, so resolve_child need not repeat it."""
        client = Fake()
        client.blocks["p"].append({"id": "c", "type": "child_page", "child_page": {"title": "Interview 1"}})
        client.pages["c"] = page(parent="elsewhere")
        self.assertFalse(notion.child_scan(client, "p").complete)

    def test_incomplete_scans_fail_closed(self):
        client = Fake()
        client.more = True
        self.assertFalse(notion.parent_scan(client, "root").complete)
        self.assertFalse(notion.child_scan(client, "p").complete)


def row(rid, company, job, stage, decision=None):
    sel = lambda name: {"select": {"name": name} if name else None}
    return {"id": rid, "properties": {"Company": {"rich_text": [{"plain_text": company}]}, "Job": {"title": [{"plain_text": job}]}, "Stage": sel(stage), "Hiring Decision": sel(decision)}}


class FakeLedger(Fake):
    """The page tree of Fake plus a Job Ledger data source (LIFE-360)."""
    def __init__(self, pages):
        super().__init__()
        self.pages_of_rows, self.queries = pages, []

    def query_data_source(self, source_id=None, body=None):
        self.queries.append((source_id, body))
        index = int((body or {}).get("start_cursor") or 0)
        if index >= len(self.pages_of_rows):
            return {"results": [], "has_more": False, "next_cursor": None}
        more = index + 1 < len(self.pages_of_rows)
        return {"results": self.pages_of_rows[index], "has_more": more, "next_cursor": str(index + 1) if more else None}


LENV = {**ENV, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "abc123"}


class LedgerParentTests(unittest.TestCase):
    def test_rows_with_stage_become_parents(self):
        client = FakeLedger([[row("r1", "Acme", "Director", "Interviewing"), row("r2", "Beta", "Lead", "Ended"), row("r3", "Gamma", "Head", "Offer", "Declined"), row("r4", "", "Nameless", "Applied")]])
        scan = notion.parents_from_ledger(client, "abc123")
        self.assertTrue(scan.complete)
        self.assertEqual([(p.page_id, p.title, p.active) for p in scan.items], [("r1", "Acme - Director", True), ("r2", "Beta - Lead", False), ("r3", "Gamma - Head", False)])
        self.assertEqual(client.queries[0][1]["filter"], {"property": "Stage", "select": {"is_not_empty": True}})

    def test_incomplete_ledger_returns_nothing(self):
        client = FakeLedger([[row("r1", "Acme", "Director", "Applied")]])
        client.query_data_source = lambda source_id=None, body=None: {"results": [], "has_more": True, "next_cursor": None}
        scan = notion.parents_from_ledger(client, "abc123")
        self.assertEqual((scan.items, scan.complete), ((), False))
        client.query_data_source = lambda source_id=None, body=None: (_ for _ in ()).throw(NotionError("NOTION_NETWORK"))
        self.assertFalse(notion.parents_from_ledger(client, "abc123").complete)

    def test_rows_come_first_and_the_tree_fills_the_rest(self):
        client = FakeLedger([[row("r1", "Example", "Program Manager", "Interviewing")]])
        client.blocks["root"].append({"id": "q", "type": "child_page", "child_page": {"title": "Other — Analyst"}})
        scan = notion.parents(client, LENV)
        self.assertTrue(scan.complete)
        self.assertEqual([p.page_id for p in scan.items], ["r1", "q"])                 # the tree page "Example — Program Manager" is the same pursuit as the row
        self.assertEqual((scan.ledger, scan.tree_only), (1, 1))                         # evidence that the Ledger path ran

    def test_without_a_ledger_source_the_page_tree_alone(self):
        client = FakeLedger([[row("r1", "Acme", "Director", "Applied")]])
        scan = notion.parents(client, {k: v for k, v in ENV.items() if k != "NOTION_JOB_LEDGER_DATA_SOURCE_ID"})
        self.assertEqual([p.page_id for p in scan.items], ["p"])
        self.assertEqual(client.queries, [])

    def test_either_read_incomplete_is_incomplete(self):
        client = FakeLedger([[row("r1", "Acme", "Director", "Applied")]])
        client.more = True
        self.assertEqual(notion.parents(client, LENV).items, ())
        ledger_down = FakeLedger([[]])
        ledger_down.query_data_source = lambda source_id=None, body=None: (_ for _ in ()).throw(NotionError("NOTION_NETWORK"))
        self.assertFalse(notion.parents(ledger_down, LENV).complete)


class NotionIdTests(unittest.TestCase):
    def test_format_equality_and_missing_values(self):
        dashed = "abcdefab-cdef-4abc-8def-abcdefabcdef"
        compact = dashed.replace("-", "")
        for left, right, expected in (
            (dashed, compact, True), (dashed.upper(), compact, True),
            (dashed, "fedcbafe-dcba-4abc-8def-abcdefabcdef", False),
            (None, compact, False), (compact, None, False), ("", "", False),
            ("---", "", False), (None, None, False), (1, 1, False),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(notion.same_notion_id(left, right), expected)

    def test_readback_and_child_resolution_use_canonical_parent_ids(self):
        from lifeos.interview.identity import resolve_child
        from lifeos.interview.models import Child, Resolution, RoundQuery, Scan, State
        dashed = "abcdefab-cdef-4abc-8def-abcdefabcdef"
        compact = dashed.replace("-", "")
        client = Fake()
        client.pages["p"]["parent"]["page_id"] = dashed
        self.assertEqual(notion.readback(client, "p", compact, "opportunity"), "readback_ok")
        child = Child(dashed, dashed)
        parent = Resolution(State.MATCH, "parent_match", compact.upper())
        result = resolve_child(parent, RoundQuery(True, explicit_child_page_id=compact), Scan((child,), True), child)
        self.assertEqual(result.code, "child_match")
        result = resolve_child(parent, RoundQuery(True, explicit_child_page_id=compact), Scan((child,), True), Child(dashed, "different"))
        self.assertEqual(result.code, "child_wrong_parent")
