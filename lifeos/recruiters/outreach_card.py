"""LIFE-20: the Daily Report's one "🤝 Recruiter Outreach" table, refreshed in place from the Recruiters data source after the Recruiters stage. V7 owns it exclusively
(router.OWNERS); like the Hiring Pipeline it is a floating region, not a callout.

The region is anchored on the already pinned Hiring Pipeline table (HIRING_TABLE_BLOCK_ID): the block right after it, in the same parent, must be a heading_3 titled exactly
`🤝 Recruiter Outreach` or `🤝 Recruiter Outreach (stamp)`, then one four-column table whose header row is exactly HEADERS, then anything but another table. Only the heading's
stamp and the table's data rows are ever written; the header row, the link under the table, the Historical Snapshot and every other block on the page are digested before the
write and must be identical after it. Anything else raises: nothing is created, moved or guessed.

One table, four columns, open rows only (Action Status Needs Action or Waiting, not Done, Active): Needs Action first, then Waiting, each by Follow-up Date (none last), then the
latest contact first. A row with any other status, including a blank one, is not an action item and is not shown.

Original Contact, in order: the row's First Contact (set by the Recruiters stage when it creates the row, from that message's own date); else a date already shown in this
table for the same recruiter (same name and the same contact or message link, one unique match), which keeps the verified dates that predate the field; else "Not verified".
Either date must not be after the row's Last Contact. Last Contact itself and the row's creation time are never used, and nothing is backfilled.

Fail closed: if the Recruiters stage failed this run, or the source cannot be read completely, the rows are kept as they are and the heading says DEGRADED. Live writes need
--live AND the repo variable RECRUITER_OUTREACH=on; otherwise the region is validated and the counts say what would change. Fixed codes and counts only."""
import json
import os
import re
from datetime import date, datetime, timezone
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from lifeos.platform import report_region as R, router
from lifeos.platform.notion_client import Client, NotionError
from . import notion as store

TITLE = router.RECRUITER_OUTREACH_REGION
MODULE = router.OWNERS[TITLE]
HEADERS = ("Recruiter · Employer", "Channel · Role", "Original Contact", "Next Step · Due")      # Jim's approved four columns (LIFE-20, Oct 23 2026)
ORDER = (store.NEEDS_ACTION, store.WAITING)
MAX_ROWS = 50
NOT_VERIFIED, NO_DUE = "Not verified", "Due date not set"
CHANNELS = (("mail.google.com", "Gmail"), ("outlook.office.com", "Outlook"), ("outlook.office365.com", "Outlook"), ("outlook.live.com", "Outlook"), ("linkedin.com", "LinkedIn"))
SKIP = (router.DCC_REGION,)                                      # as for the Hiring Pipeline: the DCC callout is recorded by id only
CHICAGO = ZoneInfo("America/Chicago")


class OutreachError(NotionError):
    """Fixed codes only; never a name, company, link or id."""


def fail(code):
    return OutreachError(f"RECRUITER_OUTREACH_{code}")


def writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})      # the Daily Report integration


def source(environ):
    return Client(environ, token_name="NOTION_RECRUITERS_TOKEN", source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")


def _step(name, call):
    try:
        return call()
    except NotionError as error:
        if isinstance(error, OutreachError):
            raise
        raise fail(f"{name}:{error}") from None


# --- the rows ---

def _link(url):
    """An https link as recorded, or None: nothing is built or guessed."""
    url = (url or "").strip()
    parts = urlparse(url)
    return url if parts.scheme == "https" and parts.netloc else None


def _day(text):
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def _shown(day):
    return f"{day:%b} {day.day}, {day.year}"


def _channel(row):
    link = _link(row["source_url"])
    host = urlparse(link).netloc.lower() if link else ""
    label = next((name for domain, name in CHANNELS if host == domain or host.endswith("." + domain)), None) or row["medium"].strip() or "Channel not recorded"
    return (f"{label} ↗", link, "bold") if link else (label, None, "bold")


def _due(row, today):
    day = _day(row["follow_up"])
    if day is None:
        return NO_DUE
    return _shown(day) + (" · overdue" if day < today else " · due today" if day == today else "")


def _shown_day(text):
    try:
        return datetime.strptime(text, "%b %d, %Y").date()
    except ValueError:
        return None


def kept_dates(table_rows):
    """The table's current data rows -> [(name, links, day)] for each row whose Original Contact cell is one plain date (no link, no other text)."""
    out = []
    for row in table_rows:
        view = R.live_view(row)
        if len(view) != 4 or not view[0] or len(view[2]) != 1 or view[2][0][1] is not None:
            continue
        day = _shown_day(view[2][0][0].strip())
        if day:
            out.append((view[0][0][0].strip().lower(), {link for cell in view[:2] for _, link, _ in cell if link}, day))
    return out


def _local_day(text):
    """A Notion date or datetime -> its Central Time calendar day, or None."""
    try:
        moment = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment.astimezone(CHICAGO) if moment.tzinfo else moment).date()


