"""Render the saved Bills snapshot into V7's one "Bills: This Week" callout on the Daily Report.

Reads only the accepted private snapshot and writes only the callout it owns; never touches the Bill Tracker or a
Due Date. The window is [today, today + 7] in America/Chicago."""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, db, report_region, router
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError, rich_text
from . import snapshot as bills_snapshot, state

TZ = ZoneInfo(bills_snapshot.LOCAL_TZ)
STALE_HOURS = 3
WINDOW_DAYS = 8
CARD_TITLE = router.BILLS_REGION
MODULE = router.OWNERS[CARD_TITLE]
# The card owns only the TEXT in the callout. The linked Bills table inside it is where bills are ticked Paid, so it
# is never rewritten.


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
    if (
        not isinstance(saved, dict)
        or saved.get("schema") != bills_snapshot.SCHEMA_V
        or not isinstance(saved.get("rows"), list)
        or not saved["rows"]
    ):
        raise CardError("BILLS_CARD_SNAPSHOT_INVALID")
    for row in saved["rows"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("Name"), str)
            or not row["Name"].strip()
            or not isinstance(row.get("Paid"), bool)
            or any(field not in row for field in bills_snapshot.CRITICAL)
        ):
            raise CardError("BILLS_CARD_SNAPSHOT_INVALID")
    with fixed((KeyError, TypeError, ValueError), CardError, "BILLS_CARD_SNAPSHOT_INVALID"):
        _instant(saved["taken_at"])
    return saved


def _amount(row):
    for field in ("Costs per Cycle", "Last Observed Amount"):
        value = row.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _money(value):
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _window(rows, today):
    buckets = state.classify(rows, today)
    last = today + timedelta(days=WINDOW_DAYS - 1)
    due = [
        row
        for row in buckets["due_today"] + buckets["due_next_7_days"]
        if (day := state._day(row.get("Next Due"))) is not None and day <= last
    ]
    overdue = sorted(buckets["stale_due"], key=lambda row: (state._day(row["Due Date"]), row["Name"]))
    late = {row["page_id"] for row in overdue if "page_id" in row}
    due = sorted(
        (row for row in due if row.get("page_id") not in late),
        key=lambda row: (state._day(row["Next Due"]), row["Name"]),
    )
    return overdue, due


def _status(saved, stale):
    taken = _instant(saved["taken_at"])
    return f"STALE · last accepted {ct_stamp.when(taken)}" if stale else ct_stamp.when(taken)


def heading_text(saved, stale):
    return f"{CARD_TITLE} ({_status(saved, stale)})"


def _summary(saved, stale, overdue, due):
    known = [amount for amount in (_amount(row) for row in due) if amount is not None]
    total = len(overdue) + len(due)  # the Daily Command Center's "Bills due" tile reads this count
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
    now = _now(now)
    saved = _valid(saved)
    overdue, due = _window(saved["rows"], now.date())
    blocks = [_paragraph(_summary(saved, stale, overdue, due), "Bill Tracker", tracker_url)]
    counts = {
        "rows": len(saved["rows"]),
        "active": sum(row.get("Status") == "Active" for row in saved["rows"]),
        "overdue": len(overdue),
        "due_window": len(due),
        "amount_missing": sum(_amount(row) is None for row in overdue + due),
        "status": "stale" if stale else "fresh",
        "blocks_written": 0,
    }
    return blocks, counts


def _tracker_link(existing):
    for block in existing[1:]:
        for part in (block.get(block.get("type")) or {}).get("rich_text") or []:
            url = ((part.get("text") or {}).get("link") or {}).get("url") or part.get("href")
            if (
                isinstance(part, dict)
                and (part.get("plain_text") or "").strip() == "Bill Tracker"
                and isinstance(url, str)
                and url.startswith("https://")
            ):
                return url
    return None


def _client(environ):
    return Client(
        {
            "NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused",
        }
    )


def run(limit, live, environ=os.environ, client=None, now=None, connect=None):
    block_id = (environ.get("BILLS_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {
            "rows": 0,
            "active": 0,
            "overdue": 0,
            "due_window": 0,
            "amount_missing": 0,
            "blocks_written": 0,
            "status": "not_configured",
        }
    with fixed(Exception, CardError, "BILLS_CARD_STORE_FAILED", keep=NotionError):
        with (connect or db.connect)() as connection:
            saved = bills_snapshot.load(connection)
    if saved is None:
        raise CardError("BILLS_CARD_SNAPSHOT_MISSING")
    now = _now(now)
    _valid(saved)
    stale = now - _instant(saved["taken_at"]) > timedelta(hours=STALE_HOURS)
    protected_ids = {
        router.JIRA_REGION: (environ.get("JIRA_CARD_BLOCK_ID") or "").strip(),
        router.CALENDAR_REGION: (environ.get("CALENDAR_CARD_BLOCK_ID") or "").strip(),
    }
    client = client or _client(environ)

    def write(blocks, live):
        return report_region.replace_text(
            client,
            block_id,
            CARD_TITLE,
            MODULE,
            blocks,
            protected_ids,
            _fail,
            "bills",
            live,
            heading=heading_text(saved, stale),
        )

    existing, _ = write([], False)
    blocks, counts = render(saved, stale, now, _tracker_link(existing))
    if not live:
        return counts
    _, removed = write(blocks, True)
    counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
