"""LIFE-933 (synthetic): the Weekly Reports Archive writer against an in-memory Notion. No network, no real page."""
import itertools
import os
import unittest
from unittest import mock
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.dcc import weekly_archive as wa
from lifeos.platform import full_width
from lifeos.platform.notion_client import rich_text

CT = ZoneInfo("America/Chicago")
ARCHIVE, REPORT, CARD = "a" * 32, "b" * 32, "c" * 32
PAGES = ("page", "child_page")


class Notion:
    """Blocks and pages in one dict. Reads are by ID; the only writes allowed are the ones the archive may make."""
    def __init__(self):
        self.blocks, self.ids, self.writes, self.full_width = {}, itertools.count(1), [], []
        self.add(ARCHIVE, "page", None, title="Weekly Reports Archive")
        self.add(REPORT, "page", None, title="Daily LIFE OS Report (Hourly)")

    def new(self):
        return f"{next(self.ids):032x}"

    def add(self, block_id, kind, parent, payload=None, title=None):
        parent_ref = {}
        if parent:
            on_page = self.blocks[parent]["type"] in PAGES
            parent_ref = {"type": "page_id", "page_id": parent} if on_page else {"type": "block_id", "block_id": parent}
        node = {"id": block_id, "type": kind, "parent": parent_ref, "has_children": False, kind: payload or {}, "kids": []}
        if title is not None:
            node["title"] = title
        self.blocks[block_id] = node
        if parent:
            self.blocks[parent]["kids"].append(block_id)
            self.blocks[parent]["has_children"] = True
        return block_id

    def text(self, parent, kind, text, **extra):
        return self.add(self.new(), kind, parent, {"rich_text": rich_text(text), **extra})

    def live_kids(self, bid):
        return [k for k in self.blocks[bid]["kids"] if not self.blocks[k].get("archived")]

    def view(self, bid):
        b = self.blocks[bid]
        out = {k: v for k, v in b.items() if k not in ("kids", "title")}
        out["has_children"] = bool(self.live_kids(bid))
        if b["type"] == "child_page":
            out["child_page"] = {"title": b["title"]}
        return out

    def call(self, method, path, body=None):
        if method == "GET" and "/children" in path:
            bid = path.split("/blocks/")[1].split("/children")[0]
            return {"results": [self.view(k) for k in self.live_kids(bid)], "has_more": False, "next_cursor": None}
        if method == "GET" and path.startswith("/pages/"):
            return {"id": path.split("/pages/")[1], "object": "page", "archived": bool(self.blocks[path.split("/pages/")[1]].get("archived"))}
        if method == "GET" and path.startswith("/blocks/"):
            return self.view(path.split("/blocks/")[1])
        if method == "DELETE" and path.startswith("/blocks/"):
            self.blocks[path.split("/blocks/")[1]]["archived"] = True
            return {}
        raise AssertionError((method, path))

    def make(self, node, parent):
        """Notion's rule: a table is created with its rows in the same request (a bare table is a 400)."""
        payload = dict(node[node["type"]])
        rows = payload.pop("children", None)
        if node["type"] == "table":
            if not rows:
                raise AssertionError("NOTION_HTTP_400: table created without rows")
        elif rows:
            raise AssertionError("children inside a non-table payload")
        block = self.add(self.new(), node["type"], parent, payload)
        for row in rows or []:
            self.make(row, block)
        return block

    def call_once(self, method, path, body=None):
        self.writes.append((method, path))
        if method == "PATCH" and path.endswith("/children"):
            parent = path.split("/blocks/")[1].split("/children")[0]
            return {"results": [{"id": self.make(node, parent)} for node in body["children"]]}
        if method == "POST" and path == "/pages":                         # D16.1: only a page made from the Full Width template is full width; the template lands with one empty paragraph and takes no children
            assert body["parent"]["page_id"] in self.blocks
            assert body.get("template") == {"type": "template_id", "template_id": "e" * 32} and "children" not in body, "page not created from the Full Width template"
            title = "".join(p["text"]["content"] for p in body["properties"]["title"]["title"])
            pid = self.add(self.new(), "child_page", body["parent"]["page_id"], {}, title=title)
            self.full_width.append(pid)
            self.text(pid, "paragraph", "")
            return {"id": pid}
        if method == "PATCH" and (path.startswith("/pages/") or path.startswith("/blocks/")):
            self.blocks[path.split("/")[2]]["archived"] = bool(body.get("archived"))
            return {}
        raise AssertionError((method, path))


