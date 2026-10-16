"""LIFE-34 (COMM-1.7): the Messages row of the Daily Report's Communications Attention callout, every hour (Jim's approval, Oct 15 2026).

hourly tick -> domains.yml `imessage` job (already on the tailnet as tag:v7-runner) -> Oracle's private endpoint, request `comms <one-run key>` -> this stage
-> Notion. The answer is the Mac reader's unread count for the last 7 days and the newest incoming sender's display name SCRAMBLED under a key this run
generated and sent. The name is unscrambled only in memory, immediately before the Notion write, and is never printed, logged, put in the database or kept:
Jim's condition is that his Notion is the only place it is stored. The count is the reader's own 7-day window (Mac read flags), not the iPhone badge.

Live, it changes exactly three things, in order: the text of the Messages launch link (`Messages (N)`, `(—)` when the source is not CURRENT), the
right-column line on the same row (`Latest: <name>`, `Latest: none in 3 days` or `Latest: unavailable`), then the heading's update time. Everything else in
the callout (Jim's custom icons, the other channels, the launch links) is sent back exactly as read; the row is read back and compared, and every other
V7 region is proven unchanged. The callout is found beside the Attention callout (same column, heading `Communications Attention`), so it needs no new
configuration. Counts and fixed codes only in the log.

LIFE-29 (Jim's approval Oct 15 2026, Jira 10203/10206): the request is `comms2`, whose answer adds Jim's personal Telegram from the TDLib adapter on Oracle.
The Telegram row is written the same way: `Telegram (N)` where N is unread messages in unmuted chats of the Main list (what Telegram's own badge shows
by default; Archive excluded), and `Latest: <name>` of the newest message from another person (scrambled in transit like the iMessage name)."""
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
SCHEMA = "lifeos.imessage.comms.v3"
TG_COUNTS = ("chats_main", "unread_messages", "unread_messages_unmuted", "unread_chats", "unread_chats_unmuted")
TG_KEYS = {"health", "auth", "error", "observed_at", "latest_state", "latest", *TG_COUNTS}
MAX_REPLY = 8192
WAIT = 100                                                             # seconds: the endpoint may spend up to 90 (Telegram start-up included)
REPLY_TTL_SECONDS = 600                                               # seconds: an older observation is refused, never shown as current
LAUNCHES = {"messages": "/launch/messages/", "telegram": "/launch/telegram/", "whatsapp": "/launch/whatsapp/"}
LABELS = {"messages": "Messages", "telegram": "Telegram", "whatsapp": "WhatsApp"}
CHANNEL_BLOCKS = ("telegram", "whatsapp")
STATES = ("PRESENT", "NONE", "UNAVAILABLE")
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("ATTENTION_CARD_BLOCK_ID", router.ATTENTION_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION))


class CommsError(Exception):
    pass


def _fail(code):
    return CommsError(code)


def fetch(host, key, port=PORT):
    data = b""
    with socket.create_connection((host, port), timeout=WAIT) as s:
        s.sendall(b"comms3 " + key.hex().encode() + b"\n")
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


def _count(n):
    return n is None or (type(n) is int and n >= 0)


def _block_ok(b):
    return (isinstance(b, dict) and set(b) == TG_KEYS and b["health"] in ("CURRENT", "DEGRADED", "UNAVAILABLE") and b["latest_state"] in STATES
            and all(_count(b[k]) for k in TG_COUNTS) and (b["latest_state"] == "PRESENT") == isinstance(b["latest"], dict)
            and (b["health"] == "CURRENT" or (b["latest_state"] == "UNAVAILABLE" and all(b[k] is None for k in TG_COUNTS))))


def validate(raw, key, now):
    """-> ({"messages" | "telegram" | "whatsapp": (count, state, name)}, {channel: detail for the log}). Anything off-contract, stale or tampered raises;
    it is never partly used."""
    if not raw or len(raw) > MAX_REPLY:
        raise _fail("COMMS_UNREACHABLE")
    try:
        p = json.loads(raw)
        seen = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _fail("COMMS_OFF_CONTRACT") from None
    unread, state = p.get("unread_7d"), p.get("latest_state")
    if (set(p) != {"schema", "observed_at", "health", "unread_7d", "latest_state", "latest", *CHANNEL_BLOCKS} or p["schema"] != SCHEMA
            or p["health"] not in ("CURRENT", "DEGRADED") or state not in STATES or not _count(unread)
            or (state == "PRESENT") != isinstance(p["latest"], dict) or not all(_block_ok(p[c]) for c in CHANNEL_BLOCKS)):
        raise _fail("COMMS_OFF_CONTRACT")
    if seen.tzinfo is None or abs((now - seen).total_seconds()) > REPLY_TTL_SECONDS:
        raise _fail("COMMS_STALE")
    rows = {"messages": (unread, state, unscramble(key, p["latest"]) if state == "PRESENT" else None)}
    detail = {}
    for c in CHANNEL_BLOCKS:
        b = p[c]
        rows[c] = (b["unread_messages_unmuted"], b["latest_state"], unscramble(key, b["latest"]) if b["latest_state"] == "PRESENT" else None)
        detail[c] = {k: b[k] for k in ("health", "auth", "error", *TG_COUNTS)}
    return rows, detail


NONE_LINE = {"messages": "Latest: none in 3 days", "telegram": "Latest: none", "whatsapp": "Latest: none"}


