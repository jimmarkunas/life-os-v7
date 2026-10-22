"""LIFE-46 E07: accepted and cancelled Calendar events move the SAME round page in place (production-shaped fake): Interview 1 in
Scheduling Pending is dated, a dated round is rescheduled or cancelled only by its own recorded event reference."""
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, stage
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.platform.notion_client import NotionError
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV

PARENT = ParentQuery("New Example", "Program Manager")
PENDING = RoundQuery(True, None, None, 1, source="https://example.test/thread/1")
DATE, LATER, REF = "2026-10-28", "2026-11-04", "0123456789ab"


def pending_round():
    """A machine parent and its one machine Interview 1 in Scheduling Pending -> (client, parent id, round id)."""
    client = WritableFake()
    for _ in range(2):
        stage.run(10, True, environ=ENV, client=client, evidence=[(PARENT, PENDING)])
    (child,) = notion.child_scan(client, "created-1").items
    assert (child.interview_date, child.ordinal, child.identity_valid) == (None, 1, True)
    return client, "created-1", child.page_id


def move(client, child_id, live=True, date=DATE, ref=REF, cancelled=False):
    query = RoundQuery(True, date, None, 1, explicit_child_page_id=child_id, source="https://example.test/thread/1", calendar_ref=ref, cancelled=cancelled)
    return stage.run(10, live, environ=ENV, client=client, evidence=[(PARENT, query)])


def texts(client, block_id):
    return [notion.text(b) for b in client.blocks.get(block_id, [])]


def snapshot(client, child):
    return " ".join(texts(client, client.blocks[child][1]["id"]))


