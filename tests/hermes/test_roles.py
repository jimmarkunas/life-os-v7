import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERMES = os.path.join(ROOT, "hermes")
ROLES = ("main", "claude-hermes", "developer", "librarian", "research", "writer")
SECRET = re.compile(r"sk-[A-Za-z0-9]{10,}|nsec1[a-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|password|token|secret)\s*[:=]\s*\S{6,}", re.I)


def read(*parts):
    with open(os.path.join(HERMES, *parts)) as handle:
        return handle.read()


class RoleFiles(unittest.TestCase):
    def test_every_role_file_exists_is_short_and_has_no_secret(self):
        for name in ROLES:
            text = read("roles", name, "SOUL.md")
            self.assertTrue(text.strip() and text.endswith("\n"), name)
            self.assertLessEqual(len(text.encode()), 2400, name)                      # the deploy script's own limit is 4096
            self.assertIsNone(SECRET.search(text), name)

    def test_the_librarian_points_at_the_notion_wiki_and_not_a_local_folder(self):
        text = read("roles", "librarian", "SOUL.md")
        for needle in ("Notion", "Hermes Wiki", "cannot write", "source: Jim", "source: outside", "ask"):
            self.assertIn(needle.lower(), text.lower(), needle)
        for banned in ("~/wiki", "/var/lib", "wiki folder", "index.md"):
            self.assertNotIn(banned, text, banned)

    def test_the_main_role_names_the_notion_wiki_and_the_ask_first_rule(self):
        text = read("roles", "main", "SOUL.md")
        self.assertIn("Hermes Wiki", text)
        self.assertIn("Ask Jim before adding any new entry", text)
        self.assertIn("Keep no wiki pages on this machine", text)

    def test_the_repo_holds_no_user_memory_and_no_connections_change(self):
        for dirpath, _dirs, files in os.walk(HERMES):
            for name in files:
                self.assertNotEqual(name, "USER.md", dirpath)
                self.assertNotEqual(name, "config.yaml", dirpath)
        for name in ROLES:
            self.assertNotIn("connections", read("roles", name, "SOUL.md").lower().replace("hub connections", ""))

    def test_the_skills_manifest_lists_names_only(self):
        names = [line.strip() for line in read("skills.manifest").splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(names, ["atlassian-mcp", "hostinger-mcp"])


class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pack = os.path.join(self.tmp, "pack")
        shutil.copytree(HERMES, self.pack, ignore=shutil.ignore_patterns("__pycache__"))
        self.home = os.path.join(self.tmp, "home")
        for sub in ("profiles/claude-hermes", "profiles/developer", "profiles/librarian", "profiles/research", "profiles/writer"):
            os.makedirs(os.path.join(self.home, sub))
        self.old = "OLD ROLE TEXT\n"
        for rel in ("SOUL.md", "profiles/claude-hermes/SOUL.md", "profiles/developer/SOUL.md", "profiles/librarian/SOUL.md", "profiles/research/SOUL.md", "profiles/writer/SOUL.md"):
            with open(os.path.join(self.home, rel), "w") as handle:
                handle.write(self.old)
        self.backup = os.path.join(self.tmp, "backup")

    def run_script(self, *args):
        done = subprocess.run(["bash", os.path.join(self.pack, "deploy_roles.sh"), "--home", self.home, *args], capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_the_script_has_valid_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(HERMES, "deploy_roles.sh")]).returncode, 0)

    def test_the_default_is_a_dry_run_that_writes_nothing(self):
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("6 file(s) would change", out)
        with open(os.path.join(self.home, "SOUL.md")) as handle:
            self.assertEqual(handle.read(), self.old)
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_backs_up_installs_reads_back_and_a_second_apply_changes_nothing(self):
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertIn("read-back OK", out)
        for name, rel in (("main", "SOUL.md"), ("librarian", "profiles/librarian/SOUL.md"), ("writer", "profiles/writer/SOUL.md")):
            with open(os.path.join(self.home, rel)) as got, open(os.path.join(HERMES, "roles", name, "SOUL.md")) as want:
                self.assertEqual(got.read(), want.read(), name)
            self.assertEqual(os.stat(os.path.join(self.home, rel)).st_mode & 0o777, 0o600, name)
            with open(os.path.join(self.backup, name + ".SOUL.md")) as old:
                self.assertEqual(old.read(), self.old, name)
        self.assertIn("ROLLBACK librarian", out)
        status, again = self.run_script("--apply", "--backup", os.path.join(self.tmp, "backup2"))
        self.assertEqual(status, 0, again)
        self.assertIn("0 file(s) would change", again)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "backup2")))

    def test_output_never_contains_role_text(self):
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertNotIn("LIBRARIAN role", out)
        self.assertNotIn("OLD ROLE TEXT", out)

    def test_a_role_file_that_looks_like_a_secret_or_is_too_big_is_refused_before_anything_is_written(self):
        with open(os.path.join(self.pack, "roles", "writer", "SOUL.md"), "a") as handle:
            handle.write("password: hunter2hunter2\n")
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 1, out)
        self.assertIn("REFUSED: writer looks like it contains a secret", out)
        with open(os.path.join(self.home, "SOUL.md")) as handle:
            self.assertEqual(handle.read(), self.old)
        with open(os.path.join(self.pack, "roles", "writer", "SOUL.md"), "w") as handle:
            handle.write("x" * 5000)
        status, out = self.run_script()
        self.assertEqual(status, 1, out)
        self.assertIn("over 4096 bytes", out)

    def test_a_missing_repo_file_is_refused(self):
        os.remove(os.path.join(self.pack, "roles", "research", "SOUL.md"))
        status, out = self.run_script()
        self.assertEqual(status, 1, out)
        self.assertIn("missing repo file for research", out)


class Canon(unittest.TestCase):
    def test_the_canon_document_states_the_homes_and_the_rules(self):
        with open(os.path.join(ROOT, "docs", "hermes-files.md")) as handle:
            text = handle.read()
        for needle in ("hermes/roles/", "Hermes Wiki", "never in this repo", "remote client only", "connections", "hold.py", "deploy_roles.sh", "Drive"):
            self.assertIn(needle.lower(), text.lower(), needle)


if __name__ == "__main__":
    unittest.main()
