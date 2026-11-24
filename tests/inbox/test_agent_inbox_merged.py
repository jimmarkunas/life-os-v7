"""LIFE-938 (LIFE-939 wireframe): the tile writer on the merged Daily Report + Agent Inbox page. A fake Notion tree: no network, no token."""
import itertools
import unittest

from lifeos.inbox import agent_inbox
from lifeos.inbox.model import InboxError

SOURCE, MERGED, OLD = "a1" * 16, "d4" * 16, "b2" * 16


def rich(text, href=None):
    return [{"type": "text", "plain_text": text, "href": href, "text": {"content": text, "link": {"url": href} if href else None}}]


class Notion:
    """Pages, a block tree and inbox rows. Every write is recorded as (method, path)."""
    def __init__(self):
        self.blocks, self.kids, self.pages, self.rows, self.writes, self.ids = {}, {}, {}, [], [], itertools.count(1)

    def new(self, parent, kind, text="", **extra):
        bid = f"blk{next(self.ids)}"
        self.blocks[bid] = {"id": bid, "type": kind, kind: {"rich_text": rich(text)}, **extra}
        self.kids.setdefault(parent, []).append(bid)
        self.kids.setdefault(bid, [])
        return bid

    def tile(self, column, number, label):
        callout = self.new(column, "callout")
        self.new(callout, "heading_3", number)
        self.new(callout, "paragraph", label)

    def page(self, pid, title, merged, tiles=4):
        self.pages[pid] = {"object": "page", "id": pid, "properties": {"title": {"title": [{"plain_text": title}]}}}
        self.kids[pid] = []
        wrapper = self.new(pid, "callout", "Daily LIFE OS Report (Hourly)") if merged == "wrapped" else pid
        row = self.new(wrapper, "column_list")
        for i in range(tiles):
            column = self.new(row, "column")
            if merged and i == 0:
                box = self.new(column, "callout")
                self.new(box, "heading_3", "Now 0 · Today 0")
            elif merged:
                self.tile(column, "9", agent_inbox.MERGED_LABELS[i - 1] if i - 1 < 3 else "x")
            else:
                self.tile(column, "9", "old")

    def row(self, urgency, subject, link=None, status="New"):
        self.rows.append({"id": f"row{len(self.rows)}", "status": status, "urgency": urgency, "properties": {"Subject": {"title": [{"plain_text": subject}]}, **({"Link": {"url": link}} if link else {})}})

    def texts(self, bid):
        return [(self.blocks[k]["type"], "".join(p["plain_text"] for p in self.blocks[k][self.blocks[k]["type"]]["rich_text"])) for k in self.kids.get(bid, [])]

    def call(self, method, path, body=None):
        if path == "/search":
            if body["filter"]["value"] == "data_source":
                return {"results": [{"object": "data_source", "id": SOURCE, "title": [{"plain_text": agent_inbox.DB_TITLE}]}], "has_more": False}
            return {"results": [p for p in self.pages.values() if body["query"] == "".join(t["plain_text"] for t in p["properties"]["title"]["title"])], "has_more": False}
        if method == "GET" and path.startswith("/blocks/"):
            bid = path.split("/")[2].split("?")[0]
            return {"results": [self.blocks[k] for k in self.kids.get(bid, [])], "has_more": False}
        if method == "GET" and path.startswith("/data_sources/"):
            return {"parent": {"type": "workspace"}}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        self.writes.append((method, path))
        parts = path.split("/")
        if method == "PATCH" and path.endswith("/children"):
            for child in body["children"]:
                kind = child["type"]
                bid = f"blk{next(self.ids)}"
                self.blocks[bid] = {"id": bid, "type": kind, kind: {"rich_text": [{**p, "plain_text": p["text"]["content"], "href": (p["text"].get("link") or {}).get("url")} for p in child[kind]["rich_text"]]}}
                self.kids.setdefault(parts[2], []).append(bid)
        elif method == "DELETE":
            for kids in self.kids.values():
                if parts[2] in kids:
                    kids.remove(parts[2])
        return {}

    def update_block(self, bid, kind, rich_text):
        self.writes.append(("PATCH", f"/blocks/{bid}"))
        self.blocks[bid][kind] = {"rich_text": [{**p, "plain_text": p["text"]["content"], "href": (p["text"].get("link") or {}).get("url")} for p in rich_text]}

    def query_data_source(self, source, body):
        assert source == SOURCE
        flt = body["filter"]
        wanted = {part["property"]: part["select"]["equals"] for part in flt.get("and", [flt])}
        return {"results": [r for r in self.rows if r["status"] == wanted["Status"] and ("Urgency" not in wanted or r["urgency"] == wanted["Urgency"])], "has_more": False}


