"""Read the complete Chicago today/tomorrow calendar window into a private snapshot."""
import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import db
from lifeos.platform.gcal import GcalError, GoogleCalendar
from lifeos.platform.notion_client import NotionError
from lifeos.platform.snapshot_store import Store

LOCAL_TZ = "America/Chicago"
TZ = ZoneInfo(LOCAL_TZ)
SCHEMA_V = 1
STORE = Store("v7_agenda_snapshot")


class AgendaError(NotionError):
    """Fixed codes only; calendar content and identifiers never enter errors."""


def _local_now(now=None):
    now = now or datetime.now(TZ)
    return now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)


def window(now=None):
    today = _local_now(now).date()
    start = datetime.combine(today, time.min, TZ)
    end = datetime.combine(today + timedelta(days=2), time.min, TZ)  # API timeMax is exclusive
    return start.isoformat(), end.isoformat(), today, today + timedelta(days=1)


def _instant(part):
    raw = part.get("dateTime")
    if not isinstance(raw, str) or not raw:
        raise AgendaError("AGENDA_EVENT_INVALID")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(part.get("timeZone") or LOCAL_TZ))
        return value.astimezone(TZ)
    except Exception:
        raise AgendaError("AGENDA_EVENT_INVALID") from None


def compact(event, today, tomorrow):
    """Keep only report fields; omit cancelled, declined, and working-location records."""
    if event.get("status") == "cancelled" or event.get("eventType") == "workingLocation":
        return None
    attendees = event.get("attendees") or []
    if not isinstance(attendees, list) or any(not isinstance(attendee, dict) for attendee in attendees):
        raise AgendaError("AGENDA_EVENT_INVALID")
    if any(attendee.get("self") is True and attendee.get("responseStatus") == "declined" for attendee in attendees):
        return None
    start, end = event.get("start"), event.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise AgendaError("AGENDA_EVENT_INVALID")
    all_day = isinstance(start.get("date"), str) and not start.get("dateTime")
    days = []
    if all_day:
        try:
            start_day, end_day = date.fromisoformat(start["date"]), date.fromisoformat(end["date"])
        except ValueError:
            raise AgendaError("AGENDA_EVENT_INVALID") from None
        if end_day <= start_day:
            raise AgendaError("AGENDA_EVENT_INVALID")
        start_value, end_value = start_day.isoformat(), end_day.isoformat()
        for day in (today, tomorrow):
            if start_day <= day < end_day:
                days.append(day.isoformat())
    else:
        if start.get("date") or end.get("date"):
            raise AgendaError("AGENDA_EVENT_INVALID")
        start_at, end_at = _instant(start), _instant(end)
        if end_at < start_at:                                     # zero-length events are legal in Google
            raise AgendaError("AGENDA_EVENT_INVALID")
        start_value, end_value = start_at.isoformat(), end_at.isoformat()
        for day in (today, tomorrow):
            day_start = datetime.combine(day, time.min, TZ)
            day_end = datetime.combine(day + timedelta(days=1), time.min, TZ)
            if start_at < day_end and (end_at > day_start or (end_at == start_at and start_at >= day_start)):
                days.append(day.isoformat())
    if not days:
        return None
    event_id, title = event.get("id"), event.get("summary")
    if not isinstance(event_id, str) or not event_id:
        raise AgendaError("AGENDA_EVENT_INVALID")
    if title is not None and not isinstance(title, str):
        raise AgendaError("AGENDA_EVENT_INVALID")
    title = (title or "").strip() or "(no title)"             # untitled events are normal on a shared calendar
    extended = event.get("extendedProperties") or {}
    if not isinstance(extended, dict):
        raise AgendaError("AGENDA_EVENT_INVALID")
    private = extended.get("private") or {}
    if not isinstance(private, dict):
        raise AgendaError("AGENDA_EVENT_INVALID")
    conference = event.get("conferenceData") or {}
    if not isinstance(conference, dict):
        raise AgendaError("AGENDA_EVENT_INVALID")
    entry_points = conference.get("entryPoints") or [] if isinstance(conference, dict) else []
    meeting = event.get("hangoutLink") or next((point.get("uri") for point in entry_points
                                                if isinstance(point, dict) and isinstance(point.get("uri"), str)), None)
    location = event.get("location")
    if location is not None and not isinstance(location, str):
        raise AgendaError("AGENDA_EVENT_INVALID")
    html_link = event.get("htmlLink")
    if html_link is not None and not isinstance(html_link, str):
        raise AgendaError("AGENDA_EVENT_INVALID")
    calendar_link = html_link if html_link and html_link.startswith(("https://", "http://")) else None     # the event's own page in Google Calendar (the title's link)
    return {"id": event_id, "title": title, "start": start_value, "end": end_value, "all_day": all_day,
            "location": location or None, "meeting_link": meeting or None, "calendar_link": calendar_link,
            "source": "bridge" if private.get("v7_src") else "native", "days": days}


def build(calendar, now=None):
    now = _local_now(now)
    time_min, time_max, today, tomorrow = window(now)
    try:
        raw_events = calendar.list_events(time_min, time_max)
    except GcalError:
        raise
    except Exception:
        raise AgendaError("AGENDA_READ_FAILED") from None
    if not isinstance(raw_events, list):
        raise AgendaError("AGENDA_LISTING_INCOMPLETE")
    events, seen = [], set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            raise AgendaError("AGENDA_LISTING_INCOMPLETE")
        try:
            item = compact(raw, today, tomorrow)
        except AgendaError:
            raise
        except Exception:
            raise AgendaError("AGENDA_EVENT_INVALID") from None
        if item is None:
            continue
        if item["id"] in seen:
            raise AgendaError("AGENDA_LISTING_INVALID")
        seen.add(item["id"])
        events.append(item)
    return {"schema": SCHEMA_V, "taken_at": now.isoformat(), "timezone": LOCAL_TZ,
            "today": today.isoformat(), "tomorrow": tomorrow.isoformat(), "events": events}


def save(connection, snapshot):
    STORE.save(connection, 1, snapshot)


def load(connection):
    try:
        return STORE.load(connection, 1)
    except (TypeError, ValueError):
        raise AgendaError("AGENDA_SNAPSHOT_INVALID") from None


def run(limit, live, environ=os.environ, calendar=None, now=None, connect=None):
    calendar = calendar or GoogleCalendar.from_env(environ)
    snapshot = build(calendar, now)
    events = snapshot["events"]
    counts = {"events": len(events), "all_day": sum(event["all_day"] for event in events),
              "today": sum(snapshot["today"] in event["days"] for event in events),
              "tomorrow": sum(snapshot["tomorrow"] in event["days"] for event in events),
              "saved": 0, "blocks_written": 0, "status": "fresh"}
    if live:
        try:
            with (connect or db.connect)() as connection:
                STORE.save_verified(connection, 1, snapshot, lambda code: AgendaError("AGENDA_" + code))
            counts["saved"] = 1
        except AgendaError:
            raise
        except Exception:
            raise AgendaError("AGENDA_STORE_FAILED") from None
    return counts
