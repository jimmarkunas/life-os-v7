"""LIFE-945: V7 writes the Weekly LIFE OS Review each Friday 10 PM CT, Saturday to Friday (Jim, Nov 24 2026); the retired ChatGPT generator no longer does (LIFE-933).

On the first run at or after Friday 10:00 PM CT it makes one row in the Weekly Reviews database for that Friday (Week Ending), Status Generated, Current, and updates the one-line Dashboard pointer. One row per Week Ending, always: a rerun finds it and
does nothing, except that a row still Degraded inside 48 hours of its first write is rebuilt from fresh reads. The six lanes (weekly_lanes.py) are read-only; the only writes are the row, its page body and the pointer line. The week ending Fri Nov 20 2026 is
a one-time backfill, never Current once a newer week exists. Off unless the `WEEKLY_REVIEW` setting is on; a dry run (no --live) reads and reports and writes nothing. Reviewed and Applied stay Jim's. Counts and fixed codes only."""
import os
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.dcc import weekly_archive, weekly_lanes as wl
from lifeos.platform import branches, oracle_drive
from lifeos.platform.notion_client import Client, NotionError, rich_text

TZ = ZoneInfo("America/Chicago")
FRIDAY, DUE_HOUR = 4, 22                                        # Friday 10:00 PM CT
REFRESH = timedelta(hours=48)                                   # a Degraded row may be rebuilt, and a missed week caught up, this long after Friday 10 PM
BACKFILL = (date(2026, 11, 20),)                                # Jim, Nov 24 2026 ("945 backfill yes"): the week ending Fri Nov 20, made once on the first production run
WORKFLOWS = ("hourly.yml", "domains.yml")
POINTER_KINDS = ("paragraph", "callout")


class ReviewError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return ReviewError("WEEKLY_REVIEW_" + code)


def label(day):
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def short(day):
    return f"{day.strftime('%a %b')} {day.day}"


def due_moment(friday):
    return datetime(friday.year, friday.month, friday.day, DUE_HOUR, tzinfo=TZ)


def latest_friday(now):
    """The most recent Friday whose 10:00 PM CT has passed."""
    back = (now.weekday() - FRIDAY) % 7
    friday = now.date() - timedelta(days=back)
    return friday - timedelta(days=7) if now < due_moment(friday) else friday


def wanted(now, have):
    """-> [(Friday, "natural" or "backfill")] this run should make: the latest Friday if missing and inside the 48-hour catch-up, plus any missing backfill week."""
    out = []
    friday = latest_friday(now)
    if friday not in have and now - due_moment(friday) <= REFRESH:
        out.append((friday, "natural"))
    out.extend((day, "backfill") for day in BACKFILL if day not in have and day not in [d for d, _ in out])
    return out


def _on(raw):
    return (raw or "").strip().lower() in ("on", "true", "1", "yes")


def _date_prop(day):
    return {"date": {"start": day.isoformat()}}


def _choice(schema, name, value):
    kind = (schema.get(name) or {}).get("type", "select")
    if kind not in ("select", "status"):
        raise _fail("SCHEMA")
    return {kind: {"name": value}}


def properties(schema, friday, health, open_count, now, current):
    return {"Review": {"title": rich_text("Week Ending " + label(friday))}, "Week Ending": _date_prop(friday), "Week Start": _date_prop(friday - timedelta(days=6)),
            "Generated At": {"date": {"start": now.replace(microsecond=0).isoformat()}}, "Status": _choice(schema, "Status", "Generated"),
            "Source Health": _choice(schema, "Source Health", health), "Open Decisions": {"number": open_count}, "Current": {"checkbox": bool(current)}}


def _t(content, bold=False):
    node = {"type": "text", "text": {"content": content[:1900]}}
    if bold:
        node["annotations"] = {"bold": True}
    return node


def _block(kind, content, **extra):
    body = {"rich_text": [_t(content)], **extra}
    return {"object": "block", "type": kind, kind: body}


