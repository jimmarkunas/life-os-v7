"""the early domains run of a tick, how the run after the tick finds it, and what each run is allowed to do."""
import datetime as dt
import pathlib
import tempfile
import unittest

from tests import wftext

try:
    import yaml
except ImportError:
    yaml = None

from lifeos.platform import gate, phase

ROOT = pathlib.Path(__file__).resolve().parents[2]
TICK = dt.datetime(2026, 11, 17, 3, 7, tzinfo=dt.timezone.utc)


def at(minutes):
    return (TICK + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def early(minutes, conclusion=None, event="workflow_dispatch", title=gate.DOMAINS_TITLE):
    return {"id": 1, "display_title": title, "event": event, "conclusion": conclusion, "created_at": at(minutes)}


class Covered(unittest.TestCase):
    def test_a_dispatch_run_created_after_the_tick_started_covers_it(self):
        self.assertTrue(phase.covered([early(1)], TICK))
        self.assertTrue(phase.covered([early(1, "success")], TICK))
        self.assertTrue(phase.covered([early(1, "failure")], TICK))

    def test_a_cancelled_run_does_not_cover_it_so_the_run_after_the_tick_does_the_work(self):
        self.assertFalse(phase.covered([early(1, "cancelled")], TICK))

    def test_an_older_run_a_manual_run_or_another_event_does_not_cover_it(self):
        self.assertFalse(phase.covered([early(-55)], TICK))  # the previous hour's early run
        self.assertFalse(phase.covered([early(1, title="domains (manual)")], TICK))
        self.assertFalse(phase.covered([early(1, event="workflow_run")], TICK))  # a run after a tick is not an early run
        self.assertFalse(phase.covered([], TICK))

    def test_a_small_clock_difference_at_the_start_of_the_tick_is_tolerated(self):
        self.assertTrue(phase.covered([early(-1)], TICK))


class Main(unittest.TestCase):
    def go(self, api, started=at(0)):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp, "out")
            code = phase.main({"TICK_STARTED": started, "GITHUB_OUTPUT": str(out)}, api=api)
            return code, out.read_text().strip()

    def test_it_writes_true_false_or_unknown_and_never_fails_the_job(self):
        self.assertEqual(self.go(lambda path: {"workflow_runs": [early(2)]}), (0, "early_covered=true"))
        self.assertEqual(self.go(lambda path: {"workflow_runs": []}), (0, "early_covered=false"))

    def test_an_unreadable_history_is_unknown_so_the_early_jobs_are_left_out_and_never_run_twice(self):
        def broken(path):
            raise OSError("GitHub unreachable")
        self.assertEqual(self.go(broken), (0, "early_covered=unknown"))
        self.assertEqual(self.go(lambda path: {"workflow_runs": []}, started="not a time"), (0, "early_covered=unknown"))

    def test_it_asks_only_for_dispatch_runs_of_the_domains_workflow(self):
        paths = []
        self.go(lambda path: paths.append(path) or {"workflow_runs": []})
        self.assertEqual(len(paths), 1)
        self.assertIn("actions/workflows/domains.yml/runs", paths[0])
        self.assertIn("event=workflow_dispatch", paths[0])


