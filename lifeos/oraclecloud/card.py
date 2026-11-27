"""The Daily Report's System callout, written every tick: its heading's stamp and the cells of its one table.

Flow: tick -> `imessage` job -> Oracle's private endpoint (`oracle1`, and `limits` for Codex) ->
lifeos.sources.cards.oracle_card -> this module -> Notion. This module owns the Oracle contract (`fetch`,
`validate`) and the writing: only the heading text and the table's row cells are written, in place, each read
back. Rows come from lifeos.oraclecloud.system_table."""
import json
import os
import re
import socket
from datetime import datetime, timezone
from urllib.parse import quote

from lifeos.oraclecloud import backup_watch, boot_backup_watch, worker_watch
from lifeos.platform import honcho_status, report_region, router
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError, rich_text

TITLE = router.ORACLE_REGION
MODULE = router.OWNERS[TITLE]
PORT = 47341
SCHEMA = "lifeos.oracle.stats.v1"
MAX_REPLY = 4096
WAIT = 100
REPLY_TTL_SECONDS = 600
KEYS = {"schema", "observed_at", "cost", "cpu_24h_pct", "memory_pct", "disk_pct", "a1_cores", "reclaim", "services", "chatgpt"}
# optional so the card and Oracle's stats.mjs can be updated in either order
OPTIONAL = {"backup", "workers", "backups", "honcho", "anthropic"}
CODE = re.compile(r"[a-z0-9_]{1,40}")
SERVICES = (("gateway", "Gateway"), ("telegram", "Telegram"), ("whatsapp", "WhatsApp"), ("alerts", "Alerts"))
# no plan windows under Sign in with ChatGPT: a known state, never counted as missing
UNREPORTED = "unreported"
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION))
UNAVAILABLE = {"cost": None, "cpu_24h_pct": None, "memory_pct": None, "disk_pct": None, "a1_cores": None, "reclaim": None,
               "services": {k: None for k, _ in SERVICES}, "chatgpt": None, "backup": None, "workers": None, "backups": None, "honcho": None, "anthropic": None, "honcho_reported": False}


class OracleCardError(Exception):
    pass


_fail = OracleCardError


def fetch(host, port=PORT, request=b"oracle1\n"):
    data = b""
    with socket.create_connection((host, port), timeout=WAIT) as s:
        s.sendall(request)
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


def _backup_ok(b):
    return b is None or (isinstance(b, dict) and set(b) == {"ok", "code", "age_h"} and type(b["ok"]) is bool and isinstance(b["code"], str)
                         and CODE.fullmatch(b["code"]) is not None and (b["age_h"] is None or _whole(b["age_h"])))


def _workers_ok(w):
    return w is None or (isinstance(w, dict) and set(w) == {"count", "agents"} and _whole(w["count"]) and _whole(w["agents"]) and w["agents"] >= 1 and w["count"] <= 1000)


def _backups_ok(b):
    return b is None or (isinstance(b, dict) and set(b) == {"used", "available"} and _whole(b["used"]) and _whole(b["available"]) and b["used"] + b["available"] <= 1000000)


def validate(raw, now):
    if not raw or len(raw) > MAX_REPLY:
        raise _fail("ORACLE_UNREACHABLE")
    with fixed((ValueError, TypeError, KeyError, AttributeError), _fail, "ORACLE_OFF_CONTRACT"):
        p = json.loads(raw)
        seen = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
    cost, a1, svc, plan = p.get("cost"), p.get("a1_cores"), p.get("services"), p.get("chatgpt")
    if (not KEYS <= set(p) <= KEYS | OPTIONAL or p["schema"] != SCHEMA or not all(_pct(p[k]) for k in ("cpu_24h_pct", "memory_pct", "disk_pct"))
            or not (cost is None or (isinstance(cost, dict) and set(cost) == {"amount", "currency"} and type(cost["amount"]) in (int, float)
                                     and cost["amount"] >= 0 and isinstance(cost["currency"], str) and len(cost["currency"]) == 3))
            or not (a1 is None or (isinstance(a1, dict) and set(a1) == {"used", "available"} and _whole(a1["used"]) and _whole(a1["available"])))
            or p["reclaim"] not in (None, "NONE", "AT_RISK")
            or not (isinstance(svc, dict) and set(svc) == {k for k, _ in SERVICES} and all(v in (None, True, False) for v in svc.values()))
            or not _backup_ok(p.get("backup")) or not _workers_ok(p.get("workers")) or not _backups_ok(p.get("backups"))
            or not honcho_status.honcho_ok(p.get("honcho")) or not honcho_status.anthropic_ok(p.get("anthropic"))
            or not (plan in (None, UNREPORTED) or (isinstance(plan, dict) and set(plan) == {"left_5h", "left_week"} and all(_whole(v) and v <= 100 for v in plan.values())))):
        raise _fail("ORACLE_OFF_CONTRACT")
    if seen.tzinfo is None or abs((now - seen).total_seconds()) > REPLY_TTL_SECONDS:
        raise _fail("ORACLE_STALE")
    return {**{k: p[k] for k in KEYS - {"schema", "observed_at"}}, "backup": p.get("backup"), "workers": p.get("workers"), "backups": p.get("backups"),
            "honcho": p.get("honcho"), "anthropic": p.get("anthropic"), "honcho_reported": "honcho" in p}


