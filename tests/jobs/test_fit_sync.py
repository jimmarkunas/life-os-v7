import unittest
from unittest import mock

from lifeos.jobs import fit_sync
from lifeos.platform.notion_client import NotionError
from tests.kit.db import FakeConn


class Client:
    def __init__(self, fail=False):
        self.patches, self.fail = [], fail

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        self.patches.append((method, path, body))
        return {}


ROW = (1, "page1", 81, "[81%] Go | Strengths: a", "REVIEW", "work mode unresolved", "unknown", "Scale-Up", "Scale-Up,Skilled Worker",
       "Scale-Up", "London, UK", None, "Scale-up:POSITIVE", "Salary range $90,000 - $120,000 per year.")


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
