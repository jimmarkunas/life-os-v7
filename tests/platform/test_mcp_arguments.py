"""`arguments` given as a JSON-string object is read as that object; nothing else changes shape."""

import unittest

from lifeos.platform import mcp_access


class OperationArguments(unittest.TestCase):
    def test_an_object_is_unchanged_and_a_json_object_string_is_parsed(self):
        self.assertEqual(mcp_access.operation_arguments({"arguments": {"a": 1}}), {"a": 1})
        self.assertEqual(
            mcp_access.operation_arguments({"arguments": '{"workspace": "Personal"}'}), {"workspace": "Personal"}
        )

    def test_anything_else_is_returned_as_it_came_so_the_argument_check_still_refuses_it(self):
        for value in ("not json", "[1, 2]", '"Personal"', "3", 7, ["x"]):
            self.assertEqual(mcp_access.operation_arguments({"arguments": value}), value)
        self.assertEqual(mcp_access.operation_arguments({}), {})

    def test_the_shape_word_never_carries_content(self):
        cases = [
            ({}, "missing"),
            ({"arguments": {"workspace": "Personal"}}, "object"),
            ({"arguments": {"query": "secret words"}}, "object_without_workspace"),
            ({"arguments": "secret words"}, "string"),
            ({"arguments": 5}, "other"),
        ]
        for args, word in cases:
            self.assertEqual(mcp_access.arguments_shape(args), word)


if __name__ == "__main__":
    unittest.main()


class LiftFlat(unittest.TestCase):
    OPS = ("memory_recall", "memory_store")

    def test_flat_memory_fields_become_the_arguments_and_community_is_dropped(self):
        flat = {"operation": "memory_recall", "community": "life-os", "workspace": "Personal", "query": "q"}
        self.assertEqual(
            mcp_access.lift_flat(flat, self.OPS),
            {"operation": "memory_recall", "arguments": {"workspace": "Personal", "query": "q"}},
        )

    def test_a_nested_memory_call_keeps_its_arguments_and_loses_only_community(self):
        """Codex's shape: a nested arguments object plus a community its credential is not a member of."""
        args = {
            "operation": "memory_recall",
            "community": "personal",
            "arguments": {"workspace": "Personal", "query": "q"},
        }
        self.assertEqual(
            mcp_access.lift_flat(args, self.OPS),
            {"operation": "memory_recall", "arguments": {"workspace": "Personal", "query": "q"}},
        )
        self.assertEqual(
            mcp_access.lift_flat({"operation": "memory_recall", "community": "life-os"}, self.OPS),
            {"operation": "memory_recall"},
        )

    def test_other_operations_and_bare_memory_calls_are_unchanged(self):
        for args in (
            {"operation": "list_channels", "community": "life-os", "workspace": "x"},
            {"operation": "list_channels", "community": "nowhere", "arguments": {"a": 1}},
            {"operation": "memory_recall"},
            {"operation": "memory_recall", "arguments": {"workspace": "Personal"}},
        ):
            self.assertEqual(mcp_access.lift_flat(args, self.OPS), args)
        self.assertEqual(mcp_access.lift_flat("x", self.OPS), "x")
