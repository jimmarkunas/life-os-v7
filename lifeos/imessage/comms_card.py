"""The Messages and Telegram rows of the Daily Report's Communications Attention callout, written hourly.

Input from Oracle's `comms2` request: unread counts and the newest sender's name (scrambled under a key this run
generated). It writes the link text `Messages (N)` (`(—)` when the source is not CURRENT), the row's `Latest: <name>`
line and the heading time, then Telegram the same way; everything else in the callout is sent back as read and every
write is read back. Names are unscrambled only in memory just before the Notion write and never printed, logged or
stored elsewhere."""
import hashlib
import hmac
import json
import os
import socket
from datetime import datetime, timezone
from urllib.parse import quote

from lifeos.imessage.alerts_endpoint import TG_COUNTS, _keystream      # the Oracle endpoint file is stdlib-only, so the runner imports from it and never the other way
from lifeos.platform import ct_stamp, report_region, router
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError, rich_text

TITLE = router.COMMS_REGION
MODULE = router.OWNERS[TITLE]
PORT = 47341
SCHEMA = "lifeos.imessage.comms.v3"
TG_KEYS = {"health", "auth", "error", "observed_at", "latest_state", "latest", *TG_COUNTS}
MAX_REPLY = 8192
WAIT = 100                                                             # seconds: the endpoint may spend up to 90 (Telegram start-up included)
REPLY_TTL_SECONDS = 600
LAUNCHES = {"messages": "/launch/messages/", "telegram": "/launch/telegram/", "whatsapp": "/launch/whatsapp/"}
LABELS = {"messages": "Messages", "telegram": "Telegram", "whatsapp": "WhatsApp"}
TODOIST_LAUNCH = "/launch/todoist/"
TODOIST_LIMIT = 99
CHANNEL_BLOCKS = ("telegram", "whatsapp")
STATES = ("PRESENT", "NONE", "UNAVAILABLE")
PROTECTED = (("JIRA_CARD_BLOCK_ID", router.JIRA_REGION), ("CALENDAR_CARD_BLOCK_ID", router.CALENDAR_REGION), ("BILLS_CARD_BLOCK_ID", router.BILLS_REGION),
             ("AMAZON_CARD_BLOCK_ID", router.AMAZON_REGION), ("HIRING_CARD_BLOCK_ID", router.HIRING_REGION))


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


def unscramble(key, sealed):
    with fixed((KeyError, TypeError, ValueError), _fail, "COMMS_OFF_CONTRACT"):
        nonce, box, tag = (bytes.fromhex(sealed[k]) for k in ("nonce", "box", "tag"))
    want = hmac.new(key, b"lifeos-comms-tag" + nonce + box, hashlib.sha256).digest()[:16]
    if len(nonce) != 16 or not hmac.compare_digest(want, tag):
        raise _fail("COMMS_SCRAMBLE_INVALID")
    with fixed(UnicodeDecodeError, _fail, "COMMS_SCRAMBLE_INVALID"):
        return bytes(a ^ b for a, b in zip(box, _keystream(key, nonce, len(box)))).decode("utf-8")


def _count(n):
    return n is None or (type(n) is int and n >= 0)


def _block_ok(b):
    return (isinstance(b, dict) and set(b) == TG_KEYS and b["health"] in ("CURRENT", "DEGRADED", "UNAVAILABLE") and b["latest_state"] in STATES
            and all(_count(b[k]) for k in TG_COUNTS) and (b["latest_state"] == "PRESENT") == isinstance(b["latest"], dict)
            and (b["health"] == "CURRENT" or (b["latest_state"] == "UNAVAILABLE" and all(b[k] is None for k in TG_COUNTS))))


def validate(raw, key, now):
    """-> ({"messages" | "telegram" | "whatsapp": (count, state, name)}, {channel: detail for the log}).

    Anything off-contract, stale or tampered raises; it is never partly used."""
    if not raw or len(raw) > MAX_REPLY:
        raise _fail("COMMS_UNREACHABLE")
    with fixed((ValueError, TypeError, KeyError, AttributeError), _fail, "COMMS_OFF_CONTRACT"):
        p = json.loads(raw)
        seen = datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00"))
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


KEEP_LAST = ("whatsapp",)   # a CURRENT WhatsApp source with no message since restart keeps Notion's last name (names are never on disk)


def render(rows, now):
    out = {}
    for channel, (n, state, name) in rows.items():
        count = "—" if n is None else ("99+" if n > 99 else str(n))
        latest = {"PRESENT": f"Latest: {name}", "NONE": NONE_LINE[channel]}.get(state, "Latest: unavailable")
        if channel in KEEP_LAST and n is not None and state == "UNAVAILABLE":
            latest = None
        out[channel] = (f"{LABELS[channel]} ({count})", latest)
    return out, f"{TITLE} ({ct_stamp.when(now)})"


UNAVAILABLE = {c: (None, "UNAVAILABLE", None) for c in ("messages", *CHANNEL_BLOCKS)}


def _children(client, block_id):
    return report_region.children(client, block_id, _fail)


def _callouts(client, parent_id, depth=0):
    found = []
    for block in _children(client, parent_id):
        if block.get("type") == "callout":
            kids = _children(client, block["id"])
            if kids and kids[0].get("type") in report_region.HEADINGS and report_region.titled(report_region.plain(kids[0]), TITLE):
                found.append(kids)
        elif block.get("type") in ("column_list", "column") and depth < 4:
            found += _callouts(client, block["id"], depth + 1)
    return found


