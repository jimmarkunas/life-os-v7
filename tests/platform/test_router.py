import hashlib
import unittest

from lifeos.jira import card
from lifeos.platform import router
from lifeos.platform.router import DEGRADED, NO_ACTION, PASS, RouterError

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

    def test_modules_are_independent_and_one_degraded_module_blocks_none(self):
        ran = []

        def ok(name, outcome):
            return lambda: (ran.append(name), outcome)[1]

        def boom():
            ran.append("boom")
            raise RuntimeError("secret detail that must not travel")

        out = router.run_modules([("interview", ok("interview", PASS)), ("jira-view", boom), ("dcc", ok("dcc", NO_ACTION)),
                                  ("odd", lambda: "MAYBE")])
        self.assertEqual(ran, ["interview", "boom", "dcc"])
        self.assertEqual(out["modules"], {"interview": PASS, "jira-view": DEGRADED, "dcc": NO_ACTION, "odd": DEGRADED})
        self.assertEqual(out["overall"], DEGRADED)
        self.assertNotIn("secret", str(out))
        self.assertEqual(out["summary"], "interview=PASS | jira-view=DEGRADED | dcc=NO_ACTION | odd=DEGRADED")

    def test_overall_outcome_follows_the_modules(self):
        self.assertEqual(router.run_modules([("a", lambda: NO_ACTION), ("b", lambda: NO_ACTION)])["overall"], NO_ACTION)
        self.assertEqual(router.run_modules([("a", lambda: NO_ACTION), ("b", lambda: PASS)])["overall"], PASS)
        self.assertEqual(router.run_modules([])["overall"], NO_ACTION)
        with self.assertRaises(RouterError) as error:
            router.run_modules([("a", lambda: PASS), ("a", lambda: PASS)])
        self.assertEqual(str(error.exception), "ROUTER_DUPLICATE_MODULE")

    def test_the_dcc_module_resolves_only_its_own_single_region_and_never_creates_one(self):
        page = [("Calendar", "b1"), (router.JIRA_REGION, "b2"), (router.DCC_REGION + "  ", "b3")]
        self.assertEqual(router.resolve_target(page, DCC), "b3")
        for regions, code in (([("Calendar", "b1"), (router.JIRA_REGION, "b2")], "ROUTER_TARGET_MISSING"),
                              (page + [(router.DCC_REGION, "b4")], "ROUTER_TARGET_AMBIGUOUS")):
            with self.assertRaises(RouterError) as error:
                router.resolve_target(regions, DCC)
            self.assertEqual(str(error.exception), code)
        with self.assertRaises(RouterError):
            router.resolve_target(page, "some-other-module")

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
