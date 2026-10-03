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
