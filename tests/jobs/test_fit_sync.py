import unittest
from unittest import mock

from lifeos.jobs import fit_sync
from lifeos.platform.notion_client import NotionError
from tests.kit.db import FakeConn


class Client:
    source = "ledger"

    def __init__(self, fail=False, empty=(), roles=None, endless=False):
        self.patches, self.fail, self.empty, self.roles, self.reads, self.endless = [], fail, list(empty), dict(roles or {}), [], endless

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        if method == "POST" and path.endswith("/query"):
            self.reads.append(body)
            pages = [{"id": p[0], "properties": {"Stable Job Key": {"rich_text": [{"plain_text": p[1]}]}}} if isinstance(p, tuple) else {"id": p} for p in self.empty]
            return {"results": pages, "has_more": self.endless, "next_cursor": "more" if self.endless else None}
        if method == "GET":
            return {"properties": {"Role": {"rich_text": [{"plain_text": self.roles.get(path.rsplit("/", 1)[1], "")}]}}}
        self.patches.append((method, path, body))
        if method == "PATCH" and "Role" in (body or {}).get("properties", {}) and len(body["properties"]) == 1:
            self.roles[path.rsplit("/", 1)[1]] = body["properties"]["Role"]["rich_text"][0]["text"]["content"]
        return {}

    call_once = call


ROW = (1, "page1", 81, "[81%] Go | Strengths: a", "REVIEW", "work mode unresolved", "unknown", "Scale-Up", "Scale-Up,Skilled Worker",
       "Scale-Up", "London, UK", None, "Scale-up:POSITIVE", "Salary range $90,000 - $120,000 per year.", "Product Owner (UX)")


def conn(rows):
    c = FakeConn()
    c.cur.fetchall = lambda: rows
    return c


class FitSync(unittest.TestCase):
    def test_properties_never_touch_human_state_or_title(self):
        props = fit_sync.properties(81, "line", "REVIEW", "why", "remote", "Scale-Up", "Scale-Up,Skilled Worker", True)
        for human in ("Applied", "Applied On", "Saturn Decision", "Job", "Apply URL"):
            self.assertNotIn(human, props)
        self.assertEqual((props["LIFE OS Fit"]["number"], props["Visible Lane"]["select"]["name"], props["Admission Status"]["select"]["name"]),
                         (81, "Scale-up", "Passed / Review"))
        self.assertEqual(props["Eligible Lanes"]["multi_select"], [{"name": "Scale-Up"}, {"name": "Skilled Worker"}])

    def test_shadow_mode_writes_the_fit_but_not_the_lane_fields(self):
        props = fit_sync.properties(81, "line", "ADMIT", None, "remote", "US Remote", "US Remote", False)
        self.assertEqual(sorted(props), ["Fit Authority", "LIFE OS Fit", "Why It Fits", "Work Mode"])

    def test_live_patches_each_due_page_and_marks_it(self):
        client = Client()
        with mock.patch.object(fit_sync.store, "connect", side_effect=lambda: conn([ROW])):
            counts = fit_sync.run(client, True, gated=True)
        self.assertEqual((counts["due"], counts["synced"], counts["failed"]), (1, 1, 0))
        self.assertEqual((client.patches[0][0], client.patches[0][1]), ("PATCH", "/pages/page1"))
        self.assertEqual(client.patches[0][2]["properties"]["Role"], {"rich_text": [{"type": "text", "text": {"content": "Product Owner (UX)"}}]})      # the Role column is the job title

    def test_dry_run_counts_and_writes_nothing(self):
        client = Client()
        with mock.patch.object(fit_sync.store, "connect", side_effect=lambda: conn([ROW])):
            counts = fit_sync.run(client, False)
        self.assertEqual((counts["due"], counts["synced"], client.patches), (1, 0, []))

    def test_notion_errors_stop_after_three(self):
        client = Client(fail=True)
        with mock.patch.object(fit_sync.store, "connect", side_effect=lambda: conn([ROW] * 5)):
            counts = fit_sync.run(client, True)
        self.assertEqual((counts["failed"], counts["synced"]), (3, 0))
        self.assertEqual(counts["failed_why"], "NOTION_HTTP_500")                                  # the failing pages' reason reaches the log as a fixed code


def jobs(rows):
    """(notion_page_id, dedupe_key, title) rows of v7_jobs, whatever the status or the Fit say."""
    c = FakeConn()
    c.cur.fetchall = lambda: rows
    return c


