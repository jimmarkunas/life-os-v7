"""LIFE-89 (PLAT-GOV-2): decision numbers are claimed on the remote before use, so parallel sessions never take the same one. Every test runs against a
throwaway local bare repository standing in for GitHub; nothing touches the real remote."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeos import decisions

ROOT = Path(__file__).resolve().parents[2]
LOG = "# Decisions\n\n## D1 — First\nx\n\n## D2 — Second\nx\n\n## D2 amendment — more on D2\nx\n\n## D3 — Third\nx\n"


def git(*args, cwd):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert out.returncode == 0, (args, out.stderr)
    return out.stdout


class Remote(unittest.TestCase):
    """A bare 'origin' whose main holds a small decision log, and clones of it standing in for parallel sessions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin = os.path.join(self.tmp, "origin.git")
        git("init", "--bare", "--initial-branch=main", self.origin, cwd=self.tmp)
        seed = self.clone("seed")
        os.makedirs(os.path.join(seed, "docs"))
        Path(seed, "docs", "DECISIONS.md").write_text(LOG)
        git("add", "docs/DECISIONS.md", cwd=seed)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "log", cwd=seed)
        git("push", "-q", "origin", "HEAD:main", cwd=seed)

    def clone(self, name):
        path = os.path.join(self.tmp, name)
        git("clone", "-q", self.origin, path, cwd=self.tmp)
        return path

    def tags(self):
        return sorted(git("ls-remote", "--tags", self.origin, cwd=self.tmp).split())


class Claim(Remote):
    def test_the_next_number_is_claimed_above_the_log_and_every_earlier_claim(self):
        session = self.clone("a")
        self.assertEqual(decisions.claim(cwd=session), 4)                          # the log on main tops out at D3
        self.assertEqual(decisions.claim(cwd=session), 5)                          # D4 is now claimed on the remote
        self.assertEqual(decisions.claimed(cwd=session), {4, 5})

    def test_a_session_that_raced_for_a_taken_number_is_refused_by_the_remote_and_takes_the_next(self):
        a, b = self.clone("a"), self.clone("b")
        self.assertEqual(decisions.claim(cwd=a), 4)
        self.assertEqual(decisions.claim(cwd=b, start=4), 5)                       # b computed D4 too (a stale read): the remote refused it
        self.assertEqual(decisions.claimed(cwd=a), {4, 5})

    def test_two_claims_of_the_same_number_on_the_same_commit_are_still_distinct_objects(self):
        a, b = self.clone("a"), self.clone("b")
        self.assertEqual(decisions.claim(cwd=a, start=7), 7)
        self.assertEqual(decisions.claim(cwd=b, start=7), 8)                       # an identical tag would have looked "up to date" and succeeded

    def test_parallel_processes_never_get_the_same_number(self):
        sessions = [self.clone(f"s{i}") for i in range(4)]
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        procs = [subprocess.Popen([sys.executable, "-m", "lifeos.decisions", "claim"], cwd=s, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for s in sessions]
        got = [p.communicate(timeout=120)[0].strip() for p in procs]
        self.assertEqual(sorted(got), ["D4", "D5", "D6", "D7"], got)              # four sessions at once: four different numbers, no gaps lost to errors

    def test_an_unreadable_remote_is_a_named_failure(self):
        lone = tempfile.mkdtemp()
        git("init", "-q", lone, cwd=self.tmp)
        with self.assertRaises(decisions.ClaimError) as ctx:
            decisions.claim(remote="nowhere", cwd=lone)
        self.assertEqual(str(ctx.exception), "DECISION_CLAIM_FETCH_FAILED")


class Duplicates(unittest.TestCase):
    def test_a_reused_number_is_found_and_an_amendment_or_advice_is_not(self):
        text = LOG + "\n## D3 — Someone else's third\nx\n\n## D1 advice - on D1\nx\n"
        self.assertEqual(decisions.duplicates(text), {3: ["Third", "Someone else's third"]})
        self.assertEqual(decisions.duplicates(LOG), {})

    def test_the_index_is_checked_the_same_way(self):
        index = "- D1 — First\n- D2 — Second\n- D2 amendment — more\n- D2 — Clash\n"
        self.assertEqual(decisions.duplicates(index, decisions.INDEX_ENTRY), {2: ["Second", "Clash"]})
