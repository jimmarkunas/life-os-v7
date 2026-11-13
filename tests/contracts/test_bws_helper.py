"""LIFE-406 (extends D209): the Bitwarden helper program comes from a saved, fingerprint-checked copy; the download retries only when no copy exists."""
import glob
import hashlib
import os
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ACTION = os.path.join(ROOT, ".github", "actions", "bws-helper")
SM_COMMIT = "1238aae8fc64b212641190a9227c8a734ab1a793"
CACHE_COMMIT = "0057852bfaa89a56745cba8c7296529d2fc39830"   # actions/cache v4.3.0


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _jobs():
    for path in sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))):
        doc = yaml.safe_load(_read(path))
        for name, job in doc["jobs"].items():
            yield os.path.basename(path), name, job


class WorkflowWiring(unittest.TestCase):
    def test_every_bitwarden_job_seeds_the_helper_right_before_its_first_load(self):
        seen = 0
        for file, name, job in _jobs():
            steps = job.get("steps") or []
            loaders = [i for i, s in enumerate(steps) if s.get("uses") == "./.github/actions/bws-load"]
            if not loaders:
                continue
            seen += 1
            first = loaders[0]
            self.assertGreater(first, 0, (file, name))
            seed = steps[first - 1]
            self.assertEqual(seed.get("uses"), "./.github/actions/bws-helper", (file, name))
            self.assertNotIn("id", seed, (file, name))                    # nothing downstream reads the helper step
            self.assertNotIn("continue-on-error", seed, (file, name))     # a missing verified helper fails the job; no fallback
            wait = int(seed["with"]["wait_seconds"])
            self.assertTrue(0 < wait <= 600, (file, name, wait))          # about 10 minutes at most
            limit = job.get("timeout-minutes", 360)
            self.assertLessEqual(wait * 3, limit * 60, (file, name))      # the retry never takes more than a third of the job's time limit
            self.assertIn("ubuntu", str(job["runs-on"]), (file, name))    # the saved program is the Linux x86_64 build
            self.assertEqual(len(loaders), 1, (file, name))               # one loader step per job: the load and its one retry (D209) live inside the local action (LIFE-631)
        self.assertEqual(seen, 45)                                        # 45 jobs, one seed each (LIFE-571: + amazon_retention)

    def test_the_helper_is_called_exactly_once_per_job(self):
        for file, name, job in _jobs():
            calls = [s for s in job.get("steps") or [] if s.get("uses") == "./.github/actions/bws-helper"]
            loaders = [s for s in job.get("steps") or [] if s.get("uses") == "./.github/actions/bws-load"]
            self.assertEqual(len(calls), 1 if loaders else 0, (file, name))


class CompositeAction(unittest.TestCase):
    def setUp(self):
        self.text = _read(os.path.join(ACTION, "action.yml"))
        self.doc = yaml.safe_load(self.text)

    def test_cache_actions_are_pinned_to_a_commit_and_the_key_holds_no_secret(self):
        self.assertEqual(re.findall(r"uses:\s*(actions/cache/\w+)@(\S+)", self.text), [("actions/cache/restore", CACHE_COMMIT), ("actions/cache/save", CACHE_COMMIT)])
        for key in re.findall(r"^\s*key:\s*(.+)$", self.text, re.M):
            self.assertEqual(key, "bws-helper-3.0.1-${{ hashFiles('.github/actions/bws-helper/helper.sha256') }}")   # version plus the committed fingerprint, nothing else
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("access_token", self.text)
        self.assertNotIn("GITHUB_ENV", self.text)

    def test_the_program_is_the_pinned_release_at_the_path_sm_action_checks(self):
        self.assertIn(f"_actions/bitwarden/sm-action/{SM_COMMIT}/target/x86_64-unknown-linux-gnu/release", self.text)
        self.assertIn("releases/download/v3.0.1/sm-action-x86_64-unknown-linux-gnu", self.text)
        loader = _read(os.path.join(ROOT, ".github", "actions", "bws-load", "action.yml"))
        self.assertEqual(set(re.findall(r"uses:\s*bitwarden/sm-action@(\w+)", loader)), {SM_COMMIT})   # the seeded folder is the folder the pinned action reads

    def test_fingerprint_is_committed_hex_never_read_from_the_download(self):
        fingerprint = _read(os.path.join(ACTION, "helper.sha256")).strip()
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(fingerprint, "fa998e9db775d5c7bdbc0fd8840ac6474a8348cdc989176c5ce24c3d09045752")   # GitHub's published digest of the v3.0.1 Linux x86_64 asset, matched by a download on Nov 8, 2026
        script = _read(os.path.join(ACTION, "fetch.sh"))
        self.assertIn("helper.sha256", self.text)
        self.assertNotRegex(script, r"sha256sum[^\n]*>\s*\S*sha256")        # the fingerprint file is only read

    def test_test_input_defaults_to_normal_and_only_fail_changes_anything(self):
        self.assertEqual(self.doc["inputs"]["test_download"]["default"], "normal")
        self.assertEqual(self.text.count("test_download != 'fail'") + self.text.count("test_download == 'fail'"), 2)


