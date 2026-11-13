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


def write(notion, lines, **kw):
    with mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Jira": "same"}):
        return report_region.replace_text(notion, "mail-callout", router.MAIL_ALERTS_REGION, router.OWNERS[router.MAIL_ALERTS_REGION],
                                          [para(t) for t in lines], {"x": "unused"}, Fail, "test", True, **kw)


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


class StampedHeading(unittest.TestCase):
    """LIFE-609: a card may show its update time in its heading (`Mail Alerts (Updated 2:30 PM CT)`), the way Calendar, Jira and Hiring do."""
    STAMPED = "Mail Alerts (Updated 2:30 PM CT)"

    def heading(self, notion):
        return report_region.plain(notion.children["mail-callout"][0])

    def test_the_heading_is_set_in_the_same_block_and_read_back_on_the_in_place_path_and_the_insert_path(self):
        for retype in (False, True):
            notion = Watched(["old status"])
            if retype:
                notion.children["mail-callout"][1]["type"] = "bulleted_list_item"
                notion.children["mail-callout"][1]["bulleted_list_item"] = notion.children["mail-callout"][1].pop("paragraph")
            write(notion, ["new status"], heading=self.STAMPED)
            self.assertEqual((self.heading(notion), notion.children["mail-callout"][0]["id"]), (self.STAMPED, "heading-mail-callout"))
            self.assertEqual([report_region.plain(k) for k in notion.children["mail-callout"][1:-1]], ["new status"])

    def test_an_already_stamped_heading_is_accepted_and_a_later_run_rewrites_only_the_stamp(self):
        notion = Watched(["old status"])
        write(notion, ["new status"], heading=self.STAMPED)
        mark = len(notion.log)
        write(notion, ["new status"], heading="Mail Alerts (Updated 3:30 PM CT)")
        self.assertEqual(self.heading(notion), "Mail Alerts (Updated 3:30 PM CT)")
        self.assertEqual({p.split("/")[2] for m, p in notion.log[mark:] if m == "PATCH"}, {"heading-mail-callout", "mail-old-0"})
        write(notion, ["new status"], heading="Mail Alerts (Updated 3:30 PM CT)")                      # nothing to change in the heading: it is not written again
        self.assertEqual(self.heading(notion), "Mail Alerts (Updated 3:30 PM CT)")

    def test_without_a_heading_the_heading_must_still_be_exactly_the_title_and_a_wrong_title_is_refused(self):
        notion = Watched(["old status"])
        write(notion, ["new status"])
        self.assertEqual(self.heading(notion), "Mail Alerts")
        notion.children["mail-callout"][0] = notion._text_block("heading-mail-callout", "heading_3", self.STAMPED)
        with self.assertRaises(Fail):
            write(notion, ["new status"])                                                               # a stamped heading is not accepted unless the caller states it
        for bad in ("Amazon Orders (Updated 2:30 PM CT)", "Mail Alerts Updated 2:30 PM CT", "Mail Alerts"[:-1] + " (Updated 2:30 PM CT)"):
            fresh = Watched(["old status"])
            with self.assertRaises(Fail):
                write(fresh, ["new status"], heading=bad)
            self.assertEqual(self.heading(fresh), "Mail Alerts")                                        # refused before any write


if __name__ == "__main__":
    unittest.main()
