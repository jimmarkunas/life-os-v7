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
