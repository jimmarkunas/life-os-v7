"""a Close-screen refresh run (`refresh: recruiters`) must load the Runtime Configuration like every other run."""

import re
import unittest
from pathlib import Path
from tests import wftext  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class RefreshLoadsSettings(unittest.TestCase):
    def step(self, text):
        match = re.search(r"      - id: bws_runtime_config\n(.*?)(?=\n      - )", text, re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_the_recruiters_job_loads_its_settings_on_a_refresh_run_in_both_the_source_and_the_generated_workflow(self):
        source = "".join(
            p.read_text() for p in sorted((ROOT / ".github/workflows-src/domains").glob("130*-recruiters.yml"))
        )  # a long job continues in lettered parts
        generated = wftext.logical("domains")
        self.assertNotIn("inputs.refresh", self.step(source))
        job = generated[generated.index("\n  recruiters:\n") :]
        job = job[: job.index("\n  interview:\n")] if "\n  interview:\n" in job else job
        self.assertNotIn("inputs.refresh", self.step(job))

    def test_the_outreach_step_that_needs_the_settings_still_runs_on_a_refresh(self):
        source = "".join(
            p.read_text() for p in sorted((ROOT / ".github/workflows-src/domains").glob("130*-recruiters.yml"))
        )  # a long job continues in lettered parts
        step = source[source.index("      - id: routreach") :]
        self.assertNotIn("if:", step.split("run:")[0])


if __name__ == "__main__":
    unittest.main()
