"""LIFE-663: tools/push_life_branch.sh is the one force-style push an agent may run: it pushes only the current life/* branch, with a lease on the tip it just fetched.
Everything runs against throw-away local bare repositories (no network, no GitHub); the script is run with `sh`, as agents will."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "push_life_branch.sh"
BRANCH = "life/LIFE-663/demo"


def clean_env(**more):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", **more)
    return env


def git(cwd, *args, check=True, env=None):
    env = env or clean_env()
    done = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, env=env, capture_output=True, text=True)
    if check and done.returncode:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done


class Wrapper(unittest.TestCase):
    def setUp(self):
        self.env = clean_env()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bare, self.work, self.other = self.tmp / "remote.git", self.tmp / "work", self.tmp / "other"
        git(self.tmp, "init", "-q", "--bare", str(self.bare))
        git(self.tmp, "clone", "-q", str(self.bare), str(self.work))
        self.commit(self.work, "seed")
        git(self.work, "push", "-q", "origin", "main")

    def commit(self, repo, name):
        (repo / f"{name}.txt").write_text(name)
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", name)
        return self.head(repo)

    def head(self, repo):
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    def remote(self, branch):
        done = git(self.bare, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False)
        return done.stdout.strip() or None

    def run_script(self, *args):
        return subprocess.run(["sh", str(SCRIPT), *args], cwd=self.work, env=self.env, capture_output=True, text=True)

    def start_branch(self, push=True):
        git(self.work, "checkout", "-q", "-b", BRANCH)
        tip = self.commit(self.work, "one")
        if push:
            git(self.work, "push", "-q", "origin", BRANCH)
        return tip

    def test_the_script_is_executable_posix_sh(self):
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(SCRIPT.read_text().splitlines()[0], "#!/bin/sh")
        self.assertEqual(subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True).returncode, 0)

    def test_it_refuses_main_and_pushes_nothing(self):
        before = self.remote("main")
        self.commit(self.work, "local only")
        done = self.run_script()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("refused", done.stderr)
        self.assertEqual(self.remote("main"), before)

    def test_it_refuses_a_detached_head(self):
        git(self.work, "checkout", "-q", "--detach")
        done = self.run_script()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("detached", done.stderr)
        self.assertEqual(git(self.bare, "for-each-ref", "--format=%(refname)").stdout.split(), ["refs/heads/main"])

    def test_it_refuses_a_branch_that_is_not_under_life(self):
        for name in ("feature/x", "lifeboat", "master", "claude/LIFE-1/x"):
            git(self.work, "checkout", "-q", "-B", name, "main")
            done = self.run_script()
            self.assertNotEqual(done.returncode, 0, name)
            self.assertIn("refused", done.stderr, name)
            self.assertIsNone(self.remote(name), name)

    def test_a_new_life_branch_is_pushed_plainly_and_tracks_origin(self):
        tip = self.start_branch(push=False)
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.remote(BRANCH), tip)
        self.assertEqual(git(self.work, "rev-parse", "--abbrev-ref", f"{BRANCH}@{{upstream}}").stdout.strip(), f"origin/{BRANCH}")

    def test_a_rewritten_branch_updates_the_remote_and_prints_old_and_new(self):
        old = self.start_branch()
        git(self.work, "commit", "-q", "--amend", "-m", "one, reworded")
        new = self.head(self.work)
        self.assertNotEqual(old, new)
        self.assertNotEqual(git(self.work, "push", "origin", BRANCH, check=False).returncode, 0)   # a plain push cannot do this: that is why the script exists
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.remote(BRANCH), new)
        self.assertIn(f"{old[:12]} -> {new[:12]}", done.stdout)

    def test_a_rebase_onto_a_newer_main_updates_the_remote(self):
        self.start_branch()
        git(self.work, "checkout", "-q", "main")
        self.commit(self.work, "main moves on")
        git(self.work, "push", "-q", "origin", "main")
        git(self.work, "checkout", "-q", BRANCH)
        git(self.work, "rebase", "-q", "main")
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.remote(BRANCH), self.head(self.work))

    def test_nothing_new_to_push_succeeds_without_change(self):
        tip = self.start_branch()
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.remote(BRANCH), tip)

    def _install_race(self):
        """A `git` stand-in first on PATH: when the script (or the control) runs `git push`, someone else's newer commit lands on the same branch just before the real
        push starts. By then the script has already fetched, so only the lease can protect that commit."""
        git(self.tmp, "clone", "-q", str(self.bare), str(self.other))
        git(self.other, "checkout", "-q", BRANCH)
        theirs = self.commit(self.other, "someone else's newer commit")
        real = shutil.which("git")
        shim = self.tmp / "bin" / "git"
        shim.parent.mkdir()
        shim.write_text(f"#!/bin/sh\nif [ \"$1\" = push ] && [ ! -e '{self.tmp}/raced' ]; then\n  : > '{self.tmp}/raced'\n"
                        f"  '{real}' -C '{self.other}' push -q origin {BRANCH} || exit 1\nfi\nexec '{real}' \"$@\"\n")
        shim.chmod(0o755)
        self.env = clean_env(PATH=f"{shim.parent}:{os.environ['PATH']}")
        return theirs

    def test_a_commit_pushed_by_someone_else_after_the_fetch_is_not_overwritten(self):
        self.start_branch()
        theirs = self._install_race()
        git(self.work, "commit", "-q", "--amend", "-m", "mine, rewritten")
        done = self.run_script()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("lease", done.stderr)
        self.assertEqual(self.remote(BRANCH), theirs)                                  # their commit is still the tip

    def test_the_same_race_does_overwrite_with_an_ordinary_force(self):
        """Control: the identical race with `git push --force` destroys the other commit, so the test above is measuring the lease."""
        self.start_branch()
        theirs = self._install_race()
        git(self.work, "commit", "-q", "--amend", "-m", "mine, rewritten")
        git(self.work, "push", "-q", "--force", "origin", BRANCH, env=self.env)
        self.assertEqual(self.remote(BRANCH), self.head(self.work))
        self.assertNotEqual(self.remote(BRANCH), theirs)

    def test_extra_arguments_are_ignored_and_cannot_change_the_target(self):
        old = self.start_branch()
        main_before = self.remote("main")
        git(self.work, "commit", "-q", "--amend", "-m", "one, reworded")
        done = self.run_script("--delete", "main", "--force", "+main:main", f":{BRANCH}", "origin")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ignoring 6 argument(s)", done.stderr)
        self.assertEqual(self.remote("main"), main_before)
        self.assertEqual(self.remote(BRANCH), self.head(self.work))
        self.assertNotEqual(self.remote(BRANCH), old)
        self.assertEqual(sorted(git(self.bare, "for-each-ref", "--format=%(refname)").stdout.split()), ["refs/heads/" + BRANCH, "refs/heads/main"])

    def test_arguments_cannot_rescue_a_refused_branch(self):
        before = self.remote("main")
        self.commit(self.work, "local only")
        for args in (("--delete", BRANCH), ("--force",), (BRANCH,), ("+HEAD:main",)):
            self.assertNotEqual(self.run_script(*args).returncode, 0, args)
        self.assertEqual(self.remote("main"), before)

    def test_the_script_source_has_no_delete_and_no_plus_refspec(self):
        code = [line for line in SCRIPT.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
        text = "\n".join(code)
        for forbidden in ("--delete", " -d ", "--force ", " -f ", "--mirror", "--prune", '"+', " +"):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertEqual(text.count("git push"), 2)                                   # the plain first push and the one lease push, nothing else



class Settings(unittest.TestCase):
    DENY = ["git push --force:*", "git push -f:*", "git push --force-with-lease:*", "git push --delete:*", "git push -d:*", "git push * --force*", "git push * -f*",
            "git push * --delete*", "git push * -d *", "git push * +*", "git branch -D:*", "git branch -d:*", "git branch --delete:*", "git branch * -D*", "git branch * --delete*"]

    def test_the_wrapper_is_allowed_and_every_force_and_delete_form_stays_denied(self):
        perms = json.loads((ROOT / ".claude" / "settings.json").read_text())["permissions"]
        self.assertIn("Bash(sh tools/push_life_branch.sh:*)", perms["allow"])
        self.assertEqual(sorted(perms["deny"]), sorted(f"Bash({rule})" for rule in self.DENY))   # LIFE-663 loosened no deny rule
        self.assertNotIn("Bash(git push:*)", perms["deny"])


if __name__ == "__main__":
    unittest.main()
