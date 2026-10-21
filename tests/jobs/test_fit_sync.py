import unittest
from unittest import mock

from lifeos.jobs import fit_sync
from lifeos.platform.notion_client import NotionError
from tests.kit.db import FakeConn


class Client:
    source = "ledger"

    def __init__(self, fail=False, empty=(), roles=None):
        self.patches, self.fail, self.empty, self.roles, self.reads = [], fail, list(empty), dict(roles or {}), []

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        if method == "POST" and path.endswith("/query"):
            self.reads.append(body)
            return {"results": [{"id": p} for p in self.empty], "has_more": False}
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


def jobs(rows):
    """(notion_page_id, title) rows of v7_jobs, whatever the Fit says."""
    c = FakeConn()
    c.cur.fetchall = lambda: rows
    return c


class RoleBackfill(unittest.TestCase):
    """D247: a published job's Role is its own title whether or not it has a Fit score; nothing but the Role is written."""

    def run_backfill(self, client, rows, live=True):
        with mock.patch.object(fit_sync.store, "connect", side_effect=lambda: jobs(rows)):
            return fit_sync.backfill_roles(client, live)

    def test_an_unscored_published_job_gets_its_role_and_nothing_else(self):
        client = Client(empty=["aaaaaaaa-0000-0000-0000-000000000001"])
        counts = self.run_backfill(client, [("aaaaaaaa000000000000000000000001", "Growth Partnerships Manager")])
        self.assertEqual((counts["role_due"], counts["role_filled"], counts["role_failed"]), (1, 1, 0))
        method, path, body = client.patches[0]
        self.assertEqual((method, sorted(body["properties"])), ("PATCH", ["Role"]))                       # no Fit, Fit Authority, admission, lane or human field
        self.assertEqual(client.roles["aaaaaaaa-0000-0000-0000-000000000001"], "Growth Partnerships Manager")

    def test_a_scored_legacy_row_with_an_empty_role_is_filled_the_same_way(self):
        client = Client(empty=["page2"])
        counts = self.run_backfill(client, [("page2", "Credit Product Manager")])
        self.assertEqual((counts["role_filled"], client.roles["page2"]), (1, "Credit Product Manager"))

    def test_a_page_that_already_has_a_role_is_never_selected_or_written(self):
        client = Client(empty=[], roles={"page3": "Head of Product (Wealth & Trading)"})            # Notion's own filter returned nothing empty
        counts = self.run_backfill(client, [("page3", "A different title")])
        self.assertEqual((counts["role_due"], counts["role_filled"], client.patches), (0, 0, []))

    def test_an_empty_page_that_is_not_a_published_job_is_left_alone(self):
        client = Client(empty=["human-made"])
        counts = self.run_backfill(client, [("page4", "Some job")])
        self.assertEqual((counts["role_due"], client.patches), (0, []))

    def test_a_second_run_finds_nothing_once_the_role_reads_back(self):
        client = Client(empty=["page5"])
        self.run_backfill(client, [("page5", "Product Designer (Platform)")])
        client.empty = [p for p in client.empty if not client.roles.get(p)]                        # Notion no longer lists it as empty
        self.assertEqual(self.run_backfill(client, [("page5", "Product Designer (Platform)")])["role_filled"], 0)

    def test_a_write_that_does_not_read_back_is_a_failure_not_a_fill(self):
        client = Client(empty=["page6"])
        client.call_once = lambda method, path, body=None: {}                                      # the write is lost
        counts = self.run_backfill(client, [("page6", "Strategy & Operations Manager")])
        self.assertEqual((counts["role_filled"], counts["role_failed"]), (0, 1))

    def test_partial_failure_stops_after_three_and_keeps_the_pages_that_worked(self):
        pages = [f"p{i}" for i in range(6)]
        client = Client(empty=pages)
        real = client.call_once
        client.call_once = lambda method, path, body=None: {} if path.endswith(("p1", "p2", "p3")) else real(method, path, body)
        counts = self.run_backfill(client, [(p, f"Title {p}") for p in pages])
        self.assertEqual((counts["role_filled"], counts["role_failed"]), (1, 3))

    def test_the_batch_is_bounded(self):
        pages = [f"q{i:03d}" for i in range(40)]
        client = Client(empty=pages)
        counts = self.run_backfill(client, [(p, f"Title {p}") for p in pages])
        self.assertEqual((counts["role_due"], counts["role_filled"]), (40, fit_sync.ROLE_BATCH))

    def test_an_unreadable_ledger_or_a_malformed_answer_changes_nothing(self):
        failing = Client(fail=True)
        self.assertEqual(self.run_backfill(failing, [("x", "T")])["role_failed"], 1)
        odd = Client()
        odd.call = lambda method, path, body=None: {"results": [{}]}
        self.assertEqual((self.run_backfill(odd, [("x", "T")])["role_failed"], odd.patches), (1, []))

    def test_a_dry_run_reads_and_writes_nothing_to_pages(self):
        client = Client(empty=["page7"])
        counts = self.run_backfill(client, [("page7", "Job")], live=False)
        self.assertEqual((counts["role_due"], counts["role_filled"], client.patches), (1, 0, []))
