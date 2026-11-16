"""LIFE-703: the workflows read the V7 settings page for every movable name, and what still comes from GitHub is listed here with its phase-5 status.

A new `vars.<movable>` or `secrets.<movable>` reference fails the test until it is added below with a reason; a settings step can never stop a job; no
workflow gains a write permission from this change; the names that left GitHub keep exactly today's default when neither GitHub nor the page holds them."""
import pathlib
import re
import unittest

from lifeos.platform import settings

try:
    import yaml
except ImportError:          # PyYAML is a dev convenience, not a runtime dependency
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REFERENCE = re.compile(r"\b(vars|secrets)\.([A-Z][A-Z0-9_]*)")

# Every remaining reference to a movable name and what phase 5 (LIFE-704) does with it. "shadow" is the declared fallback: the step declares the GitHub value, the page fills it
# when GitHub holds none, and LIFE-704 deletes the GitHub value after 24 matching hours. "own condition" and "workflow env" are the two places a name cannot move yet.
STATUS = {
    "shadow": "the step declares the GitHub value; the page fills it when GitHub holds none; LIFE-704 deletes the GitHub value after 24 matching hours",
    "gate output": "read by the settings step; the condition uses the job output with the GitHub value as fallback; LIFE-704 deletes the GitHub value",
    "workflow env": "a workflow-level env expression cannot read a step output; moves with the next workflow PR",
    "conditional": "the declaration carries a condition the page cannot express; stays until LIFE-704 decides",
    "ticker": "the Oracle ticker reads this GitHub variable through the API (lifeos/edge/ticker/ticker.py); it stays until the ticker reads the page",
}
# One line per movable name still read from GitHub (the whole list; phase 5 deletes the GitHub value). Adding a reference needs a line here and a status above.
EXPECTED = {
}


def references():
    found = {}
    for path in sorted((ROOT / ".github").rglob("*.yml")):
        for match in REFERENCE.finditer(path.read_text()):
            name = match.group(2)
            if name != "GITHUB_TOKEN" and settings.movable(name):
                found.setdefault(name, set()).add(path.name)
    return found


class MovableNamesFromGitHub(unittest.TestCase):
    def test_every_remaining_movable_reference_has_a_phase_5_status(self):
        listed = references()
        self.assertEqual(sorted(set(listed) - set(EXPECTED)), [], "a new GitHub value for a movable name: read it from the settings page instead, or list it here with Jim's yes")
        self.assertEqual(sorted(set(EXPECTED) - set(listed)), [], "no workflow reads this name from GitHub any more: delete its line (and shrink FROZEN)")
        for name, status in EXPECTED.items():
            self.assertIn(status, STATUS, name)

    def test_the_names_that_never_existed_in_github_are_declared_empty_not_read_from_github(self):
        gone = ("INTERVIEW_CONVERT", "INTERVIEW_CONVERT_CAP", "JIRA_CARD_PROJECTS", "JIRA_GTV_CONTEXT_URL", "MEGIBOW_EXCLUDED_DOMAINS", "MEGIBOW_REVIEW_DB_ID", "NETWORK_SURFACE_SKIP",
                "PHYSICAL_MAIL_EXPECT", "TICK_CAP_ATS", "TICK_CAP_DICE", "TICK_CAP_JOBRIGHT", "TICK_CAP_LINKEDIN", "TICK_CAP_TINYFISH", "TICK_MAX_PARALLEL")
        self.assertEqual(sorted(set(gone) & set(references())), [])

    def test_a_safety_value_or_credential_is_never_read_from_the_settings_page(self):
        text = "\n".join(p.read_text() for p in WORKFLOWS.glob("*.yml"))
        for line in re.findall(r"lifeos\.platform\.settings[^\n]*", text):
            for name in settings.SAFETY:
                self.assertNotIn("--output " + name, line)


