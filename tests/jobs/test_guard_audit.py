import unittest

from lifeos.jobs import guard, hiring_pipeline as hp
from lifeos.platform.notion_client import NotionError


BASE = {"Applied": {"checkbox": False}, "Applied On": {"date": None}, "Saturn Decision": {"select": None}}


class Page:
    def __init__(self, props=None, fail=False):
        self.props, self.fail = {**BASE, **(props or {})}, fail

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        return {"properties": self.props}


OPP = [hp.Opportunity("o1", "Acme — Program Manager", hp.ACTIVE, 0, "Acme", "Program Manager")]


class Guard(unittest.TestCase):
    def test_each_human_state_protects(self):
        self.assertEqual(guard.protection(Page({"Applied": {"checkbox": True}}), "p", "X", "Y"), "applied")
        self.assertEqual(guard.protection(Page({"Applied On": {"date": {"start": "2026-09-01"}}}), "p", "X", "Y"), "applied_on")
        self.assertEqual(guard.protection(Page({"Saturn Decision": {"select": {"name": "Pursue"}}}), "p", "X", "Y"), "saturn_decision")
        self.assertEqual(guard.protection(Page(), "p", "Acme Inc", "Programme Manager", OPP), "hiring_pipeline")

    def test_unreadable_fails_closed_and_nothing_to_protect_is_none(self):
        self.assertEqual(guard.protection(Page(fail=True), "p", "X", "Y"), "unreadable")
        self.assertIsNone(guard.protection(Page({"Applied": {"checkbox": False}, "Saturn Decision": {"select": None}}), "p", "X", "Y", OPP))


class AuditNeverDestroysAPursuit(unittest.TestCase):
    def test_audit_consults_the_guard_before_trashing(self):
        import inspect
        from lifeos.jobs import audit
        source = inspect.getsource(audit.run)
        self.assertLess(source.index("guard.protection"), source.index('"in_trash"'))     # no trash call can precede the check


class ProvenLinkTests(unittest.TestCase):
    def test_a_title_proven_job_is_not_demoted_for_an_ambiguous_link_shape(self):
        from lifeos.jobs import audit
        text = "Responsibilities: build pipelines and own data quality across the platform. You will need experience with SQL and Python skills. " * 6
        url = "https://careers-acme.icims.com/jobs/intro"
        self.assertEqual(audit.judge(url, text), "audit_url_no_job_id")
        self.assertIsNone(audit.judge(url, text, proven=True))
        self.assertEqual(audit.judge("https://careers-acme.icims.com/", text, proven=True), "audit_url_root")      # a root is never excused


class DuplicatePageTests(unittest.TestCase):
    def test_later_pages_for_the_same_opening_are_counted_and_the_earliest_stays(self):
        from unittest import mock
        from lifeos.jobs import audit

        pages = [(1, "PUBLISHED", "https://x.example/1", "p1", None, "ServiceNow", "Director, TA Infrastructure"),
                 (2, "PUBLISHED", "https://x.example/2", "p2", None, "ServiceNow", "Director, TA Infrastructure"),
                 (3, "PUBLISHED", "https://x.example/3", "p3", None, "ServiceNow", "Director, TA Infrastructure"),
                 (4, "PUBLISHED", "https://x.example/4", "p4", None, "Adobe", "Product Manager"),
                 (5, "PUBLISHED", "https://x.example/5", "p5", None, "Adobe", "Product Manager")]

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=()):
                self.rows = pages if "notion_page_id IS NOT NULL ORDER BY id" in sql and "status='PUBLISHED'" in sql and "NULL, company" in sql else []

            def fetchall(self):
                return self.rows

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cur()

        from lifeos.jobs import audit as a
        with mock.patch.object(a.store, "connect", return_value=Conn()), mock.patch.object(a.store, "ensure_schema"):
            counts = a.run(100, False, environ={})
        self.assertEqual(counts["by_reason"], {"duplicate_page": 2})                  # ids 2 and 3; two-word titles are never merged
        self.assertEqual(counts["failed"], 2)
