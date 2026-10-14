import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.imessage import attention_mcp as m

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
KEYS = {"channel", "attention", "count", "count_reliable", "health", "observed_at", "source"}
SECRET_TEXT, SECRET_HANDLE, SECRET_GUID = "meet at the clinic 4pm", "+13105550123", "chat-guid-ABC123"


def db(rows):
    """A synthetic Messages database: (age, is_from_me, is_read, item_type) per row, each carrying content and identities the tool must never surface."""
    path = os.path.join(tempfile.mkdtemp(), "chat.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, handle_id INTEGER, date INTEGER, is_from_me INTEGER, is_read INTEGER, item_type INTEGER)")
    con.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
    con.execute("INSERT INTO handle VALUES (1, ?)", (SECRET_HANDLE,))
    for age, mine, read, kind in rows:
        con.execute("INSERT INTO message (guid, text, handle_id, date, is_from_me, is_read, item_type) VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (SECRET_GUID, SECRET_TEXT, m._ticks(NOW - age), mine, read, kind))
    con.commit()
    con.close()
    return path


class Snapshot(unittest.TestCase):
    def check(self, out):
        self.assertEqual(set(out), KEYS)
        self.assertIsNone(out["count"])
        self.assertIs(out["count_reliable"], False)
        self.assertEqual((out["channel"], out["source"], out["observed_at"]), ("imessage", "Apple Messages / Mac Mini", "2026-10-14T15:00:00Z"))
        for secret in (SECRET_TEXT, SECRET_HANDLE, SECRET_GUID):
            self.assertNotIn(secret, json.dumps(out))
        return out["attention"], out["health"]

    def test_recent_incoming_unread_needs_attention(self):
        self.assertEqual(self.check(m.snapshot(db([(timedelta(hours=1), 0, 0, 0)]), NOW)), ("NEEDS_ATTENTION", "CURRENT"))

    def test_only_old_mine_read_or_system_rows_is_clear(self):
        rows = [(timedelta(hours=1), 0, 1, 0), (timedelta(hours=2), 1, 0, 0), (timedelta(hours=3), 0, 0, 2), (timedelta(days=30), 0, 0, 0)]
        self.assertEqual(self.check(m.snapshot(db(rows), NOW)), ("CLEAR", "CURRENT"))

    def test_stale_copy_is_unknown_never_clear(self):
        self.assertEqual(self.check(m.snapshot(db([(timedelta(days=4), 0, 0, 0)]), NOW)), ("UNKNOWN", "STALE"))

    def test_missing_or_unreadable_database_is_disconnected(self):
        self.assertEqual(self.check(m.snapshot("/nonexistent/chat.db", NOW)), ("UNKNOWN", "DISCONNECTED"))

    def test_unexpected_schema_is_degraded(self):
        path = os.path.join(tempfile.mkdtemp(), "chat.db")
        sqlite3.connect(path).execute("CREATE TABLE other (x)").connection.commit()
        self.assertEqual(self.check(m.snapshot(path, NOW)), ("UNKNOWN", "DEGRADED"))

    def test_empty_database_is_degraded(self):
        self.assertEqual(self.check(m.snapshot(db([]), NOW)), ("UNKNOWN", "DEGRADED"))

    def test_database_is_never_written(self):
        path = db([(timedelta(hours=1), 0, 0, 0)])
        with open(path, "rb") as f:
            before = f.read()
        m.snapshot(path, NOW)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)


class Protocol(unittest.TestCase):
    SNAP = staticmethod(lambda: m.snapshot(db([(timedelta(hours=1), 0, 0, 0)]), NOW))

    def call(self, params):
        return m.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}, snap=self.SNAP)

    def test_lists_exactly_one_argumentless_tool(self):
        tools = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["imessage_attention"])
        self.assertEqual(tools[0]["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})

    def test_approved_call_returns_the_contract(self):
        result = self.call({"name": "imessage_attention", "arguments": {}})["result"]
        self.assertEqual(result["structuredContent"]["attention"], "NEEDS_ATTENTION")
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertFalse(result["isError"])

    def test_other_tools_and_any_arguments_are_refused(self):
        for params in ({"name": "send", "arguments": {}}, {"name": "imessage_attention", "arguments": {"path": "/etc/passwd"}},
                       {"name": "imessage_attention", "arguments": {"query": "SELECT text FROM message"}}):
            self.assertEqual(self.call(params)["error"]["code"], -32602)

    def test_unknown_methods_are_refused_and_notifications_unanswered(self):
        self.assertEqual(m.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read"})["error"]["code"], -32601)
        self.assertIsNone(m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_stdio_server_fails_closed_without_messages_access(self):
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "imessage_attention", "arguments": {}}}]
        env = dict(os.environ, HOME=tempfile.mkdtemp())
        run = subprocess.run([sys.executable, m.__file__], input="\n".join(map(json.dumps, lines)) + "\n", capture_output=True, text=True, env=env, timeout=10)
        replies = [json.loads(x) for x in run.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        state = replies[1]["result"]["structuredContent"]
        self.assertEqual((state["attention"], state["health"], state["count"]), ("UNKNOWN", "DISCONNECTED", None))
        self.assertEqual(run.stderr, "")


if __name__ == "__main__":
    unittest.main()
