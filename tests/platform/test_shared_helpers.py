"""LIFE-645: the shared Notion paging loop, the fixed-code wrapper, the plain-text reader and the UTC clock."""
import unittest
from datetime import timedelta

from lifeos.platform import clock, notion_pages, notion_text
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError


class Boom(Exception):
    pass


def page(*results, more=None):
    return {"results": list(results), "has_more": more is not None, "next_cursor": more}


class Client:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def query_data_source(self, source_id, body):
        self.seen.append((source_id, body))
        return self.replies.pop(0)

    def call(self, method, path, body=None):
        self.seen.append((method, path))
        return self.replies.pop(0)


def codes(run):
    with unittest.TestCase().assertRaises(Boom) as caught:
        run()
    return str(caught.exception)


class Paging(unittest.TestCase):
    def test_every_page_is_followed_and_each_request_carries_the_cursor_of_the_one_before(self):
        client = Client(page(1, more="a"), page(2, more="b"), page(3))
        got = [r for p in notion_pages.query(client, "src", {"page_size": 100, "filter": 7}, Boom, incomplete="BAD") for r in p["results"]]
        self.assertEqual(got, [1, 2, 3])
        self.assertEqual(client.seen, [("src", {"page_size": 100, "filter": 7}), ("src", {"page_size": 100, "filter": 7, "start_cursor": "a"}), ("src", {"page_size": 100, "filter": 7, "start_cursor": "b"})])

    def test_a_page_that_is_not_whole_is_the_incomplete_code(self):
        for bad in (None, [], {"results": [], "has_more": "no"}, {"has_more": False}, {"results": None, "has_more": False}):
            self.assertEqual(codes(lambda bad=bad: list(notion_pages.pages(lambda c: bad, Boom, "BAD"))), "BAD")

    def test_a_missing_or_empty_or_non_text_cursor_is_the_incomplete_code(self):
        for cursor in (None, "", 5):
            reply = {"results": [], "has_more": True, "next_cursor": cursor}
            self.assertEqual(codes(lambda reply=reply: list(notion_pages.pages(lambda c: reply, Boom, "BAD", repeat="AGAIN"))), "BAD")

    def test_a_repeated_cursor_is_the_repeat_code_and_defaults_to_the_incomplete_one(self):
        fetch = lambda cursor: page(1, more="same")
        self.assertEqual(codes(lambda: list(notion_pages.pages(fetch, Boom, "BAD", repeat="AGAIN"))), "AGAIN")
        self.assertEqual(codes(lambda: list(notion_pages.pages(fetch, Boom, "BAD"))), "BAD")

    def test_more_pages_than_the_limit_is_the_capped_code_and_a_fresh_cursor_each_time_has_no_other_limit(self):
        count = []
        fetch = lambda cursor: (count.append(1), page(1, more=f"c{len(count)}"))[1]
        self.assertEqual(codes(lambda: list(notion_pages.pages(fetch, Boom, "BAD", capped="CAP", limit=3))), "CAP")
        self.assertEqual(len(count), 3)
        self.assertEqual(len(list(notion_pages.pages(lambda c: page(1, more=str(int(c or 0) + 1)) if int(c or 0) < 60 else page(1), Boom, "BAD"))), 61)      # no limit given: no cap

    def test_the_last_permitted_page_may_be_the_final_one(self):
        self.assertEqual(len(list(notion_pages.pages(lambda c: page(1, more="a") if c is None else page(2), Boom, "BAD", limit=2))), 2)

    def test_a_failed_fetch_is_the_read_code_without_a_chained_cause_or_passes_through_when_none_is_given(self):
        def refuse(cursor):
            raise NotionError("NOTION_HTTP_500")
        with self.assertRaises(Boom) as caught:
            list(notion_pages.pages(refuse, Boom, "BAD", read="READ"))
        self.assertEqual((str(caught.exception), caught.exception.__cause__, caught.exception.__suppress_context__), ("READ", None, True))
        with self.assertRaises(NotionError):
            list(notion_pages.pages(refuse, Boom, "BAD"))

    def test_children_quote_the_ids_and_ask_for_100_a_page(self):
        client = Client(page(1, more="a/b"), page(2))
        self.assertEqual([r for p in notion_pages.children(client, "x y", Boom, incomplete="BAD") for r in p["results"]], [1, 2])
        self.assertEqual(client.seen, [("GET", "/blocks/x%20y/children?page_size=100"), ("GET", "/blocks/x%20y/children?page_size=100&start_cursor=a%2Fb")])


class FixedCode(unittest.TestCase):
    def test_a_matching_error_becomes_the_fixed_code_with_no_cause_and_others_pass(self):
        with self.assertRaises(Boom) as caught:
            with fixed((KeyError, ValueError), Boom, "X_FAILED"):
                raise ValueError("secret body")
        self.assertEqual((str(caught.exception), caught.exception.__cause__, caught.exception.__suppress_context__), ("X_FAILED", None, True))
        with self.assertRaises(TypeError):
            with fixed(ValueError, Boom, "X_FAILED"):
                raise TypeError("other")
        with fixed(ValueError, Boom, "X_FAILED"):
            self.assertTrue(True)

    def test_an_error_named_in_keep_passes_through_unchanged_before_the_catch_is_tried(self):
        with self.assertRaises(KeyError):
            with fixed(Exception, Boom, "X_FAILED", keep=KeyError):
                raise KeyError("already coded")
        with self.assertRaises(Boom) as caught:
            with fixed(Exception, Boom, "X_FAILED", keep=KeyError):
                raise ValueError("anything else")
        self.assertEqual(str(caught.exception), "X_FAILED")


class CauseCode(unittest.TestCase):
    def test_a_notion_error_prints_its_own_code_before_the_fixed_code_and_nothing_else_does(self):
        import io
        from contextlib import redirect_stdout
        from lifeos.platform.notion_client import NotionError
        for error, printed in ((NotionError("NOTION_HTTP_409"), "cause=NOTION_HTTP_409\n"), (ValueError("secret body"), "")):
            out = io.StringIO()
            with redirect_stdout(out), self.assertRaises(Boom) as caught:
                with fixed(Exception, Boom, "X_FAILED"):
                    raise error
            self.assertEqual((out.getvalue(), str(caught.exception)), (printed, "X_FAILED"))


class PlainText(unittest.TestCase):
    def test_plain_text_is_preferred_then_the_text_content_and_non_objects_are_skipped(self):
        self.assertEqual(notion_text.plain([{"plain_text": "a"}, {"text": {"content": "b"}}, "x", {"plain_text": None, "text": {"content": None}}, {}]), "ab")
        self.assertEqual((notion_text.plain(None), notion_text.plain([])), ("", ""))

    def test_a_block_reads_the_rich_text_under_its_own_type(self):
        self.assertEqual(notion_text.block_text({"type": "callout", "callout": {"rich_text": [{"plain_text": "hi"}]}}), "hi")
        self.assertEqual(notion_text.block_text({"type": "divider"}), "")


class Clock(unittest.TestCase):
    def test_utcnow_is_aware_utc_and_current(self):
        self.assertEqual(clock.utcnow().utcoffset(), timedelta(0))


if __name__ == "__main__":
    unittest.main()
