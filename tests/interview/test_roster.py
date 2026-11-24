"""LIFE-931: the Interviews list on Hiring Pipeline: one row per machine round, read from the round page's own snapshot, kept current by the Interview job."""
import unittest
from lifeos.interview import notion, roster
from lifeos.interview.models import Child, Parent
from lifeos.platform.runtime import RunContext
from tests.interview.support import ENV

DB, SOURCE = "db1", "src1"
SCHEMA = {name: {"type": kind} for name, kind in roster.PROPERTIES.items()}


def text(label, kind="paragraph", id=None):
    return {"id": id or label[:12], "type": kind, kind: {"rich_text": [{"plain_text": label, "type": "text", "text": {"content": label}}]}}


class Fake:
    """Hiring Pipeline with an Interviews database, two round pages and a list of rows."""
    def __init__(self, rows=(), titled="Interviews", schema=None):
        fold = text(notion.ROUND_FOLD, "toggle", "fold")
        self.blocks = {"root": [{"id": DB, "type": "child_database", "child_database": {"title": titled}}],
                       "uber": [fold, {"id": "snap", "type": "callout", "callout": {"rich_text": []}}, {"id": "tr", "type": "child_page", "child_page": {"title": "Full Transcript"}}],
                       "snap": [text("Interviewer: Bethany Papenbrock, Hannah Hinz (from the old notes page) · Status: Held 2026-09-29 · Calendar not verified"),
                                text("When: 2026-09-29, 11:00–11:30 AM CT (from the old notes page) · Calendar event not verified")],
                       "tr": [text("Speaker A: Yeah.")],
                       "walmart": [fold, {"id": "snap2", "type": "callout", "callout": {"rich_text": []}}],
                       "snap2": [text("Interviewer: Not verified · Status: Scheduling Pending"), text("When: No verified Calendar event or meeting time")]}
        self.rows, self.schema, self.calls = list(rows), schema or SCHEMA, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith("/blocks/"):
            return {"results": self.blocks.get(path.split("/")[2].split("?")[0], []), "has_more": False}
        if path == f"/databases/{DB}":
            return {"data_sources": [{"id": SOURCE}]}
        if path == f"/data_sources/{SOURCE}":
            return {"properties": self.schema}
        if method == "PATCH":
            row = next(r for r in self.rows if r["id"] == path.split("/")[2])
            row["properties"].update(self.stored(body["properties"]))
            return {}
        raise AssertionError(path)

    @staticmethod
    def stored(props):
        out = {}
        for name, value in props.items():
            kind = roster.PROPERTIES.get(name, "rich_text")
            out[name] = {"type": kind, kind: value[kind] if kind in value else None}
            if kind in ("title", "rich_text"):
                out[name][kind] = [{"plain_text": "".join(p["text"]["content"] for p in value[kind])}]
        return out

    def call_once(self, method, path, body=None):
        self.calls.append((method, path))
        assert (method, path) == ("POST", "/pages") and body["parent"] == {"type": "data_source_id", "data_source_id": SOURCE}
        self.rows.append({"id": f"row{len(self.rows)}", "properties": self.stored(body["properties"])})
        return {"id": self.rows[-1]["id"]}

    def query_data_source(self, source_id=None, body=None):
        assert source_id == SOURCE
        return {"results": self.rows, "has_more": False, "next_cursor": None}


ROUNDS = [(Parent("p1", "Uber - Technical Project Manager"), Child("uber", "p1", "2026-09-29", None, 1, True)),
          (Parent("p2", "Walmart - Senior Manager, Operations and Implementation", False, "Interviewing"), Child("walmart", "p2", None, None, 1, True))]
ENVIRON = {**ENV, "HIRING_PIPELINE_PAGE_ID": "root"}


def run(client, rounds=ROUNDS, live=True):
    counts = {"why": {}}
    code = roster.sync(client, ENVIRON, rounds, live, RunContext.start(60), counts, stamp="Nov 23, 2026, 9:00 PM CT", today="2026-11-23")
    return code, counts


def cell(row, name):
    return roster._plain(row["properties"][name])


