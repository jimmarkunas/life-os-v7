"""The Daily Report's Oracle Cloud callout, once a day (Jim's approval Oct 15 2026: Option B, OCI's own data; the callout under Communications Attention).

hourly tick -> domains.yml `imessage` job (already on the tailnet as tag:v7-runner) -> Oracle's private endpoint, request `oracle1` -> this stage -> Notion.
Oracle answers numbers only (lifeos/oraclecloud/oracle/stats.mjs): month-to-date cost from OCI billing, CPU from OCI Monitoring, the Always Free A1 core
headroom from OCI Limits, Oracle's idle-reclaim rule, local memory and disk, service health and the ChatGPT plan windows. Each tick reads only the heading:
the card is due once per Chicago day, from 6 AM, and again on the next tick while the last write was DEGRADED. ORACLE_CARD_FORCE=true (a manual run) writes now.

Live, it changes exactly two blocks of the callout Jim made: the heading's stamp (`Oracle Cloud (Updated Oct 15 · 7:05 AM CT)`, with `DEGRADED · ` in front
when any reading is missing) and the one paragraph under it (one line per reading, text bars for memory, disk and the ChatGPT plan). Both are read back and
every other V7 region is proven unchanged. Counts and fixed codes only in the log."""
import json
import os
import socket
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text

TZ = ZoneInfo("America/Chicago")
TITLE = router.ORACLE_REGION
MODULE = router.OWNERS[TITLE]
PORT = 47341
SCHEMA = "lifeos.oracle.stats.v1"
MAX_REPLY = 4096
WAIT = 100                                                             # seconds: the endpoint itself stops at 90
REPLY_TTL_SECONDS = 600
DUE_HOUR = 6                                                           # Chicago time: the day's card is written on the first tick from 6 AM
KEYS = {"schema", "observed_at", "cost", "cpu_24h_pct", "memory_pct", "disk_pct", "a1_cores", "reclaim", "services", "chatgpt"}
SERVICES = (("gateway", "Gateway"), ("telegram", "Telegram"), ("whatsapp", "WhatsApp"), ("alerts", "Alerts"))
UNREPORTED = "unreported"         # the plan windows under Sign in with ChatGPT: OpenClaw prints none (Oct 16 2026), a known state, never counted as missing
BAR = 10
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION),
             ("JOB_OUTREACH_CARD_BLOCK_ID", router.JOB_OUTREACH_REGION))
UNAVAILABLE = {"cost": None, "cpu_24h_pct": None, "memory_pct": None, "disk_pct": None, "a1_cores": None, "reclaim": None,
               "services": {k: None for k, _ in SERVICES}, "chatgpt": None}


class OracleCardError(Exception):
    pass


def _fail(code):
    return OracleCardError(code)


def fetch(host, port=PORT):
    data = b""
    with socket.create_connection((host, port), timeout=WAIT) as s:
        s.sendall(b"oracle1\n")
        while len(data) <= MAX_REPLY:
            chunk = s.recv(MAX_REPLY + 1 - len(data))
            if not chunk:
                break
            data += chunk
    return data


def _pct(v):
    return v is None or (type(v) in (int, float) and 0 <= v <= 100)


def _whole(v):
    return type(v) is int and v >= 0


def validate(raw, now):
    """-> the readings ({key: value or None}). Off-contract or stale raises; a reply is used whole or not at all."""
    if not raw or len(raw) > MAX_REPLY:
        raise _fail("ORACLE_UNREACHABLE")
    try:
        p = json.loads(raw)
        seen = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _fail("ORACLE_OFF_CONTRACT") from None
    cost, a1, svc, plan = p.get("cost"), p.get("a1_cores"), p.get("services"), p.get("chatgpt")
    if (set(p) != KEYS or p["schema"] != SCHEMA or not all(_pct(p[k]) for k in ("cpu_24h_pct", "memory_pct", "disk_pct"))
            or not (cost is None or (isinstance(cost, dict) and set(cost) == {"amount", "currency"} and type(cost["amount"]) in (int, float)
                                     and cost["amount"] >= 0 and isinstance(cost["currency"], str) and len(cost["currency"]) == 3))
            or not (a1 is None or (isinstance(a1, dict) and set(a1) == {"used", "available"} and _whole(a1["used"]) and _whole(a1["available"])))
            or p["reclaim"] not in (None, "NONE", "AT_RISK")
            or not (isinstance(svc, dict) and set(svc) == {k for k, _ in SERVICES} and all(v in (None, True, False) for v in svc.values()))
            or not (plan in (None, UNREPORTED) or (isinstance(plan, dict) and set(plan) == {"left_5h", "left_week"} and all(_whole(v) and v <= 100 for v in plan.values())))):
        raise _fail("ORACLE_OFF_CONTRACT")
    if seen.tzinfo is None or abs((now - seen).total_seconds()) > REPLY_TTL_SECONDS:
        raise _fail("ORACLE_STALE")
    return {k: p[k] for k in KEYS - {"schema", "observed_at"}}


