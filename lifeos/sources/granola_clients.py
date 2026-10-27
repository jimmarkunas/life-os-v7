"""LIFE-164 (Jim, Oct 27 2026): the Granola bridge for active client work. A synced meeting that matches no Interview OS round (granola_match) is routed to a client page instead of being left
on Oracle: a meeting with a client attendee is a client meeting (TGP Project page, "Client Meetings" callout), a Bytalos-only meeting is internal (Bytalos page, "Internal Meetings" callout).
Routing is by attendees first, then the title (Jim: "By the attendees, then the title"). One page per meeting, word-for-word transcript, notes and summary above it when Oracle supplies them.

The page is created under the project page (the Notion API cannot create a page inside a callout) and listed in the callout with a link; the page is written and read back before
anything else happens, exactly as granola_write does for a round. The caller (granola_auto) owns the catalog, the purge and the per-run cap, so a client meeting is purged from Oracle only after
this page is written and read back and the catalog row is saved and read back. A routing rule that cannot decide (title names both sides, or other companies are in the call) is a fixed code
and no write. Titles, names and addresses are compared here and never logged: callers get codes and counts only."""
import re
from dataclasses import dataclass

from lifeos.interview import create, granola_write, notion
from lifeos.interview.granola_match import central_day
from lifeos.platform.notion_client import NotionError, rich_text


@dataclass(frozen=True)
class Target:
    key: str                         # short label used in counts and in the catalog's round key
    parent: str                      # the project page the meeting page is created under (an id is not a secret)
    callout: str                     # the callout block that lists the meeting pages


CLIENT = Target("client", "3e63c5a05926804dbbc1cfdc982e2a1e", "4063c5a0592680f982fde391bb1e3a6a")        # TGP Project page, "Client Meetings" callout
INTERNAL = Target("internal", "3ef3c5a0592680a9aed8c3e990a7d29c", "4063c5a05926804185a6f755a90ddc64")      # Bytalos page, "Internal Meetings" callout
CLIENT_DOMAIN, OWN_DOMAIN = "tgpinternational.com", "bytalos.com"
PERSONAL_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "icloud.com", "me.com", "yahoo.com"}      # Jim's own mailbox is one of the attendees; it does not make a call external
CLIENT_WORDS, OWN_WORDS = ("tgp", "tpg"), ("bytalos",)
MAX_TITLE = 120


def _domains(attendees):
    return {a.rsplit("@", 1)[1].strip().lower() for a in attendees if "@" in a and a.rsplit("@", 1)[1].strip()}


def _words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def route(meeting):
    """-> (Target | None, code). Codes: ROUTE_CLIENT_ATTENDEE, ROUTE_INTERNAL_ATTENDEE, ROUTE_CLIENT_TITLE, ROUTE_INTERNAL_TITLE, ROUTE_NONE, ROUTE_OTHER_ATTENDEES, ROUTE_TITLE_AMBIGUOUS."""
    domains = _domains(meeting.attendees)
    if CLIENT_DOMAIN in domains:
        return CLIENT, "ROUTE_CLIENT_ATTENDEE"                          # a client attendee makes it a client meeting, whoever else is on the call
    if OWN_DOMAIN in domains:
        if domains - {OWN_DOMAIN} - PERSONAL_DOMAINS:
            return None, "ROUTE_OTHER_ATTENDEES"                        # another company is on the call: not guessed
        return INTERNAL, "ROUTE_INTERNAL_ATTENDEE"
    words = _words(meeting.title)
    client, own = bool(words & set(CLIENT_WORDS)), bool(words & set(OWN_WORDS))
    if client and own:
        return None, "ROUTE_TITLE_AMBIGUOUS"
    if client:
        return CLIENT, "ROUTE_CLIENT_TITLE"
    if own:
        return INTERNAL, "ROUTE_INTERNAL_TITLE"
    return None, "ROUTE_NONE"


def check(client, target):
    """-> "target_ok" | "target_unreadable": the project page and the callout are reachable with this token and the callout is a callout."""
    try:
        page = client.call("GET", f"/pages/{target.parent}")
        block = client.call("GET", f"/blocks/{target.callout}")
        ok = not page.get("archived") and not page.get("in_trash") and block.get("type") == "callout" and not block.get("archived") and not block.get("in_trash")
        return "target_ok" if ok else "target_mismatch"
    except (NotionError, KeyError, TypeError, ValueError, AttributeError):
        return "target_unreadable"