def body(friday, lanes, queue, now, kind):
    """The page body: the eight required sections in the page's order."""
    start, end = friday - timedelta(days=6), friday
    plan = (friday + timedelta(days=1), friday + timedelta(days=7))
    by = {lane.name: lane for lane in lanes}
    degraded = [lane for lane in lanes if lane.health != "Healthy"]
    blocks = [_block("callout", f"One weekly cross-system review of execution, commitments, inbound, files and LIFE OS health. Review window {short(start)} to {short(end)}; planning window {short(plan[0])} to {short(plan[1])}. "
                     f"Generated {wl.when_text(now)} by V7 from read-only sources; a synthesis and decision surface, not a task manager.", icon={"type": "emoji", "emoji": "🔄"}, color="blue_background")]
    if kind == "backfill":
        blocks.append(_block("callout", f"Backfill: this week was made on {short(now.date())} from the sources as they are now, not as they were on {short(end)}. The numbers describe today.", icon={"type": "emoji", "emoji": "🕘"}, color="gray_background"))
    blocks.append(_block("heading_2", "Executive Summary"))
    blocks.append(_block("bulleted_list_item", ("All six source lanes read cleanly." if not degraded else f"{len(degraded)} of 6 source lanes are Degraded: " + ", ".join(f"{lane.name} ({lane.reason})" for lane in degraded) + ".")))
    blocks.append(_block("bulleted_list_item", f"{len(queue)} decisions are waiting for Jim in the Decision Queue."))
    for title, names in (("Jira Execution & Sprint Health", ("Jira",)), ("Calendar & Commitments", ("Calendar",)), ("Recruiters, Applications & Waiting-On", ("Gmail and Outlook",)),
                         ("Notion & Drive Hygiene", ("Notion", "Drive")), ("Automation Health", ("Automation",))):
        blocks.append(_block("heading_2", title))
        for name in names:
            lane = by[name]
            blocks.append(_block("paragraph", f"{lane.name}: {lane.health}" + (f" ({lane.reason})" if lane.reason else "") + (f" · source written {lane.at}" if lane.at else "")))
            blocks.extend(_block("bulleted_list_item", line) for line in lane.lines)
    blocks.append(_block("heading_2", "Decision Queue"))
    if not queue:
        blocks.append(_block("paragraph", "No decisions are waiting."))
    for group in wl.GROUPS:
        items = [text for g, text in queue if g == group]
        if items:
            blocks.append(_block("heading_3", group))
            blocks.extend(_block("to_do", text, checked=False) for text in items)
    blocks.append(_block("heading_2", "Next Week Focus"))
    top = [g for g in wl.GROUPS if any(item[0] == g for item in queue)][:3]
    blocks.append(_block("paragraph", ("Work the Decision Queue first: " + ", ".join(top) + "." if top else "Nothing is waiting on a decision; keep the fixed-time commitments.")
                         + " This page proposes nothing in Jira, Calendar or mail; Reviewed and Applied are Jim's."))
    return blocks


def github_runs(environ, friday, opener=None):
    """{workflow file: [(conclusion, finished at)]} for the review week, newest first, or None when GitHub cannot be read."""
    repo, token = environ.get("GITHUB_REPOSITORY"), environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    window = f"{(friday - timedelta(days=6)).isoformat()}..{(friday + timedelta(days=1)).isoformat()}"
    out = {}
    try:
        for name in WORKFLOWS:
            doc = branches.gh(f"/repos/{repo}/actions/workflows/{name}/runs?per_page=100&created={quote(window, safe='.')}", token, **({"opener": opener} if opener else {})) or {}
            out[name] = [(r.get("conclusion"), r.get("updated_at")) for r in doc.get("workflow_runs", [])]
    except (OSError, ValueError, KeyError, TypeError, NotionError):
        return None
    return out


def _children(client, page_id):
    return wl.kids(client, page_id)


def _pointer_text(friday, health, open_count):
    return f"Latest review: week ending {short(friday)} · Generated · Source Health {health} · {open_count} open decisions"


def _set_pointer(client, block_id, friday, health, open_count, link):
    block = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    kind = block.get("type")
    if kind not in POINTER_KINDS:
        raise _fail("POINTER_KIND")
    rich = [{"type": "text", "text": {"content": _pointer_text(friday, health, open_count), "link": {"url": link} if link else None}}]
    client.call_once("PATCH", f"/blocks/{quote(block_id, safe='')}", {kind: {"rich_text": rich}})
    again = client.call("GET", f"/blocks/{quote(block_id, safe='')}")
    if _pointer_text(friday, health, open_count) not in "".join(t.get("plain_text", "") for t in (again.get(kind) or {}).get("rich_text", [])):
        raise _fail("POINTER_READBACK")


def _rows(client, source, flt=None):
    out, cursor = [], None
    for _ in range(20):
        query = {"page_size": 100, **({"filter": flt} if flt else {}), **({"start_cursor": cursor} if cursor else {})}
        page = client.query_data_source(source, query)
        out.extend(page.get("results", []))
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise _fail("QUERY_LOOP")


def _row_date(row):
    start = (((row.get("properties") or {}).get("Week Ending") or {}).get("date") or {}).get("start")
    return date.fromisoformat(start[:10]) if start else None


def collect(client, environ, friday, now, drive_read, opener=None):
    """Read the six lanes for one week. -> [Lane in the page's order]"""
    try:
        page = weekly_archive._page_of(client, weekly_archive._clean(environ.get("JIRA_CARD_BLOCK_ID")))
        regions, header = wl.read_regions(client, page)
        report = client.call("GET", f"/pages/{quote(page, safe='')}")
        edited = datetime.fromisoformat(report["last_edited_time"].replace("Z", "+00:00")).astimezone(TZ)
        hub = bool(client.call("GET", f"/pages/{quote(weekly_archive._clean(environ.get('WEEKLY_REVIEW_PAGE_ID')), safe='')}").get("id"))
    except (NotionError, KeyError, ValueError):
        regions, header, edited, hub = {}, [], None, False
    runs = github_runs(environ, friday, opener)
    parsed = None if runs is None else {k: [(c, datetime.fromisoformat(t.replace("Z", "+00:00"))) for c, t in v if t] for k, v in runs.items()}
    return [wl.jira(regions, now), wl.calendar(regions, now), wl.mail(regions, now), wl.notion_hygiene(header, hub, edited, now), wl.automation(parsed, now), wl.drive(drive_read)]


