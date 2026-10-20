"""LIFE-46: the accepted UI 3b opportunity parent and the one-time `LIFE OS: adopt` of Jim's own page."""
import copy
import json
import unittest
from lifeos.interview import create, notion, stage
from lifeos.interview.models import Ownership, ParentQuery, RoundQuery
from lifeos.interview.template import opportunity_page
from lifeos.platform.notion_client import NotionError
from tests.interview.test_create import PARENT, ROUND, WritableFake, machine_parent
from tests.interview.test_notion import ENV, block, page

LEGACY = ParentQuery("Legacy", "Manager")
PENDING = RoundQuery(True, None, None, 1, source="https://mail.google.com/mail/u/0/#all/synthetic", stable_job_key="legacy-key",
                     ledger_page_id="ledger-row")


class AdoptFake(WritableFake):
    """Adds the two calls adoption makes: insert after a block (PATCH), and remove the adopt line (DELETE)."""
    def __init__(self, first="LIFE OS: adopt"):
        super().__init__()
        self.blocks["human"] = [block(first, id="h0"), block("Current stage", "heading_3", "h1"),
                                block("Waiting on recruiter.", id="h2", has_children=True), block("ERP notes", id="h3")]
        self.blocks["h2"] = [block("nested human detail", id="h2a")]
        self.patches = []
        self.append_error = self.marker_error = self.touch_human = False

    def call(self, method, path, body=None):
        if method not in ("PATCH", "DELETE"):
            return super().call(method, path, body)
        self.calls.append((method, path))
        self.patches.append((path, copy.deepcopy(body)))
        key = path.split("/")[2]
        if method == "PATCH":
            if self.append_error:
                raise NotionError("private error body")
            assert path.endswith("/children") and set(body) == {"children", "after"}
            items = self.register(f"{key}-new-{len(self.patches)}", body["children"])
            at = [b["id"] for b in self.blocks[key]].index(body["after"]) + 1
            self.blocks[key][at:at] = items
            if self.touch_human:
                self.blocks[key][-1] = block("edited", id="h3")
            return {"results": items}
        if self.marker_error:
            raise NotionError("private error body")
        assert body is None
        for blocks in self.blocks.values():
            blocks[:] = [item for item in blocks if item["id"] != key]
        return {"id": key, "in_trash": True}


def run(client, live=True, parent=LEGACY, query=PENDING):
    return stage.run(10, live, environ=ENV, client=client, evidence=[(parent, query)])


def depth(items, level=0):
    deepest = level
    for item in items:
        inner = (item.get(item["type"]) or {}).get("children") or []
        deepest = max(deepest, depth(inner, level + 1) if inner else level)
    return deepest


