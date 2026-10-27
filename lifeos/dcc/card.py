"""The Daily Command Center, written by V7 (D175): one callout on the Daily Report, built by rules from what the other V7 cards say on the page.

It reads the first line of each V7 card (the `Updated H:MM AM/PM CT` stamp, or DEGRADED / STALE) and the counts those lines already carry, and writes its own region only, through the
same block-local write the other cards use (heading kept, old text replaced, every other V7 region proven unchanged around the write). No new data source, no model, no judgement the
cards did not already state. It runs last in V7's tick, so no other card is being written while it reads or writes. Counts and fixed codes only in logs."""
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.dcc import header
from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError

TZ = ZoneInfo("America/Chicago")
TITLE = router.DCC_REGION
MODULE = router.OWNERS[TITLE]
FRESH_MINUTES = 90                                        # V7's own daily check uses the same figure
STAMP = re.compile(r"Updated (\d{1,2}):(\d{2}) ([AP]M) CT")
TIME = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M) CT")          # the clock in a DEGRADED or STALE line ("failed at 10:05 PM CT")
# (label, block id secret, where the status text is). "head": the callout's heading carries it; "line": the first text line under the heading; "own": the callout's own text (the headless Attention callout, D131);
# "floating": the Hiring Pipeline's heading stamp and table rows.
AREAS = (("Calendar", "CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION, "head"), ("Jira", "JIRA_CARD_BLOCK_ID", router.JIRA_REGION, "head"),
         ("Bills", "BILLS_CARD_BLOCK_ID", router.BILLS_REGION, "line"), ("Mail Alerts", "MAIL_ALERTS_CARD_BLOCK_ID", router.MAIL_ALERTS_REGION, "line"),
         ("Amazon", "AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION, "line"), ("Attention", "ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION, "own"),
         ("Hiring Pipeline", "HIRING_CARD_BLOCK_ID", router.HIRING_REGION, "floating"))
PROTECTED = tuple((name, region) for _, name, region, _ in AREAS)


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


def _text_of(client, block_id, kind, region):
    """-> (status text, [every other text line of the card]) or raises NotionError."""
    if kind == "own":
        meta = client.call("GET", f"/blocks/{report_region.clean_id(block_id)}")
        if not isinstance(meta, dict) or meta.get("type") != "callout":
            raise _fail("CARD_UNREADABLE")
        return report_region.plain({"type": "callout", "callout": meta.get("callout") or {}}).strip(), []
    if kind == "floating":
        found = report_region.floating_region(client, report_region.clean_id(block_id), region, _fail, with_status=False)
        heading, active = report_region.plain(found["heading"]).strip(), len(found["rows"]) - 1           # the Hiring Pipeline has no status paragraph: the heading and the table rows say it all
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
    return (lines[0] if lines else ""), lines[1:]


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


def classify(area, now):
    """-> (state, "H:MM AM" stamp or None). state: fresh | stale | degraded | unknown | unreadable | unset."""
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
    found = STAMP.search(text)
    if not found:
        return "unknown", None
    hour = int(found.group(1)) % 12 + (12 if found.group(3) == "PM" else 0)
    at = now.replace(hour=hour, minute=int(found.group(2)), second=0, microsecond=0)
    if at > now:
        at -= timedelta(days=1)                         # a stamp later than now is yesterday's
    return ("fresh" if now - at <= timedelta(minutes=FRESH_MINUTES) else "stale"), stamp


def _number(text, pattern):
    found = re.search(pattern, text or "")
    return int(found.group(1)) if found else None


def compose(areas, now):
    """-> the callout's content as plain data (the approved native layout, LIFE-57). Pure. A count is shown only from a card that is current: a stale, degraded or unreadable card shows
    its own state in place of a number, never a zero."""
    states = {label: classify(area, now) for label, area in areas.items()}
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
    exceptions = 0 if "No active exceptions" in ((areas.get("Attention") or {}).get("status") or "") and states["Attention"][0] == "fresh" else count("Attention", r"(\d+) active exception")
    freshness = " · ".join(f"{label} {stamp}" if state == "fresh" else f"{label} {state}" + (f" (last {stamp})" if stamp else "") for label, (state, stamp) in states.items())

    def kpi(value, name, text):
        return (str(value) if value is not None else "—", name, text)

    return {"health": f"Source health and freshness · {len(ok)} of {len(states)} current" + "".join(f" · {label} {state.upper()}" for label, state in bad),
            "detail": [freshness, ("✅" if not bad else "⚠️") + f" {len(ok)} of {len(states)} source cards current · This is source-card health, not proof that every task is complete."],
            "kpis": [kpi(bills_due, "Bills due", source("Bills")), kpi(combined, "Jira Overdue / Blocked", source("Jira") + (" · count not reported" if combined is None and states.get("Jira", ("unset",))[0] == "fresh" else "")),
                     kpi(exceptions, "Attention items", source("Attention")), kpi(waiting, "Mail waiting", source("Mail Alerts"))]}

ICONS = ("credit-card", "arrow-right", "arrow-up", "inbox")                  # the approved mockup's native icons, in card order


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
    return [health, row(*[column(0.25, _b("callout", icon=icon, children=[_b("heading_3", _t(value)), _b("paragraph", _t(name, bold=True)), _b("paragraph", _t(text, gray=True))]))
                         for icon, (value, name, text) in zip(ICONS, model["kpis"])])]


def _decision_component(client, block, fail):
    """Recognize only the standalone DCC decision callout and its one-column wrapper from the retired layout."""
    if block.get("type") == "callout":
        kids = report_region.children(client, block["id"], fail)
        return bool(kids and report_region.plain(kids[0]).strip() == "Suggested next decisions")
    if block.get("type") != "column_list":
        return False
    columns = report_region.children(client, block["id"], fail)
    if len(columns) != 1 or columns[0].get("type") != "column":
        return False
    kids = report_region.children(client, columns[0]["id"], fail)
    return len(kids) == 1 and _decision_component(client, kids[0], fail)


def _attention_has_decisions(client, attention_id, fail):
    """True only when the Attention callout's own text already carries the decisions this widget used to show."""
    if not attention_id:
        return False
    meta = client.call("GET", f"/blocks/{report_region.clean_id(attention_id)}")
    if not isinstance(meta, dict) or meta.get("type") != "callout":
        return False
    return "Suggested next decisions" in report_region.plain({"type": "callout", "callout": meta.get("callout") or {}})


def _remove_decision_component(client, block_id, protected_ids, fail, live, replaced=True):
    meta = client.call("GET", f"/blocks/{report_region.clean_id(block_id)}")
    kids = report_region.children(client, report_region.clean_id(block_id), fail)
    if not isinstance(meta, dict) or meta.get("type") != "callout" or not kids or kids[0].get("type") not in report_region.HEADINGS or report_region.plain(kids[0]).strip() != TITLE:
        raise fail("CARD_NOT_OWNED")
    matches = [(index, block) for index, block in enumerate(kids[1:], 1) if _decision_component(client, block, fail)]
    if not matches:
        return False, 0
    if not replaced:
        raise fail("DECISIONS_NOT_IN_ATTENTION")                       # never delete the only copy: the Attention card has not written the decisions yet
    if len(matches) != 1 or matches[0][0] != len(kids) - 1:
        raise fail("CARD_STRUCTURE_CHANGED")
    retired = matches[0][1]
    try:
        router.check_write(MODULE, [TITLE])
    except router.RouterError:
        raise fail("CARD_NOT_OWNED") from None
    if not live:
        return True, 0
    before = report_region.protected(client, protected_ids, fail)
    try:
        client.call("DELETE", f"/blocks/{retired['id']}")
    finally:
        report_region._prove(client, MODULE, before, protected_ids, "dcc", fail)
    if any(block.get("id") == retired["id"] for block in report_region.children(client, report_region.clean_id(block_id), fail)):
        raise fail("CARD_VERIFY_FAILED")
    return True, 1


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
    by_area = {label: classify(area, now)[0] for label, area in areas.items()}
    states = list(by_area.values())
    spec = render(compose(areas, now))
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    replaced = _attention_has_decisions(client, environ.get("ATTENTION_CARD_BLOCK_ID"), _fail)
    retired, removed_widget = _remove_decision_component(client, block_id, protected_ids, _fail, live, replaced)
    if retired and not live:
        changed, removed = 0, 0
    else:
        changed, removed = report_region.replace_tree(client, block_id, TITLE, MODULE, spec, protected_ids, _fail, "dcc", live)
    counts = {"areas": len(areas), "fresh": states.count("fresh"), "not_current": len(states) - states.count("fresh"), "states": by_area, "status": "fresh", "blocks_written": 0}
    if live:
        counts["blocks_written"], counts["removed"] = changed + removed_widget, removed
    try:
        counts["header"] = header.run(client, report_region.clean_id(block_id), now, live, _fail)                      # D183: never changes the Command Center's own result
    except NotionError as error:
        counts["header"] = str(error)[:60]
    return counts
