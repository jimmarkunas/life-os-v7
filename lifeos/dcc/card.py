"""The Daily Command Center, written by V7 (D175): one callout on the Daily Report, built by rules from what the other V7 cards say on the page.

It reads the first line of each V7 card (the `11/13 · 3:31 PM CT` stamp (or the older `Updated Nov 13 · 3:31 PM CT` or `Updated H:MM AM/PM CT`), or DEGRADED / STALE) and the counts those lines already carry, and writes its own region only, through the
same block-local write the other cards use (heading kept, old text replaced, every other V7 region proven unchanged around the write). No new data source, no model, no judgement the
cards did not already state. It runs last in V7's tick, so no other card is being written while it reads or writes. Counts and fixed codes only in logs."""
import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.dcc import header
from lifeos.platform import ct_stamp, due, report_region, router
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.notion_text import plain

TZ = ZoneInfo("America/Chicago")
TITLE = router.DCC_REGION
MODULE = router.OWNERS[TITLE]
FRESH_MINUTES = 90                                        # an hourly card is current this long after its stamp
# LIFE-612: a card that runs less often than every tick is current for the longest gap between its runs plus the same 90 minutes, read from the due-time table (lifeos/platform/due_table.json).
JOB_OF = {"Calendar": "outlook", "Jira": "jira", "Bills": "bills", "Mail Alerts": "mail", "Amazon": "amazon", "Hiring Pipeline": "hiring"}
TIME = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M) CT")          # the clock in a DEGRADED or STALE line ("failed at 10:05 PM CT")
# (label, block id secret, where the status text is). "head": the callout's heading carries it; "line": the heading's stamp, then the first text line under the heading (the counts; a card that still carries its stamp in that line works as before); "floating": the Hiring Pipeline's heading stamp and table rows.
# LIFE-645: V7 retired the Attention card, so the Attention callout is no longer a source (it would read STALE for good); it stays a protected region.
AREAS = (("Calendar", "CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION, "head"), ("Jira", "JIRA_CARD_BLOCK_ID", router.JIRA_REGION, "head"),
         ("Bills", "BILLS_CARD_BLOCK_ID", router.BILLS_REGION, "line"), ("Mail Alerts", "MAIL_ALERTS_CARD_BLOCK_ID", router.MAIL_ALERTS_REGION, "line"),
         ("Amazon", "AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION, "line"),
         ("Hiring Pipeline", "HIRING_CARD_BLOCK_ID", router.HIRING_REGION, "floating"))
PROTECTED = tuple((name, region) for _, name, region, _ in AREAS) + (("ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION),)


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("DCC_" + code)


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _link(block):
    return next((url for part in (block.get(block.get("type")) or {}).get("rich_text") or [] if (url := ((part.get("text") or {}).get("link") or {}).get("url"))), None)


def _links(client, block_id):
    kids = report_region.children(client, report_region.clean_id(block_id), _fail)
    return {report_region.plain(b).strip(): _link(b) for b in kids if _link(b)}


def _held(row):
    """LIFE-111: a row Jim paused or set to waiting reads "No to-do · ..." in its Next step cell; it is listed, never counted as active."""
    cells = (row.get("table_row") or {}).get("cells") or []
    step = cells[2] if len(cells) > 2 else []
    return plain(step).startswith("No to-do")


def _text_of(client, block_id, kind, region):
    """-> (status text, [every other text line of the card]) or raises NotionError."""
    if kind == "floating":
        found = report_region.floating_region(client, report_region.clean_id(block_id), region, _fail, with_status=False)
        heading, active = report_region.plain(found["heading"]).strip(), sum(1 for r in found["rows"][1:] if not _held(r))     # the Hiring Pipeline has no status paragraph: the heading and the table rows say it all
        stamp = heading[len(region) + 2:-1].strip() if heading.startswith(region + " (") and heading.endswith(")") else ""        # D188: the update time lives in the heading, as Calendar's does
        return (f"{stamp} · {active} active" if stamp and active else stamp), []
    kids = report_region.children(client, report_region.clean_id(block_id), _fail)
    texts = [report_region.plain(b).strip() for b in kids if b.get("type") not in ("table", "child_database", "child_page", "link_to_page")]
    texts = [t for t in texts if t]
    if kind == "head":
        return (texts[0] if texts else ""), texts[1:]
    body = [b for b in kids if b.get("type") not in report_region.HEADINGS]
    lines = [report_region.plain(b).strip() for b in body]
    lines = [t for t in lines if t]
    heading = next((report_region.plain(b).strip() for b in kids if b.get("type") in report_region.HEADINGS), "")
    stamp = heading[len(region) + 2:-1].strip() if heading.startswith(region + " (") and heading.endswith(")") else ""        # LIFE-609: the update time lives in the heading, as Calendar's does
    first = lines[0] if lines else ""
    return (f"{stamp} · {first}" if stamp and first else stamp or first), lines[1:]


def read_areas(client, environ):
    """{label: {"status": text, "lines": [...]} | {"unreadable": True} | {"unset": True}}. One card that cannot be read never stops the others."""
    out = {}
    for label, name, region, kind in AREAS:
        block_id = (environ.get(name) or "").strip()
        if not block_id:
            out[label] = {"unset": True}
            continue
        try:
            status, lines = _text_of(client, block_id, kind, region)
            links = _links(client, block_id) if label in ("Jira", "Bills") else {}              # the real records Primary focus and a suggestion open
        except NotionError:
            out[label] = {"unreadable": True}
            continue
        out[label] = {"status": status, "lines": lines, "links": links}
    return out


def fresh_minutes(label, table=None):
    """How long after its stamp this card still counts as current: 90 minutes for an hourly card, plus the longest gap between runs for a card the due-time table runs less often."""
    row = (table if table is not None else due.load_table()).get(JOB_OF.get(label, ""), {"every_hours": 1})
    if "slots_ct" in row:
        slots = sorted(int(h) * 60 + int(m) for h, m in (slot.split(":") for slot in row["slots_ct"]))
        gap = max([b - a for a, b in zip(slots, slots[1:])] + [slots[0] + 24 * 60 - slots[-1]])
    else:
        gap = row["every_hours"] * 60 if row["every_hours"] > 1 else 0
    return FRESH_MINUTES + gap


def classify(area, now, minutes=FRESH_MINUTES):
    """-> (state, "H:MM AM" stamp or None). state: fresh | stale | degraded | unknown | unreadable | unset. `minutes`: how old a stamp may be and still be fresh."""
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
    found = ct_stamp.read(text)                         # LIFE-679: the new `11/13 · 3:31 PM CT` or an older `Updated ...` form
    if not found:
        return "unknown", None
    month, day, hour12, minute, half = found
    hour = hour12 % 12 + (12 if half == "PM" else 0)
    if month is None:                                   # the time-only form: today's clock, or yesterday's when it is later than now
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at > now:
            at -= timedelta(days=1)
    else:                                               # LIFE-660: the real date; a stamp that lands after now is the year before (a Dec 31 stamp read on Jan 1)
        try:
            at = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if at > now + timedelta(days=1):
                at = at.replace(year=at.year - 1)
        except ValueError:
            return "unknown", None                      # not a date: never guessed fresh
    return ("fresh" if now - at <= timedelta(minutes=minutes) else "stale"), stamp


def _number(text, pattern):
    found = re.search(pattern, text or "")
    return int(found.group(1)) if found else None


def compose(areas, now):
    """-> the callout's content as plain data (the approved native layout, LIFE-57). Pure. A count is shown only from a card that is current: a stale, degraded or unreadable card shows
    its own state in place of a number, never a zero."""
    states = {label: classify(area, now, fresh_minutes(label)) for label, area in areas.items()}
    ok, bad = [l for l, (state, _) in states.items() if state == "fresh"], [(l, state) for l, (state, _) in states.items() if state != "fresh"]

    def source(label):
        state, stamp = states.get(label, ("unset", None))
        return f"{label} · {stamp} CT" if state == "fresh" else f"{state.upper()} · {label}" + (f" (last {stamp})" if stamp else "")

    def count(label, pattern, line=None):
        text = line if line is not None else (areas.get(label) or {}).get("status")
        return _number(text, pattern) if states.get(label, ("unset",))[0] == "fresh" else None

    jira = areas.get("Jira") or {}
    fresh_lines = list(jira.get("lines", [])) if states.get("Jira", ("unset",))[0] == "fresh" else []
    sprint = next((t for t in fresh_lines if "Overdue/Blocked" in t), None)
    bills_due, waiting = count("Bills", r"(\d+) bills? due"), count("Mail Alerts", r"(\d+) waiting")
    combined = count("Jira", r"(\d+) Overdue/Blocked", sprint)
    freshness = " · ".join(f"{label} {stamp}" if state == "fresh" else f"{label} {state}" + (f" (last {stamp})" if stamp else "") for label, (state, stamp) in states.items())

    def kpi(value, name, text):
        return (str(value) if value is not None else "—", name, text)

    return {"health": f"Source health and freshness · {len(ok)} of {len(states)} current" + "".join(f" · {label} {state.upper()}" for label, state in bad),
            "detail": [freshness, ("✅" if not bad else "⚠️") + f" {len(ok)} of {len(states)} source cards current · This is source-card health, not proof that every task is complete."],
            "kpis": [kpi(bills_due, "Bills due", source("Bills")), kpi(combined, "Jira Overdue / Blocked", source("Jira") + (" · count not reported" if combined is None and states.get("Jira", ("unset",))[0] == "fresh" else "")),
                     kpi(waiting, "Mail waiting", source("Mail Alerts"))]}

ICONS = ("credit-card", "arrow-right", "inbox")                  # the approved mockup's native icons, in card order


def _t(text, bold=False, gray=False, link=None):
    return {"type": "text", "text": {"content": text[:1800], **({"link": {"url": link}} if link else {})}, **({"annotations": {**({"bold": True} if bold else {}), **({"color": "gray"} if gray else {})}} if bold or gray else {})}


def _b(kind, *parts, children=None, icon=None):
    return {"object": "block", "type": kind, kind: {"rich_text": list(parts), **({"icon": {"type": "external", "external": {"url": f"https://www.notion.so/icons/{icon}_gray.svg"}}} if icon else {}),
                                                    **({"children": children} if children else {})}}


def render(model):
    """Source health directly below the heading, then four equal KPI callouts."""
    def column(ratio, *kids):
        return {"object": "block", "type": "column", "column": {"width_ratio": ratio, "children": list(kids)}}

    def row(*cols):
        return {"object": "block", "type": "column_list", "column_list": {"children": list(cols)}}

    health = _b("toggle", _t(model["health"]), children=[_b("paragraph", _t(text)) for text in model["detail"]])
    return [health, row(*[column(round(1 / len(model["kpis"]), 4), _b("callout", icon=icon, children=[_b("heading_3", _t(value)), _b("paragraph", _t(name, bold=True)), _b("paragraph", _t(text, gray=True))]))
                         for icon, (value, name, text) in zip(ICONS, model["kpis"])])]


def _kpi_name(client, column):
    """The KPI's name line (the bold paragraph under its number) in a live column, or "" when the column does not look like one of ours."""
    for callout in report_region.children(client, column["id"], _fail)[:1]:
        lines = [b for b in report_region.children(client, callout["id"], _fail) if b.get("type") == "paragraph"]
        return report_region.plain(lines[0]).strip() if lines else ""
    return ""


def converge_columns(client, block_id, names, protected_ids, live):
    """LIFE-655: the sync refuses to shrink a row of columns, so when the callout's KPI row holds more columns than the card now renders (LIFE-645 retired the Attention KPI: 4 live, 3 rendered),
    the surplus columns are removed here first, through a client scoped to this callout (any other block is refused before it is sent). A surplus column is one whose KPI name is not rendered
    any more; what is left must be exactly the rendered names in order, or nothing is deleted. Each delete is read back, then the remaining columns get the balanced width. -> columns removed (0: nothing to do)."""
    scoped = report_region.ScopedClient(client, block_id, _fail)
    meta, existing = scoped.call("GET", f"/blocks/{quote(block_id, safe='')}"), report_region.children(scoped, block_id, _fail)
    ours = ("toggle", "column_list", *report_region.TEXT_KINDS)
    if (not isinstance(meta, dict) or meta.get("type") != "callout" or not existing or report_region.plain(existing[0]).strip() != TITLE or any(b.get("type") not in ours for b in existing[1:])):
        return 0                                                                      # not our callout as the sync expects it: it raises CARD_NOT_OWNED itself, before any write
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
                pass                                                                  # the width is cosmetic: the next sync still fills the columns in place
    return len(surplus)


def _stamp(client, block_id, now, live):
    try:
        return header.run(client, report_region.clean_id(block_id), now, live, _fail)
    except NotionError as error:
        return str(error)[:60]


def run(limit, live, environ=os.environ, client=None, now=None):
    block_id = (environ.get("DCC_CARD_BLOCK_ID") or "").strip()
    if not block_id:
        return {"status": "not_configured", "areas": 0, "blocks_written": 0}
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    client = client or _writer(environ)
    areas = read_areas(client, environ)
    if not any("status" in area for area in areas.values()):
        raise _fail("NO_EVIDENCE")                         # nothing could be read: say nothing rather than write an empty summary
    by_area = {label: classify(area, now, fresh_minutes(label))[0] for label, area in areas.items()}
    states = list(by_area.values())
    model = compose(areas, now)
    spec = render(model)
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    try:
        trimmed = converge_columns(client, block_id, [name for _, name, _ in model["kpis"]], protected_ids, live)
        changed, removed = (0, 0) if trimmed and not live else report_region.replace_tree(client, block_id, TITLE, MODULE, spec, protected_ids, _fail, "dcc", live)       # a dry run cannot preflight a tree it has not yet trimmed
    except NotionError as error:
        if "CARD_NOT_OWNED" not in str(error):                       # LIFE-655: a failed callout write no longer freezes the page's Updated line; a wrong target still writes nothing
            print("dcc: header", _stamp(client, block_id, now, live))
        raise
    counts = {"areas": len(areas), "fresh": states.count("fresh"), "not_current": len(states) - states.count("fresh"), "states": by_area, "status": "fresh", "blocks_written": 0, "columns_removed": trimmed}
    if live:
        counts["blocks_written"], counts["removed"] = changed, removed
    counts["header"] = _stamp(client, block_id, now, live)                                                              # D183: never changes the Command Center's own result
    return counts