def run_fetch(dest, url, fingerprint, wait="30", pause="1"):
    with tempfile.TemporaryDirectory() as tmp:
        sha_file = os.path.join(tmp, "helper.sha256")
        out = os.path.join(tmp, "out")
        with open(sha_file, "w", encoding="utf-8") as handle:
            handle.write(fingerprint + "\n")
        env = {"PATH": os.environ["PATH"], "BWS_DEST": dest, "BWS_SHA256_FILE": sha_file, "BWS_URL": url, "BWS_WAIT_SECONDS": wait,
               "BWS_FIRST_PAUSE": pause, "BWS_SLEEP_CMD": "true", "GITHUB_OUTPUT": out}
        done = subprocess.run(["bash", os.path.join(ACTION, "fetch.sh")], env=env, capture_output=True, text=True, timeout=60)
        source = [line for line in (_read(out).splitlines() if os.path.exists(out) else []) if line.startswith("source=")]
        return done, source


class FetchScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.good = os.path.join(self.tmp.name, "good")
        with open(self.good, "wb") as handle:
            handle.write(b"example helper program")
        self.sha = hashlib.sha256(b"example helper program").hexdigest()
        self.dest = os.path.join(self.tmp.name, "release", "sm-action")
        self.dead = "file://" + os.path.join(self.tmp.name, "missing")

    def test_gwt_bw_1_a_verified_saved_copy_means_no_download(self):
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest, "wb") as handle:
            handle.write(b"example helper program")
        done, source = run_fetch(self.dest, self.dead, self.sha)      # the download address is dead: any download attempt would fail the run
        self.assertEqual((done.returncode, source), (0, ["source=saved"]), done.stdout + done.stderr)
        self.assertIn("no download", done.stdout)
        self.assertTrue(os.access(self.dest, os.X_OK))

    def test_gwt_bw_2_download_retries_with_growing_pauses_then_fails_clearly(self):
        done, source = run_fetch(self.dest, self.dead, self.sha, wait="600", pause="10")
        self.assertEqual((done.returncode, source), (1, []))
        pauses = [int(n) for n in re.findall(r"retrying in (\d+)s", done.stdout)]
        self.assertEqual(pauses[:5], [10, 20, 40, 80, 120])           # growing, capped at 120 s
        self.assertGreater(sum(pauses), 500)                           # a 600 s budget really spends about ten minutes
        self.assertLessEqual(sum(pauses), 600)
        self.assertIn("Bitwarden was not contacted", done.stdout)
        self.assertFalse(os.path.exists(self.dest))

    def test_gwt_bw_2_a_download_that_comes_back_is_used_once_verified(self):
        done, source = run_fetch(self.dest, "file://" + self.good, self.sha)
        self.assertEqual((done.returncode, source), (0, ["source=downloaded"]), done.stdout + done.stderr)
        self.assertEqual(_read(self.dest), "example helper program")

    def test_gwt_bw_3_a_saved_copy_with_the_wrong_fingerprint_is_discarded_not_run(self):
        os.makedirs(os.path.dirname(self.dest))
        with open(self.dest, "wb") as handle:
            handle.write(b"tampered")
        done, source = run_fetch(self.dest, self.dead, self.sha)       # no good download either: the job fails and the bad copy is gone
        self.assertEqual((done.returncode, source), (1, []))
        self.assertIn("does not match the committed fingerprint; discarded", done.stdout)
        self.assertFalse(os.path.exists(self.dest))
        done, source = run_fetch(self.dest, "file://" + self.good, self.sha)   # with a good download the discarded copy is replaced
        self.assertEqual((done.returncode, source), (0, ["source=downloaded"]))

    def test_a_download_that_does_not_match_is_never_kept(self):
        done, source = run_fetch(self.dest, "file://" + self.good, "0" * 64)
        self.assertEqual((done.returncode, source), (1, []))
        self.assertIn("discarded, not used", done.stdout)
        self.assertFalse(os.path.exists(self.dest) or os.path.exists(self.dest + ".part"))

    def test_a_malformed_fingerprint_file_fails_closed(self):
        done, _ = run_fetch(self.dest, "file://" + self.good, "not-a-hash")
        self.assertEqual(done.returncode, 1)
        self.assertFalse(os.path.exists(self.dest))

    def test_the_script_never_touches_a_credential(self):
        script = _read(os.path.join(ACTION, "fetch.sh"))
        for word in ("secrets", "access_token", "TOKEN", "GITHUB_ENV"):
            self.assertNotIn(word, script)


if __name__ == "__main__":
    unittest.main()
