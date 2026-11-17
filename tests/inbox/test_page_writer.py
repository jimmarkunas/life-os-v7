"""LIFE-711: the Action Inbox page writer fills Jim's QA9 page blocks from stored Followup payloads, writes only below its marker, and is idempotent."""
import copy
import unittest
from datetime import datetime, timezone

from lifeos.inbox import page_view, page_writer
from lifeos.inbox.model import InboxError

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)
OK = [{"source": s, "status": "ok", "reason": "ok", "last_accepted": None} for s in ("jira", "calendar", "gmail", "todoist")]


def rich(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


class FakeNotion:
    """An in-memory block tree with the three calls the writer uses; records every id it was asked to change."""

    def __init__(self):
        self.blocks, self.kids, self.touched, self.n = {}, {}, [], 0
        self.page = self._add(None, "page", {})
        self.marker = self.top("paragraph", rich(page_writer.MARKER))
        self.header = self.top("callout", [], [("heading_3", rich("Action Inbox")), ("paragraph", rich("Filters")), ("paragraph", rich("Last refreshed: waiting"))])
        cols = self.top("column_list", None)
        for name in ("Needs me", "Waiting", "Approvals", "Upcoming", "Sources healthy"):
            col = self._add(cols, "column", {})
            self.child(self._add(col, "callout", {"rich_text": []}), [("heading_3", rich("—")), ("paragraph", rich(name)), ("paragraph", rich("Not read yet"))])
        self.main = self.table(7, "Workspace Source Bucket Outcome Owner Next Freshness".split(), ["—", "—", "—", "No rows yet", "—", "—", "—"])
        self.top("paragraph", rich("Every row shows its workspace label."))
        self.top("divider", None)
        self.health = self.top("callout", [], [("heading_3", rich("Source health")), ("paragraph", rich("Jira not read yet")), ("paragraph", rich("A source that cannot be read..."))])
        self.top("divider", None)
        self.top("callout", rich("How Followup maps in."))
        self.followup = self.table(6, "Workspace Loop Owner Move Checkpoint Freshness".split(), ["—", "No Followup rows yet", "—", "—", "—", "—"])
        self.review = self.top("paragraph", rich("Review Needed (not counted): —"))
        self.touched = []

    def _add(self, parent, kind, body):
        self.n += 1
        block_id = "p" * 32 if parent is None else f"b{self.n:031d}"
        self.blocks[block_id] = {"id": block_id, "type": kind, kind: body}
        self.kids[block_id] = []
        if parent:
            self.kids[parent].append(block_id)
            self.blocks[parent]["has_children"] = True
        return block_id

    def top(self, kind, text, kids=None):
        block_id = self._add(self.page, kind, {"rich_text": text} if text is not None else {})
        if kids:
            self.child(block_id, kids)
        return block_id

    def child(self, parent, kids):
        for kind, text in kids:
            self._add(parent, kind, {"rich_text": text})

    def table(self, width, head, row):
        table = self.top("table", None)
        for cells in (head, row):
            block_id = self._add(table, "table_row", {"cells": [rich(c) for c in cells]})
            self.blocks[block_id]["has_children"] = False
        return table

    def plain(self, block_id):
        body = self.blocks[block_id][self.blocks[block_id]["type"]]
        return "".join(p["plain_text"] for p in body.get("rich_text", []))

    def rows(self, table):
        return [tuple("".join(p["plain_text"] for p in cell) for cell in self.blocks[i]["table_row"]["cells"]) for i in self.kids[table]][1:]

    @staticmethod
    def _pieces(pieces):
        return [{"type": "text", "plain_text": p["text"]["content"], "text": p["text"], "annotations": p.get("annotations", {})} for p in pieces]

    def call(self, method, path, body=None):
        if method == "POST" and path == "/search":
            return {"results": [{"id": self.page, "properties": {"title": {"title": [{"plain_text": "Agent Inbox"}]}}}]}
        path = path.split("?")[0]
        parts = path.strip("/").split("/")
        if method == "GET":
            return {"results": [copy.deepcopy(self.blocks[i]) for i in self.kids[parts[1]]], "has_more": False}
        self.touched.append(parts[1])
        if method == "DELETE":
            for ids in self.kids.values():
                if parts[1] in ids:
                    ids.remove(parts[1])
            return {}
        if method == "PATCH" and len(parts) == 3:
            for child in body["children"]:
                if child["type"] == "table_row":
                    new = self._add(parts[1], "table_row", {"cells": [self._pieces(cell) for cell in child["table_row"]["cells"]]})
                else:
                    new = self._add(parts[1], "callout", {"rich_text": self._pieces(child["callout"]["rich_text"])})
                    self.blocks[new]["callout"]["color"] = child["callout"]["color"]
            return {}
        kind = self.blocks[parts[1]]["type"]
        self.blocks[parts[1]][kind]["rich_text"] = self._pieces(body[kind]["rich_text"])
        return {}

    call_once = call


def payload(workspace="personal", account="personal inbox", threads=(), sources=OK, state="PASS", review=()):
    return {"component": "followup", "state": state, "workspace": workspace, "account": account, "generated_at": "2026-11-17T01:00:00+00:00", "last_accepted": None, "counts": {},
            "threads": list(threads), "review": list(review), "sources": [dict(s) for s in sources], "provenance": {}}


LOOP = {"id": "a" * 64, "title": "Send the revised quote", "state": "needs_action_today", "owner": "jim", "next_checkpoint": None, "notes": "checkpoint source-backed"}
BOTH = lambda **kw: [payload("personal", **kw), payload("bytalos", "bytalos inbox", **{k: v for k, v in kw.items() if k != "threads"})]


class PageWriterTests(unittest.TestCase):
    def test_a_live_payload_fills_the_blocks_and_the_second_run_writes_nothing(self):
        notion = FakeNotion()
        first = page_writer.publish(BOTH(threads=[LOOP]), NOW, client=notion)
        self.assertGreater(first["text_blocks_written"], 0)
        self.assertEqual(notion.rows(notion.main), [("Personal", "Followup", "Needs me", "Send the revised quote", "You", "Open the source record", "11/16 · 7:00 PM CT")])
        self.assertEqual(notion.rows(notion.followup)[0][:3], ("Personal", "Send the revised quote", "You"))
        self.assertIn("Source-backed", notion.rows(notion.followup)[0][4])
        self.assertEqual(notion.plain(notion.review), "Review Needed (not counted): 0")
        before = copy.deepcopy(notion.blocks)
        notion.touched.clear()
        again = page_writer.publish(BOTH(threads=[LOOP]), NOW, client=notion)
        self.assertEqual(notion.touched, [])
        self.assertEqual(again, {"text_blocks_written": 0, "table_rows_written": 0, "state_callouts_changed": 0, "unchanged": 1})
        self.assertEqual(notion.blocks, before)

    def test_tiles_show_a_number_only_when_every_connected_source_read_ok(self):
        notion = FakeNotion()
        page_writer.publish(BOTH(threads=[LOOP]), NOW, client=notion)
        tiles = [notion.plain(notion.kids[notion.kids[c][0]][0]) for c in notion.kids[[i for i, b in notion.blocks.items() if b["type"] == "column_list"][0]]]
        self.assertEqual(tiles, ["1", "0", "—", "0", "4 of 6"])
        down = [dict(OK[0], status="degraded", last_accepted="2026-11-17T00:00:00+00:00")] + OK[1:]
        page_writer.publish(BOTH(threads=[LOOP], sources=down), NOW, client=notion)
        tiles = [notion.plain(notion.kids[notion.kids[c][0]][0]) for c in notion.kids[[i for i, b in notion.blocks.items() if b["type"] == "column_list"][0]]]
        self.assertEqual(tiles[:4], ["—", "—", "—", "—"])

    def test_a_degraded_source_shows_degraded_never_zero_and_adds_then_removes_the_state_callout(self):
        notion = FakeNotion()
        down = [dict(OK[0], status="degraded", last_accepted="2026-11-17T00:00:00+00:00")] + OK[1:]
        page_writer.publish(BOTH(sources=down), NOW, client=notion)
        self.assertIn("DEGRADED, last accepted", notion.plain(notion.kids[notion.health][1]))
        self.assertTrue(notion.plain(notion.kids[notion.page][-1]).startswith("Cannot say."))
        self.assertTrue(notion.rows(notion.main)[0][3].startswith("Cannot say"))
        page_writer.publish(BOTH(), NOW, client=notion)                                    # every source read and nothing open: Truly empty replaces Cannot say
        self.assertTrue(notion.plain(notion.kids[notion.page][-1]).startswith("Truly empty."))
        page_writer.publish(BOTH(threads=[LOOP]), NOW, client=notion)                      # a loop exists: no state callout at all
        self.assertEqual(notion.blocks[notion.kids[notion.page][-1]]["type"], "paragraph")

    def test_asana_and_concord_say_not_connected_and_tgp_is_never_a_row(self):
        notion = FakeNotion()
        tgp = payload("personal", "tgp inbox", threads=[dict(LOOP, title="TGP thing")])
        page_writer.publish([tgp, *BOTH(threads=[LOOP])], NOW, client=notion)
        line = notion.plain(notion.kids[notion.health][1])
        self.assertIn("Asana not connected (view only)", line)
        self.assertIn("Concord not connected", line)
        self.assertNotIn("TGP thing", " ".join(" ".join(r) for r in notion.rows(notion.main) + notion.rows(notion.followup)))
        unknown = payload("tgp", "tgp inbox", threads=[LOOP])
        self.assertEqual(page_view.render([unknown], NOW)["main"][0][3], "Cannot say: not every source was read")

    def test_it_writes_only_blocks_it_read_below_the_marker(self):
        notion = FakeNotion()
        page_writer.publish(BOTH(threads=[LOOP]), NOW, client=notion)
        known = set(notion.blocks)
        self.assertTrue(set(notion.touched) <= known)
        page = page_writer.Page(notion, notion.page)
        with self.assertRaises(InboxError) as caught:
            page.set_text({"id": "f" * 32, "type": "paragraph", "paragraph": {"rich_text": rich("x")}}, [("y", False, False)])
        self.assertEqual(caught.exception.reason, "write_outside_page")
        self.assertNotIn(notion.marker, notion.touched)
        self.assertNotIn(notion.page, [t for t in notion.touched if t != notion.page])

    def test_the_page_is_found_by_title_and_marker_never_by_a_committed_id(self):
        notion = FakeNotion()
        self.assertEqual(page_writer.locate(notion), notion.page)
        notion.kids[notion.page].remove(notion.marker)
        with self.assertRaises(InboxError) as caught:
            page_writer.locate(notion)
        self.assertEqual(caught.exception.reason, "page_not_found")

    def test_a_page_whose_shape_changed_or_has_no_marker_is_left_alone(self):
        notion = FakeNotion()
        notion.kids[notion.page].pop()                                                     # the Review Needed line is gone
        with self.assertRaises(InboxError) as caught:
            page_writer.publish(BOTH(), NOW, client=notion)
        self.assertEqual(caught.exception.reason, "page_shape_changed")
        self.assertEqual(notion.touched, [])
        notion = FakeNotion()
        notion.kids[notion.page].remove(notion.marker)
        with self.assertRaises(InboxError) as caught:
            page_writer.publish(BOTH(), NOW, client=notion)
        self.assertEqual(caught.exception.reason, "page_not_found")

    def test_the_followup_stage_publishes_after_a_live_run_never_fails_on_a_page_error_and_honours_the_switch(self):
        from lifeos.inbox import followup_stage
        seen = []
        out = followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "on"}, writer=lambda p, n, e: seen.append(len(p)) or {"unchanged": 1})
        self.assertEqual((out, seen), ({"unchanged": 1}, [1]))
        self.assertIsNone(followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "off"}, writer=lambda p, n, e: seen.append(9)))
        self.assertEqual(seen, [1])                                                          # off: the writer is never called

        def refuse(payloads, now, env):
            raise InboxError("page_shape_changed")
        self.assertIsNone(followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "on"}, writer=refuse))   # a refusal prints a fixed word and never raises

    def test_no_payload_yet_is_not_read_yet_not_zero(self):
        view = page_view.render([], NOW)
        self.assertEqual([t[0] for t in view["tiles"]], ["—", "—", "—", "—"])
        self.assertEqual(view["healthy"][0], "0 of 6")
        self.assertIsNone(view["state"])


if __name__ == "__main__":
    unittest.main()
