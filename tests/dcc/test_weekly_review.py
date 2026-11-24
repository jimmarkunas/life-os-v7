"""LIFE-945 (synthetic): the Weekly LIFE OS Review stage against an in-memory Notion and GitHub. No network, no real page."""
import itertools
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.dcc import weekly_lanes as wl
from lifeos.dcc import weekly_review as wr
from lifeos.platform.notion_client import rich_text

CT = ZoneInfo("America/Chicago")
SOURCE, REPORT, CARD, HUB, POINTER = "1" * 32, "2" * 32, "3" * 32, "4" * 32, "5" * 32
ENV = {"WEEKLY_REVIEW": "on", "NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID": SOURCE, "DCC_CARD_BLOCK_ID": CARD, "WEEKLY_REVIEW_PAGE_ID": HUB, "WEEKLY_REVIEW_POINTER_BLOCK_ID": POINTER,
       "NOTION_JIRA_TOKEN": "x", "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}
TUE = datetime(2026, 11, 24, 13, 30, tzinfo=CT)
FRI = datetime(2026, 11, 27, 22, 5, tzinfo=CT)


class Notion:
    """Blocks, pages and Weekly Reviews rows in memory. Every write is recorded; only the review's own paths are accepted."""
    def __init__(self, fresh=True, cards=6, dup=False):
        self.blocks, self.rows, self.writes, self.ids = {}, [], [], itertools.count(100)
        self.add(REPORT, "page", None)
        self.add(CARD, "callout", REPORT, "Calendar (11/24 · 1:20 PM CT)")
        self.add(HUB, "page", None)
        self.add(POINTER, "paragraph", None, "Latest review: none")
        self.report_edited = "2026-11-24T19:20:00.000Z" if fresh else "2026-11-23T01:00:00.000Z"
        self.add_header(cards)
        self.card("JIRA Execution", "JIRA Execution (11/24 · 1:25 PM CT)", ["JFM Sprint 22 · 16 Overdue/Blocked · 8 Today · 87 This Week · 1 Done"], fresh)
        self.card("Calendar", "Calendar (11/24 · 1:20 PM CT)", ["Next · 2:00 PM–3:00 PM — A", "2:30 PM–3:30 PM — B", "5:00 PM–5:30 PM — C"], fresh)
        self.card("Mail Alerts", "Mail Alerts (11/24 · 1:20 PM CT)", ["No alerts"], fresh)
        self.card("📈 Hiring Pipeline", "📈 Hiring Pipeline (11/24 · 1:20 PM CT)", ["Status"], fresh)
        self.card("🤝 Recruiter Outreach", "🤝 Recruiter Outreach (11/24 · 1:20 PM CT)", ["Header", "Row one", "Row two"], fresh, table=True)
        if dup:
            self.rows.append(self.row("2026-11-20", "Healthy", False))
            self.rows.append(self.row("2026-11-20", "Healthy", False))

    def add(self, bid, kind, parent, text=""):
        self.blocks[bid] = {"id": bid, "type": kind, "has_children": False, "kids": [], "parent": {"type": "page_id", "page_id": parent} if parent else {}, kind: {"rich_text": rich_text(text)}}
        if parent:
            self.blocks[parent]["kids"].append(bid)
            self.blocks[parent]["has_children"] = True
        return bid

    def add_header(self, cards):
        self.add(f"{next(self.ids):032x}", "paragraph", REPORT, f"{cards} of 6 source cards current")

    def card(self, name, heading, lines, fresh, table=False):
        if not fresh:
            heading = heading.replace("(11/24 · 1:", "(11/20 · 1:")
        if table:
            self.add(f"{next(self.ids):032x}", "heading_3", REPORT, heading)
            tbl = self.add(f"{next(self.ids):032x}", "table", REPORT)
            for line in lines:
                self.add(f"{next(self.ids):032x}", "table_row", tbl, line)
            return
        box = self.add(f"{next(self.ids):032x}", "callout", REPORT)
        self.add(f"{next(self.ids):032x}", "heading_3", box, heading)
        for line in lines:
            self.add(f"{next(self.ids):032x}", "paragraph", box, line)

    def row(self, ending, health, current):
        rid = f"{next(self.ids):032x}"
        self.blocks[rid] = {"id": rid, "type": "page", "kids": [], "has_children": False}
        return {"id": rid, "created_time": "2026-11-21T04:00:00.000Z", "properties": {"Week Ending": {"date": {"start": ending}}, "Source Health": {"select": {"name": health}}, "Current": {"checkbox": current}}}

    def view(self, bid):
        return {k: v for k, v in self.blocks[bid].items() if k != "kids"}

    def call(self, method, path, body=None):
        if method == "GET" and "/children" in path:
            bid = path.split("/blocks/")[1].split("/children")[0]
            return {"results": [self.view(k) for k in self.blocks[bid]["kids"] if not self.blocks[k].get("archived")], "has_more": False}
        if method == "GET" and path.startswith("/blocks/"):
            return self.view(path.split("/blocks/")[1])
        if method == "GET" and path.startswith("/pages/"):
            return {"id": path.split("/pages/")[1], "last_edited_time": self.report_edited}
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": {"Status": {"type": "select"}, "Source Health": {"type": "select"}}}
        if method == "PATCH" and path.endswith("/children"):
            parent = path.split("/blocks/")[1].split("/children")[0] if "/blocks/" in path else path.split("/pages/")[1].split("/children")[0]
            self.writes.append(("children", parent, len(body["children"])))
            for node in body["children"]:
                self.add(f"{next(self.ids):032x}", node["type"], parent)
            return {}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        target = path.split("/")[2]
        self.writes.append((method, path))
        if path.startswith("/blocks/") and target == POINTER:
            self.blocks[POINTER][self.blocks[POINTER]["type"]] = {"rich_text": [{"type": "text", "plain_text": "".join(t["text"]["content"] for t in body["paragraph"]["rich_text"])}]}
            return {}
        if path.startswith("/blocks/") and body.get("archived"):
            self.blocks[target]["archived"] = True
            return {}
        if path.startswith("/pages/"):
            row = next((r for r in self.rows if r["id"] == target), None)
            if row is None:
                raise AssertionError(("a write outside the review's rows", path))
            for key, value in body["properties"].items():
                row["properties"][key] = value
            return {}
        raise AssertionError((method, path))

    def query_data_source(self, source, body):
        assert source == SOURCE
        rows = self.rows
        if "filter" in body:
            rows = [r for r in rows if r["properties"]["Current"].get("checkbox")]
        return {"results": rows, "has_more": False}

    def create(self, props, blocks):
        rid = f"{next(self.ids):032x}"
        self.blocks[rid] = {"id": rid, "type": "page", "kids": [], "has_children": False}
        for node in blocks:
            self.add(f"{next(self.ids):032x}", node["type"], rid)
        self.writes.append(("create", rid, len(blocks)))
        row = {"id": rid, "created_time": "2026-11-24T19:30:00.000Z", "properties": props, "blocks": blocks}
        self.rows.append(row)
        return rid


def runs(ok=True):
    class Reply:
        def __init__(self, doc):
            self.doc = doc

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            import json
            return json.dumps(self.doc).encode()

    def opener(req, timeout=0):
        return Reply({"workflow_runs": [{"conclusion": "success" if ok else "failure", "updated_at": "2026-11-23T20:00:00Z"}]})
    return opener


def drive_ok():
    return {"new": 4, "missing_metadata": 0}


def go(n, now=TUE, live=True, drive=None, opener=None, env=None):
    return wr.run(0, live, {**ENV, **(env or {})}, client=n, now=now, drive_read=drive, opener=opener or runs())


class Schedule(unittest.TestCase):
    def test_the_review_is_due_from_friday_10_pm_ct_and_covers_saturday_to_friday(self):
        self.assertEqual(wr.latest_friday(datetime(2026, 11, 27, 21, 59, tzinfo=CT)), date(2026, 11, 20))
        self.assertEqual(wr.latest_friday(datetime(2026, 11, 27, 22, 0, tzinfo=CT)), date(2026, 11, 27))
        self.assertEqual(wr.latest_friday(datetime(2026, 11, 28, 0, 30, tzinfo=CT)), date(2026, 11, 27))
        self.assertEqual(wr.wanted(FRI, {}), [(date(2026, 11, 27), "natural"), (date(2026, 11, 20), "backfill")])
        self.assertEqual(wr.wanted(FRI, {date(2026, 11, 27): 1, date(2026, 11, 20): 1}), [])

    def test_a_missed_week_is_caught_up_only_inside_48_hours_and_the_backfill_is_made_once(self):
        self.assertEqual(wr.wanted(datetime(2026, 11, 29, 20, 0, tzinfo=CT), {date(2026, 11, 20): 1}), [(date(2026, 11, 27), "natural")])
        self.assertEqual(wr.wanted(datetime(2026, 11, 29, 23, 0, tzinfo=CT), {date(2026, 11, 20): 1}), [])
        self.assertEqual(wr.wanted(TUE, {}), [(date(2026, 11, 20), "backfill")])

    def test_the_properties_use_the_friday_as_week_ending_and_the_saturday_before_as_the_start(self):
        props = wr.properties({"Status": {"type": "status"}, "Source Health": {"type": "select"}}, date(2026, 11, 27), "Degraded", 3, FRI, True)
        self.assertEqual(props["Week Ending"]["date"]["start"], "2026-11-27")
        self.assertEqual(props["Week Start"]["date"]["start"], "2026-11-21")
        self.assertEqual(props["Status"], {"status": {"name": "Generated"}})
        self.assertEqual(props["Source Health"], {"select": {"name": "Degraded"}})
        self.assertEqual(props["Open Decisions"], {"number": 3})


class Run(unittest.TestCase):
    def test_off_means_no_call_and_a_missing_setting_means_not_configured(self):
        n = Notion()
        self.assertEqual(go(n, env={"WEEKLY_REVIEW": ""}), {"status": "off"})
        self.assertEqual(go(n, env={"NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID": ""}), {"status": "not_configured"})
        self.assertEqual(n.writes, [])

    def test_a_dry_run_reads_and_reports_and_writes_nothing(self):
        n = Notion()
        out = go(n, live=False, drive=drive_ok)
        self.assertEqual(out["weeks"][0]["action"], "would_create")
        self.assertEqual(n.writes, [])
        self.assertEqual(n.blocks[POINTER]["paragraph"]["rich_text"][0]["text"]["content"], "Latest review: none")

    def test_the_backfill_week_is_made_once_current_with_the_old_current_cleared_and_the_pointer_set(self):
        n = Notion()
        old = n.row("2026-08-30", "Degraded", True)
        n.rows.append(old)
        out = go(n, drive=drive_ok)
        self.assertEqual([w["action"] for w in out["weeks"]], ["created"])
        made = n.rows[-1]
        self.assertEqual(made["properties"]["Week Ending"]["date"]["start"], "2026-11-20")
        self.assertEqual(made["properties"]["Status"], {"select": {"name": "Generated"}})
        self.assertTrue(made["properties"]["Current"]["checkbox"])
        self.assertEqual(old["properties"]["Current"], {"checkbox": False})
        self.assertIn("week ending Fri Nov 20", n.blocks[POINTER]["paragraph"]["rich_text"][0]["plain_text"])
        self.assertEqual(out["pointer"], "updated")
        again = go(n, drive=drive_ok)
        self.assertEqual(again["weeks"], [])
        self.assertEqual(len([r for r in n.rows if r["properties"]["Week Ending"]["date"]["start"] == "2026-11-20"]), 1)

    def test_the_page_body_has_the_eight_sections_in_the_required_order_and_open_decisions_is_the_queue_length(self):
        n = Notion()
        go(n, drive=drive_ok)
        row = n.rows[-1]
        heads = [b["heading_2"]["rich_text"][0]["text"]["content"] for b in row["blocks"] if b["type"] == "heading_2"]
        self.assertEqual(heads, ["Executive Summary", "Jira Execution & Sprint Health", "Calendar & Commitments", "Recruiters, Applications & Waiting-On", "Notion & Drive Hygiene",
                                 "Automation Health", "Decision Queue", "Next Week Focus"])
        todos = [b for b in row["blocks"] if b["type"] == "to_do"]
        self.assertEqual(row["properties"]["Open Decisions"], {"number": len(todos)})
        self.assertGreaterEqual(len(todos), 3)
        self.assertTrue(any("Backfill" in b["callout"]["rich_text"][0]["text"]["content"] for b in row["blocks"] if b["type"] == "callout"))

    def test_source_health_is_healthy_only_when_all_six_lanes_read_cleanly_and_a_down_drive_alone_marks_only_drive(self):
        healthy = Notion()
        go(healthy, drive=drive_ok)
        self.assertEqual(healthy.rows[-1]["properties"]["Source Health"], {"select": {"name": "Healthy"}})
        n = Notion()
        out = go(n)                                                   # no governed reader connected
        self.assertEqual(n.rows[-1]["properties"]["Source Health"], {"select": {"name": "Degraded"}})
        self.assertEqual(out["weeks"][0]["degraded"], ["Drive"])
        text = " ".join(b[b["type"]]["rich_text"][0]["text"]["content"] for b in n.rows[-1]["blocks"] if b.get("type") in ("paragraph", "bulleted_list_item"))
        self.assertIn("Drive: Degraded (DRIVE_ROUTE_NOT_CONNECTED)", text)

    def test_a_stale_report_a_failed_github_read_and_a_missing_card_each_degrade_only_their_lane(self):
        n = Notion(fresh=False, cards=4)
        out = go(n, drive=drive_ok, opener=runs(ok=False))
        self.assertEqual(set(out["weeks"][0]["degraded"]), {"Jira", "Calendar", "Gmail and Outlook", "Notion", "Automation"})

    def test_the_natural_friday_run_makes_that_week_current_and_the_backfill_is_not_current_once_a_newer_week_exists(self):
        n = Notion()
        n.rows.append(n.row("2026-11-20", "Healthy", True))
        out = go(n, now=FRI, drive=drive_ok)
        self.assertEqual([w["week_ending"] for w in out["weeks"]], ["2026-11-27"])
        self.assertEqual(n.rows[0]["properties"]["Current"], {"checkbox": False})
        self.assertTrue(n.rows[-1]["properties"]["Current"]["checkbox"])
        late = Notion()
        late.rows.append(late.row("2026-11-27", "Healthy", True))
        go(late, now=FRI, drive=drive_ok)
        self.assertFalse(late.rows[-1]["properties"]["Current"]["checkbox"])

    def test_a_degraded_row_inside_48_hours_is_rebuilt_in_place_and_after_48_hours_it_is_left_alone(self):
        n = Notion()
        n.rows.append(n.row("2026-11-20", "Healthy", False))
        n.rows.append(n.row("2026-11-27", "Degraded", True))
        n.rows[1]["created_time"] = "2026-11-28T04:05:00.000Z"
        out = go(n, now=datetime(2026, 11, 28, 20, 0, tzinfo=CT), drive=drive_ok)
        self.assertEqual([w["action"] for w in out["weeks"]], ["rebuilt"])
        self.assertEqual(len(n.rows), 2)
        self.assertTrue(n.rows[1]["properties"]["Generated At"]["date"]["start"].startswith("2026-11-28T20:00"))
        old = Notion()
        old.rows.append(old.row("2026-11-20", "Healthy", False))
        old.rows.append(old.row("2026-11-27", "Degraded", True))
        old.rows[1]["created_time"] = "2026-11-28T04:05:00.000Z"
        self.assertEqual(go(old, now=datetime(2026, 11, 30, 9, 0, tzinfo=CT), drive=drive_ok)["weeks"], [])

    def test_two_rows_for_one_week_fail_closed_and_nothing_is_written(self):
        n = Notion(dup=True)
        out = go(n, drive=drive_ok)
        self.assertEqual(out["status"], "duplicate_weeks")
        self.assertEqual(n.writes, [])

    def test_the_only_writes_are_the_new_row_its_body_the_old_current_flag_and_the_pointer(self):
        n = Notion()
        n.rows.append(n.row("2026-08-30", "Degraded", True))
        go(n, drive=drive_ok)
        kinds = {w[0] for w in n.writes}
        self.assertTrue(kinds <= {"create", "PATCH", "children"})
        self.assertTrue(all(w[1].startswith("/pages/") or w[1].startswith("/blocks/") for w in n.writes if w[0] == "PATCH"))
        self.assertEqual(sum(1 for w in n.writes if w[0] == "create"), 1)


class Lanes(unittest.TestCase):
    NOW = datetime(2026, 11, 24, 13, 30, tzinfo=CT)

    def test_heading_times_parse_and_a_stale_or_old_card_degrades_with_its_reason(self):
        when, stale = wl.heading_time("Calendar (11/24 · 1:01 PM CT)", self.NOW)
        self.assertEqual((when.hour, when.minute, stale), (13, 1, False))
        self.assertEqual(wl.heading_time("JIRA Execution (STALE · last accepted 11/24 · 9:00 AM CT)", self.NOW)[1], True)
        jira = wl.jira({"JIRA Execution": ("JIRA Execution (11/24 · 12:40 PM CT)", [])}, self.NOW)
        self.assertEqual((jira.health, jira.reason), ("Degraded", "CARD_OLDER_THAN_30_MINUTES"))
        self.assertEqual(wl.calendar({}, self.NOW).reason, "CARD_MISSING")

    def test_overlapping_commitments_become_one_schedule_item(self):
        lane = wl.calendar({"Calendar": ("Calendar (11/24 · 1:20 PM CT)", [{"type": "paragraph", "paragraph": {"rich_text": rich_text(t)}} for t in ("2:00 PM–3:00 PM — A", "2:30 PM–3:30 PM — B")])}, self.NOW)
        self.assertEqual(lane.items[0][0], "Schedule")

    def test_a_drive_reader_that_fails_gives_a_degraded_lane_with_the_fixed_code_and_never_raises(self):
        def down():
            raise OSError("DRIVE_ORACLE_UNREACHABLE")
        lane = wl.drive(down)
        self.assertEqual((lane.health, lane.reason), ("Degraded", "DRIVE_ORACLE_UNREACHABLE"))
        self.assertEqual(wl.drive(lambda: {"new": 1, "missing_metadata": 2}).items[0][0], "System Maintenance")


if __name__ == "__main__":
    unittest.main()