class DefaultsWhenNothingIsSet(unittest.TestCase):
    """The gate and the steps declare the name empty; with nothing on the page the code sees exactly what it saw when GitHub held nothing."""

    def test_nothing_on_the_page_changes_nothing(self):
        env = {n: "" for n in ("INTERVIEW_CONVERT", "NETWORK_SURFACE_SKIP", "TICK_CAP_LINKEDIN", "TICK_CAP_ATS", "TICK_MAX_PARALLEL", "PHYSICAL_MAIL_EXPECT")}
        out = settings.apply(env, _Page(["# nothing here"]))
        self.assertEqual((out["status"], out["filled"], set(env.values())), ("ok", [], {""}))

    def test_the_code_defaults_are_the_old_workflow_defaults(self):
        from lifeos.jobs import tick                                                       # noqa: PLC0415
        from lifeos.sources.interview import interview_sweep                               # noqa: PLC0415
        caps = tick.concurrency_from_env({})
        self.assertEqual((caps["caps"]["linkedin"], caps["caps"]["tinyfish"], caps["caps"]["jobright"], caps["caps"]["dice"], caps["caps"]["ats"], caps["parallel"]), (1, 1, 1, 1, 8, 4))
        self.assertEqual(tick.concurrency_from_env({n: "" for n in ("TICK_CAP_LINKEDIN", "TICK_CAP_ATS", "TICK_MAX_PARALLEL")}), caps)           # declared empty = unset
        self.assertEqual(interview_sweep.mode({"INTERVIEW_CONVERT": ""}), interview_sweep.mode({}))      # empty = off, as with an unset variable
        self.assertEqual(interview_sweep.mode({}), "")
        self.assertIn('(environ.get("NETWORK_SURFACE_SKIP") or "on")', (ROOT / "lifeos/sources/network/network_surface.py").read_text())      # empty means on, as the workflow's `|| 'on'` did

    def test_the_page_can_set_the_two_switches_the_gate_and_the_network_stage_read(self):
        env = {"INTERVIEW_CONVERT": "", "NETWORK_SURFACE_SKIP": ""}
        out = settings.apply(env, _Page(["INTERVIEW_CONVERT inventory", "NETWORK_SURFACE_SKIP off"]))
        self.assertEqual((env["INTERVIEW_CONVERT"], env["NETWORK_SURFACE_SKIP"], out["filled"]), ("inventory", "off", ["INTERVIEW_CONVERT", "NETWORK_SURFACE_SKIP"]))


