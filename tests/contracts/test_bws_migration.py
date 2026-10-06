"""EDGE-1.3: the one-time GitHub-secrets -> Bitwarden migration workflow is manual-only, confirmation-gated, scoped to exactly 25 sources, resumable from a
partial state (existing destinations are compared in memory, never overwritten), paced, fail-closed before any write, read back at the end, and never prints a
value. Delete with the workflow once Phase B has landed."""
import json
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

FIRST_FIVE = ["FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN"]   # the live partial run
ORDER = ["FIT_PROFILE_EXTRA_JSON", "FIT_PROFILE_JSON", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN", "JIRA_API_TOKEN", "JIRA_EMAIL",
         "JOBRIGHT_EMAIL", "JOBRIGHT_PASSWORD", "LIFEOS_ACQ_DB_NAME", "LIFEOS_ACQ_DB_PASSWORD", "LIFEOS_ACQ_DB_USER", "LIFEOS_ACQ_SSH_HOST", "LIFEOS_ACQ_SSH_KNOWN_HOSTS",
         "LIFEOS_ACQ_SSH_PRIVATE_KEY", "LIFEOS_ACQ_SSH_USER", "NOTION_AMAZON_TOKEN", "NOTION_API_TOKEN", "NOTION_BILLS_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_JIRA_TOKEN",
         "NOTION_RECRUITERS_TOKEN", "NTFY_TOPIC", "OPEN_JOBS_CONTACT", "TINYFISH_API_KEY"]

STUB = r'''#!/usr/bin/env bash
# Test double for bws: a small stateful vault in $STUB_DIR/store.json, an ordered call log in $STUB_DIR/log, the real CLI's habit of returning values in its JSON,
# and failure switches. It never overwrites or deletes (the workflow must not ask it to).
set -u
d="$STUB_DIR"
log() { echo "$*" >> "$d/log"; }
case "$1 $2" in
  "project list") log "project-list"; printf '%s\n' "$STUB_PROJECTS" ;;
  "secret list")
    log "list"; [ "$3" = "$STUB_PROJECT" ] || exit 1
    jq 'map(. + {value: "STUB_LEAK"})' "$d/store.json" ;;
  "secret get")
    log "get $3"; jq -e --arg id "$3" '.[] | select(.id == $id)' "$d/store.json" || exit 1 ;;
  "secret create")
    shift 2; [ "$1" = "-o" ] && shift 2
    [ "$1" = "--" ] || { echo "stub: missing -- before positional arguments" >&2; exit 2; }
    key="$2"; value="$3"; project="$4"
    log "create $key"
    [ "$project" = "$STUB_PROJECT" ] || exit 1
    [ "$key" != "${STUB_FAIL_ON:-}" ] || { echo "stub failure carrying $value" >&2; exit 1; }
    if [ "$key" = "${STUB_RATELIMIT_ON:-}" ]; then
      tries=$(cat "$d/rl" 2>/dev/null || echo 0)
      if [ "$tries" -lt "${STUB_RATELIMIT_TIMES:-0}" ]; then echo $((tries + 1)) > "$d/rl"; echo "Error: 429 Too Many Requests carrying $value" >&2; exit 1; fi
    fi
    n=$(( $(cat "$d/count" 2>/dev/null || echo 100) + 1 )); echo "$n" > "$d/count"
    id=$(printf '00000000-0000-4000-8000-%012d' "$n")
    stored="$value"; [ "$key" != "${STUB_CORRUPT_ON_CREATE:-}" ] || stored="CORRUPTED"
    if [ "$key" != "${STUB_DROP_AFTER_CREATE:-}" ]; then
      jq --arg k "$key" --arg i "$id" --arg v "$stored" '. + [{key: $k, id: $i, value: $v}]' "$d/store.json" > "$d/store.tmp" && mv "$d/store.tmp" "$d/store.json"
    fi
    jq -n --arg k "$key" --arg i "$id" --arg v "$value" '{object: "secret", id: $i, key: $k, value: $v}' ;;
  *) exit 2 ;;
esac
'''
SLEEP_STUB = '#!/usr/bin/env bash\necho "sleep $1" >> "$STUB_DIR/log"\n'


def source_value(name):
    return f"-----VALUE_SENTINEL_{name}"      # leading dashes: the SSH-key shape


