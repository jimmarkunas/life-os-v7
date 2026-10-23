"""E10: the accepted Calendar invite's own joining details (join link, location) on the round page's Logistics, written in place with the round's
other Calendar facts and read back; nothing is guessed, and a cancelled invite shows none."""
import unittest
from datetime import datetime
from lifeos.interview import create, notion, stage
from lifeos.interview.models import RoundQuery
from lifeos.sources import hiring, interview_evidence as ev
from tests.interview.test_confirm import DATE, LATER, PARENT, REF, pending_round
from tests.interview.test_notion import ENV
from tests.sources.test_interview_evidence import event
from tests.sources import test_interview_journey as J
from tests.sources.test_interview_journey import INVITE, LEAD, NO_REQ, REQUEST

JOIN, PLACE = "https://meet.example.test/abc-defg-hij", "Example HQ, Room 4"


def move(client, child_id, meeting=None, date=DATE, cancelled=False, live=True):
    query = RoundQuery(True, date, None, 1, explicit_child_page_id=child_id, source="https://example.test/thread/1", calendar_ref=REF, cancelled=cancelled,
                       meeting=meeting)
    return stage.run(10, live, environ=ENV, client=client, evidence=[(PARENT, query)])


def logistics(client, child):
    return " ".join(notion.text(b) for blocks in client.blocks.values() for b in blocks if notion.text(b).startswith("Meeting link / location: ")
                    and b["id"].startswith(child))


class Joining(unittest.TestCase):
    def test_the_confirmed_round_shows_the_invites_join_link_and_location_and_reads_them_back(self):
        client, _, child = pending_round()
        notes = notion.protected_snapshot(client, child, "round")
        result = move(client, child, (JOIN, PLACE))
        self.assertEqual((result["writes"], result["readback_ok"], result["why"].get("round_confirmed")), (1, 1, 1))
        self.assertEqual(create.round_logistics(client, child), (REF, False, (JOIN, PLACE)))
        self.assertEqual(create.round_calendar(client, child), (REF, False))                         # the E07 reader is unchanged
        self.assertEqual(notion.protected_snapshot(client, child, "round"), notes)
        again = move(client, child, (JOIN, PLACE))
        self.assertEqual((again["writes"], again["why"].get("round_unchanged")), (0, 1))

    def test_details_arriving_later_update_the_same_dated_round_in_place(self):
        client, parent, child = pending_round()
        move(client, child)
        self.assertEqual(create.round_logistics(client, child)[2], None)
        result = move(client, child, (JOIN, None))
        self.assertEqual((result["writes"], result["readback_ok"], result["why"].get("round_details")), (1, 1, 1))
        self.assertEqual(create.round_logistics(client, child), (REF, False, (JOIN, None)))
        (round_,) = notion.child_scan(client, parent).items
        self.assertEqual((round_.page_id, round_.interview_date), (child, DATE))

    def test_a_changed_link_alone_is_rewritten_and_a_location_alone_is_text(self):
        client, _, child = pending_round()
        move(client, child, (JOIN, None))
        other = "https://meet.example.test/zzz-yyyy-xxx"
        self.assertEqual(move(client, child, (other, None))["why"].get("round_details"), 1)
        self.assertEqual(create.round_logistics(client, child)[2], (other, None))
        self.assertEqual(move(client, child, (None, PLACE))["why"].get("round_details"), 1)
        self.assertEqual(create.round_logistics(client, child)[2], (None, PLACE))
        self.assertEqual(logistics(client, child), f"Meeting link / location: {PLACE}")

    def test_a_moved_invite_carries_its_details_and_a_cancelled_one_shows_none(self):
        client, _, child = pending_round()
        move(client, child, (JOIN, PLACE))
        self.assertEqual(move(client, child, (JOIN, None), date=LATER)["why"].get("round_rescheduled"), 1)
        self.assertEqual(create.round_logistics(client, child), (REF, False, (JOIN, None)))
        self.assertEqual(move(client, child, (JOIN, None), date=LATER, cancelled=True)["why"].get("round_cancelled"), 1)
        self.assertEqual(create.round_logistics(client, child), (REF, True, None))
        self.assertEqual(logistics(client, child), "Meeting link / location: Unavailable")

    def test_a_dry_run_plans_the_details_write_and_changes_nothing(self):
        client, _, child = pending_round()
        move(client, child)
        before = {k: [dict(b) for b in v] for k, v in client.blocks.items()}
        result = move(client, child, (JOIN, PLACE), live=False)
        self.assertEqual((result["writes_planned"], result["writes"], result["why"].get("details_allowed")), (1, 0, 1))
        self.assertEqual({k: [dict(b) for b in v] for k, v in client.blocks.items()}, before)


