"""LIFE-687 (synthetic): Jim's weekly database is the history and the source of his lifetime Cumulative; Matt's lifetime and both history links."""
import unittest
from datetime import date, datetime, timezone

from lifeos.megibow import classify as C, project as P, v3, weekly
from lifeos.sources.cards import megibow as stage
from tests.megibow import test_v3 as T

SHEET = {  # Jim's 16 sheet weeks (LIFE-687): Outreach, Scheduled, Networking, Recruiter, Company
    date(2026, 7, 27): (2, 0, 0, 6, 0), date(2026, 8, 3): (5, 2, 1, 7, 0), date(2026, 8, 10): (2, 4, 1, 5, 4), date(2026, 8, 17): (4, 0, 1, 5, 4), date(2026, 8, 24): (4, 1, 5, 3, 0),
    date(2026, 8, 31): (1, 0, 0, 3, 1), date(2026, 9, 7): (1, 3, 0, 2, 1), date(2026, 9, 14): (1, 1, 2, 1, 3), date(2026, 9, 21): (0, 1, 1, 3, 1), date(2026, 9, 28): (0, 1, 0, 1, 1),
    date(2026, 10, 5): (0, 0, 1, 2, 0), date(2026, 10, 12): (0, 0, 0, 4, 0), date(2026, 10, 19): (0, 0, 0, 0, 0), date(2026, 10, 26): (0, 2, 0, 2, 0),
    date(2026, 11, 2): (4, 0, 0, 1, 0), date(2026, 11, 9): (0, 0, 0, 0, 0)}
LIFETIME = {"Outreach": 24, "Scheduled": 15, "Networking Calls": 12, "Recruiter Calls": 45, "Company Calls": 15}


def wrow(monday, nums, source="Sheet", over=None, edited="2026-11-01T00:00:00Z"):
    props = {"Week Start": {"date": {"start": monday.isoformat()}}, "Source": {"select": {"name": source}},
             **{a: {"number": n} for a, n in zip(C.ACTIVITIES, nums, strict=True)}, **{o: {"number": (over or {}).get(a)} for a, o in weekly.OVERRIDE.items()}}
    return {"id": f"w{monday}", "last_edited_time": edited, "properties": props}


class WeeklyPage(T.Page):
    """The V3 fake plus Jim's weekly database and the two history pages."""
    def __init__(self, jim_rows, history=True, schema_ok=True, **kw):
        super().__init__(**kw)
        self.jim_rows, self.history, self.schema_ok, self.created = list(jim_rows), history, schema_ok, []

    def call(self, method, path, body=None):
        if method == "POST" and path == "/search":
            kind = body["filter"]["value"]
            base = super().call(method, path, body)
            if kind == "page" and self.history:
                base["results"] += [{"id": "histjim", "properties": {"title": {"type": "title", "title": [{"plain_text": v3.HISTORY_TITLE["Jim"]}]}}},
                                    {"id": "histmatt", "properties": {"title": {"type": "title", "title": [{"plain_text": v3.HISTORY_TITLE["Matt"]}]}}}]
            if kind == "data_source":
                base["results"].append({"id": "jimdb", "title": [{"plain_text": weekly.JIM_SOURCE_TITLE}]})
            return base
        if method == "GET" and path == "/data_sources/jimdb":
            types = dict(weekly.FIELDS)
            if not self.schema_ok:
                types.pop("Source")
            return {"properties": {n: {"type": t} for n, t in types.items()}}
        return super().call(method, path, body)

    def query_data_source(self, source_id, body):
        return {"results": self.jim_rows, "has_more": False} if source_id == "jimdb" else super().query_data_source(source_id, body)

    def call_once(self, method, path, body=None):
        if method == "POST" and path == "/pages":
            p = body["properties"]
            self.created.append(date.fromisoformat(p["Week Start"]["date"]["start"]))
            self.jim_rows.append({"id": f"new{len(self.created)}", "last_edited_time": "2026-11-20T00:00:00Z", "properties": {
                **p, **{o: {"number": None} for o in weekly.OVERRIDE.values()}}})
            return {}
        return super().call_once(method, path, body)

    def update_page_properties(self, page_id, props):
        for r in self.jim_rows:
            if r["id"] == page_id:
                r["properties"].update(props)
                self.created.append(("finalized", page_id))
                return {}
        raise AssertionError(page_id)


