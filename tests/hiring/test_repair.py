"""LIFE-47 P0: the one-shot Hiring Pipeline repair. A hand edit trashed the status paragraph and inserted a stray three-column table; the repair puts the ORIGINAL paragraph back by its pinned id,
removes only the stray table, fixes only the one Notes cell, and otherwise stops with the page untouched."""
import unittest
from copy import deepcopy

from lifeos.hiring import card, models as M, repair
from lifeos.hiring.snapshot import HiringError
from lifeos.platform import report_region as R
from tests.hiring.test_hiring import ENV, page, writes
from tests.kit.tree import Tree, rich

STATUS, TABLE, HEADING = "0000000000000000000000000000a002", "0000000000000000000000000000a003", "0000000000000000000000000000a001"
URL = "https://www.notion.so/3d63c5a0592681cba881ebd42113d679"


class RepairTree(Tree):
    """Adds Notion's trash and restore on a block (PATCH in_trash). `restore_at` is where a restored block lands: None = its old place."""
    restore_at, old_index = None, {}

    def trash(self, block_id):
        parent = self.blocks[block_id]["parent"].get("block_id") or self.blocks[block_id]["parent"]["page_id"]
        self.old_index[block_id] = self.kids[parent].index(block_id)
        self.kids[parent].remove(block_id)
        self.blocks[block_id]["in_trash"] = True

    def call(self, method, path, body=None):
        parts = path.strip("/").split("/")
        if method == "PATCH" and len(parts) == 2 and body and "in_trash" in body and parts[1] in self.blocks:
            self.log.append((method, path))
            parent = self.blocks[parts[1]]["parent"].get("block_id") or self.blocks[parts[1]]["parent"]["page_id"]
            if body["in_trash"] and not self.blocks[parts[1]].get("in_trash"):
                self.trash(parts[1])
            elif not body["in_trash"] and self.blocks[parts[1]].get("in_trash"):
                self.kids[parent].insert(self.old_index[parts[1]] if self.restore_at is None else self.restore_at, parts[1])
                self.blocks[parts[1]]["in_trash"] = False
            self._tripped(method, path)
            return {}
        return super().call(method, path, body)


def broken(second_stray=False, header=("Company/Role", "Stage", "Next Step"), bullets=1, links=1):
    """The live incident: heading -> stray table -> the pinned table; the status paragraph in the trash; one Notes cell hand-edited."""
    t = page(extra_rows=0)
    t.__class__ = RepairTree
    t.old_index, t.restore_at = {}, None
    notes = [{"plain_text": "• ", "text": {"content": "• ", "link": None}}] + [{"plain_text": "Opportunity", "text": {"content": "Opportunity", "link": {"url": URL}}}] * links + [{"plain_text": "\n• ", "text": {"content": "\n• ", "link": None}}]
    t.row(f"{TABLE}-s", TABLE, ["🔴 SH/FT — Technical Program Manager (Contract)", "Interviewing", "Possible rejection email received Sep 15", "x"])
    t.blocks[f"{TABLE}-s"]["table_row"]["cells"][3] = notes if bullets else rich("Opportunity Page ↗", URL)
    t.row(f"{TABLE}-u", TABLE, ["🔴 Uber — Technical Project Manager", "Interviewing", "No news in 16 days", "x"])
    t.blocks[f"{TABLE}-u"]["table_row"]["cells"][3] = rich("Opportunity Page ↗", "https://www.notion.so/3ed3c5a0592681a6b025d99f5019800e")
    at = t.kids["right"].index(HEADING)
    t.table("stray", "right", [list(header), ["SH/FT", "Interviewing", ""]], width=3, index=at + 1)
    if second_stray:
        t.table("stray2", "right", [list(header)], width=3, index=at + 1)
    t.trash(STATUS)
    return t


def state(t):
    return {k: deepcopy(v) for k, v in t.blocks.items()}, deepcopy(t.kids)


