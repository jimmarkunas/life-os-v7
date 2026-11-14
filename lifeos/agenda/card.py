"""Render the saved Agenda snapshot into V7's one Calendar callout."""
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, db, report_region, router
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError, rich_text
from lifeos.platform.report_region import plain
from . import snapshot as agenda_snapshot

LOCAL_TZ = "America/Chicago"
TZ = ZoneInfo(LOCAL_TZ)
STALE_HOURS = 3
CARD_TITLE = router.CALENDAR_REGION
MODULE = router.OWNERS[CARD_TITLE]


class CardError(NotionError):
    """Fixed codes only; never expose calendar content, IDs, or URLs."""


def _owned(blocks):
    """The first child is the Calendar heading: exactly "Calendar", or "Calendar (11/13 · 1:18 PM CT)" (an older heading says "Updated 1:18 PM CT") once the update time shares the heading (D119)."""
    if not (blocks and blocks[0].get("type") in ("heading_3", "heading_4")):
        return False
    return report_region.titled(plain(blocks[0]), CARD_TITLE)


def _fail(code):
    return CardError("AGENDA_" + code)


def _configured(environ):
    """The Jira callout must still be configured (a missing one means the page is not set up as expected); nothing is read from it. LIFE-613: the proof that this card writes only inside its own
    region is the scope guard on the client, which refuses any other block before the write is sent, not a comparison of the other cards' regions afterwards."""
    if not (environ.get("JIRA_CARD_BLOCK_ID") or "").strip():
        raise CardError("AGENDA_PROTECTED_REGION_UNAVAILABLE")


def _target(client, block_id):
    # The configured ID is the single authority for this region (D58: one owner per region);
    # scanning the Daily Report tree would be costly and is intentionally unnecessary.
    meta = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not report_region.same_id(meta.get("id"), block_id):
        raise CardError("AGENDA_CARD_NOT_OWNED")
    blocks = report_region.children(client, block_id, _fail)
    if not _owned(blocks):
        raise CardError("AGENDA_CARD_NOT_OWNED")
    with fixed(router.RouterError, CardError, "AGENDA_CARD_NOT_OWNED"):
        router.check_write(MODULE, [CARD_TITLE])
    return blocks


def _text(content, url=None):
    parts = rich_text(content)
    if url and parts:
        parts[0]["text"]["link"] = {"url": url}
    return parts


URL = re.compile(r"https?://[^\s<>\"']+")


def _location_parts(location, meeting_link=None):
    """The location as ` · text`, with any web address in it as a small link named for its site (a location that is only a link, such as a live-session address, printed its
    whole address as plain text that wrapped onto a second line). Plain words stay plain; an address the Join link already carries is not repeated; a very long address stays text."""
    pieces, last = [], 0
    for found in URL.finditer(location):
        url = found.group(0).rstrip(".,;)")
        pieces.append((location[last:found.start()], None))
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if url == meeting_link:
            pass                                                                        # the Join link already carries it
        elif len(url) <= 1900 and host:
            pieces.append((host, url))
        else:
            pieces.append((url, None))
        last = found.start() + len(url)
    pieces.append((location[last:], None))
    pieces = [(content.strip() if url is None else content, url) for content, url in pieces if url or content.strip()]
    if not pieces:
        return []
    parts = _text(" · ")
    for index, (content, url) in enumerate(pieces):
        if index and (url or content[0] not in ".,;)"):
            parts += _text(" ")                                                         # the space is plain, never inside a link's underline
        parts += _text(content, url)
    return parts


def _paragraph(content):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(content)}}


def _heading(content):
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _text(content)}}


def _day_events(snapshot, day):
    return [event for event in snapshot["events"] if day in event.get("days", [])]


def _line(event):
    return (" ".join(event["title"].split()).casefold(), event["all_day"], event["start"], event["end"])


