"""LIFE-658: an edit in a machine-written region is never fatal and never silently lost: its text is copied into the page's one Preserved edits toggle, the region is rewritten from the
current render through the guarded write path, and the page and the counts say so. A page whose structure is not recognisable keeps the old refusal. Everything here is synthetic."""
import copy
import unittest
from lifeos.interview import create, notion, template
from lifeos.interview.models import RoundQuery
from lifeos.platform.notion_client import NotionError
from tests.interview.support import block, opportunity, texts as page_texts, written
from tests.interview.test_confirm import DATE, LATER, move, pending_round, snapshot
from tests.interview.test_confirm import texts as top_texts

EDIT = "When: Jim wrote this"


def fail(client, test):
    """Make the calls `test(method, path, body)` names fail, as a Notion outage would."""
    real = client.call

    def call(method, path, body=None):
        if test(method, path, body):
            raise NotionError("NOTION_NETWORK")
        return real(method, path, body)
    client.call = call


def toggle_write(method, path, body):
    return method == "PATCH" and path.endswith("/children") and any(notion.text(b) == notion.PRESERVED for b in (body or {}).get("children", []))


def edited_round():
    client, parent, child = pending_round()
    client.blocks[client.blocks[child][1]["id"]][1]["paragraph"]["rich_text"] = [{"plain_text": EDIT}]
    return client, parent, child


def preserved(client, page_id):
    found = [b for b in client.blocks[page_id] if notion.is_preserved(b)]
    return found, [notion.text(b) for b in client.blocks.get(found[0]["id"], [])] if found else []


def mutations(client, start):
    return [c for c in client.calls[start:] if c[0] != "GET"]


def marks(client, block_id):
    return [t for t in top_texts(client, block_id) if template.REPAIRED_LINE.fullmatch(t)]