def bar(pct):
    """-> [(text, color)]: the filled part green, the rest gray (Jim, Oct 15)."""
    filled = min(BAR, max(0, round(pct / 100 * BAR)))
    return [(text, color) for text, color in (("█" * filled, "green"), ("░" * (BAR - filled), "gray")) if text]


def missing(r):
    """How many readings are unavailable (a stopped service is a reading, not a missing one)."""
    return sum(r[k] is None for k in ("cost", "cpu_24h_pct", "memory_pct", "disk_pct", "a1_cores", "reclaim", "chatgpt")) + sum(
        v is None for v in r["services"].values())


def _gauge(label, pct, suffix="%"):
    """`Memory ████░░░░░░ 44%` as segments, or `Memory unavailable`."""
    return [(f"{label} unavailable", None)] if pct is None else [(f"{label} ", None), *bar(pct), (f" {pct:.0f}{suffix}", None)]


def render(r, now):
    """readings -> (paragraph segments [(text, color or None)], heading text). Jim's layout (Oct 15): cost and CPU on one line; memory and disk; cores
    and reclaim risk on one line with services under it; both Codex (ChatGPT plan) windows; blank lines between those groups."""
    cost, a1, plan, sep = r["cost"], r["a1_cores"], r["chatgpt"], [(" · ", None)]
    first = ("Cost this month: unavailable" if cost is None else
             f"Cost this month: {'$' if cost['currency'] == 'USD' else cost['currency'] + ' '}{cost['amount']:.2f} (Oracle billing)")
    cpu = "CPU, last 24 h: unavailable" if r["cpu_24h_pct"] is None else f"CPU, last 24 h: avg {r['cpu_24h_pct']:.1f}%"
    cores = ("Always Free A1 cores: unavailable" if a1 is None else f"Always Free A1 cores: {a1['used']} of {a1['used'] + a1['available']} in use · "
             + ("no headroom" if a1["available"] == 0 else f"{a1['available']} free"))
    reclaim = {"NONE": "Reclaim risk: none", "AT_RISK": "Reclaim risk: AT RISK (CPU and memory under Oracle's 20% idle line)"}.get(
        r["reclaim"], "Reclaim risk: unavailable")
    services = "Services: " + " · ".join(f"{label} {'✓' if r['services'][k] else '✗' if r['services'][k] is False else '?'}" for k, label in SERVICES)
    chatgpt = ([("Codex plan: unavailable", None)] if plan is None else [("ChatGPT plan: not reported by Sign in with ChatGPT", "gray")] if plan == UNREPORTED else
               _gauge("Codex 5h", plan["left_5h"]) + sep + _gauge("Codex week", plan["left_week"]))
    segments = ([(f"{first} · {cpu}\n\n", None)] + _gauge("Memory", r["memory_pct"]) + sep + _gauge("Disk", r["disk_pct"])
                + [(f"\n\n{cores} · {reclaim}\n{services}\n\n", None)] + chatgpt)
    ct = now.astimezone(TZ)
    stamp = f"Updated {ct.strftime('%b')} {ct.day} · {ct.strftime('%-I:%M %p')} CT"
    return segments, f"{TITLE} ({'DEGRADED · ' if missing(r) else ''}{stamp})"


def text_of(segments):
    return "".join(text for text, _ in segments)


