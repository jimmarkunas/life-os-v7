"""LIFE-945: the six read-only lanes of the Weekly LIFE OS Review, and the fixed rules that turn them into a Decision Queue (no model call, no spend).

Each lane returns a Lane: `health` Healthy or Degraded, the exact `reason` when Degraded, the CT time its source was last written, a few fact lines and its Decision Queue items. A lane never raises: a failed read is a
Degraded lane with a fixed code. Jira, Calendar, Mail and Notion read the V7 cards on the Daily Report (what the hourly stages already wrote, with the existing Notion token); Automation reads the workflow runs
through the GitHub API; Drive reads through the governed Oracle reader passed in as `drive`. Counts and fixed codes only."""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote

from lifeos.platform import ct_stamp, router
from lifeos.platform.notion_client import NotionError
from lifeos.platform.notion_text import block_text

JIRA_FRESH, CARD_FRESH = timedelta(minutes=30), timedelta(hours=3)      # the page's own gates: Jira at most 30 minutes old; the other cards use their own 3-hour STALE rule
HEADING_TIME = re.compile(r"\((STALE · last accepted )?(\d{1,2})/(\d{1,2}) · (\d{1,2}):(\d{2}) ([AP]M) CT\)")
RANGE = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M)\s*[–-]\s*(\d{1,2}):(\d{2}) ([AP]M)")
SOURCE_CARDS = re.compile(r"(\d+) of (\d+) source cards current")
GROUPS = ("Carry Forward", "Reprioritize", "Close / Drop", "Schedule", "Create Work", "Waiting On", "System Maintenance")


@dataclass
class Lane:
    name: str
    health: str = "Healthy"
    reason: str = ""
    at: str = ""
    lines: list = field(default_factory=list)
    items: list = field(default_factory=list)          # [(group, text)]


def _down(lane, reason):
    if lane.health == "Healthy":                                       # the first reason is the one reported
        lane.health, lane.reason = "Degraded", reason
    return lane


def kids(client, block_id):
    """Every child of a block, all pages; a loop or an unreadable page is a NotionError (the lane turns it into a fixed code)."""
    out, cursor = [], None
    for _ in range(50):
        path = f"/blocks/{quote(block_id, safe='')}/children?page_size=100" + (f"&start_cursor={quote(cursor, safe='')}" if cursor else "")
        page = client.call("GET", path)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise NotionError("WEEKLY_REVIEW_READ")
        out.extend(page["results"])
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise NotionError("WEEKLY_REVIEW_READ")


CONTAINERS = ("callout", "column_list", "column", "toggle", "table")


def _tree(client, block_id, depth=0):
    """[(block, [children...])] under a block, each block read once."""
    out = []
    for block in kids(client, block_id):
        sub = _tree(client, block["id"], depth + 1) if block.get("has_children") and depth < 5 and block.get("type") in CONTAINERS else []
        out.append((block, sub))
    return out


def _flat(nodes):
    for block, sub in nodes:
        yield block
        yield from _flat(sub)


def _regions_in(nodes, names, found):
    for i, (block, sub) in enumerate(nodes):
        kind = block.get("type", "")
        if kind == "callout":
            head = next((block_text(b).strip() for b, _ in sub if b.get("type", "").startswith("heading")), "")
            for name in names:
                if head.startswith(name) and name not in found:
                    found[name] = (head, list(_flat(sub)))
        elif kind.startswith("heading"):
            text = block_text(block).strip()
            for name in names:
                if text.startswith(name) and name not in found:                    # a floating region: its heading and the siblings up to the next heading
                    end = next((j for j in range(i + 1, len(nodes)) if nodes[j][0].get("type", "").startswith("heading")), len(nodes))
                    found[name] = (text, list(_flat(nodes[i + 1:end])))
        _regions_in(sub, names, found)


def read_regions(client, page_id):
    """-> ({region name: (heading text, [every block under it])}, [the first block texts of the page, for the source-card line]) for the V7 cards on the Daily Report page."""
    names = (router.JIRA_REGION, router.CALENDAR_REGION, router.MAIL_ALERTS_REGION, router.RECRUITER_OUTREACH_REGION, router.HIRING_REGION)
    nodes = _tree(client, page_id)
    found = {}
    _regions_in(nodes, names, found)
    header = [t for t in (block_text(b).strip() for b in _flat(nodes[:6])) if t][:120]
    return found, header


