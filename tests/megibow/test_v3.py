"""LIFE-672: Megibow on Jim & Matt V3 — fixtures for Jim's and Matt's callouts (ticket step 9)."""
import unittest
from datetime import date, datetime, timedelta, timezone

from lifeos.megibow import classify as C, project as P, render as R, review, v3, warnings as W
from lifeos.megibow.windows import CHI, visible_weeks
from lifeos.platform.notion_client import NotionError
from lifeos.sources.cards import megibow as stage

NOW = datetime(2026, 11, 13, 14, 0, tzinfo=CHI)          # Friday; the visible Mondays run Sep 21 .. Nov 9
WEEKS = visible_weeks(NOW.date())
def CELL(t):
    return [{"type": "text", "plain_text": t, "text": {"content": t}}]

DASHES = [["Activity", "Sep 21", "Sep 28", "Oct 5", "Oct 12", "Oct 19", "Oct 26", "Nov 2", "Nov 9", "Cumulative"]] + [
    [label] + ["–"] * 9 for label in ("Outreach", "Scheduled", "Networking calls", "Recruiter calls", "Company calls", "Total")]


MATT_ID, GONE_ID = "4183c5a0-5926-8115-b53c-c45f6efd8ec2", "4183c5a0-5926-8115-b53c-000000000000"
JIM_ID, ELSEWHERE_ID = "4183c5a0-5926-81be-9e19-e85714dc256f", "4183c5a0-5926-81be-9e19-111111111111"


def para(i, text):
    return {"id": i, "type": "paragraph", "paragraph": {"rich_text": CELL(text)}}