def sheet_rows(changes=None):
    return [wrow(w, nums, **(changes or {}).get(w, {})) for w, nums in SHEET.items()]


def run(page, now=T.NOW, mail=True):
    gm = T.Gmail({"a": T.sent("a", datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)), "b": T.sent("b", datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))} if mail else {})
    return stage.run(0, True, environ=T.ENV, gmail=gm, gcal=T.Gcal(), outlook_clients=[], notion=page, now=now, known={"lensa"})


def cumulative(page):
    return {a: int(page.cells("jim")[i][-1]) for i, a in enumerate(C.ACTIVITIES, start=1)}


class History(unittest.TestCase):
    def test_the_loaded_sheet_gives_jims_lifetime_cumulative_of_111(self):
        page = WeeklyPage(sheet_rows())
        run(page, mail=False)
        self.assertEqual(cumulative(page), LIFETIME)
        self.assertEqual(page.cells("jim")[6][-1], "111")
        self.assertEqual(page.cells("jim")[0][1:4], ["Sep 21", "Sep 28", "Oct 5"])
        self.assertEqual([r[1] for r in page.cells("jim")[1:6]], ["0", "1", "1", "3", "1"])                      # the Sep 21 week as typed in the sheet

    def test_a_week_that_leaves_the_eight_week_table_still_counts(self):
        later = datetime(2026, 12, 4, 14, 0, tzinfo=T.CHI)
        page = WeeklyPage(sheet_rows())
        run(page, now=later, mail=False)
        self.assertNotIn("Sep 21", page.cells("jim")[0])                                                         # gone from the table
        self.assertEqual(cumulative(page), LIFETIME)                                                             # but still in the lifetime total

    def test_a_finished_week_with_a_row_is_frozen_and_the_open_week_is_live(self):
        page = WeeklyPage(sheet_rows())
        run(page)                                                                                                # the engine reads 1 Outreach in the Nov 2 week and 1 in the open week
        t = page.cells("jim")
        self.assertEqual((t[1][-3], t[1][-2]), ("4", "1"))                                                       # Nov 2: the sheet's 4 stays; Nov 9: live 1, not the loaded 0
        self.assertEqual(cumulative(page)["Outreach"], 25)                                                       # 24 + the open week's live 1
        self.assertEqual(page.created, [])                                                                       # nothing to write while the weeks are still open or already rowed

    def test_a_typed_override_replaces_the_number_marks_it_and_counts(self):
        page = WeeklyPage(sheet_rows({date(2026, 11, 2): {"over": {"Outreach": 9}}}))
        run(page, mail=False)
        t = page.cells("jim")
        self.assertEqual(t[1][-3], "9*")
        self.assertEqual(cumulative(page)["Outreach"], 29)
        self.assertIn(v3.TYPED_NOTE, page.text("jim-notes"))

    def test_the_engine_writes_a_finished_week_once_and_finalizes_the_open_at_load_sheet_week(self):
        later = datetime(2026, 11, 20, 14, 0, tzinfo=T.CHI)                                                      # Friday of the week of Nov 16: Nov 9 has ended
        page = WeeklyPage(sheet_rows())
        run(page, now=later)
        self.assertIn(("finalized", f"w{date(2026, 11, 9)}"), page.created)                                      # the Nov 9 sheet week became the engine's final counts
        final = next(r for r in page.jim_rows if r["id"] == f"w{date(2026, 11, 9)}")
        self.assertEqual(final["properties"]["Source"]["select"]["name"], "Engine")
        before = len(page.created)
        run(page, now=later)
        self.assertEqual(len(page.created), before)                                                              # written once, never again

    def test_a_week_with_no_row_shows_the_engine_reading_and_is_written_when_it_ends(self):
        rows = [r for r in sheet_rows() if r["properties"]["Week Start"]["date"]["start"] != "2026-11-02"]
        page = WeeklyPage(rows)
        run(page, now=T.NOW)
        self.assertEqual(page.created, [date(2026, 11, 2)])
        self.assertEqual(page.jim_rows[-1]["properties"]["Source"]["select"]["name"], "Engine")
        self.assertEqual(page.cells("jim")[1][-3], "1")                                                          # the engine's own reading for that week, until frozen

    def test_an_unreadable_weekly_database_keeps_the_last_table_and_says_degraded(self):
        page = WeeklyPage(sheet_rows(), schema_ok=False)
        counts = run(page, mail=False)
        self.assertEqual(counts["weekly_code"], "MEGIBOW_WEEKLY_SCHEMA")
        self.assertEqual(page.cells("jim"), T.DASHES)
        self.assertTrue(page.text("jim-stamp").startswith("DEGRADED at"))

    def test_both_callouts_link_to_their_history_page(self):
        page = WeeklyPage(sheet_rows(), matt_rows=[T.row(T.WEEKS[1], 3)])
        run(page, mail=False)
        for who, ident in (("jim", "histjim"), ("matt", "histmatt")):
            links = [p["text"]["link"]["url"] for p in page.blocks[f"{who}-notes"]["paragraph"]["rich_text"] if p["text"].get("link")]
            self.assertIn(f"https://www.notion.so/{ident}", links)
        self.assertIn("Megibow history", page.text("jim-notes"))

    def test_without_history_pages_the_note_lines_have_no_link_and_nothing_fails(self):
        page = WeeklyPage(sheet_rows(), history=False)
        run(page, mail=False)
        self.assertNotIn("Megibow history", page.text("jim-notes"))


