"""LIFE-46 E07: an accepted Calendar time dates the SAME Scheduling Pending Interview 1 page in place (production-shaped fake)."""
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, stage
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.platform.notion_client import NotionError
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV

PARENT = ParentQuery("New Example", "Program Manager")
PENDING = RoundQuery(True, None, None, 1, source="https://example.test/thread/1")
DATE = "2026-10-28"


def pending_round():
    """A machine parent and its one machine Interview 1 in Scheduling Pending -> (client, parent id, round id)."""
    client = WritableFake()
    for _ in range(2):
        stage.run(10, True, environ=ENV, client=client, evidence=[(PARENT, PENDING)])
    (child,) = notion.child_scan(client, "created-1").items
    assert (child.interview_date, child.ordinal, child.identity_valid) == (None, 1, True)
    return client, "created-1", child.page_id


def confirm(client, child_id, live=True, date=DATE, copies=1):
    query = RoundQuery(True, date, None, 1, explicit_child_page_id=child_id, source="https://example.test/thread/1")
    return stage.run(10, live, environ=ENV, client=client, evidence=[(PARENT, query)] * copies)


def texts(client, block_id):
    return [notion.text(b) for b in client.blocks.get(block_id, [])]


class Confirm(unittest.TestCase):
    def test_the_pending_round_is_dated_in_place_and_nothing_else_is_created(self):
        client, parent, child = pending_round()
        posts, notes = len(client.posts), notion.protected_snapshot(client, child, "round")
        result = confirm(client, child)
        self.assertEqual((result["writes"], result["readback_ok"], result["why"].get("round_confirmed")), (1, 1, 1))
        self.assertEqual(len(client.posts), posts)                                   # no new page
        (dated,) = notion.child_scan(client, parent).items
        self.assertEqual((dated.page_id, dated.interview_date, dated.ordinal, dated.interviewer, dated.identity_valid), (child, DATE, 1, None, True))
        self.assertEqual(texts(client, child + "-block-0")[1], create.identity_text(RoundQuery(True, DATE, None, 1)))   # still the v1 line
        self.assertEqual(notion.protected_snapshot(client, child, "round"), notes)     # Live and Raw Notes untouched
        snapshot = " ".join(texts(client, client.blocks[child][1]["id"]))
        self.assertIn(DATE, snapshot)
        self.assertNotIn("Scheduling Pending", snapshot)

    def test_replay_of_the_same_event_never_dates_or_creates_again(self):
        client, parent, child = pending_round()
        confirm(client, child)
        posts = len(client.posts)
        first = confirm(client, child)                                               # the dated round: the opportunity page's Interviews table catches up once
        again = confirm(client, child)
        self.assertEqual((first["why"].get("round_confirmed"), again["writes"], again["writes_planned"], len(client.posts)), (None, 0, 0, posts))
        self.assertEqual([c.interview_date for c in notion.child_scan(client, parent).items], [DATE])

    def test_a_dry_run_plans_one_write_and_changes_nothing(self):
        client, _, child = pending_round()
        before = {k: [dict(b) for b in v] for k, v in client.blocks.items()}
        result = confirm(client, child, live=False)
        self.assertEqual((result["writes_planned"], result["writes"], result["why"].get("confirm_allowed")), (1, 0, 1))
        self.assertEqual({k: [dict(b) for b in v] for k, v in client.blocks.items()}, before)

    def test_a_human_edit_in_a_machine_block_stops_with_no_write(self):
        client, _, child = pending_round()
        snapshot = client.blocks[child][1]["id"]
        client.blocks[snapshot][1]["paragraph"]["rich_text"] = [{"plain_text": "When: Jim wrote this"}]
        result = confirm(client, child)
        self.assertEqual((result["writes"], result["why"].get("machine_region_edited")), (0, 1))
        self.assertIsNone(notion.child_scan(client, "created-1").items[0].interview_date)

    def test_a_changed_live_note_during_the_write_is_reported_and_the_date_is_not_trusted(self):
        client, _, child = pending_round()
        real = notion.set_text

        def edit_notes(c, block_id, kind, rich):
            out = real(c, block_id, kind, rich)
            tab = next(b for blocks in c.blocks.values() for b in blocks if b.get("type") == "tab" and b["id"].startswith(child))
            live = next(b for b in c.blocks[tab["id"]] if notion.text(b) == notion.LIVE_PANE)
            c.blocks.setdefault(live["id"], []).append({"id": "jim", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Jim typed"}]}})
            return out
        with patch.object(notion, "set_text", side_effect=edit_notes):
            result = confirm(client, child)
        self.assertEqual(result["why"].get("readback_protected_changed"), 1)

    def test_a_failed_write_rolls_the_machine_blocks_back_and_leaves_the_round_pending(self):
        client, _, child = pending_round()
        before = texts(client, client.blocks[child][1]["id"])
        real, calls = notion.set_text, []

        def fail_second(c, block_id, kind, rich):
            calls.append(block_id)
            if len(calls) == 2:
                raise NotionError("NOTION_HTTP_500")
            return real(c, block_id, kind, rich)
        with patch.object(notion, "set_text", side_effect=fail_second):
            result = confirm(client, child)
        self.assertEqual(result["why"].get("write_failed"), 1)
        self.assertEqual(texts(client, client.blocks[child][1]["id"]), before)
        self.assertIsNone(notion.child_scan(client, "created-1").items[0].interview_date)

    def test_a_dated_round_or_another_parents_child_is_never_rewritten(self):
        client, parent, child = pending_round()
        confirm(client, child)
        self.assertEqual(create.confirm(client, parent, child, None, "2026-11-04", True, {}), "round_unconfirmable")        # already dated: a move is not E07
        self.assertEqual(create.confirm(client, "another-parent", child, None, DATE, True, {}), "round_unconfirmable")
        self.assertEqual(notion.child_scan(client, parent).items[0].interview_date, DATE)

    def test_life_43_reads_the_dated_round_as_the_same_valid_machine_identity(self):
        client, parent, child = pending_round()
        confirm(client, child)
        (found,) = notion.child_scan(client, parent).items
        self.assertTrue(found.identity_valid)                                        # J1C's progression signal stays TRUE: a machine child exists
        self.assertEqual(create.parse_identity(create.identity_text(RoundQuery(True, DATE, None, 1))), {"interview_date": DATE, "interviewer": None, "ordinal": 1})


if __name__ == "__main__":
    unittest.main()
