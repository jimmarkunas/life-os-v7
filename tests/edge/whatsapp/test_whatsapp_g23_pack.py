"""LIFE-647 groups 2+3 pack, run on a fixture (a fake user systemctl that counts restarts, a real Unix socket answering like Attention, fake host files under a temp dir, through the script's environment hooks).
It proves: a good run installs only what matches its pin with the owner-mode kept and restarts the one unit once; a host file that differs stops it before anything changes; a file that fails its check, or a unit that
does not stay up, puts every old file back; a second run and --dry-run change nothing; a copy of the script without the pin check overwrites what the real one refuses (fail-then-pass); the pack builds and passes the runner's check."""
import hashlib
import json
import os
import shutil
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
PACK = ROOT / "lifeos" / "edge" / "whatsapp" / "pack_g23"
SCRIPT = PACK / "pack" / "steps" / "10-install.sh"
SNAPSHOT = {"channel": "whatsapp", "health": "CURRENT", "observed_at": "2026-11-13T15:00:00Z", "chats_known": 80, "unread_messages": 12, "unread_messages_unmuted": 9,
            "unread_chats": 5, "unread_chats_unmuted": 4, "latest_state": "NONE", "latest_name": None}
FAKE_USYS = """#!/bin/bash
S="$G_FAKE"; echo "$*" >> "$S/calls.log"
case "$1" in
  is-active) [ -e "$S/active" ] && echo active || { echo inactive; exit 3; } ;;
  restart) n=$(cat "$S/pid"); echo $((n + 1)) > "$S/pid"; [ ! -e "$S/restart-fails" ] || exit 1 ;;
  show) case "$3" in MainPID) cat "$S/pid"; [ ! -e "$S/loop" ] || echo $(( $(cat "$S/pid") + 1 )) > "$S/pid" ;; NRestarts) echo 0 ;; esac ;;
esac
exit 0
"""
SRC = {"alerts_endpoint.py": "lifeos/imessage/alerts_endpoint.py", "openclaw_imessage.py": "lifeos/imessage/openclaw_imessage.py", "attention.py": "lifeos/telegram/attention.py",
       "forward_alerts.py": "lifeos/whatsapp/oracle/forward_alerts.py"}
HOST = {"alerts_endpoint.py": ".local/lib/lifeos-imessage/alerts_endpoint.py", "openclaw_imessage.py": ".local/bin/openclaw-imessage",
        "attention.py": ".local/lib/lifeos-telegram/attention.py", "forward_alerts.py": ".local/lib/lifeos-alerts/forward_alerts.py"}
MODES = {"alerts_endpoint.py": 0o600, "openclaw_imessage.py": 0o755, "attention.py": 0o700, "forward_alerts.py": 0o640}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Fixture:
    def __init__(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="g23", dir="/tmp")))
        self.root, self.fake, self.files = self.tmp / "root", self.tmp / "fake", self.tmp / "files"
        self.sock_path = self.root / "run/lifeos-wa/attention.sock"
        self.server = None
        (self.tmp / "usys").write_text(FAKE_USYS)
        (self.tmp / "as-ubuntu").write_text('#!/bin/sh\nexec "$@"\n')
        for name in ("usys", "as-ubuntu"):
            (self.tmp / name).chmod(0o755)
        self.env = dict(os.environ, G_ROOT=str(self.root), G_USYS=str(self.tmp / "usys"), G_FAKE=str(self.fake), G_AS_UBUNTU=str(self.tmp / "as-ubuntu"), G_PY=sys.executable,
                        G_SLEEP="true", PACK_DIR=str(self.tmp))
        self.reset()

    def reset(self):
        self.stop_server()
        for path in (self.root, self.fake, self.files):
            shutil.rmtree(path, ignore_errors=True)
        self.fake.mkdir(parents=True)
        (self.fake / "active").touch()
        (self.fake / "pid").write_text("100\n")
        self.files.mkdir(parents=True)
        rows = []
        for name, rel in SRC.items():
            new = (ROOT / rel).read_text()
            (self.files / name).write_text(new)
            old = "# old %s\n" % name
            path = self.home(HOST[name])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(old)
            path.chmod(MODES[name])
            rows.append("%s /%s %s %s" % (name, "home/ubuntu/" + HOST[name], sha(old), sha(new)))
        (self.files / "table").write_text("\n".join(rows) + "\n")
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
                self.server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.server.close()
            self.server = None
            try:
                self.sock_path.unlink()
            except OSError:
                pass

    def home(self, rel):
        return self.root / "home/ubuntu" / rel

    def snapshot(self):
        return {name: (self.home(rel).read_text(), stat.S_IMODE(self.home(rel).stat().st_mode)) for name, rel in HOST.items()}

    def restarts(self):
        return (self.fake / "calls.log").read_text().count("restart ") if (self.fake / "calls.log").exists() else 0