def report(n, with_notes=None):
    """A Daily Report page: two columns, a callout with bullets, a table, a linked database view, and optionally a Notes callout. -> the Notes callout id."""
    cols = n.add(n.new(), "column_list", REPORT, {})
    left, right = n.add(n.new(), "column", cols, {}), n.add(n.new(), "column", cols, {})
    n.add(CARD, "callout", left, {"rich_text": rich_text("Command Center")})
    call = n.text(right, "callout", "Calendar (11/23 · 3:00 PM CT)", icon={"type": "emoji", "emoji": "📅"})
    n.text(call, "bulleted_list_item", "10:00 AM Standup")
    n.text(call, "bulleted_list_item", "2:00 PM Review")
    table = n.add(n.new(), "table", right, {"table_width": 2, "has_column_header": True, "has_row_header": False})
    n.add(n.new(), "table_row", table, {"cells": [rich_text("Pool"), rich_text("Used")]})
    n.add(n.new(), "table_row", table, {"cells": [rich_text("Codex"), rich_text("2%")]})
    n.add(n.new(), "child_database", right, {"title": "Bills"})
    if with_notes is None:
        return None
    box = n.text(right, "callout", "Notes")
    for line in with_notes:
        n.text(box, "paragraph", line)
    return box


os.environ["LIFEOS_NOTION_FULL_WIDTH_TEMPLATE"] = "e" * 32
full_width.WAIT = 0
ENV = {"WEEKLY_ARCHIVE_PAGE_ID": ARCHIVE, "DCC_CARD_BLOCK_ID": CARD}
TUE_6PM = datetime(2026, 11, 24, 18, 5, tzinfo=CT)


def titles(n, page):
    return [n.blocks[k]["title"] for k in n.live_kids(page) if n.blocks[k]["type"] == "child_page"]


class DailySnapshot(unittest.TestCase):
    def test_the_first_run_after_six_pm_makes_the_week_page_and_one_static_snapshot_read_back(self):
        n = Notion()
        report(n)
        out = wa.run(0, True, ENV, n, TUE_6PM)
        self.assertEqual((out["snapshot"], out["live_views_skipped"], out["notes"]), ("created", 1, "none"))
        self.assertEqual(titles(n, ARCHIVE), ["Weekly Reports — Week Ending Nov 29, 2026"])
        week = n.live_kids(ARCHIVE)[0]
        self.assertEqual(titles(n, week), ["Daily LIFE OS Report — Nov 24, 2026"])
        snap = n.live_kids(week)[-1]
        self.assertNotIn("child_database", [n.blocks[k]["type"] for k in n.live_kids(snap)])               # no live view is copied
        self.assertTrue(wa._complete(n, snap))
        text = " ".join(t for _, _, t in wa.digest(wa.read_tree(n, snap)))
        for want in ("10:00 AM Standup", "Codex | 2%", "Command Center", "Frozen from the Nov 24, 2026 V7 Daily Report taken at 6:05 PM CT"):
            self.assertIn(want, text)                                                                        # columns are flattened in order

    def test_a_rerun_the_same_day_is_a_no_op_and_before_six_pm_nothing_is_written(self):
        n = Notion()
        report(n)
        wa.run(0, True, ENV, n, TUE_6PM)
        before = len(n.writes)
        self.assertEqual(wa.run(0, True, ENV, n, TUE_6PM.replace(hour=21))["snapshot"], "exists")
        self.assertEqual(len(n.writes), before)
        early = Notion()
        report(early)
        self.assertEqual(wa.run(0, True, ENV, early, TUE_6PM.replace(hour=17, minute=59))["snapshot"], "not_due")
        self.assertEqual(early.writes, [])

    def test_a_dry_run_writes_nothing_and_each_week_gets_its_own_page(self):
        n = Notion()
        report(n)
        self.assertEqual(wa.run(0, False, ENV, n, TUE_6PM)["snapshot"], "would_create_week")
        self.assertEqual(n.writes, [])
        wa.run(0, True, ENV, n, TUE_6PM)
        wa.run(0, True, ENV, n, TUE_6PM.replace(day=25))                                                     # Wednesday, same week
        wa.run(0, True, ENV, n, datetime(2026, 11, 30, 18, 1, tzinfo=CT))                                    # Monday, the next week
        self.assertEqual(set(titles(n, ARCHIVE)), {"Weekly Reports — Week Ending Nov 29, 2026", "Weekly Reports — Week Ending Dec 6, 2026"})
        week = [k for k in n.live_kids(ARCHIVE) if n.blocks[k]["title"].endswith("Nov 29, 2026")][0]
        self.assertEqual(sorted(titles(n, week)), ["Daily LIFE OS Report — Nov 24, 2026", "Daily LIFE OS Report — Nov 25, 2026"])

    def test_an_interrupted_snapshot_is_set_aside_and_written_afresh_and_a_duplicate_fails_closed(self):
        n = Notion()
        report(n)
        wa.run(0, True, ENV, n, TUE_6PM)
        week = n.live_kids(ARCHIVE)[0]
        snap = n.live_kids(week)[-1]
        n.blocks[n.live_kids(snap)[-1]]["archived"] = True                                                   # lose the completion marker: an interrupted write
        self.assertEqual(wa.run(0, True, ENV, n, TUE_6PM)["snapshot"], "created")
        self.assertTrue(n.blocks[snap].get("archived"))
        n.add(n.new(), "child_page", week, {}, title="Daily LIFE OS Report — Nov 24, 2026")
        with self.assertRaises(wa.ArchiveError) as caught:
            wa.run(0, True, ENV, n, TUE_6PM)
        self.assertEqual(str(caught.exception), "ARCHIVE_DUPLICATE_SNAPSHOT")

    def test_not_configured_makes_no_call_and_the_week_arithmetic_matches_the_existing_sep_13_page(self):
        self.assertEqual(wa.run(0, True, {}, Notion(), TUE_6PM), {"status": "not_configured"})
        self.assertEqual(wa.week_end(datetime(2026, 9, 7).date()).isoformat(), "2026-09-13")
        self.assertEqual(wa.label(datetime(2026, 9, 13).date()), "Sep 13, 2026")


