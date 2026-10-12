"""The Daily Command Center, written by V7 (D175): one callout on the Daily Report, built by rules from what the other V7 cards say on the page.

It reads the first line of each V7 card (the `Updated H:MM AM/PM CT` stamp, or DEGRADED / STALE) and the counts those lines already carry, and writes its own region only, through the
same block-local write the other cards use (heading kept, old text replaced, every other V7 region proven unchanged around the write). No new data source, no model, no judgement the
cards did not already state. It runs last in V7's tick, so no other card is being written while it reads or writes. Counts and fixed codes only in logs."""
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError

TZ = ZoneInfo("America/Chicago")
TITLE = router.DCC_REGION
MODULE = router.OWNERS[TITLE]
FRESH_MINUTES = 90                                        # V7's own daily check uses the same figure
STAMP = re.compile(r"Updated (\d{1,2}):(\d{2}) ([AP]M) CT")
TIME = re.compile(r"(\d{1,2}):(\d{2}) ([AP]M) CT")          # the clock in a DEGRADED or STALE line ("failed at 10:05 PM CT")
# (label, block id secret, where the status text is). "head": the callout's heading carries it; "line": the first text line under the heading; "floating": the Hiring Pipeline's status paragraph.
AREAS = (("Calendar", "CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION, "head"), ("Jira", "JIRA_CARD_BLOCK_ID", router.JIRA_REGION, "head"),
         ("Bills", "BILLS_CARD_BLOCK_ID", router.BILLS_REGION, "line"), ("Mail Alerts", "MAIL_ALERTS_CARD_BLOCK_ID", router.MAIL_ALERTS_REGION, "line"),
         ("Amazon", "AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION, "line"), ("Attention", "ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION, "line"),
         ("Hiring Pipeline", "HIRING_CARD_BLOCK_ID", router.HIRING_REGION, "floating"))
PROTECTED = tuple((name, region) for _, name, region, _ in AREAS)


class CardError(NotionError):
    """Fixed codes only."""


def _fail(code):
    return CardError("DCC_" + code)


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _text_of(client, block_id, kind, region):
    """-> (status text, [every other text line of the card]) or raises NotionError."""
    if kind == "floating":
        found = report_region.floating_region(client, report_region.clean_id(block_id), region, _fail)
        return report_region.plain(found["status"]).strip(), []
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
        except NotionError:
            out[label] = {"unreadable": True}
            continue
        out[label] = {"status": status, "lines": lines}
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
    """-> [(label or None, text)]: the callout's lines. Pure."""
    states = {label: classify(area, now) for label, area in areas.items()}
    ok = [label for label, (state, _) in states.items() if state == "fresh"]
    bad = [(label, state) for label, (state, _) in states.items() if state != "fresh"]
    head = f"Updated {now.strftime('%-I:%M %p')} CT · {len(ok)} of {len(states)} areas current" + (f" · {len(bad)} need a look" if bad else "")

    def status(label):
        return (areas.get(label) or {}).get("status") or ""

    jira = (areas.get("Jira") or {})
    lines = [jira.get("status", "")] + list(jira.get("lines", []))
    gtv = next((t for t in lines if t.startswith("GTV Action")), None)
    sprint = next((t for t in lines if "Overdue/Blocked" in t), None)
    focus = "; ".join(t for t in (gtv, sprint) if t) or ("Jira " + states.get("Jira", ("unknown", None))[0])
    jira_open = _number(sprint, r"(\d+) Overdue/Blocked")

    overdue = _number(status("Bills"), r"(\d+) overdue")
    exceptions = _number(status("Attention"), r"(\d+) active exception")
    waiting, review = _number(status("Mail Alerts"), r"(\d+) waiting"), _number(status("Mail Alerts"), r"(\d+) need review")
    hiring = _number(status("Hiring Pipeline"), r"(\d+) active")
    attention = [f"{overdue} overdue bills" if overdue else None, f"{exceptions} active Attention exceptions" if exceptions else None,
                 f"{waiting} mail items waiting" if waiting else None, f"{review} mail items need review" if review else None,
                 f"{hiring} interview opportunities active" if hiring else None]
    attention = [a for a in attention if a]

    freshness = " · ".join(f"{label} {stamp}" if state == "fresh" else f"{label} {state}" + (f" (last {stamp})" if stamp else "")
                           for label, (state, stamp) in states.items())
    nxt = []
    if bad:
        nxt.append("Check " + ", ".join(label for label, _ in bad) + " (not current)")
    if overdue:
        nxt.append(f"Triage {overdue} overdue bills")
    if jira_open:
        nxt.append(f"Clear {jira_open} overdue or blocked Jira items")
    if exceptions:
        nxt.append(f"Review {exceptions} Attention exceptions")
    if review:
        nxt.append(f"Review {review} mail items")
    return [(None, head), ("Current focus", focus), ("Attention needed", "; ".join(attention) or "Nothing flagged by the cards."),
            ("Evidence freshness", freshness), ("Next useful action", "; ".join(nxt) or "Nothing flagged; continue the day's planned work.")]


def render(lines):
    blocks = []
    for label, text in lines:
        rich = ([{"type": "text", "text": {"content": label + ": "}, "annotations": {"bold": True}}] if label else []) + [{"type": "text", "text": {"content": text[:1800]}}]
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}})
    return blocks


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
    blocks = render(compose(areas, now))
    protected_ids = {region: (environ.get(name) or "").strip() for name, region in PROTECTED}
    _, removed = report_region.replace_text(client, block_id, TITLE, MODULE, blocks, protected_ids, _fail, "dcc", live)
    counts = {"areas": len(areas), "fresh": states.count("fresh"), "not_current": len(states) - states.count("fresh"), "states": by_area, "status": "fresh", "blocks_written": 0}
    if live:
        counts["blocks_written"], counts["removed"] = len(blocks), removed
    return counts
