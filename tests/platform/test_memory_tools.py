"""First-class memory tools: the rewrite into the `buzz_read` alias, the reply unwrap and the tool entries."""

import json
import unittest

from lifeos.platform import memory_honcho, memory_tools as T

OPS = tuple(memory_honcho.OPS)


def call(name, arguments):
    return {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def reply(payload):
    return {"jsonrpc": "2.0", "id": 7, "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


class Rewrite(unittest.TestCase):
    def test_a_memory_tool_call_becomes_the_alias_with_nested_arguments_and_no_community(self):
        msg = T.as_buzz_read(call("memory_recall", {"workspace": "Personal", "query": "q", "community": "x"}), OPS)
        self.assertEqual(msg["params"]["name"], "buzz_read")
        self.assertEqual(
            msg["params"]["arguments"],
            {"operation": "memory_recall", "arguments": {"workspace": "Personal", "query": "q"}},
        )
        self.assertEqual(msg["_memory_tool"], "memory_recall")

    def test_any_other_message_is_unchanged(self):
        for msg in (call("buzz_read", {"operation": "list_channels"}), {"method": "tools/list"}, call("memory_x", {})):
            self.assertIs(T.as_buzz_read(msg, OPS), msg)

    def test_non_object_arguments_become_empty_so_the_workspace_check_refuses_them(self):
        msg = T.as_buzz_read(call("memory_recall", "not an object"), OPS)
        self.assertEqual(msg["params"]["arguments"]["arguments"], {})


class Unwrap(unittest.TestCase):
    marked = {"_memory_tool": "memory_recall"}

    def text(self, out):
        return json.loads(out["result"]["content"][0]["text"])

    def test_an_ok_reply_becomes_the_operation_data(self):
        out = T.unwrap(
            reply({"contract": "c", "operation": "memory_recall", "ok": True, "data": {"count": 1}}), self.marked
        )
        self.assertEqual(self.text(out), {"ok": True, "count": 1})

    def test_a_refusal_keeps_only_its_fixed_fields(self):
        out = T.unwrap(
            reply({"contract": "c", "ok": False, "error": "access_denied", "reason": "no_grant"}), self.marked
        )
        self.assertEqual(self.text(out), {"ok": False, "error": "access_denied", "reason": "no_grant"})

    def test_an_alias_call_or_an_unreadable_reply_is_unchanged(self):
        r = reply({"ok": True, "data": {}})
        self.assertIs(T.unwrap(r, {}), r)
        bad = {"result": {"content": [{"type": "text", "text": "not json"}]}}
        self.assertIs(T.unwrap(bad, self.marked), bad)


class Entries(unittest.TestCase):
    def test_every_operation_has_an_entry_with_personal_only_and_honest_hints(self):
        entries = T.tool_entries()
        self.assertEqual(tuple(entries), OPS)
        for name, entry in entries.items():
            self.assertEqual(entry["inputSchema"]["properties"]["workspace"]["enum"], ["Personal"], name)
            self.assertFalse(entry["inputSchema"]["additionalProperties"], name)
            self.assertEqual(entry["annotations"]["readOnlyHint"], name in ("memory_recall", "memory_ask"), name)
            self.assertEqual(entry["annotations"]["destructiveHint"], name == "memory_revoke", name)

    def test_a_caller_whose_grants_cannot_be_read_is_offered_no_memory_tool(self):
        listing = {"result": {"tools": [{"name": "buzz_read"}]}}
        out = T.add_tools(listing, {"method": "tools/list"}, {"facts": {"credential": "nobody"}}, object())
        self.assertEqual(out["result"]["tools"], [{"name": "buzz_read"}])