def _merged(events):
    """One line per title and time: a Gmail-created copy beside the event you made shows once, as the richer of the two (a meeting link, then the longer location; a tie keeps the first). Display only: the snapshot is unchanged."""
    kept = {}
    for event in events:
        rank = (bool(event.get("meeting_link")), len(event.get("location") or ""))
        if _line(event) not in kept or rank > kept[_line(event)][0]:
            kept[_line(event)] = (rank, event)
    return [event for event in events if kept[_line(event)][1] is event]


def _instant(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _today_marker(events, now):
    timed = [event for event in events if not event["all_day"]]
    active = next((event for event in timed if _instant(event["start"]) <= now < _instant(event["end"])), None)
    if active:
        return active["id"], "Now"
    upcoming = next((event for event in timed if _instant(event["start"]) > now), None)
    if upcoming:
        return upcoming["id"], "Next"
    all_day = next((event for event in events if event["all_day"]), None)
    return (all_day["id"], "Now") if all_day else (None, None)


def _event_block(event, marker=None):
    if event["all_day"]:
        prefix = "All day — "
    else:
        start, end = _instant(event["start"]), _instant(event["end"])
        prefix = f"{start.strftime('%-I:%M %p')}–{end.strftime('%-I:%M %p')} — "
    if marker:
        prefix = f"{marker} · {prefix}"
    parts = _text(prefix) + _text(event["title"], event.get("calendar_link"))          # the title opens the event in Google Calendar, never the meeting
    if event.get("meeting_link"):
        parts += _text(" · ") + _text("Join", event["meeting_link"])                      # the conference (Teams / Meet) is its own small link
    if event.get("location"):
        parts += _location_parts(event["location"], event.get("meeting_link"))
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parts}}


def _status(snapshot, stale):
    with fixed((KeyError, TypeError, ValueError), CardError, "AGENDA_SNAPSHOT_INVALID"):
        taken = _instant(snapshot["taken_at"])
    return f"STALE · last accepted {ct_stamp.when(taken)}" if stale else ct_stamp.when(taken)


def _heading_text(snapshot, stale):
    return f"{CARD_TITLE} ({_status(snapshot, stale)})"


def _snapshot_counts(snapshot, stale):
    if not isinstance(snapshot, dict) or snapshot.get("schema") != agenda_snapshot.SCHEMA_V or not isinstance(snapshot.get("events"), list):
        raise CardError("AGENDA_SNAPSHOT_INVALID")
    today, tomorrow = snapshot.get("today"), snapshot.get("tomorrow")
    if not isinstance(today, str) or not isinstance(tomorrow, str):
        raise CardError("AGENDA_SNAPSHOT_INVALID")
    events = snapshot["events"]
    for event in events:
        if (not isinstance(event, dict) or not isinstance(event.get("days"), list) or any(day not in (today, tomorrow) for day in event["days"])
                or not isinstance(event.get("id"), str) or not isinstance(event.get("title"), str) or not isinstance(event.get("all_day"), bool)
                or not isinstance(event.get("start"), str) or not isinstance(event.get("end"), str)
                or any(event.get(key) is not None and not isinstance(event[key], str) for key in ("location", "meeting_link", "calendar_link"))):      # calendar_link is optional: snapshots saved before it existed stay valid
            raise CardError("AGENDA_SNAPSHOT_INVALID")
        with fixed((TypeError, ValueError), CardError, "AGENDA_SNAPSHOT_INVALID"):
            if event["all_day"]:
                datetime.fromisoformat(event["start"])
                datetime.fromisoformat(event["end"])
            else:
                if _instant(event["end"]) <= _instant(event["start"]):
                    raise ValueError
    return {"events": len(events), "all_day": sum(bool(event.get("all_day")) for event in events),
            "today": sum(today in event["days"] for event in events),
            "tomorrow": sum(tomorrow in event["days"] for event in events),
            "saved": 0, "blocks_written": 0, "status": "stale" if stale else "fresh"}


