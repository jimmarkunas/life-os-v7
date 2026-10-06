"""EDGE-1.3 Phase A: the one-time GitHub-secrets -> Bitwarden migration workflow is manual-only, confirmation-gated, scoped to exactly 25 sources,
fail-closed before any write, and never prints a value. Delete with the workflow once Phase B has landed."""
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    import yaml
except ImportError:          # PyYAML is a dev convenience, not a runtime dependency
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = ROOT / ".github" / "workflows" / "bws-migrate.yml"
APPROVED = {
    "FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN",
    "JIRA_API_TOKEN", "JIRA_EMAIL", "JOBRIGHT_EMAIL", "JOBRIGHT_PASSWORD", "LIFEOS_ACQ_DB_NAME", "LIFEOS_ACQ_DB_PASSWORD", "LIFEOS_ACQ_DB_USER",
    "LIFEOS_ACQ_SSH_HOST", "LIFEOS_ACQ_SSH_KNOWN_HOSTS", "LIFEOS_ACQ_SSH_PRIVATE_KEY", "LIFEOS_ACQ_SSH_USER", "NOTION_AMAZON_TOKEN",
    "NOTION_API_TOKEN", "NOTION_BILLS_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN", "NOTION_RECRUITERS_TOKEN", "NTFY_TOPIC",
    "OPEN_JOBS_CONTACT", "TINYFISH_API_KEY",
}
EXCLUDED = {"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "LIFEOS_EDGE_HOST", "LIFEOS_EDGE_SSH_PRIVATE_KEY", "LIFEOS_EDGE_USER"}
PROJECT = "3244a340-8b60-45f0-9f33-b4db0082127e"
PHRASE = "MIGRATE 25 SECRETS TO BITWARDEN"

STUB = r'''#!/usr/bin/env bash
# Test double for bws: records state in $STUB_DIR, answers like the real CLI (JSON that includes the value), fails on request.
set -u
d="$STUB_DIR"
case "$1 $2" in
  "project list") printf '%s\n' "$STUB_PROJECTS" ;;
  "secret list")
    [ "$3" = "$STUB_PROJECT" ] || exit 1
    jq -n --arg keys "$STUB_EXISTING" '[ ($keys | split(",") | .[] | select(. != "")) | {key: ., id: "00000000-0000-4000-8000-000000000000", value: "STUB_LEAK"} ]' ;;
  "secret create")
    shift 2; [ "$1" = "-o" ] && shift 2
    [ "$1" = "--" ] || { echo "stub: missing -- before positional arguments" >&2; exit 2; }
    key="$2"; value="$3"; project="$4"
    [ "$project" = "$STUB_PROJECT" ] || exit 1
    [ "$key" != "$STUB_FAIL_ON" ] || { echo "stub failure carrying $value" >&2; exit 1; }
    n=$(( $(cat "$d/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$d/count"; echo "$key" >> "$d/created"
    printf '%s\n' "{\"id\":\"$(printf '00000000-0000-4000-8000-%012d' "$n")\",\"key\":\"$key\",\"value\":\"$value\"}" ;;
  *) exit 2 ;;
esac
'''


@unittest.skipIf(yaml is None, "PyYAML not installed")
class MigrationWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PATH.read_text()
        cls.doc = yaml.safe_load(cls.text)
        cls.job = cls.doc["jobs"]["migrate"]
        cls.install, cls.migrate = cls.job["steps"]

    def test_manual_only_with_one_required_confirmation_input_and_no_schedule(self):
        on = self.doc[True]
        self.assertEqual(list(on), ["workflow_dispatch"])
        self.assertEqual(list(on["workflow_dispatch"]["inputs"]), ["confirm"])
        self.assertIs(on["workflow_dispatch"]["inputs"]["confirm"]["required"], True)
        self.assertNotRegex(self.text, r"(?m)^\s*(schedule|push|pull_request\w*|workflow_run|repository_dispatch):")

    def test_job_runs_only_on_the_exact_confirmation_on_main(self):
        self.assertIn(f"inputs.confirm == '{PHRASE}'", self.job["if"])
        self.assertIn("github.ref == 'refs/heads/main'", self.job["if"])

    def test_minimal_permissions_and_no_checkout_cache_artifact_or_repo_code(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        for banned in ("actions/checkout", "actions/cache", "upload-artifact", "lifeos.run", "python", "toJSON", "set -x", "ACTIONS_STEP_DEBUG", "printenv", "export -p", "env |", "tinyfish."):
            self.assertNotIn(banned, self.text, banned)
        self.assertEqual([s["uses"] for s in self.job["steps"] if "uses" in s], [])         # no third-party action to pin: the CLI is a checksummed release download

    def test_only_the_25_approved_sources_and_the_migration_token_are_referenced(self):
        referenced = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", self.text))
        self.assertEqual(referenced, APPROVED | {"LIFEOS_BWS_MIGRATION_TOKEN"})
        self.assertEqual(len(APPROVED), 25)
        self.assertTrue(referenced.isdisjoint(EXCLUDED | {"LIFEOS_BWS_RUNTIME_TOKEN"}))
        self.assertEqual({k for k, v in self.migrate["env"].items() if k != "BWS_ACCESS_TOKEN"}, APPROVED)
        self.assertEqual(self.migrate["env"]["BWS_ACCESS_TOKEN"], "${{ secrets.LIFEOS_BWS_MIGRATION_TOKEN }}")

    def test_secrets_are_declared_only_on_the_migration_step_and_the_payload_list_matches(self):
        self.assertNotIn("secrets.", str(self.doc.get("env", "")) + str(self.job.get("env", "")) + str(self.install))
        listed = set(re.search(r"NAMES=\((.*?)\)", self.migrate["run"], re.S).group(1).split())
        self.assertEqual(listed, APPROVED)

    def test_destination_is_the_one_project_with_the_exact_key_scheme_and_the_cli_is_pinned(self):
        self.assertIn(f'PROJECT="{PROJECT}"', self.migrate["run"])
        self.assertIn('PREFIX="lifeos/github-actions/"', self.migrate["run"])
        self.assertIn("bws-v2.1.0", self.install["run"])
        self.assertRegex(self.install["run"], r"[0-9a-f]{64}  \$RUNNER_TEMP/bws\.zip")
        self.assertIn("sha256sum -c", self.install["run"])

    def test_every_preflight_check_precedes_the_first_write(self):
        run = self.migrate["run"]
        self.assertEqual(run.count("bws secret create"), 1)
        write = run.index("bws secret create")
        for marker in ("PREFLIGHT 1/4", "PREFLIGHT 2/4", "PREFLIGHT 3/4", "PREFLIGHT 4/4", "bws project list", "bws secret list", "CONFLICT"):
            self.assertLess(run.index(marker), write, marker)
        self.assertNotRegex(run, r"secret (update|delete|edit)")


@unittest.skipIf(yaml is None or shutil.which("jq") is None or shutil.which("bash") is None, "needs PyYAML, bash and jq")
class MigrationScriptBehaviour(unittest.TestCase):
    """Runs the workflow's real script against a stub bws. Source values are sentinels; none may ever reach the output."""

    @classmethod
    def setUpClass(cls):
        cls.script = yaml.safe_load(PATH.read_text())["jobs"]["migrate"]["steps"][1]["run"]

    def run_script(self, **override):
        with tempfile.TemporaryDirectory() as tmp:
            stub = pathlib.Path(tmp) / "bws"
            stub.write_text(STUB)
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            env = {"PATH": f"{tmp}:{os.environ['PATH']}", "STUB_DIR": tmp, "STUB_PROJECT": PROJECT, "STUB_PROJECTS": f'[{{"id":"{PROJECT}","name":"x"}}]',
                   "STUB_EXISTING": "", "STUB_FAIL_ON": "", "BWS_ACCESS_TOKEN": "TOKEN_SENTINEL"}
            env.update({name: f"-----VALUE_SENTINEL_{name}" for name in APPROVED})       # leading dashes: the SSH-key shape
            env.update(override)
            env = {k: v for k, v in env.items() if v is not None}
            done = subprocess.run(["bash", "-c", self.script], env=env, capture_output=True, text=True, cwd=tmp)
            created = (pathlib.Path(tmp) / "created").read_text().split() if (pathlib.Path(tmp) / "created").exists() else []
            return done, created

    def assertNoLeak(self, done):
        for needle in ("SENTINEL", "STUB_LEAK"):
            self.assertNotIn(needle, done.stdout + done.stderr)

    def test_happy_path_creates_exactly_25_and_prints_only_names_and_uuids(self):
        done, created = self.run_script()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(created), sorted(f"lifeos/github-actions/{n}" for n in APPROVED))
        self.assertEqual(len(re.findall(r"^[A-Z0-9_]+ -> [0-9a-f-]{36} -> CREATED$", done.stdout, re.M)), 25)
        self.assertNoLeak(done)

    def test_a_conflicting_destination_key_stops_before_any_write_and_names_only_that_key(self):
        done, created = self.run_script(STUB_EXISTING="other/key,lifeos/github-actions/NTFY_TOPIC")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(created, [])
        self.assertEqual(re.findall(r"^CONFLICT (.*)$", done.stdout, re.M), ["lifeos/github-actions/NTFY_TOPIC"])
        self.assertNoLeak(done)

    def test_an_empty_source_value_stops_before_any_write(self):
        done, created = self.run_script(JIRA_EMAIL="")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(created, [])
        self.assertIn("MISSING JIRA_EMAIL", done.stdout)
        self.assertNoLeak(done)

    def test_a_machine_account_that_sees_more_or_other_projects_stops_before_any_write(self):
        for projects in ('[]', f'[{{"id":"{PROJECT}"}},{{"id":"11111111-1111-4111-8111-111111111111"}}]', '[{"id":"11111111-1111-4111-8111-111111111111"}]'):
            done, created = self.run_script(STUB_PROJECTS=projects)
            self.assertNotEqual(done.returncode, 0, projects)
            self.assertEqual(created, [], projects)

    def test_a_failed_write_stops_reports_the_partial_state_and_deletes_nothing(self):
        done, created = self.run_script(STUB_FAIL_ON="lifeos/github-actions/JIRA_EMAIL")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(created, [f"lifeos/github-actions/{n}" for n in ("FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "GCAL_SERVICE_ACCOUNT_JSON",
                                                                          "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "JIRA_API_TOKEN")])
        self.assertIn("PARTIAL CANDIDATE: BLOCKED ON JIRA_EMAIL (6 of 25 created", done.stdout)
        self.assertNoLeak(done)

    def test_runner_debug_logging_refuses_to_run(self):
        done, created = self.run_script(RUNNER_DEBUG="1")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(created, [])


if __name__ == "__main__":
    unittest.main()
