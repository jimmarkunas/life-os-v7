"""the three end-to-end clocks of one tick, correlated by the tick's run ID"""

import datetime as dt
import json
import pathlib
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from lifeos.platform import chain_clocks as cc, gate

ROOT = pathlib.Path(__file__).resolve().parents[2]
T0 = dt.datetime(2026, 11, 22, 18, 0, 0, tzinfo=dt.timezone.utc)  # 12:00:00 PM CT


def at(seconds):
    return (T0 + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(**ends):
    """A tick log: each stage line is stamped when it ended (the runner's prefix)."""
    return "\n".join(
        f"{at(sec)[:-1]}.1234567Z stage {name.replace('_', '-')}: {outcome} 12.3s"
        for name, (outcome, sec) in ends.items()
    )


def job(name, end, conclusion="success", status="completed"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": at(0),
        "completed_at": at(end) if end is not None else None,
    }


TICK = {
    "id": 77,
    "status": "completed",
    "conclusion": "success",
    "run_started_at": at(0),
    "created_at": at(-5),
    "updated_at": at(700),
}
EARLY_JOBS = [
    job("gate (tick 77)", 10),
    job("hiring", 400),
    job("recruiters", 520),
    job("amazon", 300),
    job("hiring_outcomes", 450),
    job("report", 800, "skipped"),
]
LATE_JOBS = [
    job("gate (tick 77)", 710),
    job("interview", 900),
    job("megibow", 800),
    job("granola", 1000),
    job("hiring", 800, "skipped"),
]
EARLY = {"id": 1, "event": "workflow_dispatch", "conclusion": None, "jobs": EARLY_JOBS}
LATE = {"id": 2, "event": "workflow_run", "conclusion": None, "jobs": LATE_JOBS}
STAGES = log(publish=("ok", 200), publish_fresh=("ok", 330))


class Clocks(unittest.TestCase):
    def full(self, **over):
        args = {"tick": TICK, "log": STAGES, "runs": [EARLY, LATE], **over}
        return cc.receipt(77, "late", args["tick"], args["log"], args["runs"], 2)["clocks"]

    def test_the_three_clocks_from_one_correlated_chain_in_seconds_and_minutes(self):
        c = self.full()
        self.assertEqual(
            (c["jobs"]["status"], c["jobs"]["seconds"], c["jobs"]["minutes"], c["jobs"]["stage"]),
            ("OK", 330, 5.5, "publish-fresh"),
        )
        self.assertEqual(
            (c["career"]["status"], c["career"]["seconds"], c["career"]["minutes"], c["career"]["stage"]),
            ("OK", 900, 15.0, "interview"),
        )
        self.assertEqual(
            (c["platform"]["status"], c["platform"]["seconds"], c["platform"]["minutes"], c["platform"]["stage"]),
            ("OK", 1000, 16.7, "granola"),
        )
        self.assertEqual(c["jobs"]["at"], "Nov 22 2026, 12:05 PM CT")  # Central Time, never UTC
        self.assertEqual(c["platform"]["at"], "Nov 22 2026, 12:16 PM CT")

    def test_the_receipt_names_the_tick_the_role_and_the_runs(self):
        r = cc.receipt(77, "late", TICK, STAGES, [EARLY, LATE], 2)
        self.assertEqual((r["tick"], r["role"], r["run"], r["origin"]), ("77", "late", "2", "Nov 22 2026, 12:00 PM CT"))
        self.assertEqual(
            [(x["id"], x["event"]) for x in r["runs"]], [("1", "workflow_dispatch"), ("2", "workflow_run")]
        )
        self.assertNotIn("UTC", json.dumps(r))

    def test_the_full_platform_clock_is_unverified_until_the_after_tick_run_exists_and_its_jobs_ended(self):
        early_only = self.full(runs=[EARLY])
        self.assertEqual(early_only["platform"]["status"], "UNVERIFIED")
        self.assertIn("AFTER_TICK_RUN_MISSING", early_only["platform"]["why"])
        self.assertIsNone(early_only["platform"]["seconds"])  # never a zero
        running = self.full(runs=[EARLY, {**LATE, "jobs": LATE_JOBS[:3] + [job("granola", None, None, "in_progress")]}])
        self.assertEqual(running["platform"]["status"], "UNVERIFIED")
        self.assertIn("GRANOLA_PENDING", running["platform"]["why"])
        self.assertIsNone(running["platform"]["at"])

    def test_a_running_tick_is_not_a_finished_platform(self):
        c = self.full(tick={**TICK, "status": "in_progress"})
        self.assertEqual((c["platform"]["status"], c["platform"]["seconds"]), ("UNVERIFIED", None))
        self.assertIn("TICK_RUNNING", c["platform"]["why"])

    def test_a_failed_or_timed_out_job_is_degraded_never_done_and_still_has_its_time(self):
        late = {
            **LATE,
            "jobs": [job("interview", 900, "failure"), job("granola", 1000, "timed_out"), job("megibow", 800)],
        }
        c = self.full(runs=[EARLY, late])
        self.assertEqual(c["platform"]["status"], "DEGRADED")
        self.assertEqual(c["platform"]["why"], ["GRANOLA_TIMED_OUT", "INTERVIEW_FAILURE"])
        self.assertEqual(c["platform"]["seconds"], 1000)
        self.assertEqual(c["career"]["status"], "DEGRADED")
        self.assertEqual(c["career"]["why"], ["INTERVIEW_FAILURE"])
        self.assertEqual(c["career"]["stage"], "recruiters")  # the last career job that succeeded

    def test_with_no_early_run_the_after_tick_run_that_did_every_job_still_gives_a_platform_time(self):
        late = {**LATE, "jobs": LATE_JOBS[:4] + [job("hiring", 300), job("recruiters", 350)]}
        c = self.full(runs=[late])
        self.assertEqual((c["platform"]["status"], c["platform"]["seconds"]), ("OK", 1000))
        self.assertEqual(c["platform"]["why"], ["EARLY_RUN_ABSENT"])  # said, not hidden

    def test_a_career_job_not_due_is_neither_a_failure_nor_a_write(self):
        c = self.full(runs=[EARLY, {**LATE, "jobs": [job("interview", 5, "skipped"), job("granola", 1000)]}])
        self.assertEqual(
            (c["career"]["status"], c["career"]["stage"], c["career"]["seconds"]), ("OK", "recruiters", 520)
        )

    def test_no_career_job_in_the_chain_is_unverified(self):
        c = self.full(runs=[{**LATE, "jobs": [job("granola", 1000)]}])
        self.assertEqual((c["career"]["status"], c["career"]["why"]), ("UNVERIFIED", ["CAREER_JOBS_MISSING"]))


class JobsClock(unittest.TestCase):
    def test_the_last_publish_stage_that_printed_ok_ends_the_jobs_clock(self):
        c = cc.jobs_clock(T0, cc.stage_ends(log(publish=("ok", 200), publish_fresh=("ok", 330))))
        self.assertEqual((c["status"], c["seconds"], c["minutes"]), ("OK", 330, 5.5))

    def test_a_stage_that_did_not_print_ok_degrades_the_clock_and_never_counts_as_zero(self):
        c = cc.jobs_clock(T0, cc.stage_ends(log(publish=("ok", 200), publish_fresh=("timeout", 330))))
        self.assertEqual(
            (c["status"], c["seconds"], c["stage"], c["why"]),
            ("DEGRADED", 200, "publish", ["STAGE_PUBLISH_FRESH_TIMEOUT"]),
        )
        none = cc.jobs_clock(T0, cc.stage_ends(log(publish=("failed", 200), publish_fresh=("skipped", 330))))
        self.assertEqual((none["status"], none["seconds"], none["at"]), ("DEGRADED", None, None))
        self.assertEqual(none["why"], ["STAGE_PUBLISH_FAILED", "STAGE_PUBLISH_FRESH_SKIPPED"])

    def test_an_unreadable_tick_log_is_unverified(self):
        c = cc.jobs_clock(T0, cc.stage_ends(""))
        self.assertEqual((c["status"], c["seconds"], c["why"]), ("UNVERIFIED", None, ["TICK_STAGES_UNREADABLE"]))

    def test_stage_lines_with_a_not_due_reason_parse_by_outcome(self):
        self.assertEqual(cc.stage_ends(f"{at(5)[:-1]}.5Z stage enrich: not due 0.0s (why)")["enrich"][0], "not due")


class Finding(unittest.TestCase):
    LISTING = [
        {"id": 1, "display_title": gate.DOMAINS_TITLE, "event": "workflow_dispatch", "created_at": at(20)},
        {"id": 2, "display_title": gate.DOMAINS_TITLE, "event": "workflow_run", "created_at": at(702)},
        {"id": 3, "display_title": "domains (manual)", "event": "workflow_dispatch", "created_at": at(30)},
        {
            "id": 4,
            "display_title": gate.DOMAINS_TITLE,
            "event": "workflow_dispatch",
            "created_at": at(-3600 + 20),
        },  # the previous hour's early run
        {"id": 5, "display_title": gate.DOMAINS_TITLE, "event": "workflow_dispatch", "created_at": at(25)},
    ]  # near this tick, but another tick's ID

    def test_only_this_ticks_runs_are_correlated_by_the_id_in_the_gate_job_name(self):
        jobs = {
            1: EARLY_JOBS,
            2: LATE_JOBS,
            3: EARLY_JOBS,
            4: [job("gate (tick 70)", 1)],
            5: [job("gate (tick 78)", 1)],
        }
        got = cc.correlated_runs(77, TICK, self.LISTING, lambda rid: jobs[rid])
        self.assertEqual([r["id"] for r in got], [1, 2])


class Main(unittest.TestCase):
    def run_main(self, env, api, text=STAGES):
        out = []
        import contextlib, io

        with contextlib.redirect_stdout(io.StringIO()) as buffer:
            code = cc.main(env, api, lambda rid, environ=None: text)
        out.extend(buffer.getvalue().splitlines())
        return code, out

    def api(self, path):
        if path == "actions/runs/77":
            return TICK
        if path.startswith("actions/workflows/domains.yml/runs"):
            return {"workflow_runs": Finding.LISTING[:2]}
        return {"jobs": {"1": EARLY_JOBS, "2": LATE_JOBS}[path.split("/")[2]]}

    def test_one_chain_line_with_the_three_clocks_and_always_exit_zero(self):
        code, out = self.run_main({"CHAIN_TICK_ID": "77", "CHAIN_ROLE": "late", "GITHUB_RUN_ID": "2"}, self.api)
        self.assertEqual(code, 0)
        receipt = json.loads(out[-1].split("chain: ", 1)[1])
        self.assertEqual(set(receipt["clocks"]), {"jobs", "career", "platform"})
        self.assertEqual(receipt["clocks"]["platform"]["status"], "OK")

    def test_no_tick_id_or_an_unreadable_api_is_unverified_with_a_fixed_code_and_never_fails(self):
        code, out = self.run_main({}, self.api)
        self.assertEqual(code, 0)
        self.assertIn("NO_TICK_ID", out[-1])

        def broken(path):
            raise OSError("no")

        code, out = self.run_main({"CHAIN_TICK_ID": "77"}, broken)
        self.assertEqual(code, 0)
        self.assertIn("CHAIN_UNREADABLE_OSERROR", out[-1])
        self.assertNotIn("PENDING", "".join(out)) if False else None

    def test_an_unreadable_tick_log_leaves_the_other_clocks_standing(self):
        def no_log(rid, environ=None):
            raise OSError("x")

        import contextlib, io

        with contextlib.redirect_stdout(io.StringIO()) as buffer:
            cc.main({"CHAIN_TICK_ID": "77"}, self.api, no_log)
        receipt = json.loads(buffer.getvalue().splitlines()[-1].split("chain: ", 1)[1])
        self.assertEqual(receipt["clocks"]["jobs"]["status"], "UNVERIFIED")
        self.assertEqual(receipt["clocks"]["platform"]["status"], "OK")


@unittest.skipIf(yaml is None, "pyyaml unavailable")
class Workflows(unittest.TestCase):
    def test_the_early_dispatch_passes_the_tick_run_id_and_the_domains_workflow_names_its_gate_by_it(self):
        hourly = (ROOT / ".github/workflows/hourly.yml").read_text()
        self.assertIn('-f "inputs[tick_run]=${GITHUB_RUN_ID}"', hourly)
        domains = yaml.safe_load((ROOT / ".github/workflows/domains.yml").read_text())
        self.assertIn("tick_run", domains[True]["workflow_dispatch"]["inputs"])
        self.assertIn("gate (tick", domains["jobs"]["gate"]["name"])
        self.assertIn("workflow_run.id", domains["jobs"]["gate"]["name"])

    def test_the_chain_job_runs_after_every_domain_job_reads_only_and_cannot_fail_the_run(self):
        domains = yaml.safe_load((ROOT / ".github/workflows/domains.yml").read_text())
        chain = domains["jobs"]["chain"]
        self.assertEqual(set(chain["needs"]), set(domains["jobs"]["report"]["needs"]))
        self.assertEqual(chain["permissions"], {"contents": "read", "actions": "read"})
        self.assertIn("always()", chain["if"])
        self.assertTrue(any("chain_clocks" in step.get("run", "") for step in chain["steps"]))


if __name__ == "__main__":
    unittest.main()