class Page:
    """One V3 page: Jim's and Matt's callouts, their summary pages, the register and Matt's database. Any write outside a callout or summary page fails the test."""
    def __init__(self, matt_rows=None, matt_down=False, register_block=MATT_ID, jim_block="jim"):
        self.blocks = {}
        self.kids = {}
        for who, note in (("jim", None), ("matt", "A dash means the week is not entered yet. It is never counted as zero.")):
            kids = [f"{who}-h", f"{who}-stamp", f"{who}-tbl"] + ([f"{who}-note"] if note else []) + [f"{who}-sum"]
            self.blocks[who] = {"id": who, "type": "callout", "parent": {"type": "page_id", "page_id": "v3page"}, "callout": {"rich_text": []}}
            self.blocks[f"{who}-h"] = {"id": f"{who}-h", "type": "heading_3", "heading_3": {"rich_text": CELL(f"Megibow ({who.title()})")}}
            self.blocks[f"{who}-stamp"] = para(f"{who}-stamp", "Not updated yet")
            self.blocks[f"{who}-tbl"] = {"id": f"{who}-tbl", "type": "table", "table": {"table_width": 10}}
            if note:
                self.blocks[f"{who}-note"] = para(f"{who}-note", note)
            self.blocks[f"{who}-sum"] = {"id": f"{who}-sum", "type": "paragraph", "paragraph": {"rich_text": CELL("Summary for Claude: ") + [
                {"type": "mention", "plain_text": "V3 summary", "mention": {"type": "page", "page": {"id": f"{who}-page"}}}]}}
            self.kids[who] = kids
            self.kids[f"{who}-tbl"] = []
            for r, row in enumerate(DASHES):
                rid = f"{who}-r{r}"
                self.blocks[rid] = {"id": rid, "type": "table_row", "table_row": {"cells": [CELL(c) for c in row]}}
                self.kids[f"{who}-tbl"].append(rid)
            self.blocks[f"{who}-page-p"] = para(f"{who}-page-p", "Machine page.")
            self.kids[f"{who}-page"] = [f"{who}-page-p"]
        self.blocks["reg-code"] = {"id": "reg-code", "type": "code", "code": {"rich_text": CELL(f"R2  jim.megibow     {jim_block}\nR3  matt.megibow    {register_block}\nR4L jim.week x")}}
        self.kids["register"] = ["reg-code"]
        self.blocks["elsewhere"] = {"id": "elsewhere", "type": "callout", "parent": {"type": "page_id", "page_id": "otherpage"}, "callout": {"rich_text": []}}
        self.matt_rows, self.matt_down = matt_rows or [], matt_down
        self.writes, self.inserted = [], 0

    def snapshot(self):
        return {k: repr(v) for k, v in self.blocks.items()}, {k: list(v) for k, v in self.kids.items()}

    def text(self, block_id):
        return "".join(p.get("plain_text") or p["text"]["content"] for p in self.blocks[block_id][self.blocks[block_id]["type"]]["rich_text"])

    def cells(self, who):
        return [[c[0]["text"]["content"] for c in self.blocks[r]["table_row"]["cells"]] for r in self.kids[f"{who}-tbl"]]

    def call(self, method, path, body=None):
        path = path.replace(MATT_ID, "matt").replace(JIM_ID, "jim").replace(ELSEWHERE_ID, "elsewhere")
        if method == "POST" and path == "/search":
            kind = body["filter"]["value"]
            if kind == "page":
                return {"results": [{"id": "register", "properties": {"Name": {"type": "title", "title": [{"plain_text": v3.REGISTER_TITLE}]}}},
                                    {"id": "other", "properties": {"Name": {"type": "title", "title": [{"plain_text": v3.REGISTER_TITLE + " (old)"}]}}}]}
            return {"results": [{"id": "mattdb", "title": [{"plain_text": v3.MATT_SOURCE_TITLE}]}]}
        if method == "GET" and path == "/data_sources/mattdb":
            if self.matt_down:
                raise NotionError("NOTION_HTTP_404")
            return {"properties": {"Week Start": {"type": "date"}, "Week": {"type": "title"}, **{a: {"type": "number"} for a in C.ACTIVITIES}}}
        if method == "GET" and path.startswith("/data_sources/"):
            return {"parent": {"type": "database_id", "database_id": "review-db"}, "properties": {n: {"type": t} for n, t in review.SCHEMA.items()}}
        if method == "GET" and path.startswith("/pages/"):
            name = v3.V3_PAGE_TITLE if path.endswith("v3page") else "Some other page"
            return {"id": path.split("/")[2], "properties": {"title": {"type": "title", "title": [{"plain_text": name}]}}}
        if method == "GET" and "/children" in path:
            parent = path.split("/")[2]
            if parent not in self.kids:
                raise NotionError("NOTION_HTTP_404")
            return {"results": [self.blocks[k] for k in self.kids[parent]], "has_more": False}
        if method == "GET" and path.startswith("/blocks/"):
            bid = path.split("/")[2]
            if bid not in self.blocks:
                raise NotionError("NOTION_HTTP_404")
            return self.blocks[bid]
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None):
        path = path.replace(MATT_ID, "matt").replace(JIM_ID, "jim").replace(ELSEWHERE_ID, "elsewhere")
        bid = path.split("/")[2]
        if path.endswith("/children"):
            assert bid in ("jim", "matt"), path                                  # a new paragraph only ever goes inside a callout
            new = f"{bid}-notes"
            self.blocks[new] = {"id": new, "type": "paragraph", "paragraph": {"rich_text": [dict(p, plain_text=p["text"]["content"]) for p in body["children"][0]["paragraph"]["rich_text"]]}}
            self.kids[bid].insert(self.kids[bid].index(body["after"]) + 1, new)
            self.inserted += 1
        else:
            assert bid in self.blocks and not bid.startswith(("jim-h", "matt-h", "jim-sum", "matt-sum", "matt-note", "reg")), path
            kind = self.blocks[bid]["type"]
            if kind == "table_row":
                self.blocks[bid]["table_row"]["cells"] = [[dict(c[0], plain_text=c[0]["text"]["content"])] for c in body["table_row"]["cells"]]
            else:
                self.blocks[bid][kind]["rich_text"] = [dict(p, plain_text=p["text"]["content"]) for p in body[kind]["rich_text"]]
        self.writes.append(bid)
        return {}

    def query_data_source(self, source_id, body):
        if source_id == "mattdb":
            return {"results": self.matt_rows, "has_more": False}
        return {"results": [], "has_more": False}

    def update_page_properties(self, page_id, props):
        raise AssertionError("no review rows in these fixtures")


