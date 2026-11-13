"""LIFE-643 rows 3 and 4: the WhatsApp session-modes and unit-hardening pack, built from the repo and run through the REAL runner on a temp tree with a fake `systemctl --user`, fake `ss` and a real Unix socket (no sudo, no host, no session file)."""
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
from tests.edge.runner.test_runner import FakeSystemd

ROOT = Path(__file__).resolve().parents[3]
BUILDER = ROOT / "lifeos" / "edge" / "whatsapp" / "build_pack.py"
WA_HOST = ROOT / "lifeos" / "edge" / "whatsapp" / "pack" / "files" / "wa_host.py"
OLD_UNIT = """# COMM-1.3 (LIFE-30): Jim's personal WhatsApp attention as one dedicated linked device. Counts and the latest sender in memory; answers only on a
# 0600 Unix socket. No output (stdout/stderr discarded), memory-capped, restarts after a crash, never re-pairs by itself.
[Unit]
Description=LIFE OS WhatsApp attention (dedicated linked device; counts only)
After=network-online.target

[Service]
WorkingDirectory=%h/.local/lib/lifeos-whatsapp
ExecStart=%h/.openclaw/tools/node/bin/node %h/.local/lib/lifeos-whatsapp/attention.mjs serve
Restart=on-failure
RestartSec=30
UMask=0077
MemoryMax=400M
NoNewPrivileges=yes
StandardOutput=null
StandardError=null

[Install]
WantedBy=default.target
"""
OLD_UNIT_SHA = "2ed076ee246fc8296ad00d32711b05a7b52c3d7cfa956e411ee88628f2863a77"
FAKE_SYSTEMCTL = r"""#!/bin/bash
# fake `systemctl --user`: `restart` of the attention unit records which unit text is now running and applies the failure flags (files in the fake root)
P="$(cd "$(dirname "$0")/../.." && pwd)"
echo "$@" >> "$P/calls.log"
[ "$1" = --user ] && shift
UNITF="$P/home/ubuntu/.config/systemd/user/lifeos-whatsapp-attention.service"
SOCK="$P/home/ubuntu/.local/state/lifeos-whatsapp/attention.sock"
new() { grep -q '^RestrictNamespaces=yes' "$P/running-unit" 2>/dev/null; }
case "$1" in
  restart)
    cp "$UNITF" "$P/running-unit"
    if new; then
      if [ -e "$P/interrupt" ]; then kill -TERM "$(ps -o ppid= -p "$PPID" | tr -d ' ')"; sleep 20; fi
      [ -e "$P/fail-restart" ] && exit 1
      [ -e "$P/no-socket" ] && mv "$SOCK" "$SOCK.away"
      [ -e "$P/bad-mode" ] && chmod 660 "$SOCK"
    else
      [ -e "$SOCK.away" ] && mv "$SOCK.away" "$SOCK"
      chmod 600 "$SOCK"
    fi
    exit 0 ;;
  is-active)
    if [ "$2" = lifeos-whatsapp-attention.service ] && new && [ -e "$P/dies" ]; then echo inactive; exit 3; fi
    if [ "$2" = openclaw-gateway.service ] && [ -e "$P/gateway-down" ]; then echo inactive; exit 3; fi
    echo active ;;
  show)
    case "$3" in
      MainPID) echo 4242 ;;
      NRestarts) if new && [ -e "$P/crashloop" ]; then echo 3; else echo 0; fi ;;
      *) echo value ;;
    esac ;;
  *) exit 0 ;;
esac
"""
FAKE_SS = r"""#!/bin/bash
P="$(cd "$(dirname "$0")/../.." && pwd)"
if grep -q '^RestrictNamespaces=yes' "$P/running-unit" 2>/dev/null && [ -e "$P/no-connection" ]; then exit 0; fi
echo 'ESTAB 0 0 10.0.0.2:40000 10.0.0.9:443 users:(("node",pid=4242,fd=21))'
"""