class RoundRepair(unittest.TestCase):
    def test_an_edited_machine_block_is_preserved_then_repaired_and_counted_on_its_own(self):
        client, parent, child = edited_round()
        notes = notion.protected_snapshot(client, child, "round")
        result = move(client, child)
        self.assertEqual((result["blocked"], result["why"].get("round_confirmed"), result["repaired_edits"], result["writes"]), (0, 1, 1, 1))
        toggles, held = preserved(client, child)
        self.assertEqual((len(toggles), held[1:]), (1, [EDIT]))                              # copied verbatim; the first line only says when
        self.assertTrue(held[0].startswith("Preserved ") and held[0].endswith(" CT"))
        self.assertNotIn(EDIT, snapshot(client, child))                                       # the block reads as the render again
        self.assertIn(f"{DATE} · accepted Calendar invite", snapshot(client, child))
        self.assertEqual(len(marks(client, client.blocks[child][1]["id"])), 1)                # the one-line repaired marker
        self.assertIn(template.ROUND_MACHINE_NOTE, snapshot(client, child))
        (dated,) = notion.child_scan(client, parent).items
        self.assertEqual(dated.interview_date, DATE)
        self.assertEqual(notion.protected_snapshot(client, child, "round"), notes)           # Live and Raw Notes untouched

    def test_the_toggle_is_created_once_and_a_second_repair_appends_to_it(self):
        client, parent, child = edited_round()
        move(client, child)
        client.blocks[client.blocks[child][1]["id"]][1]["paragraph"]["rich_text"] = [{"plain_text": "When: and again"}]
        result = move(client, child, date=LATER)
        self.assertEqual((result["why"].get("round_rescheduled"), result["repaired_edits"]), (1, 1))
        toggles, held = preserved(client, child)
        self.assertEqual((len(toggles), [t for t in held if not t.startswith("Preserved ")]), (1, [EDIT, "When: and again"]))
        self.assertEqual(len(marks(client, client.blocks[child][1]["id"])), 1)               # the one marker line is updated, not repeated
        self.assertEqual(notion.child_scan(client, parent).items[0].interview_date, LATER)

    def test_a_page_nobody_edited_is_written_as_before_with_no_toggle_and_no_marker(self):
        client, _, child = pending_round()
        result = move(client, child)
        self.assertEqual((result["writes"], result.get("repaired_edits")), (1, None))
        self.assertEqual(preserved(client, child)[0], [])
        self.assertEqual(marks(client, client.blocks[child][1]["id"]), [])

    def test_a_stale_block_and_a_hand_typed_identity_are_repaired_and_the_old_text_kept(self):
        client, parent, child = pending_round()
        move(client, child)
        fold = client.blocks[child][0]["id"]
        client.blocks[fold][1]["paragraph"]["rich_text"] = [{"plain_text": create.identity_text(RoundQuery(True, "2026-11-01", None, 1))}]      # a hand-typed, valid date
        result = move(client, child, date=LATER)
        self.assertEqual((result["blocked"], result["why"].get("round_rescheduled"), result["repaired_edits"]), (0, 1, 1))
        self.assertEqual(notion.text(client.blocks[fold][1]), create.identity_text(RoundQuery(True, LATER, None, 1)))
        self.assertTrue(any(DATE in t for t in preserved(client, child)[1]))                  # what the page said is still there
        self.assertEqual(notion.child_scan(client, parent).items[0].interview_date, LATER)

    def test_a_preserve_that_cannot_be_written_rewrites_nothing_and_fails_loudly(self):
        client, _, child = edited_round()
        fail(client, toggle_write)
        before, start = copy.deepcopy(client.blocks), len(client.calls)
        result = move(client, child)
        self.assertEqual((result["why"].get("preserve_failed"), result["blocked"], result["writes"], result.get("repaired_edits")), (1, 1, 0, None))
        self.assertEqual(client.blocks, before)
        self.assertEqual(mutations(client, start), [])                                        # the refused attempt never reached the page

    def test_a_repair_write_that_fails_after_the_preserve_rolls_back_and_keeps_the_copy(self):
        client, _, child = edited_round()
        identity_id = client.blocks[client.blocks[child][0]["id"]][1]["id"]
        fail(client, lambda method, path, body: method == "PATCH" and path == f"/blocks/{identity_id}")
        result = move(client, child)
        self.assertEqual((result["why"].get("write_failed"), result["blocked"], result.get("repaired_edits")), (1, 1, None))
        self.assertEqual(preserved(client, child)[1][1:], [EDIT])                              # the copy stays
        self.assertIn(EDIT, snapshot(client, child))                                           # the page is as the human left it
        self.assertEqual(marks(client, client.blocks[child][1]["id"]), [])
        self.assertIsNone(notion.child_scan(client, "created-1").items[0].interview_date)      # still not re-identified: the identity line is the commit point

    def test_a_page_whose_structure_is_not_recognisable_still_refuses_with_no_write(self):
        for mode in ("columns", "identity"):
            with self.subTest(mode=mode):
                client, _, child = pending_round()
                if mode == "columns":
                    client.blocks[child] = [b for b in client.blocks[child] if b["type"] != "column_list"]
                else:
                    client.blocks[client.blocks[child][0]["id"]][1]["paragraph"]["rich_text"] = [{"plain_text": "not an identity"}]
                before, start = copy.deepcopy(client.blocks), len(client.calls)
                result = move(client, child)
                self.assertEqual((result["writes"], result.get("repaired_edits"), result["blocked"]), (0, None, 1))
                self.assertEqual((client.blocks, mutations(client, start)), (before, []))

    def test_a_dry_run_plans_the_repair_and_changes_nothing(self):
        client, _, child = edited_round()
        before, start = copy.deepcopy(client.blocks), len(client.calls)
        result = move(client, child, live=False)
        self.assertEqual((result["writes_planned"], result["writes"], result["why"].get("confirm_allowed"), result.get("repaired_edits")), (1, 0, 1, None))
        self.assertEqual((client.blocks, mutations(client, start)), (before, []))