def build(wrapped=True, old_page=True):
    n = Notion()
    n.page(MERGED, "Daily LIFE OS Report (Hourly)", "wrapped" if wrapped else "bare")
    if old_page:
        n.page(OLD, "Agent Inbox", False, tiles=5)
    return n


class MergedTiles(unittest.TestCase):
    def box(self, n):
        merged = agent_inbox._merged_row(n, MERGED)
        return merged[0], merged[1], merged[2]

    def test_the_merged_page_wins_over_the_old_agent_inbox_page_and_the_old_page_is_never_written(self):
        n = build()
        n.row("Now", "Approve the key plan", "https://example.test/a")
        n.row("Today", "Read the weekly summary")
        self.assertEqual(agent_inbox.locate_page(n, SOURCE), MERGED)
        out = agent_inbox.recount_page(n, source=SOURCE)
        self.assertEqual((out["layout"], out["tiles"]), ("merged", 4))
        self.assertFalse([w for w in n.writes if OLD in w[1] or w[1].split("/")[2] in n.kids.get(OLD, [])])
        box, heading, tiles = self.box(n)
        self.assertEqual(n.texts(box["id"]), [("heading_3", "Now 1 · Today 1")])                   # Jim, Nov 24 2026 2:36 PM CT: a counter only, no item list
        self.assertEqual([agent_inbox._words(number) for _, number, _ in tiles], ["0", "0", "0"])      # This week, FYI, Snoozed counts

    def test_a_list_an_earlier_version_left_in_the_box_is_removed_and_the_same_heading_block_is_kept(self):
        n = build()
        n.row("Now", "first", "https://example.test/n")
        box, heading, _ = self.box(n)
        n.new(box["id"], "bulleted_list_item", "Now: first")
        n.new(box["id"], "bulleted_list_item", "+3 more in the Agent Inbox list")
        out = agent_inbox.recount_page(n, source=SOURCE)
        self.assertEqual(n.texts(box["id"]), [("heading_3", "Now 1 · Today 0")])
        self.assertEqual(sum(1 for w in n.writes if w[0] == "DELETE"), 2)                          # exactly the two list lines, nothing else
        box2, heading2, _ = self.box(n)
        self.assertEqual((box2["id"], heading2["id"]), (box["id"], heading["id"]))
        self.assertEqual({w[0] for w in n.writes}, {"PATCH", "DELETE"})
        self.assertFalse([w for w in n.writes if w[0] == "PATCH" and w[1].endswith("/children")])  # nothing is ever appended to the box

    def test_a_second_run_with_nothing_new_writes_nothing_and_a_change_touches_only_the_heading(self):
        n = build()
        n.row("Now", "one")
        n.row("Now", "two")
        agent_inbox.recount_page(n, source=SOURCE)
        n.writes.clear()
        self.assertEqual(agent_inbox.recount_page(n, source=SOURCE)["tiles_changed"], 0)
        self.assertEqual(n.writes, [])
        n.rows[0]["status"] = "Done"                                                         # one resolved: only the heading changes
        out = agent_inbox.recount_page(n, source=SOURCE)
        self.assertEqual(out["tiles_changed"], 1)
        box, _, _ = self.box(n)
        self.assertEqual(n.texts(box["id"]), [("heading_3", "Now 1 · Today 0")])

    def test_many_items_still_give_only_the_counts_with_no_more_line(self):
        n = build()
        for i in range(15):
            n.row("Today", f"item {i}")
        agent_inbox.recount_page(n, source=SOURCE)
        box, _, _ = self.box(n)
        self.assertEqual(n.texts(box["id"]), [("heading_3", "Now 0 · Today 15")])

    def test_the_bare_row_without_a_wrapper_works_and_a_wrong_shape_falls_back_to_the_old_page(self):
        n = build(wrapped=False)
        self.assertEqual(agent_inbox.locate_page(n, SOURCE), MERGED)
        broken = build()
        broken.pages[MERGED]  # a merged page whose row lost a column is not a tile page: the old page serves
        for column in list(broken.kids[[k for k in broken.kids if broken.blocks.get(k, {}).get("type") == "column_list"][0]])[-1:]:
            broken.kids[[k for k in broken.kids if broken.blocks.get(k, {}).get("type") == "column_list"][0]].remove(column)
        self.assertEqual(agent_inbox.locate_page(broken, SOURCE), OLD)

    def test_two_merged_pages_or_none_at_all_are_fixed_refusals(self):
        n = build()
        twin = "e5" * 16
        n.pages[twin] = {**n.pages[MERGED], "id": twin}
        n.kids[twin] = list(n.kids[MERGED])
        with self.assertRaises(InboxError) as raised:
            agent_inbox.locate_page(n, SOURCE)
        self.assertEqual(str(raised.exception), "page_ambiguous")
        empty = Notion()
        with self.assertRaises(InboxError) as raised:
            agent_inbox.locate_page(empty, SOURCE)
        self.assertEqual(str(raised.exception), "page_not_found")

    def test_an_archived_or_trashed_merged_page_is_not_used(self):
        n = build()
        n.pages[MERGED]["archived"] = True
        self.assertEqual(agent_inbox.locate_page(n, SOURCE), OLD)


