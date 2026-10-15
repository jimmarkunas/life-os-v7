"""report_region.replace_text rewrites a card's text lines in place (Jim, Oct 15 2026): the page never shows the old and the new card at once."""
import unittest
from unittest import mock

from lifeos.platform import report_region, router
from tests.kit.notion import MailRegions


class Fail(Exception):
    pass


def para(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


class Watched(MailRegions):
    """MailRegions that records how many text lines the Mail callout holds after every write."""

    def __init__(self, old_lines, **kw):
        super().__init__(**kw)
        kids = self.children["mail-callout"]
        kids[1:-1] = [self._text_block(f"mail-old-{i}", "paragraph", t) for i, t in enumerate(old_lines)]
        self.peaks = []

    def _count(self):
        self.peaks.append(sum(k["type"] in report_region.TEXT_KINDS for k in self.children["mail-callout"]))

    def call(self, method, path, body=None):
        out = super().call(method, path, body)
        if method in ("PATCH", "DELETE"):
            self._count()
        return out

    def call_once(self, method, path, body=None):
        out = super().call_once(method, path, body)
        self._count()
        return out


def write(notion, lines):
    with mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Jira": "same"}):
        return report_region.replace_text(notion, "mail-callout", router.MAIL_ALERTS_REGION, router.OWNERS[router.MAIL_ALERTS_REGION],
                                          [para(t) for t in lines], {"x": "unused"}, Fail, "test", True)


class InPlace(unittest.TestCase):
    def kids(self, notion):
        return notion.children["mail-callout"]

    def test_same_count_rewrites_the_same_blocks_and_deletes_nothing(self):
        notion = Watched(["old status", "old detail"])
        _, removed = write(notion, ["new status", "new detail"])
        self.assertEqual([k["id"] for k in self.kids(notion)], ["heading-mail-callout", "mail-old-0", "mail-old-1", "mail-table-old"])
        self.assertEqual([report_region.plain(k) for k in self.kids(notion)[1:3]], ["new status", "new detail"])
        self.assertEqual((removed, [m for m, _ in notion.log if m in ("DELETE", "APPEND")]), (0, []))
        self.assertEqual(max(notion.peaks), 2)                                                  # never more lines than either version

    def test_more_lines_go_right_under_the_rewritten_ones_above_the_kept_block(self):
        notion = Watched(["old status"])
        write(notion, ["new status", "line two", "line three"])
        self.assertEqual([report_region.plain(k) for k in self.kids(notion)[1:4]], ["new status", "line two", "line three"])
        self.assertEqual((self.kids(notion)[1]["id"], self.kids(notion)[-1]["id"]), ("mail-old-0", "mail-table-old"))
        self.assertEqual(max(notion.peaks), 3)

    def test_fewer_lines_remove_only_the_surplus(self):
        notion = Watched(["old status", "old detail", "old extra"])
        _, removed = write(notion, ["new status"])
        self.assertEqual([k["id"] for k in self.kids(notion)], ["heading-mail-callout", "mail-old-0", "mail-table-old"])
        self.assertEqual(removed, 2)
        self.assertEqual(max(notion.peaks), 3)

    def test_a_changed_block_type_falls_back_to_insert_then_delete(self):
        notion = Watched(["old status"])
        notion.children["mail-callout"][1]["type"] = "bulleted_list_item"
        notion.children["mail-callout"][1]["bulleted_list_item"] = notion.children["mail-callout"][1].pop("paragraph")
        write(notion, ["new status"])
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], ["APPEND", "DELETE"])
        self.assertEqual([report_region.plain(k) for k in self.kids(notion)[1:-1]], ["new status"])


if __name__ == "__main__":
    unittest.main()
