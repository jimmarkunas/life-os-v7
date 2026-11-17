"""LIFE-711: the Action Inbox page writer fills Jim's approved dashboard (five tiles, the list database, the System health database, the last-refreshed line) from stored Followup payloads, writes only what it owns, and is idempotent."""
import copy
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from lifeos.inbox import followup_stage, page_rows, page_view, page_writer
from lifeos.inbox.model import InboxError

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)
SOURCES = ("jira", "calendar", "gmail", "todoist", "notion", "interview_os")
OK = [{"source": s, "status": "ok", "reason": "ok", "last_accepted": None} for s in SOURCES]
PAGE, LIST_DB, HEALTH_DB = "p" * 32, "l" * 32, "h" * 32
SOURCE_OF = {LIST_DB: "s_list", HEALTH_DB: "s_health"}


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
    """An in-memory page tree plus two in-memory databases (the list and System health); records every block id and row it changes."""

    def __init__(self, wrapped=False):
        self.blocks, self.kids, self.touched, self.n, self.row_writes = {}, {}, [], 0, []
        self.rows = {"s_list": {}, "s_health": {}}
        self.page = self._add(None, "page", {})
        cols = self.top("column_list", None)
        for name in ("Needs me", "Waiting", "Approvals", "Upcoming", "Sources healthy"):
            col = self._add(cols, "column", {})
            self.child(self._add(col, "callout", {"rich_text": []}), [("heading_3", rich("—")), ("paragraph", rich(name))])
        self.top("divider", None)
        self.listing = self._database(LIST_DB, wrapped)
        self.top("divider", None)
        self.healthdb = self._database(HEALTH_DB, wrapped)
        self.refreshed = self.top("paragraph", rich("Last refreshed: —"))
        self.touched = []

    def _database(self, db_id, wrapped):
        if not wrapped:
            self.blocks[db_id] = {"id": db_id, "type": "child_database", "child_database": {}}
            self.kids[db_id] = []
            self.kids[self.page].append(db_id)
            return db_id
        wrapper = self.top("callout", rich(""))
        self.blocks[db_id] = {"id": db_id, "type": "child_database", "child_database": {}}
        self.kids[db_id] = []
        self.kids[wrapper].append(db_id)
        self.blocks[wrapper]["has_children"] = True
        return wrapper

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
        return self._add(self.page, kind, {"rich_text": text} if text is not None else {})

    def child(self, parent, kids):
        for kind, text in kids:
            self._add(parent, kind, {"rich_text": text})

    def plain(self, block_id):
        body = self.blocks[block_id][self.blocks[block_id]["type"]]
        return "".join(p["plain_text"] for p in body.get("rich_text", []))

    def live(self, source, archived=False):
        return [r for r in self.rows[source].values() if bool(r.get("in_trash")) == archived]

    def values(self, source):
        return sorted(r["properties"]["Record ID"]["rich_text"][0]["text"]["content"] for r in self.live(source))

    @staticmethod
    def _pieces(pieces):
        return [{"type": "text", "plain_text": p["text"]["content"], "text": p["text"], "annotations": p.get("annotations", {})} for p in pieces]

    def call(self, method, path, body=None):
        if method == "POST" and path == "/search":
            return {"results": [{"id": self.page, "properties": {"title": {"title": [{"plain_text": "Agent Inbox"}]}}}]}
        if method == "GET" and path.startswith("/databases/"):
            return {"data_sources": [{"id": SOURCE_OF[path.split("/")[2]]}]}
        if "/data_sources/" in path and path.endswith("/query"):
            source = path.split("/")[2]
            return {"results": [{"id": k, "properties": {n: notion_prop(v) if "type" not in v else v for n, v in r["properties"].items()}} for k, r in self.rows[source].items() if not r.get("in_trash")],
                    "has_more": False}
        if method == "POST" and path == "/pages":
            source = body["parent"]["data_source_id"]
            key = f"{source}:{len(self.rows[source])}"
            self.rows[source][key] = {"properties": body["properties"]}
            self.row_writes.append(("create", key))
            return {"id": key}
        if path.startswith("/pages/"):
            key = path.split("/")[2]
            source = key.split(":")[0]
            if body.get("in_trash"):
                self.rows[source][key]["in_trash"] = True
            else:
                self.rows[source][key]["properties"] = body["properties"]
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
                kind = child["type"]
                new = self._add(parts[1], kind, {"rich_text": self._pieces(child[kind]["rich_text"])})
                if "color" in child[kind]:
                    self.blocks[new][kind]["color"] = child[kind]["color"]
            return {}
        kind = self.blocks[parts[1]]["type"]
        self.blocks[parts[1]][kind]["rich_text"] = self._pieces(body[kind]["rich_text"])
        return {}

    call_once = call