class OpportunityTemplateTests(unittest.TestCase):
    def test_new_parent_is_the_accepted_ui_3b_template(self):
        blocks = opportunity_page(PARENT, PENDING, "root-page", notion.MARKERS["opportunity"])
        self.assertEqual((blocks[0]["type"], notion.text(blocks[0]), blocks[0]["toggle"]["color"]), ("toggle", notion.FOLD, "gray"))
        self.assertEqual([notion.text(b) for b in blocks[0]["toggle"]["children"]], [notion.MARKERS["opportunity"]])
        self.assertTrue(notion.text(blocks[1]).startswith("Canonical opportunity parent"))
        self.assertEqual(blocks[2]["type"], "column_list")
        left, right = (c["column"]["children"] for c in blocks[2]["column_list"]["children"])
        self.assertEqual([notion.text(b).split("\n")[0] for b in left],
                         ["Role Snapshot", "Interview Timeline · evidence versus demo", "Follow-up · owner and evidence", "Working strategy / next action"])
        self.assertEqual([notion.text(b).split("\n")[0] for b in right],
                         ["Quick navigation", "Role context · highlights (not full JD)", "Contacts", "Source health · freshness", "Canonical Ownership", "Guardrails"])
        heads = [(b["type"], notion.text(b)) for b in blocks if b["type"].startswith("heading")]
        self.assertEqual(heads, [("heading_2", "Interviews"), ("heading_2", "Strategy"), ("heading_2", "LinkedIn & Outreach"),
                                 ("heading_2", "Materials & Sources"), ("heading_3", "Job Description — original source (separate from Strategy)"),
                                 ("heading_2", notion.JIM_NOTES)])
        self.assertTrue(all(b["heading_2"]["is_toggleable"] for b in blocks[3:7]))
        self.assertEqual([b["type"] for b in blocks[-3:]], ["heading_2", "callout", "paragraph"])
        self.assertLessEqual(depth(blocks), 2)                                 # Notion's per-request nesting limit
        page = json.dumps(blocks, ensure_ascii=False)
        for fact in ("Scheduling Pending", "legacy-key", "ledgerrow", "https://mail.google.com/mail/u/0/#all/synthetic", "Not verified"):
            self.assertIn(fact, page.replace("ledger-row", "ledgerrow"))
        for demo in ("ExampleCo", "Smartsheet", "KellyMitchell", "Demo Contact", "Oct 20", "Rituraj", "SANDBOX"):
            self.assertNotIn(demo, page)
        adopted = opportunity_page(PARENT, PENDING, "root-page", "m", adopted=True)
        self.assertEqual((notion.text(adopted[0]["toggle"]["children"][0]), adopted[1:]), ("m", blocks[1:-1]))
        unlinked = json.dumps(opportunity_page(PARENT, ROUND, "root-page", "m"), ensure_ascii=False)
        self.assertIn("exact row not linked", unlinked)
        self.assertNotIn("Request message ↗", unlinked)

    def test_jim_notes_region_runs_to_the_end_and_rounds_never_touch_it(self):
        client = WritableFake()
        parent_id = machine_parent(client)
        blocks = client.blocks[parent_id]
        blocks += [block("My own heading", "heading_2", "mine-h"), block("my private note", id="mine")]
        before = notion.protected_snapshot(client, parent_id, "opportunity")
        self.assertIsNotNone(before)
        self.assertIsNone(notion.protected_snapshot(client, parent_id))   # a round page still needs Live Notes + Raw Notes
        result = run(client, parent=PARENT, query=ROUND)
        self.assertEqual((result["rounds_created"], result["readback_ok"]), (1, 1))
        self.assertEqual(client.blocks[parent_id][-1]["type"], "child_page")
        self.assertEqual(notion.protected_snapshot(client, parent_id, "opportunity"), before)
        client.blocks[parent_id][-2] = block("changed", id="mine")
        self.assertNotEqual(notion.protected_snapshot(client, parent_id, "opportunity"), before)

    def test_pages_written_before_the_fold_are_still_read(self):
        client = WritableFake()
        parent_id = machine_parent(client)
        run(client, parent=PARENT, query=ROUND)
        for page_id in (parent_id, "created-2"):                                # unfold: the two lines as plain paragraphs
            fold = client.blocks[page_id].pop(0)
            client.blocks[page_id][0:0] = client.blocks.pop(fold["id"])
        self.assertEqual(notion.text(client.blocks["created-2"][0]), notion.MARKERS["round"])
        self.assertEqual(notion.ownership(client, parent_id, "opportunity"), Ownership.MACHINE)
        self.assertEqual(notion.explicit_child(client, "created-2").ordinal, 1)
        result = run(client, parent=PARENT, query=ROUND)
        self.assertEqual((result["writes"], result["why"]), (0, {"child_match": 1}))

    def test_legacy_skeleton_parent_still_protected_by_live_and_raw_notes(self):
        client = WritableFake()
        self.assertIsNotNone(notion.protected_snapshot(client, "p", "opportunity"))