def title_of(meeting):
    day = central_day(meeting.start) or "undated"
    return f"{day} · {(meeting.title or 'Meeting').strip()}"[:MAX_TITLE]


def _pages_titled(client, parent, title):
    found = []
    for block in notion.children(client, parent, [50]):
        if block.get("type") == "child_page" and (block.get("child_page") or {}).get("title") == title:
            found.append(block["id"])
    return found


def _read_back(client, page_id):
    """(marker, transcript lines, block count) from Notion, the same reading granola_write uses."""
    found = notion.children(client, page_id, [400])
    texts = [notion.text(b) for b in found if b.get("type") == "paragraph"]
    return (texts[0] if texts else ""), texts[2:], len(found)


def _listed(client, callout, page_id):
    for block in notion.children(client, callout, [20]):
        if block.get("type") == "link_to_page" and (block.get("link_to_page") or {}).get("page_id", "").replace("-", "") == page_id.replace("-", ""):
            return True
    return False


def _list_in_callout(client, target, page_id):
    """Add a link to the meeting page inside the callout, once. -> True when the link is there."""
    try:
        if _listed(client, target.callout, page_id):
            return True
        client.call("PATCH", f"/blocks/{target.callout}/children", {"children": [{"object": "block", "type": "link_to_page", "link_to_page": {"type": "page_id", "page_id": page_id}}]})
        return True
    except NotionError:
        return False


def write(client, target, meeting, envelope, live, header=()):
    """-> (code, counts). Codes: TRANSCRIPT_WRITTEN, TRANSCRIPT_NOOP, TRANSCRIPT_PLANNED (not live), CLIENT_PAGE_NOT_OURS, TRANSCRIPT_WRITE_FAILED, TRANSCRIPT_READBACK_MISMATCH.
    counts: segments, blocks_planned, blocks_written, readback_ok, readback_failed, listed (1 when the callout shows the page)."""
    counts = {"segments": len(envelope.segments), "blocks_planned": 0, "blocks_written": 0, "readback_ok": 0, "readback_failed": 0, "listed": 0}
    title = title_of(meeting)
    marker = f"{create.ACCEPTED}{envelope.source_content_sha256}"
    existing = _pages_titled(client, target.parent, title)
    if len(existing) > 1:
        return "CLIENT_PAGE_NOT_OURS", counts
    if existing:
        first, _, _ = _read_back(client, existing[0])
        if first != marker:
            return "CLIENT_PAGE_NOT_OURS", counts                 # a page of that title that is not this transcript: never touched, never duplicated
        if live:
            counts["listed"] = int(_list_in_callout(client, target, existing[0]))
        return "TRANSCRIPT_NOOP", counts
    body = granola_write.blocks(envelope, header)
    counts["blocks_planned"] = len(body)
    if not live:
        return "TRANSCRIPT_PLANNED", counts
    want = granola_write.header_lines(header) + granola_write.lines(envelope)
    try:
        made = client.call("POST", "/pages", {"parent": {"type": "page_id", "page_id": target.parent}, "properties": {"title": {"type": "title", "title": rich_text(title)}},
                                              "children": body[:granola_write.FIRST_BATCH]})["id"]
        counts["blocks_written"] = min(len(body), granola_write.FIRST_BATCH)
        for start in range(granola_write.FIRST_BATCH, len(body), granola_write.FIRST_BATCH):
            client.call("PATCH", f"/blocks/{made}/children", {"children": body[start:start + granola_write.FIRST_BATCH]})
            counts["blocks_written"] = min(len(body), start + granola_write.FIRST_BATCH)
    except NotionError:
        return "TRANSCRIPT_WRITE_FAILED", counts
    try:
        first, got, total = _read_back(client, made)
    except NotionError:
        return "TRANSCRIPT_READBACK_MISMATCH", counts
    if first == marker and granola_write.digest(got) == granola_write.digest(want) and total == len(body):
        counts["readback_ok"] = 1
        counts["listed"] = int(_list_in_callout(client, target, made))
        return "TRANSCRIPT_WRITTEN", counts
    counts["readback_failed"] = 1
    return "TRANSCRIPT_READBACK_MISMATCH", counts