class Server(threading.Thread):
    """Stands in for the attention process: answers one line (counts only) per connection unless the `silent` flag exists."""
    def __init__(self, path, silent):
        super().__init__(daemon=True)
        self.sock, self.silent = socket.socket(socket.AF_UNIX), silent
        self.sock.bind(path)
        os.chmod(path, 0o600)
        self.sock.listen(5)

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                if not os.path.exists(self.silent):
                    conn.sendall(b'{"version":1,"unread":0}\n')


def modes(tree):
    out = {}
    for base, dirs, files in os.walk(tree):
        for path in [base] + [os.path.join(base, name) for name in files]:
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
                key = ("d" if stat.S_ISDIR(info.st_mode) else "f", stat.S_IMODE(info.st_mode))
                out[key] = out.get(key, 0) + 1
    return out


class PackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))               # short path: Unix socket names are limited to ~100 characters
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.prefix = self.tmp / "r"
        home = self.prefix / "home" / "ubuntu"
        self.state_dir = home / ".local" / "state" / "lifeos-whatsapp"
        self.cred_dir = home / ".openclaw" / "credentials" / "whatsapp"
        self.unit_path = home / ".config" / "systemd" / "user" / "lifeos-whatsapp-attention.service"
        (self.state_dir / "auth").mkdir(parents=True)
        self.cred_dir.mkdir(parents=True)
        self.unit_path.parent.mkdir(parents=True)
        self.unit_path.write_text(OLD_UNIT)
        for tree, names in ((self.state_dir / "auth", ("creds-a.json", "creds-b.json", "pre-key-1.json")), (self.cred_dir, ("creds.json", "app-state-1.json"))):
            for name in names:
                (tree / name).write_text("NOT-A-REAL-SESSION-SECRET-%s" % name)
                (tree / name).chmod(0o664)
        (self.state_dir / "chats.json").write_text("{}")
        (self.state_dir / "chats.json").chmod(0o600)
        for folder in (self.state_dir, self.state_dir / "auth", self.cred_dir):
            folder.chmod(0o775)
        self.server = Server(str(self.state_dir / "attention.sock"), str(self.prefix / "silent"))
        self.server.start()
        self.addCleanup(self.server.sock.close)
        (self.prefix / "proc" / "4242").mkdir(parents=True)
        bindir = self.prefix / "usr" / "bin"
        bindir.mkdir(parents=True)
        for name, body in (("systemctl", FAKE_SYSTEMCTL), ("ss", FAKE_SS), ("sleep", "#!/bin/bash\nexit 0\n")):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        (bindir / "python3").symlink_to(sys.executable)
        self.pack = self.tmp / "pack"
        self.build(self.pack)
        self.state, self.inbox = str(self.tmp / "state"), str(self.tmp / "lifeos-pack-{key}")
        shutil.copytree(self.pack, self.inbox.format(key="LIFE-643"))
        self.host = runner.Host(state=self.state, inbox=self.inbox, require_root=False, systemctl=FakeSystemd(str(self.tmp)))
        self.sha8 = runner.pack_hash(self.inbox.format(key="LIFE-643"))[:8]

    def build(self, out):
        subprocess.run([sys.executable, str(BUILDER), str(out), "--root-prefix", str(self.prefix)], cwd=ROOT, check=True, capture_output=True)

    def flag(self, name):
        (self.prefix / name).touch()

    def run_pack(self):
        return runner.execute("LIFE-643", self.sha8, self.host)

    def both(self):
        return modes(self.state_dir), modes(self.cred_dir)

    def running_unit(self):
        return (self.prefix / "running-unit").read_text()

    def calls(self):
        return (self.prefix / "calls.log").read_text().splitlines() if (self.prefix / "calls.log").exists() else []

    def test_the_old_unit_fixture_is_the_one_the_pack_expects_on_the_host(self):
        self.assertEqual(hashlib.sha256(OLD_UNIT.encode()).hexdigest(), OLD_UNIT_SHA)
        self.assertIn(OLD_UNIT_SHA, (ROOT / "lifeos/edge/whatsapp/pack/steps/10-preflight.sh").read_text())

    def test_the_pack_is_valid_and_declares_the_installed_unit_and_the_service(self):
        manifest = runner.load_manifest(str(self.pack))
        self.assertEqual((manifest["ticket"], manifest["services"]), ("LIFE-643", ["lifeos-whatsapp-attention.service"]))
        self.assertEqual(manifest["files"], [str(self.unit_path)])
        built = (self.pack / "files" / "lifeos-whatsapp-attention.service").read_text()
        self.assertEqual(built, (ROOT / "lifeos/whatsapp/oracle/lifeos-whatsapp-attention.service").read_text())

    def test_the_hardened_unit_keeps_what_was_there_and_adds_the_sandbox_lines(self):
        lines = (ROOT / "lifeos/whatsapp/oracle/lifeos-whatsapp-attention.service").read_text().splitlines()
        for want in ("NoNewPrivileges=yes", "MemoryMax=400M", "UMask=0077", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "RestrictNamespaces=yes",
                     "LockPersonality=yes", "SystemCallFilter=@system-service", "SystemCallErrorNumber=EPERM", "RestrictSUIDSGID=yes"):
            self.assertIn(want, lines)
        for name in ("ProtectSystem", "PrivateTmp", "ProtectKernelTunables", "ProtectControlGroups"):       # need a mount namespace: impossible for this user manager on Oracle
            self.assertFalse([line for line in lines if line.startswith(name)], name)

    def test_a_good_run_tightens_both_trees_hardens_the_unit_and_reads_back(self):
        before = self.both()
        self.assertEqual(before[1], {("d", 0o775): 1, ("f", 0o664): 2})
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        self.assertEqual(self.both(), ({("d", 0o700): 2, ("f", 0o600): 4}, {("d", 0o700): 1, ("f", 0o600): 2}))
        self.assertEqual(self.unit_path.read_text(), (ROOT / "lifeos/whatsapp/oracle/lifeos-whatsapp-attention.service").read_text())
        self.assertEqual(self.running_unit(), self.unit_path.read_text())
        readback = " | ".join(result["readback"])
        for want in ("before_state d775=2 f600=1 f664=3", "before_credentials d775=1 f664=2", "after_state d700=2 f600=4", "after_credentials d700=1 f600=2", "other_user_holders=0", "applied=yes", "attention_active=active", "gateway_active=active",
                     "socket_mode=600", "attention_whatsapp_connections=1", "NRestarts=0", "RestrictNamespaces=value"):
            self.assertIn(want, readback)
        listing = self.prefix / "var" / "lib" / "lifeos-runner" / "whatsapp-modes-LIFE-643.list"
        self.assertEqual(stat.S_IMODE(listing.stat().st_mode), 0o600)
        self.assertEqual(len(listing.read_text().splitlines()), 9)           # names and modes only
        for place in (listing.read_text(), json.dumps(result)):
            self.assertNotIn("NOT-A-REAL-SESSION-SECRET", place)

    def test_only_the_attention_user_service_is_restarted_and_nothing_else_is_touched(self):
        self.run_pack()
        restarts = [c for c in self.calls() if " restart " in " %s " % c]
        self.assertEqual(restarts, ["--user restart lifeos-whatsapp-attention.service"])
        text = "".join(p.read_text() for p in (self.pack / "steps").glob("*.sh"))
        self.assertNotIn("restart \"$GW\"", text)
        self.assertNotIn("alerts-whatsapp", text)

    def assert_everything_back(self, result, code="E_STEP_2"):
        self.assertEqual((result["outcome"], result["code"]), ("fail", code), result)
        self.assertEqual(self.unit_path.read_text(), OLD_UNIT)
        self.assertEqual(self.running_unit(), OLD_UNIT)                       # the service was restarted on the old unit
        self.assertEqual(self.both(), ({("d", 0o775): 2, ("f", 0o664): 3, ("f", 0o600): 1}, {("d", 0o775): 1, ("f", 0o664): 2}))
        self.assertEqual(stat.S_IMODE((self.state_dir / "attention.sock").stat().st_mode), 0o600)

    def failure(self, flag):
        self.flag(flag)
        self.assert_everything_back(self.run_pack())

    def test_a_failed_restart_puts_the_old_unit_and_every_mode_back(self):
        self.failure("fail-restart")

    def test_a_missing_socket_puts_everything_back(self):
        self.failure("no-socket")

    def test_a_socket_that_is_not_mode_600_puts_everything_back(self):
        self.failure("bad-mode")

    def test_a_socket_that_does_not_answer_puts_everything_back(self):
        self.failure("silent")

    def test_a_service_that_dies_after_starting_puts_everything_back(self):
        self.failure("dies")

    def test_a_crash_loop_puts_everything_back(self):
        self.failure("crashloop")

    def test_no_connection_to_whatsapp_under_the_new_filter_puts_everything_back(self):
        self.failure("no-connection")

    def test_a_gateway_that_is_down_stops_before_anything_changes(self):
        self.flag("gateway-down")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=not_active_openclaw-gateway", " ".join(result["readback"]))
        self.assertEqual(modes(self.cred_dir), {("d", 0o775): 1, ("f", 0o664): 2})
        self.assertEqual(self.unit_path.read_text(), OLD_UNIT)

    def test_an_unexpected_installed_unit_stops_before_anything_changes(self):
        self.unit_path.write_text(OLD_UNIT + "# edited by hand\n")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=unit_unexpected", " ".join(result["readback"]))
        self.assertEqual(modes(self.cred_dir), {("d", 0o775): 1, ("f", 0o664): 2})

    def test_a_link_in_a_session_tree_stops_before_anything_changes(self):
        (self.cred_dir / "link").symlink_to("creds.json")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=tree_symlink", " ".join(result["readback"]))
        self.assertEqual(modes(self.cred_dir).get(("f", 0o664)), 2)

    def test_an_interrupted_run_restores_the_unit_file_and_leaves_the_safe_tight_modes(self):
        """The runner kills a step's process group with SIGKILL, so a step cannot undo its own chmod on Ctrl-C; owner-only is the safe end state and the list stays for a manual put-back."""
        self.flag("interrupt")
        driver = ("import sys; from lifeos.edge.runner import runner; "
                  "h = runner.Host(state=sys.argv[1], inbox=sys.argv[2], require_root=False, systemctl=lambda *a: (0, 'active')); "
                  "sys.exit(runner.main(sys.argv[3:], h))")
        done = subprocess.run([sys.executable, "-c", driver, self.state, self.inbox, "LIFE-643", self.sha8], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "RESULT fail E_INTERRUPTED\n", ""))
        self.assertEqual(self.unit_path.read_text(), OLD_UNIT)
        self.assertEqual(modes(self.cred_dir), {("d", 0o700): 1, ("f", 0o600): 2})
        self.assertTrue((self.prefix / "var" / "lib" / "lifeos-runner" / "whatsapp-modes-LIFE-643.list").exists())


