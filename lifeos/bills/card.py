"""Render the saved Bills snapshot into V7's one "Bills: This Week" callout on the Daily Report (D116).

Bill Tracker -> bills-paid -> bills-snapshot -> bills-card. This stage only reads the accepted private snapshot and writes the one callout it owns;
it never reads or writes the Bill Tracker, and it never advances a Due Date (the Paid processor owns that). The supporting window is calculated at
render time from the America/Chicago date, [today, today + 7] (the same boundary as the Bills view's native 'one week from now' filter); no calendar date is stored anywhere. Counts-only logs, fixed error codes."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, db, report_region, router
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import snapshot as bills_snapshot, state

TZ = ZoneInfo(bills_snapshot.LOCAL_TZ)
STALE_HOURS = 3
WINDOW_DAYS = 8                                  # [today, today + 7]: today through one week from now, 8 calendar dates inclusive
CARD_TITLE = router.BILLS_REGION
MODULE = router.OWNERS[CARD_TITLE]
# D117: the card owns only the TEXT in the callout (the summary line). The linked Bills table inside it is Jim's interactive view (it is where he ticks
# Paid), so it is never removed or rewritten: report_region.replace_text replaces only the plain text kinds; every other block stays exactly where it is.


class CardError(NotionError):
    """Fixed codes only; never expose bill names, amounts or IDs."""


def _fail(code):
    return CardError("BILLS_" + code)


def _now(now):
    now = now or datetime.now(TZ)
    return now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)


def _instant(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _valid(saved):
    """A snapshot is usable only when it is whole. An empty or malformed one is never shown as 'no bills'."""
    if not isinstance(saved, dict) or saved.get("schema") != bills_snapshot.SCHEMA_V or not isinstance(saved.get("rows"), list) or not saved["rows"]:
        raise CardError("BILLS_CARD_SNAPSHOT_INVALID")
    for row in saved["rows"]:
        if (not isinstance(row, dict) or not isinstance(row.get("Name"), str) or not row["Name"].strip()
                or not isinstance(row.get("Paid"), bool) or any(field not in row for field in bills_snapshot.CRITICAL)):
            raise CardError("BILLS_CARD_SNAPSHOT_INVALID")
    with fixed((KeyError, TypeError, ValueError), CardError, "BILLS_CARD_SNAPSHOT_INVALID"):
        _instant(saved["taken_at"])
    return saved


def _amount(row):
    """The known amount of a bill, or None. Missing is never zero."""
    for field in ("Costs per Cycle", "Last Observed Amount"):
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _money(value):
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _window(rows, today):
    """Active unpaid recurring bills whose Next Due falls in [today, today + 7], from the state classifier's buckets (its due_today and due_next_7_days)."""
    buckets = state.classify(rows, today)
    last = today + timedelta(days=WINDOW_DAYS - 1)                  # today + 7
    due = [row for row in buckets["due_today"] + buckets["due_next_7_days"] if (day := state._day(row.get("Next Due"))) is not None and day <= last]
    overdue = sorted(buckets["stale_due"], key=lambda row: (state._day(row["Due Date"]), row["Name"]))
    late = {row["page_id"] for row in overdue if "page_id" in row}
    due = sorted((row for row in due if row.get("page_id") not in late), key=lambda row: (state._day(row["Next Due"]), row["Name"]))   # an overdue bill is listed once, as overdue
    return overdue, due


def _status(saved, stale):
    taken = _instant(saved["taken_at"])
    return f"STALE · last accepted {ct_stamp.when(taken)}" if stale else ct_stamp.when(taken)


def heading_text(saved, stale):
    """LIFE-609: the update time shares the heading, like Calendar: "Bills: This Week (11/13 · 2:30 PM CT)"."""
    return f"{CARD_TITLE} ({_status(saved, stale)})"


def _summary(saved, stale, overdue, due):
    known = [amount for amount in (_amount(row) for row in due) if amount is not None]
    total = len(overdue) + len(due)                                       # the Daily Command Center's "Bills due" tile reads this: due this week, plus unpaid and already past due
    parts = [f"{total} bill{'s' if total != 1 else ''} due", f"{len(due)} due through one week from now"]
    if due:
        parts.append(f"{_money(sum(known))} known")
        if len(known) < len(due):
            parts.append(f"{len(due) - len(known)} amount missing")
    if overdue:
        parts.append(f"{len(overdue)} overdue")
    return " · ".join(parts)


def _text(content, url=None):
    parts = rich_text(content)
    if url and parts:
        parts[0]["text"]["link"] = {"url": url}
    return parts


def _paragraph(content, url_label=None, url=None):
    parts = _text(content)
    if url:
        parts += _text(" · ") + _text(url_label, url)
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": parts}}


def render(saved, stale=False, now=None, tracker_url=None):
    """-> (blocks, counts). Pure: the window comes from `now` in America/Chicago, the rows from the saved snapshot."""
    now = _now(now)
    saved = _valid(saved)
    overdue, due = _window(saved["rows"], now.date())
    blocks = [_paragraph(_summary(saved, stale, overdue, due), "Bill Tracker", tracker_url)]
    counts = {"rows": len(saved["rows"]), "active": sum(row.get("Status") == "Active" for row in saved["rows"]), "overdue": len(overdue),
              "due_window": len(due), "amount_missing": sum(_amount(row) is None for row in overdue + due),
              "status": "stale" if stale else "fresh", "blocks_written": 0}
    return blocks, counts


def _tracker_link(existing):
    """Keep the Bill Tracker link the callout already carries (never invented here)."""
    for block in existing[1:]:
        for part in (block.get(block.get("type")) or {}).get("rich_text") or []:
            url = ((part.get("text") or {}).get("link") or {}).get("url") or part.get("href")
            if isinstance(part, dict) and (part.get("plain_text") or "").strip() == "Bill Tracker" and isinstance(url, str) and url.startswith("https://"):
                return url
    return None


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    block_id = (environ.get("BILLS_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"rows": 0, "active": 0, "overdue": 0, "due_window": 0, "amount_missing": 0, "blocks_written": 0, "status": "not_configured"}
    with fixed(Exception, CardError, "BILLS_CARD_STORE_FAILED", keep=NotionError):
        with (connect or db.connect)() as connection:
            saved = bills_snapshot.load(connection)
    if saved is None:
        raise CardError("BILLS_CARD_SNAPSHOT_MISSING")
    now = _now(now)
    _valid(saved)
    stale = now - _instant(saved["taken_at"]) > timedelta(hours=STALE_HOURS)
    protected_ids = {router.JIRA_REGION: (environ.get("JIRA_CARD_BLOCK_ID") or "").strip(), router.CALENDAR_REGION: (environ.get("CALENDAR_CARD_BLOCK_ID") or "").strip()}
    client = client or _client(environ)

    def write(blocks, live):
        return report_region.replace_text(client, block_id, CARD_TITLE, MODULE, blocks, protected_ids, _fail, "bills", live, heading=heading_text(saved, stale))

    existing, _ = write([], False)                              # the dry run checks the callout is ours and returns what it holds, so the Bill Tracker link can be kept
    blocks, counts = render(saved, stale, now, _tracker_link(existing))
    if not live:
        return counts
    _, removed = write(blocks, True)
    counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
