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
KEYS = {"channel", "attention", "count", "count_reliable", "health", "sync", "observed_at", "source"}
SECRET_TEXT, SECRET_HANDLE, SECRET_GUID = "meet at the clinic 4pm", "+13105550123", "chat-guid-ABC123"
H, D = timedelta(hours=1), timedelta(days=1)


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


def raw(content):
    path = os.path.join(tempfile.mkdtemp(), "chat.db")
    with open(path, "wb") as f:
        f.write(content)
    return path


class Snapshot(unittest.TestCase):
    def observe(self, path):
        out = m.snapshot(path, NOW)
        self.assertEqual(set(out), KEYS)
        self.assertIsNone(out["count"])                                   # never a count, never zero
        self.assertIs(out["count_reliable"], False)
        self.assertEqual(out["sync"], "UNVERIFIED")                       # a readable database never proves iCloud sync
        self.assertNotEqual(out["attention"], "CLEAR")
        self.assertEqual((out["channel"], out["source"], out["observed_at"]), ("imessage", "Apple Messages / Mac Mini", "2026-10-14T15:00:00Z"))
        for secret in (SECRET_TEXT, SECRET_HANDLE, SECRET_GUID, "1", path):
            self.assertNotIn(secret, json.dumps({k: v for k, v in out.items() if k != "observed_at"}))
        return out["attention"], out["health"]

    def test_recent_incoming_unread_needs_attention(self):
        self.assertEqual(self.observe(db([(H, 0, 0, 0), (2 * H, 0, 1, 0)])), ("NEEDS_ATTENTION", "CURRENT"))

    def test_historical_unread_without_recent_activity_is_unknown_not_clear(self):
        self.assertEqual(self.observe(db([(400 * D, 0, 0, 0), (30 * D, 0, 0, 0), (H, 0, 1, 0)])), ("UNKNOWN", "CURRENT"))

    def test_only_read_messages_is_unknown_because_sync_is_unverified(self):
        self.assertEqual(self.observe(db([(H, 0, 1, 0), (2 * H, 1, 0, 0), (3 * H, 0, 0, 2)])), ("UNKNOWN", "CURRENT"))

    def test_quiet_days_are_not_stale(self):
        self.assertEqual(self.observe(db([(5 * D, 0, 0, 0)])), ("NEEDS_ATTENTION", "CURRENT"))
        self.assertEqual(self.observe(db([(10 * D, 0, 1, 0)])), ("UNKNOWN", "CURRENT"))
        self.assertEqual(self.observe(db([])), ("UNKNOWN", "CURRENT"))

    def test_unavailable_database_is_disconnected(self):
        self.assertEqual(self.observe("/nonexistent/chat.db"), ("UNKNOWN", "DISCONNECTED"))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores file modes")
    def test_permission_denied_is_disconnected(self):
        path = db([(H, 0, 0, 0)])
        os.chmod(path, 0)
        try:
            self.assertEqual(self.observe(path), ("UNKNOWN", "DISCONNECTED"))
        finally:
            os.chmod(path, 0o600)

    def test_unexpected_schema_or_not_a_database_is_degraded(self):
        other = os.path.join(tempfile.mkdtemp(), "chat.db")
        with sqlite3.connect(other) as con:
            con.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER)")
        self.assertEqual(self.observe(other), ("UNKNOWN", "DEGRADED"))
        self.assertEqual(self.observe(raw(b"this is not a sqlite database" * 100)), ("UNKNOWN", "DEGRADED"))

    def test_database_is_never_written(self):
        path = db([(H, 0, 0, 0), (400 * D, 0, 0, 0)])
        with open(path, "rb") as f:
            before = f.read()
        self.observe(path)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))), ["chat.db"])


class Protocol(unittest.TestCase):
    SNAP = staticmethod(lambda: m.snapshot(db([(H, 0, 0, 0)]), NOW))

    def call(self, params):
        return m.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}, snap=self.SNAP)

    def test_lists_exactly_the_five_read_only_tools(self):
        tools = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["imessage_attention", "imessage_counts", "imessage_recent", "imessage_conversation", "imessage_page"])
        self.assertEqual(tools[0]["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})
        self.assertTrue(all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools))

    def test_approved_call_returns_the_contract(self):
        result = self.call({"name": "imessage_attention", "arguments": {}})["result"]
        self.assertEqual(result["structuredContent"]["attention"], "NEEDS_ATTENTION")
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertFalse(result["isError"])

    def test_unsupported_tools_are_refused(self):
        for name in ("send", "imsg", "mark_read", "", None):
            self.assertEqual(self.call({"name": name, "arguments": {}})["error"]["code"], -32602)

    def test_any_arguments_paths_or_sql_are_refused_with_a_fixed_error(self):
        for params in ({"name": "imessage_attention", "arguments": {"path": "/etc/passwd"}}, {"name": "imessage_attention", "arguments": {"query": "SELECT text FROM message"}},
                       {"name": "imessage_attention", "arguments": "; rm -rf ~"}, {"name": "imessage_attention", "arguments": []}, ["imessage_attention"], "imessage_attention"):
            reply = self.call(params)
            self.assertEqual(reply["error"], {"code": -32602, "message": "unsupported tool or arguments"})

    def test_unknown_methods_are_refused_and_notifications_unanswered(self):
        self.assertEqual(m.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read"})["error"]["code"], -32601)
        self.assertIsNone(m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_stdio_server_survives_bad_input_and_fails_closed_without_messages_access(self):
        lines = ["not json", "[1, 2]", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                 json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": [1]}),
                 json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "imessage_attention", "arguments": {}}})]
        env = dict(os.environ, HOME=tempfile.mkdtemp())
        run = subprocess.run([sys.executable, m.__file__], input="\n".join(lines) + "\n", capture_output=True, text=True, env=env, timeout=10)
        replies = [json.loads(x) for x in run.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[1]["error"]["code"], -32602)
        state = replies[2]["result"]["structuredContent"]
        self.assertEqual((state["attention"], state["health"], state["sync"], state["count"]), ("UNKNOWN", "DISCONNECTED", "UNVERIFIED", None))
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertEqual((run.returncode, run.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
