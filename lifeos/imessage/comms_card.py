"""LIFE-34 (COMM-1.7): the Messages row of the Daily Report's Communications Attention callout, every hour (Jim's approval, Oct 15 2026).

hourly tick -> domains.yml `imessage` job (already on the tailnet as tag:v7-runner) -> Oracle's private endpoint, request `comms <one-run key>` -> this stage
-> Notion. The answer is the Mac reader's unread count for the last 7 days and the newest incoming sender's display name SCRAMBLED under a key this run
generated and sent. The name is unscrambled only in memory, immediately before the Notion write, and is never printed, logged, put in the database or kept:
Jim's condition is that his Notion is the only place it is stored. The count is the reader's own 7-day window (Mac read flags), not the iPhone badge.

Live, it changes exactly three things, in order: the text of the Messages launch link (`Messages (N)`, `(—)` when the source is not CURRENT), the
right-column line on the same row (`Latest: <name>`, `Latest: none in 3 days` or `Latest: unavailable`), then the heading's update time. Everything else in
the callout (Jim's custom icons, the other channels, the launch links) is sent back exactly as read; the row is read back and compared, and every other
V7 region is proven unchanged. The callout is found beside the Attention callout (same column, heading `Communications Attention`), so it needs no new
configuration. Counts and fixed codes only in the log."""
import hashlib
import hmac
import json
import os
import socket
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.platform import report_region, router
from lifeos.platform.notion_client import Client, NotionError, rich_text

TZ = ZoneInfo("America/Chicago")
TITLE = router.COMMS_REGION
MODULE = router.OWNERS[TITLE]
PORT = 47341
SCHEMA = "lifeos.imessage.comms.v1"
MAX_REPLY = 4096
REPLY_TTL_SECONDS = 600                                               # seconds: an older observation is refused, never shown as current
LAUNCH = "/launch/messages/"
STATES = ("PRESENT", "NONE", "UNAVAILABLE")
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION))


class CommsError(Exception):
    pass


def _fail(code):
    return CommsError(code)


def fetch(host, key, port=PORT):
    data = b""
    with socket.create_connection((host, port), timeout=30) as s:
        s.sendall(b"comms " + key.hex().encode() + b"\n")
        while len(data) <= MAX_REPLY:
            chunk = s.recv(MAX_REPLY + 1 - len(data))
            if not chunk:
                break
            data += chunk
    return data


def _keystream(key, nonce, n):
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, b"lifeos-comms-ks" + nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def unscramble(key, sealed):
    try:
        nonce, box, tag = (bytes.fromhex(sealed[k]) for k in ("nonce", "box", "tag"))
    except (KeyError, TypeError, ValueError):
        raise _fail("COMMS_OFF_CONTRACT") from None
    want = hmac.new(key, b"lifeos-comms-tag" + nonce + box, hashlib.sha256).digest()[:16]
    if len(nonce) != 16 or not hmac.compare_digest(want, tag):
        raise _fail("COMMS_SCRAMBLE_INVALID")
    try:
        return bytes(a ^ b for a, b in zip(box, _keystream(key, nonce, len(box)))).decode("utf-8")
    except UnicodeDecodeError:
        raise _fail("COMMS_SCRAMBLE_INVALID") from None


def validate(raw, key, now):
    """-> (unread or None, state, name or None). Anything off-contract, stale or tampered raises; it is never partly used."""
    if not raw or len(raw) > MAX_REPLY:
        raise _fail("COMMS_UNREACHABLE")
    try:
        p = json.loads(raw)
        seen = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _fail("COMMS_OFF_CONTRACT") from None
    unread, state = p.get("unread_7d"), p.get("latest_state")
    if (set(p) != {"schema", "observed_at", "health", "unread_7d", "latest_state", "latest"} or p["schema"] != SCHEMA
            or p["health"] not in ("CURRENT", "DEGRADED") or state not in STATES
            or not (unread is None or (type(unread) is int and unread >= 0))
            or (state == "PRESENT") != isinstance(p["latest"], dict)):
        raise _fail("COMMS_OFF_CONTRACT")
    if seen.tzinfo is None or abs((now - seen).total_seconds()) > REPLY_TTL_SECONDS:
        raise _fail("COMMS_STALE")
    return unread, state, unscramble(key, p["latest"]) if state == "PRESENT" else None


def render(unread, state, name, now):
    count = "—" if unread is None else ("99+" if unread > 99 else str(unread))
    latest = {"PRESENT": f"Latest: {name}", "NONE": "Latest: none in 3 days"}.get(state, "Latest: unavailable")
    return f"Messages ({count})", latest, f"{TITLE} (Updated {now.astimezone(TZ).strftime('%-I:%M %p')} CT)"


def _children(client, block_id):
    return report_region.children(client, block_id, _fail)


