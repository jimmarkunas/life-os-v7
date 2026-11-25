"""LIFE-971 (Jim, Nov 24 2026): a Close-screen refresh run (`refresh: recruiters`) must load the Runtime Configuration like every other run.
The Nov 24 refresh failed with RECRUITER_OUTREACH_NOT_CONFIGURED because its settings step was skipped for that input, so HIRING_TABLE_BLOCK_ID was empty."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RefreshLoadsSettings(unittest.TestCase):
    def step(self, text):
        match = re.search(r"      - id: bws_runtime_config\n(.*?)(?=\n      - )", text, re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_the_recruiters_job_loads_its_settings_on_a_refresh_run_in_both_the_source_and_the_generated_workflow(self):
        source = (ROOT / ".github/workflows-src/domains/130-recruiters.yml").read_text()
        generated = (ROOT / ".github/workflows/domains.yml").read_text()
        self.assertNotIn("inputs.refresh", self.step(source))
        job = generated[generated.index("\n  recruiters:\n"):]
        job = job[:job.index("\n  interview:\n")] if "\n  interview:\n" in job else job
        self.assertNotIn("inputs.refresh", self.step(job))

    def test_the_outreach_step_that_needs_the_settings_still_runs_on_a_refresh(self):
        source = (ROOT / ".github/workflows-src/domains/130-recruiters.yml").read_text()
        step = source[source.index("      - id: routreach"):]
        self.assertNotIn("if:", step.split("run:")[0])


if __name__ == "__main__":
    unittest.main()