def payload(workspace="personal", account="personal outlook", threads=(), sources=OK, state="PASS", review=(), **extra):
    return {"component": "followup", "state": state, "workspace": workspace, "account": account, "generated_at": "2026-11-17T01:00:00+00:00", "last_accepted": None, "counts": {},
            "threads": list(threads), "review": list(review), "sources": [dict(s) for s in sources], "provenance": {}, **extra}


LOOP = {"id": "a" * 64, "title": "Send the revised quote", "state": "needs_action_today", "owner": "jim", "next_checkpoint": None, "notes": "checkpoint source-backed", "url": "https://acme.atlassian.net/browse/X-1"}
WAIT = {"id": "b" * 64, "title": "Vendor reply", "state": "waiting_aging", "owner": "external", "next_checkpoint": "2026-11-20T15:00:00+00:00", "notes": "checkpoint suggested", "url": ""}


def both(threads=(), **kw):
    return [payload("personal", threads=threads, **kw), payload("bytalos", "bytalos inbox", **kw)]


def kinds(notion):
    return [notion.blocks[i]["type"] for i in notion.kids[notion.page]]


def tiles(notion):
    column_list = notion.kids[notion.page][0]
    return [notion.plain(notion.kids[notion.kids[c][0]][0]) for c in notion.kids[column_list]]


def tile_shapes(notion):
    """The child block types of each of the five tile callouts: they must all be the identical two blocks, so the tiles are always the same height."""
    return [tuple(notion.blocks[k]["type"] for k in notion.kids[notion.kids[c][0]]) for c in notion.kids[notion.kids[notion.page][0]]]


def cell(row, name):
    return row["properties"][name]


def text_of(row, name):
    prop = row["properties"][name]
    kind = "title" if "title" in prop else "rich_text"
    return "".join(p["text"]["content"] for p in prop[kind])


