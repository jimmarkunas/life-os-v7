"""LIFE-164 / LIFE-166 (Jim, Oct 27 2026): every Granola meeting that is not an interview lives as one row of the Meetings database (a row's page holds the word-for-word transcript, notes and
summary above it). A page elsewhere shows a filtered view of the same table, so there is no second page and no link to keep in step.

Routing of a meeting that matches no Interview OS round (granola_match): a client attendee gives TGP (whoever else is on the call), only Bytalos people (and personal mailboxes) gives Bytalos,
with no attendee evidence the title decides (Jim: "By the attendees, then the title"). Anything these rules cannot decide (another company on a Bytalos call, a title naming both sides, nothing at
all) becomes an Unfiled row: Jim picks "File as" (TGP, Bytalos, Personal, Interview or Delete) and the hourly job files it. The caller (granola_auto) owns the catalog, the purge and the per-run cap,
so a meeting leaves Oracle only after its row is written and read back and the catalog row is saved and read back. A row that carries a transcript that is not ours is never touched.
Titles, names and addresses are compared here and never logged: callers get codes and counts only."""
import re

from lifeos.interview import create, granola_write, notion
from lifeos.interview.granola_match import central_day
from lifeos.platform.notion_client import NotionError, rich_text

DATA_SOURCE = "15df6ed1-58d8-47f4-9c6c-a336f04b8e7a"        # the Meetings database (an id is not a secret)
TITLE, DATE, STATUS, FILE_AS, FILED_TO, KEY, LINES = "Meeting", "Meeting date", "Status", "File as", "Filed to", "Source key", "Transcript lines"
SCHEMA = {TITLE: "title", DATE: "date", STATUS: "select", FILE_AS: "select", FILED_TO: "select", KEY: "rich_text", LINES: "number"}
CLIENT, INTERNAL, PERSONAL = "TGP", "Bytalos", "Personal"       # the values of Filed to / File as that carry a transcript
UNFILED, FILED, DELETED = "Unfiled", "Filed", "Deleted"
CLIENT_DOMAIN, OWN_DOMAIN = "tgpinternational.com", "bytalos.com"
PERSONAL_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "icloud.com", "me.com", "yahoo.com"}      # Jim's own mailbox is one of the attendees; it does not make a call external
CLIENT_WORDS, OWN_WORDS = ("tgp", "tpg"), ("bytalos",)
MAX_TITLE = 120


def _domains(attendees):
    return {a.rsplit("@", 1)[1].strip().lower() for a in attendees if "@" in a and a.rsplit("@", 1)[1].strip()}


def _words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def route(meeting):
    """-> (CLIENT | INTERNAL | None, code). Codes: ROUTE_CLIENT_ATTENDEE, ROUTE_INTERNAL_ATTENDEE, ROUTE_CLIENT_TITLE, ROUTE_INTERNAL_TITLE, ROUTE_NONE, ROUTE_OTHER_ATTENDEES, ROUTE_TITLE_AMBIGUOUS."""
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


def check(client):
    """-> "target_ok" | "target_mismatch" | "target_unreadable": the Meetings data source is reachable with this token and has the columns the job writes."""
    try:
        props = client.call("GET", f"/data_sources/{DATA_SOURCE}").get("properties") or {}
        return "target_ok" if all((props.get(name) or {}).get("type") == kind for name, kind in SCHEMA.items()) else "target_mismatch"
    except (NotionError, KeyError, TypeError, ValueError, AttributeError):
        return "target_unreadable"


def title_of(meeting):
    day = central_day(meeting.start) or "undated"
    return f"{day} · {(meeting.title or 'Meeting').strip()}"[:MAX_TITLE]


def _select(name):
    return {"select": {"name": name}}


def _props(meeting, key, status, filed_to=None, lines=None):
    day = central_day(meeting.start)
    out = {TITLE: {"title": rich_text(title_of(meeting))}, STATUS: _select(status), KEY: {"rich_text": rich_text(key)}}
    if day:
        out[DATE] = {"date": {"start": day}}
    if filed_to:
        out[FILED_TO] = _select(filed_to)
    if lines is not None:
        out[LINES] = {"number": lines}
    return out


def _plain(prop):
    kind = (prop or {}).get("type")
    parts = (prop or {}).get(kind) or []
    if kind in ("title", "rich_text"):
        return "".join(p.get("plain_text", (p.get("text") or {}).get("content", "")) for p in parts)
    if kind == "select":
        return (parts or {}).get("name") if isinstance(parts, dict) else None
    return None


def find(client, key):
    """The row for a catalog key, or None. Two rows for one key is a stop (NotionError), never a guess."""
    reply = client.query_data_source(DATA_SOURCE, {"page_size": 3, "filter": {"property": KEY, "rich_text": {"equals": key}}})
    rows = reply.get("results") if isinstance(reply, dict) else None
    if not isinstance(rows, list) or len(rows) > 1:
        raise NotionError("MEETINGS_ROW_AMBIGUOUS")
    return rows[0] if rows else None