def render(snapshot, stale=False, now=None):
    now = agenda_snapshot.local_now(now)
    counts = _snapshot_counts(snapshot, stale)
    blocks = []
    today, tomorrow = _merged(_day_events(snapshot, snapshot["today"])), _merged(_day_events(snapshot, snapshot["tomorrow"]))
    counts["merged"] = counts["today"] + counts["tomorrow"] - len(today) - len(tomorrow)               # duplicate lines hidden
    marker_id, marker = _today_marker(today, now)
    for label, day, events in (("Today", snapshot["today"], today), ("Tomorrow", snapshot["tomorrow"], tomorrow)):
        blocks.append(_heading(label))
        if events:
            blocks.extend(_event_block(event, marker if label == "Today" and event["id"] == marker_id else None) for event in events)
        else:
            blocks.append(_paragraph("Nothing scheduled"))
    return blocks, counts


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
                   "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _append(client, block_id, blocks):
    for start in range(0, len(blocks), 100):
        chunk = blocks[start:start + 100]
        result = client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}/children", {"children": chunk})
        if not isinstance(result, dict) or not isinstance(result.get("results"), list) or len(result["results"]) != len(chunk):
            raise CardError("AGENDA_CARD_APPEND_MISMATCH")


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    block_id = (environ.get("CALENDAR_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"events": 0, "all_day": 0, "today": 0, "tomorrow": 0, "saved": 0,
                "blocks_written": 0, "status": "not_configured"}
    with fixed(Exception, CardError, "AGENDA_STORE_FAILED", keep=NotionError):
        with (connect or db.connect)() as connection:
            saved = agenda_snapshot.load(connection)
    if saved is None:
        raise CardError("AGENDA_SNAPSHOT_MISSING")
    now = agenda_snapshot.local_now(now)
    with fixed((KeyError, TypeError, ValueError), CardError, "AGENDA_SNAPSHOT_INVALID"):
        taken = _instant(saved["taken_at"])
    stale = now - taken > timedelta(hours=STALE_HOURS)
    blocks, counts = render(saved, stale, now)
    client = report_region.ScopedClient(client or _client(environ), block_id, _fail)      # LIFE-613: every write is checked against this card's own callout first
    existing = _target(client, block_id)
    heading = _heading_text(saved, stale)
    heading_block = existing[0]
    heading_patch = {heading_block["type"]: {"rich_text": rich_text(heading)}}
    if stale:
        if len(existing) < 2 or not plain(heading_block).strip().startswith(CARD_TITLE + " ("):
            raise CardError("AGENDA_CARD_STALE_NO_BASELINE")                 # V7 has not written this card yet: nothing known-good of ours to keep showing
        if live:
            _configured(environ)
            client.call("PATCH", f"/blocks/{quote(heading_block['id'], safe='')}", heading_patch)     # only the heading text changes; the events stay as accepted
            after = report_region.children(client, block_id, _fail)
            if (not _owned(after) or plain(after[0]) != heading or after[0].get("id") != heading_block["id"]
                    or json.dumps(existing[1:], sort_keys=True) != json.dumps(after[1:], sort_keys=True)):
                raise CardError("AGENDA_CARD_VERIFY_FAILED")
            counts["blocks_written"] = 1
        return counts
    if not live:
        return counts
    _configured(environ)
    _append(client, block_id, blocks)
    for old in existing[1:]:
        try:
            client.call("DELETE", f"/blocks/{quote(old['id'], safe='')}")
        except NotionError as error:
            if str(error) != "NOTION_HTTP_404":
                raise
    client.call("PATCH", f"/blocks/{quote(heading_block['id'], safe='')}", heading_patch)   # the update time shares the heading (D119)
    after = report_region.children(client, block_id, _fail)                    # read back the configured Calendar region
    if not _owned(after) or len(after) != 1 + len(blocks) or plain(after[0]) != heading or after[0].get("id") != heading_block["id"]:
        raise CardError("AGENDA_CARD_VERIFY_FAILED")
    counts["blocks_written"] = len(blocks)
    return counts