class FullWidthAndFailClosed(unittest.TestCase):
    def test_every_page_it_creates_comes_from_the_full_width_template_and_the_filler_is_gone(self):
        n = Notion()
        report(n)
        wa.run(0, True, ENV, n, TUE_6PM)                                       # the fake refuses a page created without the template or with children in the create request
        week = n.live_kids(ARCHIVE)[0]
        snap = n.live_kids(week)[-1]
        self.assertEqual(n.full_width, [week, snap])
        for page in (week, snap):
            first = n.blocks[n.live_kids(page)[0]]
            self.assertNotEqual((first["type"], first["paragraph"]["rich_text"] if first["type"] == "paragraph" else None), ("paragraph", []))   # the template's empty paragraph is not left on top

    def test_an_empty_body_fails_closed_and_makes_no_page(self):
        n = Notion()
        n.add(CARD, "child_database", REPORT, {"title": "Command Center"})                              # a report whose only content is a live view: nothing static to copy
        with self.assertRaises(wa.ArchiveError) as caught:
            wa.run(0, True, ENV, n, TUE_6PM)
        self.assertEqual(str(caught.exception), "ARCHIVE_EMPTY_SNAPSHOT")
        self.assertEqual([t for t in titles(n, n.live_kids(ARCHIVE)[0]) if t.startswith("Daily")], [])     # the week page exists, no daily page was made

    def test_a_missing_template_makes_no_page_at_all(self):
        n = Notion()
        report(n)
        with mock.patch.dict(os.environ, {"LIFEOS_NOTION_FULL_WIDTH_TEMPLATE": ""}):
            with self.assertRaises(wa.ArchiveError) as caught:
                wa.run(0, True, ENV, n, TUE_6PM)
        self.assertEqual((str(caught.exception), titles(n, ARCHIVE), n.writes), ("ARCHIVE_TEMPLATE_MISSING", [], []))

    def test_a_week_page_left_blank_by_an_interrupted_run_is_filled_not_duplicated(self):
        n = Notion()
        report(n)
        blank = n.add(n.new(), "child_page", ARCHIVE, {}, title="Weekly Reports — Week Ending Nov 29, 2026")
        wa.run(0, True, ENV, n, TUE_6PM)
        self.assertEqual(titles(n, ARCHIVE), ["Weekly Reports — Week Ending Nov 29, 2026"])
        self.assertTrue(n.live_kids(blank))