class HelperTests(unittest.TestCase):
    """wa_host.py checks that a pack-level test cannot stage (a second user needs root)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "tree").mkdir()
        (self.tmp / "tree" / "a").write_text("x")

    def wa(self, *args):
        done = subprocess.run([sys.executable, "-I", str(WA_HOST), *args], capture_output=True, text=True)
        return done.returncode, done.stdout.strip()

    def test_a_tree_owned_by_someone_else_is_refused(self):
        self.assertEqual(self.wa("check", str(os.getuid()), str(self.tmp / "tree")), (0, ""))
        self.assertEqual(self.wa("check", str(os.getuid() + 1), str(self.tmp / "tree")), (1, "owner"))

    def test_a_process_of_another_user_holding_the_tree_is_counted(self):
        proc = self.tmp / "proc" / "77" / "fd"
        proc.mkdir(parents=True)
        (proc / "3").symlink_to(self.tmp / "tree" / "a")
        self.assertEqual(self.wa("holders", str(self.tmp / "proc"), str(os.getuid()), str(self.tmp / "tree")), (0, "0"))
        self.assertEqual(self.wa("holders", str(self.tmp / "proc"), str(os.getuid() + 1), str(self.tmp / "tree")), (0, "1"))

    def test_the_socket_is_counted_never_printed(self):
        path = str(self.tmp / "s")
        server = Server(path, str(self.tmp / "silent"))
        server.start()
        self.addCleanup(server.sock.close)
        self.assertEqual(self.wa("connect", path), (0, str(len(b'{"version":1,"unread":0}\n'))))


if __name__ == "__main__":
    unittest.main()
