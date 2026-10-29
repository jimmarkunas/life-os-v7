"""The two-column Ledger page (lifeos.platform.ledger_body) with the Network Leads block in its right column. Invented people, companies and ids only."""
import unittest

from lifeos.network import surface
from lifeos.platform import hidden, ledger_body as body
from tests.kit.ledger_pages import FakeLedger
from tests.network.test_surface import KEY1, build, go

DESC = {"summary": "About the role\n• lead the program", "responsibilities": "Own the roadmap", "requirements": "", "qualifications": "Ten years"}
PROPS = {"Stable Job Key": {"rich_text": [{"text": {"content": KEY1}}]}}


def page():
    ledger = FakeLedger()
    pid = ledger.create(PROPS, body.build(KEY1, DESC))
    body.fill(ledger, pid, DESC)
    return ledger, pid


def texts(ledger, block_id):
    return ["".join(r["plain_text"] for r in ledger.blocks[k][ledger.blocks[k]["type"]]["rich_text"]) for k in ledger.kids[block_id]]


class Layout(unittest.TestCase):
    def test_the_page_is_a_hidden_marker_then_two_columns_with_the_description_in_the_left_one(self):
        ledger, pid = page()
        top = ledger.top(pid)
        self.assertEqual([b["type"] for b in top], ["paragraph", "column_list"])
        self.assertEqual(body.marker_text(top[0]), f"v7-jd:2 | key={KEY1}")
        self.assertEqual("".join(ledger.texts(pid)[:1]), "\u2060")                                        # nothing readable on the first line
        left, right = ledger.kids[top[1]["id"]]
        self.assertEqual([ledger.blocks[k]["type"] for k in ledger.kids[left]], ["heading_2", "paragraph", "bulleted_list_item", "toggle"])
        self.assertEqual(texts(ledger, left)[0], "Overview")
        toggle = ledger.kids[left][-1]
        self.assertEqual(texts(ledger, toggle), ["Responsibilities", "Own the roadmap", "Qualifications", "Ten years"])      # Summary is the Overview; empty sections are not written
        self.assertEqual(len(ledger.kids[right]), 1)

    def test_fill_writes_once_and_a_replay_writes_nothing(self):
        ledger, pid = page()
        before = len(ledger.writes)
        self.assertEqual(body.fill(ledger, pid, DESC), 0)
        self.assertEqual(len(ledger.writes), before)

    def test_fill_on_an_older_flat_page_is_nothing_when_not_strict_and_a_refusal_when_strict(self):
        ledger = FakeLedger()
        ledger.add_page("old", KEY1)
        self.assertEqual(body.fill(ledger, "old", DESC, strict=False), 0)
        with self.assertRaises(Exception):
            body.fill(ledger, "old", DESC)

    def test_either_layout_reads_back_as_the_same_description_text(self):
        ledger, pid = page()
        self.assertEqual(body.text_of(ledger, pid), "About the role\n- lead the program\nResponsibilities\nOwn the roadmap\nQualifications\nTen years")
        flat = FakeLedger()
        flat.add_page("old", KEY1)
        self.assertEqual(body.text_of(flat, "old"), "Description\nInvented job description.")

    def test_the_guard_accepts_the_hidden_marker_and_the_visible_one(self):
        ledger, pid = page()
        flat = FakeLedger()
        flat.add_page("old", KEY1)
        for l, p in ((ledger, pid), (flat, "old")):
            top = surface.Top(l.top(p))
            self.assertTrue(body.is_marker(top[0], KEY1))
            self.assertFalse(body.is_marker(top[0], "z" * 64))