def locate(client, anchor):
    """-> (heading block, {channel: (left paragraph, right-column paragraph)}, Todoist launcher paragraph or None).

    Exactly one Communications callout (found through the Calendar card in its column), two columns, and for each
    channel exactly one row whose link opens its launch page with a paragraph at the same position on the right; else
    CARD_NOT_OWNED. Two Todoist launcher paragraphs are ambiguous (CARD_NOT_OWNED)."""
    meta = client.call("GET", f"/blocks/{quote(report_region.clean_id(anchor), safe='')}")
    if not isinstance(meta, dict) or not meta.get("parent"):
        raise _fail("CARD_NOT_OWNED")
    page = report_region.page_of(client, meta, _fail)
    found = _callouts(client, page)
    if len(found) != 1:
        raise _fail("CARD_NOT_OWNED")
    heading, rest = found[0][0], found[0][1:]
    lists = []                                                     # the one column list whose left column holds a launch link; other rows are never read or written
    for block in rest:
        if block.get("type") == "column_list":
            columns = _children(client, block["id"])
            first = _children(client, columns[0]["id"]) if columns else []
            if any(_link_item(b, channel) is not None for b in first for channel in LAUNCHES):
                lists.append(columns)
    if len(lists) != 1 or len(lists[0]) != 2:
        raise _fail("CARD_NOT_OWNED")
    todoists = [b for block in rest if block.get("type") == "column_list" for column in _children(client, block["id"]) for b in _children(client, column["id"]) if _todoist_item(b) is not None]
    if len(todoists) > 1:
        raise _fail("CARD_NOT_OWNED")
    left, right = (_children(client, c["id"]) for c in lists[0])
    found = {}
    for channel in LAUNCHES:
        rows = [i for i, b in enumerate(left) if _link_item(b, channel) is not None]
        if len(rows) != 1 or rows[0] >= len(right) or right[rows[0]].get("type") != "paragraph":
            raise _fail("CARD_NOT_OWNED")
        found[channel] = (left[rows[0]], right[rows[0]])
    return heading, found, (todoists[0] if todoists else None)


def _items(block):
    return ((block.get(block.get("type")) or {}).get("rich_text") or []) if isinstance(block, dict) else []


def _launch_item(block, launch):
    if not isinstance(block, dict) or block.get("type") != "paragraph":
        return None
    hits = [i for i, item in enumerate(_items(block))
            if item.get("type") == "text" and launch in str(((item.get("text") or {}).get("link") or {}).get("url") or "")]
    return hits[0] if len(hits) == 1 else None


def _link_item(block, channel):
    return _launch_item(block, LAUNCHES[channel])


def _todoist_item(block):
    return _launch_item(block, TODOIST_LAUNCH)


def _sendable(item):
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


def write_todoist(client, block, label):
    before, at = _items(block), _todoist_item(block)
    items = [_sendable(i) for i in before]
    items[at]["text"]["content"] = label
    if _plain(before)[at] != label:
        _patch_text(client, block, items)
        expect = _plain(before)
        expect[at] = label
        if _plain(_items(client.call("GET", f"/blocks/{quote(block['id'], safe='')}"))) != expect:
            raise _fail("COMMS_READBACK_MISMATCH")
        return 1
    return 0


def todoist_label(count):
    return f"Todoist ({'99+' if count > TODOIST_LIMIT else count})"


def todoist_count(environ, tasks=None):
    token = (environ.get("FOLLOWUP_TODOIST_PERSONAL_TOKEN") or "").strip()
    if not token:
        return None
    try:
        from lifeos.platform import todoist                                                      # noqa: PLC0415
        return len((tasks or todoist.Todoist(token).tasks)(TODOIST_LIMIT + 1))
    except Exception:                                                                            # noqa: BLE001 - a fixed word only: never the token, a URL or a body
        print("imessage-comms: todoist unavailable", json.dumps({"code": "TODOIST_UNREADABLE"}))
        return None


def write(client, heading, rows, lines, heading_text):
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


def run(limit, live, environ=os.environ, now=None, fetcher=fetch, client=None, keygen=os.urandom, tasks=None):
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    anchor = (environ.get("CALENDAR_CARD_BLOCK_ID") or "").strip()                          # the Communications callout sits in the Calendar card's column
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
    except CommsError as e:
        print("imessage-comms: source refused", json.dumps({"code": str(e)}))
        rows = UNAVAILABLE
    lines, heading_text = render(rows, now)
    client = client or _writer(environ)
    heading, blocks, todoist = locate(client, anchor)
    count = todoist_count(environ, tasks) if todoist is not None else None
    counts = {"unread_7d": rows["messages"][0], "latest": rows["messages"][1].lower(),
              **{f"{c}_latest": rows[c][1].lower() for c in CHANNEL_BLOCKS},
              **{f"{c}_{k}": v for c in CHANNEL_BLOCKS for k, v in detail[c].items()}, "todoist": "none" if todoist is None else "unread" if count is None else count, "written": 0}
    if not live:
        return counts
    report_region.require_configured({region: (environ.get(var) or "").strip() for var, region in PROTECTED}, _fail)
    scoped = report_region.ScopedClient(client, heading["id"], _fail).adopt(*(b["id"] for pair in blocks.values() for b in pair), *([todoist["id"]] if todoist else []))      # only the blocks located above can be written
    with fixed(NotionError, _fail, "COMMS_WRITE_FAILED"):
        write(scoped, heading, blocks, lines, heading_text)
        counts["written"] = 2 * len(blocks) + 1
        if todoist is not None and count is not None:
            counts["written"] += write_todoist(scoped, todoist, todoist_label(count))
    return counts
