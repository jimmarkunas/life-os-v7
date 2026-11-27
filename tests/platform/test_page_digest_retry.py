"""page_digest walks again once when a block it listed is gone (another card writer rewrote the page mid-read) and fails
as before when the second walk.
"""

import unittest
from unittest import mock
from urllib.parse import unquote

from lifeos.platform import report_region
from lifeos.platform.notion_client import NotionError


class Fail(Exception):
    pass


def block(block_id, kind, has_children=False):
    return {"id": block_id, "type": kind, "has_children": has_children, kind: {"rich_text": []}}


class Page:
    """A page with one toggle (id `toggle`) that holds one bullet."""

    def __init__(self, errors=None):
        self.tree = {
            "page": [block("toggle", "toggle", True), block("tail", "paragraph")],
            "toggle": [block("bullet", "bulleted_list_item")],
        }
        self.errors = {k: list(v) for k, v in (errors or {}).items()}
        self.listed = []

    def call(self, method, path, body=None):
        block_id = unquote(path.split("/blocks/")[1].split("/children")[0])
        self.listed.append(block_id)
        if self.errors.get(block_id):
            raise NotionError(self.errors[block_id].pop(0))
        return {"results": self.tree[block_id], "has_more": False}


def digest(client):
    with mock.patch.object(report_region.time, "sleep") as sleep:
        result = report_region.page_digest(client, "page", [], Fail)
    return result, sleep


class PageDigestRetry(unittest.TestCase):
    def test_a_block_that_vanishes_once_is_walked_again(self):
        page = Page({"toggle": ["NOTION_HTTP_404"]})
        result, sleep = digest(page)
        self.assertEqual(sorted(result["layout"]), ["page", "toggle"])
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(page.listed, ["page", "toggle", "page", "toggle"])

    def test_a_block_that_stays_missing_fails_with_the_same_code_as_before(self):
        page = Page({"toggle": ["NOTION_HTTP_404", "NOTION_HTTP_404"]})
        with self.assertRaises(Fail) as raised:
            digest(page)
        self.assertEqual(str(raised.exception), "PAGE_WALK_NOTION_HTTP_404:toggle")

    def test_another_notion_error_is_not_retried(self):
        page = Page({"toggle": ["NOTION_HTTP_429"]})
        with self.assertRaises(Fail) as raised:
            digest(page)
        self.assertEqual(str(raised.exception), "PAGE_WALK_NOTION_HTTP_429:toggle")
        self.assertEqual(page.listed, ["page", "toggle"])

    def test_a_missing_page_itself_is_not_retried(self):
        page = Page({"page": ["NOTION_HTTP_404"]})
        with self.assertRaises(Fail) as raised:
            digest(page)
        self.assertEqual(str(raised.exception), "PAGE_WALK_NOTION_HTTP_404:page")
        self.assertEqual(page.listed, ["page"])


if __name__ == "__main__":
    unittest.main()
