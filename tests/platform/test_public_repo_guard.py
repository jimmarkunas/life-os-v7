"""The public repository holds no personal email address and no real Notion id, in files, commits or pull requests."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lifeos.platform import public_repo_guard as guard

ROOT = Path(__file__).resolve().parents[2]
ALLOW = ROOT / "tools" / "public_repo_guard_allow.txt"

# Bad values are built from pieces, so this file does not contain one.
BAD_MAIL = "someone" + "@" + "gmail" + ".com"
BAD_UUID = "1a2b3c4d" + "-5926-" + "4e5f-9a8b-7c6d5e4f3a2b"
VENDOR = "sender" + "@" + "vendor.test"
BAD_HEX = "".join(["9f8e7d6c", "5b4a4938", "8271605f", "4e3d2c1b"])


def tracked_text():
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    for name in filter(None, names):
        try:
            yield name, (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def event():
    path = os.environ.get("GITHUB_EVENT_PATH")
    return json.loads(Path(path).read_text()) if path and Path(path).is_file() else {}


class Tree(unittest.TestCase):
    def test_every_tracked_file_is_clean(self):
        allowed = guard.load_allowlist()
        found = [guard.format_finding(f) for name, text in tracked_text() for f in guard.scan_text(name, text, allowed)]
        self.assertEqual(found, [], "see tools/public_repo_guard_allow.txt and docs/PRIVACY.md")

    def test_every_allowlist_entry_has_a_reason_and_still_matches_something(self):
        allowed = guard.load_allowlist()
        seen = set()
        for name, text in tracked_text():
            if name != "tools/public_repo_guard_allow.txt":
                seen |= {guard.digest("personal email", m.group(0)) for m in guard.EMAIL.finditer(text)}
                seen |= {guard.digest("notion id", m.group(1)) for m in guard.ID.finditer(text)}
        self.assertGreater(len(allowed), 0)
        for value, reason in allowed.items():
            self.assertTrue(reason.strip(), value[:6])
            self.assertIn(value, seen, f"unused entry {value[:6]}: remove it")

    def test_a_line_without_a_reason_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "allow.txt"
            for bad in ("value-only\n", "abc |\n", " | reason\n", "not-a-hash | reason\n"):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    guard.load_allowlist(str(path))
            line = guard.digest("personal email", VENDOR) + " | a vendor sender address\n"
            path.write_text("# comment\n\n" + line)
            self.assertEqual(
                guard.load_allowlist(str(path)), {guard.digest("personal email", VENDOR): "a vendor sender address"}
            )


class Pull(unittest.TestCase):
    """In CI only: the pull request's title, body and commit messages"""

    def test_the_pull_request_text_is_clean(self):
        request = event().get("pull_request")
        if not request:
            self.skipTest("not a pull request run")
        texts = {"pull request title": request.get("title") or "", "pull request body": request.get("body") or ""}
        base, head = (request.get("base") or {}).get("sha"), (request.get("head") or {}).get("sha")
        if base and head:
            log = subprocess.run(
                ["git", "log", "--format=%B%x00", f"{base}..{head}"], cwd=ROOT, capture_output=True, text=True
            )
            if log.returncode == 0:
                for number, message in enumerate(filter(str.strip, log.stdout.split("\x00")), 1):
                    texts[f"commit message {number}"] = message
        found = [guard.format_finding(f) for name, text in texts.items() for f in guard.scan_text(name, text)]
        self.assertEqual(found, [])