class FromTheInvite(unittest.TestCase):
    def event(self, **extra):
        return {"id": "e1", "summary": "ExampleCo Interview", "status": "confirmed", "start": {"dateTime": "2026-10-28T15:00:00Z"}, **extra}

    def read(self, *events):
        class Calendar:
            def list_events(self, *_):
                return list(events)
        return hiring.read_events(Calendar(), datetime(2026, 10, 23, tzinfo=hiring.TZ))

    def test_the_video_entry_point_wins_then_the_meet_link_and_only_https(self):
        video = {"conferenceData": {"entryPoints": [{"entryPointType": "phone", "uri": "tel:+1-555-0100"}, {"entryPointType": "video", "uri": JOIN}]},
                 "hangoutLink": "https://meet.example.test/other"}
        self.assertEqual(self.read(self.event(**video))[0]["meeting"], (JOIN, None))
        self.assertEqual(self.read(self.event(hangoutLink=JOIN))[0]["meeting"], (JOIN, None))
        self.assertEqual(self.read(self.event(hangoutLink="http://meet.example.test/plain", location="  Example HQ,\n Room 4 "))[0]["meeting"], (None, PLACE))

    def test_an_invite_with_no_details_has_no_meeting_key(self):
        self.assertEqual(self.read(self.event(description="Join at https://zoom.example.test/j/1")), [{"id": "e1", "title": "ExampleCo Interview",
                                                                                                        "start": "2026-10-28T10:00:00-05:00"}])



class Through(unittest.TestCase):
    """The real Interview runner tick after tick (the LIFE-78 journey harness): the invite's details reach the same round page, and only a change writes."""
    setUp, tick, settle, pages = J.Journey.setUp, J.Journey.tick, J.Journey.settle, J.Journey.pages

    def test_the_accepted_invites_details_land_on_its_round_and_a_later_change_updates_it_in_place(self):
        self.settle(LEAD, NO_REQ, REQUEST)
        self.events.append(event(INVITE, hangoutLink=JOIN))
        ticks = self.settle(LEAD, NO_REQ, REQUEST)
        self.assertEqual(ticks[0]["why"].get("round_confirmed"), 1)
        (_, (round_,)), = self.pages()
        self.assertEqual(create.round_logistics(self.notion, round_.page_id), (ev.reference(INVITE), False, (JOIN, None)))
        self.events[0] = event(INVITE, hangoutLink=JOIN, location=PLACE)               # the same invite gains a location: the same page, no new round
        ticks = self.settle(LEAD, NO_REQ, REQUEST)
        self.assertEqual(ticks[0]["why"].get("round_details"), 1)
        (_, (again,)), = self.pages()
        self.assertEqual((again.page_id, again.interview_date), (round_.page_id, "2026-10-28"))
        self.assertEqual(create.round_logistics(self.notion, round_.page_id)[2], (JOIN, PLACE))
        self.assertEqual(self.tick(LEAD, NO_REQ, REQUEST)["writes"], 0)                 # replay
        self.assertNotIn("meet.example.test", str(ticks))                              # counts only: no link reaches the run's output


if __name__ == "__main__":
    unittest.main()