@unittest.skipIf(yaml is None, "PyYAML not installed")
class SettingsStepsNeverStopAJob(unittest.TestCase):
    def steps(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            data = yaml.safe_load(path.read_text())
            for job_name, job in (data.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    yield path.name, job_name, step

    def test_every_settings_step_and_every_settings_token_step_is_continue_on_error(self):
        seen = 0
        for file, job, step in self.steps():
            ours = "lifeos.platform.settings" in (step.get("run") or "") or step.get("id") == "bws_settings"
            if ours:
                seen += 1
                self.assertIs(step.get("continue-on-error"), True, f"{file}:{job}:{step.get('name')}")
        self.assertGreaterEqual(seen, 20)

    def test_the_settings_token_step_loads_only_the_notion_token_and_nothing_is_exported_job_wide(self):
        for file, job, step in self.steps():
            if step.get("id") == "bws_settings":
                self.assertEqual(step["with"]["set_env"], False, file)
                lines = [x.strip() for x in step["with"]["secrets"].splitlines() if x.strip()]
                self.assertEqual(len(lines), 1, file)
                self.assertTrue(lines[0].endswith("> NOTION_JIRA_TOKEN"), file)

    def test_the_gate_outputs_read_the_page_only(self):
        """LIFE-699: GitHub no longer holds these switches, so the gate outputs are the settings step's output alone (no `|| vars.X` fallback)."""
        data = yaml.safe_load((WORKFLOWS / "domains.yml").read_text())
        out = data["jobs"]["gate"]["outputs"]
        self.assertEqual(out["granola_auto"].split("#")[0].strip(), "${{ steps.settings.outputs.GRANOLA_AUTO }}")
        self.assertEqual(out["imessage_alerts"].split("#")[0].strip(), "${{ steps.settings.outputs.V7_IMESSAGE_ALERTS }}")
        sync = yaml.safe_load((WORKFLOWS / "profile-sync.yml").read_text())["jobs"]["settings"]["outputs"]["fit_profile_sync"]
        self.assertEqual(sync.split("#")[0].strip(), "${{ steps.settings.outputs.FIT_PROFILE_SYNC }}")

    def test_no_job_gets_a_permissions_block_from_this_change(self):
        """The gate keeps its two read permissions and the new jobs and steps declare none; any `write` permission anywhere is already in the baseline below."""
        baseline = {"contents: write": {"branches.yml"}}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text()
            for permission, files in baseline.items():
                if permission in text:
                    self.assertIn(path.name, files, f"{path.name} gained `{permission}`")
        gate = yaml.safe_load((WORKFLOWS / "domains.yml").read_text())["jobs"]["gate"]
        self.assertEqual(gate["permissions"], {"contents": "read", "actions": "read"})
        self.assertNotIn("permissions", yaml.safe_load((WORKFLOWS / "profile-sync.yml").read_text())["jobs"]["settings"])


# The only names a `needs.<job>.outputs.<name>` may carry: due flags and trigger facts, settings switches, fixed words (failed stage names, codes) and run facts. Never a credential.
KNOWN_OUTPUTS = {"all", "codes", "due_agenda", "due_amazon", "due_amazon_retention", "due_bills", "due_dcc", "due_granola", "due_hiring", "due_hiring_outcomes", "due_imessage",
                 "due_interview", "due_jira", "due_mail", "due_megibow", "due_outlook", "due_recruiters", "extract", "failed", "fit_profile_sync", "granola_auto", "imessage_alerts",
                 "matrix", "pipeline", "sweep", "tick"}
SECRET_NAME = re.compile(r"(_TOKEN|_KEY|_SECRET|_PASSWORD|_PRIVATE_KEY)$")


def output_problems(docs, texts):
    """LIFE-703: a credential must never travel through a job output (anything a later job can read). -> what breaks the rule, by file and job, never a value."""
    problems = []
    for file, doc in docs.items():
        for job, body in (doc.get("jobs") or {}).items():
            loaders = {s.get("id") for s in body.get("steps") or [] if str(s.get("uses", "")).endswith("bws-load") and s.get("id")}
            for key, value in (body.get("outputs") or {}).items():
                text = str(value)
                if "secrets." in text:
                    problems.append(f"{file}:{job}: output {key} carries a secrets value")
                for step, name in re.findall(r"steps\.(\w+)\.outputs\.(\w+)", text):
                    if step in loaders or SECRET_NAME.search(name):
                        problems.append(f"{file}:{job}: output {key} carries a loaded credential")
    for file, text in texts.items():
        for name in sorted(set(re.findall(r"needs\.[\w-]+\.outputs\.(\w+)", text))):
            if name not in KNOWN_OUTPUTS:
                problems.append(f"{file}: needs.*.outputs.{name} is not a known switch name")
    return problems


@unittest.skipIf(yaml is None, "PyYAML not installed")
class NoCredentialTravelsThroughAJobOutput(unittest.TestCase):
    def test_no_job_output_carries_a_secret_and_every_needs_output_is_a_known_name(self):
        texts = {p.name: p.read_text() for p in sorted(WORKFLOWS.glob("*.yml"))}
        docs = {name: yaml.safe_load(text) for name, text in texts.items()}
        self.assertEqual(output_problems(docs, texts), [])

    def test_the_check_fails_on_a_deliberately_bad_fixture(self):
        bad = """
jobs:
  gate:
    outputs:
      granola_auto: ${{ steps.settings.outputs.GRANOLA_AUTO || vars.GRANOLA_AUTO }}
      leak_loader: ${{ steps.bws_settings.outputs.NOTION_JIRA_TOKEN }}
      leak_secret: ${{ secrets.JIRA_BASE_URL }}
      leak_named: ${{ steps.other.outputs.SOME_API_TOKEN }}
    steps:
      - id: bws_settings
        uses: ./.github/actions/bws-load
  later:
    needs: [gate]
    if: ${{ needs.gate.outputs.granola_auto == 'on' && needs.gate.outputs.mystery_switch == 'on' }}
"""
        problems = output_problems({"bad.yml": yaml.safe_load(bad)}, {"bad.yml": bad})
        self.assertEqual(len(problems), 4, problems)
        self.assertEqual(sorted(p.split(":")[-1].split(" carries")[0].strip() for p in problems if "carries" in p), ["output leak_loader", "output leak_named", "output leak_secret"])
        self.assertTrue(any("mystery_switch" in p for p in problems))
        self.assertFalse(any("granola_auto" in p for p in problems))                              # a settings step's switch output is allowed


class _Page:
    def __init__(self, lines):
        self.lines = lines

    def call(self, method, path, body=None):
        if path == "/search":
            return {"results": [{"id": "p", "properties": {"t": {"type": "title", "title": [{"plain_text": settings.TITLE}]}}}]}
        if path.endswith("/children") or "/blocks/" in path:
            return {"results": [{"id": "b1", "type": "code", "code": {"rich_text": [{"plain_text": "\n".join(self.lines)}], "language": "plain text"}}], "has_more": False}
        return {}