class Reader(unittest.TestCase):
    def test_duplicate_rows_for_a_week_use_the_newest_and_bad_rows_are_ignored(self):
        old, new = wrow(date(2026, 11, 2), (1, 0, 0, 0, 0), edited="2026-11-01T00:00:00Z"), wrow(date(2026, 11, 3), (7, 0, 0, 0, 0), edited="2026-11-05T00:00:00Z")
        bad = wrow(date(2026, 11, 9), (1, 0, 0, 0, 0))
        bad["properties"]["Outreach"] = {"number": -1}
        rows, ignored = weekly.read(WeeklyPage([old, new, bad]), "jimdb")
        self.assertEqual((rows[date(2026, 11, 2)]["stored"]["Outreach"], ignored), (7, 2))
        self.assertNotIn(date(2026, 11, 9), rows)

    def test_the_schema_must_match(self):
        with self.assertRaises(weekly.WeeklyError) as caught:
            weekly.read(WeeklyPage([], schema_ok=False), "jimdb")
        self.assertEqual(str(caught.exception), "MEGIBOW_WEEKLY_SCHEMA")

    def test_an_override_only_row_uses_the_engine_for_the_rest(self):
        rows = {date(2026, 11, 2): {"stored": None, "over": {"Outreach": 5, "Scheduled": None, "Networking Calls": None, "Recruiter Calls": None, "Company Calls": None}, "source": "Typed", "id": "x"}}
        engine = {a: 2 for a in C.ACTIVITIES}
        counts, typed = weekly.shown(date(2026, 11, 2), date(2026, 11, 9), rows[date(2026, 11, 2)], engine)
        self.assertEqual((counts["Outreach"], counts["Scheduled"], typed), (5, 2, frozenset({"Outreach"})))
        self.assertEqual(weekly.shown(date(2026, 11, 2), date(2026, 11, 9), None, None), (None, frozenset()))

    def test_a_future_dated_row_never_counts(self):
        proj = P.project([], date(2026, 11, 13))
        rows = {date(2026, 12, 7): {"stored": {a: 9 for a in C.ACTIVITIES}, "over": {a: None for a in C.ACTIVITIES}, "source": "Typed", "id": "f"}}
        self.assertEqual(weekly.project(proj, rows)["cumulative"], P.zero())


class Matt(unittest.TestCase):
    def test_matts_cumulative_counts_every_entered_week_even_those_that_left_the_table(self):
        old = T.WEEKS[0] - (T.WEEKS[1] - T.WEEKS[0]) * 3
        page = T.Page([T.row(old, 5, 1, 0, 2, 0), T.row(T.WEEKS[3], 1, 0, 0, 0, 0)])
        out = T.matt(page)
        self.assertEqual(out["status"], "OK")
        self.assertEqual(page.cells("matt")[1][-1], "6")                                                         # Outreach 5 (not in the table) + 1
        self.assertEqual(page.cells("matt")[6][-1], "9")


if __name__ == "__main__":
    unittest.main()
