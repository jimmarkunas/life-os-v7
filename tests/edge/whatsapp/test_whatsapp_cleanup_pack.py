"""LIFE-643 cleanup pack, run on a fixture (fake systemctl for both managers, a real Unix socket answering like Attention, fake old and new folders under a temp dir, through the script's environment hooks). It proves: the gates
stop it before anything changes, the consumer files are installed and a bad one is put back, the old copies are removed and the new session is untouched, a second run changes nothing, --dry-run changes nothing, a link is never
followed, and a copy of the script without the gates deletes what the real one keeps (fail-then-pass). The pack builds and passes the runner's own check."""
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from lifeos.edge.runner import runner

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "lifeos" / "edge" / "whatsapp" / "pack_cleanup"
SCRIPT = PACK / "pack" / "files" / "cleanup.sh"
SECRET = "NOT-A-REAL-SESSION-SECRET"
SNAPSHOT = {"channel": "whatsapp", "health": "CURRENT", "observed_at": "2026-11-13T15:00:00Z", "chats_known": 80, "unread_messages": 12, "unread_messages_unmuted": 9,
            "unread_chats": 5, "unread_chats_unmuted": 4, "latest_state": "NONE", "latest_name": None}
FAKE_SYSTEMCTL = """#!/bin/bash
S="$CL_FAKE/$CL_WHO"; mkdir -p "$S"; echo "$CL_WHO $*" >> "$CL_FAKE/calls.log"
case "$1" in
  is-active) [ -e "$S/active-$2" ] && echo active || { echo inactive; exit 3; } ;;
  is-enabled) [ -e "$S/enabled-$2" ] && echo enabled || { echo disabled; exit 1; } ;;
  show) [ "$2 $3" = "-p User" ] && cat "$S/user-$5" 2>/dev/null ;;
esac
exit 0
"""
LABELS = ("old_session", "old_key_folder", "old_program", "old_unit_file", "pre_slim_tree", "rows34_mode_list")


class Fixture:
    def __init__(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="cl", dir="/tmp")))
        self.root, self.fake, self.files = self.tmp / "root", self.tmp / "fake", self.tmp / "files"
        self.sock_path = self.root / "run/lifeos-wa/attention.sock"
        self.server = None
        for name in ("system", "user"):
            path = self.tmp / ("systemctl-" + name)
            path.write_text(FAKE_SYSTEMCTL.replace("$CL_WHO", name))
            path.chmod(0o755)
        (self.tmp / "node").write_text("#!/bin/sh\nexit 0\n")
        (self.tmp / "node").chmod(0o755)
        (self.tmp / "as-ubuntu").write_text('#!/bin/sh\nexec "$@"\n')
        (self.tmp / "as-ubuntu").chmod(0o755)
        self.env = dict(os.environ, CL_ROOT=str(self.root), CL_SYSTEMCTL=str(self.tmp / "systemctl-system"), CL_USER_SYSTEMCTL=str(self.tmp / "systemctl-user"), CL_FAKE=str(self.fake),
                        CL_NODE=str(self.tmp / "node"), CL_AS_UBUNTU=str(self.tmp / "as-ubuntu"), CL_FILES=str(self.files), CL_PY=sys.executable)
        self.reset()

    def reset(self, good_files=True):
        import shutil
        self.stop_server()
        for path in (self.root, self.fake, self.files):
            shutil.rmtree(path, ignore_errors=True)
        uh = self.root / "home/ubuntu"
        for rel in (".local/state/lifeos-whatsapp/auth", ".config/lifeos-whatsapp", ".local/lib/lifeos-whatsapp/node_modules"):
            (uh / rel).mkdir(parents=True)
        (uh / ".local/state/lifeos-whatsapp/auth/creds.json").write_text(SECRET)
        (uh / ".config/lifeos-whatsapp/hash-key").write_text(SECRET)
        (uh / ".local/lib/lifeos-whatsapp/node_modules/x.js").write_text("x")
        (uh / ".config/systemd/user").mkdir(parents=True)
        (uh / ".config/systemd/user/lifeos-whatsapp-attention.service").write_text("[Unit]\n")
        for rel, text in ((".local/lib/lifeos-imessage/alerts_endpoint.py", "OLD ENDPOINT\n"), (".local/lib/lifeos-oracle/stats.mjs", "OLD STATS\n")):
            (uh / rel).parent.mkdir(parents=True, exist_ok=True)
            (uh / rel).write_text(text)
        (self.root / "opt/lifeos-wa/app/node_modules.pre-slim").mkdir(parents=True)
        (self.root / "opt/lifeos-wa/app/node_modules.pre-slim/y.js").write_text("y")
        (self.root / "var/lib/lifeos-runner").mkdir(parents=True)
        (self.root / "var/lib/lifeos-runner/whatsapp-modes-LIFE-643.list").write_text("names\n")
        new = self.root / "var/lib/lifeos-wa/.local/state/lifeos-whatsapp/auth"
        new.mkdir(parents=True)
        (new / "creds.json").write_text(SECRET)
        (self.fake / "system").mkdir(parents=True)
        (self.fake / "user").mkdir(parents=True)
        svc = "lifeos-wa-attention.service"
        (self.fake / "system" / ("active-" + svc)).touch()
        (self.fake / "system" / ("enabled-" + svc)).touch()
        (self.fake / "system" / ("user-" + svc)).write_text("lifeos-wa\n")
        self.files.mkdir(parents=True)
        for name, src in (("alerts_endpoint.py", ROOT / "lifeos/imessage/alerts_endpoint.py"), ("stats.mjs", ROOT / "lifeos/oraclecloud/oracle/stats.mjs")):
            (self.files / name).write_text(src.read_text())
        self.start_server()

    def start_server(self):
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.sock_path))
        self.server.listen(8)

        def loop(server):
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                conn.sendall((json.dumps(SNAPSHOT) + "\n").encode())
                conn.close()
        threading.Thread(target=loop, args=(self.server,), daemon=True).start()

    def stop_server(self):
        if self.server is not None:
            try:
                self.server.shutdown(socket.SHUT_RDWR)             # wakes the accept loop on Linux, where close() alone leaves the listener open
            except OSError:
                pass
            self.server.close()
            self.server = None
            try:
                self.sock_path.unlink()
            except OSError:
                pass

    def listing(self):
        return sorted(str(p.relative_to(self.tmp)) for p in self.root.rglob("*") if not p.is_socket())

    def home(self, rel):
        return self.root / "home/ubuntu" / rel


