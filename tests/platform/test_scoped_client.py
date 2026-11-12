"""LIFE-586: the write-scope guard. Only the network (a fake Notion client that records calls) is replaced; the guard is the real class."""
import unittest

from lifeos.platform.report_region import ScopedClient

ROOT = "11111111111111111111111111111111"
CHILD = "22222222222222222222222222222222"
NESTED = "33333333333333333333333333333333"
OTHER = "44444444444444444444444444444444"


class Refused(Exception):
    pass


def fail(code):
    return Refused(code)


class Fake:
    def __init__(self):
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith(f"/blocks/{ROOT}/children") and method == "GET":
            return {"results": [{"id": CHILD}]}
        if path.startswith(f"/blocks/{CHILD}/children") and method == "GET":
            return {"results": [{"id": NESTED}]}
        if path.startswith(f"/blocks/{ROOT}/children") and method == "PATCH":
            return {"results": [{"id": "55555555555555555555555555555555"}]}
        return {}

    call_once = call
    extra = "forwarded"


class Guard(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.client = ScopedClient(self.fake, ROOT, fail)

    def test_the_root_and_every_block_learned_from_a_listing_can_be_written_nested_ones_too(self):
        self.client.call("PATCH", f"/blocks/{ROOT}", {})
        self.client.call("GET", f"/blocks/{ROOT}/children?page_size=100")
        self.client.call("PATCH", f"/blocks/{CHILD}", {})
        self.client.call("GET", f"/blocks/{CHILD}/children")
        self.client.call("DELETE", f"/blocks/{NESTED}")
        self.assertEqual(len(self.fake.calls), 5)

    def test_a_block_never_seen_under_the_root_is_refused_before_anything_is_sent(self):
        for method in ("PATCH", "DELETE", "POST", "PUT"):
            with self.assertRaises(Refused) as caught:
                self.client.call(method, f"/blocks/{OTHER}", {})
            self.assertEqual(str(caught.exception), "WRITE_OUT_OF_SCOPE")
        self.assertEqual(self.fake.calls, [])

    def test_appending_under_another_block_and_writing_a_page_are_refused(self):
        with self.assertRaises(Refused):
            self.client.call("PATCH", f"/blocks/{OTHER}/children", {"children": []})
        with self.assertRaises(Refused):
            self.client.call("PATCH", f"/pages/{ROOT}", {})
        self.assertEqual(self.fake.calls, [])

    def test_reads_are_never_restricted_and_other_blocks_stay_unowned_after_reading_them(self):
        self.client.call("GET", f"/blocks/{OTHER}")
        self.client.call("GET", f"/blocks/{OTHER}/children")
        with self.assertRaises(Refused):
            self.client.call("PATCH", f"/blocks/{OTHER}", {})                         # reading a foreign block does not make it ours

    def test_a_listing_of_a_foreign_block_does_not_make_its_children_ours(self):
        self.client.call("GET", f"/blocks/{OTHER}/children")
        with self.assertRaises(Refused):
            self.client.call("PATCH", f"/blocks/{CHILD}", {})                         # CHILD was not listed under the root yet

    def test_ids_are_compared_without_dashes_and_case_and_call_once_is_guarded_too(self):
        dashed = "22222222-2222-2222-2222-222222222222"
        self.client.call("GET", f"/blocks/{ROOT}/children")
        self.client.call_once("PATCH", f"/blocks/{dashed.upper()}", {})
        with self.assertRaises(Refused):
            self.client.call_once("PATCH", f"/blocks/{OTHER}", {})

    def test_other_attributes_of_the_client_pass_through(self):
        self.assertEqual(self.client.extra, "forwarded")


if __name__ == "__main__":
    unittest.main()
