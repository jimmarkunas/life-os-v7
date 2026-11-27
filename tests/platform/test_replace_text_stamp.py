"""a card whose body write fails after its heading stamp was written keeps the OLD stamp (and the old body), never a NEW
stamp over OLD content.
"""

import copy
import unittest

from lifeos.amazon import card as amazon_card
from lifeos.platform import report_region
from lifeos.platform.notion_client import NotionError
from tests.amazon.test_card import ENV, NOW, ROWS, Reader
from tests.kit.notion import AmazonRegions
from tests.platform.test_report_region_in_place import Fail, Watched, write

STAMP, OLD_STAMP = "Mail Alerts (Updated 3:30 PM CT)", "Mail Alerts"
WRITES = ("PATCH", "POST", "DELETE", "PUT", "APPEND")


class FailsAtWrite:
    """Counts the write requests (appends included) and refuses the `n`th with a Notion conflict"""

    def __init__(self, inner, n, refuse_restore=False):
        self.inner, self.n, self.refuse_restore, self.writes, self.heading_writes = inner, n, refuse_restore, 0, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _write(self, method, path, body):
        self.writes += 1
        heading = "/blocks/heading-" in path and method == "PATCH"
        if heading:
            kind = next(iter(body))
            self.heading_writes.append(body[kind]["rich_text"][0]["text"]["content"])
        if self.writes == self.n:
            raise NotionError("NOTION_HTTP_409")
        if heading and self.refuse_restore and self.writes > self.n:
            raise NotionError("NOTION_HTTP_429")

    def call(self, method, path, body=None):
        if method in WRITES:
            self._write(method, path, body)
        return self.inner.call(method, path, body)

    def call_once(self, method, path, body=None, *args, **kwargs):
        self._write("APPEND", path, body)
        return self.inner.call_once(method, path, body)