class Confirm(unittest.TestCase):
    def test_the_pending_round_is_dated_in_place_with_its_event_reference_and_nothing_else_is_created(self):
        client, parent, child = pending_round()
        posts, notes = len(client.posts), notion.protected_snapshot(client, child, "round")
        result = move(client, child)
        self.assertEqual((result["writes"], result["readback_ok"], result["why"].get("round_confirmed")), (1, 1, 1))
        self.assertEqual(len(client.posts), posts)                                   # no new page
        (dated,) = notion.child_scan(client, parent).items
        self.assertEqual((dated.page_id, dated.interview_date, dated.ordinal, dated.interviewer, dated.identity_valid), (child, DATE, 1, None, True))
        self.assertEqual(texts(client, child + "-block-0")[1], create.identity_text(RoundQuery(True, DATE, None, 1)))   # still the v1 line
        self.assertEqual(notion.round_calendar(client, child), (REF, False))
        self.assertEqual(notion.protected_snapshot(client, child, "round"), notes)     # Live and Raw Notes untouched
        self.assertIn(f"{DATE} · accepted Calendar invite", snapshot(client, child))
        self.assertNotIn("Scheduling Pending", snapshot(client, child))

    def test_replay_of_the_same_event_writes_nothing(self):
        client, _, child = pending_round()
        move(client, child)
        again = move(client, child)
        self.assertEqual((again["writes"], again["writes_planned"], again["why"].get("round_unchanged")), (0, 0, 1))

    def test_a_dry_run_plans_one_write_and_changes_nothing(self):
        client, _, child = pending_round()
        before = {k: [dict(b) for b in v] for k, v in client.blocks.items()}
        result = move(client, child, live=False)
        self.assertEqual((result["writes_planned"], result["writes"], result["why"].get("confirm_allowed")), (1, 0, 1))
        self.assertEqual({k: [dict(b) for b in v] for k, v in client.blocks.items()}, before)

    def test_a_human_edit_in_a_machine_block_stops_with_no_write(self):
        client, _, child = pending_round()
        client.blocks[client.blocks[child][1]["id"]][1]["paragraph"]["rich_text"] = [{"plain_text": "When: Jim wrote this"}]
        result = move(client, child)
        self.assertEqual((result["writes"], result["why"].get("machine_region_edited")), (0, 1))
        self.assertIsNone(notion.child_scan(client, "created-1").items[0].interview_date)

    def test_a_changed_live_note_during_the_write_is_reported(self):
        client, _, child = pending_round()
        real = notion.set_text

        def edit_notes(c, block_id, kind, rich):
            out = real(c, block_id, kind, rich)
            tab = next(b for blocks in c.blocks.values() for b in blocks if b.get("type") == "tab" and b["id"].startswith(child))
            live = next(b for b in c.blocks[tab["id"]] if notion.text(b) == notion.LIVE_PANE)
            c.blocks.setdefault(live["id"], []).append({"id": "jim", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Jim typed"}]}})
            return out
        with patch.object(notion, "set_text", side_effect=edit_notes):
            result = move(client, child)
        self.assertEqual(result["why"].get("readback_protected_changed"), 1)

    def test_a_failed_write_rolls_the_machine_blocks_back_and_leaves_the_round_pending(self):
        client, _, child = pending_round()
        before = snapshot(client, child)
        real, calls = notion.set_text, []

        def fail_second(c, block_id, kind, rich):
            calls.append(block_id)
            if len(calls) == 2:
                raise NotionError("NOTION_HTTP_500")
            return real(c, block_id, kind, rich)
        with patch.object(notion, "set_text", side_effect=fail_second):
            result = move(client, child)
        self.assertEqual(result["why"].get("write_failed"), 1)
        self.assertEqual(snapshot(client, child), before)
        self.assertIsNone(notion.child_scan(client, "created-1").items[0].interview_date)
        self.assertEqual(notion.round_calendar(client, child), (None, False))

    def test_another_parents_child_or_a_cancel_of_a_pending_round_is_refused(self):
        client, parent, child = pending_round()
        want = RoundQuery(True, DATE, None, 1, calendar_ref=REF)
        self.assertEqual(create.transition(client, "another-parent", child, None, want, True, {}), "round_unconfirmable")
        self.assertEqual(create.transition(client, parent, child, None, RoundQuery(True, DATE, None, 1, calendar_ref=REF, cancelled=True), True, {}), "round_unconfirmable")
        self.assertIsNone(notion.child_scan(client, parent).items[0].interview_date)


class Reschedule(unittest.TestCase):
    def dated(self):
        client, parent, child = pending_round()
        move(client, child)
        return client, parent, child

    def test_the_same_event_at_a_new_time_re_dates_the_same_page(self):
        client, parent, child = self.dated()
        notes, posts = notion.protected_snapshot(client, child, "round"), len(client.posts)
        result = move(client, child, date=LATER)
        self.assertEqual((result["writes"], result["readback_ok"], result["why"].get("round_rescheduled")), (1, 1, 1))
        (round_,) = notion.child_scan(client, parent).items
        self.assertEqual((round_.page_id, round_.interview_date, round_.ordinal), (child, LATER, 1))
        self.assertEqual((notion.round_calendar(client, child), len(client.posts)), ((REF, False), posts))
        self.assertEqual(notion.protected_snapshot(client, child, "round"), notes)

    def test_another_event_never_moves_a_dated_round(self):
        client, parent, child = self.dated()
        result = move(client, child, date=LATER, ref="ffffffffffff")
        self.assertEqual((result["writes"], result["why"].get("round_unlinked")), (0, 1))
        self.assertEqual(notion.child_scan(client, parent).items[0].interview_date, DATE)


class Cancel(unittest.TestCase):
    def test_a_cancelled_event_marks_its_round_cancelled_and_keeps_its_date_and_identity(self):
        client, parent, child = pending_round()
        move(client, child)
        notes = notion.protected_snapshot(client, child, "round")
        result = move(client, child, cancelled=True)
        self.assertEqual((result["writes"], result["readback_ok"], result["why"].get("round_cancelled")), (1, 1, 1))
        (round_,) = notion.child_scan(client, parent).items
        self.assertEqual((round_.interview_date, round_.ordinal, round_.identity_valid), (DATE, 1, True))      # LIFE-43: still one valid machine child
        self.assertEqual(notion.round_calendar(client, child), (REF, True))
        self.assertIn(f"Cancelled {DATE}", snapshot(client, child))
        self.assertEqual(notion.protected_snapshot(client, child, "round"), notes)
        again = move(client, child, cancelled=True)
        self.assertEqual((again["writes"], again["why"].get("round_unchanged")), (0, 1))

    def test_a_round_with_no_recorded_event_is_never_cancelled(self):
        client = WritableFake()
        dated = RoundQuery(True, DATE, None, 1)
        for _ in range(2):
            stage.run(10, True, environ=ENV, client=client, evidence=[(PARENT, dated)])
        (child,) = notion.child_scan(client, "created-1").items
        result = move(client, child.page_id, cancelled=True)
        self.assertEqual((result["writes"], result["why"].get("round_unlinked")), (0, 1))


class Life43(unittest.TestCase):
    def test_every_state_reads_as_the_same_valid_machine_identity(self):
        client, parent, child = pending_round()
        for step in ({}, {"date": LATER}, {"date": LATER, "cancelled": True}):
            move(client, child, **step)
            (found,) = notion.child_scan(client, parent).items
            self.assertEqual((found.page_id, found.identity_valid, found.ordinal), (child, True, 1), step)   # J1C's progression signal stays TRUE


if __name__ == "__main__":
    unittest.main()