def pre_id(index):
    return f"11111111-1111-4111-8111-{index:012d}"


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
        for banned in ("actions/checkout", "actions/cache", "upload-artifact", "lifeos.run", "python", "toJSON", "set -x", "ACTIONS_STEP_DEBUG", "printenv", "export -p", "env |", "tinyfish.",
                       "base64", "sha256sum \"$", "md5sum", "LIFEOS_BWS_RUNTIME_TOKEN"):
            self.assertNotIn(banned, self.migrate["run"] + str(self.job.get("env", "")) + str(self.doc.get("env", "")), banned)
        for banned in ("actions/checkout", "actions/cache", "upload-artifact", "lifeos.run", "toJSON", "ACTIONS_STEP_DEBUG"):
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

    def test_the_only_write_is_one_create_and_it_is_called_only_after_every_preflight_check(self):
        run = self.migrate["run"]
        self.assertEqual(run.count("bws secret create"), 1)
        self.assertEqual(run.count("create_one \""), 1)
        call = run.index("create_one \"")
        for marker in ("PREFLIGHT 1/4", "PREFLIGHT 2/4", "PREFLIGHT 3/4", "PREFLIGHT 4/4", "bws project list", "DUPLICATE", "MISMATCH", "READ-BACK"):
            self.assertIn(marker, run, marker)
        for marker in ("PREFLIGHT 1/4", "PREFLIGHT 2/4", "PREFLIGHT 3/4", "PREFLIGHT 4/4", "stopped before any write"):
            self.assertLess(run.index(marker), call, marker)
        self.assertLess(call, run.index("READ-BACK"))
        self.assertNotRegex(run, r"secret (update|delete|edit)")

    def test_requests_are_paced_with_a_bounded_delay_and_only_a_rate_limit_is_retried_once(self):
        run = self.migrate["run"]
        self.assertRegex(run, r"PAUSE=[2-9]\b")
        self.assertEqual(len(re.findall(r'sleep "\$PAUSE"', run)), 3)             # before each read, each create, and the read-back listing
        self.assertIn("for attempt in 1 2", run)
        self.assertIn('[ "$attempt" -eq 1 ]', run)
        self.assertEqual(run.count("RATE_LIMIT_RE"), 2)                          # defined once, tested once
        self.assertNotRegex(run, r"\b(until|while)\b.*bws")

    def test_the_comparison_is_a_shell_builtin_and_never_hands_a_source_value_to_another_process(self):
        run = self.migrate["run"]
        self.assertIn('[[ "$STORED" == "${!n}" ]]', run)
        for line in run.splitlines():                                                       # every other use of a source value is an emptiness test or the builtin comparison
            if "${!" in line and "bws secret create" not in line:
                self.assertRegex(line.strip(), r'^(if \[ -z "\$\{!n:-\}" \]|if \[\[ "\$STORED" == "\$\{!n\}" \]\])', line)


