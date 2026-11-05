"""LIFE-318 (Jim, Nov 5 2026: "Hermes backup: add to nightly"): the Hermes files in the nightly off-site backup (LIFE-129)."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lifeos.edge.backup import offsite_backup as ob
from tests.edge.backup.test_offsite_backup import local_run, make_db

HERMES_NAMES = [n for n, *_ in ob.SQLITE + ob.FILES if n.startswith("hermes/")] + [p for p, _ in ob.TREES if p.startswith("hermes/")]
NEVER = (".env", "auth", "mcp-tokens", "cred", "token", "secret", "installs", "tools", "cache", "logs", "sessions", "hold.py", "last_seen", "key")


class Lists(unittest.TestCase):
    def test_the_hermes_entries_are_listed_under_one_prefix_and_one_owner(self):
        self.assertGreaterEqual(len(HERMES_NAMES), 20)
        for name, source, owner, _required in [e for e in ob.SQLITE if e[0].startswith("hermes/")]:
            self.assertEqual(owner, "hermes", name)
            self.assertTrue(source.startswith(ob.HERMES + "/"), name)
            self.assertTrue(name.endswith(".db"), name)
        for name, source, _required in [e for e in ob.FILES if e[0].startswith("hermes/")]:
            self.assertTrue(source.startswith(ob.HERMES + "/"), name)
            self.assertTrue(name.endswith(("config.yaml", "SOUL.md")), name)
        for prefix, root in [e for e in ob.TREES if e[0].startswith("hermes/")]:
            self.assertTrue(root.startswith(ob.HERMES + "/") and root.endswith("/memories"), prefix)

    def test_no_hermes_entry_is_secret_shaped_and_none_names_a_credential_store_or_the_program(self):
        for name, source, *_ in [e for e in ob.SQLITE + ob.FILES if e[0].startswith("hermes/")]:
            self.assertFalse(ob.denied(name, source), name)
            for word in NEVER:
                self.assertNotIn(word, name.lower() + " " + source.lower().replace("/var/lib/hermeseval/home/.hermes", ""), (name, word))
        for prefix, root in [e for e in ob.TREES if e[0].startswith("hermes/")]:
            self.assertFalse(ob.denied(prefix, root), prefix)

    def test_a_hermes_problem_can_never_stop_the_other_copies(self):
        for entry in [e for e in ob.SQLITE if e[0].startswith("hermes/")]:
            self.assertFalse(entry[3], entry[0])
        for entry in [e for e in ob.FILES if e[0].startswith("hermes/")]:
            self.assertFalse(entry[2], entry[0])

    def test_the_main_history_database_and_the_settings_and_role_memory_are_listed(self):
        for wanted in ("hermes/state.db", "hermes/config.yaml", "hermes/profiles/librarian/memories", "hermes/profiles/librarian/state.db", "hermes/SOUL.md"):
            self.assertIn(wanted, HERMES_NAMES)


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = Path(self.tmp) / "hermes"
        self.work = Path(self.tmp) / "work"
        self.work.mkdir()
        h = self.home
        (h / "profiles" / "librarian" / "memories").mkdir(parents=True)
        (h / "memories").mkdir()
        make_db(h / "state.db", rows=5).close()
        make_db(h / "profiles" / "librarian" / "state.db", rows=2).close()
        (h / "config.yaml").write_text("model:\n  default: x\n")
        (h / "SOUL.md").write_text("soul\n")
        (h / "profiles" / "librarian" / "SOUL.md").write_text("librarian soul\n")
        (h / "profiles" / "librarian" / "memories" / "USER.md").write_text("synthetic note\n")
        # things that must never travel:
        (h / ".env").write_text("PROVIDER_KEY=not-real\n")
        (h / "auth.json").write_text("{}")
        (h / "mcp-tokens").mkdir()
        (h / "mcp-tokens" / "x.json").write_text("{}")
        (h / "installs").mkdir()
        (h / "installs" / "big.bin").write_text("x")
        (h / "logs").mkdir()
        (h / "logs" / "agent.log").write_text("log")
        (h / "sessions").mkdir()
        (h / "sessions" / "s.json").write_text("{}")
        (h / "memories" / "my-token.txt").write_text("a secret-shaped name inside a listed folder")

    def remap(self, entries):
        out = []
        for e in entries:
            if not e[0].startswith("hermes/"):
                continue
            moved = list(e)
            idx = 1
            moved[idx] = e[idx].replace(ob.HERMES, str(self.home))
            if len(e) == 4:
                moved[2] = "me"                      # the test's own user stands in for `hermes`
            out.append(tuple(moved))
        return tuple(out)

    def collect(self):
        return ob.collect(str(self.work), local_run, sqlite=self.remap(ob.SQLITE), files=self.remap(ob.FILES), trees=self.remap(ob.TREES), secret_trees=(), chown=lambda *a, **k: None)

    def test_only_the_listed_hermes_files_are_collected_and_nothing_secret_or_bulky(self):
        entries, extra = self.collect()
        self.assertEqual(sorted(entries), ["hermes/SOUL.md", "hermes/config.yaml", "hermes/profiles/librarian/SOUL.md", "hermes/profiles/librarian/memories/USER.md",
                                           "hermes/profiles/librarian/state.db", "hermes/state.db"])
        self.assertEqual(extra["skipped"], 1)                                   # the secret-shaped file inside a listed folder
        for banned in (".env", "auth", "mcp-tokens", "installs", "logs", "sessions", "token"):
            self.assertFalse([n for n in entries if banned in n], banned)

    def test_the_database_copies_are_consistent_snapshots_with_the_same_rows(self):
        entries, _ = self.collect()
        copy = sqlite3.connect(entries["hermes/state.db"])
        self.assertEqual(copy.execute("select count(*) from t").fetchone(), (5,))
        self.assertEqual(copy.execute("pragma integrity_check").fetchone(), ("ok",))
        copy.close()
        self.assertNotEqual(entries["hermes/state.db"], str(self.home / "state.db"))     # a copy, never the live file

    def test_a_missing_hermes_source_is_counted_and_never_stops_the_run(self):
        os.unlink(self.home / "state.db")
        os.unlink(self.home / "config.yaml")
        entries, extra = self.collect()
        self.assertNotIn("hermes/state.db", entries)
        self.assertGreaterEqual(extra["missing"], 2)
        self.assertIn("hermes/profiles/librarian/state.db", entries)


class Docs(unittest.TestCase):
    def test_the_readme_and_the_canon_say_hermes_is_in_the_nightly_backup(self):
        root = Path(__file__).resolve().parents[3]
        self.assertIn("Hermes", (root / "lifeos" / "edge" / "backup" / "README.md").read_text())
        self.assertIn("off-site backup", (root / "docs" / "hermes-files.md").read_text().lower())


if __name__ == "__main__":
    unittest.main()