def _unavailable():
    raise OSError("DRIVE_ROUTE_NOT_CONNECTED")


def write_week(client, source, environ, friday, kind, now, live, drive_read, existing=None, newest=True, opener=None):
    """Make (or, when `existing` is a Degraded row inside 48 hours, rebuild) the row for one Friday. -> a small receipt dict."""
    lanes = collect(client, environ, friday, now, drive_read, opener)
    queue = wl.decisions(lanes)
    health = "Healthy" if all(lane.health == "Healthy" for lane in lanes) else "Degraded"
    receipt = {"week_ending": friday.isoformat(), "kind": kind, "health": health, "open_decisions": len(queue), "degraded": [lane.name for lane in lanes if lane.health != "Healthy"]}
    if not live:
        receipt["action"] = "would_rebuild" if existing else "would_create"
        return receipt
    schema = (client.call("GET", f"/data_sources/{quote(source, safe='')}") or {}).get("properties") or {}
    blocks = body(friday, lanes, queue, now, kind)
    props = properties(schema, friday, health, len(queue), now, newest)
    if existing:
        for child in _children(client, existing):
            client.call_once("PATCH", f"/blocks/{quote(child['id'], safe='')}", {"archived": True})
        client.call_once("PATCH", f"/pages/{quote(existing, safe='')}", {"properties": props})
        page = existing
        for start in range(0, len(blocks), 90):
            client.call("PATCH", f"/blocks/{quote(page, safe='')}/children", {"children": blocks[start:start + 90]})
    else:
        page = client.create(props, blocks)
    read = [b for b in _children(client, page) if b.get("type") != "child_page"]
    if len(read) < len(blocks):
        raise _fail("READBACK_SHORT")
    receipt.update(action="rebuilt" if existing else "created", page=page, blocks=len(read))
    return receipt


def run(limit, live, environ=os.environ, client=None, now=None, drive_read=None, opener=None):
    if not _on(environ.get("WEEKLY_REVIEW")):
        return {"status": "off"}
    source = weekly_archive._clean(environ.get("NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID"))
    if not source or not weekly_archive._clean(environ.get("JIRA_CARD_BLOCK_ID")) or not weekly_archive._clean(environ.get("WEEKLY_REVIEW_PAGE_ID")):
        return {"status": "not_configured"}
    now = (now or datetime.now(TZ))
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    client = client or Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID": source}, source_name="NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID")
    rows = _rows(client, source)
    have = {}
    for row in rows:
        day = _row_date(row)
        if day:
            have.setdefault(day, []).append(row)
    out = {"status": "ok", "live": bool(live), "rows": len(rows), "weeks": []}
    duplicates = [d.isoformat() for d, found in have.items() if len(found) > 1]
    if duplicates:
        out["status"] = "duplicate_weeks"
        out["duplicates"] = duplicates
        return out
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    drive_read = drive_read or (oracle_drive.reader(host) if host else _unavailable)
    todo = wanted(now, have)
    friday = latest_friday(now)
    prior = have.get(friday)
    if prior and now - due_moment(friday) <= REFRESH:
        row = prior[0]
        props = row.get("properties") or {}
        health = (((props.get("Source Health") or {}).get("select") or (props.get("Source Health") or {}).get("status")) or {}).get("name")
        created = datetime.fromisoformat(row["created_time"].replace("Z", "+00:00")).astimezone(TZ)
        if health == "Degraded" and now - created <= REFRESH:
            out["weeks"].append(write_week(client, source, environ, friday, "natural", now, live, drive_read, existing=row["id"], newest=True, opener=opener))
    for day, kind in todo:
        newest = day >= max(have, default=day)
        out["weeks"].append(write_week(client, source, environ, day, kind, now, live, drive_read, newest=newest, opener=opener))
    made = [w for w in out["weeks"] if w.get("action") in ("created", "rebuilt")]
    if live and made:
        current = [r for r in _rows(client, source, {"property": "Current", "checkbox": {"equals": True}})]
        newest_made = max(made, key=lambda w: w["week_ending"])
        for row in current:
            if row["id"].replace("-", "") != newest_made["page"].replace("-", "") and (_row_date(row) or date.min) < date.fromisoformat(newest_made["week_ending"]):
                client.call_once("PATCH", f"/pages/{quote(row['id'], safe='')}", {"properties": {"Current": {"checkbox": False}}})
        pointer = weekly_archive._clean(environ.get("WEEKLY_REVIEW_POINTER_BLOCK_ID"))
        if pointer:
            _set_pointer(client, pointer, date.fromisoformat(newest_made["week_ending"]), newest_made["health"], newest_made["open_decisions"], "")
            out["pointer"] = "updated"
        else:
            out["pointer"] = "not_configured"
    return out
