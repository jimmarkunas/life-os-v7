"""LIFE-618: the job-source registry (lifeos/platform/source_registry.json) drives the stage table, the Jobs-tick plan and the source's own codes, and a stage under lifeos/sources/ that is neither registered nor on
the short "not yet migrated" list fails here, by name."""
import importlib
import sys
import types
import unittest

from lifeos import run
from lifeos.jobs import tick
from lifeos.platform import codes
from lifeos.platform import source_registry as registry

# Stages whose target sits under lifeos/sources/ and that are still wired by hand in lifeos/run.py (and, for the tick ones, lifeos/jobs/tick.py). Each migration PR moves one name from here into
# source_registry.json; a new source is never added here.
NOT_YET_MIGRATED = frozenset((
    "mail-filing", "manual-job",
    "megibow", "network-layout", "network-match", "network-surface", "newsletter-trace", "notion-sandbox-access", "notion-sandbox-outreach-proof", "oracle-card", "outlook-newsletters",
    "scale-up-boards", "scale-up-register"))


def unlisted(stages, sources=registry.SOURCES, legacy=NOT_YET_MIGRATED):
    """The stages whose target is under lifeos/sources/ and that are neither registered nor legacy, sorted."""
    registered = {s.name for s in sources}
    return sorted(name for name, stage in stages.items()
                  if getattr(stage, "target", ("",))[0].startswith("lifeos.sources") and name not in registered and name not in legacy)


class EveryEntryIsReal(unittest.TestCase):
    def test_names_are_unique_and_every_module_imports_and_has_its_function(self):
        names = [s.name for s in registry.SOURCES]
        self.assertEqual(len(names), len(set(names)))
        for s in registry.SOURCES:
            module = importlib.import_module(s.module)
            self.assertTrue(callable(getattr(module, s.function, None)), f"source {s.name}: {s.module}.{s.function} is not a function")

    def test_every_registered_source_is_a_stage_pointing_at_its_module(self):
        for s in registry.SOURCES:
            self.assertEqual(run.STAGES[s.name].target, (s.module, s.function), s.name)

    def test_every_tick_entry_is_in_the_plan_with_its_arguments_right_after_the_step_it_names(self):
        names = [step.name for step in tick.PLAN]
        for s in registry.SOURCES:
            if s.tick is None:
                continue
            self.assertEqual(tick.PLAN[names.index(s.name)], tick.Step(s.name, **s.tick), s.name)
            self.assertEqual(names.index(s.name), names.index(s.after) + 1, s.name)

    def test_a_registered_source_is_not_also_on_the_not_yet_migrated_list(self):
        self.assertEqual({s.name for s in registry.SOURCES} & NOT_YET_MIGRATED, set())

    def test_every_not_yet_migrated_name_is_still_a_stage_under_lifeos_sources(self):
        for name in NOT_YET_MIGRATED:
            self.assertTrue(run.STAGES[name].target[0].startswith("lifeos.sources"), name)


class FixedArguments(unittest.TestCase):
    def test_a_registered_source_passes_its_fixed_arguments_to_the_stage_function(self):
        self.assertEqual(next(s for s in registry.SOURCES if s.name == "web-scale-up").args, {"lane": "Scale-Up"})
        seen = {}
        module = types.ModuleType("lifeos.sources._args_source")
        module.run = lambda limit, live, **fixed: seen.update(limit=limit, live=live, **fixed) or {}
        sys.modules[module.__name__] = module
        try:
            stages = registry.stages(run.lazy, (registry.Source("args-source", module.__name__, args={"lane": "X"}),))
            stages["args-source"](3, False)
        finally:
            del sys.modules[module.__name__]
        self.assertEqual(seen, {"limit": 3, "live": False, "lane": "X"})


class MissingSourcesAreCaught(unittest.TestCase):
    def test_no_stage_under_lifeos_sources_is_unregistered_today(self):
        self.assertEqual(unlisted(run.STAGES), [])

    def test_a_source_wired_by_hand_without_a_registry_line_fails_and_is_named(self):
        stages = dict(run.STAGES)
        stages["brand-new-source"] = run.lazy("lifeos.sources.brand_new_source")
        self.assertEqual(unlisted(stages), ["brand-new-source"])

    def test_removing_a_registry_line_is_caught_by_name(self):
        self.assertEqual(unlisted(run.STAGES, sources=()), sorted(s.name for s in registry.SOURCES if s.module.startswith("lifeos.sources")))          # with the registry emptied, every registered source shows up


class ThePilotIsUnchanged(unittest.TestCase):
    def test_sponsors_has_the_same_stage_tick_step_and_place_as_before_the_registry(self):
        self.assertEqual(run.STAGES["sponsors"].target, ("lifeos.sources.discovery.sponsor_register", "CONTRACT"))          # LIFE-589: the pilot stage is declared through its contract
        self.assertEqual(next(s for s in tick.PLAN if s.name == "sponsors"), tick.Step("sponsors", 120, 40, bulk=True, group="B"))
        group_b = [s.name for s in tick.PLAN if s.group == "B"]
        self.assertEqual(group_b, ["boards", "web-scale-up", "web-titlewatch", "discover-jobs", "sponsors"])
        names = [s.name for s in tick.PLAN]
        self.assertEqual(names[names.index("sponsors") + 1], "boards-backfill")


class AFakeSourceNeedsOnlyItsModuleAndOneLine(unittest.TestCase):
    def setUp(self):
        module = types.ModuleType("lifeos.sources._fake_source")
        module.run = lambda limit, live: {"limit": limit, "live": live, "found": 3}
        sys.modules["lifeos.sources._fake_source"] = module
        self.addCleanup(sys.modules.pop, "lifeos.sources._fake_source", None)
        self.fake = registry.Source("fake-source", "lifeos.sources._fake_source", floor=5, tick={"budget": 90, "bulk": True, "group": "B"}, after="sponsors",
                                    codes={"FAKE_SOURCE_FAILED": ("degraded", False, "The fake source could not be read.", "Repeat the run.")})

    def test_the_stage_runs_with_the_floor_applied(self):
        stage = registry.stages(run.lazy, [self.fake])["fake-source"]
        self.assertEqual(stage(2, False), {"limit": 5, "live": False, "found": 3})

    def test_its_tick_step_lands_right_after_the_step_it_names(self):
        plan = registry.with_tick_steps(tick.PLAN, tick.Step, [self.fake])
        names = [s.name for s in plan]
        self.assertEqual(names[names.index("sponsors") + 1], "fake-source")
        self.assertEqual(plan[names.index("fake-source")], tick.Step("fake-source", 90, bulk=True, group="B"))

    def test_a_step_named_after_a_step_that_does_not_exist_raises(self):
        with self.assertRaises(ValueError):
            registry.with_tick_steps(tick.PLAN, tick.Step, [registry.Source("x", "m", tick={"budget": 1}, after="no-such-step")])

    def test_its_codes_become_registered_codes_with_the_sources_stage(self):
        rows = registry.code_rows(codes._r, [self.fake])
        self.assertEqual(rows["FAKE_SOURCE_FAILED"], codes._r("fake-source", "degraded", False, "The fake source could not be read.", "Repeat the run."))
        self.assertIn(rows["FAKE_SOURCE_FAILED"].klass, codes.CLASSES)


if __name__ == "__main__":
    unittest.main()