def items_of(segments):
    """Segments -> Notion rich text, a color annotation on the colored pieces only."""
    out = []
    for text, color in segments:
        out += [{**i, "annotations": {"color": color}} if color else i for i in rich_text(text)]
    return out


def due(heading_text, now, force=False):
    """Once per Chicago day from DUE_HOUR; again while the day's card is DEGRADED."""
    if force:
        return True
    ct = now.astimezone(TZ)
    if ct.hour < DUE_HOUR:
        return False
    return not (f"Updated {ct.strftime('%b')} {ct.day} · " in heading_text and "DEGRADED" not in heading_text)


def _children(client, block_id):
    return report_region.children(client, block_id, _fail)


def locate(client, anchor):
    """-> (heading block, paragraph block): exactly one Oracle Cloud callout beside the Attention callout, holding its heading and exactly one paragraph."""
    meta = client.call("GET", f"/blocks/{quote(report_region.clean_id(anchor), safe='')}")
    parent = (meta.get("parent") if isinstance(meta, dict) else None) or {}
    if parent.get("type") != "block_id":
        raise _fail("CARD_NOT_OWNED")
    found = []
    for block in _children(client, parent["block_id"]):
        if block.get("type") == "callout":
            kids = _children(client, block["id"])
            if kids and kids[0].get("type") in report_region.HEADINGS and report_region.titled(report_region.plain(kids[0]), TITLE):
                found.append(kids)
    if len(found) != 1 or len(found[0]) != 2 or found[0][1].get("type") != "paragraph":
        raise _fail("CARD_NOT_OWNED")
    return found[0][0], found[0][1]


def _patch(client, block, items):
    client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {block["type"]: {"rich_text": items}})


def write(client, heading, paragraph, segments, heading_text):
    """The paragraph, then the heading (keeping the heading's own formatting, e.g. Jim's bold); both read back."""
    body = text_of(segments)
    _patch(client, paragraph, items_of(segments))
    first = ((heading.get(heading["type"]) or {}).get("rich_text") or [{}])[0]
    items = rich_text(heading_text)
    if isinstance(first.get("annotations"), dict):
        items = [{**i, "annotations": first["annotations"]} for i in items]
    _patch(client, heading, items)
    for block, want in ((paragraph, body), (heading, heading_text)):
        if report_region.plain(client.call("GET", f"/blocks/{quote(block['id'], safe='')}")) != want:
            raise _fail("ORACLE_READBACK_MISMATCH")


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, now=None, fetcher=fetch, client=None):
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    anchor = (environ.get("ATTENTION_CARD_BLOCK_ID") or "").strip()
    if not host or not anchor:
        raise _fail("ORACLE_NOT_CONFIGURED")
    router.check_write(MODULE, [TITLE])
    now = now or datetime.now(timezone.utc)
    client = client or _writer(environ)
    heading, paragraph = locate(client, anchor)
    if not due(report_region.plain(heading), now, (environ.get("ORACLE_CARD_FORCE") or "").strip() == "true"):
        return {"due": False, "written": 0}
    try:
        raw = fetcher(host)
    except OSError:
        raw = b""
    try:
        readings = validate(raw, now)
    except OracleCardError as e:                                  # a source we cannot trust is shown as unavailable, with this run's time
        print("oracle-card: source refused", json.dumps({"code": str(e)}))
        readings = UNAVAILABLE
    segments, heading_text = render(readings, now)
    counts = {"due": True, "missing": missing(readings), "reclaim": (readings["reclaim"] or "unavailable").lower(),
              "services_down": sum(v is False for v in readings["services"].values()), "written": 0}
    if not live:
        return counts
    protected_ids = {region: (environ.get(var) or "").strip() for var, region in PROTECTED}
    before = report_region.protected(client, protected_ids, _fail)
    try:
        write(client, heading, paragraph, segments, heading_text)
    except NotionError:
        raise _fail("ORACLE_WRITE_FAILED") from None
    if not router.protected_intact(MODULE, before, report_region.protected(client, protected_ids, _fail)):
        raise _fail("PROTECTED_REGION_CHANGED")
    counts["written"] = 2
    return counts
