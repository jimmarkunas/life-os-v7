"""LIFE-711: the Action Inbox page writer fills Jim's approved dashboard page (tiles, health table, inline list, Followup section) from stored Followup payloads, writes only what it owns, and is idempotent."""
import copy
import unittest
from datetime import datetime, timezone

from lifeos.inbox import page_rows, page_view, page_writer
from lifeos.inbox.model import InboxError

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)
SOURCES = ("jira", "calendar", "gmail", "todoist", "notion", "interview_os")
OK = [{"source": s, "status": "ok", "reason": "ok", "last_accepted": None} for s in SOURCES]
PAGE = "p" * 32
SOURCE_ID = "s" * 32


def rich(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def notion_prop(value):
    """A property as the Notion API returns it, from the shape the writer sends."""
    if "title" in value:
        return {"type": "title", "title": [{"plain_text": p["text"]["content"]} for p in value["title"]]}
    if "rich_text" in value:
        return {"type": "rich_text", "rich_text": [{"plain_text": p["text"]["content"]} for p in value["rich_text"]]}
    if "select" in value:
        return {"type": "select", "select": value["select"]}
    if "number" in value:
        return {"type": "number", "number": value["number"]}
    return {"type": "url", "url": value["url"]}


class FakeNotion:
    """An in-memory page tree plus an in-memory list database, with the calls the writer uses; records every block id changed."""

    def __init__(self):
        self.blocks, self.kids, self.touched, self.n, self.rows, self.row_writes = {}, {}, [], 0, {}, []
        self.page = self._add(None, "page", {})
        cols = self.top("column_list", None)
        for name in ("Needs me", "Waiting", "Approvals", "Upcoming", "Sources healthy"):
            col = self._add(cols, "column", {})
            self.child(self._add(col, "callout", {"rich_text": []}), [("heading_3", rich("—")), ("paragraph", rich(name))])
        self.listing = self.top("child_database", None)
        self.top("heading_3", rich("Followup"))
        self.followup = self.table(["Workspace", "Loop", "Owner", "Move", "Checkpoint", "Freshness"], ["—", "—", "—", "—", "—", "—"])
        self.review = self.top("paragraph", rich("Review Needed (not counted): —"))
        self.top("heading_3", rich("Source health"))
        self.health = self.table(["Workspace", "Source", "Health", "Last OK", "Items"], ["Personal", "Jira", "⚪ not read yet", "—", "—"])
        self.refreshed = self.top("paragraph", rich("Last refreshed: —"))
        self.touched = []

    def _add(self, parent, kind, body):
        self.n += 1
        block_id = PAGE if parent is None else f"b{self.n:031d}"
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

    def table(self, head, row):
        table = self.top("table", None)
        for cells in (head, row):
            self._add(table, "table_row", {"cells": [rich(c) for c in cells]})
        return table

    def plain(self, block_id):
        body = self.blocks[block_id][self.blocks[block_id]["type"]]
        return "".join(p["plain_text"] for p in body.get("rich_text", []))

    def table_rows(self, table):
        return [tuple("".join(p["plain_text"] for p in cell) for cell in self.blocks[i]["table_row"]["cells"]) for i in self.kids[table]][1:]

    def list_rows(self, archived=False):
        return [r for r in self.rows.values() if bool(r.get("in_trash")) == archived]

    @staticmethod
    def _pieces(pieces):
        return [{"type": "text", "plain_text": p["text"]["content"], "text": p["text"], "annotations": p.get("annotations", {})} for p in pieces]

    def call(self, method, path, body=None):
        if method == "POST" and path == "/search":
            return {"results": [{"id": self.page, "properties": {"title": {"title": [{"plain_text": "Agent Inbox"}]}}}]}
        if method == "GET" and path == f"/databases/{self.listing}":
            return {"data_sources": [{"id": SOURCE_ID}]}
        if path == f"/data_sources/{SOURCE_ID}/query":
            return {"results": [{"id": k, "properties": {n: notion_prop(v) if "type" not in v else v for n, v in r["properties"].items()}} for k, r in self.rows.items() if not r.get("in_trash")],
                    "has_more": False}
        if method == "POST" and path == "/pages":
            assert body["parent"]["data_source_id"] == SOURCE_ID
            key = f"row{len(self.rows)}"
            self.rows[key] = {"properties": body["properties"]}
            self.row_writes.append(("create", key))
            return {"id": key}
        if path.startswith("/pages/"):
            key = path.split("/")[2]
            if body.get("in_trash"):
                self.rows[key]["in_trash"] = True
            else:
                self.rows[key]["properties"] = body["properties"]
            self.row_writes.append(("trash" if body.get("in_trash") else "update", key))
            return {}
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
                    self._add(parts[1], "table_row", {"cells": [self._pieces(cell) for cell in child["table_row"]["cells"]]})
                else:
                    new = self._add(parts[1], "callout", {"rich_text": self._pieces(child["callout"]["rich_text"])})
                    self.blocks[new]["callout"]["color"] = child["callout"]["color"]
            return {}
        kind = self.blocks[parts[1]]["type"]
        self.blocks[parts[1]][kind]["rich_text"] = self._pieces(body[kind]["rich_text"])
        return {}

    call_once = call


def payload(workspace="personal", account="personal outlook", threads=(), sources=OK, state="PASS", review=()):
    return {"component": "followup", "state": state, "workspace": workspace, "account": account, "generated_at": "2026-11-17T01:00:00+00:00", "last_accepted": None, "counts": {},
            "threads": list(threads), "review": list(review), "sources": [dict(s) for s in sources], "provenance": {}}


LOOP = {"id": "a" * 64, "title": "Send the revised quote", "state": "needs_action_today", "owner": "jim", "next_checkpoint": None, "notes": "checkpoint source-backed", "url": "https://example.com/x"}
WAIT = {"id": "b" * 64, "title": "Vendor reply", "state": "waiting_aging", "owner": "external", "next_checkpoint": "2026-11-20T15:00:00+00:00", "notes": "checkpoint suggested", "url": ""}


def both(threads=(), **kw):
    return [payload("personal", threads=threads, **kw), payload("bytalos", "bytalos inbox", **kw)]


def tile_numbers(notion):
    column_list = next(i for i, b in notion.blocks.items() if b["type"] == "column_list")
    return [notion.plain(notion.kids[notion.kids[c][0]][0]) for c in notion.kids[column_list]]


def tile_shapes(notion):
    """The child block types of each of the five tile callouts: they must all be the identical two blocks, so the tiles are always the same height."""
    column_list = next(i for i, b in notion.blocks.items() if b["type"] == "column_list")
    return [tuple(notion.blocks[k]["type"] for k in notion.kids[notion.kids[c][0]]) for c in notion.kids[column_list]]


class PageWriterTests(unittest.TestCase):
    def test_a_live_payload_fills_every_part_and_the_second_run_writes_nothing(self):
        notion = FakeNotion()
        first = page_writer.publish(both([LOOP, WAIT]), NOW, client=notion)
        self.assertEqual((first["list_rows_created"], first["list_rows_updated"], first["list_rows_archived"]), (2, 0, 0))
        self.assertEqual(tile_numbers(notion), ["1", "1", "—", "0", "4 of 6"])
        self.assertEqual(tile_shapes(notion), [("heading_3", "paragraph")] * 5)
        health = notion.table_rows(notion.health)
        self.assertEqual(len(health), 16)
        self.assertEqual(health[0][:3], ("Personal", "Jira", "🟢 ok"))
        self.assertIn(("All", "Concord", "⚪ not connected", "—", "—"), health)
        self.assertIn(("TGP", "Everything", "⚪ tracked, not connected (by design)", "—", "—"), health)
        self.assertEqual(notion.table_rows(notion.followup)[0][:3], ("Personal", "Send the revised quote", "You"))
        ranks = sorted(r["properties"]["Rank"]["number"] for r in notion.list_rows())
        self.assertEqual(ranks, [1, 3])                                                      # Needs me is rank 1, Waiting rank 3: the page sorts most urgent first
        notion.touched.clear()
        notion.row_writes.clear()
        again = page_writer.publish(both([LOOP, WAIT]), NOW, client=notion)
        self.assertEqual((notion.touched, notion.row_writes), ([], []))
        self.assertEqual(again["unchanged"], 1)

    def test_a_degraded_source_shows_degraded_keeps_the_rows_and_a_clean_read_archives_them(self):
        notion = FakeNotion()
        page_writer.publish(both([LOOP]), NOW, client=notion)
        down = [dict(OK[0], status="degraded", last_accepted="2026-11-17T00:00:00+00:00")] + OK[1:]
        page_writer.publish(both([], sources=down), NOW, client=notion)
        self.assertEqual(len(notion.list_rows()), 1)                                           # an unreadable source never empties the list
        self.assertTrue(notion.table_rows(notion.health)[0][2].startswith("🟡"))
        self.assertEqual(tile_numbers(notion)[:4], ["—", "—", "—", "—"])
        self.assertEqual(tile_shapes(notion), [("heading_3", "paragraph")] * 5)             # a down source never adds a line to a tile
        self.assertTrue(notion.plain(notion.kids[notion.page][-1]).startswith("Cannot say."))
        page_writer.publish(both([]), NOW, client=notion)                                      # every source read and the record is gone
        self.assertEqual(len(notion.list_rows()), 0)
        self.assertEqual(len(notion.list_rows(archived=True)), 1)
        self.assertTrue(notion.plain(notion.kids[notion.page][-1]).startswith("Truly empty."))
        page_writer.publish(both([LOOP]), NOW, client=notion)                                  # a loop exists again: no state callout at all
        self.assertEqual(notion.blocks[notion.kids[notion.page][-1]]["type"], "paragraph")

    def test_tgp_is_never_a_row_and_a_row_it_does_not_own_is_never_touched(self):
        notion = FakeNotion()
        notion.rows["foreign"] = {"properties": {"Outcome": {"type": "title", "title": [{"plain_text": "Jim's own row"}]}, "Managed by": {"type": "select", "select": None},
                                                 "Record ID": {"type": "rich_text", "rich_text": [{"plain_text": "followup:personal:x:9"}]}, "Workspace": {"type": "select", "select": {"name": "Personal"}}}}
        tgp = payload("personal", "tgp inbox", threads=[dict(LOOP, title="TGP thing")])
        page_writer.publish([tgp, *both([LOOP])], NOW, client=notion)
        written = [r["properties"]["Outcome"]["title"][0]["text"]["content"] for k, r in notion.rows.items() if k != "foreign"]
        self.assertEqual(written, ["Send the revised quote"])
        self.assertFalse(any(key == "foreign" for _, key in notion.row_writes))

    def test_it_writes_only_blocks_it_read_from_the_page(self):
        notion = FakeNotion()
        page_writer.publish(both([LOOP]), NOW, client=notion)
        self.assertTrue(set(notion.touched) <= set(notion.blocks))
        page = page_writer.Page(notion, notion.page)
        with self.assertRaises(InboxError) as caught:
            page.set_text({"id": "f" * 32, "type": "paragraph", "paragraph": {"rich_text": rich("x")}}, [("y", False, False)])
        self.assertEqual(caught.exception.reason, "write_outside_page")

    def test_the_page_is_found_by_title_and_shape_never_by_a_committed_id(self):
        notion = FakeNotion()
        self.assertEqual(page_writer.locate(notion), notion.page)
        notion.kids[notion.page].remove(notion.review)
        with self.assertRaises(InboxError) as caught:
            page_writer.locate(notion)
        self.assertEqual(caught.exception.reason, "page_not_found")

    def test_a_page_whose_shape_changed_is_left_alone(self):
        notion = FakeNotion()
        notion.kids[notion.page].pop()
        with self.assertRaises(InboxError) as caught:
            page_writer.publish(both(), NOW, client=notion)                                    # the page is found by its shape, so a changed page is not found and nothing is written
        self.assertEqual(caught.exception.reason, "page_not_found")
        with self.assertRaises(InboxError) as caught:
            page_writer.publish(both(), NOW, client=notion, page_id=notion.page)
        self.assertEqual(caught.exception.reason, "page_shape_changed")
        self.assertEqual((notion.touched, notion.row_writes), ([], []))

    def test_the_followup_stage_publishes_after_a_live_run_never_fails_on_a_page_error_and_honours_the_switch(self):
        from lifeos.inbox import followup_stage
        seen = []
        out = followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "on"}, writer=lambda p, n, e: seen.append(len(p)) or {"unchanged": 1})
        self.assertEqual((out, seen), ({"unchanged": 1}, [1]))
        self.assertIsNone(followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "off"}, writer=lambda p, n, e: seen.append(9)))
        self.assertEqual(seen, [1])

        def refuse(payloads, now, env):
            raise InboxError("page_shape_changed")
        self.assertIsNone(followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "on"}, writer=refuse))

    def test_no_payload_yet_is_not_read_yet_not_zero(self):
        view = page_view.render([], NOW)
        self.assertEqual(view["tiles"], ["—", "—", "—", "—"])
        self.assertEqual(view["healthy"], "0 of 6")
        self.assertIsNone(view["state"])
        self.assertTrue(all(row[2].startswith("⚪") for row in view["health"][:11]))

    def test_the_page_carries_no_marker_header_or_explanatory_text_and_ends_on_one_last_refreshed_line(self):
        notion = FakeNotion()
        page_writer.publish(both([LOOP]), NOW, client=notion)
        texts = [notion.plain(i) for i, b in notion.blocks.items() if "rich_text" in b.get(b["type"], {})] + [c for t in (notion.health, notion.followup) for row in notion.table_rows(t) for c in row]
        for banned in ("machine-written", "hand-edit", "Filters", "Most urgent", "How Followup maps", "TGP is never shown", "not wired", "waiting for the first", "Cannot say: a source"):
            self.assertFalse([t for t in texts if banned.lower() in t.lower()], banned)
        last = notion.kids[notion.page][-1]
        self.assertEqual(notion.blocks[last]["type"], "paragraph")
        self.assertEqual(notion.plain(last), "Last refreshed: 11/16 · 8:00 PM CT")
        self.assertEqual(notion.blocks[notion.kids[notion.page][0]]["type"], "column_list")        # the page opens on the tiles, the list is directly under them
        self.assertEqual(notion.blocks[notion.kids[notion.page][1]]["type"], "child_database")

    def test_the_list_plan_is_idempotent_and_archives_only_a_cleanly_read_workspace(self):
        desired = [{"id": "followup:personal:x:1", "Outcome": "A", "Workspace": "Personal", "Source": "Followup", "Bucket": "Needs me", "Rank": 1, "Owner": "You", "Next move": "n", "Due": "", "Freshness": "fresh", "Link": ""}]
        plan = page_rows.plan([], desired, {"personal"})
        self.assertEqual([op[0] for op in plan], ["create"])
        current = [{**desired[0], "page_id": "r1"}]
        self.assertEqual(page_rows.plan(current, desired, {"personal"}), ())
        self.assertEqual(page_rows.plan(current, [], {"bytalos"}), ())                         # personal was not read cleanly: its row stays
        self.assertEqual([op[0] for op in page_rows.plan(current, [], {"personal"})], ["archive"])


if __name__ == "__main__":
    unittest.main()
