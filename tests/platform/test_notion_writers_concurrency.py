"""LIFE-688: two cards writing different regions of the Daily Report at the same moment both succeed (no 409, no 404, nothing aimed at the page itself); a failed card write keeps the last accepted card.

`ConflictingNotion` simulates Notion's conflict behavior: a request that touches a region while another request is touching it answers 409; a request aimed at the page itself while any other request is in flight answers 409
(page-level edits collide with everything); a request for a block that no longer exists answers 404. The negative controls prove the simulation bites."""
import copy
import threading
import time
import unittest
from unittest import mock

from lifeos.platform import report_region, router
from lifeos.platform.notion_client import NotionError
from tests.kit.notion import MailRegions


class Fail(Exception):
    pass


def para(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


class ConflictingNotion:
    def __init__(self, inner, hold=0.002):
        self.inner, self.hold = inner, hold
        self.busy, self.touched, self.conflicts, self.guard, self.data = {}, [], [], threading.Lock(), threading.Lock()

    def _region(self, path):
        parts = path.split("?", 1)[0].split("/")
        block = parts[2] if len(parts) > 2 else ""
        if block == self.inner.page_id:
            return "page"
        if block in self.inner.metas:
            return block
        for root, blocks in self.inner.children.items():
            if any(b["id"] == block for b in blocks):
                return root
        return None

    def _run(self, fn, method, path):
        region, me = self._region(path), threading.current_thread().name
        if region is None:
            raise NotionError("NOTION_HTTP_404")                                    # the block was deleted since it was listed
        with self.guard:
            others = {r: t for r, t in self.busy.items() if t != me}
            if region in others or (region == "page" and others) or "page" in others:
                self.conflicts.append((me, region, method))
                raise NotionError("NOTION_HTTP_409")
            self.busy[region] = me
            self.touched.append((me, region))
        try:
            time.sleep(self.hold)                                                    # long enough for another thread to arrive
            with self.data:
                return fn()
        finally:
            with self.guard:
                self.busy.pop(region, None)

    def call(self, method, path, body=None):
        return self._run(lambda: self.inner.call(method, path, body), method, path)

    def call_once(self, method, path, body=None, *args, **kwargs):
        return self._run(lambda: self.inner.call_once(method, path, body), method, path)


def write(client, block_id, title, module, lines, heading=None):
    return report_region.replace_text(client, block_id, title, module, [para(t) for t in lines], {"x": "unused"}, Fail, "test", True, heading=heading)


def both_write(rounds, first, second):
    """Two threads, aligned at the start of every round so their requests overlap; -> the exceptions each raised."""
    barrier, errors = threading.Barrier(2), []

    def runner(work):
        for n in range(rounds):
            barrier.wait()
            try:
                work(n)
            except Exception as error:                                              # noqa: BLE001 - the test reports whatever a thread raised
                errors.append(repr(error))
    threads = [threading.Thread(target=runner, args=(work,), name=name) for name, work in (("mail-job", first), ("agenda-job", second))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return errors


class TwoCardsAtTheSameMoment(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Jira": "same"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.notion = ConflictingNotion(MailRegions())

    def test_two_cards_writing_different_regions_both_succeed_with_no_409_or_404(self):
        mail = lambda n: write(self.notion, "mail-callout", "Mail Alerts", router.OWNERS[router.MAIL_ALERTS_REGION], [f"mail status {n}", f"mail detail {n}"])             # noqa: E731
        calendar = lambda n: write(self.notion, "calendar-callout", "Calendar", router.OWNERS[router.CALENDAR_REGION], [f"calendar line {n}"])                              # noqa: E731
        errors = both_write(15, mail, calendar)
        self.assertEqual((errors, self.notion.conflicts), ([], []))
        regions = {}
        for thread, region in self.notion.touched:
            regions.setdefault(thread, set()).add(region)
        self.assertEqual(regions, {"mail-job": {"mail-callout"}, "agenda-job": {"calendar-callout"}})        # each card touched only its own callout, never the page
        self.assertEqual([report_region.plain(b) for b in self.notion.inner.children["mail-callout"][1:3]], ["mail status 14", "mail detail 14"])
        self.assertEqual(report_region.plain(self.notion.inner.children["calendar-callout"][1]), "calendar line 14")

    def test_the_simulation_bites_two_writers_on_one_region_collide(self):
        one = lambda n: write(self.notion, "mail-callout", "Mail Alerts", router.OWNERS[router.MAIL_ALERTS_REGION], [f"first {n}"])                                         # noqa: E731
        two = lambda n: write(self.notion, "mail-callout", "Mail Alerts", router.OWNERS[router.MAIL_ALERTS_REGION], [f"second {n}"])                                        # noqa: E731
        errors = both_write(10, one, two)
        self.assertTrue(self.notion.conflicts and errors)
        self.assertTrue(all("NOTION_HTTP_409" in e for e in errors))

    def test_the_simulation_bites_a_whole_page_walk_beside_a_card_write_collides(self):
        walk = lambda n: self.notion.call("GET", f"/blocks/{self.notion.inner.page_id}/children?page_size=100")                                                               # noqa: E731
        mail = lambda n: write(self.notion, "mail-callout", "Mail Alerts", router.OWNERS[router.MAIL_ALERTS_REGION], [f"mail status {n}"])                                   # noqa: E731
        errors = both_write(10, mail, walk)
        self.assertTrue(self.notion.conflicts and errors)

    def test_a_block_deleted_since_it_was_listed_is_a_404(self):
        with self.assertRaises(NotionError) as raised:
            self.notion.call("GET", "/blocks/block-that-was-deleted")
        self.assertEqual(str(raised.exception), "NOTION_HTTP_404")


class FailsOnItsFirstWrite:
    """Refuses the first write request it sees the way Notion answers a conflict, then behaves normally."""
    WRITES = ("PATCH", "POST", "DELETE", "PUT")

    def __init__(self, inner):
        self.inner, self.refused = inner, False

    def call(self, method, path, body=None):
        if method in self.WRITES and not self.refused:
            self.refused = True
            raise NotionError("NOTION_HTTP_409")
        return self.inner.call(method, path, body)

    def call_once(self, method, path, body=None, *args, **kwargs):
        if not self.refused:
            self.refused = True
            raise NotionError("NOTION_HTTP_409")
        return self.inner.call_once(method, path, body)


class AFailedWriteKeepsTheLastAcceptedCard(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Jira": "same"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_write_refused_on_its_first_request_leaves_the_old_content_and_the_old_stamp(self):
        for heading in (None, "Mail Alerts (Updated 3:30 PM CT)"):
            notion = MailRegions()
            before = copy.deepcopy(notion.children["mail-callout"])
            with self.assertRaises(NotionError):
                write(FailsOnItsFirstWrite(notion), "mail-callout", "Mail Alerts", router.OWNERS[router.MAIL_ALERTS_REGION], ["a new status"], heading=heading)
            self.assertEqual(notion.children["mail-callout"], before, heading)                         # the heading (the stamp) and every line are exactly as accepted before
            self.assertEqual(report_region.plain(notion.children["mail-callout"][0]), "Mail Alerts")


if __name__ == "__main__":
    unittest.main()