def first_contact(row, kept):
    """The row's First Contact; else the date this table already shows for exactly this recruiter (same name AND a shared contact or message link, one match only); else None.
    Never after the row's Last Contact (a first contact cannot follow the latest one), and never derived from Last Contact itself."""
    day = _local_day(row.get("first_contact"))
    if day is None:
        mine = {u for u in (_link(row["contact"]), _link(row["source_url"])) if u}
        found = [d for name, links, d in kept if name == row["person"].strip().lower() and links & mine]
        day = found[0] if len(found) == 1 else None
    latest = _local_day(row["last_contact"])
    return day if day and latest and day <= latest else None


def cells(row, today, first=None):
    """One open row -> the four cells. Line 1 bold, line 2 gray (D222 spans); the name links to the verified contact channel (the database's Contact link, else the original
    message), the channel to the original message only. `first`: the verified first-contact date (first_contact), else "Not verified"."""
    contact = _link(row["contact"]) or _link(row["source_url"])
    return [[(row["person"].strip() or "Name not recorded", contact, "bold"), ("\n" + (row["company"].strip() or "Company not recorded"), None, "gray")],
            [_channel(row), ("\n" + (row["role"].strip() or "Role not recorded"), None, "gray")],
            [(_shown(first) if first else NOT_VERIFIED, None)],
            [(row["action"], None, "bold"), ("\n" + _due(row, today), None, "gray")]]


def ordered(rows):
    """Open rows only, Needs Action before Waiting, then by Follow-up Date (none last), then the latest contact first; ties by name and page, so a replay is identical."""
    open_rows = [r for r in rows if r["action"] in ORDER and not r["done"] and r["active"]]
    open_rows.sort(key=lambda r: (r["person"].strip().lower(), r["page_id"]))
    open_rows.sort(key=lambda r: r["last_contact"] or "", reverse=True)
    open_rows.sort(key=lambda r: (ORDER.index(r["action"]), _day(r["follow_up"]) or date.max))
    return open_rows


