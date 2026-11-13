import hashlib
import unittest

from lifeos.jira import card
from lifeos.platform import router
from lifeos.platform.router import RouterError

DCC = "daily-command-center"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class RouterTests(unittest.TestCase):
    def test_the_protected_jira_region_is_exactly_the_heading_v7_owns(self):
        self.assertEqual(router.JIRA_REGION, card.CARD_TITLE)                # the two cannot drift apart
        self.assertEqual(router.OWNERS[router.JIRA_REGION], "v7-jira")

    def test_calendar_region_has_one_v7_owner(self):
        self.assertEqual(router.CALENDAR_REGION, "Calendar")
        self.assertEqual(router.OWNERS[router.CALENDAR_REGION], "v7-calendar")

    def test_a_write_touching_the_jira_region_or_any_unmarked_region_fails_closed(self):
        router.check_write(DCC, [router.DCC_REGION])
        for touched in ([router.JIRA_REGION], [router.DCC_REGION, router.JIRA_REGION], ["Hiring Pipeline"], [""]):
            with self.assertRaises(RouterError) as error:
                router.check_write(DCC, touched)
            self.assertEqual(str(error.exception), "ROUTER_REGION_NOT_OWNED")

    def test_protected_regions_must_come_out_exactly_as_they_went_in(self):
        before = {router.JIRA_REGION: digest("card"), router.DCC_REGION: digest("old")}
        self.assertTrue(router.protected_intact(DCC, before, {router.JIRA_REGION: digest("card"), router.DCC_REGION: digest("new")}))
        self.assertFalse(router.protected_intact(DCC, before, {router.JIRA_REGION: digest("rewritten"), router.DCC_REGION: digest("new")}))
        self.assertFalse(router.protected_intact(DCC, before, {router.DCC_REGION: digest("new")}))               # deleted
        self.assertFalse(router.protected_intact(DCC, {router.DCC_REGION: digest("old")}, dict(before)))         # recreated from nothing

    def test_the_router_imports_nothing_so_it_can_hold_no_scheduler_queue_datastore_or_framework(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(router))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], [])
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)][0].name, "RouterError")      # its only class


if __name__ == "__main__":
    unittest.main()
