"""COMM-1.4: the read-only Messages reader, served over MCP stdio on the Mac Mini by the signed launchd helper in `mac/` (the one process with Full Disk Access).

Runs on the Mac with the system /usr/bin/python3 (3.9, stdlib only); `python3 -I` cannot import sibling modules, so every tool lives in this one file. Four tools,
each fixed SQL over the read-only database with at most one bounded integer argument: no caller-controlled path, query, command or flag exists.
- `imessage_attention` (no arguments): the accepted aggregate contract; it selects one COUNT(*) and never reads text, handles, chats or GUIDs.
- `imessage_counts` (no arguments): unread messages and unread conversations by defined window, service and group/direct; never presented as the iPhone badge.
- `imessage_recent` (`hours` 1-72): the newest incoming messages (at most 50) with sender, time, conversation reference and text, for on-demand reading.
- `imessage_conversation` (`chat` reference from `imessage_recent`): the last 20 messages of that one conversation, both directions.
- `imessage_page` (`hours` 1-72 for the first page, or the integer cursor `after_date` + `after_rowid` it returned): ALL incoming messages of the window,
  oldest first in strict (date, ROWID) order, one row per message, PAGE at a time; `more` is decided by fetching one extra row, so the end of results is
  proven, never assumed. Used only by the private alert scan on Oracle, which pages until `more` is false.
Content is returned to the caller only; nothing is written to disk, logged or kept. Never writes stderr; any failure is a fixed state, never an exception text.

What one observation can and cannot establish:
- `health` is the local read only: CURRENT = this Mac's database was read at `observed_at`; DISCONNECTED = it could not be opened (missing, no Full Disk Access);
  DEGRADED = it opened but did not have the expected shape. STALE is never set here: a quiet week is not a sync failure, so freshness is the consumer's call on
  `observed_at`.
- `sync` is always UNVERIFIED: nothing local proves that iCloud is still delivering to this Mac, so a readable database is never reported as a healthy sync.
- `attention` is NEEDS_ATTENTION only on positive local evidence (an incoming unread message from the last 7 days); otherwise UNKNOWN. CLEAR needs both a verified
  sync and no outstanding unread anywhere in the history (this Mac holds historical unread rows whose state disagrees with Jim's other devices), so it is never returned.
- `count` stays null and `count_reliable` false until the cross-device read/unread history is reconciled."""
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

TOOL = "imessage_attention"
DB = os.path.expanduser("~/Library/Messages/chat.db")
SOURCE = "Apple Messages / Mac Mini"
RECENT = timedelta(days=7)                 # the recent-attention window: older unread rows can only block CLEAR, never prove attention
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
PROTOCOL = "2025-06-18"
SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
RECENT_UNREAD = "SELECT COUNT(*) FROM message WHERE is_from_me = 0 AND is_read = 0 AND item_type = 0 AND date > ?"
CONTACTS = os.path.expanduser("~/Library/Application Support/AddressBook")
MAX_HOURS, MAX_RECENT, MAX_THREAD, MAX_TEXT, MAX_CHAT = 72, 50, 20, 2000, 10 ** 9
PAGE, MAX_TICKS, MAX_ROWID = 50, 10 ** 18, 2 ** 31 - 1
EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
TOOLS = {
    "imessage_attention": ("Aggregate iMessage attention state for the Mac Mini's local Messages copy. Read-only; no content or identities.", EMPTY),
    "imessage_counts": ("Unread incoming messages and conversations on the Mac Mini's local Messages copy, by window, service and group/direct. Read-only.", EMPTY),
    "imessage_recent": ("The newest incoming messages (at most 50) of the last N hours with sender, time, conversation reference and text. Read-only.",
                        {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": MAX_HOURS}}, "required": ["hours"],
                         "additionalProperties": False}),
    "imessage_conversation": ("The last 20 messages of one conversation (reference from imessage_recent), both directions. Read-only.",
                              {"type": "object", "properties": {"chat": {"type": "integer", "minimum": 1, "maximum": MAX_CHAT}}, "required": ["chat"],
                               "additionalProperties": False}),
    "imessage_page": ("All incoming messages of a window, oldest first, one page (50) at a time with a cursor; `more` false proves the end. Read-only.",
                      {"type": "object", "oneOf": [
                          {"properties": {"hours": {"type": "integer", "minimum": 1, "maximum": MAX_HOURS}}, "required": ["hours"], "additionalProperties": False},
                          {"properties": {"after_date": {"type": "integer", "minimum": 1, "maximum": MAX_TICKS},
                                          "after_rowid": {"type": "integer", "minimum": 1, "maximum": MAX_ROWID}},
                           "required": ["after_date", "after_rowid"], "additionalProperties": False}]}),
}
COUNT_DEFINITIONS = {
    "messages": "distinct incoming messages (by GUID) in a conversation with is_read = 0; reactions, retracted (unsent) and non-message rows excluded",
    "conversations": "distinct conversations containing at least one such message",
    "badge": "the Messages app badge is not readable here; compare by hand",
}
COUNT_LIMITATION = ("These are this Mac's local read flags. This Mac holds historical unread rows that disagree with Jim's iPhone, so all-time numbers are not the "
                    "iPhone's. A window's numbers are exact only after a same-time comparison with the iPhone confirms them.")


