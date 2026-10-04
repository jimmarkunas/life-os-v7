import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.agenda import card, snapshot
from lifeos.platform.gcal import GcalError, GoogleCalendar
from tests.kit.db import AgendaSnapshotDB
from tests.kit.gcal import CalendarEvents
from tests.kit.notion import AgendaRegions


TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 3, 8, 9, 30, tzinfo=TZ)


def timed(event_id, title, start="2026-03-08T10:00:00-05:00", end="2026-03-08T10:30:00-05:00", **extra):
    return {"id": event_id, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def all_day(event_id="all-day", title="Example all day", start="2026-03-08", end="2026-03-09", **extra):
    return {"id": event_id, "summary": title, "start": {"date": start}, "end": {"date": end}, **extra}


def saved_snapshot(events, taken_at=None):
    return {"schema": 1, "taken_at": (taken_at or NOW).isoformat(), "timezone": "America/Chicago",
            "today": "2026-03-08", "tomorrow": "2026-03-09", "events": events}


class AgendaSnapshotTests(unittest.TestCase):
    def test_window_edges_are_chicago_midnights_and_follow_daylight_saving(self):
        start, end, today, tomorrow = snapshot.window(datetime(2026, 3, 8, 12, tzinfo=TZ))
        self.assertEqual((start, end, today.isoformat(), tomorrow.isoformat()),
                         ("2026-03-08T00:00:00-06:00", "2026-03-10T00:00:00-05:00", "2026-03-08", "2026-03-09"))

    def test_full_read_across_pages_filters_events_and_keeps_all_day_timed_and_bridge_source(self):
        copied = timed("event-1", "Example meeting", location="Room 2, example.com",
                       hangoutLink="https://example.com/meeting", extendedProperties={"private": {"v7_src": "outlook"}})
        day_event = all_day()
        next_day = timed("event-3", "Example tomorrow", "2026-03-09T09:00:00-05:00", "2026-03-09T09:30:00-05:00")
        events = CalendarEvents([copied, day_event], [next_day,
            timed("cancelled", "Example cancelled", status="cancelled"),
            timed("declined", "Example declined", attendees=[{"self": True, "responseStatus": "declined"}]),
            {**timed("working", "Example working location"), "eventType": "workingLocation"}])
        snap = snapshot.build(events, NOW)
        self.assertEqual([item["id"] for item in snap["events"]], ["event-1", "all-day", "event-3"])
        self.assertEqual((snap["events"][0]["source"], snap["events"][1]["all_day"], snap["events"][0]["meeting_link"]),
                         ("bridge", True, "https://example.com/meeting"))
        self.assertEqual(events.windows, [("2026-03-08T00:00:00-06:00", "2026-03-10T00:00:00-05:00")])

    def test_incomplete_calendar_read_raises_and_leaves_previous_snapshot(self):
        old = saved_snapshot([{"id": "old", "title": "Example old", "days": ["2026-03-08"]}])
        database = AgendaSnapshotDB(old)
        calendar = CalendarEvents(GcalError("GCAL_LISTING_INCOMPLETE"))
        with self.assertRaises(GcalError):
            snapshot.run(1, True, calendar=calendar, now=NOW, connect=lambda: database)
        self.assertEqual(json.loads(database.payload), old)
        self.assertEqual(database.sql, [])

    def test_unexpected_private_read_error_becomes_fixed_code(self):
        with self.assertRaises(snapshot.AgendaError) as caught:
            snapshot.build(CalendarEvents(RuntimeError("Example secret title at example.com")), NOW)
        self.assertEqual(str(caught.exception), "AGENDA_READ_FAILED")
        self.assertNotIn("Example secret title", str(caught.exception))

    def test_snapshot_counts_are_private_free_and_live_save_reads_back(self):
        calendar = CalendarEvents([timed("e1", "Example private title"), all_day()])
        database = AgendaSnapshotDB()
        counts = snapshot.run(1, True, calendar=calendar, now=NOW, connect=lambda: database)
        self.assertEqual(counts, {"events": 2, "all_day": 1, "today": 2, "tomorrow": 0,
                                  "saved": 1, "blocks_written": 0, "status": "fresh"})
        self.assertNotIn("Example private title", json.dumps(counts))
        self.assertEqual(snapshot.load(database)["events"][0]["title"], "Example private title")

    def test_google_client_paginates_in_start_time_order_and_rejects_incomplete_page(self):
        client = GoogleCalendar('{"client_email":"synthetic","private_key":"key"}', "calendar", sign=lambda *_: b"x")
        pages = [{"items": [{"id": "a", "eventType": "default", "attendees": []}], "nextPageToken": "page-2"}, {"items": [{"id": "b"}]}]
        calls = []
        client.request = lambda method, path, params: (calls.append(dict(params)), pages.pop(0))[1]
        self.assertEqual([row["id"] for row in client.list_events("min", "max")], ["a", "b"])
        self.assertEqual(calls[0]["orderBy"], "startTime")
        self.assertEqual(calls[1]["pageToken"], "page-2")
        client.request = lambda *args: {"items": None}
        with self.assertRaises(GcalError) as caught:
            client.list_events("min", "max")
        self.assertEqual(str(caught.exception), "GCAL_LISTING_INCOMPLETE")


class AgendaCardTests(unittest.TestCase):
    def test_empty_day_now_next_marker_and_long_list_layout(self):
        empty, _ = card.render(saved_snapshot([]), now=NOW)
        self.assertEqual(sum("Nothing scheduled" in card._plain(block) for block in empty), 2)
        current = timed("now", "Example current", "2026-03-08T09:00:00-05:00", "2026-03-08T10:00:00-05:00")
        later = timed("next", "Example next", "2026-03-08T11:00:00-05:00", "2026-03-08T11:30:00-05:00")
        blocks, _ = card.render(saved_snapshot([snapshot.compact(current, NOW.date(), NOW.date().replace(day=9)),
                                                snapshot.compact(later, NOW.date(), NOW.date().replace(day=9))]), now=NOW)
        rendered = [card._plain(block) for block in blocks]
        self.assertTrue(any(line.startswith("Now ·") for line in rendered))
        upcoming, _ = card.render(saved_snapshot([snapshot.compact(later, NOW.date(), NOW.date().replace(day=9))]), now=NOW)
        self.assertTrue(any(card._plain(block).startswith("Next ·") for block in upcoming))
        many = [snapshot.compact(timed(f"e-{i}", f"Example event {i}"), NOW.date(), NOW.date().replace(day=9)) for i in range(105)]
        many_blocks, _ = card.render(saved_snapshot(many), now=NOW)
        self.assertGreater(len(many_blocks), 100)

    def test_stale_changes_only_status_and_never_blanks_events(self):
        event = snapshot.compact(timed("old", "Example retained"), NOW.date(), NOW.date().replace(day=9))
        fresh, _ = card.render(saved_snapshot([event]), now=NOW)
        stale, counts = card.render(saved_snapshot([event], NOW.replace(hour=4)), stale=True, now=NOW)
        self.assertIn("STALE · last accepted", card._plain(stale[0]))
        self.assertEqual([card._plain(block) for block in fresh[1:]], [card._plain(block) for block in stale[1:]])
        self.assertEqual(counts["status"], "stale")

    def test_nested_card_accepts_heading_three_and_four_and_replaces_foreign_content(self):
        event = snapshot.compact(timed("one", "Example fresh"), NOW.date(), NOW.date().replace(day=9))
        database = AgendaSnapshotDB(saved_snapshot([event]))
        for heading_type in ("heading_3", "heading_4"):
            with self.subTest(heading_type=heading_type):
                notion = AgendaRegions(heading_type=heading_type, extra_calendar=True)
                before_heading = dict(notion.children["calendar-callout"][0])
                before_jira = notion.full_tree("jira-callout")
                counts = card.run(1, True, environ=self._env(), client=notion,
                                  now=NOW, connect=lambda: database)
                ops = [kind for kind, _ in notion.log]
                append_at, delete_at = ops.index("APPEND"), ops.index("DELETE")
                final_read = max(i for i, (kind, path) in enumerate(notion.log)
                                 if kind == "GET" and path.startswith("/blocks/calendar-callout/children"))
                self.assertLess(append_at, delete_at)
                self.assertLess(delete_at, final_read)
                self.assertEqual(notion.children["calendar-callout"][0], before_heading)
                self.assertNotIn("Foreign writer content", [card._plain(block) for block in notion.children["calendar-callout"]])
                self.assertEqual(notion.full_tree("jira-callout"), before_jira)
                self.assertEqual(counts["status"], "fresh")
                self.assertGreater(counts["blocks_written"], 0)
                self.assertFalse(any(path == f"/blocks/{notion.page_id}/children" for _, path in notion.log))

    @staticmethod
    def _env():
        return {"CALENDAR_CARD_BLOCK_ID": "calendar-callout", "JIRA_CARD_BLOCK_ID": "jira-callout"}

    def test_invalid_target_shape_refuses_writes(self):
        database = AgendaSnapshotDB(saved_snapshot([]))
        for notion, configured in ((AgendaRegions(calendar_title="Other"), "calendar-callout"),
                                   (AgendaRegions(), "jira-callout"),
                                   (AgendaRegions(calendar_type="paragraph"), "calendar-callout"),
                                   (AgendaRegions(calendar_title="Calendar", heading_type="paragraph"), "calendar-callout")):
            before = len(notion.log)
            with self.assertRaises(Exception):
                card.run(1, True, environ={**self._env(), "CALENDAR_CARD_BLOCK_ID": configured}, client=notion,
                         now=NOW, connect=lambda: database)
            self.assertFalse(any(kind in ("APPEND", "DELETE", "PATCH") for kind, _ in notion.log[before:]))

    def test_missing_or_unreadable_jira_id_refuses_before_writes(self):
        database = AgendaSnapshotDB(saved_snapshot([]))
        for env in ({"CALENDAR_CARD_BLOCK_ID": "calendar-callout"},
                    {"CALENDAR_CARD_BLOCK_ID": "calendar-callout", "JIRA_CARD_BLOCK_ID": "missing"}):
            notion = AgendaRegions()
            with self.assertRaises(card.CardError):
                card.run(1, True, environ=env, client=notion, now=NOW, connect=lambda: database)
            self.assertFalse(any(kind in ("APPEND", "DELETE", "PATCH") for kind, _ in notion.log))

    def test_jira_change_mid_write_is_reported_before_deletes(self):
        database = AgendaSnapshotDB(saved_snapshot([]))
        notion = AgendaRegions(change_jira_on="APPEND")
        with self.assertRaises(card.CardError) as caught:
            card.run(1, True, environ=self._env(), client=notion, now=NOW, connect=lambda: database)
        self.assertEqual(str(caught.exception), "AGENDA_PROTECTED_REGION_CHANGED")
        self.assertFalse(any(kind == "DELETE" for kind, _ in notion.log))

    def test_missing_callout_is_reported_without_read_or_write(self):
        counts = card.run(1, True, environ={}, client=None, connect=lambda: self.fail("no database"))
        self.assertEqual(counts["status"], "not_configured")
        self.assertEqual(counts["blocks_written"], 0)

    def test_stale_live_run_updates_status_only_and_keeps_existing_event_blocks(self):
        saved = saved_snapshot([], NOW.replace(hour=4))
        database = AgendaSnapshotDB(saved)
        notion = AgendaRegions()
        notion.children["calendar-callout"][1] = notion._paragraph("Updated 04:00 CT")
        notion.children["calendar-callout"].append(notion._paragraph("Example previous event"))
        counts = card.run(1, True, environ=self._env(), client=notion,
                          now=NOW, connect=lambda: database)
        self.assertEqual(counts["status"], "stale")
        self.assertEqual(counts["blocks_written"], 1)
        self.assertTrue(any(card._plain(block).startswith("STALE · last accepted") for block in notion.children["calendar-callout"]))
        self.assertIn("Example previous event", [card._plain(block) for block in notion.children["calendar-callout"]])


if __name__ == "__main__":
    unittest.main()


class RealCalendarShapeTests(unittest.TestCase):
    """Shapes a real shared calendar produces that must not fail the whole snapshot."""

    def compact(self, event):
        return snapshot.compact(event, NOW.date(), NOW.date().replace(day=9))

    def test_an_untitled_event_is_kept_as_no_title(self):
        event = timed("u1", "x")
        del event["summary"]
        self.assertEqual(self.compact(event)["title"], "(no title)")
        self.assertEqual(self.compact({**timed("u2", "x"), "summary": "   "})["title"], "(no title)")

    def test_a_zero_length_event_is_kept_on_its_day(self):
        item = self.compact(timed("z1", "Example reminder", start="2026-03-08T11:00:00-05:00", end="2026-03-08T11:00:00-05:00"))
        self.assertEqual(item["days"], ["2026-03-08"])

    def test_an_event_that_ends_before_it_starts_is_still_invalid(self):
        with self.assertRaises(snapshot.AgendaError):
            self.compact(timed("b1", "x", start="2026-03-08T11:00:00-05:00", end="2026-03-08T10:00:00-05:00"))


class HyphenatedIdTests(unittest.TestCase):
    """The API returns ids with hyphens; the secret (copied from a block link) has none. The card must still find its block."""

    HYPHENATED = "fc809376-99e6-45a2-ac96-257d093d8e7c"
    PLAIN = "fc80937699e645a2ac96257d093d8e7c"

    class Client:
        def __init__(self, hyphenated):
            self.hyphenated = hyphenated

        def call(self, method, path, body=None):
            if path.endswith("/children?page_size=100"):
                heading = {"object": "block", "id": "h1", "type": "heading_3", "has_children": False,
                           "heading_3": {"rich_text": [{"plain_text": "Calendar"}]}}
                return {"results": [heading], "has_more": False}
            return {"object": "block", "id": self.hyphenated, "type": "callout", "has_children": True}

    def test_a_plain_secret_matches_the_hyphenated_api_id(self):
        blocks = card._target(self.Client(self.HYPHENATED), self.PLAIN)
        self.assertEqual(len(blocks), 1)

    def test_a_different_block_is_still_refused(self):
        with self.assertRaises(card.CardError) as error:
            card._target(self.Client("00000000-0000-0000-0000-000000000000"), self.PLAIN)
        self.assertEqual(str(error.exception), "AGENDA_CARD_NOT_OWNED")


class ProtectedRegionDigestTests(unittest.TestCase):
    """The Jira region must compare equal when only metadata moved, differ when its content moved, and say where (names only)."""

    JIRA = "aaaaaaaa-0000-0000-0000-000000000001"

    class Client:
        def __init__(self, edited, text="Example task"):
            self.edited, self.text = edited, text

        def call(self, method, path, body=None):
            if path.endswith("/children?page_size=100"):
                para = {"object": "block", "id": "c1", "type": "paragraph", "has_children": False, "last_edited_time": self.edited,
                        "paragraph": {"rich_text": [{"plain_text": self.text}]}}
                return {"results": [para], "has_more": False}
            return {"object": "block", "id": ProtectedRegionDigestTests.JIRA, "type": "callout", "has_children": True,
                    "last_edited_time": self.edited, "last_edited_by": {"id": self.edited}}

    def digest(self, edited, text="Example task"):
        return card._region_digest(self.Client(edited, text), self.JIRA.replace("-", ""))

    def test_edit_time_alone_is_not_a_change(self):
        self.assertEqual(self.digest("2026-10-03T16:40:00Z"), self.digest("2026-10-03T16:41:00Z"))

    def test_a_content_change_differs_and_is_located_by_position_not_content(self):
        before, after = self.digest("t1"), self.digest("t1", text="Different task")
        self.assertNotEqual(before, after)
        located = card._changed(before, after)
        self.assertEqual((located["fields"], located["children"]), ([], [0]))
        self.assertNotIn("Different", json.dumps(located))


def links(block):
    """[(text, url)] of every linked piece in a rendered event bullet."""
    return [(part["text"]["content"], part["text"]["link"]["url"]) for part in block["bulleted_list_item"]["rich_text"] if part["text"].get("link")]


def event_blocks(snap):
    blocks, _ = card.render(snap, now=NOW)
    return [block for block in blocks if block.get("type") == "bulleted_list_item" and links(block) is not None]


class CalendarDeepLinkTests(unittest.TestCase):
    """The event title opens the event in Google Calendar; the conference (Teams / Meet) is a separate small Join link."""
    CAL = "https://www.google.com/calendar/event?eid=abc123"
    MEET = "https://teams.example.com/meet/42"

    def compacted(self, raw):
        return snapshot.compact(raw, NOW.date(), NOW.date().replace(day=9))

    def test_snapshot_keeps_the_google_event_link_and_the_meeting_link_separately(self):
        both = self.compacted(timed("t1", "Example sync", htmlLink=self.CAL, hangoutLink=self.MEET))
        self.assertEqual((both["calendar_link"], both["meeting_link"]), (self.CAL, self.MEET))
        day = self.compacted(all_day(htmlLink=self.CAL))
        self.assertEqual((day["calendar_link"], day["meeting_link"]), (self.CAL, None))
        conference = self.compacted(timed("t2", "Example call", htmlLink=self.CAL,
                                          conferenceData={"entryPoints": [{"uri": self.MEET}]}))
        self.assertEqual((conference["calendar_link"], conference["meeting_link"]), (self.CAL, self.MEET))

    def test_a_missing_or_non_web_event_link_is_none_and_a_wrong_type_is_invalid(self):
        self.assertIsNone(self.compacted(timed("t3", "Example"))["calendar_link"])
        self.assertIsNone(self.compacted(timed("t4", "Example", htmlLink="javascript:alert(1)"))["calendar_link"])
        with self.assertRaises(snapshot.AgendaError) as caught:
            self.compacted(timed("t5", "Example", htmlLink=7))
        self.assertEqual(str(caught.exception), "AGENDA_EVENT_INVALID")

    def test_timed_event_title_links_to_the_calendar_and_join_links_to_the_meeting(self):
        snap = saved_snapshot([self.compacted(timed("t1", "Example sync", "2026-03-08T09:30:00-05:00", "2026-03-08T10:45:00-05:00",
                                                    htmlLink=self.CAL, hangoutLink=self.MEET, location="Room 2"))])
        block = event_blocks(snap)[0]
        self.assertEqual(links(block), [("Example sync", self.CAL), ("Join", self.MEET)])
        self.assertEqual(card._plain(block), "Now · 9:30 AM–10:45 AM — Example sync · Join · Room 2")

    def test_all_day_event_links_to_the_calendar_and_has_no_join_without_a_meeting(self):
        snap = saved_snapshot([self.compacted(all_day(htmlLink=self.CAL))])
        block = event_blocks(snap)[0]
        self.assertEqual(links(block), [("Example all day", self.CAL)])
        self.assertNotIn("Join", card._plain(block))

    def test_a_meeting_without_a_calendar_link_never_links_the_title_to_the_meeting(self):
        snap = saved_snapshot([self.compacted(timed("t6", "Example call", hangoutLink=self.MEET))])
        self.assertEqual(links(event_blocks(snap)[0]), [("Join", self.MEET)])

    def test_a_snapshot_saved_before_the_field_existed_is_still_valid_and_renders_unlinked(self):
        old = {"id": "old", "title": "Example old", "start": "2026-03-08T11:00:00-05:00", "end": "2026-03-08T11:30:00-05:00",
               "all_day": False, "location": None, "meeting_link": None, "source": "native", "days": ["2026-03-08"]}
        blocks, counts = card.render(saved_snapshot([old]), now=NOW)
        self.assertEqual(counts["events"], 1)
        self.assertEqual(links(event_blocks(saved_snapshot([old]))[0]), [])

    def test_a_non_string_calendar_link_in_a_saved_snapshot_is_refused(self):
        bad = {**self.compacted(timed("t7", "Example")), "calendar_link": 5}
        with self.assertRaises(card.CardError) as caught:
            card.render(saved_snapshot([bad]), now=NOW)
        self.assertEqual(str(caught.exception), "AGENDA_SNAPSHOT_INVALID")

    def test_production_path_calendar_read_to_saved_snapshot_to_card(self):
        calendar = CalendarEvents([timed("p1", "Example sync", "2026-03-08T09:30:00-05:00", "2026-03-08T10:45:00-05:00",
                                         htmlLink=self.CAL, hangoutLink=self.MEET), all_day(htmlLink=self.CAL + "2")])
        database = AgendaSnapshotDB()
        snapshot.run(1, True, calendar=calendar, now=NOW, connect=lambda: database)
        loaded = snapshot.load(database)
        self.assertEqual([event["calendar_link"] for event in loaded["events"]], [self.CAL, self.CAL + "2"])
        rendered = event_blocks(loaded)
        flat = [pair for block in rendered for pair in links(block)]
        self.assertIn(("Example sync", self.CAL), flat)
        self.assertIn(("Join", self.MEET), flat)
        self.assertIn(("Example all day", self.CAL + "2"), flat)
