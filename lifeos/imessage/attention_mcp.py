"""COMM-1.4: the one read-only iMessage attention tool, served over MCP stdio by the Mac Mini's OpenClaw node (nodeHost.mcp.servers -> mcp.tools.call.v1).

Runs on the Mac with the system /usr/bin/python3 (3.9, stdlib only). One tool, no arguments: no caller-controlled path, query, command or flag exists. It opens
the local Messages database read-only and selects only aggregates (MAX(date), COUNT(*)): no text, handle, chat or GUID column is ever read, returned, logged or stored.
Writes nothing to disk; never writes stderr.

The Mac Mini's read/unread history disagrees with Jim's other devices, so the count is never published (count null, count_reliable false) until reconciled.
Attention is derived only from recent incoming unread rows while the local copy is current; anything else fails closed to UNKNOWN, never CLEAR or zero."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

TOOL = "imessage_attention"
DB = os.path.expanduser("~/Library/Messages/chat.db")
SOURCE = "Apple Messages / Mac Mini"
RECENT = timedelta(days=7)                 # unread older than this is history, not attention
STALE_AFTER = timedelta(hours=72)          # no new row for this long: the local copy may have stopped syncing
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
PROTOCOL = "2025-06-18"
SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _apple(value):
    """Messages stores nanoseconds since 2001 (seconds on very old rows)."""
    return APPLE_EPOCH + timedelta(seconds=value / 1e9 if value > 1e11 else value)


def _ticks(moment):
    return int((moment - APPLE_EPOCH).total_seconds() * 1e9)


def snapshot(db=DB, now=None):
    now = now or datetime.now(timezone.utc)
    out = {"channel": "imessage", "attention": "UNKNOWN", "count": None, "count_reliable": False,
           "health": "DISCONNECTED", "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "source": SOURCE}
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return out                                                      # missing file or no Full Disk Access
    try:
        con.execute("PRAGMA query_only = ON")
        newest = con.execute("SELECT MAX(date) FROM message").fetchone()[0]
        unread = con.execute("SELECT COUNT(*) FROM message WHERE is_from_me = 0 AND is_read = 0 AND item_type = 0 AND date > ?",
                             (_ticks(now - RECENT),)).fetchone()[0]
    except sqlite3.OperationalError as e:
        out["health"] = "DISCONNECTED" if "unable to open" in str(e) or "authoriz" in str(e) else "DEGRADED"
        return out
    except sqlite3.Error:
        out["health"] = "DEGRADED"
        return out
    finally:
        con.close()
    if not isinstance(newest, int) or not isinstance(unread, int):
        out["health"] = "DEGRADED"
        return out
    if now - _apple(newest) > STALE_AFTER:
        out["health"] = "STALE"
        return out
    out["health"] = "CURRENT"
    out["attention"] = "NEEDS_ATTENTION" if unread else "CLEAR"
    return out


def handle(msg, snap=snapshot):
    """One JSON-RPC message -> one reply (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "lifeos-imessage-attention", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": TOOL, "description": "Aggregate iMessage attention state for the Mac Mini's local Messages copy. Read-only; no content or identities.",
                             "inputSchema": SCHEMA, "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != TOOL or params.get("arguments") not in (None, {}):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "only " + TOOL + " with no arguments"}}
        state = snap()
        result = {"content": [{"type": "text", "text": json.dumps(state)}], "structuredContent": state, "isError": state["health"] != "CURRENT"}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
