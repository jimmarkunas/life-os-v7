"""The read-only lanes of the Weekly LIFE OS Review and the rules that turn them into a Decision Queue.

A lane never raises: a failed read is a Degraded Lane with a fixed code. Jira, Calendar, Mail and Notion read the V7
cards on the Daily Report, Automation reads GitHub runs, Drive uses the `drive` reader. Counts and codes only.

A card is found by the name its heading, or the first line of its callout, begins with, ignoring a leading emoji: the
Recruiter Outreach and Hiring Pipeline cards lost their emoji when they became live views."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote

from lifeos.dcc import card as dcc_card
from lifeos.platform import ct_stamp, router
from lifeos.platform.notion_client import NotionError
from lifeos.platform.notion_text import block_text

JIRA_FRESH, CARD_FRESH = timedelta(minutes=30), timedelta(hours=3)  # max card age
HEADING_TIME = re.compile(r"\((STALE · last accepted )?(\d{1,2})/(\d{1,2}) · (\d{1,2}):(\d{2}) ([AP]M) CT\)")
RANGE = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M)\s*[–-]\s*(\d{1,2}):(\d{2}) ([AP]M)")
NEED_ACTION = re.compile(r"(\d+) need action")
LEADING_SYMBOLS = re.compile(r"^[^\w]+")
# The Daily Report cards the Command Center reads (dcc.card.AREAS): "N of M source cards current" is counted from
# their headings now that the Command Center no longer writes that line.
SOURCE_AREAS = (
    ("Calendar", router.CALENDAR_REGION),
    ("Jira", router.JIRA_REGION),
    ("Bills", router.BILLS_REGION),
    ("Mail Alerts", router.MAIL_ALERTS_REGION),
    ("Amazon", router.AMAZON_REGION),
)
GROUPS = (
    "Carry Forward",
    "Reprioritize",
    "Close / Drop",
    "Schedule",
    "Create Work",
    "Waiting On",
    "System Maintenance",
)


@dataclass
class Lane:
    name: str
    health: str = "Healthy"
    reason: str = ""
    at: str = ""
    lines: list = field(default_factory=list)
    items: list = field(default_factory=list)  # [(group, text)]


def _down(lane, reason):
    if lane.health == "Healthy":
        lane.health, lane.reason = "Degraded", reason
    return lane


def kids(client, block_id):
    out, cursor = [], None
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100" + (
            f"&start_cursor={quote(cursor, safe='')}" if cursor else ""
        )
        page = client.call("GET", path)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise NotionError("WEEKLY_REVIEW_READ")
        out.extend(page["results"])
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise NotionError("WEEKLY_REVIEW_READ")


CONTAINERS = ("callout", "column_list", "column", "toggle", "table")


def _tree(client, block_id, depth=0, limit=5):
    out = []
    for block in kids(client, block_id):
        sub = (
            _tree(client, block["id"], depth + 1, limit)
            if block.get("has_children") and depth < limit and block.get("type") in CONTAINERS
            else []
        )
        out.append((block, sub))
    return out


def _flat(nodes):
    for block, sub in nodes:
        yield block
        yield from _flat(sub)


def _bare(text):
    return LEADING_SYMBOLS.sub("", (text or "").strip())


def _named(text, name):
    return _bare(text).startswith(_bare(name))


def _regions_in(nodes, names, found):
    for i, (block, sub) in enumerate(nodes):
        kind = block.get("type", "")
        if kind == "callout":
            head = next((block_text(b).strip() for b, _ in sub if b.get("type", "").startswith("heading")), "")
            own = block_text(block).strip().split("\n")[0]
            for name in names:
                if name in found:
                    continue
                if head and _named(head, name):
                    found[name] = (head, list(_flat(sub)))
                elif not head and own and _named(own, name):
                    found[name] = (own, [block, *_flat(sub)])  # the callout's own text carries the card's lines
        elif kind.startswith("heading"):
            text = block_text(block).strip()
            for name in names:
                if _named(text, name) and name not in found:
                    end = next(
                        (j for j in range(i + 1, len(nodes)) if nodes[j][0].get("type", "").startswith("heading")),
                        len(nodes),
                    )
                    found[name] = (text, list(_flat(nodes[i + 1 : end])))
        _regions_in(sub, names, found)


def read_regions(client, page_id):
    """Returns {region name: (heading, [blocks under it])} for the Daily Report cards the lanes read."""
    names = (
        router.JIRA_REGION,
        router.CALENDAR_REGION,
        router.BILLS_REGION,
        router.AMAZON_REGION,
        router.MAIL_ALERTS_REGION,
        router.RECRUITER_OUTREACH_REGION,
        router.HIRING_REGION,
    )
    found = {}
    _regions_in(_tree(client, page_id), names, found)
    return found


def heading_time(head, now):
    """Returns (datetime or None, stale?) from "Calendar (11/24 · 1:01 PM CT)"."""
    m = HEADING_TIME.search(head or "")
    if not m:
        return None, False
    month, day, hour, minute = int(m[2]), int(m[3]), int(m[4]) % 12, int(m[5])
    hour += 12 if m[6] == "PM" else 0
    try:
        when = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None, bool(m[1])
    if when > now + timedelta(days=1):
        when = when.replace(year=when.year - 1)
    return when, bool(m[1])


def _card(lane, regions, name, fresh, now):
    """Set the lane's time and health from a card heading; returns the card's blocks or None."""
    got = regions.get(name)
    if not got:
        _down(lane, "CARD_MISSING")
        return None
    when, stale = heading_time(got[0], now)
    if when is None:
        _down(lane, "CARD_TIME_UNREADABLE")
    elif stale:
        _down(lane, "CARD_STALE")
    elif now - when > fresh:
        _down(lane, "CARD_OLDER_THAN_" + ("30_MINUTES" if fresh == JIRA_FRESH else "3_HOURS"))
    if when is not None:
        lane.at = ct_stamp.when(when)
    return got[1]


def _texts(blocks):
    return [t for t in (block_text(b).strip() for b in blocks or []) if t]


def jira(regions, now):
    lane = Lane("Jira")
    lines = _card(lane, regions, router.JIRA_REGION, JIRA_FRESH, now)
    summary = next((t for t in _texts(lines) if "Overdue" in t and "Today" in t and "This Week" in t), "")
    if summary:
        lane.lines.append(summary)
        counts = {k: int(v) for v, k in re.findall(r"(\d+) (Today|This Week|Done)", summary)}
        late = dcc_card._overdue_blocked(summary)
        if late:
            lane.items.append(
                (
                    "Reprioritize",
                    f"{late} Jira items are Overdue or Blocked: decide the disposition of each in Jira.",
                )
            )
        if counts.get("Today", 0) > 3:
            lane.items.append(
                (
                    "Reprioritize",
                    f"{counts['Today']} items are on Today against a capacity of 3: move the extra ones deliberately.",
                )
            )
    elif lines is not None:
        _down(lane, "CARD_SUMMARY_MISSING")
    return lane


def _minutes(hour, minute, ampm):
    return (int(hour) % 12 + (12 if ampm == "PM" else 0)) * 60 + int(minute)


def calendar(regions, now):
    lane = Lane("Calendar")
    lines = _card(lane, regions, router.CALENDAR_REGION, CARD_FRESH, now)
    days, spans = [[]], 0
    for block in lines or []:
        if block.get("type", "").startswith("heading"):
            days.append([])  # overlaps only count within a day
            continue
        for m in RANGE.findall(block_text(block))[:1]:
            days[-1].append((_minutes(*m[:3]), _minutes(*m[3:])))
            spans += 1
    lane.lines.append(f"{spans} timed commitments on the Calendar card.")
    overlaps = sum(1 for day in days for i, a in enumerate(day) for b in day[i + 1 :] if a[0] < b[1] and b[0] < a[1])
    if overlaps:
        lane.items.append(
            (
                "Schedule",
                f"{overlaps} overlapping calendar commitments within a day on the card: decide whether each overlap is intended.",
            )
        )
    return lane


def mail(regions, now):
    lane = Lane("Gmail and Outlook")
    rows = 0
    for name in (router.MAIL_ALERTS_REGION, router.RECRUITER_OUTREACH_REGION, router.HIRING_REGION):
        got = regions.get(name)
        if not got:
            _down(lane, "CARD_MISSING")
            continue
        when, stale = heading_time(got[0], now)
        if stale or (when is not None and now - when > CARD_FRESH):
            _down(lane, "CARD_STALE")
        if name == router.RECRUITER_OUTREACH_REGION:
            counted = next((int(m[1]) for t in _texts(got[1]) if (m := NEED_ACTION.search(t))), None)
            rows = (
                counted if counted is not None else max(0, sum(1 for b in got[1] if b.get("type") == "table_row") - 1)
            )
    lane.lines.append(
        "Recruiter, Mail Alerts and Hiring Pipeline cards read; open recruiter rows are listed on the Daily Report."
    )
    if rows:
        lane.items.append(
            (
                "Waiting On",
                f"{rows} Recruiter Outreach rows are open on the Daily Report: answer, pass or mark each one.",
            )
        )
    return lane


def notion_hygiene(regions, hub_readable, report_edited, now):
    """Counts the Daily Report source cards whose heading time is current; none found means the report is unreadable."""
    lane = Lane("Notion")
    seen = [(label, regions.get(region)) for label, region in SOURCE_AREAS if regions.get(region)]
    if not seen:
        _down(lane, "SOURCE_CARD_LINE_MISSING")
    else:
        current = 0
        for label, got in seen:
            when, stale = heading_time(got[0], now)
            if when is not None and not stale and now - when <= timedelta(minutes=dcc_card.fresh_minutes(label)):
                current += 1
        total = len(SOURCE_AREAS)
        lane.lines.append(f"{current} of {total} Daily Report source cards current.")
        if current != total:
            _down(lane, "SOURCE_CARDS_NOT_CURRENT")
            lane.items.append(
                (
                    "System Maintenance",
                    f"Only {current} of {total} Daily Report source cards were current: find the card that stopped updating.",
                )
            )
    if not hub_readable:
        _down(lane, "WEEKLY_HUB_UNREADABLE")
    if report_edited is None or now - report_edited > CARD_FRESH:
        _down(lane, "DAILY_REPORT_NOT_RECENT")
    return lane


def automation(runs, now):
    """`runs`: {workflow file: [(conclusion, finished)]}, newest first; None if unreadable."""
    lane = Lane("Automation")
    if runs is None:
        return _down(lane, "GITHUB_UNREADABLE")
    failed = 0
    for name, rows in sorted(runs.items()):
        bad = sum(1 for c, _ in rows if c not in ("success", "skipped", "cancelled", None))
        failed += bad
        if not rows:
            _down(lane, "NO_RUNS_" + name.split(".")[0].upper())
            continue
        lane.lines.append(f"{name}: {len(rows)} runs, {bad} failed, latest {rows[0][0] or 'running'}")
        if rows[0][0] not in ("success", "skipped", "cancelled", None):
            _down(lane, "LATEST_RUN_FAILED_" + name.split(".")[0].upper())
    if failed:
        lane.items.append(
            (
                "System Maintenance",
                f"{failed} workflow runs failed in the review week: confirm each has a cause and a fix.",
            )
        )
    return lane


def drive(read):
    """`read` returns the Drive counts or raises OSError, ValueError or TimeoutError with a fixed code."""
    lane = Lane("Drive")
    try:
        got = read()
        lane.lines.append(
            f"{got['new']} new and {got.get('changed', 0)} changed files in the review week; {got.get('unfiled', got['missing_metadata'])} new files sit unfiled at the top of My Drive; {got.get('duplicates', 0)} duplicate copies."
        )
        if got["missing_metadata"]:
            lane.items.append(
                (
                    "System Maintenance",
                    f"{got['missing_metadata']} new Drive files are unfiled: decide where they belong.",
                )
            )
        if got.get("duplicates"):
            lane.items.append(
                ("Close / Drop", f"{got['duplicates']} Drive files are duplicate copies: decide which to keep.")
            )
        if got.get("complete") is False:
            _down(lane, "DRIVE_LIST_INCOMPLETE")
    except (OSError, ValueError, TimeoutError, KeyError, TypeError) as error:
        _down(lane, str(error) if str(error).isupper() else "DRIVE_READER_UNAVAILABLE")
    return lane


def decisions(lanes):
    items = [item for lane in lanes for item in lane.items]
    return sorted(items, key=lambda item: GROUPS.index(item[0]))


def when_text(now):
    return ct_stamp.when(now)