def locate(client, anchor):
    """-> (heading block, Messages paragraph, its right-column paragraph). Exactly one Communications callout beside the Attention callout, two columns,
    one row whose link opens the Messages launch page, and a paragraph at the same position on the right; anything else is CARD_NOT_OWNED."""
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
    if len(found) != 1:
        raise _fail("CARD_NOT_OWNED")
    heading, rest = found[0][0], found[0][1:]
    lists = [b for b in rest if b.get("type") == "column_list"]
    if len(lists) != 1:
        raise _fail("CARD_NOT_OWNED")
    columns = _children(client, lists[0]["id"])
    if len(columns) != 2:
        raise _fail("CARD_NOT_OWNED")
    left, right = (_children(client, c["id"]) for c in columns)
    rows = [i for i, b in enumerate(left) if _link_item(b) is not None]
    if len(rows) != 1 or rows[0] >= len(right) or right[rows[0]].get("type") != "paragraph":
        raise _fail("CARD_NOT_OWNED")
    return heading, left[rows[0]], right[rows[0]]


def _items(block):
    return ((block.get(block.get("type")) or {}).get("rich_text") or []) if isinstance(block, dict) else []


def _link_item(block):
    if not isinstance(block, dict) or block.get("type") != "paragraph":
        return None
    hits = [i for i, item in enumerate(_items(block))
            if item.get("type") == "text" and LAUNCH in str(((item.get("text") or {}).get("link") or {}).get("url") or "")]
    return hits[0] if len(hits) == 1 else None


def _sendable(item):
    """A rich-text item as read -> the same item as Notion accepts it in a write (custom icons by id), or CARD_NOT_OWNED for a kind it cannot round-trip."""
    kind, out = item.get("type"), {}
    if kind == "text":
        text = item.get("text") or {}
        out = {"type": "text", "text": {"content": text.get("content", ""), **({"link": text["link"]} if text.get("link") else {})}}
    elif kind == "mention" and (item.get("mention") or {}).get("type") == "custom_emoji":
        out = {"type": "mention", "mention": {"type": "custom_emoji", "custom_emoji": {"id": item["mention"]["custom_emoji"]["id"]}}}
    elif kind == "mention":
        sub = item["mention"]["type"]
        out = {"type": "mention", "mention": {"type": sub, sub: item["mention"][sub]}}
    else:
        raise _fail("CARD_NOT_OWNED")
    if isinstance(item.get("annotations"), dict):
        out["annotations"] = item["annotations"]
    return out


def _patch_text(client, block, items):
    kind = block["type"]
    client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {kind: {"rich_text": items}})


def _plain(items):
    return [i.get("plain_text", "") for i in items]


def write(client, heading, row, latest_block, link_text, latest_line, heading_text):
    before = _items(row)
    at = _link_item(row)
    items = [_sendable(i) for i in before]
    items[at]["text"]["content"] = link_text
    _patch_text(client, row, items)
    _patch_text(client, latest_block, rich_text(latest_line))
    _patch_text(client, heading, rich_text(heading_text))
    after_row = _items(client.call("GET", f"/blocks/{quote(row['id'], safe='')}"))
    after_latest = report_region.plain(client.call("GET", f"/blocks/{quote(latest_block['id'], safe='')}"))
    expect = _plain(before)
    expect[at] = link_text
    if _plain(after_row) != expect or after_latest != latest_line:
        raise _fail("COMMS_READBACK_MISMATCH")


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run(limit, live, environ=os.environ, now=None, fetcher=fetch, client=None, keygen=os.urandom):
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    anchor = (environ.get("ATTENTION_CARD_BLOCK_ID") or "").strip()
    if not host or not anchor:
        raise _fail("COMMS_NOT_CONFIGURED")
    router.check_write(MODULE, [TITLE])
    key = keygen(32)
    try:
        raw = fetcher(host, key)
    except OSError:
        raw = b""
    now = now or datetime.now(timezone.utc)
    try:
        unread, state, name = validate(raw, key, now)
    except CommsError as e:                                    # a source we cannot trust is shown as unavailable, with this run's time
        print("imessage-comms: source refused", json.dumps({"code": str(e)}))
        unread, state, name = None, "UNAVAILABLE", None
    link_text, latest_line, heading_text = render(unread, state, name, now)
    client = client or _writer(environ)
    heading, row, latest_block = locate(client, anchor)
    counts = {"unread_7d": unread, "latest": state.lower(), "written": 0}
    if not live:
        return counts
    protected_ids = {region: (environ.get(var) or "").strip() for var, region in PROTECTED}
    before = report_region.protected(client, protected_ids, _fail)
    try:
        write(client, heading, row, latest_block, link_text, latest_line, heading_text)
    except NotionError:
        raise _fail("COMMS_WRITE_FAILED") from None
    if not router.protected_intact(MODULE, before, report_region.protected(client, protected_ids, _fail)):
        raise _fail("PROTECTED_REGION_CHANGED")
    counts["written"] = 3
    return counts