class RoleBackfill(unittest.TestCase):
    """D247: a Ledger page's Role is its job's own title whether or not the job has a Fit score, a stored page id or a PUBLISHED status; nothing but the Role is written."""

    def run_backfill(self, client, rows, live=True, conns=None):
        def connect():
            conn = jobs(rows)
            (conns if conns is not None else []).append(conn)
            return conn
        with mock.patch.object(fit_sync.store, "connect", side_effect=connect):
            return fit_sync.backfill_roles(client, live)

    def test_an_unscored_published_job_gets_its_role_and_nothing_else(self):
        client = Client(empty=[("aaaaaaaa-0000-0000-0000-000000000001", "k1")])
        counts = self.run_backfill(client, [("aaaaaaaa000000000000000000000001", "k1", "Growth Partnerships Manager")])
        self.assertEqual((counts["role_due"], counts["role_filled"], counts["role_failed"]), (1, 1, 0))
        method, path, body = client.patches[0]
        self.assertEqual((method, sorted(body["properties"])), ("PATCH", ["Role"]))                       # no Fit, Fit Authority, admission, lane or human field
        self.assertEqual(client.roles["aaaaaaaa-0000-0000-0000-000000000001"], "Growth Partnerships Manager")

    def test_a_scored_legacy_row_with_an_empty_role_is_filled_the_same_way(self):
        client = Client(empty=[("page2", "k2")])
        counts = self.run_backfill(client, [("page2", "k2", "Credit Product Manager")])
        self.assertEqual((counts["role_filled"], client.roles["page2"]), (1, "Credit Product Manager"))

    def test_a_row_whose_page_id_was_cleared_is_found_by_the_pages_stable_job_key(self):
        client = Client(empty=[("page3", "k3")])
        counts = self.run_backfill(client, [(None, "k3", "Head of Product (Wealth & Trading)")])           # the audit clears notion_page_id; the page and its key remain
        self.assertEqual((counts["role_due"], counts["role_filled"], client.roles["page3"]), (1, 1, "Head of Product (Wealth & Trading)"))

    def test_the_job_query_does_not_require_a_published_status_or_a_stored_page_id(self):
        sent = []
        conn = jobs([("page4", "k4", "Some job")])
        conn.cur.execute = lambda sql, args=(): sent.append(sql)
        with mock.patch.object(fit_sync.store, "connect", side_effect=lambda: conn):
            fit_sync.backfill_roles(Client(empty=[("page4", "k4")]), True)
        self.assertEqual(len(sent), 1)
        self.assertIn("dedupe_key IN", sent[0])
        self.assertIn("notion_page_id IN", sent[0])
        self.assertNotIn("status", sent[0].lower())                                       # a kept page whose job the audit moved on still gets its Role

    def test_a_page_that_already_has_a_role_is_never_selected_or_written(self):
        client = Client(empty=[], roles={"page5": "Head of Product (Wealth & Trading)"})                  # Notion's own filter returned nothing empty
        counts = self.run_backfill(client, [("page5", "k5", "A different title")])
        self.assertEqual((counts["role_empty"], counts["role_due"], counts["role_filled"], client.patches), (0, 0, 0, []))

    def test_an_empty_page_with_no_job_row_is_left_alone_and_counted(self):
        client = Client(empty=[("human-made", "k-unknown"), "no-key-page"])
        counts = self.run_backfill(client, [("page6", "k6", "Some job")])
        self.assertEqual((counts["role_empty"], counts["role_due"], counts["role_no_row"], counts["role_not_job"], client.patches), (2, 0, 1, 1, []))

    def test_a_job_row_without_a_title_is_not_guessed_and_is_counted(self):
        client = Client(empty=[("page7", "k7")])
        counts = self.run_backfill(client, [("page7", "k7", "  ")])
        self.assertEqual((counts["role_no_title"], counts["role_due"], client.patches), (1, 0, []))

    def test_a_second_run_finds_nothing_once_the_role_reads_back(self):
        client = Client(empty=[("page8", "k8")])
        self.run_backfill(client, [("page8", "k8", "Product Designer (Platform)")])
        client.empty = [p for p in client.empty if not client.roles.get(p[0])]                              # Notion no longer lists it as empty
        self.assertEqual(self.run_backfill(client, [("page8", "k8", "Product Designer (Platform)")])["role_filled"], 0)

    def test_a_write_that_does_not_read_back_is_a_failure_not_a_fill(self):
        client = Client(empty=[("page9", "k9")])
        client.call_once = lambda method, path, body=None: {}                                      # the write is lost
        counts = self.run_backfill(client, [("page9", "k9", "Strategy & Operations Manager")])
        self.assertEqual((counts["role_filled"], counts["role_failed"]), (0, 1))

    def test_partial_failure_stops_after_three_and_keeps_the_pages_that_worked(self):
        pages = [(f"p{i}", f"k{i}") for i in range(6)]
        client = Client(empty=pages)
        real = client.call_once
        client.call_once = lambda method, path, body=None: {} if path.endswith(("p1", "p2", "p3")) else real(method, path, body)
        counts = self.run_backfill(client, [(p, k, f"Title {p}") for p, k in pages])
        self.assertEqual((counts["role_filled"], counts["role_failed"]), (1, 3))

    def test_the_batch_is_bounded(self):
        pages = [(f"q{i:03d}", f"kq{i:03d}") for i in range(40)]
        client = Client(empty=pages)
        counts = self.run_backfill(client, [(p, k, f"Title {p}") for p, k in pages])
        self.assertEqual((counts["role_due"], counts["role_filled"]), (40, fit_sync.ROLE_BATCH))

    def test_a_read_cut_off_by_the_page_limit_is_flagged_never_a_clean_zero(self):
        client = Client(empty=[], endless=True)
        counts = self.run_backfill(client, [])
        self.assertEqual((counts.get("role_incomplete"), len(client.reads)), (1, fit_sync.ROLE_PAGES))
        self.assertEqual(counts["role_empty"], 0)

    def test_an_unreadable_ledger_or_a_malformed_answer_changes_nothing_and_is_counted(self):
        failing = Client(fail=True)
        self.assertEqual(self.run_backfill(failing, [("x", "kx", "T")])["role_failed"], 1)
        odd = Client()
        odd.call = lambda method, path, body=None: {"results": [{}]}
        self.assertEqual((self.run_backfill(odd, [("x", "kx", "T")])["role_failed"], odd.patches), (1, []))

    def test_a_dry_run_reads_and_writes_nothing_to_pages(self):
        client = Client(empty=[("page10", "k10")])
        counts = self.run_backfill(client, [("page10", "k10", "Job")], live=False)
        self.assertEqual((counts["role_due"], counts["role_filled"], client.patches), (1, 0, []))