class ParentRepair(unittest.TestCase):
    def edited(self):
        client, page_id = written()
        self.type_in(client, page_id, "Jim typed here")
        return client, page_id, opportunity()

    @staticmethod
    def type_in(client, page_id, line):
        columns = [b for b in client.blocks[page_id] if b["type"] == "column_list"][-1]
        client.blocks[client.blocks[columns["id"]][0]["id"]].append(block(line, id=f"typed-{len(client.calls)}"))

    def protected(self, client, page_id):
        return notion.human_tail(client, page_id), [b["id"] for b in client.blocks[page_id] if b["type"] == "child_page"]

    def test_an_edited_region_is_preserved_above_jims_notes_then_rewritten(self):
        client, page_id, current = self.edited()
        kept, out = self.protected(client, page_id), {"writes": 0}
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_refreshed")
        self.assertEqual(out["repaired_edits"], 1)
        tops = [notion.text(b) for b in client.blocks[page_id]]
        self.assertEqual(tops.index(notion.PRESERVED) + 1, tops.index(notion.JIM_NOTES))      # the toggle sits right above Jim's notes, never inside them
        toggles, held = preserved(client, page_id)
        self.assertEqual((len(toggles), held[1:]), (1, ["Jim typed here"]))
        machine, _, _ = create.machine_region(client, page_id)
        self.assertNotIn("Jim typed here", "\n".join(page_texts(client, machine[0]["id"]) + [notion.text(b) for b in machine]))
        self.assertEqual(len(marks(client, page_id)), 1)
        self.assertIn(template.MACHINE_NOTE, page_texts(client, page_id))
        self.assertEqual(self.protected(client, page_id), kept)                               # Jim's notes and the interview page untouched
        self.assertIn("Jim's private note", page_texts(client, page_id))
        self.assertEqual(create.refresh(client, page_id, current, True, {"writes": 0}), "parent_unchanged")      # the repaired line is part of the page: it settles, no loop

    def test_a_second_edit_is_appended_to_the_same_toggle_and_the_marker_is_not_repeated(self):
        client, page_id, current = self.edited()
        create.refresh(client, page_id, current, True, {"writes": 0})
        self.type_in(client, page_id, "typed again")
        self.assertEqual(create.refresh(client, page_id, current, True, {"writes": 0}), "parent_refreshed")
        toggles, held = preserved(client, page_id)
        self.assertEqual((len(toggles), [t for t in held if not t.startswith("Preserved ")]), (1, ["Jim typed here", "typed again"]))
        self.assertEqual(len(marks(client, page_id)), 1)

    def test_a_preserve_that_cannot_be_written_changes_nothing(self):
        client, page_id, current = self.edited()
        fail(client, toggle_write)
        before, start = copy.deepcopy(client.blocks), len(client.calls)
        out = {"writes": 0}
        self.assertEqual(create.refresh(client, page_id, current, True, out), "preserve_failed")
        self.assertEqual((client.blocks, out["writes"], mutations(client, start)), (before, 0, []))

    def test_a_dry_run_plans_the_repair_and_changes_nothing(self):
        client, page_id, current = self.edited()
        before, start = copy.deepcopy(client.blocks), len(client.calls)
        self.assertEqual(create.refresh(client, page_id, current, False, {"writes": 0}), "refresh_allowed")
        self.assertEqual((client.blocks, mutations(client, start)), (before, []))

    def test_an_edit_with_no_text_is_repaired_and_says_nothing_was_left_to_copy(self):
        client, page_id = written()
        self.type_in(client, page_id, "")
        self.assertEqual(create.refresh(client, page_id, opportunity(), True, {"writes": 0}), "parent_refreshed")
        self.assertEqual(preserved(client, page_id)[1][1:], [create.NOTHING])