KEEP_LAST = ("whatsapp",)   # a CURRENT WhatsApp source that has seen no message since it restarted keeps Notion's last name (names are never on disk)


def render(rows, now):
    """{channel: (count, state, name)} -> ({channel: (link text, latest line or None = keep the line already in Notion)}, heading text)."""
    out = {}
    for channel, (n, state, name) in rows.items():
        count = "—" if n is None else ("99+" if n > 99 else str(n))
        latest = {"PRESENT": f"Latest: {name}", "NONE": NONE_LINE[channel]}.get(state, "Latest: unavailable")
        if channel in KEEP_LAST and n is not None and state == "UNAVAILABLE":
            latest = None
        out[channel] = (f"{LABELS[channel]} ({count})", latest)
    return out, f"{TITLE} (Updated {now.astimezone(TZ).strftime('%-I:%M %p')} CT)"


UNAVAILABLE = {c: (None, "UNAVAILABLE", None) for c in ("messages", *CHANNEL_BLOCKS)}


def _children(client, block_id):
    return report_region.children(client, block_id, _fail)


def locate(client, anchor):
    """-> (heading block, {channel: (left paragraph, its right-column paragraph)}). Exactly one Communications callout beside the Attention callout, two
    columns, and for each channel exactly one row whose link opens its launch page with a paragraph at the same position on the right; else CARD_NOT_OWNED."""
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
    lists = []                                                     # the channel rows: the one column list whose left column holds a launch link; Jim's other rows (Reminders | Todoist) are never read or written
    for block in rest:
        if block.get("type") == "column_list":
            columns = _children(client, block["id"])
            first = _children(client, columns[0]["id"]) if columns else []
            if any(_link_item(b, channel) is not None for b in first for channel in LAUNCHES):
                lists.append(columns)
    if len(lists) != 1 or len(lists[0]) != 2:
        raise _fail("CARD_NOT_OWNED")
    left, right = (_children(client, c["id"]) for c in lists[0])
    found = {}
    for channel in LAUNCHES:
        rows = [i for i, b in enumerate(left) if _link_item(b, channel) is not None]
        if len(rows) != 1 or rows[0] >= len(right) or right[rows[0]].get("type") != "paragraph":
            raise _fail("CARD_NOT_OWNED")
        found[channel] = (left[rows[0]], right[rows[0]])
    return heading, found


def _items(block):
    return ((block.get(block.get("type")) or {}).get("rich_text") or []) if isinstance(block, dict) else []


def _link_item(block, channel):
    if not isinstance(block, dict) or block.get("type") != "paragraph":
        return None
    hits = [i for i, item in enumerate(_items(block))
            if item.get("type") == "text" and LAUNCHES[channel] in str(((item.get("text") or {}).get("link") or {}).get("url") or "")]
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


def write(client, heading, rows, lines, heading_text):
    """rows: {channel: (row, latest block)}; lines: {channel: (link text, latest line)}. Every row, then the heading; then every row is read back."""
    expected = {}
    for channel, (row, latest_block) in rows.items():
        link_text, latest_line = lines[channel]
        before, at = _items(row), _link_item(row, channel)
        items = [_sendable(i) for i in before]
        items[at]["text"]["content"] = link_text
        _patch_text(client, row, items)
        if latest_line is None:
            latest_line = report_region.plain(latest_block)
        else:
            _patch_text(client, latest_block, rich_text(latest_line))
        expect = _plain(before)
        expect[at] = link_text
        expected[channel] = (expect, latest_line)
    _patch_text(client, heading, rich_text(heading_text))
    for channel, (row, latest_block) in rows.items():
        after_row = _items(client.call("GET", f"/blocks/{quote(row['id'], safe='')}"))
        after_latest = report_region.plain(client.call("GET", f"/blocks/{quote(latest_block['id'], safe='')}"))
        if _plain(after_row) != expected[channel][0] or after_latest != expected[channel][1]:
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
    detail = {c: {"health": "UNAVAILABLE", "auth": None, "error": None, **{k: None for k in TG_COUNTS}} for c in CHANNEL_BLOCKS}
    try:
        rows, detail = validate(raw, key, now)
    except CommsError as e:                                    # a source we cannot trust is shown as unavailable, with this run's time
        print("imessage-comms: source refused", json.dumps({"code": str(e)}))
        rows = UNAVAILABLE
    lines, heading_text = render(rows, now)
    client = client or _writer(environ)
    heading, blocks = locate(client, anchor)
    counts = {"unread_7d": rows["messages"][0], "latest": rows["messages"][1].lower(),
              **{f"{c}_latest": rows[c][1].lower() for c in CHANNEL_BLOCKS},
              **{f"{c}_{k}": v for c in CHANNEL_BLOCKS for k, v in detail[c].items()}, "written": 0}
    if not live:
        return counts
    protected_ids = {region: (environ.get(var) or "").strip() for var, region in PROTECTED}
    before = report_region.protected(client, protected_ids, _fail)
    try:
        write(client, heading, blocks, lines, heading_text)
    except NotionError:
        raise _fail("COMMS_WRITE_FAILED") from None
    if not router.protected_intact(MODULE, before, report_region.protected(client, protected_ids, _fail)):
        raise _fail("PROTECTED_REGION_CHANGED")
    counts["written"] = 2 * len(blocks) + 1
    return counts