def rows_where(client, status, with_choice):
    """-> [(page id, key, File as)] for rows with this Status; with_choice keeps only rows where Jim has picked a File as. Complete or NotionError."""
    flt = {"and": [{"property": STATUS, "select": {"equals": status}}, {"property": FILE_AS, "select": {"is_not_empty": True}}]} if with_choice else {"property": STATUS, "select": {"equals": status}}
    out, cursor = [], None
    for _ in range(20):
        body = {"page_size": 100, "filter": flt, **({"start_cursor": cursor} if cursor else {})}
        reply = client.query_data_source(DATA_SOURCE, body)
        if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
            raise NotionError("MEETINGS_READ_INCOMPLETE")
        out += [(r["id"], _plain(r["properties"].get(KEY)), _plain(r["properties"].get(FILE_AS))) for r in reply["results"]]
        if not reply.get("has_more"):
            return out
        cursor = reply.get("next_cursor")
    raise NotionError("MEETINGS_READ_INCOMPLETE")


def close_gone(client, present_keys, live):
    """Close every Unfiled row whose meeting is no longer on Oracle (nothing is left to file or delete). -> how many rows (closed, or would be closed in a dry run)."""
    gone = [page for page, key, _ in rows_where(client, UNFILED, False) if key not in present_keys]
    if live:
        for page in gone:
            set_status(client, page, DELETED)
    return len(gone)


def unfiled_row(client, meeting, key, live):
    """Make sure an Unfiled row exists for a meeting nothing could place. -> "created" | "exists" | "planned"."""
    if find(client, key):
        return "exists"
    if not live:
        return "planned"
    client.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE}, "properties": _props(meeting, key, UNFILED)})
    return "created"


def set_status(client, page_id, status, filed_to=None, lines=None):
    props = {STATUS: _select(status)}
    if filed_to:
        props[FILED_TO] = _select(filed_to)
    if lines is not None:
        props[LINES] = {"number": lines}
    client.call_once("PATCH", f"/pages/{page_id}", {"properties": props})


def _read_back(client, page_id):
    found = notion.children(client, page_id, [400])
    texts = [notion.text(b) for b in found if b.get("type") == "paragraph"]
    return (texts[0] if texts else ""), texts[2:], len(found)


def write(client, filed_to, meeting, key, envelope, live, header=()):
    """File one meeting's transcript on its Meetings row, creating the row if it has none. -> (code, counts).
    Codes: TRANSCRIPT_WRITTEN, TRANSCRIPT_NOOP, TRANSCRIPT_PLANNED (not live), CLIENT_PAGE_NOT_OURS, TRANSCRIPT_WRITE_FAILED, TRANSCRIPT_READBACK_MISMATCH.
    counts: segments, blocks_planned, blocks_written, readback_ok, readback_failed."""
    counts = {"segments": len(envelope.segments), "blocks_planned": 0, "blocks_written": 0, "readback_ok": 0, "readback_failed": 0}
    marker = f"{create.ACCEPTED}{envelope.source_content_sha256}"
    body = granola_write.blocks(envelope, header)
    want = granola_write.header_lines(header) + granola_write.lines(envelope)
    counts["blocks_planned"] = len(body)
    row = find(client, key)
    page = row["id"] if row else None
    if page:
        first, got, total = _read_back(client, page)
        if first == marker:                                         # a page of ours: it counts as filed only if it reads back whole, never on the marker alone
            if granola_write.digest(got) != granola_write.digest(want) or total != len(body):
                counts["readback_failed"] = 1
                return "TRANSCRIPT_READBACK_MISMATCH", counts
            if live and _plain(row["properties"].get(STATUS)) != FILED:
                set_status(client, page, FILED, filed_to, len(envelope.segments))
            return "TRANSCRIPT_NOOP", counts
        if total:
            return "CLIENT_PAGE_NOT_OURS", counts                   # the row's page already holds something that is not this transcript: never touched
    if not live:
        return "TRANSCRIPT_PLANNED", counts
    batch = granola_write.FIRST_BATCH
    try:
        if page is None:
            page = client.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE}, "properties": _props(meeting, key, UNFILED), "children": body[:batch]})["id"]
            counts["blocks_written"] = min(len(body), batch)
            start = batch
        else:
            start = 0
        for at in range(start, len(body), batch):
            client.call("PATCH", f"/blocks/{page}/children", {"children": body[at:at + batch]})
            counts["blocks_written"] = min(len(body), at + batch)
    except NotionError:
        return "TRANSCRIPT_WRITE_FAILED", counts
    try:
        first, got, total = _read_back(client, page)
    except NotionError:
        return "TRANSCRIPT_READBACK_MISMATCH", counts
    if first == marker and granola_write.digest(got) == granola_write.digest(want) and total == len(body):
        counts["readback_ok"] = 1
        try:
            set_status(client, page, FILED, filed_to, len(envelope.segments))
        except NotionError:
            pass                                                    # the transcript is written and read back; a status that did not stick is repaired by the next run's NOOP path
        return "TRANSCRIPT_WRITTEN", counts
    counts["readback_failed"] = 1
    return "TRANSCRIPT_READBACK_MISMATCH", counts