def heading_time(head, now):
    """-> (datetime or None, stale?) from a card heading like "Calendar (11/24 · 1:01 PM CT)" or "(STALE · last accepted ...)"."""
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
    """Fill the lane's time and health from one card heading; -> the card's text lines, or None when the card is missing."""
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
        counts = {k: int(v) for v, k in re.findall(r"(\d+) (Overdue/Blocked|Today|This Week|Done)", summary)}
        if counts.get("Overdue/Blocked"):
            lane.items.append(("Reprioritize", f"{counts['Overdue/Blocked']} Jira items are Overdue or Blocked: decide the disposition of each in Jira."))
        if counts.get("Today", 0) > 3:
            lane.items.append(("Reprioritize", f"{counts['Today']} items are on Today against a capacity of 3: move the extra ones deliberately."))
    elif lines is not None:
        _down(lane, "CARD_SUMMARY_MISSING")
    return lane


def _minutes(hour, minute, ampm):
    return (int(hour) % 12 + (12 if ampm == "PM" else 0)) * 60 + int(minute)


def calendar(regions, now):
    lane = Lane("Calendar")
    lines = _card(lane, regions, router.CALENDAR_REGION, CARD_FRESH, now)
    spans = [(_minutes(*m[:3]), _minutes(*m[3:]), t) for t in _texts(lines) for m in RANGE.findall(t)[:1]]
    lane.lines.append(f"{len(spans)} timed commitments on the Calendar card.")
    overlaps = sum(1 for i, a in enumerate(spans) for b in spans[i + 1:] if a[0] < b[1] and b[0] < a[1])
    if overlaps:
        lane.items.append(("Schedule", f"{overlaps} overlapping calendar commitments on the card: decide whether each overlap is intended."))
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
            rows = max(0, sum(1 for b in got[1] if b.get("type") == "table_row") - 1)
    lane.lines.append("Recruiter, Mail Alerts and Hiring Pipeline cards read; open recruiter rows are listed on the Daily Report.")
    if rows:
        lane.items.append(("Waiting On", "Recruiter Outreach rows are open on the Daily Report: answer, pass or mark each one."))
    return lane


def notion_hygiene(header, hub_readable, report_edited, now):
    lane = Lane("Notion")
    text = " ".join(header[:120])
    m = SOURCE_CARDS.search(text)
    if not m:
        _down(lane, "SOURCE_CARD_LINE_MISSING")
    else:
        lane.lines.append(f"{m[1]} of {m[2]} Daily Report source cards current.")
        if m[1] != m[2]:
            _down(lane, "SOURCE_CARDS_NOT_CURRENT")
            lane.items.append(("System Maintenance", f"Only {m[1]} of {m[2]} Daily Report source cards were current: find the card that stopped updating."))
    if not hub_readable:
        _down(lane, "WEEKLY_HUB_UNREADABLE")
    if report_edited is None or now - report_edited > CARD_FRESH:
        _down(lane, "DAILY_REPORT_NOT_RECENT")
    return lane


def automation(runs, now):
    """`runs` is {workflow file: [(conclusion, finished datetime)]} newest first, over the review week, or None when GitHub could not be read."""
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
        if rows[0][0] not in ("success", "skipped", None):
            _down(lane, "LATEST_RUN_FAILED_" + name.split(".")[0].upper())
    if failed:
        lane.items.append(("System Maintenance", f"{failed} workflow runs failed in the review week: confirm each has a cause and a fix."))
    return lane


def drive(read):
    """`read` is the governed Oracle reader: a callable -> {"new": n, "missing_metadata": n} or raising OSError, ValueError or TimeoutError with a fixed code."""
    lane = Lane("Drive")
    try:
        got = read()
        lane.lines.append(f"{got['new']} new or changed files; {got['missing_metadata']} without metadata.")
        if got["missing_metadata"]:
            lane.items.append(("System Maintenance", f"{got['missing_metadata']} Drive files lack metadata: decide who classifies them."))
    except (OSError, ValueError, TimeoutError, KeyError, TypeError) as error:
        _down(lane, str(error) if str(error).isupper() else "DRIVE_READER_UNAVAILABLE")
    return lane


def decisions(lanes):
    """The Decision Queue: every lane's items, in the page's fixed group order. -> [(group, text)]"""
    items = [item for lane in lanes for item in lane.items]
    return sorted(items, key=lambda item: GROUPS.index(item[0]))


def when_text(now):
    return ct_stamp.when(now)