def read_rows(notion):
    """Every Needs Action or Waiting row, completely, or a fixed reason it cannot be trusted."""
    store.check_schema(notion)
    rows = store.rows_where(notion, {"or": [{"property": "Action Status", "select": {"equals": s}} for s in ORDER]})
    ids = [r["page_id"] for r in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise fail("SOURCE_DUPLICATE")
    return rows


# --- the region ---

def region(client, environ):
    """-> {"heading", "status": None, "table", "rows", "page_id"}: the exact region right after the pinned Hiring Pipeline table, or a fixed code."""
    anchor = R.clean_id(environ.get("HIRING_TABLE_BLOCK_ID"))
    if not anchor:
        raise fail("NOT_CONFIGURED")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", anchor.replace("-", "")):
        raise fail(f"ID_INVALID:HIRING_TABLE_BLOCK_ID:len{len(anchor.replace('-', ''))}")
    meta = _step("ANCHOR_GET", lambda: R._meta(client, anchor))
    if not isinstance(meta, dict) or meta.get("type") != "table" or not R.same_id(meta.get("id"), anchor):
        raise fail("ANCHOR_INVALID")
    ancestors = []
    page_id = _step("PARENT_WALK", lambda: R.page_of(client, meta, fail, ancestors))
    if "callout" in ancestors:
        raise fail("WRAPPED_IN_CALLOUT")
    parent = meta.get("parent") or {}
    siblings = _step("SIBLINGS", lambda: R.children(client, parent.get("block_id") or parent.get("page_id"), fail))
    at = [i for i, b in enumerate(siblings) if R.same_id(b.get("id"), anchor)]
    if len(at) != 1:
        raise fail("ANCHOR_AMBIGUOUS")
    heading, table, after = (siblings[at[0] + 1:at[0] + 4] + [None] * 3)[:3]
    if not heading or heading.get("type") != "heading_3" or not R.titled(R.plain(heading), TITLE):
        raise fail("HEADING_MISSING")
    if not table or table.get("type") != "table" or (after and after.get("type") == "table"):
        raise fail("SHAPE_INVALID")
    rows = _step("TABLE_ROWS", lambda: R.children(client, table["id"], fail))
    spec = table.get("table") or {}
    if spec.get("table_width") != 4 or spec.get("has_column_header") is not True or not rows or any(r.get("type") != "table_row" for r in rows):
        raise fail("TABLE_INVALID")
    if R.cells_of(rows[0]) != list(HEADERS):
        raise fail("HEADERS_CHANGED")
    return {"heading": heading, "status": None, "table": table, "rows": rows, "page_id": page_id}


def validate(client, environ):
    """-> (region, digest of the whole page outside the two owned blocks)."""
    found = region(client, environ)
    own = [found["heading"]["id"], found["table"]["id"]]
    digest = _step("DIGEST", lambda: R.page_digest(client, found["page_id"], own, fail, SKIP))
    if any(R.titled(text, TITLE) for text in digest["h3"]):          # the owned heading is outside the digest: any other heading with the title is a second region
        raise fail("HEADING_NOT_UNIQUE")
    try:
        router.check_write(MODULE, [TITLE])
    except router.RouterError:
        raise fail("NOT_OWNED") from None
    return found, digest


def _retitle(client, block_id, text):
    client.call("PATCH", f"/blocks/{quote(block_id, safe='')}", {"heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}})


def present(client, environ, build, heading, live):
    """Dry run: validate only. Live: rewrite the data rows and the heading, read back, prove the rest of the page unchanged. build(current data rows) -> the new rows,
    or build None keeps them."""
    found, before = validate(client, environ)
    rows = build(found["rows"][1:]) if build else None
    counts = {"rows_changed": 0, "rows_added": 0, "rows_removed": 0, "verified": False}
    if rows is not None:
        have = [R.live_view(r) for r in found["rows"][1:]]
        counts["would_change"] = sum(a != R.want_view(b) for a, b in zip(have, rows)) + abs(len(have) - len(rows))
    if not live:
        return counts
    own = [found["heading"]["id"], found["table"]["id"]]
    try:
        counts.update(R.write_floating(client, found, None, rows, fail))
        if R.plain(found["heading"]).strip() != heading:
            _retitle(client, found["heading"]["id"], heading)
    except Exception:
        try:                                                     # a half-written table must not look current: the heading says so, best effort
            _retitle(client, found["heading"]["id"], f"{TITLE} (DEGRADED)")
        except Exception:
            pass
        raise fail("WRITE_FAILED") from None
    again = region(client, environ)                                                  # authoritative read-back
    after = R.page_digest(client, again["page_id"], own, fail, SKIP)
    if not R.same_id(again["heading"]["id"], found["heading"]["id"]) or not R.same_id(again["table"]["id"], found["table"]["id"]) or R.plain(again["heading"]).strip() != heading:
        raise fail("HEADING_CHANGED")
    if before["nodes"] != after["nodes"] or before["layout"] != after["layout"]:
        print("recruiter-outreach: protected content changed", json.dumps(R.page_changed(before, after)))
        raise fail("PROTECTED_REGION_CHANGED")
    if rows is not None and [R.live_view(r) for r in again["rows"][1:]] != [R.want_view(r) for r in rows]:
        raise fail("READBACK_MISMATCH")
    counts["verified"] = True
    return counts


def run(limit, live, environ=os.environ, notion=None, client=None, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(CHICAGO)
    clock = now.strftime("%-I:%M %p") + " CT"
    write = bool(live) and (environ.get("RECRUITER_OUTREACH") or "").strip().lower() == "on"
    counts = {"rows": 0, "needs_action": 0, "waiting": 0, "live": write, "status": "ok"}
    reason = None
    if (environ.get("RECRUITERS_STAGE_OUTCOME") or "success").strip() != "success":         # the Recruiters stage failed this run: what the source holds is not this run's truth
        reason = "RECRUITERS_STAGE_FAILED"
    else:
        try:
            shown = ordered(read_rows(notion or source(environ)))
        except NotionError:
            reason = "SOURCE_UNREADABLE"
        else:
            if len(shown) > MAX_ROWS:
                reason = "SOURCE_TOO_MANY"
    target = client or writer(environ)
    if reason:
        counts["status"] = "degraded"
        counts.update(present(target, environ, None, f"{TITLE} (DEGRADED at {clock})", write))
        print("recruiter-outreach:", json.dumps(counts, sort_keys=True))
        raise fail(f"DEGRADED:{reason}")                            # the rows keep their last accepted content; the step is a warning
    today = now.date()
    counts.update(rows=len(shown), needs_action=sum(r["action"] == store.NEEDS_ACTION for r in shown), waiting=sum(r["action"] == store.WAITING for r in shown))
    def build(current):
        kept = kept_dates(current)
        firsts = [first_contact(r, kept) for r in shown]
        counts["first_contact_shown"] = sum(1 for f in firsts if f)
        return [cells(r, today, f) for r, f in zip(shown, firsts)]
    counts.update(present(target, environ, build, f"{TITLE} (Updated {clock})", write))
    return counts
