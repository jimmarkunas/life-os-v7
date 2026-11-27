"""a Notion lookup by exact title reads EVERY page of `POST /search`"""

import unittest

from lifeos.accountability import region
from lifeos.inbox import agent_inbox
from lifeos.inbox.model import InboxError
from lifeos.megibow import v3
from lifeos.platform import notion_pages, settings
from lifeos.platform.notion_client import NotionError


class Boom(Exception):
    pass


def page(title, pid, kind="page"):
    if kind == "data_source":
        return {"object": "data_source", "id": pid, "title": [{"plain_text": title}]}
    return {"object": "page", "id": pid, "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}}}


class Search:
    """A Notion double whose /search answers `per` results a page with a cursor"""

    def __init__(self, items, per=2, other=None, repeat_cursor=False, drop_has_more=False):
        self.items, self.per, self.other, self.repeat, self.drop = items, per, other or {}, repeat_cursor, drop_has_more
        self.searches = []

    def call(self, method, path, body=None, *args, **kwargs):
        if method == "POST" and path == "/search":
            self.searches.append(dict(body))
            start = int(body.get("start_cursor") or 0)
            chunk = self.items[start : start + self.per]
            more = start + self.per < len(self.items)
            out = {
                "results": chunk,
                "has_more": more,
                "next_cursor": ("0" if self.repeat else str(start + self.per)) if more else None,
            }
            if self.drop:
                out.pop("has_more")
            return out
        handler = self.other.get(path.split("?")[0])
        if handler is None:
            raise AssertionError(f"unexpected call {method} {path}")
        return handler(path) if callable(handler) else handler


def refuse(code):
    return Boom(code)


class Helper(unittest.TestCase):
    def test_a_match_on_the_second_page_is_found_and_every_page_uses_the_full_page_size(self):
        items = [page(f"Agent Wikis {n}", f"w{n}") for n in range(5)] + [page("Wanted", "hit")]
        client = Search(items)
        found = notion_pages.search_all(client, "Wanted", "page", refuse, incomplete="X")
        self.assertEqual([r["id"] for r in found], ["w0", "w1", "w2", "w3", "w4", "hit"])
        self.assertEqual(len(client.searches), 3)
        self.assertNotIn("start_cursor", client.searches[0])
        self.assertEqual(client.searches[1]["start_cursor"], "2")
        self.assertTrue(
            all(
                s["page_size"] == 100
                and s["query"] == "Wanted"
                and s["filter"] == {"property": "object", "value": "page"}
                for s in client.searches
            )
        )

    def test_no_result_is_an_empty_list_not_an_error(self):
        self.assertEqual(notion_pages.search_all(Search([]), "Wanted", "page", refuse, incomplete="X"), [])

    def test_two_matches_are_both_returned_for_the_caller_to_refuse(self):
        client = Search([page("Wanted", "a"), page("other", "b"), page("Wanted", "c")])
        self.assertEqual(
            [r["id"] for r in notion_pages.search_all(client, "Wanted", "page", refuse, incomplete="X")],
            ["a", "b", "c"],
        )

    def test_the_twenty_page_cap_is_a_fixed_refusal_never_a_short_list(self):
        client = Search([page(f"n{n}", f"i{n}") for n in range(60)], per=2)
        with self.assertRaises(Boom) as caught:
            notion_pages.search_all(client, "Wanted", "page", refuse, incomplete="INCOMPLETE", capped="CAPPED")
        self.assertEqual(str(caught.exception), "CAPPED")
        self.assertEqual(len(client.searches), notion_pages.SEARCH_PAGE_LIMIT)

    def test_exactly_twenty_pages_is_complete(self):
        client = Search([page(f"n{n}", f"i{n}") for n in range(40)], per=2)
        self.assertEqual(len(notion_pages.search_all(client, "x", "page", refuse, incomplete="I", capped="C")), 40)

    def test_a_repeated_cursor_and_a_reply_without_has_more_are_refused(self):
        with self.assertRaises(Boom) as caught:
            notion_pages.search_all(
                Search([page("a", "a")] * 6, repeat_cursor=True),
                "x",
                "page",
                refuse,
                incomplete="INCOMPLETE",
                repeat="REPEAT",
            )
        self.assertEqual(str(caught.exception), "REPEAT")
        with self.assertRaises(Boom) as caught:
            notion_pages.search_all(
                Search([page("a", "a")], drop_has_more=True), "x", "page", refuse, incomplete="INCOMPLETE"
            )
        self.assertEqual(str(caught.exception), "INCOMPLETE")

    def test_a_failed_search_passes_through_unless_read_is_named(self):
        class Down:
            def call(self, *a, **k):
                raise NotionError("NOTION_HTTP_500")

        with self.assertRaises(NotionError):
            notion_pages.search_all(Down(), "x", "page", refuse, incomplete="I")
        with self.assertRaises(Boom) as caught:
            notion_pages.search_all(Down(), "x", "page", refuse, incomplete="I", read="READ")
        self.assertEqual(str(caught.exception), "READ")


JUNK = [page(f"Agent Wikis {n}", f"w{n}") for n in range(7)]


class Callers(unittest.TestCase):
    def test_megibow_finds_the_register_past_the_first_page_and_still_refuses_none_or_two(self):
        self.assertEqual(v3.find(Search(JUNK + [page(v3.REGISTER_TITLE, "reg")]), v3.REGISTER_TITLE, "page"), "reg")
        self.assertEqual(
            v3.find(
                Search(JUNK + [page(v3.MATT_SOURCE_TITLE, "ds", "data_source")]), v3.MATT_SOURCE_TITLE, "data_source"
            ),
            "ds",
        )
        for items in (JUNK, JUNK + [page(v3.REGISTER_TITLE, "a"), page(v3.REGISTER_TITLE, "b")]):
            with self.assertRaises(v3.V3Error) as caught:
                v3.find(Search(items), v3.REGISTER_TITLE, "page")
            self.assertEqual(str(caught.exception), "MEGIBOW_V3_TARGET_NOT_FOUND")

    def test_megibow_refuses_a_capped_or_short_search_with_the_same_code(self):
        for client in (
            Search([page(f"n{n}", f"i{n}") for n in range(80)], per=2),
            Search([page("a", "a")], drop_has_more=True),
        ):
            with self.assertRaises(v3.V3Error) as caught:
                v3.find(client, v3.REGISTER_TITLE, "page")
            self.assertEqual(str(caught.exception), "MEGIBOW_V3_TARGET_NOT_FOUND")

    def test_the_accountability_register_is_found_past_the_first_page(self):
        cal = "c" * 32
        children = {
            "results": [
                {"id": "r", "type": "code", "code": {"rich_text": [{"plain_text": f"R4L jim.week        {cal}"}]}}
            ],
            "has_more": False,
        }
        client = Search(JUNK + [page(region.REGISTER_TITLE, "reg")], other={"/blocks/reg/children": children})
        self.assertEqual(region.register_row(client, "jim.week"), cal)
        with self.assertRaises(region.AccountabilityError) as caught:
            region.register_row(Search(JUNK), "jim.week")
        self.assertEqual(str(caught.exception), "ACCOUNTABILITY_TARGET_NOT_FOUND")

    def test_the_inbox_database_is_found_past_the_first_page(self):
        client = Search(
            [page(f"Agent Inbox list {n}", f"d{n}", "data_source") for n in range(30)]
            + [page(agent_inbox.DB_TITLE, "inbox-db", "data_source")]
        )
        self.assertEqual(agent_inbox.locate(client), "inbox-db")
        with self.assertRaises(InboxError) as caught:
            agent_inbox.locate(Search([]))
        self.assertEqual(caught.exception.reason, "inbox_not_found")


class TilePage(unittest.TestCase):
    """The tile page: the parent of the located database first, the paged title search as the fallback."""

    def tree(self, **over):
        cols = [{"id": f"col{i}", "type": "column"} for i in range(5)]
        blocks = {
            "pg": {"results": [{"id": "cl", "type": "column_list"}], "has_more": False},
            "cl": {"results": cols, "has_more": False},
        }
        for i in range(5):
            blocks[f"col{i}"] = {"results": [{"id": f"co{i}", "type": "callout"}], "has_more": False}
            blocks[f"co{i}"] = {
                "results": [
                    {"id": f"h{i}", "type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "-"}]}},
                    {"id": f"p{i}", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "old"}]}},
                ],
                "has_more": False,
            }
        other = {f"/blocks/{k}/children": v for k, v in blocks.items()}
        other.update(
            {
                "/data_sources/src": {"parent": {"type": "database_id", "database_id": "db"}},
                "/databases/db": {"parent": {"type": "page_id", "page_id": "pg"}},
                "/pages/pg": page(agent_inbox.PAGE_TITLE, "pg"),
            }
        )
        other.update(over)
        return other

    def test_the_search_fallback_finds_the_page_past_the_first_page(self):
        client = Search(
            JUNK + [page(agent_inbox.PAGE_TITLE, "pg")],
            other=self.tree(**{"/databases/db": {"parent": {"type": "workspace"}}}),
        )
        self.assertEqual(agent_inbox.locate_page(client, "src"), "pg")
        self.assertEqual(agent_inbox.locate_page(client), "pg")
        self.assertGreaterEqual(len(client.searches), 8)  # 4 pages a call, two calls

    def test_the_parent_page_is_used_without_any_search_for_the_old_page_title(self):
        client = Search(JUNK, other=self.tree())
        self.assertEqual(agent_inbox.locate_page(client, "src"), "pg")
        self.assertEqual(
            {body["query"] for body in client.searches}, {agent_inbox.MERGED_TITLE}
        )  # the merged page is looked for first

    def test_a_parent_with_another_title_or_a_failed_read_falls_back_to_the_search(self):
        for over in (
            {"/pages/pg": page("Some other page", "pg")},
            {"/pages/pg": lambda path: (_ for _ in ()).throw(NotionError("NOTION_HTTP_404"))},
        ):
            client = Search(JUNK + [page(agent_inbox.PAGE_TITLE, "pg")], other=self.tree(**over))
            self.assertEqual(agent_inbox.locate_page(client, "src"), "pg")
            self.assertTrue(client.searches)

    def test_none_or_two_tile_pages_is_still_a_fixed_refusal(self):
        for items, word in (
            (JUNK, "page_not_found"),
            (JUNK + [page(agent_inbox.PAGE_TITLE, "pg"), page(agent_inbox.PAGE_TITLE, "pg")], "page_ambiguous"),
        ):
            with self.assertRaises(InboxError) as caught:
                agent_inbox.locate_page(
                    Search(items, other=self.tree(**{"/databases/db": {"parent": {"type": "workspace"}}})), "src"
                )
            self.assertEqual(caught.exception.reason, word)


if __name__ == "__main__":
    unittest.main()