def row(monday, outreach, scheduled=0, net=0, rec=0, comp=0, edited="2026-11-01T00:00:00Z"):
    nums = dict(zip(C.ACTIVITIES, (outreach, scheduled, net, rec, comp), strict=True))
    return {"id": f"p{monday}", "last_edited_time": edited, "properties": {"Week Start": {"date": {"start": monday.isoformat()}}, **{a: {"number": n} for a, n in nums.items()}}}


def matt(page, live=True, now=NOW):
    return stage._matt(page, now, live)


class Matt(unittest.TestCase):
    def test_all_eight_weeks_are_shown_with_cumulative_and_warnings_evaluated(self):
        page = Page([row(w, 5, 1, 1, 1, 1) for w in WEEKS[:-1]] + [row(WEEKS[-1], 0, 0, 0, 0, 0)])
        out = matt(page)
        self.assertEqual((out["status"], out["entered"]), ("OK", 8))
        t = page.cells("matt")
        self.assertEqual(t[1], ["Outreach"] + ["5"] * 7 + ["0", "35"])
        self.assertEqual(t[6], ["Total"] + ["9"] * 7 + ["0", "63"])
        notes = page.text("matt-notes")
        self.assertIn(W.NO_MEETINGS, notes)                                  # Friday, the week is entered with Scheduled 0
        self.assertIn(W.OUTREACH_DROP, notes)
        self.assertIn(W.CONVERSATION_DROP, notes)
        self.assertTrue(page.text("matt-stamp").startswith("Last refreshed 11/13 · 2:00 PM CT"))
        self.assertEqual(page.kids["matt"][-2:], ["matt-notes", "matt-sum"])  # notes sit after the dash note, before the summary link
        self.assertEqual(page.kids["matt"][3], "matt-note")

    def test_some_weeks_show_dashes_never_zero_and_cumulative_adds_entered_weeks_only(self):
        page = Page([row(WEEKS[1], 3, 1, 0, 2, 0), row(WEEKS[4], 4, 0, 1, 0, 1)])
        out = matt(page)
        t = page.cells("matt")
        self.assertEqual(t[1], ["Outreach", "–", "3", "–", "–", "4", "–", "–", "–", "7"])
        self.assertEqual(t[6], ["Total", "–", "6", "–", "–", "6", "–", "–", "–", "12"])
        self.assertEqual((out["entered"], out["warnings"]), (2, 0))

    def test_no_entries_keeps_every_cell_a_dash(self):
        page = Page([])
        out = matt(page)
        self.assertEqual(page.cells("matt"), DASHES)
        self.assertEqual(out["status"], "OK")
        self.assertIn("Warnings start once 4 weeks are entered (0 so far)", page.text("matt-notes"))

    def test_fewer_than_four_entered_weeks_shows_no_warning(self):
        page = Page([row(WEEKS[-3], 9, 2, 3, 0, 0), row(WEEKS[-2], 9, 2, 3, 0, 0), row(WEEKS[-1], 0, 0, 0, 0, 0)])
        out = matt(page)
        self.assertEqual(out["warnings"], 0)
        self.assertNotIn("⚠", page.text("matt-notes"))

    def test_an_unentered_current_week_fires_no_warning(self):
        page = Page([row(w, 5, 1, 1, 1, 0) for w in WEEKS[:-1]])
        out = matt(page)
        self.assertEqual(out["warnings"], 0)
        self.assertEqual(page.cells("matt")[1][8], "–")

    def test_unreadable_database_is_degraded_and_keeps_the_last_accepted_rows(self):
        page = Page([row(WEEKS[1], 3)])
        matt(page)
        before = page.cells("matt")
        page.matt_down = True
        out = matt(page, now=NOW + timedelta(hours=1))
        self.assertEqual(out["status"], "DEGRADED")
        self.assertEqual(page.cells("matt"), before)
        self.assertTrue(page.text("matt-stamp").startswith("DEGRADED at 11/13 · 3:00 PM CT"))

    def test_missing_target_block_writes_nothing(self):
        page = Page([row(WEEKS[1], 3)], register_block=GONE_ID)
        before = page.snapshot()
        out = matt(page)
        self.assertEqual((out["status"], out["code"]), ("DEGRADED", "MEGIBOW_V3_BLOCK_NOT_FOUND"))
        self.assertEqual(page.snapshot(), before)

    def test_second_run_is_a_no_op_and_dry_run_writes_nothing(self):
        page = Page([row(WEEKS[1], 3)])
        dry = matt(page, live=False)
        self.assertEqual((page.writes, dry["rows_changed"]), ([], 6))          # every activity row: the week and Cumulative
        matt(page)
        writes = len(page.writes)
        matt(page)
        self.assertEqual((len(page.writes), page.inserted), (writes, 1))

    def test_summary_page_is_plain_and_at_most_1000_characters(self):
        page = Page([row(w, 5, 1, 1, 1, 0) for w in WEEKS])
        matt(page)
        text = page.text("matt-page-p")
        self.assertLessEqual(len(text), v3.SUMMARY_MAX)
        self.assertIn("Week of Sep 21: 5/1/1/1/0 = 8", text)

    def test_duplicate_and_blank_rows(self):
        dup = [row(WEEKS[2], 1, edited="2026-10-01T00:00:00Z"), row(WEEKS[2], 4, edited="2026-10-02T00:00:00Z"), row(WEEKS[3], None, None, None, None, None)]
        got, ignored = v3.matt_rows(Page(dup), "mattdb")
        self.assertEqual((got[WEEKS[2]][C.OUTREACH], WEEKS[3] in got, ignored), (4, False, 1))


