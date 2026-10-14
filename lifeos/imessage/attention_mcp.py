"""COMM-1.4: the one read-only iMessage attention tool, served over MCP stdio on the Mac Mini (candidate; not deployed until Jim approves the helper boundary).

Runs on the Mac with the system /usr/bin/python3 (3.9, stdlib only). One tool, no arguments: no caller-controlled path, query, command or flag exists. It opens
the local Messages database read-only and selects one aggregate, COUNT(*): no text, handle, chat or GUID column is ever read, returned, logged or stored.
Writes nothing to disk; never writes stderr; any failure is a fixed state, never an exception text.

What one observation can and cannot establish:
- `health` is the local read only: CURRENT = this Mac's database was read at `observed_at`; DISCONNECTED = it could not be opened (missing, no Full Disk Access);
  DEGRADED = it opened but did not have the expected shape. STALE is never set here: a quiet week is not a sync failure, so freshness is the consumer's call on
  `observed_at`.
- `sync` is always UNVERIFIED: nothing local proves that iCloud is still delivering to this Mac, so a readable database is never reported as a healthy sync.
- `attention` is NEEDS_ATTENTION only on positive local evidence (an incoming unread message from the last 7 days); otherwise UNKNOWN. CLEAR needs both a verified
  sync and no outstanding unread anywhere in the history (this Mac holds historical unread rows whose state disagrees with Jim's other devices), so it is never returned.
- `count` stays null and `count_reliable` false until the cross-device read/unread history is reconciled."""
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


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg, snap=snapshot):
    """One JSON-RPC message -> one reply (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "lifeos-imessage-attention", "version": "2"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": TOOL, "description": "Aggregate iMessage attention state for the Mac Mini's local Messages copy. Read-only; no content or identities.",
                             "inputSchema": SCHEMA, "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}]}
    elif method == "tools/call":
        params = msg.get("params")
        if not isinstance(params, dict) or params.get("name") != TOOL or params.get("arguments") not in (None, {}):
            return _error(mid, -32602, "only " + TOOL + " with no arguments")
        state = snap()
        result = {"content": [{"type": "text", "text": json.dumps(state)}], "structuredContent": state, "isError": state["health"] != "CURRENT"}
    else:
        return _error(mid, -32601, "method not found")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
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