class NotesFreezeAndMondayReset(unittest.TestCase):
    SUN_6PM = datetime(2026, 11, 29, 18, 2, tzinfo=CT)
    MON_8AM = datetime(2026, 11, 30, 8, 0, tzinfo=CT)

    def test_no_notes_callout_means_nothing_is_made_or_reset(self):
        n = Notion()
        report(n)
        out = wa.run(0, True, ENV, n, self.SUN_6PM)
        self.assertEqual((out["notes"], "weekly_notes" in out), ("none", False))
        self.assertEqual(wa.run(0, True, ENV, n, self.MON_8AM)["notes"], "none")

    def test_sunday_freezes_the_notes_once_and_monday_resets_only_after_the_snapshot_re_verifies(self):
        n = Notion()
        box = report(n, with_notes=["call Smith", "send thanks"])
        self.assertEqual(wa.run(0, True, ENV, n, self.SUN_6PM)["weekly_notes"], "created")
        self.assertEqual(wa.run(0, True, ENV, n, self.SUN_6PM.replace(hour=20))["weekly_notes"], "exists")
        self.assertEqual(wa.run(0, True, ENV, n, self.MON_8AM)["reset"], "reset")
        self.assertEqual([t for _, _, t in wa.digest(wa.freeze(n, box, []))], [""])                           # live Notes now one empty paragraph
        self.assertEqual(wa.run(0, True, ENV, n, self.MON_8AM.replace(hour=9))["reset"], "already_empty")

    def test_a_missing_or_different_or_duplicate_snapshot_keeps_the_live_notes(self):
        n = Notion()
        box = report(n, with_notes=["call Smith"])
        self.assertEqual(wa.run(0, True, ENV, n, self.MON_8AM)["reset"], "kept_no_week_page")
        wa.run(0, True, ENV, n, self.SUN_6PM)
        n.text(box, "paragraph", "added after the snapshot")                                                   # the live Notes no longer equal the snapshot
        self.assertEqual(wa.run(0, True, ENV, n, self.MON_8AM)["reset"], "kept_mismatch")
        self.assertEqual(wa.run(0, False, ENV, n, self.MON_8AM)["reset"], "kept_mismatch")
        week = n.live_kids(ARCHIVE)[0]
        n.add(n.new(), "child_page", week, {}, title="Weekly Notes — Week Ending Nov 29, 2026")
        self.assertEqual(wa.run(0, True, ENV, n, self.MON_8AM)["reset"], "kept_duplicate_snapshot")
        self.assertEqual(len(n.live_kids(box)), 2)                                                           # nothing was removed

class ArchivePageText(unittest.TestCase):
    """LIFE-954: the writer lists only the archive page's child pages; no heading or paragraph on it is read."""
    def test_the_same_snapshots_are_made_with_the_old_sections_or_the_trimmed_page(self):
        outs = []
        for lines in (("Archive Model", "Production Owner", "Production Status", "Gap: No snapshots Sep 14 to Nov 22, 2026"),
                      ("One page per week.", "Rules: Weekly Reports Archive - machine contract"), ()):
            n = Notion()
            for text in lines:
                n.text(ARCHIVE, "heading_2" if text.startswith(("Archive", "Production")) else "paragraph", text)
            n.add(n.new(), "child_page", ARCHIVE, {}, title="Weekly Reports - Week Ending Sep 13, 2026")
            report(n)
            out = wa.run(0, True, ENV, n, TUE_6PM)
            outs.append((out["snapshot"], titles(n, ARCHIVE)[-1]))
            self.assertIn("Weekly Reports - Week Ending Sep 13, 2026", titles(n, ARCHIVE))      # the old week page stays listed, untouched
        self.assertEqual(len(set(outs)), 1)

    def test_the_archive_is_found_by_its_page_id_wherever_its_parent_is(self):
        """LIFE-944: moving the archive under the Reports & Archives hub changes nothing; the id alone finds it, and the week pages stay listed."""
        outs = []
        for hub in (False, True):
            n = Notion()
            n.add(n.new(), "child_page", ARCHIVE, {}, title="Weekly Reports - Week Ending Sep 13, 2026")
            if hub:
                hub_id = n.add("d" * 32, "page", None, title="Reports & Archives")
                n.blocks[ARCHIVE]["parent"] = {"type": "page_id", "page_id": hub_id}
                n.blocks[hub_id]["kids"].append(ARCHIVE)
            report(n)
            out = wa.run(0, True, ENV, n, TUE_6PM)
            self.assertEqual(wa.run(0, True, ENV, n, TUE_6PM)["snapshot"], "exists")            # a second run does not duplicate
            outs.append((out["snapshot"], sorted(titles(n, ARCHIVE))))
            self.assertEqual((out["archive"]["root"], out["archive"]["archived"], out["archive"]["weeks"]), ("readable", False, ["Weekly Reports - Week Ending Sep 13, 2026"]))
        self.assertEqual(outs[0], outs[1])
        self.assertIn("Weekly Reports - Week Ending Sep 13, 2026", outs[1][1])


if __name__ == "__main__":
    unittest.main()