class AdoptTests(unittest.TestCase):
    def test_dry_run_reports_and_never_writes(self):
        client = AdoptFake()
        self.assertEqual(notion.ownership(client, "human", "opportunity"), Ownership.ADOPT)
        result = run(client, live=False)
        self.assertEqual((result["writes_planned"], result["writes"], result["why"]), (1, 0, {"adopt_allowed": 1}))
        self.assertEqual((client.patches, client.posts, result["blocked"]), ([], [], 0))

    def test_live_adoption_once_then_rounds_on_the_next_run(self):
        client = AdoptFake()
        original = copy.deepcopy(client.blocks["human"][1:])
        result = run(client)
        self.assertEqual((result["writes"], result["parents_adopted"], result["readback_ok"], result["blocked"]), (1, 1, 1, 0))
        self.assertEqual(result["why"], {"parent_adopted": 1})
        self.assertEqual(client.posts, [])                                     # no round in the adoption run
        self.assertEqual([c for c in client.calls if c[0] != "GET"], [("PATCH", "/blocks/human/children"), ("DELETE", "/blocks/h0")])
        self.assertEqual(client.patches[0][1]["after"], "h0")
        page = client.blocks["human"]
        self.assertNotIn("h0", [b["id"] for b in page])                       # the adopt line is gone; the fold is first
        self.assertEqual(notion.text(page[0]), notion.FOLD)
        self.assertRegex(notion.text(client.blocks[page[0]["id"]][0]), r"^v7-interview:1;owner=machine;kind=opportunity;adopted=\d{4}-\d{2}-\d{2}$")
        self.assertEqual(notion.ownership(client, "human", "opportunity"), Ownership.MACHINE)
        notes = [notion.text(b) for b in page].index(notion.JIM_NOTES)
        self.assertEqual(page[notes + 1]["type"], "callout")
        self.assertEqual(page[notes + 2:], original)                           # Jim's blocks: same ids, order and content
        self.assertNotEqual(notion.text(page[-1]), "Write here.")
        replay = run(client)
        self.assertEqual((replay["rounds_created"], replay["readback_ok"]), (1, 1))
        self.assertEqual(len(client.patches), 2)
        self.assertEqual(client.blocks["human"][notes + 2:-1], original)
        again = run(client)
        self.assertEqual((again["writes"], again["why"]), (0, {"child_match": 1}))

    def test_marker_rules_never_adopt(self):
        cases = (("LIFE OS: Adopt", "human_page"), ("LIFE OS: adopt ", "human_page"), ("adopt", "human_page"))
        for first, code in cases:
            with self.subTest(first=first):
                client = AdoptFake(first)
                result = run(client)
                self.assertIn(code, result["why"])
                self.assertEqual((client.patches, client.posts), ([], []))
        for mode in ("second", "notes", "machine", "later"):
            with self.subTest(mode=mode):
                client = AdoptFake("human first line" if mode == "later" else "LIFE OS: adopt")
                extra = {"second": block("LIFE OS: adopt", id="x"), "notes": block(notion.JIM_NOTES, "heading_2", "x"),
                         "machine": block(notion.FOLD, "toggle", "x"), "later": block("LIFE OS: adopt", id="x")}[mode]
                client.blocks["human"].append(extra)
                result = run(client)
                self.assertIn("human_page" if mode == "later" else "adopt_conflict", result["why"])
                self.assertEqual(result["blocked"], 1)
                self.assertEqual((client.patches, client.posts), ([], []))
        client = AdoptFake()
        client.blocks["human"][0]["type"] = "heading_1"
        client.blocks["human"][0]["heading_1"] = client.blocks["human"][0].pop("paragraph")
        self.assertIn("human_page", run(client)["why"])
        client = AdoptFake()
        self.assertIn("pursuit_unconfirmed", run(client, query=RoundQuery(False))["why"])
        self.assertEqual(client.patches, [])

    def test_undo_by_deleting_the_ownership_line(self):
        client = AdoptFake()
        run(client)
        self.assertEqual(notion.text(client.blocks["human"].pop(0)), notion.FOLD)   # undo = delete the folded LIFE OS toggle
        patches = len(client.patches)
        result = run(client)
        self.assertIn("human_page", result["why"])
        self.assertEqual((len(client.patches), client.posts, result["writes"]), (patches, [], 0))
        client.blocks["human"].insert(0, block("LIFE OS: adopt", id="again"))
        self.assertIn("adopt_conflict", run(client)["why"])                   # never a second copy of the sections
        self.assertEqual(len(client.patches), patches)

    def test_adopted_marker_shape(self):
        base = notion.MARKERS["opportunity"]
        self.assertTrue(notion.is_marker(base + ";adopted=2026-10-20", "opportunity"))
        for value in (base + ";adopted=2026-13-01", base + ";adopted=2026-10-20;x", base + ";adopted=", base + "adopted=2026-10-20"):
            self.assertFalse(notion.is_marker(value, "opportunity"))
        self.assertFalse(notion.is_marker(notion.MARKERS["round"] + ";adopted=2026-10-20", "round"))

    def test_write_failures_never_retry_and_fail_closed(self):
        client = AdoptFake()
        client.append_error = True
        result = run(client)
        self.assertEqual((result["why"], result["writes"]), ({"write_failed": 1}, 0))
        self.assertEqual(notion.text(client.blocks["human"][0]), "LIFE OS: adopt")
        client = AdoptFake()
        client.marker_error = True
        result = run(client)
        self.assertEqual((result["why"], result["writes"], result["readback_failed"]), ({"write_failed": 1}, 1, 1))
        client.marker_error = False
        patches = len(client.patches)
        self.assertIn("adopt_conflict", run(client)["why"])
        self.assertEqual(len(client.patches), patches)
        client = AdoptFake()
        client.touch_human = True
        result = run(client)
        self.assertEqual((result["why"], result["readback_ok"], result["readback_failed"]), ({"readback_protected_changed": 1}, 0, 1))

    def test_counts_privacy(self):
        client = AdoptFake()
        result = run(client)
        for private in ("Legacy", "Manager", "h0", "Waiting on recruiter", "legacy-key", "ledger-row", "mail.google"):
            self.assertNotIn(private, json.dumps(result))