ENV = {"MEGIBOW_BLOCK_ID": "jim", "MEGIBOW_REVIEW_DB_ID": "abc123", "NOTION_JIRA_TOKEN": "t"}


class Gmail:
    def __init__(self, records):
        self.records = records

    def list_ids_complete(self, query, limit):
        return list(self.records)

    def sent_record(self, i):
        return self.records[i]

    def profile_address(self):
        return "jim@example.com"


class Gcal:
    def list_events(self, a, b):
        return []


def sent(i, when):
    return {"id": i, "to": "pat@lensa.com", "cc": "", "subject": "Hello", "snippet": "", "sent_at": when, "bulk": False}


def run(page, env=ENV, gcal=None):
    gm = Gmail({"a": sent("a", datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)), "b": sent("b", datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))})
    return stage.run(0, True, environ=env, gmail=gm, gcal=gcal or Gcal(), outlook_clients=[], notion=page, now=NOW, known={"lensa"})


class Jim(unittest.TestCase):
    def test_jims_v3_callout_is_written_and_matt_is_isolated(self):
        page = Page([row(WEEKS[1], 3)])
        counts = run(page)
        self.assertEqual((counts["layout"], counts["matt"]["status"]), ("v3", "OK"))
        t = page.cells("jim")
        self.assertEqual(t[0][-1], "Cumulative")
        self.assertEqual((t[1][-2], t[1][-3], t[6][-2]), ("1", "1", "1"))      # Outreach this week and last week; the current week's Total
        self.assertTrue(page.text("jim-stamp").startswith("Last refreshed 11/13 · 2:00 PM CT"))
        self.assertIn("Review needed — none · MegIBOW Review", page.text("jim-notes"))
        link = [p for p in page.blocks["jim-notes"]["paragraph"]["rich_text"] if p["text"].get("link")]
        self.assertEqual(link[0]["text"]["link"]["url"], "https://www.notion.so/reviewdb")
        self.assertIn("Week of Nov 9:", page.text("jim-page-p"))

    def test_jims_callout_updates_when_matts_database_is_down(self):
        page = Page([row(WEEKS[1], 3)], matt_down=True)
        counts = run(page)
        self.assertEqual((counts["matt"]["status"], counts["failed"]), ("DEGRADED", 1))
        self.assertTrue(page.text("jim-stamp").startswith("Last refreshed"))
        self.assertTrue(page.text("matt-stamp").startswith("DEGRADED at"))

    def test_jims_missing_block_writes_nothing_for_jim_and_matt_still_runs(self):
        page = Page([row(WEEKS[1], 3)])
        with self.assertRaises(NotionError) as caught:
            run(page, env=dict(ENV, MEGIBOW_BLOCK_ID="nope"))
        self.assertIn("MEGIBOW_V3_BLOCK_NOT_FOUND", str(caught.exception))
        self.assertFalse(any(w.startswith("jim") for w in page.writes))
        self.assertTrue(any(w.startswith("matt") for w in page.writes))

    def test_a_failed_source_keeps_jims_table_and_says_degraded(self):
        class Down(Gcal):
            def list_events(self, a, b):
                from lifeos.platform.gcal import GcalError
                raise GcalError("GCAL_DOWN")
        page = Page()
        with self.assertRaises(stage.MegibowError):
            run(page, gcal=Down())
        self.assertEqual(page.cells("jim"), DASHES)
        self.assertTrue(page.text("jim-stamp").startswith("DEGRADED at"))

    def test_the_register_row_names_jims_callout_so_no_github_value_is_needed(self):
        page = Page([row(WEEKS[1], 3)], jim_block=JIM_ID)
        counts = run(page, env=dict(ENV, MEGIBOW_BLOCK_ID="v2-block-that-must-not-be-touched"))
        self.assertEqual((counts["block_source"], counts["layout"]), ("register", "v3"))
        self.assertTrue(page.text("jim-stamp").startswith("Last refreshed"))

    def test_a_register_row_pointing_off_the_v3_page_is_refused(self):
        page = Page([row(WEEKS[1], 3)], register_block=ELSEWHERE_ID)
        before = page.snapshot()
        out = matt(page)
        self.assertEqual((out["status"], out["code"]), ("DEGRADED", "MEGIBOW_V3_TARGET_NOT_FOUND"))
        self.assertEqual(page.snapshot(), before)

    def test_jims_v3_totals_equal_the_v2_table_for_the_same_weeks(self):
        outcomes = [{"status": C.COUNTED, "activity": a, "week": w, "key": f"{a}{w}{i}", "reason": "x"} for i, (a, w) in enumerate(
            [(C.OUTREACH, WEEKS[-1]), (C.OUTREACH, WEEKS[-2]), (C.SCHEDULED, WEEKS[-2]), (C.RECRUITER, WEEKS[-3]), (C.COMPANY, WEEKS[-1])])]
        frozen = {WEEKS[-4]: {a: 2 for a in C.ACTIVITIES}, WEEKS[-5]: {a: 1 for a in C.ACTIVITIES}}
        proj = P.project(outcomes, NOW.date(), frozen, {a: 3 for a in C.ACTIVITIES}, date(2026, 10, 5), {WEEKS[0]: {a: 1 for a in C.ACTIVITIES}, WEEKS[1]: {a: 0 for a in C.ACTIVITIES}})
        old, new = R.table(proj), v3.table(proj["weeks"], proj["counts"], proj["cumulative"])
        for r in range(1, 7):
            self.assertEqual(old[r][2:], new[r][1:9])                       # every week, every row (the V2 table has Cumulative second, V3 last)
            self.assertEqual(old[r][1], new[r][9])


if __name__ == "__main__":
    unittest.main()
