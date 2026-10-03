import unittest

from lifeos.jobs.resolve import stage
from tests.kit.db import FakeConn

DUP = {"SELECT id FROM v7_jobs WHERE final_apply_url": (99,)}


class ApplyResultTests(unittest.TestCase):
    def test_landed_is_resolved(self):
        conn = FakeConn()
        ok = {"outcome": "landed", "url": "https://job-boards.greenhouse.io/x/jobs/1", "kind": "ats"}
        self.assertEqual(stage.apply_result(conn, 1, ok), "resolved")
        self.assertTrue(any("status='RESOLVED'" in a[0] or a[0] == "UPDATE v7_jobs" for a in conn.cur.sql))

    def test_same_final_link_is_duplicate(self):
        ok = {"outcome": "landed", "url": "https://careers.acme.example/j/1", "kind": "employer"}
        self.assertEqual(stage.apply_result(FakeConn(DUP), 1, ok), "duplicate")

    def test_failure_stays_pending_with_reason(self):
        conn = FakeConn()
        self.assertEqual(stage.apply_result(conn, 1, {"outcome": "apply_unavailable"}), "pending")
        self.assertEqual(conn.cur.sql[-1][1][0], "apply_unavailable")

    def test_landed_without_url_is_not_resolved(self):
        self.assertEqual(stage.apply_result(FakeConn(), 1, {"outcome": "landed", "url": None, "kind": "internal"}),
                         "pending")


class LinkProofTests(unittest.TestCase):
    """A landed link that cannot be one vacancy is never RESOLVED: Enrich would reject it and send the job round again."""

    def test_a_listing_or_root_link_is_pending_not_resolved(self):
        for url in ("https://careers.acme.example/", "https://careers.acme.example/jobs/search", "https://apply.workable.com/j/ABCDEF/"):
            out = stage.apply_result(FakeConn(), 1, {"outcome": "landed", "url": url, "kind": "employer"})
            self.assertEqual(out, "pending", url)

    def test_a_single_vacancy_link_still_resolves(self):
        out = stage.apply_result(FakeConn(), 1, {"outcome": "landed", "url": "https://job-boards.greenhouse.io/x/jobs/12345", "kind": "ats"})
        self.assertEqual(out, "resolved")


class WhyTests(unittest.TestCase):
    def test_reasons_are_fixed_codes_without_detail(self):
        self.assertEqual(stage.why({"outcome": "target_timeout:apply:dialog=False:pages=1"}), "target_timeout")
        self.assertEqual(stage.why({"outcome": "landed", "url": "https://careers.acme.example/", "kind": "employer"}), "bad_link_root")
        self.assertEqual(stage.why({"outcome": "landed", "url": "https://job-boards.greenhouse.io/x/jobs/12345", "kind": "ats"}), "landed:ats")
        self.assertEqual(stage.why({}), "unknown")

    def test_a_live_run_counts_why_each_job_was_not_resolved(self):
        from unittest import mock
        results = [{"outcome": "apply_unavailable"}, {"outcome": "apply_unavailable"},
                   {"outcome": "landed", "url": "https://careers.acme.example/", "kind": "employer"},
                   {"outcome": "landed", "url": "https://job-boards.greenhouse.io/x/jobs/12345", "kind": "ats"}]
        with mock.patch.object(stage.store, "connect", FakeConn), mock.patch.object(stage.store, "ensure_schema", lambda c: None), \
                mock.patch.object(stage, "pick", lambda c, s, l: [(i, "https://x.example/%d" % i) for i in range(4)]):
            counts = stage.run("jobright", 10, True, lambda urls: results)
        self.assertEqual(counts["why"], {"apply_unavailable": 2, "bad_link_root": 1, "landed:ats": 1})
        self.assertEqual(counts["resolved"], 1)


    def test_a_dry_run_applies_the_same_test_and_names_where_refusals_come_from(self):
        from unittest import mock
        results = [{"outcome": "landed", "url": "https://www.acme.example/careers?gh_jid=123&utm=x", "kind": "employer"},
                   {"outcome": "landed", "url": "https://www.acme.example/careers?gh_jid=456", "kind": "employer"},
                   {"outcome": "landed", "url": "https://job-boards.greenhouse.io/x/jobs/12345", "kind": "ats"}]
        with mock.patch.object(stage.store, "connect", FakeConn), mock.patch.object(stage.store, "ensure_schema", lambda c: None), \
                mock.patch.object(stage, "pick", lambda c, s, l: [(i, "https://x.example/%d" % i) for i in range(3)]):
            counts = stage.run("jobright", 10, False, lambda urls: results)
        self.assertEqual(counts["why"], {"bad_link_listing_url": 2, "landed:ats": 1})
        self.assertEqual(counts["refused"], {"hosts": {"acme.example": 2}, "query_keys": {"gh_jid": 2, "utm": 1}})   # names only, never values or paths


if __name__ == "__main__":
    unittest.main()
