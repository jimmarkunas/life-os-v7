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

    def test_card_adds_then_removes_then_reads_back_and_preserves_jira_region(self):
        event = snapshot.compact(timed("one", "Example fresh"), NOW.date(), NOW.date().replace(day=9))
        database = AgendaSnapshotDB(saved_snapshot([event]))
        notion = AgendaRegions()
        before_jira = [card._plain(block) for block in notion.children["jira-callout"]]
        counts = card.run(1, True, environ={"CALENDAR_CARD_BLOCK_ID": "calendar-callout"}, client=notion,
                          now=NOW, connect=lambda: database)
        ops = [kind for kind, _ in notion.log]
        append_at, delete_at = ops.index("APPEND"), ops.index("DELETE")
        final_read = max(i for i, (kind, path) in enumerate(notion.log)
                         if kind == "GET" and path.startswith("/blocks/calendar-callout/children"))
        self.assertLess(append_at, delete_at)
        self.assertLess(delete_at, final_read)
        self.assertEqual([card._plain(block) for block in notion.children["jira-callout"]], before_jira)
        self.assertEqual(counts["status"], "fresh")
        self.assertGreater(counts["blocks_written"], 0)

    def test_wrong_heading_block_id_and_duplicate_owner_refuse_writes(self):
        database = AgendaSnapshotDB(saved_snapshot([]))
        for notion, configured in ((AgendaRegions(calendar_title="Other"), "calendar-callout"),
                                   (AgendaRegions(), "jira-callout"),
                                   (AgendaRegions(extra_calendar=True), "calendar-callout")):
            before = len(notion.log)
            with self.assertRaises(Exception):
                card.run(1, True, environ={"CALENDAR_CARD_BLOCK_ID": configured}, client=notion,
                         now=NOW, connect=lambda: database)
            self.assertFalse(any(kind in ("APPEND", "DELETE", "PATCH") for kind, _ in notion.log[before:]))

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
        counts = card.run(1, True, environ={"CALENDAR_CARD_BLOCK_ID": "calendar-callout"}, client=notion,
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
