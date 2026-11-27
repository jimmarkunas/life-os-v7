"""the numbers docs/LIMITS.md and docs/US_REMOTE_WEB.md state are the numbers in lifeos/platform/limits.py and the source
lists, so a change to a.
"""
import json
import pathlib
import unittest

from lifeos.platform import limits

ROOT = pathlib.Path(__file__).resolve().parents[2]
LIMITS = (ROOT / "docs" / "LIMITS.md").read_text(encoding="utf-8")
US_REMOTE = (ROOT / "docs" / "US_REMOTE_WEB.md").read_text(encoding="utf-8")
WEB = ROOT / "lifeos" / "sources" / "web"


def n(value):
    return f"{value:,}"


class LimitsDocMatchesCode(unittest.TestCase):
    def test_the_limits_the_page_names_are_the_constants(self):
        for name in ("NOTION_PER_RUN", "AUDIT_PAGES_PER_RUN", "DICE_PER_RUN", "BOARDS_INGEST_PER_RUN", "BOARDS_PER_RUN", "BOARDS_CATCHUP_PER_RUN", "WEB_BOARDS_PER_RUN", "ATS_WORKERS", "BOARDS_WORKERS"):
            self.assertIn(f"`{name}`", LIMITS, f"{name} is not named in docs/LIMITS.md")
            self.assertIn(n(getattr(limits, name)), LIMITS, f"docs/LIMITS.md does not state {name} = {getattr(limits, name)}")

    def test_the_board_counts_are_the_seed_and_the_registries(self):
        seed = json.loads((WEB / "boards.json").read_text(encoding="utf-8"))["seed"]
        self.assertIn(n(sum(len(v) for v in seed.values())), LIMITS)
        for kind, count in {k: len(v) for k, v in seed.items()}.items():
            self.assertIn(f"{n(count)} {kind.capitalize()}", LIMITS)
        us = json.loads((WEB / "us_remote.json").read_text(encoding="utf-8"))["sources"]
        scale_up = json.loads((WEB / "scale_up.json").read_text(encoding="utf-8"))["sources"]
        ready = sum(1 for s in us if s["status"] == "ready")
        self.assertIn(f"The US Remote list holds {len(us)} sources, {ready} of them ready", LIMITS)
        self.assertIn(f"the Scale-Up list holds {len(scale_up)} (`scale_up.json`: {sum(1 for s in scale_up if s['status'] == 'ready')} ready, {sum(1 for s in scale_up if s['status'] == 'fallback')} fallback)", LIMITS)
        self.assertIn(f"So {ready} of {len(us)} sources can be read", US_REMOTE)

    def test_no_stale_figure_is_left(self):
        for stale in ("26 ready boards", "60 pages/run", "100 admissions per run", "40/run, stop on first 429"):
            self.assertNotIn(stale, LIMITS)

    def test_the_boards_cap_text_matches_the_workflow_timeouts(self):
        workflow = (ROOT / ".github" / "workflows" / "boards.yml").read_text(encoding="utf-8")
        self.assertIn(f"up to {n(limits.BOARDS_PER_RUN)} due boards", workflow)
        self.assertIn(f"up to {n(limits.BOARDS_CATCHUP_PER_RUN)} due boards", workflow)


if __name__ == "__main__":
    unittest.main()