class PageWriterTests(unittest.TestCase):
    def test_a_live_payload_fills_every_part_and_the_second_run_writes_nothing(self):
        notion = FakeNotion()
        first = page_writer.publish(both([LOOP, WAIT]), NOW, client=notion)
        self.assertEqual((first["list_rows_created"], first["list_rows_updated"], first["list_rows_archived"]), (2, 0, 0))
        self.assertEqual(first["health_rows_created"], 16)
        self.assertEqual(tiles(notion), ["1", "1", "—", "0", "4 of 6"])
        self.assertEqual(tile_shapes(notion), [("heading_3", "paragraph")] * 5)
        self.assertEqual(notion.plain(notion.refreshed), "Last refreshed: 11/16 · 8:00 PM CT")
        ranks = sorted(cell(r, "Rank")["number"] for r in notion.live("s_list"))
        self.assertEqual(ranks, [1, 3])                                                      # Needs me is rank 1, Waiting rank 3: the list sorts most urgent first
        due = sorted(text_of(r, "Due") for r in notion.live("s_list"))
        self.assertEqual(due, ["", "Nov 20 · 9:00 AM CT"])                                    # a readable Central time, never an ISO string
        names = {text_of(r, "Source"): cell(r, "Health")["select"]["name"] for r in notion.live("s_health")}
        self.assertEqual(names["Jira"], "🟢 ok")
        self.assertEqual(names["Concord"], "⚪ not connected")
        self.assertEqual(names["Outlook (personal)"], "⚪ not read yet")
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
        self.assertEqual(len(notion.live("s_list")), 1)                                        # an unreadable source never empties the list
        self.assertEqual(tiles(notion)[:4], ["—", "—", "—", "—"])
        self.assertEqual(tile_shapes(notion), [("heading_3", "paragraph")] * 5)               # a down source never adds a line to a tile
        self.assertTrue(notion.plain(notion.kids[notion.page][-1]).startswith("Cannot say."))
        self.assertIn("🟡 degraded", [cell(r, "Health")["select"]["name"] for r in notion.live("s_health")])
        page_writer.publish(both([]), NOW, client=notion)                                      # every source read and the record is gone
        self.assertEqual((len(notion.live("s_list")), len(notion.live("s_list", archived=True))), (0, 1))
        self.assertTrue(notion.plain(notion.kids[notion.page][-1]).startswith("Truly empty."))
        page_writer.publish(both([LOOP]), NOW, client=notion)
        self.assertEqual(notion.blocks[notion.kids[notion.page][-1]]["type"], "paragraph")
        self.assertEqual(len(notion.live("s_health")), 16)                                     # the fixed System health rows are updated in place, never archived or duplicated

    def test_tgp_is_never_a_row_and_a_row_it_does_not_own_is_never_touched(self):
        notion = FakeNotion()
        notion.rows["s_list"]["foreign"] = {"properties": {"Outcome": {"type": "title", "title": [{"plain_text": "Jim's own row"}]}, "Managed by": {"type": "select", "select": None},
                                                           "Record ID": {"type": "rich_text", "rich_text": [{"plain_text": "followup:personal:x:9"}]}, "Workspace": {"type": "select", "select": {"name": "Personal"}}}}
        tgp = payload("personal", "tgp inbox", threads=[dict(LOOP, title="TGP thing")])
        page_writer.publish([tgp, *both([LOOP])], NOW, client=notion)
        written = [text_of(r, "Outcome") for k, r in notion.rows["s_list"].items() if k != "foreign"]
        self.assertEqual(written, ["Send the revised quote"])
        self.assertFalse(any(key == "foreign" for _, key in notion.row_writes))

    def test_it_writes_only_blocks_it_read_from_the_page_and_never_recreates_the_databases(self):
        notion = FakeNotion()
        page_writer.publish(both([LOOP]), NOW, client=notion)
        self.assertTrue(set(notion.touched) <= set(notion.blocks))
        self.assertNotIn(LIST_DB, notion.touched)
        self.assertNotIn(HEALTH_DB, notion.touched)
        page = page_writer.Page(notion, notion.page)
        with self.assertRaises(InboxError) as caught:
            page.set_text({"id": "f" * 32, "type": "paragraph", "paragraph": {"rich_text": rich("x")}}, [("y", False, False)])
        self.assertEqual(caught.exception.reason, "write_outside_page")

    def test_jims_callout_around_a_database_is_kept_and_understood(self):
        notion = FakeNotion(wrapped=True)
        self.assertEqual(page_writer.locate(notion), notion.page)
        page_writer.publish(both([LOOP]), NOW, client=notion)
        self.assertEqual(len(notion.live("s_list")), 1)
        self.assertEqual(kinds(notion), ["column_list", "divider", "callout", "divider", "callout", "paragraph"])        # the callouts and dividers are exactly as Jim left them

    def test_the_page_is_found_by_title_and_shape_never_by_a_committed_id(self):
        notion = FakeNotion()
        self.assertEqual(page_writer.locate(notion), notion.page)
        notion.kids[notion.page].remove(notion.refreshed)
        with self.assertRaises(InboxError) as caught:
            page_writer.locate(notion)
        self.assertEqual(caught.exception.reason, "page_not_found")
        with self.assertRaises(InboxError) as caught:
            page_writer.publish(both(), NOW, client=notion, page_id=notion.page)
        self.assertEqual(caught.exception.reason, "page_shape_changed")
        self.assertEqual((notion.touched, notion.row_writes), ([], []))

    def test_a_synthetic_or_example_payload_never_reaches_the_page(self):
        notion = FakeNotion()
        for bad in (payload(synthetic=True), payload(threads=[dict(LOOP, url="https://sample.example.test/x")]), payload(threads=[dict(LOOP, url="https://example.com/a")]),
                    {**payload(), "provenance": {"synthetic": True}}):
            with self.assertRaises(InboxError) as caught:
                page_writer.publish([bad, *both()], NOW, client=notion)
            self.assertEqual(caught.exception.reason, "synthetic_payload_refused")
        self.assertEqual((notion.touched, notion.row_writes), ([], []))

    def test_the_followup_stage_publishes_only_from_the_production_wiring_and_honours_the_switch(self):
        seen = []
        out = followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "on"}, writer=lambda p, n, e: seen.append(len(p)) or {"unchanged": 1})
        self.assertEqual((out, seen), ({"unchanged": 1}, [1]))
        self.assertIsNone(followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "off"}, writer=lambda p, n, e: seen.append(9)))
        self.assertEqual(seen, [1])

        def refuse(payloads, now, env):
            raise InboxError("page_shape_changed")
        self.assertIsNone(followup_stage.publish_page([payload()], NOW, environ={"ACTION_INBOX_VIEW": "on"}, writer=refuse))
        called, sunk = [], []
        fake = (payload(), {"written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0})
        with mock.patch.object(followup_stage, "publish_page", lambda *a, **k: called.append(1)), mock.patch.object(followup_stage, "reconcile", lambda *a, **k: fake), \
                mock.patch.dict(os.environ, {"NOTION_API_TOKEN": "x", "ACTION_INBOX_VIEW": "on"}):
            scope = followup_stage.Scope("personal", "personal outlook", ())
            wiring = followup_stage.Wiring(store=None, scopes=(scope,), grants=(), fetchers={}, audit=lambda r: None, sink=sunk.append)
            self.assertFalse(wiring.production)
            followup_stage.run(0, True, wiring=wiring)
            self.assertEqual((len(sunk), called), (1, []))                                    # the payload was stored, and a fixture wiring still never writes the page
            followup_stage.run(0, True, wiring=followup_stage.Wiring(store=None, scopes=(scope,), grants=(), fetchers={}, audit=lambda r: None, sink=sunk.append, production=True, env={}))
        self.assertEqual(called, [1])                                                          # production is a property of the wiring the repo builds, never of a flag or token a test can set

    def test_the_page_carries_no_marker_header_or_explanatory_text_and_ends_on_one_last_refreshed_line(self):
        notion = FakeNotion()
        page_writer.publish(both([LOOP]), NOW, client=notion)
        texts = [notion.plain(i) for i, b in notion.blocks.items() if "rich_text" in b.get(b["type"], {})]
        for banned in ("machine-written", "hand-edit", "Filters", "Most urgent", "How Followup maps", "TGP is never shown", "not wired", "waiting for the first", "Cannot say: a source"):
            self.assertFalse([t for t in texts if banned.lower() in t.lower()], banned)
        self.assertEqual(kinds(notion), ["column_list", "divider", "child_database", "divider", "child_database", "paragraph"])       # tiles, divider, list, divider, System health, one line
        self.assertEqual(notion.plain(notion.kids[notion.page][-1]), "Last refreshed: 11/16 · 8:00 PM CT")

    def test_the_row_plans_are_idempotent_and_archive_only_a_cleanly_read_workspace(self):
        desired = [{"id": "followup:personal:x:1", "Outcome": "A", "Workspace": "Personal", "Source": "Followup", "Bucket": "Needs me", "Rank": 1, "Owner": "You", "Next move": "n", "Due": "", "Freshness": "fresh", "Link": ""}]
        self.assertEqual([op[0] for op in page_rows.plan([], desired, {"personal"})], ["create"])
        current = [{**desired[0], "page_id": "r1"}]
        self.assertEqual(page_rows.plan(current, desired, {"personal"}), ())
        self.assertEqual(page_rows.plan(current, [], {"bytalos"}), ())                         # personal was not read cleanly: its row stays
        self.assertEqual([op[0] for op in page_rows.plan(current, [], {"personal"})], ["archive"])
        self.assertEqual(page_rows.plan(current, [], {"personal"}, archive=False), ())          # the System health rows are never archived

    def test_no_payload_yet_is_not_read_yet_not_zero(self):
        view = page_view.render([], NOW)
        self.assertEqual(view["tiles"], ["—", "—", "—", "—"])
        self.assertEqual(view["healthy"], "0 of 6")
        self.assertIsNone(view["state"])
        self.assertTrue(all(row["Health"] == "⚪ not read yet" for row in view["health"][:11]))

    def test_due_times_read_as_central_time(self):
        self.assertEqual(page_view.due_text("2026-11-20T15:00:00+00:00"), "Nov 20 · 9:00 AM CT")
        self.assertEqual(page_view.due_text("2026-12-01T01:30:00+00:00"), "Nov 30 · 7:30 PM CT")
        self.assertEqual(page_view.due_text(""), "")


if __name__ == "__main__":
    unittest.main()
