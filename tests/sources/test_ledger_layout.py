"""The flat-to-two-column Ledger page migration: a Notion that behaves like Notion's (nested blocks, `after`, in-place update), and the stored description the pages are checked against."""
import json
import unittest
from unittest import mock

from lifeos.platform import ledger_body as body
from lifeos.sources import ledger_layout as layout
from tests.kit.ledger_pages import FakeLedger

KEY = "a" * 64
DESC = {"summary": "About the role\n• lead the program", "responsibilities": "Own the roadmap", "requirements": "", "qualifications": "Ten years"}
NOTE = "Jim's own note below the description"


def flat(note=True, leads=False):
    """A page as publish wrote it before: the visible marker, the description sections at the top level, then Jim's note."""
    ledger = FakeLedger()
    ledger.add_page("page-1", KEY, description=False)
    ledger.kids["page-1"] = []
    for b in body.legacy_blocks(KEY, DESC):
        ledger.kids["page-1"].append(ledger._new(b["type"], b[b["type"]]["rich_text"]))
    if note:
        ledger.kids["page-1"].append(ledger._new("paragraph", NOTE))
    return ledger


class Migrate(unittest.TestCase):
    def test_a_dry_run_reads_and_writes_nothing(self):
        ledger = flat()
        self.assertEqual(layout.migrate(ledger, "page-1", KEY, DESC, False), "would_migrate")
        self.assertEqual(ledger.writes, [])

    def test_a_flat_page_becomes_the_two_column_page_and_nothing_of_jims_moves(self):
        ledger = flat()
        before_text, note_id = body.text_of(ledger, "page-1"), ledger.kids["page-1"][-1]
        self.assertEqual(layout.migrate(ledger, "page-1", KEY, DESC, True), "migrated")
        top = ledger.top("page-1")
        self.assertEqual([b["type"] for b in top], ["paragraph", "column_list", "paragraph"])
        self.assertEqual((top[2]["id"], ledger.texts("page-1")[0], ledger.texts("page-1")[2]), (note_id, "\u2060", NOTE))      # Jim's note is the same block, still last
        self.assertEqual(body.marker_text(top[0]), f"v7-jd:2 | key={KEY}")
        self.assertEqual(body.text_of(ledger, "page-1"), "About the role\n- lead the program\nResponsibilities\nOwn the roadmap\nQualifications\nTen years")      # the same words (the Summary is now the Overview heading)
        self.assertIn("Own the roadmap", before_text)
        self.assertEqual(layout.migrate(ledger, "page-1", KEY, DESC, True), "done")

    def test_a_page_whose_description_is_not_exactly_what_v7_wrote_is_left_alone(self):
        ledger = flat()
        ledger.blocks[ledger.kids["page-1"][2]]["paragraph"]["rich_text"][0]["plain_text"] = "Jim fixed a typo here"
        self.assertEqual(layout.migrate(ledger, "page-1", KEY, DESC, True), "left")
        self.assertEqual(ledger.writes, [])

    def test_an_interrupted_page_is_finished_without_a_second_column_list(self):
        ledger = flat()
        top = ledger.top("page-1")
        ledger.call("PATCH", "/blocks/page-1/children", {"children": [body.column_list(DESC)], "after": top[0]["id"]})        # step 1 only, then the run died
        self.assertEqual(layout.migrate(ledger, "page-1", KEY, DESC, True), "migrated")
        self.assertEqual([b["type"] for b in ledger.top("page-1")], ["paragraph", "column_list", "paragraph"])
        self.assertEqual(body.text_of(ledger, "page-1").count("Own the roadmap"), 1)

    def test_a_page_that_changes_under_the_run_deletes_nothing(self):
        ledger = flat()
        real = ledger.call
        seen = []

        def call(method, path, body=None):
            if method == "PATCH" and path.endswith("/children") and not seen:
                seen.append(1)
                out = real(method, path, body)
                ledger.blocks[ledger.kids["page-1"][3]]["paragraph"]["rich_text"][0]["plain_text"] = "changed meanwhile"     # an old block (after the column_list) edited
                return out
            return real(method, path, body)
        with mock.patch.object(ledger, "call", call):
            with self.assertRaises(layout.LayoutError):
                layout.migrate(ledger, "page-1", KEY, DESC, True)
        self.assertNotIn("DELETE", ledger.writes)


if __name__ == "__main__":
    unittest.main()