def run(script, fx, *args):
    return subprocess.run(["bash", str(script), *args], env=fx.env, capture_output=True, text=True, timeout=120)


class G23Tests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.stop_server)

    def test_a_good_run_installs_all_four_keeps_each_mode_and_restarts_the_one_unit_once(self):
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("READBACK gates=to_install=4 already_new=0 restart_forward=yes", done.stdout)
        self.assertIn("READBACK result=installed_4 already_new_0", done.stdout)
        for name, rel in HOST.items():
            self.assertEqual(self.fx.home(rel).read_text(), (self.fx.files / name).read_text())
            self.assertEqual(stat.S_IMODE(self.fx.home(rel).stat().st_mode), MODES[name])
        self.assertEqual(self.fx.restarts(), 1)

    def test_only_the_files_that_still_match_their_pin_change_and_no_restart_without_the_forward_file(self):
        new = (self.fx.files / "forward_alerts.py").read_text()
        self.fx.home(HOST["forward_alerts.py"]).write_text(new)                                    # already the new one
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("READBACK gates=to_install=3 already_new=1 restart_forward=no", done.stdout)
        self.assertEqual(self.fx.restarts(), 0)

    def test_a_host_file_that_differs_stops_it_before_anything_changes(self):
        for name in HOST:
            with self.subTest(name):
                self.fx.reset()
                self.fx.home(HOST[name]).write_text("someone edited this\n")
                before = self.fx.snapshot()
                done = run(SCRIPT, self.fx)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("READBACK failed=%s_host_differs" % name, done.stdout)
                self.assertEqual(self.fx.snapshot(), before)
                self.assertEqual(self.fx.restarts(), 0)

    def test_a_link_and_an_inactive_unit_stop_it_with_one_word(self):
        path = self.fx.home(HOST["attention.py"])
        path.unlink()
        path.symlink_to(self.fx.home(HOST["forward_alerts.py"]))
        done = run(SCRIPT, self.fx)
        self.assertIn("READBACK failed=attention.py_not_a_plain_file", done.stdout)
        self.fx.reset()
        (self.fx.fake / "active").unlink()
        before = self.fx.snapshot()
        done = run(SCRIPT, self.fx)
        self.assertIn("READBACK failed=forward_unit_not_active", done.stdout)
        self.assertEqual(self.fx.snapshot(), before)

    def test_a_file_that_fails_its_check_puts_every_old_file_back(self):
        for name, word in (("forward_alerts.py", "forward_alerts.py_syntax"), ("alerts_endpoint.py", "alerts_endpoint.py_syntax")):
            with self.subTest(name):
                self.fx.reset()
                rows = (self.fx.files / "table").read_text().splitlines()
                bad = "def broken(:\n"
                (self.fx.files / name).write_text(bad)
                (self.fx.files / "table").write_text("\n".join(r if not r.startswith(name) else " ".join(r.split()[:3] + [sha(bad)]) for r in rows) + "\n")
                before = self.fx.snapshot()
                done = run(SCRIPT, self.fx)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("READBACK failed=" + word, done.stdout)
                self.assertEqual(self.fx.snapshot(), before)
                self.assertEqual(self.fx.restarts(), 0)

    def test_a_wrapper_that_does_not_print_usage_and_an_unhealthy_endpoint_roll_back(self):
        (self.fx.files / "openclaw_imessage.py").write_text("print('hello')\n")
        rows = [r if not r.startswith("openclaw") else " ".join(r.split()[:3] + [sha("print('hello')\n")]) for r in (self.fx.files / "table").read_text().splitlines()]
        (self.fx.files / "table").write_text("\n".join(rows) + "\n")
        before = self.fx.snapshot()
        done = run(SCRIPT, self.fx)
        self.assertIn("READBACK failed=wrapper_usage", done.stdout)
        self.assertEqual(self.fx.snapshot(), before)
        self.fx.reset()
        self.fx.stop_server()
        done = run(SCRIPT, self.fx)
        self.assertIn("READBACK failed=endpoint_health_", done.stdout)
        self.assertEqual(self.fx.snapshot(), {n: (f"# old {n}\n", MODES[n]) for n in HOST})

    def test_a_unit_that_keeps_restarting_puts_the_old_files_back_and_restarts_it_again(self):
        (self.fx.fake / "loop").touch()
        before = self.fx.snapshot()
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("READBACK failed=forward_unit_unstable", done.stdout)
        self.assertEqual({n: v[0] for n, v in self.fx.snapshot().items()}, {n: f"# old {n}\n" for n in HOST})
        self.assertEqual(self.fx.snapshot(), before)
        self.assertEqual(self.fx.restarts(), 2)

    def test_a_second_run_and_dry_run_change_nothing(self):
        before = self.fx.snapshot()
        done = run(SCRIPT, self.fx, "--dry-run")
        self.assertIn("READBACK dry_run=no_change", done.stdout)
        self.assertEqual((self.fx.snapshot(), self.fx.restarts()), (before, 0))
        run(SCRIPT, self.fx)
        after = self.fx.snapshot()
        done = run(SCRIPT, self.fx)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("READBACK result=nothing_to_do", done.stdout)
        self.assertEqual((self.fx.snapshot(), self.fx.restarts()), (after, 1))

    def test_a_copy_without_the_pin_check_overwrites_what_the_real_script_refuses(self):                       # fail-then-pass
        self.fx.home(HOST["attention.py"]).write_text("someone edited this\n")
        run(SCRIPT, self.fx)
        self.assertEqual(self.fx.home(HOST["attention.py"]).read_text(), "someone edited this\n")             # real script: kept
        text = SCRIPT.read_text()
        self.assertIn('fail "${name}_host_differs"', text)
        mutated = self.fx.tmp / "mutated.sh"
        mutated.write_text(text.replace('else fail "${name}_host_differs"; fi', 'else todo+=("$name"); fi'))
        run(mutated, self.fx)
        self.assertEqual(self.fx.home(HOST["attention.py"]).read_text(), (self.fx.files / "attention.py").read_text())      # without the pin: overwritten

    def test_the_pack_builds_and_passes_the_runners_own_check_with_all_four_files_in_the_restore_list(self):
        out = self.fx.tmp / "pack"
        subprocess.run([sys.executable, str(PACK / "build_pack.py"), str(out), "--old", "HEAD"], cwd=ROOT, check=True, capture_output=True)
        manifest = runner.load_manifest(str(out))
        self.assertEqual((manifest["steps"], manifest["services"]), (["steps/10-install.sh"], []))
        self.assertEqual(sorted(manifest["files"]), sorted("/home/ubuntu/" + rel for rel in HOST.values()))
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("PACK ok LIFE-647 steps=1 restore_files=4 restore_services=0", done.stdout)


if __name__ == "__main__":
    unittest.main()