class TickEvent(unittest.TestCase):
    def test_the_run_after_a_tick_and_the_early_run_are_ticks_and_nothing_else_is(self):
        self.assertTrue(gate.is_tick_event({"GITHUB_EVENT_NAME": "workflow_run"}))
        self.assertTrue(gate.is_tick_event({"GITHUB_EVENT_NAME": "workflow_dispatch", "DOMAINS_PHASE": "tick"}))
        self.assertFalse(gate.is_tick_event({"GITHUB_EVENT_NAME": "workflow_dispatch", "DOMAINS_PHASE": "none"}))
        self.assertFalse(gate.is_tick_event({"GITHUB_EVENT_NAME": "workflow_dispatch"}))
        self.assertFalse(gate.is_tick_event({}))


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class Workflows(unittest.TestCase):
    """What the two workflow files must keep true for the early run (the proof that it adds no permission, no secret and
    no second run of a job).
    """
    hourly = domains = None

    @classmethod
    def setUpClass(cls):
        cls.hourly = yaml.safe_load((ROOT / ".github/workflows/hourly.yml").read_text())
        cls.domains = yaml.safe_load(wftext.text(ROOT / ".github/workflows/domains.yml"))

    def dispatch_step(self):
        steps = self.hourly["jobs"]["prep"]["steps"]
        found = [s for s in steps if str(s.get("name", "")).startswith("Start the early domains run")]
        self.assertEqual(len(found), 1)
        return steps, found[0]

    def test_the_prep_job_starts_the_early_run_once_right_after_the_gate_and_a_failure_there_costs_only_the_saving(self):
        steps, step = self.dispatch_step()
        gate_at = next(i for i, s in enumerate(steps) if str(s.get("name", "")).startswith("Gate - skip"))
        self.assertEqual(steps.index(step), gate_at + 1)
        self.assertIn("github.run_attempt == 1", step["if"])  # a retried prep job never dispatches twice
        self.assertIn("!cancelled()", step["if"])
        self.assertIn("github.event_name == 'schedule' || inputs.tick", step["if"])  # ticks only: a manual run is not a tick
        self.assertTrue(step["continue-on-error"])
        self.assertIn("actions/workflows/domains.yml/dispatches", step["run"])
        self.assertIn("inputs[phase]=tick", step["run"])
        self.assertEqual(set(step["env"]), {"GH_TOKEN"})  # the job's own token, no secret

    def test_the_dispatch_needs_no_permission_the_prep_job_did_not_already_have_and_no_other_job_gets_one(self):
        self.assertEqual(self.hourly["jobs"]["prep"]["permissions"], {"contents": "read", "actions": "write", "issues": "write"})
        self.assertEqual(self.hourly["permissions"], {"contents": "read"})
        self.assertEqual(self.domains["permissions"], {"contents": "read"})
        grants = {name: job["permissions"] for name, job in self.domains["jobs"].items() if "permissions" in job}
        self.assertEqual(grants, {"gate": {"contents": "read", "actions": "read"},
                                  "dcc": {"contents": "read", "actions": "read", "issues": "read"},  # exactly the two grants the file had before
                                  "weekly_review": {"contents": "read", "actions": "read"},
                                  "chain": {"contents": "read", "actions": "read"},
                                  **{j: {"contents": "read", "id-token": "write"} for j in ("followup", "outlook", "agenda", "hiring", "megibow", "interview")}})
        self.assertNotIn("secrets.", str(self.dispatch_step()[1]))

    def test_the_early_run_takes_exactly_the_jobs_that_read_nothing_the_tick_writes_and_the_late_run_the_rest(self):
        jobs = self.domains["jobs"]
        early = {n for n, j in jobs.items() if "needs.gate.outputs.early == 'true'" in str(j.get("if"))}
        late = {n for n, j in jobs.items() if "needs.gate.outputs.late == 'true'" in str(j.get("if"))}
        self.assertEqual(late, {"megibow", "interview", "granola", "network_surface", "fit_cloud_backlog", "last_seen_purge"})
        self.assertEqual(early & late, set())
        self.assertEqual(early | late | {"gate", "report", "chain"}, set(jobs))  # every job is in exactly one run
        for name in early | late:
            for need in jobs[name].get("needs") or []:
                if need != "gate":
                    self.assertTrue(need in early if name in early else need in late, f"{name} needs {need} across the two runs")

    def test_the_early_run_and_the_run_after_the_tick_never_share_a_group_and_a_do_nothing_run_has_its_own(self):
        group = self.domains["concurrency"]["group"]
        self.assertIn("inputs.phase == 'tick')) && 'life-os-v7-domains' ||", group)
        self.assertIn("|| 'life-os-v7-domains-late') }}", group)
        self.assertIn("'life-os-v7-domains-idle'", group)
        self.assertIn("'life-os-v7-domains-manual'", group)
        self.assertIs(self.domains["concurrency"]["cancel-in-progress"], False)

    def test_the_title_the_monitors_read_and_the_inputs_limit_hold(self):
        self.assertIn("inputs.phase == 'tick'", self.domains["run-name"])
        self.assertIn("'domains (after tick)'", self.domains["run-name"])
        inputs = self.domains[True]["workflow_dispatch"]["inputs"]
        self.assertLessEqual(len(inputs), 25)  # GitHub's limit on dispatch inputs
        self.assertEqual(inputs["phase"]["options"], ["none", "tick"])
        self.assertEqual(inputs["phase"]["default"], "none")

    def test_every_condition_that_means_a_tick_also_accepts_the_early_run(self):
        """A step or output that says `workflow_run` to mean 'this is a tick' must say `inputs.phase == 'tick'` too, or
        the early run would treat the tick as a.
        """
        exceptions = ("if: ${{ github.event_name == 'workflow_run' }}",
                      "late: ${{ inputs.all || github.event_name == 'workflow_run' }}",  # the late jobs belong to the run after the tick
                      "early: ${{ inputs.all || inputs.phase == 'tick' ||")  # already names the phase
        for number, line in enumerate(wftext.text(ROOT / ".github/workflows/domains.yml").splitlines(), 1):
            code = line.split("      #")[0] if "${{" in line else line
            if code.lstrip().startswith("#") or "github.event_name == 'workflow_run'" not in code or any(code.strip().startswith(e) for e in exceptions):
                continue
            if code.lstrip().startswith("group:"):
                continue
            self.assertIn("inputs.phase == 'tick'", code, f"domains.yml:{number} treats only the run after a tick as a tick")

    def test_every_automatic_live_decision_treats_the_early_run_as_a_tick(self):
        text = wftext.text(ROOT / ".github/workflows/domains.yml")
        self.assertEqual(text.count("github.event_name == 'workflow_run' && 'follow'"), 0)
        self.assertGreaterEqual(text.count("(github.event_name == 'workflow_run' || inputs.phase == 'tick') && 'follow'"), 17)  # one per job that loads plain switches


if __name__ == "__main__":
    unittest.main()