@unittest.skipIf(yaml is None or shutil.which("jq") is None or shutil.which("bash") is None, "needs PyYAML, bash and jq")
class MigrationScriptBehaviour(unittest.TestCase):
    """Runs the workflow's real script against a stub bws and a stub sleep. Source values are sentinels; none may ever reach the output."""

    @classmethod
    def setUpClass(cls):
        cls.script = yaml.safe_load(PATH.read_text())["jobs"]["migrate"]["steps"][1]["run"]

    def run_script(self, preseed=(), wrong=(), duplicate=(), **override):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name, body in (("bws", STUB), ("sleep", SLEEP_STUB)):
                (root / name).write_text(body)
                (root / name).chmod(0o755)
            store = [{"key": f"lifeos/github-actions/{n}", "id": pre_id(ORDER.index(n) + 1), "value": f"-----OTHER_SENTINEL_{n}" if n in wrong else source_value(n)} for n in preseed]
            store += [{"key": f"lifeos/github-actions/{n}", "id": pre_id(50 + i), "value": source_value(n)} for i, n in enumerate(duplicate)]
            (root / "store.json").write_text(json.dumps(store))
            env = {"PATH": f"{tmp}:{os.environ['PATH']}", "STUB_DIR": tmp, "STUB_PROJECT": PROJECT, "STUB_PROJECTS": f'[{{"id":"{PROJECT}","name":"x"}}]', "BWS_ACCESS_TOKEN": "TOKEN_SENTINEL"}
            env.update({name: source_value(name) for name in APPROVED})
            env.update(override)
            done = subprocess.run(["bash", "-c", self.script], env=env, capture_output=True, text=True, cwd=tmp)
            log = (root / "log").read_text().splitlines() if (root / "log").exists() else []
            return done, log, json.loads((root / "store.json").read_text()), store

    def assertNoLeak(self, done):
        for needle in ("SENTINEL", "STUB_LEAK", "CORRUPTED"):
            self.assertNotIn(needle, done.stdout + done.stderr)

    @staticmethod
    def creates(log):
        return [line.split(" ", 1)[1].rsplit("/", 1)[1] for line in log if line.startswith("create ")]

    def test_a_fresh_run_creates_all_25_and_reads_them_back(self):
        done, log, store, _ = self.run_script()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.creates(log), ORDER)
        self.assertEqual(len(re.findall(r"^[A-Z0-9_]+ -> [0-9a-f-]{36} -> CREATED$", done.stdout, re.M)), 25)
        self.assertEqual(len(re.findall(r"^[A-Z0-9_]+ -> [0-9a-f-]{36} -> VERIFIED$", done.stdout, re.M)), 25)
        self.assertIn("VERIFIED 25 OF 25", done.stdout)
        self.assertEqual(len(store), 25)
        self.assertNoLeak(done)

    def test_the_live_partial_shape_five_present_and_equal_resumes_creates_only_the_other_20_and_verifies_25(self):
        done, log, store, before = self.run_script(preseed=FIRST_FIVE)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for i, name in enumerate(FIRST_FIVE, 1):
            self.assertIn(f"{name} -> {pre_id(i)} -> ALREADY_PRESENT", done.stdout)          # ids come from discovery, not from the workflow
            self.assertIn(f"{name} -> {pre_id(i)} -> VERIFIED", done.stdout)
        self.assertEqual(self.creates(log), ORDER[5:])                                       # exactly the 20 pending, never the five
        self.assertEqual(store[:5], before)                                                  # untouched: same ids, same values, nothing overwritten
        self.assertEqual(len(store), 25)
        self.assertEqual(len(re.findall(r" -> CREATED$", done.stdout, re.M)), 20)
        self.assertIn("VERIFIED 25 OF 25", done.stdout)
        self.assertNoLeak(done)

    def test_a_rerun_after_a_complete_run_changes_nothing(self):
        done, log, store, before = self.run_script(preseed=ORDER)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.creates(log), [])
        self.assertEqual(store, before)
        self.assertEqual(len(re.findall(r" -> ALREADY_PRESENT$", done.stdout, re.M)), 25)
        self.assertIn("VERIFIED 25 OF 25", done.stdout)

    def test_an_existing_destination_that_differs_from_its_source_stops_before_any_write_naming_only_the_key(self):
        done, log, store, before = self.run_script(preseed=FIRST_FIVE, wrong=["FIT_PROFILE_JSON"])
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.creates(log), [])
        self.assertEqual(store, before)
        self.assertEqual(re.findall(r"^MISMATCH (.*)$", done.stdout, re.M), ["lifeos/github-actions/FIT_PROFILE_JSON"])
        self.assertNotIn("VERIFIED", done.stdout)
        self.assertNoLeak(done)

    def test_a_duplicated_destination_key_stops_before_any_write(self):
        done, log, _, _ = self.run_script(preseed=["NTFY_TOPIC"], duplicate=["NTFY_TOPIC"])
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.creates(log), [])
        self.assertEqual(re.findall(r"^DUPLICATE (.*)$", done.stdout, re.M), ["lifeos/github-actions/NTFY_TOPIC"])

    def test_creates_are_paced_never_back_to_back(self):
        done, log, _, _ = self.run_script(preseed=FIRST_FIVE)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        seen_create = False
        for line in log:
            if line.startswith("create "):
                self.assertFalse(seen_create, "two creates with no pause between them")
                seen_create = True
            elif line.startswith("sleep "):
                seen_create = False
        pauses = [int(line.split()[1]) for line in log if line.startswith("sleep ")]
        self.assertGreaterEqual(len(pauses), 20)
        self.assertGreaterEqual(min(pauses), 2)
        before_each_get = [i for i, line in enumerate(log) if line.startswith("get ")]
        self.assertTrue(all(log[i - 1].startswith("sleep ") for i in before_each_get))      # reads are paced too

    def test_a_generic_failure_stops_with_a_fixed_code_retries_nothing_and_deletes_nothing(self):
        done, log, store, _ = self.run_script(preseed=FIRST_FIVE, STUB_FAIL_ON="lifeos/github-actions/JIRA_EMAIL")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.creates(log), ["JIRA_API_TOKEN", "JIRA_EMAIL"])               # one attempt at the failing key: no retry
        self.assertIn("PARTIAL CANDIDATE: BLOCKED ON JIRA_EMAIL (UNKNOWN_ERROR; 1 created this run, 5 already present", done.stdout)
        self.assertEqual(len(store), 6)
        self.assertNotIn("READ-BACK", done.stdout)
        self.assertNoLeak(done)

    def test_one_rate_limit_response_is_retried_exactly_once_after_a_wait_and_then_succeeds(self):
        done, log, _, _ = self.run_script(preseed=FIRST_FIVE, STUB_RATELIMIT_ON="lifeos/github-actions/JIRA_EMAIL", STUB_RATELIMIT_TIMES="1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.creates(log).count("JIRA_EMAIL"), 2)
        self.assertIn("RATE_LIMIT on JIRA_EMAIL", done.stdout)
        self.assertIn("sleep 30", log)
        self.assertIn("VERIFIED 25 OF 25", done.stdout)
        self.assertNoLeak(done)

    def test_a_persistent_rate_limit_gets_one_retry_only_then_stops_classified_and_safe(self):
        done, log, store, _ = self.run_script(preseed=FIRST_FIVE, STUB_RATELIMIT_ON="lifeos/github-actions/JIRA_EMAIL", STUB_RATELIMIT_TIMES="9")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.creates(log).count("JIRA_EMAIL"), 2)
        self.assertIn("BLOCKED ON JIRA_EMAIL (RATE_LIMIT;", done.stdout)
        self.assertEqual(len(store), 6)
        self.assertNoLeak(done)

    def test_the_read_back_fails_naming_only_the_key_when_a_created_secret_is_missing_and_repairs_nothing(self):
        done, log, store, _ = self.run_script(STUB_DROP_AFTER_CREATE="lifeos/github-actions/NTFY_TOPIC")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(re.findall(r"^MISSING (.*)$", done.stdout, re.M), ["lifeos/github-actions/NTFY_TOPIC"])
        self.assertEqual(self.creates(log), ORDER)                                           # no repair create after the read-back
        self.assertNotIn("VERIFIED 25", done.stdout)
        self.assertNoLeak(done)

    def test_the_read_back_fails_naming_only_the_key_when_a_stored_value_differs_and_repairs_nothing(self):
        done, log, _, _ = self.run_script(STUB_CORRUPT_ON_CREATE="lifeos/github-actions/OPEN_JOBS_CONTACT")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(re.findall(r"^MISMATCH (.*)$", done.stdout, re.M), ["lifeos/github-actions/OPEN_JOBS_CONTACT"])
        self.assertEqual(self.creates(log), ORDER)
        self.assertNotIn("VERIFIED 25", done.stdout)
        self.assertNoLeak(done)

    def test_an_empty_source_value_stops_before_any_request(self):
        done, log, _, _ = self.run_script(JIRA_EMAIL="")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(log, [])
        self.assertIn("MISSING JIRA_EMAIL", done.stdout)
        self.assertNoLeak(done)

    def test_a_machine_account_that_sees_more_or_other_projects_stops_before_any_write(self):
        for projects in ('[]', f'[{{"id":"{PROJECT}"}},{{"id":"11111111-1111-4111-8111-111111111111"}}]', '[{"id":"11111111-1111-4111-8111-111111111111"}]'):
            done, log, _, _ = self.run_script(STUB_PROJECTS=projects)
            self.assertNotEqual(done.returncode, 0, projects)
            self.assertEqual(self.creates(log), [], projects)

    def test_runner_debug_logging_refuses_to_run(self):
        done, log, _, _ = self.run_script(RUNNER_DEBUG="1")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()