def _ticks(moment):
    """Messages `date`: nanoseconds since 2001-01-01 UTC (rows from old macOS used seconds; they are years old and never fall in the recent window)."""
    return int((moment - APPLE_EPOCH).total_seconds()) * 1_000_000_000


def snapshot(db=DB, now=None):
    now = now or datetime.now(timezone.utc)
    out = {"channel": "imessage", "attention": "UNKNOWN", "count": None, "count_reliable": False, "health": "DISCONNECTED", "sync": "UNVERIFIED",
           "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "source": SOURCE}
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return out                                                      # missing file or no Full Disk Access
    try:
        con.execute("PRAGMA query_only = ON")
        unread = con.execute(RECENT_UNREAD, (_ticks(now - RECENT),)).fetchone()[0]
    except sqlite3.OperationalError as e:
        out["health"] = "DISCONNECTED" if "unable to open" in str(e) else "DEGRADED"
        return out
    except Exception:
        out["health"] = "DEGRADED"                                      # not a database, wrong schema, anything unexpected
        return out
    finally:
        con.close()
    if not isinstance(unread, int) or isinstance(unread, bool) or unread < 0:
        out["health"] = "DEGRADED"
        return out
    out["health"] = "CURRENT"
    if unread:
        out["attention"] = "NEEDS_ATTENTION"
    return out


def _iso(ticks):
    seconds = ticks / 1e9 if ticks > 1e11 else ticks
    return (APPLE_EPOCH + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _body(text, attributed):
    """Message text: the `text` column, or the string inside the `attributedBody` typedstream (recent macOS leaves `text` empty). None when neither decodes."""
    if isinstance(text, str) and text:
        return text
    if not isinstance(attributed, bytes):
        return None
    at = attributed.find(b"NSString")
    plus = attributed.find(b"\x01+", at) if at >= 0 else -1
    if plus < 0 or plus - at > 16:
        return None
    p = plus + 2
    if p >= len(attributed):
        return None
    size = attributed[p]
    if size == 0x81:
        size, p = int.from_bytes(attributed[p + 1:p + 3], "little"), p + 3
    elif size == 0x82:
        size, p = int.from_bytes(attributed[p + 1:p + 5], "little"), p + 5
    else:
        p += 1
    raw = attributed[p:p + size]
    return raw.decode("utf-8", "replace") if len(raw) == size and size else None


def _contacts(handles, root=CONTACTS):
    """handle -> display name from the local Contacts databases (phone numbers by their last 10 digits, emails case-insensitively). Best effort: any failure
    leaves names unknown and is reported as such, never guessed."""
    wanted = {h for h in handles if h}
    if not wanted:
        return {}, "NOT_NEEDED"
    key = lambda h: h.lower() if "@" in h else "".join(c for c in h if c.isdigit())[-10:]
    by_key = {key(h): h for h in wanted}
    names, read = {}, False
    for path in [os.path.join(root, "AddressBook-v22.abcddb")] + sorted(glob.glob(os.path.join(root, "Sources", "*", "AddressBook-v22.abcddb"))):
        try:
            con = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=2)
        except Exception:
            continue
        try:
            con.execute("PRAGMA query_only = ON")
            rows = con.execute("SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER FROM ZABCDPHONENUMBER p JOIN ZABCDRECORD r ON r.Z_PK = p.ZOWNER").fetchall()
            rows += con.execute("SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS FROM ZABCDEMAILADDRESS e JOIN ZABCDRECORD r ON r.Z_PK = e.ZOWNER").fetchall()
            read = True
        except Exception:
            continue
        finally:
            con.close()
        for first, last, org, value in rows:
            handle = by_key.get(key(value)) if isinstance(value, str) else None
            name = " ".join(x for x in (first, last) if isinstance(x, str) and x) or (org if isinstance(org, str) else "")
            if handle and name and handle not in names:
                names[handle] = name
    return names, ("CURRENT" if read else "UNAVAILABLE")


def _base(now, db, health="DISCONNECTED"):
    return {"channel": "imessage", "health": health, "sync": "UNVERIFIED", "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": SOURCE}


def _read(db, now, work):
    """Open read-only, run `work(con, cols)`, map failures to the fixed health states. Returns (health, result)."""
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return "DISCONNECTED", None
    try:
        con.execute("PRAGMA query_only = ON")
        cols = {r[1] for r in con.execute("PRAGMA table_info(message)")}
        if not {"guid", "date", "is_from_me", "is_read", "item_type", "handle_id", "service"} <= cols:
            return "DEGRADED", None
        return "CURRENT", work(con, cols)
    except sqlite3.OperationalError as e:
        return ("DISCONNECTED" if "unable to open" in str(e) else "DEGRADED"), None
    except Exception:
        return "DEGRADED", None
    finally:
        con.close()


def _real(cols, incoming=True):
    """The fixed filter for a real message row: no reactions, no retracted (unsent) messages, no system rows."""
    where = ["m.item_type = 0"] + (["m.is_from_me = 0"] if incoming else [])
    if "associated_message_type" in cols:
        where.append("m.associated_message_type = 0")
    if "date_retracted" in cols:
        where.append("(m.date_retracted IS NULL OR m.date_retracted = 0)")
    return " AND ".join(where)


def counts(db=DB, now=None):
    now = now or datetime.now(timezone.utc)

    def work(con, cols):
        unread = _real(cols) + " AND m.is_read = 0"
        joined = " FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID JOIN chat c ON c.ROWID = j.chat_id WHERE " + unread
        windows = {}
        for name, since in (("all_time", None), ("last_7_days", now - timedelta(days=7)), ("last_24_hours", now - timedelta(hours=24))):
            where, args = (" AND m.date > ?", (_ticks(since),)) if since else ("", ())
            m, c = con.execute("SELECT COUNT(DISTINCT m.guid), COUNT(DISTINCT j.chat_id)" + joined + where, args).fetchone()
            windows[name] = {"messages": m, "conversations": c}
        services = {str(k or "unknown"): v for k, v in con.execute("SELECT m.service, COUNT(DISTINCT m.guid)" + joined + " GROUP BY m.service")}
        group = con.execute("SELECT COUNT(DISTINCT CASE WHEN c.style = 43 THEN j.chat_id END), COUNT(DISTINCT CASE WHEN c.style <> 43 THEN j.chat_id END)"
                            + joined).fetchone()
        return {"unread": windows, "unread_messages_by_service_all_time": services,
                "unread_conversations_all_time": {"group": group[0], "direct": group[1]}}
    health, result = _read(db, now, work)
    out = _base(now, db, health)
    out.update({"counts": result if health == "CURRENT" else None, "count_reliable": False, "definitions": COUNT_DEFINITIONS, "limitation": COUNT_LIMITATION})
    return out


def _messages(con, cols, where, args, limit, names_root):
    extra = ", ".join("m." + c if c in cols else "0" for c in ("attributedBody", "cache_has_attachments", "date_edited"))
    rows = con.execute("SELECT m.ROWID, m.guid, m.text, " + extra + ", m.date, m.service, m.is_from_me, m.is_read, h.id, c.ROWID, c.style, c.display_name"
                       " FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID JOIN chat c ON c.ROWID = j.chat_id"
                       " LEFT JOIN handle h ON h.ROWID = m.handle_id WHERE " + where + " ORDER BY m.date DESC, m.ROWID DESC LIMIT ?", args + (limit * 2,)).fetchall()
    seen, picked = set(), []
    for row in rows:
        if row[1] not in seen and len(picked) < limit:                 # one entry per message even when it is joined to two chats
            seen.add(row[1])
            picked.append(row)
    return _shape(picked, names_root)


def _shape(picked, names_root):
    """Rows (ROWID, guid, text, attributedBody, attachment, edited, date, service, mine, read, handle, chat, style, chat_name) -> message dicts."""
    names, contacts = _contacts([r[10] for r in picked if not r[8]], names_root)
    out = []
    for rowid, _, text, attributed, attachment, edited, date, service, mine, read, handle, chat, style, chat_name in picked:
        body = _body(text, attributed if isinstance(attributed, bytes) else None)
        out.append({"ref": "m%d" % rowid, "chat": "c%d" % chat, "sent_at": _iso(date), "from_me": bool(mine),
                    "sender": None if mine else {"handle": handle, "name": names.get(handle)},
                    "group": style == 43, "chat_name": chat_name or None, "service": service or "unknown", "unread": (not mine) and not read,
                    "edited": bool(edited), "attachment": bool(attachment), "text": body[:MAX_TEXT] if body else None,
                    "text_truncated": bool(body) and len(body) > MAX_TEXT})
    return out, contacts


def page(hours=None, after=None, db=DB, now=None, contacts_root=CONTACTS):
    """One page of ALL incoming messages after a cursor, oldest first. The first page starts at now - hours; each later one strictly after the (date, ROWID)
    of the previous page's last message, so a page boundary between messages that share a timestamp neither skips nor repeats one. A message joined to
    several conversations is one row (its lowest conversation id)."""
    now = now or datetime.now(timezone.utc)
    after_date, after_rowid = after if after else (_ticks(now - timedelta(hours=hours)), 0)
    box = {}

    def work(con, cols):
        extra = ", ".join("m." + c if c in cols else "0" for c in ("attributedBody", "cache_has_attachments", "date_edited"))
        rows = con.execute("SELECT m.ROWID, m.guid, m.text, " + extra + ", m.date, m.service, m.is_from_me, m.is_read, h.id, c.ROWID, c.style, c.display_name"
                           " FROM (SELECT m.ROWID AS rid, MIN(j.chat_id) AS cid FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID"
                           " WHERE " + _real(cols) + " AND (m.date > ? OR (m.date = ? AND m.ROWID > ?)) GROUP BY m.ROWID ORDER BY m.date, m.ROWID LIMIT ?) p"
                           " JOIN message m ON m.ROWID = p.rid JOIN chat c ON c.ROWID = p.cid LEFT JOIN handle h ON h.ROWID = m.handle_id"
                           " ORDER BY m.date, m.ROWID", (after_date, after_date, after_rowid, PAGE + 1)).fetchall()
        more = len(rows) > PAGE
        rows = rows[:PAGE]
        box["cursor"] = {"date": rows[-1][6], "rowid": rows[-1][0]} if rows else None
        box["more"] = more
        box["messages"], box["contacts"] = _shape(rows, contacts_root)
        return True
    health, _ = _read(db, now, work)
    out = _base(now, db, health)
    ok = health == "CURRENT"
    out.update({"messages": box.get("messages") if ok else None, "contacts": box.get("contacts", "UNAVAILABLE"), "limit": PAGE,
                "more": box.get("more") if ok else None, "cursor": box.get("cursor") if ok else None})
    return out


def recent(hours, db=DB, now=None, contacts_root=CONTACTS):
    now = now or datetime.now(timezone.utc)
    box = {}

    def work(con, cols):
        box["messages"], box["contacts"] = _messages(con, cols, _real(cols) + " AND m.date > ?", (_ticks(now - timedelta(hours=hours)),), MAX_RECENT, contacts_root)
        return True
    health, _ = _read(db, now, work)
    out = _base(now, db, health)
    out.update({"hours": hours, "messages": box.get("messages") if health == "CURRENT" else None, "contacts": box.get("contacts", "UNAVAILABLE"),
                "limit": MAX_RECENT})
    return out


def conversation(chat, db=DB, now=None, contacts_root=CONTACTS):
    now = now or datetime.now(timezone.utc)
    box = {}

    def work(con, cols):
        box["messages"], box["contacts"] = _messages(con, cols, _real(cols, incoming=False) + " AND c.ROWID = ?", (chat,), MAX_THREAD, contacts_root)
        return True
    health, _ = _read(db, now, work)
    out = _base(now, db, health)
    out.update({"chat": "c%d" % chat, "messages": box.get("messages") if health == "CURRENT" else None, "contacts": box.get("contacts", "UNAVAILABLE"),
                "limit": MAX_THREAD})
    return out


def _int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg, snap=snapshot, ops=None):
    """One JSON-RPC message -> one reply (None for notifications)."""
    ops = ops or {"imessage_counts": lambda args: counts(), "imessage_recent": lambda args: recent(args["hours"]),
                  "imessage_conversation": lambda args: conversation(args["chat"]),
                  "imessage_page": lambda args: page(args["hours"]) if "hours" in args else page(after=(args["after_date"], args["after_rowid"]))}
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "lifeos-imessage-attention", "version": "2"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": name, "description": text, "inputSchema": schema,
                             "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}} for name, (text, schema) in TOOLS.items()]}
    elif method == "tools/call":
        params = msg.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        args = params.get("arguments") if isinstance(params, dict) else None
        args = {} if args is None else args
        valid = isinstance(args, dict) and (
            (name in (TOOL, "imessage_counts") and args == {})
            or (name == "imessage_recent" and set(args) == {"hours"} and _int(args["hours"], 1, MAX_HOURS))
            or (name == "imessage_conversation" and set(args) == {"chat"} and _int(args["chat"], 1, MAX_CHAT))
            or (name == "imessage_page" and set(args) == {"hours"} and _int(args["hours"], 1, MAX_HOURS))
            or (name == "imessage_page" and set(args) == {"after_date", "after_rowid"} and _int(args["after_date"], 1, MAX_TICKS)
                and _int(args["after_rowid"], 1, MAX_ROWID)))
        if not valid:
            return _error(mid, -32602, "unsupported tool or arguments")
        state = snap() if name == TOOL else ops[name](args)
        result = {"content": [{"type": "text", "text": json.dumps(state)}], "structuredContent": state, "isError": state["health"] != "CURRENT"}
    else:
        return _error(mid, -32601, "method not found")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    sys.stdin.reconfigure(errors="replace")                            # undecodable bytes from a socket client are a refused line, never a crash
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        try:
            reply = handle(msg)
        except Exception:
            reply = _error(msg.get("id"), -32603, "internal error")
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
