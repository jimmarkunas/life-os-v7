import unittest

from lifeos.jobs import guard, hiring_pipeline as hp
from lifeos.platform.notion_client import NotionError


class Page:
    def __init__(self, props=None, fail=False):
        self.props, self.fail = props or {}, fail

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
