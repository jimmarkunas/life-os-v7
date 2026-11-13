"""LIFE-638: every job fetches the repository with our own git step instead of actions/checkout. The step fetches the exact commit, passes the token once, masks it, and leaves nothing in the git config."""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
NAME = "Checkout the exact commit (own git fetch; the token is passed once and never stored)"
HEAD = ["auth=$(printf 'x-access-token:%s' '${{ github.token }}' | base64 | tr -d '\\n'); echo \"::add-mask::$auth\"",
        "git init -q . && git remote add origin \"$GITHUB_SERVER_URL/$GITHUB_REPOSITORY\""]
VARIANTS = {                                                       # the only three steps that may exist: the exact commit, its two parents (pr-tests), and main with every branch (branches archive)
    "default": HEAD + ["git -c \"http.extraheader=AUTHORIZATION: basic $auth\" fetch -q --no-tags --depth=1 origin \"$GITHUB_SHA\"", "git checkout -q --detach FETCH_HEAD"],
    "depth2": HEAD + ["git -c \"http.extraheader=AUTHORIZATION: basic $auth\" fetch -q --no-tags --depth=2 origin \"$GITHUB_SHA\"", "git checkout -q --detach FETCH_HEAD"],
    "main": HEAD + ["git -c \"http.extraheader=AUTHORIZATION: basic $auth\" fetch -q --tags origin '+refs/heads/*:refs/remotes/origin/*'", "git checkout -q -B main origin/main"],
}
TOKEN = "ghs_FIXTURETOKEN0123456789"


def script(variant):
    return "\n".join(VARIANTS[variant])


def git(cwd, *args, **kw):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, **kw).stdout.strip()


def make_remote(tmp):
    remote = pathlib.Path(tmp, "owner", "repo")
    remote.mkdir(parents=True)
    git(remote, "init", "-q", "-b", "main")
    for key, value in (("user.email", "fixture.invalid"), ("user.name", "t"), ("uploadpack.allowAnySHA1InWant", "true")):
        git(remote, "config", key, value)
    (remote / "one.txt").write_text("1")
    git(remote, "add", "."), git(remote, "commit", "-qm", "one")
    (remote / "two.txt").write_text("2")
    git(remote, "add", "."), git(remote, "commit", "-qm", "two")
    git(remote, "branch", "side")
    return remote


def run_step(variant, tmp, remote, sha, token=TOKEN, text=None):
    work = pathlib.Path(tmp, "work")
    work.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), "GITHUB_SERVER_URL": "file://" + str(remote.parents[1]), "GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": sha, "GITHUB_TOKEN": token}
    done = subprocess.run(["bash", "-c", (text or script(variant)).replace("${{ github.token }}", token)], cwd=work, env=env, capture_output=True, text=True, timeout=60)
    return work, done


class StepBehaviour(unittest.TestCase):
    def test_it_fetches_the_exact_commit_and_leaves_no_token_in_the_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = make_remote(tmp)
            first = git(remote, "rev-parse", "HEAD~1")
            work, done = run_step("default", tmp, remote, first)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(git(work, "rev-parse", "HEAD"), first)                                            # the exact commit, not the newest one
            self.assertTrue((work / "one.txt").exists() and not (work / "two.txt").exists())
            self.assertEqual(git(work, "rev-list", "--count", "HEAD"), "1")                                     # depth 1
            config = (work / ".git" / "config").read_text() + git(work, "config", "--list")
            self.assertNotIn(TOKEN, config)
            self.assertNotIn("extraheader", config)
            lines = done.stdout.splitlines()
            self.assertTrue(lines[0].startswith("::add-mask::") and TOKEN not in lines[0])                      # the encoded header is masked first
            self.assertNotIn(TOKEN, done.stdout + done.stderr)

    def test_depth2_keeps_the_parent_and_main_fetches_every_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = make_remote(tmp)
            work, done = run_step("depth2", tmp, remote, git(remote, "rev-parse", "HEAD"))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(git(work, "rev-list", "--count", "HEAD"), "2")
        with tempfile.TemporaryDirectory() as tmp:
            remote = make_remote(tmp)
            work, done = run_step("main", tmp, remote, "unused")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(git(work, "branch", "--show-current"), "main")
            self.assertIn("origin/side", git(work, "branch", "-r"))
            self.assertEqual(git(work, "rev-list", "--count", "HEAD"), "2")

    def test_a_step_that_stores_the_token_fails_the_same_check(self):                                       # fail-then-pass: the leak this test exists to catch
        leaking = script("default").replace("git -c \"http.extraheader=AUTHORIZATION: basic $auth\" fetch", "git config http.extraheader \"AUTHORIZATION: basic $auth\" && git fetch")
        with tempfile.TemporaryDirectory() as tmp:
            remote = make_remote(tmp)
            work, done = run_step("default", tmp, remote, git(remote, "rev-parse", "HEAD"), text=leaking)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("extraheader", (work / ".git" / "config").read_text())


@unittest.skipIf(yaml is None, "PyYAML not installed")
class EveryWorkflow(unittest.TestCase):
    def test_no_workflow_uses_actions_checkout_and_every_checkout_step_is_one_of_the_three_exact_steps(self):
        found = 0
        for path in sorted((ROOT / ".github").rglob("*.yml")):
            text = path.read_text()
            self.assertNotIn("uses: actions/checkout", text, path.name)
            if path.parent.name != "workflows":
                continue
            for job, body in yaml.safe_load(text)["jobs"].items():
                steps = body.get("steps", [])
                named = [s for s in steps if s.get("name") == NAME]
                if steps and steps[0].get("name") == NAME:
                    found += 1
                    self.assertEqual(len(named), 1, (path.name, job))
                    run = "\n".join(re.sub(r"\s+#.*$", "", line) for line in named[0]["run"].strip().splitlines())
                    self.assertIn(run, [script(v) for v in VARIANTS], (path.name, job))
                    self.assertNotIn("env", named[0], (path.name, job))                                         # no step environment: the token is the runner's own, written once in the command
                else:
                    self.assertEqual(named, [], (path.name, job))
        self.assertGreaterEqual(found, 40)


if __name__ == "__main__":
    unittest.main()