def items_of(segments):
    """Segments [(text, colour)] -> Notion rich text; `bold` marks the header, any other colour is an annotation."""
    out = []
    for text, color in segments:
        extra = {"annotations": {"bold": True}} if color == "bold" else {"annotations": {"color": color}} if color else {}
        out += [{**i, **extra} for i in rich_text(text)]
    return out


def text_of(segments):
    return "".join(text for text, _ in segments)


COLS, ROWS = 5, 17


def _parent_of(client, anchor):
    meta = client.call("GET", f"/blocks/{quote(report_region.clean_id(anchor), safe='')}")
    return report_region.page_of(client, meta, _fail)


def _cell(rich):
    out = []
    for item in rich:
        ann = item.get("annotations") or {}
        color = "bold" if ann.get("bold") else None if ann.get("color") in (None, "default") else ann["color"]
        piece = item.get("plain_text") or (item.get("text") or {}).get("content") or ""
        out = out[:-1] + [(out[-1][0] + piece, color)] if out and out[-1][1] == color else out + [(piece, color)]
    return [p for p in out if p[0]]


def cells_of(row):
    return [_cell(cell) for cell in (row.get("table_row") or {}).get("cells") or []]


def locate(client, anchor):
    """-> (heading block, table block, [row blocks]) of the one top-level callout holding the System heading and a
    5x17 table. Any other shape is CARD_NOT_OWNED and nothing is written."""
    page = _parent_of(client, anchor)
    if not page:
        raise _fail("CARD_NOT_OWNED")
    found = []
    for block in report_region.children(client, page, _fail):
        if block.get("type") == "callout":
            kids = report_region.children(client, block["id"], _fail)
            if kids and kids[0].get("type") in report_region.HEADINGS and report_region.titled(report_region.plain(kids[0]), TITLE):
                found.append(kids)
    if len(found) != 1:
        raise _fail("CARD_NOT_OWNED")
    tables = [b for b in found[0] if b.get("type") == "table"]
    if len(tables) != 1 or (tables[0].get("table") or {}).get("table_width") != COLS:
        raise _fail("CARD_NOT_OWNED")
    rows = report_region.children(client, tables[0]["id"], _fail)
    if len(rows) != ROWS or any(len(cells_of(r)) != COLS for r in rows):
        raise _fail("CARD_NOT_OWNED")
    return found[0][0], tables[0], rows


def table_of(rows):
    return [cells_of(r) for r in rows]


def write(client, heading, rows, table, heading_text, live):
    """In place only: rows and heading whose text differs, then read back."""
    changed = [(r["id"], want) for r, want in zip(rows, table) if cells_of(r) != [_cell(items_of(c)) for c in want]]
    heading_changed = report_region.plain(heading) != heading_text
    result = {"rows_changed": len(changed), "rows_written": 0, "heading_written": False}
    if not live or not (changed or heading_changed):
        return result
    for row_id, want in changed:
        client.call_once("PATCH", f"/blocks/{quote(row_id, safe='')}", {"table_row": {"cells": [items_of(c) for c in want]}})
        result["rows_written"] += 1
    if heading_changed:
        first = ((heading.get(heading["type"]) or {}).get("rich_text") or [{}])[0]
        items = rich_text(heading_text)
        if isinstance(first.get("annotations"), dict):
            items = [{**i, "annotations": first["annotations"]} for i in items]
        client.call_once("PATCH", f"/blocks/{quote(heading['id'], safe='')}", {heading["type"]: {"rich_text": items}})
        result["heading_written"] = True
    for row_id, want in changed:
        after = client.call("GET", f"/blocks/{quote(row_id, safe='')}")
        if cells_of(after) != [_cell(items_of(c)) for c in want]:
            raise _fail("ORACLE_READBACK_MISMATCH")
    if heading_changed and report_region.plain(client.call("GET", f"/blocks/{quote(heading['id'], safe='')}")) != heading_text:
        raise _fail("ORACLE_READBACK_MISMATCH")
    return result


def heading_text_of(heading):
    return report_region.plain(heading)


def scoped(environ, client, heading, rows):
    report_region.require_configured({region: (environ.get(var) or "").strip() for var, region in PROTECTED}, _fail)
    return report_region.ScopedClient(client, heading["id"], _fail).adopt(*(r["id"] for r in rows))


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def stats_of(host, fetcher, now):
    """-> the validated `oracle1` readings, or None when Oracle cannot be trusted (rows then go stale, never zero)."""
    try:
        raw = fetcher(host)
    except OSError:
        raw = b""
    try:
        return validate(raw, now)
    except OracleCardError as e:
        print("oracle-card: source refused", json.dumps({"code": str(e)}))
        return None
