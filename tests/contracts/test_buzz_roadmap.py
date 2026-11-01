"""LIFE-216 (Jim, Nov 1 2026): the approved Buzz roadmap baseline is frozen at exactly 25 ordered steps.
Pure file checks: no network, Jira or Notion. Live Jira/Notion read-back is NOT covered here."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROADMAP = (ROOT / "docs" / "buzz-implementation-roadmap-v1.md").read_text()
AGENTS = (ROOT / "AGENTS.md").read_text()

EXPECTED_IDS = [f"BUZZ-{n:02d}" for n in range(1, 26)]
ROW = re.compile(r"^\|\s*(BUZZ-\d+)\s*\|(.*)\|\s*$", re.M)

# Guard language an agent must keep; matched case-insensitively with markdown emphasis removed.
GUARDS = [
    "not blanket agent go",
    "do not invent a 26th step, reorder, split, merge, delete or redefine any step",
    "progress lives only in jira life-216",
    "roadmap_drift — no mutation",
    "never infer pass from a claim or simulation",
    "agent reviews do not substitute for jim's human uat",
    "no new scheduler, orchestrator, datastore",
]


def plain(text):
    return re.sub(r"[*`]", "", text).lower()


class BuzzRoadmapBaseline(unittest.TestCase):
    def rows(self):
        return [(m.group(1), [c.strip() for c in m.group(2).split("|")]) for m in ROW.finditer(ROADMAP)]

    def test_exactly_25_steps_in_order_without_duplicates(self):
        self.assertEqual([i for i, _ in self.rows()], EXPECTED_IDS)

    def test_every_step_has_phase_owner_action_and_evidence(self):
        for step, cells in self.rows():
            self.assertEqual(len(cells), 4, step)
            self.assertTrue(all(cells), step)

    def test_mandatory_guard_language_is_present(self):
        text = plain(ROADMAP)
        for guard in GUARDS:
            self.assertIn(guard, text)

    def test_roadmap_names_its_authorities(self):
        for ref in ("LOS-70", "LIFE-216", "development-policy"):
            self.assertIn(ref, ROADMAP)

    def test_agents_entrypoint_points_to_the_roadmap_without_granting_authority(self):
        self.assertIn("(docs/buzz-implementation-roadmap-v1.md)", AGENTS)
        self.assertIn("LIFE-216", AGENTS)
        self.assertIn("No Agent Go", AGENTS)


if __name__ == "__main__":
    unittest.main()