class RepairTests(unittest.TestCase):
    def test_a_dry_run_reports_the_incident_and_writes_nothing(self):
        t = broken()
        before = state(t)
        facts = repair.run(0, False, ENV, t)
        self.assertEqual((facts["stray_tables"], facts["status_in_trash"], facts["cells_to_fix"], facts["plan_ok"], facts["validate"]), (1, True, 1, True, "HIRING_FLOATING_SHAPE_INVALID"))
        self.assertEqual((writes(t), state(t)), ([], before))

    def test_the_live_repair_restores_the_original_paragraph_trashes_only_the_stray_and_fixes_only_the_cell(self):
        t = broken()
        facts = repair.run(0, True, ENV, t)
        self.assertEqual((facts["validate"], facts["rest_of_page_unchanged"]), ("OK", True))
        self.assertEqual(t.kids["right"][t.kids["right"].index(HEADING):][:3], [HEADING, STATUS, TABLE])          # the same three ids, in order
        self.assertTrue(t.blocks["stray"]["in_trash"] and "stray" not in t.kids["right"])
        self.assertEqual([c[0]["text"]["content"] for c in t.blocks[f"{TABLE}-s"]["table_row"]["cells"]][3], "Opportunity Page ↗")
        self.assertEqual(t.blocks[f"{TABLE}-s"]["table_row"]["cells"][3][0]["text"]["link"]["url"], URL)
        self.assertEqual(R.cells_of(t.blocks[f"{TABLE}-s"])[:3], ["🔴 SH/FT — Technical Program Manager (Contract)", "Interviewing", "Possible rejection email received Sep 15"])
        self.assertEqual(R.cells_of(t.blocks[f"{TABLE}-u"])[:3], ["🔴 Uber — Technical Project Manager", "Interviewing", "No news in 16 days"])       # every other row untouched
        patched = [path for method, path in writes(t) if method == "PATCH"]
        self.assertEqual(sorted(p.split("/")[-1] for p in patched), sorted([STATUS, "stray", f"{TABLE}-s"]))      # only these three blocks were written
        t.log.clear()
        again = repair.run(0, True, ENV, t)                                                                       # idempotent: a healthy region is left alone
        self.assertEqual((again["validate"], writes(t)), ("OK", []))

    def test_a_change_elsewhere_on_the_page_during_the_repair_is_reported_not_hidden(self):
        t = broken()
        t.trip = ("stray", lambda tree: tree.blocks["cal-p"]["paragraph"].update({"rich_text": rich("someone edited the Calendar")}))
        facts = repair.run(0, True, ENV, t)
        self.assertEqual((facts["validate"], facts["rest_of_page_unchanged"]), ("OK", False))

    def test_a_paragraph_restored_to_the_wrong_place_is_put_back_and_nothing_else_changes(self):
        t = broken()
        t.restore_at = len(t.kids["right"])
        with self.assertRaises(HiringError) as error:
            repair.run(0, True, ENV, t)
        self.assertEqual(str(error.exception), "HIRING_REPAIR_STATUS_MISPLACED")
        self.assertTrue(t.blocks[STATUS]["in_trash"])
        self.assertFalse(t.blocks["stray"].get("in_trash"))
        self.assertIn("•", R.cells_of(t.blocks[f"{TABLE}-s"])[3])                                               # the Notes cell was not touched before the stop

    def test_anything_unexpected_stops_before_a_single_write(self):
        cases = {"two stray tables": ("HIRING_REPAIR_STATE_UNEXPECTED", broken(second_stray=True)), "a stray table with another header": ("HIRING_REPAIR_STATE_UNEXPECTED", broken(header=("Company", "Stage", "Action"))),
                 "two links in the Notes cell": ("HIRING_REPAIR_STATE_UNEXPECTED", broken(links=2))}
        gone = broken()
        del gone.blocks[STATUS]                                                                                  # the original paragraph no longer exists at all
        cases["the original paragraph is gone"] = ("HIRING_REPAIR_STATE_UNEXPECTED", gone)
        for name, (code, t) in cases.items():
            with self.subTest(name):
                before = state(t)
                with self.assertRaises(HiringError) as error:
                    repair.run(0, True, ENV, t)
                self.assertEqual(str(error.exception), code)
                self.assertEqual((writes(t), state(t)), ([], before))

    def test_the_wrong_pinned_heading_is_refused(self):
        t = broken()
        t.blocks[HEADING]["heading_3"]["rich_text"] = rich("Something else")
        with self.assertRaises(HiringError) as error:
            repair.run(0, True, ENV, t)
        self.assertEqual((str(error.exception), writes(t)), ("HIRING_REPAIR_HEADING_UNEXPECTED", []))


if __name__ == "__main__":
    unittest.main()
