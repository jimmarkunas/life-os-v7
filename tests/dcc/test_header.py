"""D183: V7 keeps one dated line at the top of the Daily Report, creates it once, and never leaves copies."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.dcc import card, header
from lifeos.platform.notion_client import NotionError

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 12, 14, 15, tzinfo=TZ)
LINE = "Monday, Oct 12 · Updated 2:15 PM CT"


def para(block_id, content):
    return {"id": block_id, "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": content, "text": {"content": content}}]}}


class FakePage:
    """The Daily Report's top level in memory: blocks in order, the Command Center callout one level down, and the two Notion calls the header makes."""
    def __init__(self, top, honours_position=True, refuse_create=False):
        self.top, self.honours, self.refuse, self.log, self.n = list(top), honours_position, refuse_create, [], 0

    def _find(self, block_id):
        return next((b for b in self.top if b["id"] == block_id), None)

    def call(self, method, path, body=None):
        self.log.append(method)
        parts = path.split("?")[0].strip("/").split("/")
        if method == "GET" and parts == ["blocks", "dcc"]:
            return {"id": "dcc", "type": "callout", "parent": {"type": "page_id", "page_id": "page"}}
        if method == "GET" and parts == ["blocks", "page", "children"]:
            return {"results": list(self.top), "has_more": False}
        if method == "GET" and parts[0] == "blocks":
            found = self._find(parts[1])
            if found is None:
                raise NotionError("NOTION_HTTP_404")
            return found
        if method == "PATCH" and parts == ["blocks", "page", "children"]:
            if self.refuse:
                raise NotionError("NOTION_HTTP_400")
            self.n += 1
            block = {"id": f"new{self.n}", "type": "paragraph", "paragraph": {"rich_text": body["children"][0]["paragraph"]["rich_text"]}}
            block["paragraph"]["rich_text"] = [{"plain_text": r["text"]["content"], "text": r["text"]} for r in block["paragraph"]["rich_text"]]
            if self.honours and (body.get("position") or {}).get("type") == "start":
                self.top.insert(0, block)
            else:
                self.top.append(block)
            return {"results": [block]}
        if method == "PATCH" and parts[0] == "blocks":
            found = self._find(parts[1])
            found["paragraph"]["rich_text"] = [{"plain_text": r["text"]["content"], "text": r["text"]} for r in body["paragraph"]["rich_text"]]
            return found
        if method == "DELETE":
            self.top = [b for b in self.top if b["id"] != parts[1]]
            return {}
        raise AssertionError((method, path))

    call_once = call


def run(page, live=True, now=NOW):
    return header.run(page, "dcc", now, live, lambda code: NotionError("DCC_" + code))


class TextTests(unittest.TestCase):
    def test_the_line_names_the_day_and_the_time_in_central(self):
        self.assertEqual(header.text(NOW), LINE)
        self.assertEqual(header.text(datetime(2026, 10, 5, 9, 5, tzinfo=TZ)), "Monday, Oct 5 · Updated 9:05 AM CT")
        self.assertTrue(header.LINE.match(LINE))


class RunTests(unittest.TestCase):
    def test_the_first_live_run_creates_the_line_at_the_very_top_and_proves_it(self):
        page = FakePage([{"id": "db", "type": "child_database"}, para("x", "Calendar (Updated 1:05 PM CT)")])
        self.assertEqual(run(page), "created")
        self.assertEqual([b["id"] for b in page.top][1:], ["db", "x"])                                  # nothing else moved
        self.assertEqual(header.LINE.match(page.top[0]["paragraph"]["rich_text"][0]["plain_text"]) is not None, True)

    def test_later_runs_update_that_one_block_and_a_same_minute_run_writes_nothing(self):
        page = FakePage([para("h", "Monday, Oct 12 · Updated 1:15 PM CT"), {"id": "db", "type": "child_database"}])
        self.assertEqual(run(page), "updated")
        self.assertEqual((len(page.top), page.top[0]["id"], page.top[0]["paragraph"]["rich_text"][0]["plain_text"]), (2, "h", LINE))
        before = list(page.log)
        self.assertEqual(run(page), "unchanged")
        self.assertNotIn("PATCH", page.log[len(before):])

    def test_a_dry_run_reads_and_writes_nothing(self):
        page = FakePage([{"id": "db", "type": "child_database"}])
        self.assertEqual(run(page, live=False), "would_create")
        self.assertEqual((page.log.count("PATCH"), len(page.top)), (0, 1))
        page = FakePage([para("h", "Monday, Oct 12 · Updated 1:15 PM CT")])
        self.assertEqual(run(page, live=False), "would_update")
        self.assertEqual(page.log.count("PATCH"), 0)

    def test_a_notion_that_ignores_the_position_leaves_no_copy_and_the_next_run_does_not_add_another(self):
        page = FakePage([{"id": "db", "type": "child_database"}], honours_position=False)
        with self.assertRaises(NotionError) as error:
            run(page)
        self.assertEqual(str(error.exception), "DCC_HEADER_VERIFY_FAILED")
        self.assertEqual([b["id"] for b in page.top], ["db"])                                           # the stray block was taken back out
        with self.assertRaises(NotionError):
            run(page)
        self.assertEqual(len(page.top), 1)

    def test_a_refused_create_raises_a_fixed_code_and_changes_nothing(self):
        page = FakePage([{"id": "db", "type": "child_database"}], refuse_create=True)
        with self.assertRaises(NotionError):
            run(page)
        self.assertEqual(len(page.top), 1)

    def test_two_lines_or_a_line_below_the_top_stop_the_write(self):
        two = FakePage([para("a", LINE), para("b", "Monday, Oct 12 · Updated 1:00 PM CT")])
        with self.assertRaises(NotionError) as error:
            run(two)
        self.assertEqual(str(error.exception), "DCC_HEADER_AMBIGUOUS")
        low = FakePage([{"id": f"b{i}", "type": "child_database"} for i in range(4)] + [para("h", LINE)])
        with self.assertRaises(NotionError) as error:
            run(low)
        self.assertEqual(str(error.exception), "DCC_HEADER_MISPLACED")
        self.assertEqual(low.log.count("PATCH"), 0)

    def test_text_that_only_looks_like_the_line_is_not_taken_for_it(self):
        page = FakePage([para("n", "Notes · Updated 2:15 PM CT"), {"id": "db", "type": "child_database"}])
        self.assertEqual(run(page), "created")
        self.assertEqual(page.top[1]["id"], "n")


class CardTests(unittest.TestCase):
    def test_a_header_that_cannot_be_written_never_changes_the_command_center_result(self):
        from tests.dcc import test_card
        notion = test_card.page()
        counts = test_card.go(notion)                        # this fake page has no top level to find: the header says why, the card still wrote
        self.assertNotIn(counts["header"], ("created", "updated", "unchanged"))
        self.assertEqual((counts["status"], counts["blocks_written"]), ("fresh", 5))


if __name__ == "__main__":
    unittest.main()