if __name__ == "__main__":
    unittest.main()


class JimsRearrangedLayout(unittest.TestCase):
    """Jim, Nov 24 2026, 2:30 PM CT: the box is its own callout near the top; the three tiles sit in a row inside the Command Center callout with an empty first column."""
    def make(self):
        n = Notion()
        n.pages[MERGED] = {"object": "page", "id": MERGED, "properties": {"title": {"title": [{"plain_text": "Daily LIFE OS Report (Hourly)"}]}}}
        n.kids[MERGED] = []
        dcc = n.new(MERGED, "callout")
        n.new(dcc, "heading_3", "ChatGPT · Daily Command Center")
        row = n.new(dcc, "column_list")
        first = n.new(row, "column")
        n.new(first, "paragraph", "")
        for label in agent_inbox.MERGED_LABELS:
            n.tile(n.new(row, "column"), "9", label)
        box = n.new(MERGED, "callout")
        n.new(box, "heading_3", "Now 0 · Today 0")
        n.new(box, "bulleted_list_item", "Now: a list from the earlier layout")
        return n, box

    def test_the_box_and_tiles_are_found_by_what_they_say_wherever_they_are(self):
        n, box = self.make()
        found = agent_inbox._merged_row(n, MERGED)
        self.assertEqual((found[0]["id"], [agent_inbox._words(t[2]) for t in found[2]]), (box, ["This week", "FYI", "Snoozed"]))

    def test_a_run_writes_the_counter_into_the_standalone_box_and_the_three_tiles_and_nothing_else(self):
        n, box = self.make()
        n.row("Now", "one")
        n.row("Now", "two")
        out = agent_inbox.recount_page(n, source=SOURCE)
        self.assertEqual(out["layout"], "merged")
        self.assertEqual(n.texts(box), [("heading_3", "Now 2 · Today 0")])                             # counter only, the earlier list line is removed
        self.assertEqual(agent_inbox.locate_page(n, SOURCE), MERGED)

    def test_two_boxes_or_a_missing_tile_are_not_a_merged_page(self):
        n, box = self.make()
        again = n.new(MERGED, "callout")
        n.new(again, "heading_3", "Now 1 · Today 1")
        self.assertIsNone(agent_inbox._merged_row(n, MERGED))
        m, _ = self.make()
        callout = [k for k, b in m.blocks.items() if b["type"] == "paragraph" and agent_inbox._words(b) == "FYI"][0]
        m.blocks[callout]["paragraph"] = {"rich_text": rich("Other")}
        self.assertIsNone(agent_inbox._merged_row(m, MERGED))