def legacy(client):
    """A LIFE OS opportunity page written before D235, folded (D236) like the real Walmart page, with Interview 1 under it."""
    fold = lambda bid, *lines: {"id": bid, "type": "toggle", "toggle": {"rich_text": [{"plain_text": notion.FOLD}]}, "has_children": True}
    client.blocks["human"] = [fold("f"), block("Live Notes", "heading_2", "lv"), block("my live note", id="n1"),
                              block("Raw Notes", "heading_2", "rw"), block("Interview Rounds", "heading_2", "ir"),
                              {"id": "r1", "type": "child_page", "child_page": {"title": "Interview 1"}}]
    client.blocks["f"] = [block(notion.MARKERS["opportunity"], id="fm")]
    client.pages["r1"] = page("Interview 1", "human")
    client.blocks["r1"] = [fold("rf"), block("Live Notes", "heading_2", "rl"), block("Raw Notes", "heading_2", "rr")]
    client.blocks["rf"] = [block(notion.MARKERS["round"], id="rm"), block(create.identity_text(PENDING), id="ri")]
    return copy.deepcopy(client.blocks["human"][1:])


class UpgradeTests(unittest.TestCase):
    def test_dry_run_then_one_upgrade_then_the_existing_round_matches(self):
        client = AdoptFake()
        original = legacy(client)
        result = run(client, live=False)
        self.assertEqual((result["why"], result["writes_planned"], result["writes"], client.patches), ({"upgrade_allowed": 1}, 1, 0, []))
        result = run(client)
        self.assertEqual((result["why"], result["writes"], result["parents_upgraded"], result["readback_ok"]), ({"parent_upgraded": 1}, 1, 1, 1))
        self.assertEqual([c for c in client.calls if c[0] != "GET"], [("PATCH", "/blocks/human/children")])
        self.assertEqual(client.patches[0][1]["after"], "f")
        page_blocks = client.blocks["human"]
        self.assertEqual(page_blocks[0]["id"], "f")
        self.assertEqual([notion.text(b) for b in page_blocks].count(notion.FOLD), 1)
        notes = [notion.text(b) for b in page_blocks].index(notion.JIM_NOTES)
        self.assertEqual(page_blocks[notes + 1]["type"], "callout")
        self.assertEqual(page_blocks[notes + 2:], original)                   # old headings, notes and Interview 1: untouched
        self.assertTrue(notion.text(page_blocks[1]).startswith("Canonical opportunity parent"))
        self.assertIsNotNone(notion.protected_snapshot(client, "human", "opportunity"))
        replay = run(client)
        self.assertEqual((replay["why"], replay["writes"]), ({"child_match": 1}, 0))
        self.assertEqual((len(client.patches), client.posts), (1, []))

    def test_unrecognized_or_unconfirmed_pages_are_never_upgraded(self):
        for mode in ("raw", "second", "unconfirmed"):
            with self.subTest(mode=mode):
                client = AdoptFake()
                legacy(client)
                if mode == "raw": client.blocks["human"].pop(3)
                if mode == "second": client.blocks["human"].insert(2, block(notion.MARKERS["opportunity"], id="x"))
                result = run(client, query=RoundQuery(False) if mode == "unconfirmed" else PENDING)
                self.assertEqual(result["why"], {"pursuit_unconfirmed" if mode == "unconfirmed" else "upgrade_unrecognized": 1})
                self.assertEqual((client.patches, result["writes"]), ([], 0))

    def test_write_and_readback_failures_fail_closed(self):
        client = AdoptFake()
        legacy(client)
        client.append_error = True
        self.assertEqual(run(client)["why"], {"write_failed": 1})
        client = AdoptFake()
        legacy(client)
        client.touch_human = True
        result = run(client)
        self.assertEqual((result["why"], result["readback_failed"]), ({"readback_protected_changed": 1}, 1))


if __name__ == "__main__":
    unittest.main()