def run(script, fx, *args):
    return subprocess.run(["bash", str(script), *args], env=fx.env, capture_output=True, text=True, timeout=120)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.stop_server)

    def test_a_good_run_removes_the_old_copies_installs_the_consumers_and_leaves_the_new_session_untouched(self):
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 0, done.stdout)
        for label in LABELS:
            self.assertRegex(done.stdout, r"READBACK %s=removed_entries_\d+" % label)
        self.assertIn("READBACK gates=new_service=active_enabled_lifeos-wa health=CURRENT old_unit=not_running", done.stdout)
        self.assertIn("READBACK consumers=installed endpoint_health=CURRENT", done.stdout)
        self.assertIn("READBACK result=removed_6 failed_0 dry_run=no", done.stdout)
        for rel in (".local/state/lifeos-whatsapp", ".config/lifeos-whatsapp", ".local/lib/lifeos-whatsapp", ".config/systemd/user/lifeos-whatsapp-attention.service"):
            self.assertFalse(self.fx.home(rel).exists(), rel)
        self.assertFalse((self.fx.root / "opt/lifeos-wa/app/node_modules.pre-slim").exists())
        self.assertEqual((self.fx.root / "var/lib/lifeos-wa/.local/state/lifeos-whatsapp/auth/creds.json").read_text(), SECRET)
        for name, rel in (("alerts_endpoint.py", ".local/lib/lifeos-imessage/alerts_endpoint.py"), ("stats.mjs", ".local/lib/lifeos-oracle/stats.mjs")):
            self.assertEqual(self.fx.home(rel).read_text(), (self.fx.files / name).read_text())
            self.assertEqual(stat.S_IMODE(self.fx.home(rel).stat().st_mode), 0o600)
        self.assertNotIn(SECRET, done.stdout + done.stderr)
        self.assertIn("user daemon-reload", (self.fx.fake / "calls.log").read_text())

    def test_a_second_run_changes_nothing_and_says_skipped(self):
        run(SCRIPT, self.fx)
        after = self.fx.listing()
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(self.fx.listing(), after)
        for label in LABELS:
            self.assertIn("READBACK %s=skipped" % label, done.stdout)

    def test_dry_run_changes_nothing(self):
        before = self.fx.listing()
        done = run(SCRIPT, self.fx, "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(self.fx.listing(), before)
        self.assertEqual(self.fx.home(".local/lib/lifeos-imessage/alerts_endpoint.py").read_text(), "OLD ENDPOINT\n")
        self.assertIn("READBACK old_session=would_remove_entries_", done.stdout)
        self.assertIn("READBACK consumers=would_install", done.stdout)

    def test_each_gate_stops_it_with_one_fixed_word_and_nothing_changes(self):
        cases = {
            "new_service_not_active": lambda fx: (fx.fake / "system/active-lifeos-wa-attention.service").unlink(),
            "new_service_not_enabled": lambda fx: (fx.fake / "system/enabled-lifeos-wa-attention.service").unlink(),
            "new_service_wrong_user": lambda fx: (fx.fake / "system/user-lifeos-wa-attention.service").write_text("ubuntu\\n"),
            "new_service_health_none": lambda fx: fx.stop_server(),
            "new_session_missing": lambda fx: __import__("shutil").rmtree(fx.root / "var/lib/lifeos-wa/.local/state/lifeos-whatsapp/auth"),
            "old_unit_running": lambda fx: (fx.fake / "user/active-lifeos-whatsapp-attention.service").touch(),
        }
        for word, change in cases.items():
            with self.subTest(word):
                self.fx.reset()
                change(self.fx)
                before = self.fx.listing()
                done = run(SCRIPT, self.fx)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("READBACK failed=" + word, done.stdout)
                self.assertEqual(self.fx.listing(), before)
                self.assertEqual(self.fx.home(".local/lib/lifeos-imessage/alerts_endpoint.py").read_text(), "OLD ENDPOINT\n")

    def test_a_consumer_file_that_does_not_parse_is_put_back_and_nothing_old_is_deleted(self):
        (self.fx.files / "alerts_endpoint.py").write_text("def broken(:\n")
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("READBACK failed=endpoint_syntax", done.stdout)
        self.assertEqual(self.fx.home(".local/lib/lifeos-imessage/alerts_endpoint.py").read_text(), "OLD ENDPOINT\n")
        self.assertEqual(self.fx.home(".local/lib/lifeos-oracle/stats.mjs").read_text(), "OLD STATS\n")
        self.assertTrue(self.fx.home(".local/state/lifeos-whatsapp/auth/creds.json").exists())

    def test_a_link_is_refused_and_never_followed(self):
        target = self.fx.tmp / "precious"
        target.mkdir()
        (target / "keep.txt").write_text("keep")
        import shutil
        shutil.rmtree(self.fx.home(".local/lib/lifeos-whatsapp"))
        self.fx.home(".local/lib/lifeos-whatsapp").symlink_to(target)
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("READBACK old_program=refused_link", done.stdout)
        self.assertEqual((target / "keep.txt").read_text(), "keep")

    def test_a_copy_without_the_gates_deletes_what_the_real_script_keeps(self):                              # fail-then-pass
        (self.fx.fake / "system/active-lifeos-wa-attention.service").unlink()
        run(SCRIPT, self.fx)
        self.assertTrue(self.fx.home(".local/state/lifeos-whatsapp/auth/creds.json").exists())            # real script: kept
        text = SCRIPT.read_text()
        self.assertIn("|| fail new_service_not_active", text)
        mutated = self.fx.tmp / "mutated.sh"
        mutated.write_text(text.replace("|| fail new_service_not_active", "|| true"))
        run(mutated, self.fx)
        self.assertFalse(self.fx.home(".local/state/lifeos-whatsapp").exists())                           # without the gate: gone

    def test_the_pack_builds_and_passes_the_runners_own_check_and_restores_only_the_two_consumer_files(self):
        out = self.fx.tmp / "pack"
        subprocess.run([sys.executable, str(PACK / "build_pack.py"), str(out)], cwd=ROOT, check=True, capture_output=True)
        manifest = runner.load_manifest(str(out))
        self.assertEqual((manifest["steps"], manifest["services"]), (["steps/10-cleanup.sh"], []))
        self.assertEqual(manifest["files"], ["/home/ubuntu/.local/lib/lifeos-imessage/alerts_endpoint.py", "/home/ubuntu/.local/lib/lifeos-oracle/stats.mjs"])
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("PACK ok LIFE-643 steps=1 restore_files=2 restore_services=0", done.stdout)


if __name__ == "__main__":
    unittest.main()