class Rows(unittest.TestCase):
    def test_a_list_with_no_rows_gets_one_row_per_round_read_from_the_page(self):
        client = Fake()
        code, counts = run(client)
        self.assertEqual((code, counts["roster_created"], len(client.rows)), ("roster_synced", 2, 2))
        uber, walmart = client.rows
        self.assertEqual([cell(uber, n) for n in ("Interview", "Company", "Role", "Round", "Date (CT)", "Interviewer", "Status", "Transcript", "Round page")],
                         ["Uber · Interview 1", "Uber", "Technical Project Manager", 1, "2026-09-29", "Bethany Papenbrock, Hannah Hinz (from the old notes page)", "Held", True, "https://www.notion.so/uber"])
        self.assertEqual([cell(walmart, n) for n in ("Status", "Transcript", "Interviewer", "Date (CT)")], ["Scheduling pending", False, "Not verified", None])
        self.assertTrue(all(m == "GET" or p.startswith("/pages") for m, p in client.calls))      # reads, and writes only to list rows

    def test_a_replay_writes_nothing(self):
        client = Fake()
        run(client)
        before = len(client.calls)
        code, counts = run(client)
        self.assertEqual((code, counts.get("roster_created", 0), counts.get("roster_updated", 0), counts.get("roster_planned", 0)), ("roster_synced", 0, 0, 0))
        self.assertNotIn(("POST", "/pages"), client.calls[before:])
        self.assertFalse(any(m == "PATCH" for m, _ in client.calls))

    def test_a_changed_page_updates_its_row_and_a_dry_run_only_counts(self):
        client = Fake()
        run(client)
        client.blocks["snap2"][0] = text("Interviewer: Not verified · Status: Cancelled 2026-12-01 · Calendar")
        code, counts = run(client, live=False)
        self.assertEqual((counts["roster_planned"], counts.get("roster_updated", 0)), (1, 0))
        self.assertEqual(cell(client.rows[1], "Status"), "Scheduling pending")
        code, counts = run(client)
        self.assertEqual((counts["roster_updated"], cell(client.rows[1], "Status")), (1, "Cancelled"))

    def test_a_row_whose_round_is_gone_is_retired_not_deleted_and_an_unreadable_page_never_retires(self):
        client = Fake()
        run(client)
        code, counts = run(client, rounds=ROUNDS[:1])
        self.assertEqual((counts["roster_retired"], len(client.rows), cell(client.rows[1], "Status")), (1, 2, "Retired"))
        client = Fake()
        run(client)
        client.blocks["walmart"] = []                                  # the page no longer reads as a round: skipped, so its row stays as it was
        code, counts = run(client)
        self.assertEqual((counts["roster_skipped"], counts.get("roster_retired", 0), cell(client.rows[1], "Status")), (1, 0, "Scheduling pending"))

    def test_no_list_or_the_wrong_shape_is_a_code_and_writes_nothing(self):
        for client, want in ((Fake(titled="Something else"), "roster_absent"), (Fake(schema={**SCHEMA, "Status": {"type": "rich_text"}}), "roster_unreadable")):
            code, counts = run(client)
            self.assertEqual((code, counts["why"][want], client.rows), (want, 1, []))

    def test_status_of_reads_the_snapshot_and_a_past_date_is_not_called_held(self):
        for raw, date, want in (("Scheduling Pending", None, "Scheduling pending"), ("Held 2026-09-29 · Calendar not verified", "2026-09-29", "Held"),
                                ("Cancelled 2026-10-01 · Calendar", "2026-10-01", "Cancelled"), ("Scheduled 2026-10-13 · Calendar invite accepted", "2026-10-13", "Date passed"),
                                ("Scheduled 2026-12-13 · Calendar invite accepted", "2026-12-13", "Scheduled")):
            self.assertEqual(roster.status_of(raw, date, "2026-11-23"), want)


if __name__ == "__main__":
    unittest.main()


class RootPages(unittest.TestCase):
    """A machine opportunity page directly under Hiring Pipeline (not in the Active or Retired container) still lists its rounds."""
    def test_a_machine_page_under_the_root_lists_its_round_and_a_human_page_does_not(self):
        from lifeos.interview import create, stage
        from lifeos.interview.models import RoundQuery
        from tests.interview.support import Fake, block, page
        client = Fake()
        client.blocks["root"] += [{"id": "u", "type": "child_page", "child_page": {"title": "Uber — Technical Project Manager"}},
                                  {"id": "h", "type": "child_page", "child_page": {"title": "Hand made"}}]
        client.pages.update({"u": page(parent="root"), "r": page(parent="u"), "h": page(parent="root")})
        client.blocks["u"] = [block(notion.MARKERS["opportunity"]), {"id": "r", "type": "child_page", "child_page": {"title": "Interview 1"}}]
        client.blocks["r"] = [block(notion.MARKERS["round"]), block(create.identity_text(RoundQuery(True, "2026-09-29", None, 1)))]
        client.blocks["h"] = [block("my own notes")]
        rounds = []
        self.assertTrue(stage._root_rounds(client, ENVIRON, RunContext.start(60), rounds))
        self.assertEqual([(p.title, c.page_id, c.interview_date) for p, c in rounds], [("Uber — Technical Project Manager", "r", "2026-09-29")])