class Rules(unittest.TestCase):
    def test_a_personal_email_is_a_finding_with_file_line_and_a_short_snippet(self):
        found = guard.scan_text("docs/x.md", f"first\nwrite to {BAD_MAIL} today\n", {})
        self.assertEqual([(f[0], f[1], f[2]) for f in found], [("docs/x.md", 2, "personal email")])
        self.assertLessEqual(len(found[0][3]), 6)
        self.assertNotIn(BAD_MAIL, str(found))

    def test_allowed_addresses_pass(self):
        for address in (
            "noreply" + "@" + "anthropic.com",
            "noreply" + "@" + "github.com",
            "a@example.com",
            "a" + "@" + "mail.example.org",
            "x" + "@" + "example.net",
        ):
            self.assertEqual(guard.scan_text("f", address, {}), [], address)
        listed = {guard.digest("personal email", VENDOR): "r"}
        self.assertEqual(guard.scan_text("f", VENDOR.upper(), listed), [])
        self.assertEqual(len(guard.scan_text("f", VENDOR, {})), 1)

    def test_a_real_looking_id_is_a_finding_dashed_or_plain_and_never_shown_whole(self):
        for value in (BAD_UUID, BAD_UUID.replace("-", ""), BAD_HEX, BAD_HEX.upper()):
            found = guard.scan_text("tests/x.py", f'ID = "{value}"', {})
            self.assertEqual([(f[1], f[2]) for f in found], [(1, "notion id")], value[:4])
            self.assertLessEqual(len(found[0][3]), 6)
            self.assertNotIn(value[:8], found[0][3] + str(found).replace(found[0][3], ""))

    def test_made_up_ids_and_listed_ids_pass(self):
        for value in (
            "00000000-0000-0000-0000-000000000001",
            "11111111-2222-4333-8444-555555555555",
            "1234567890abcdef1234567890abcdef",
            "00000000000000000000000000000abc",
        ):
            self.assertEqual(guard.scan_text("f", value, {}), [], value)
        self.assertEqual(guard.scan_text("f", BAD_HEX, {guard.digest("notion id", BAD_HEX): "a proven hash"}), [])
        self.assertEqual(
            guard.scan_text("f", BAD_UUID, {guard.digest("notion id", BAD_UUID): "r"}), []
        )  # dashed or plain

    def test_longer_hex_such_as_a_commit_or_content_hash_is_not_an_id(self):
        self.assertEqual(guard.scan_text("f", "sha " + "a1b2c3d4e5" * 4 + " end", {}), [])
        self.assertEqual(guard.scan_text("f", "x" + BAD_HEX + "x", {}), [])

    def test_a_diff_reports_only_added_lines_with_new_file_line_numbers(self):
        diff = (
            "diff --git a/docs/y.md b/docs/y.md\n--- a/docs/y.md\n+++ b/docs/y.md\n@@ -1,0 +5,2 @@\n"
            f"+fine\n+mail {BAD_MAIL}\n@@ -9 +11 @@\n-old {BAD_MAIL}\n+new {BAD_HEX}\n"
        )
        found = guard.scan_diff(diff, {})
        self.assertEqual(
            [(f[0], f[1], f[2]) for f in found], [("docs/y.md", 6, "personal email"), ("docs/y.md", 11, "notion id")]
        )

    def test_main_reports_a_message_file_and_exits_nonzero_only_on_a_finding(self):
        with tempfile.TemporaryDirectory() as folder:
            bad, good = Path(folder) / "bad", Path(folder) / "good"
            bad.write_text(f"fix\n\ncc {BAD_MAIL}\n")
            good.write_text("fix\n\ncc a@example.com\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(guard.main(["--message", str(good)]), 0)
                self.assertEqual(guard.main(["--message", str(bad)]), 1)


class Hooks(unittest.TestCase):
    def test_the_installed_hooks_block_a_staged_violation_and_a_message_but_not_clean_work(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run = lambda *a, **k: subprocess.run(a, cwd=repo, capture_output=True, text=True, **k)  # noqa: E731
            run("git", "init", "-q")
            (repo / "lifeos/platform").mkdir(parents=True)
            (repo / "tools").mkdir()
            shutil.copy(ROOT / "lifeos/platform/public_repo_guard.py", repo / "lifeos/platform/public_repo_guard.py")
            shutil.copy(ROOT / "tools/install_hooks.sh", repo / "tools/install_hooks.sh")
            (repo / "tools/public_repo_guard_allow.txt").write_text("")
            self.assertEqual(run("sh", "tools/install_hooks.sh").returncode, 0)
            self.assertEqual(run("sh", "tools/install_hooks.sh").returncode, 0)  # again: ours, so replaced
            ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
            (repo / "ok.txt").write_text("fine\n")
            run("git", "add", "ok.txt")
            self.assertEqual(run("git", *ident, "commit", "-q", "-m", "clean").returncode, 0)
            (repo / "bad.txt").write_text(f"mail {BAD_MAIL}\n")
            run("git", "add", "bad.txt")
            blocked = run("git", *ident, "commit", "-q", "-m", "staged mail")
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("bad.txt:1: personal email", blocked.stderr)
            self.assertNotIn(BAD_MAIL, blocked.stderr)
            run("git", "rm", "-q", "--cached", "bad.txt")
            message = run("git", *ident, "commit", "-q", "--allow-empty", "-m", f"cc {BAD_MAIL}")
            self.assertNotEqual(message.returncode, 0)
            self.assertIn("commit message:", message.stderr)

    def test_a_checkout_without_the_guard_file_commits_freely(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)  # noqa: E731
            run("git", "init", "-q")
            (repo / "tools").mkdir()
            shutil.copy(ROOT / "tools/install_hooks.sh", repo / "tools/install_hooks.sh")
            self.assertEqual(run("sh", "tools/install_hooks.sh").returncode, 0)
            (repo / "a.txt").write_text(f"mail {BAD_MAIL}\n")
            run("git", "add", "a.txt")
            done = run(
                "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "old checkout"
            )
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_an_existing_foreign_hook_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            subprocess.run(["git", "init", "-q"], cwd=repo)
            (repo / "tools").mkdir()
            shutil.copy(ROOT / "tools/install_hooks.sh", repo / "tools/install_hooks.sh")
            hook = repo / ".git/hooks/pre-commit"
            hook.write_text("#!/bin/sh\necho mine\n")
            result = subprocess.run(["sh", "tools/install_hooks.sh"], cwd=repo, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(hook.read_text(), "#!/bin/sh\necho mine\n")


if __name__ == "__main__":
    unittest.main()
