"""The Daily Command Center: one callout on the Daily Report, built by rules from the other V7 cards.

It reads each card's first line (its `11/13 · 3:31 PM CT` stamp, or DEGRADED / STALE) and its counts, and rewrites
only its own region. Runs last in the tick. Counts and fixed codes only in logs."""

import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, due, report_region, router, runs_today
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.notion_text import plain

TZ = ZoneInfo("America/Chicago")
TITLE = router.DCC_REGION
MODULE = router.OWNERS[TITLE]
FRESH_MINUTES = 90
# Slower cards add their longest gap between runs to FRESH_MINUTES (lifeos/platform/due_table.json).
JOB_OF = {"Calendar": "outlook", "Jira": "jira", "Bills": "bills", "Mail Alerts": "mail", "Amazon": "amazon"}
TIME = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M) CT")
# (label, block id secret, region, status location). "head": in the callout heading; "line": heading stamp plus the
# first text line under it. The retired Attention card is not a source but stays a protected region.
AREAS = (
    ("Calendar", "CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION, "head"),
    ("Jira", "JIRA_CARD_BLOCK_ID", router.JIRA_REGION, "head"),
    ("Bills", "BILLS_CARD_BLOCK_ID", router.BILLS_REGION, "line"),
    ("Mail Alerts", "MAIL_ALERTS_CARD_BLOCK_ID", router.MAIL_ALERTS_REGION, "line"),
    ("Amazon", "AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION, "line"),
)
PROTECTED = tuple((name, region) for _, name, region, _ in AREAS)


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("DCC_" + code)


def _writer(environ):
    return Client(
        {
            "NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused",
        }
    )


def _link(block):
    return next(
        (
            url
            for part in (block.get(block.get("type")) or {}).get("rich_text") or []
            if (url := ((part.get("text") or {}).get("link") or {}).get("url"))
        ),
        None,
    )


def _links(client, block_id):
    kids = report_region.children(client, report_region.clean_id(block_id), _fail)
    return {report_region.plain(b).strip(): _link(b) for b in kids if _link(b)}


def _held(row):
    cells = (row.get("table_row") or {}).get("cells") or []
    step = cells[2] if len(cells) > 2 else []
    return plain(step).startswith("No to-do")


def _text_of(client, block_id, kind, region):
    if kind == "floating":
        found = report_region.floating_region(
            client, report_region.clean_id(block_id), region, _fail, with_status=False
        )
        heading, active = (
            report_region.plain(found["heading"]).strip(),
            sum(1 for r in found["rows"][1:] if not _held(r)),
        )
        stamp = (
            heading[len(region) + 2 : -1].strip() if heading.startswith(region + " (") and heading.endswith(")") else ""
        )
        return (f"{stamp} · {active} active" if stamp and active else stamp), []
    kids = report_region.children(client, report_region.clean_id(block_id), _fail)
    texts = [
        report_region.plain(b).strip()
        for b in kids
        if b.get("type") not in ("table", "child_database", "child_page", "link_to_page")
    ]
    texts = [t for t in texts if t]
    if kind == "head":
        return (texts[0] if texts else ""), texts[1:]
    body = [b for b in kids if b.get("type") not in report_region.HEADINGS]
    lines = [report_region.plain(b).strip() for b in body]
    lines = [t for t in lines if t]
    heading = next((report_region.plain(b).strip() for b in kids if b.get("type") in report_region.HEADINGS), "")
    stamp = heading[len(region) + 2 : -1].strip() if heading.startswith(region + " (") and heading.endswith(")") else ""
    first = lines[0] if lines else ""
    return (f"{stamp} · {first}" if stamp and first else stamp or first), lines[1:]


def read_areas(client, environ):
    out = {}
    for label, name, region, kind in AREAS:
        block_id = (environ.get(name) or "").strip()
        if not block_id:
            out[label] = {"unset": True}
            continue
        try:
            status, lines = _text_of(client, block_id, kind, region)
            links = _links(client, block_id) if label in ("Jira", "Bills") else {}
        except NotionError:
            out[label] = {"unreadable": True}
            continue
        out[label] = {"status": status, "lines": lines, "links": links}
    return out


def fresh_minutes(label, table=None):
    row = (table if table is not None else due.load_table()).get(JOB_OF.get(label, ""), {"every_hours": 1})
    if "slots_ct" in row:
        slots = sorted(int(h) * 60 + int(m) for h, m in (slot.split(":") for slot in row["slots_ct"]))
        gap = max([b - a for a, b in zip(slots, slots[1:])] + [slots[0] + 24 * 60 - slots[-1]])
    else:
        gap = row["every_hours"] * 60 if row["every_hours"] > 1 else 0
    return FRESH_MINUTES + gap


def classify(area, now, minutes=FRESH_MINUTES):
    """Returns (state, "H:MM AM" stamp or None); state: fresh, stale, degraded, unknown, unreadable or unset."""
    if area.get("unset"):
        return "unset", None
    if area.get("unreadable"):
        return "unreadable", None
    text = area.get("status") or ""
    shown = TIME.search(text)
    stamp = f"{shown.group(1)}:{shown.group(2)} {shown.group(3)}" if shown else None
    if text.startswith("DEGRADED"):
        return "degraded", stamp
    if text.startswith("STALE"):
        return "stale", stamp
    found = ct_stamp.read(text)
    if not found:
        return "unknown", None
    month, day, hour12, minute, half = found
    hour = hour12 % 12 + (12 if half == "PM" else 0)
    if month is None:  # time-only stamp: today, or yesterday if later than now
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at > now:
            at -= timedelta(days=1)
    else:  # a stamp after now is from the previous year
        try:
            at = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if at > now + timedelta(days=1):
                at = at.replace(year=at.year - 1)
        except ValueError:
            return "unknown", None
    return ("fresh" if now - at <= timedelta(minutes=minutes) else "stale"), stamp


OVERDUE_BLOCKED = re.compile(r"(\d+) Overdue/Blocked")
SPLIT_COUNTS = re.compile(r"(\d+) Overdue · (\d+) Blocked")


def _overdue_blocked(line):
    """Overdue plus Blocked from the Jira header line (older `N Overdue/Blocked` also reads)."""
    split = SPLIT_COUNTS.search(line or "")
    if split:
        return int(split.group(1)) + int(split.group(2))
    old = OVERDUE_BLOCKED.search(line or "")
    return int(old.group(1)) if old else None


def _number(text, pattern):
    found = re.search(pattern, text or "")
    return int(found.group(1)) if found else None


def compose(areas, now, runs=None):
    """The callout's content as plain data; a count shows only from a current card, never a zero."""
    states = {label: classify(area, now, fresh_minutes(label)) for label, area in areas.items()}

    def source(label):
        state, stamp = states.get(label, ("unset", None))
        return (
            f"{label} · {stamp} CT"
            if state == "fresh"
            else f"{state.upper()} · {label}" + (f" (last {stamp})" if stamp else "")
        )

    def count(label, pattern, line=None):
        text = line if line is not None else (areas.get(label) or {}).get("status")
        return _number(text, pattern) if states.get(label, ("unset",))[0] == "fresh" else None

    jira = areas.get("Jira") or {}
    fresh_lines = list(jira.get("lines", [])) if states.get("Jira", ("unset",))[0] == "fresh" else []
    sprint = next((t for t in fresh_lines if OVERDUE_BLOCKED.search(t) or SPLIT_COUNTS.search(t)), None)
    bills_due, waiting = count("Bills", r"(\d+) bills? due"), count("Mail Alerts", r"(\d+) waiting")
    combined = _overdue_blocked(sprint) if states.get("Jira", ("unset",))[0] == "fresh" else None

    def kpi(value, name, text):
        return (str(value) if value is not None else "—", name, text)

    return {
        "kpis": [
            kpi(bills_due, "Bills due", source("Bills")),
            kpi(
                combined,
                "Jira Overdue / Blocked",
                source("Jira")
                + (
                    " · count not reported" if combined is None and states.get("Jira", ("unset",))[0] == "fresh" else ""
                ),
            ),
            kpi(waiting, "Mail waiting", source("Mail Alerts")),
        ],
        **({"runs": runs} if runs is not None else {}),
    }


ICONS = ("credit-card", "arrow-right", "inbox")


def _t(text, bold=False, gray=False, link=None):
    return {
        "type": "text",
        "text": {"content": text[:1800], **({"link": {"url": link}} if link else {})},
        **(
            {"annotations": {**({"bold": True} if bold else {}), **({"color": "gray"} if gray else {})}}
            if bold or gray
            else {}
        ),
    }


def _b(kind, *parts, children=None, icon=None):
    return {
        "object": "block",
        "type": kind,
        kind: {
            "rich_text": list(parts),
            **(
                {"icon": {"type": "external", "external": {"url": f"https://www.notion.so/icons/{icon}_gray.svg"}}}
                if icon
                else {}
            ),
            **({"children": children} if children else {}),
        },
    }


RUNS_NAME, RUNS_ICON = runs_today.NAME, "layers"
RED = "red"


def _colored(text, color, link=None):
    return {
        "type": "text",
        "text": {"content": text, **({"link": {"url": link}} if link else {})},
        "annotations": {"bold": True, "color": color},
    }


def runs_card(runs, now):
    """The Runs OK today card from `runs_today.read`."""
    state, ok, total, failed = (
        runs.get("state"),
        int(runs.get("ok") or 0),
        int(runs.get("total") or 0),
        list(runs.get("failed") or []),
    )
    value = {"paused": "Paused", "unverified": "—"}.get(state, f"{ok} / {total}")
    if state == "latest_failed":
        label = [_colored("Last run failed", RED, failed[-1] if failed else None)]
    elif state == "failed":
        label = [_colored(f"{len(failed)} failed today", RED, failed[-1] if failed else None)] + [
            _colored(" ↗", RED, url) for url in failed[:-1][-2:]
        ]
    else:
        label = [_t(RUNS_NAME, bold=True)]
    line = "GitHub unreadable" if state == "unverified" else f"GitHub · {ct_stamp.when(now).split(' · ', 1)[1]}"
    return _b(
        "callout",
        icon=RUNS_ICON,
        children=[_b("heading_3", _t(value)), _b("paragraph", *label), _b("paragraph", _t(line, gray=True))],
    )


def render(model):
    def column(ratio, *kids):
        return {"object": "block", "type": "column", "column": {"width_ratio": ratio, "children": list(kids)}}

    def row(*cols):
        return {"object": "block", "type": "column_list", "column_list": {"children": list(cols)}}

    runs = model.get("runs")
    ratio = round(1 / (len(model["kpis"]) + (runs is not None)), 4)
    columns = [
        column(
            ratio,
            _b(
                "callout",
                icon=icon,
                children=[
                    _b("heading_3", _t(value)),
                    _b("paragraph", _t(name, bold=True)),
                    _b("paragraph", _t(text, gray=True)),
                ],
            ),
        )
        for icon, (value, name, text) in zip(ICONS, model["kpis"])
    ]
    if runs is not None:
        columns.append(column(ratio, runs_card(runs, model.get("now") or datetime.now(TZ))))
    return [row(*columns)]


def _kpi_name(client, column):
    for callout in report_region.children(client, column["id"], _fail)[:1]:
        kids = report_region.children(client, callout["id"], _fail)
        lines = [b for b in kids if b.get("type") == "paragraph"]
        return report_region.plain(lines[0]).strip() if lines else ""
    return ""


def foreign_ids(client, block_id, names):
    """Ids of callout blocks that are not this card's; every write leaves them alone."""
    kids = report_region.children(client, block_id, _fail)
    out = set()
    for b in kids[1:]:
        if b.get("type") == "divider":
            out.add(b["id"])
        elif b.get("type") == "column_list":
            columns = report_region.children(client, b["id"], _fail)
            if not any(_kpi_name(client, column) in names for column in columns):
                out.add(b["id"])
    return out


def converge_columns(client, block_id, names, protected_ids, live, skip=frozenset()):
    """Remove surplus KPI columns (the sync cannot shrink a row).

    Nothing is deleted unless the remaining names are exactly the rendered ones in order. Returns columns removed."""
    scoped = report_region.ScopedClient(client, block_id, _fail)
    meta, existing = (
        scoped.call("GET", f"/blocks/{quote(block_id, safe='')}"),
        report_region.children(scoped, block_id, _fail),
    )
    ours = ("toggle", "column_list", *report_region.TEXT_KINDS)
    existing = existing[:1] + [b for b in existing[1:] if b.get("id") not in skip]
    if (
        not isinstance(meta, dict)
        or meta.get("type") != "callout"
        or not existing
        or not report_region.stamped_by_dot(report_region.plain(existing[0]), TITLE)
        or any(b.get("type") not in ours for b in existing[1:])
    ):
        return 0  # not our callout: the sync raises CARD_NOT_OWNED before any write
    rows = [b for b in existing if b.get("type") == "column_list"]
    columns = report_region.children(scoped, rows[0]["id"], _fail) if len(rows) == 1 else []
    if len(columns) <= len(names):
        return 0
    live_names = [_kpi_name(scoped, column) for column in columns]
    surplus = [column for column, name in zip(columns, live_names) if name not in names]
    if [name for name in live_names if name in names] != list(names):
        raise _fail("COLUMNS_UNRECOGNIZED")
    if not live:
        return len(surplus)
    report_region.require_configured(protected_ids, _fail)
    for column in surplus:
        scoped.call("DELETE", f"/blocks/{quote(column['id'], safe='')}")
    left = report_region.children(scoped, rows[0]["id"], _fail)
    if [b["id"] for b in left] != [b["id"] for b in columns if b not in surplus]:
        raise _fail("COLUMNS_VERIFY_FAILED")
    ratio = round(1 / len(names), 4)
    for column in left:
        if (column.get("column") or {}).get("width_ratio") != ratio:
            try:
                scoped.call("PATCH", f"/blocks/{quote(column['id'], safe='')}", {"column": {"width_ratio": ratio}})
            except NotionError:
                pass
    return len(surplus)


def heading_text(now):
    return f"{TITLE} · {now.strftime('%a')} {ct_stamp.when(now)}"


def run(limit, live, environ=os.environ, client=None, now=None):
    block_id = (environ.get("DCC_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"status": "not_configured", "areas": 0, "blocks_written": 0}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    client = client or _writer(environ)
    areas = read_areas(client, environ)
    if not any("status" in area for area in areas.values()):
        raise _fail("NO_EVIDENCE")  # nothing readable: write nothing
    by_area = {label: classify(area, now, fresh_minutes(label))[0] for label, area in areas.items()}
    states = list(by_area.values())
    runs = runs_today.read(environ, now)  # unreadable GitHub history is `unverified`, never a healthy count
    model = compose(areas, now, runs)
    model["now"] = now
    spec = render(model)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    try:
        kpi_names = [name for _, name, _ in model["kpis"]] + [RUNS_NAME]
        skip = foreign_ids(client, block_id, kpi_names)
        trimmed = converge_columns(client, block_id, kpi_names, protected_ids, live, skip)
        # a dry run cannot preflight an untrimmed tree
        changed, removed = (
            (0, 0)
            if trimmed and not live
            else report_region.replace_tree(
                client, block_id, TITLE, MODULE, spec, protected_ids, _fail, "dcc", live, skip, heading_text(now)
            )
        )
    except NotionError:
        raise
    counts = {
        "areas": len(areas),
        "fresh": states.count("fresh"),
        "not_current": len(states) - states.count("fresh"),
        "states": by_area,
        "status": "fresh",
        "blocks_written": 0,
        "columns_removed": trimmed,
        "runs": {k: runs[k] for k in ("state", "ok", "total", "why")},
    }
    if live:
        counts["blocks_written"], counts["removed"] = changed, removed
    return counts