class DropsBodyWrites:
    """Accepts every write but applies only the heading's: the body read-back then cannot match what was written."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def call(self, method, path, body=None):
        if method in ("PATCH", "DELETE") and "/blocks/heading-" not in path:
            return {}
        return self.inner.call(method, path, body)

    def call_once(self, method, path, body=None, *args, **kwargs):
        return {"results": [{}] * len((body or {}).get("children", []))}


def heading_of(notion):
    return report_region.plain(notion.children["mail-callout"][0])


def body_of(notion):
    return [report_region.plain(b) for b in notion.children["mail-callout"][1:-1]]


def insert_path(notion):
    """The first old line changes block type, so the write takes the insert-then-delete path instead of editing in place."""
    kid = notion.children["mail-callout"][1]
    kid["type"] = "bulleted_list_item"
    kid["bulleted_list_item"] = kid.pop("paragraph")
    return notion


class FailureAfterTheStamp(unittest.TestCase):
    def test_a_body_write_refused_after_the_heading_write_keeps_the_old_stamp_and_the_old_body(self):
        for name, notion in (
            ("in place", Watched(["old status", "old detail"])),
            ("insert", insert_path(Watched(["old status", "old detail"]))),
        ):
            client = FailsAtWrite(notion, 2)
            with self.assertRaises(NotionError) as raised:
                write(client, ["new status", "new detail"], heading=STAMP)
            self.assertEqual(
                str(raised.exception), "NOTION_HTTP_409", name
            )  # the failure the card already reported, unchanged
            self.assertEqual((heading_of(notion), body_of(notion)), (OLD_STAMP, ["old status", "old detail"]), name)
            self.assertEqual(
                client.heading_writes, [STAMP, OLD_STAMP], name
            )  # the stamp was written, then put back once

    def test_a_later_failure_also_puts_the_old_stamp_back(self):
        notion = Watched(["old status", "old detail"])
        with self.assertRaises(NotionError):
            write(FailsAtWrite(notion, 3), ["new status", "new detail"], heading=STAMP)
        self.assertEqual(heading_of(notion), OLD_STAMP)
        self.assertEqual(body_of(notion), ["new status", "old detail"])

    def test_a_read_back_that_does_not_match_puts_the_old_stamp_back_and_keeps_its_fixed_code(self):
        notion = Watched(["old status"])
        with self.assertRaises(Fail) as raised:
            write(DropsBodyWrites(notion), ["new status"], heading=STAMP)
        self.assertEqual(str(raised.exception), "CARD_VERIFY_FAILED")
        self.assertEqual((heading_of(notion), body_of(notion)), (OLD_STAMP, ["old status"]))

    def test_if_the_restore_is_refused_too_the_caller_still_sees_the_original_failure(self):
        notion = Watched(["old status"])
        client = FailsAtWrite(notion, 2, refuse_restore=True)
        with self.assertRaises(NotionError) as raised:
            write(client, ["new status"], heading=STAMP)
        self.assertEqual(str(raised.exception), "NOTION_HTTP_409")  # not the restore's 429
        self.assertEqual(client.heading_writes, [STAMP, OLD_STAMP])  # it tried once, best effort, and said nothing more


class NothingElseChanges(unittest.TestCase):
    def test_when_every_write_succeeds_the_requests_and_the_card_are_exactly_what_they_were(self):
        """The logs are what the code sent before (captured from main): the same requests in the same order, so no
        request is added.
        """
        notion = Watched(["old status", "old detail"])
        write(notion, ["new status", "new detail"], heading=STAMP)
        self.assertEqual(
            [(m, p.split("?")[0]) for m, p in notion.log],
            [
                ("GET", "/blocks/mail-callout"),
                ("GET", "/blocks/mail-callout/children"),
                ("PATCH", "/blocks/heading-mail-callout"),
                ("PATCH", "/blocks/mail-old-0"),
                ("PATCH", "/blocks/mail-old-1"),
                ("GET", "/blocks/mail-callout/children"),
            ],
        )
        self.assertEqual(
            [(b["id"], b["type"], report_region.plain(b)) for b in notion.children["mail-callout"]],
            [
                ("heading-mail-callout", "heading_3", STAMP),
                ("mail-old-0", "paragraph", "new status"),
                ("mail-old-1", "paragraph", "new detail"),
                ("mail-table-old", "table", ""),
            ],
        )
        notion = insert_path(Watched(["old status", "old detail"]))
        write(notion, ["new status", "new detail"], heading=STAMP)
        self.assertEqual(
            [(m, p.split("?")[0]) for m, p in notion.log],
            [
                ("GET", "/blocks/mail-callout"),
                ("GET", "/blocks/mail-callout/children"),
                ("PATCH", "/blocks/heading-mail-callout"),
                ("APPEND", "/blocks/mail-callout/children"),
                ("DELETE", "/blocks/mail-old-0"),
                ("DELETE", "/blocks/mail-old-1"),
                ("GET", "/blocks/mail-callout/children"),
            ],
        )
        self.assertEqual(
            [(b["id"], b["type"], report_region.plain(b)) for b in notion.children["mail-callout"]],
            [
                ("heading-mail-callout", "heading_3", STAMP),
                ("agenda-4", "paragraph", "new status"),
                ("agenda-5", "paragraph", "new detail"),
                ("mail-table-old", "table", ""),
            ],
        )

    def test_a_write_refused_on_its_first_request_is_unchanged_and_sends_no_restore(self):
        notion = Watched(["old status"])
        client = FailsAtWrite(notion, 1)
        with self.assertRaises(NotionError):
            write(client, ["new status"], heading=STAMP)
        self.assertEqual(
            (heading_of(notion), body_of(notion), client.heading_writes), (OLD_STAMP, ["old status"], [STAMP])
        )

    def test_a_card_whose_heading_needed_no_write_sends_no_restore_when_its_body_fails(self):
        notion = Watched(["old status"])
        write(notion, ["new status"], heading=STAMP)  # the card already shows this stamp
        client = FailsAtWrite(notion, 1)
        with self.assertRaises(NotionError):
            write(client, ["newer status"], heading=STAMP)  # the first write is a body write
        self.assertEqual((heading_of(notion), body_of(notion), client.heading_writes), (STAMP, ["new status"], []))

    def test_a_card_with_no_stamp_sends_no_restore(self):
        notion = Watched(["old status"])
        client = FailsAtWrite(notion, 1)
        with self.assertRaises(NotionError):
            write(client, ["new status"])
        self.assertEqual((heading_of(notion), body_of(notion), client.heading_writes), (OLD_STAMP, ["old status"], []))


class ThroughARealCard(unittest.TestCase):
    def test_the_amazon_card_keeps_its_old_stamp_and_its_old_content_and_its_failure_code_when_its_body_write_fails(
        self,
    ):
        notion = AmazonRegions()
        others = ("calendar-callout", "jira-callout", "dcc-callout", "bills-callout")
        before = {k: copy.deepcopy(notion.full_tree(k)) for k in others}
        card_before = [(b["id"], b["type"], report_region.plain(b)) for b in notion.children["amazon-callout"]]
        client = FailsAtWrite(notion, 2)
        with self.assertRaises(NotionError) as raised:
            amazon_card.run(0, True, environ=ENV, reader=Reader(ROWS), client=client, now=NOW)
        self.assertEqual(str(raised.exception), "NOTION_HTTP_409")  # the failure code the card already reported
        self.assertEqual(
            [(b["id"], b["type"], report_region.plain(b)) for b in notion.children["amazon-callout"]], card_before
        )  # the Amazon card: old heading text and old body
        self.assertEqual(report_region.plain(notion.children["amazon-callout"][0]), "Amazon Orders")
        self.assertEqual({k: notion.full_tree(k) for k in others}, before)  # every other region exactly as before
        self.assertEqual(client.heading_writes, ["Amazon Orders (10/4 · 1:30 PM CT)", "Amazon Orders"])


if __name__ == "__main__":
    unittest.main()