class Leads(unittest.TestCase):
    def world(self):
        ledger, pid = page()
        return ledger, pid, build(jobs=[(KEY1, pid, "Acme Corp", "Product Director", "ADMIT")])

    def test_the_block_goes_into_the_right_column_replaces_the_blank_and_a_replay_writes_nothing(self):
        ledger, pid, conn = self.world()
        counts = go(conn, ledger, live=True)
        self.assertEqual(counts["blocks"], {"created": 1})
        right = ledger.right(pid)
        self.assertEqual([ledger.blocks[k]["type"] for k in ledger.kids[right]], ["callout"])             # the placeholder blank paragraph is gone
        self.assertEqual([b["type"] for b in ledger.top(pid)], ["paragraph", "column_list"])             # nothing else on the page moved
        self.assertEqual(surface.leads_on_page(ledger, pid)[0]["name"], "Invented Person1")
        ledger.writes.clear()
        self.assertEqual((go(conn, ledger, live=True)["blocks"], ledger.writes), ({"unchanged": 1}, []))

    def test_a_dismiss_tick_in_the_right_column_is_read_persisted_and_the_block_replaced(self):
        ledger, pid, conn = self.world()
        go(conn, ledger, live=True)
        ledger.tick(pid, "Closed 1:")
        counts = go(conn, ledger, live=True)
        self.assertEqual((counts["ticks_dismiss"], counts["blocks"]), (1, {"replaced": 1}))
        self.assertEqual([ledger.blocks[k]["type"] for k in ledger.kids[ledger.right(pid)]], ["callout"])
        self.assertEqual([x["person_id"] for x in surface.leads_on_page(ledger, pid)], [2])

    def test_removing_the_block_leaves_the_column_with_one_blank_paragraph(self):
        ledger, pid, conn = self.world()
        go(conn, ledger, live=True)
        conn.raw.execute("UPDATE v7_job_fit SET admission='EXCLUDE'")
        self.assertEqual(go(conn, ledger, live=True)["blocks"], {"removed": 1})
        kids = [ledger.blocks[k] for k in ledger.kids[ledger.right(pid)]]
        self.assertEqual(([k["type"] for k in kids], "".join(r["plain_text"] for r in kids[0]["paragraph"]["rich_text"])), (["paragraph"], ""))


class Migration(unittest.TestCase):
    def test_a_page_with_leads_on_the_flat_layout_gets_them_moved_into_the_right_column_after_the_migration(self):
        from lifeos.sources import ledger_layout as layout
        ledger = FakeLedger()
        ledger.add_page("page-1", KEY1, description=False)
        ledger.kids["page-1"] = [ledger._new(b["type"], b[b["type"]]["rich_text"]) for b in body.legacy_blocks(KEY1, DESC)]
        conn = build(jobs=[(KEY1, "page-1", "Acme Corp", "Product Director", "ADMIT")])
        go(conn, ledger, live=True)                                                                        # the block lands at the top level of the flat page
        self.assertEqual([b["type"] for b in ledger.top("page-1")][-1], "callout")
        self.assertEqual(layout.migrate(ledger, "page-1", KEY1, DESC, True), "migrated")
        self.assertEqual(sorted(b["type"] for b in ledger.top("page-1")), ["callout", "column_list", "paragraph"])      # the old block is still there until the surface runs
        counts = go(conn, ledger, live=True)
        self.assertEqual(counts["blocks"], {"replaced": 1})
        self.assertEqual(sorted(b["type"] for b in ledger.top("page-1")), ["column_list", "paragraph"])
        self.assertEqual([ledger.blocks[k]["type"] for k in ledger.kids[ledger.right("page-1")]], ["callout"])
        self.assertEqual(go(conn, ledger, live=True)["blocks"], {"unchanged": 1})


if __name__ == "__main__":
    unittest.main()


class FewerCalls(unittest.TestCase):
    """Jim, Oct 28: the card writer takes Notion's own reply for the appended block, and retries only an append Notion refused with 429."""

    def test_the_appended_block_id_comes_from_notions_reply_without_a_read(self):
        from lifeos.network import surface

        class Reply:
            calls = []

            def call_once(self, method, path, body=None):
                self.calls.append(method)
                return {"results": [{"id": "new1", "type": "callout"}]}

            def call(self, method, path, body=None):
                raise AssertionError("no read is needed")

        client = Reply()
        self.assertEqual(surface._append_last(client, "parent", {"type": "callout", "callout": {}}), "new1")
        self.assertEqual(client.calls, ["PATCH"])

    def test_only_a_429_refusal_is_retried(self):
        from lifeos.network import surface
        from lifeos.platform.notion_client import NotionError
        answers = [NotionError("NOTION_HTTP_429"), {"results": []}]

        class Busy:
            def call_once(self, method, path, body=None):
                answer = answers.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer

        self.assertEqual(surface._write(Busy(), "/blocks/x/children", {}, sleep=lambda s: None), {"results": []})
        answers[:] = [NotionError("NOTION_HTTP_400")]
        with self.assertRaises(NotionError):
            surface._write(Busy(), "/blocks/x/children", {}, sleep=lambda s: None)


class CutShortWrite(unittest.TestCase):
    """A run cancelled mid-write leaves a block whose cards are empty: the next read skips those cards instead of failing the page, so the page is rebuilt."""

    def test_an_unfinished_card_is_skipped_not_fatal(self):
        from lifeos.network import surface

        class Half:
            def call(self, method, path, body=None):
                return {"results": [], "has_more": False}                # the card holds no column_list yet

        self.assertEqual(surface._cards(Half(), [{"id": "c1", "type": "callout"}]), [])
