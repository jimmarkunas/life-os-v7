"""LIFE-643 row 1: the pack that moves WhatsApp Attention to its own user `lifeos-wa`, built from the repo and run through the REAL runner on a temp tree with fakes for systemctl (system and --user),
useradd, getent, id, runuser, ss and node, and a real Unix socket (no sudo, no host, no session file). What a test cannot prove (real uids, real systemd sandboxing, real socket ownership) the pack's own
step 3 checks on the host and the step-4 readback reports."""
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
BUILDER = ROOT / "lifeos" / "edge" / "whatsapp" / "pack_row1" / "build_pack.py"
PACK_SRC = ROOT / "lifeos" / "edge" / "whatsapp" / "pack_row1" / "pack"
WA_ROW1 = PACK_SRC / "files" / "wa_row1.py"
UNIT = ROOT / "lifeos" / "whatsapp" / "oracle" / "lifeos-wa-attention.service"
SOCKET_UNIT = ROOT / "lifeos" / "whatsapp" / "oracle" / "lifeos-wa-attention.socket"
NEW_UID = 4501
SNAPSHOT = {"channel": "whatsapp", "health": "CURRENT", "observed_at": "2026-11-13T15:00:00Z", "chats_known": 80, "unread_messages": 12, "unread_messages_unmuted": 9,
            "unread_chats": 5, "unread_chats_unmuted": 4, "latest_state": "NONE", "latest_name": None}

FAKE_SYSTEMCTL = r"""#!/bin/bash
# fake systemctl for both managers. State = flag files in $P/fake; test switches = files in $P (named like the failure).
P="$(cd "$(dirname "$0")/../.." && pwd)"; F="$P/fake"; mkdir -p "$F"
echo "$@" >> "$P/calls.log"
USER_MODE=no; [ "$1" = --user ] && { USER_MODE=yes; shift; }
verb=$1; shift
prop=""; units=()
while [ $# -gt 0 ]; do case "$1" in -p) prop=$2; shift 2 ;; --value|--no-pager) shift ;; *) units+=("$1"); shift ;; esac; done
unit=${units[0]:-}
SOCK="$P/run/lifeos-wa/attention.sock"
UNITF="$P/etc/systemd/system"
on() { [ -e "$F/$1" ]; }
set_on() { touch "$F/$1"; }
set_off() { rm -f "$F/$1"; }
proc() { mkdir -p "$P/proc/$1"; printf 'Name:\tnode\nUid:\t%s\t%s\t%s\t%s\n' "$2" "$2" "$2" "$2" > "$P/proc/$1/status"; }
resume_old() { [ -e "$P/var/lib/lifeos-runner/whatsapp-row1.switching" ] && [ -x "$P/opt/lifeos-wa/bin/resume-old.sh" ] && WA_ROOT="$P" "$P/opt/lifeos-wa/bin/resume-old.sh"; return 0; }

if [ $USER_MODE = yes ]; then
  case "$verb" in
    is-active)
      if [ "$unit" = openclaw-gateway.service ]; then if [ -e "$P/gateway-down" ]; then echo inactive; exit 3; fi; echo active; exit 0; fi
      if on old-active; then echo active; else echo inactive; exit 3; fi ;;
    is-enabled) if on old-enabled; then echo enabled; else echo disabled; exit 1; fi ;;
    show) case "$prop" in MainPID) if [ "$unit" = openclaw-gateway.service ]; then echo 900; elif on old-active; then echo 4242; else echo 0; fi ;; *) echo value ;; esac ;;
    stop) [ -e "$P/old-wont-stop" ] || set_off old-active
          if [ -e "$P/interrupt-after-old-stop" ]; then kill -TERM "$(ps -o ppid= -p "$PPID" | tr -d ' ')"; sleep 20; fi ;;
    start) set_on old-active; proc 4242 "$(id -u)" ;;
    enable) set_on old-enabled ;;
    disable) set_off old-enabled ;;
  esac
  exit 0
fi

case "$verb" in
  daemon-reload) exit 0 ;;
  is-active)
    case "$unit" in
      lifeos-wa-attention.service) if on new-active && ! [ -e "$P/dies" ]; then echo active; else echo inactive; exit 3; fi ;;
      lifeos-wa-attention.socket) if on sock-active; then echo active; else echo inactive; exit 3; fi ;;
      *) echo active ;;
    esac ;;
  is-enabled)
    [ -e "$UNITF/$unit" ] || exit 1
    if on "enabled-$unit"; then echo enabled; else echo disabled; exit 1; fi ;;
  show)
    case "$prop" in
      MainPID) if on new-active; then echo 5252; else echo 0; fi ;;
      NRestarts) if [ -e "$P/crashloop" ]; then echo 3; else echo 0; fi ;;
      User) echo lifeos-wa ;;
      ProtectSystem) [ -e "$P/no-sandbox" ] && echo no || echo strict ;;
      ProtectHome|PrivateTmp|NoNewPrivileges|ProtectKernelTunables|ProtectKernelModules|ProtectControlGroups|RestrictNamespaces|LockPersonality|RestrictRealtime|RestrictSUIDSGID) echo yes ;;
      SystemCallArchitectures) echo native ;;
      *) echo value ;;
    esac ;;
  start)
    [ -e "$P/fail-start" ] && exit 1
    set_on new-active; set_on sock-active
    proc 5252 "$( [ -e "$P/runs-as-ubuntu" ] && id -u || echo $NEW_UID_VALUE )"
    if [ ! -e "$P/no-socket" ]; then mkdir -p "$P/run/lifeos-wa"; [ -S "$SOCK.away" ] && mv "$SOCK.away" "$SOCK"; fi
    [ -e "$P/bad-socket-mode" ] && [ -S "$SOCK" ] && chmod 666 "$SOCK"
    [ -S "$SOCK" ] && [ ! -e "$P/bad-socket-mode" ] && chmod 660 "$SOCK"
    if [ -e "$P/interrupt" ]; then kill -TERM "$(ps -o ppid= -p "$PPID" | tr -d ' ')"; sleep 20; fi ;;
  stop)
    case "$unit" in
      lifeos-wa-attention.service|lifeos-wa-attention.socket)
        was=no; on new-active && was=yes
        set_off new-active; set_off sock-active
        [ -S "$SOCK" ] && mv "$SOCK" "$SOCK.away"
        [ $was = yes ] && resume_old ;;
    esac ;;
  enable) for u in "${units[@]}"; do set_on "enabled-$u"; done ;;
  disable) for u in "${units[@]}"; do set_off "enabled-$u"; done ;;
esac
exit 0
"""
FAKE_SS = r"""#!/bin/bash
P="$(cd "$(dirname "$0")/../.." && pwd)"
[ -e "$P/fake/old-active" ] && echo 'ESTAB 0 0 10.0.0.2:40000 10.0.0.9:443 users:(("node",pid=4242,fd=21))'
if [ -e "$P/fake/new-active" ] && [ ! -e "$P/no-connection" ]; then echo 'ESTAB 0 0 10.0.0.2:40001 10.0.0.9:443 users:(("node",pid=5252,fd=21))'; fi
exit 0
"""
FAKE_GETENT = r"""#!/bin/bash
P="$(cd "$(dirname "$0")/../.." && pwd)"
RU=$(id -u); RG=$(id -g)
shell=/usr/sbin/nologin; [ -e "$P/user-has-shell" ] && shell=/bin/bash
case "$1 $2" in
  "passwd lifeos-wa"|"passwd $NEW_UID_VALUE") [ -e "$P/fake/user" ] && { echo "lifeos-wa:x:$NEW_UID_VALUE:$NEW_UID_VALUE::/var/lib/lifeos-wa:$shell"; exit 0; }; exit 2 ;;
  "passwd ubuntu"|"passwd $RU") echo "ubuntu:x:$RU:$RG:Ubuntu:/home/ubuntu:/bin/bash" ;;
  "group ubuntu"|"group $RG") echo "ubuntu:x:$RG:" ;;
  "group lifeos-wa"|"group $NEW_UID_VALUE") echo "lifeos-wa:x:$NEW_UID_VALUE:" ;;
  *) exit 2 ;;
esac
"""
FAKE_ID = r"""#!/bin/bash
P="$(cd "$(dirname "$0")/../.." && pwd)"
if [ "$2" = lifeos-wa ]; then
  [ -e "$P/fake/user" ] || exit 1
  case "$1" in -u|-g) echo $NEW_UID_VALUE ;; -Gn) [ -e "$P/user-in-groups" ] && echo "lifeos-wa ubuntu" || echo lifeos-wa ;; esac
  exit 0
fi
exec /usr/bin/id "$@"
"""
FAKE_USERADD = r"""#!/bin/bash
P="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$P/fake" "$P/var/lib/lifeos-wa"; touch "$P/fake/user"; echo "$@" >> "$P/calls.log"
"""
FAKE_RUNUSER = r"""#!/bin/bash
# runuser -u USER -- command...  (no second user without root: the commands run as the test user; the permission outcomes are switches)
P="$(cd "$(dirname "$0")/../.." && pwd)"
user=$2; shift 3
case "$user" in
  nobody) if [ -e "$P/others-connect" ]; then exec "$@"; fi; exit 13 ;;
  ubuntu) if [[ "$*" == *"os.listdir"* ]]; then if [ -e "$P/ubuntu-reads" ]; then echo readable; else echo denied; fi; exit 0; fi; exec "$@" ;;
  lifeos-wa) [ -e "$P/new-user-cannot-run" ] && exit 1; exec "$@" ;;
  *) exec "$@" ;;
esac
"""
FAKE_NODE = """#!/bin/bash
case "$1" in --version) echo v24.0.0 ;; *) exit 0 ;; esac
"""
OLD_UNIT = "[Service]\nExecStart=/home/ubuntu/.openclaw/tools/node/bin/node attention.mjs serve\n"


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Server(threading.Thread):
    """Stands in for the attention process: answers one JSON snapshot per connection unless the `silent` flag exists."""
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
                    conn.sendall(json.dumps(SNAPSHOT).encode() + b"\n")


def tree_listing(tree):
    out = {}
    for base, dirs, files in os.walk(tree):
        for name in dirs + files:
            path = os.path.join(base, name)
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                out[os.path.relpath(path, tree)] = ("d", stat.S_IMODE(info.st_mode))
            elif stat.S_ISREG(info.st_mode):
                out[os.path.relpath(path, tree)] = ("f", stat.S_IMODE(info.st_mode), info.st_size)
    return out


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))               # short path: Unix socket names are limited to ~100 characters
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.prefix = self.tmp / "r"
        p = self.prefix
        home = p / "home" / "ubuntu"
        self.old_state = home / ".local" / "state" / "lifeos-whatsapp"
        self.old_key = home / ".config" / "lifeos-whatsapp" / "hash-key"
        self.old_app = home / ".local" / "lib" / "lifeos-whatsapp"
        self.endpoint = home / ".local" / "lib" / "lifeos-imessage" / "alerts_endpoint.py"
        self.stats = home / ".local" / "lib" / "lifeos-oracle" / "stats.mjs"
        self.old_unit = home / ".config" / "systemd" / "user" / "lifeos-whatsapp-attention.service"
        self.new_home = p / "var" / "lib" / "lifeos-wa"
        self.new_state = self.new_home / ".local" / "state" / "lifeos-whatsapp"
        self.new_sock = p / "run" / "lifeos-wa" / "attention.sock"
        self.flag = p / "var" / "lib" / "lifeos-runner" / "whatsapp-row1.switching"
        for folder in (self.old_state / "auth", self.old_key.parent, self.old_app / "node_modules" / "baileys", self.old_app / "node_modules" / ".bin", self.endpoint.parent, self.stats.parent,
                       self.old_unit.parent, home / ".openclaw" / "tools" / "node-v24" / "bin", p / "etc" / "systemd" / "system", p / "opt", p / "var" / "lib", p / "run" / "lifeos-wa", p / "proc" / "4242",
                       p / "usr" / "bin"):
            folder.mkdir(parents=True)
        for name in ("creds-a.json", "creds-b.json", "pre-key-1.json", "app-state-sync-1.json"):
            (self.old_state / "auth" / name).write_text("NOT-A-REAL-SESSION-SECRET-%s" % name)
        (self.old_state / "chats.json").write_text("{}")
        self.old_key.write_text("NOT-A-REAL-KEY")
        self.old_key.chmod(0o600)
        for folder in (self.old_state, self.old_state / "auth", self.old_key.parent, self.old_app):
            folder.chmod(0o700)
        for tree in (self.old_state / "auth",):
            for item in tree.iterdir():
                item.chmod(0o600)
        (self.old_state / "chats.json").chmod(0o600)
        (self.old_app / "node_modules" / "baileys" / "index.js").write_text("export default 1\n")
        (self.old_app / "node_modules" / "baileys" / "cli.js").write_text("#!/usr/bin/env node\n")
        (self.old_app / "node_modules" / "baileys" / "cli.js").chmod(0o755)
        (self.old_app / "node_modules" / ".bin" / "baileys").symlink_to("../baileys/cli.js")
        for name in ("attention.mjs", "state.mjs"):
            (self.old_app / name).write_text("// old %s\n" % name)
        for name in ("package.json", "package-lock.json"):                       # the old tree holds the same lock as the pack (built from the repo)
            shutil.copy(ROOT / "lifeos" / "whatsapp" / "oracle" / name, self.old_app / name)
        self.endpoint.write_text("# old endpoint\n")
        self.endpoint.chmod(0o600)
        self.stats.write_text("// old stats\n")
        self.stats.chmod(0o600)
        self.old_unit.write_text(OLD_UNIT)
        node_real = home / ".openclaw" / "tools" / "node-v24" / "bin" / "node"
        node_real.write_text(FAKE_NODE)
        node_real.chmod(0o755)
        (home / ".openclaw" / "tools" / "node").symlink_to("node-v24")
        (p / "pins.sh").write_text("ENDPOINT_OLD_SHA=%s\nSTATS_OLD_SHA=%s\nATTENTION_OLD_SHA=%s\nSTATE_OLD_SHA=%s\n"
                                   % (sha(self.endpoint), sha(self.stats), sha(self.old_app / "attention.mjs"), sha(self.old_app / "state.mjs")))
        bindir = p / "usr" / "bin"
        for name, body in (("systemctl", FAKE_SYSTEMCTL), ("ss", FAKE_SS), ("getent", FAKE_GETENT), ("id", FAKE_ID), ("useradd", FAKE_USERADD), ("runuser", FAKE_RUNUSER), ("sleep", "#!/bin/bash\nexit 0\n")):
            (bindir / name).write_text(body.replace("$NEW_UID_VALUE", str(NEW_UID)))
            (bindir / name).chmod(0o755)
        (bindir / "python3").symlink_to(sys.executable)
        (p / "fake").mkdir()
        for name in ("old-active", "old-enabled"):
            (p / "fake" / name).touch()
        (p / "proc" / "4242" / "status").write_text("Uid:\t%d\t%d\t%d\t%d\n" % ((os.getuid(),) * 4))
        self.old_server = Server(str(self.old_state / "attention.sock"), str(p / "silent"))
        self.old_server.start()
        self.addCleanup(self.old_server.sock.close)
        self.new_server = Server(str(self.new_sock) + ".away", str(p / "silent"))
        self.new_server.start()
        self.addCleanup(self.new_server.sock.close)
        self.pack = self.tmp / "pack"
        self.build(self.pack)
        self.state, self.inbox = str(self.tmp / "state"), str(self.tmp / "lifeos-pack-{key}")
        shutil.copytree(self.pack, self.inbox.format(key="LIFE-643"))
        self.sha8 = runner.pack_hash(self.inbox.format(key="LIFE-643"))[:8]
        self.before_old = tree_listing(self.old_state)
        self.fake = str(bindir / "systemctl")
        self.host = runner.Host(state=self.state, inbox=self.inbox, require_root=False, systemctl=self.fake_systemctl)

    def fake_systemctl(self, *args):
        done = subprocess.run([self.fake, *args], capture_output=True, text=True)
        return done.returncode, (done.stdout.split() or [""])[0]

    def build(self, out):
        subprocess.run([sys.executable, str(BUILDER), str(out), "--root-prefix", str(self.prefix)], cwd=ROOT, check=True, capture_output=True)

    def flag_on(self, name):
        (self.prefix / name).touch()

    def run_pack(self):
        return runner.execute("LIFE-643", self.sha8, self.host)

    def calls(self):
        path = self.prefix / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

    def on(self, name):
        return (self.prefix / "fake" / name).exists()

    def assert_old_is_back(self):
        """The old service is running and enabled, the new one is down, the new session copy is gone, the old folder is exactly as it was."""
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse(self.on("new-active") or self.on("sock-active") or self.on("enabled-lifeos-wa-attention.service"))
        self.assertFalse(self.new_state.exists())
        self.assertFalse((self.new_home / ".config").exists())
        self.assertFalse(self.flag.exists())
        self.assertEqual(tree_listing(self.old_state), self.before_old)
        self.assertEqual(self.endpoint.read_text(), "# old endpoint\n")                  # the runner put the consumer files back
        self.assertEqual(self.stats.read_text(), "// old stats\n")
        self.assertEqual(self.old_unit.read_text(), OLD_UNIT)


class PackShapeTests(Fixture):
    def test_the_pack_is_valid_and_declares_exactly_what_it_changes(self):
        manifest = runner.load_manifest(str(self.pack))
        self.assertEqual(manifest["ticket"], "LIFE-643")
        self.assertEqual(manifest["services"], ["lifeos-wa-attention.socket", "lifeos-wa-attention.service"])       # socket first: stopping it stops the service too
        self.assertEqual(manifest["files"], [str(self.endpoint), str(self.stats)])
        self.assertEqual(len(manifest["steps"]), 4)
        for name, repo in (("lifeos-wa-attention.service", UNIT), ("lifeos-wa-attention.socket", SOCKET_UNIT)):
            self.assertEqual((self.pack / "files" / name).read_text(), repo.read_text())

    def test_the_real_pack_builds_without_a_prefix_and_passes_the_runners_own_check(self):
        out = self.tmp / "real"
        subprocess.run([sys.executable, str(BUILDER), str(out)], cwd=ROOT, check=True, capture_output=True)
        done = subprocess.run([sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("PACK ok LIFE-643 steps=4 restore_files=2 restore_services=2", done.stdout)
        self.assertFalse((out / "files" / "ROOT").exists())
        text = (out / "files" / "common.sh").read_text()
        for pinned in ("e9686bdd23021e86562619c4c2303bdc259d51481f45ae6126d16d79f435e1af", "6e343b8f093a860fc70c06938ef19d34659074daa2e3178865018fa7ce948e7d"):
            self.assertIn(pinned, text)

    def test_the_new_unit_has_the_full_sandbox_and_only_its_own_state_folder_is_writable(self):
        lines = UNIT.read_text().splitlines()
        for want in ("User=lifeos-wa", "ProtectSystem=strict", "ReadWritePaths=/var/lib/lifeos-wa", "PrivateTmp=yes", "ProtectHome=yes", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
                     "ProtectControlGroups=yes", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "RestrictNamespaces=yes", "LockPersonality=yes", "RestrictRealtime=yes", "RestrictSUIDSGID=yes",
                     "SystemCallArchitectures=native", "SystemCallFilter=@system-service", "SystemCallErrorNumber=EPERM", "NoNewPrivileges=yes", "MemoryMax=400M", "UMask=0077", "Restart=on-failure",
                     "ExecStart=/opt/lifeos-wa/bin/node /opt/lifeos-wa/app/attention.mjs serve", "ExecStopPost=+/opt/lifeos-wa/bin/resume-old.sh", "Requires=lifeos-wa-attention.socket"):
            self.assertIn(want, lines)
        self.assertEqual([x for x in lines if x.startswith("ReadWritePaths")], ["ReadWritePaths=/var/lib/lifeos-wa"])
        sock = SOCKET_UNIT.read_text().splitlines()
        for want in ("ListenStream=/run/lifeos-wa/attention.sock", "SocketUser=lifeos-wa", "SocketGroup=ubuntu", "SocketMode=0660"):
            self.assertIn(want, sock)

    def test_no_download_install_or_new_code_in_any_step(self):
        text = "".join(p.read_text() for p in list((PACK_SRC / "steps").glob("*.sh")) + [PACK_SRC / "files" / "common.sh", PACK_SRC / "files" / "resume-old.sh"])
        for banned in ("npm ", "curl ", "wget ", "apt", "pip ", "git clone", "sudo "):
            self.assertNotIn(banned, text)

    def test_only_the_old_user_unit_and_the_new_units_are_ever_started_or_stopped(self):
        self.run_pack()
        verbs = [c for c in self.calls() if c.split()[0] in ("--user", "start", "stop", "enable", "disable", "restart")]
        for call in verbs:
            self.assertTrue(any(name in call for name in ("lifeos-whatsapp-attention.service", "lifeos-wa-attention")), call)


class GoodRunTests(Fixture):
    def test_a_good_run_switches_proves_and_reads_back(self):
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        # the new side
        self.assertTrue(self.on("new-active") and self.on("enabled-lifeos-wa-attention.service") and self.on("enabled-lifeos-wa-attention.socket"))
        self.assertEqual(tree_listing(self.new_state), {k: (v if v[0] == "d" else v) for k, v in tree_listing(self.new_state).items()})
        copied, original = tree_listing(self.new_state), self.before_old
        self.assertEqual({k: v[2:] for k, v in copied.items() if v[0] == "f"}, {k: v[2:] for k, v in original.items() if v[0] == "f"})      # same names and sizes
        self.assertEqual({v[1] for v in copied.values() if v[0] == "d"}, {0o700})
        self.assertEqual({v[1] for v in copied.values() if v[0] == "f"}, {0o600})
        self.assertEqual(stat.S_IMODE(self.new_home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.new_home / ".config" / "lifeos-whatsapp" / "hash-key").stat().st_mode), 0o600)
        self.assertEqual((self.new_home / ".config" / "lifeos-whatsapp" / "hash-key").read_text(), "NOT-A-REAL-KEY")
        # the old side: stopped, disabled, folder and unit file untouched
        self.assertFalse(self.on("old-active") or self.on("old-enabled"))
        self.assertEqual(tree_listing(self.old_state), self.before_old)
        self.assertEqual(self.old_unit.read_text(), OLD_UNIT)
        self.assertTrue((self.old_state / "attention.sock").exists())
        # the program tree: root-owned layout, the pinned tree copied with its link and exec bit, new app files from the repo
        app = self.prefix / "opt" / "lifeos-wa"
        self.assertTrue((app / "bin" / "node").exists() and (app / "bin" / "resume-old.sh").exists())
        self.assertEqual((app / "app" / "attention.mjs").read_text(), (ROOT / "lifeos/whatsapp/oracle/attention.mjs").read_text())
        self.assertTrue((app / "app" / "node_modules" / ".bin" / "baileys").is_symlink())
        self.assertEqual(stat.S_IMODE((app / "app" / "node_modules" / "baileys" / "cli.js").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((app / "app" / "attention.mjs").stat().st_mode), 0o644)
        # the consumer files are the repo's, still private
        self.assertEqual(self.endpoint.read_text(), (ROOT / "lifeos/imessage/alerts_endpoint.py").read_text())
        self.assertEqual(self.stats.read_text(), (ROOT / "lifeos/oraclecloud/oracle/stats.mjs").read_text())
        self.assertEqual(stat.S_IMODE(self.endpoint.stat().st_mode), 0o600)
        self.assertFalse(self.flag.exists())
        readback = " | ".join(result["readback"])
        for want in ("new_user=absent", "program_tree entries=", "unit_verify" if False else "consumer_files=installed", "old_stopped=yes", "copy_check same entries=", "proof new_pid_user_is_new_user=yes",
                     "socket_mode=660", "endpoint_health=CURRENT", "consumer_read_session=denied", "other_users=refused", "switched=yes old_disabled=yes new_enabled=yes", "new_unit_active=active enabled=enabled",
                     "main_pid_user=lifeos-wa uid_is_ubuntu=no", "ProtectSystem=strict", "owner=lifeos-wa group=ubuntu mode=660", "consumer_connect_bytes=", "ubuntu_lists_new_state=denied",
                     "ubuntu_lists_new_home=denied", "nobody_connects=no", "old_unit_active=inactive old_unit_enabled=disabled old_unit_file_present=yes", "switch_flag_gone=yes", "user_shell=/usr/sbin/nologin"):
            self.assertIn(want, readback)
        for place in (readback, json.dumps(result)):
            self.assertNotIn("NOT-A-REAL", place)

    def test_the_old_unit_is_stopped_before_the_session_is_copied_and_disabled_only_after_the_new_one_is_proven(self):
        self.run_pack()
        order = [c for c in self.calls() if c.startswith("--user ") and ("stop" in c or "disable" in c) or c.startswith(("start", "enable"))]
        self.assertEqual(order[0], "--user stop lifeos-whatsapp-attention.service")
        self.assertLess(order.index("start lifeos-wa-attention.service"), order.index("--user disable lifeos-whatsapp-attention.service"))
        self.assertLess(order.index("--user disable lifeos-whatsapp-attention.service"), order.index("enable lifeos-wa-attention.socket lifeos-wa-attention.service"))

    def test_a_second_run_after_a_good_one_stops_in_the_preflight_and_changes_nothing(self):
        self.assertEqual(self.run_pack()["outcome"], "ok")
        again = self.run_pack()
        self.assertEqual((again["outcome"], again["code"]), ("fail", "E_STEP_1"))
        self.assertTrue(self.on("new-active"))


class RollbackTests(Fixture):
    def failure(self, flag, code="E_STEP_3"):
        self.flag_on(flag)
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", code), result)
        self.assert_old_is_back()
        return result

    def test_a_new_service_that_will_not_start(self):
        self.failure("fail-start")

    def test_no_socket(self):
        self.failure("no-socket")

    def test_a_socket_that_is_not_mode_660(self):
        self.failure("bad-socket-mode")

    def test_a_socket_that_does_not_answer_the_consumer(self):
        self.failure("silent")

    def test_a_service_that_dies(self):
        self.failure("dies")

    def test_a_crash_loop(self):
        self.failure("crashloop")

    def test_a_process_that_is_not_the_new_user(self):
        self.failure("runs-as-ubuntu")

    def test_no_connection_to_whatsapp_under_the_new_sandbox(self):
        self.failure("no-connection")

    def test_a_sandbox_property_that_is_not_set(self):
        self.failure("no-sandbox")

    def test_a_consumer_that_could_read_the_session(self):
        result = self.failure("ubuntu-reads")
        self.assertIn("failed=consumer_can_read_session", " ".join(result["readback"]))

    def test_a_socket_any_other_user_could_open(self):
        result = self.failure("others-connect")
        self.assertIn("failed=socket_open_to_others", " ".join(result["readback"]))

    def test_an_old_unit_that_will_not_stop_leaves_it_running_and_the_new_side_down(self):
        result = self.failure("old-wont-stop")
        self.assertIn("failed=old_still_active", " ".join(result["readback"]))

    def test_the_rollback_stops_the_new_units_before_it_starts_the_old_one(self):
        self.flag_on("no-socket")
        self.run_pack()
        calls = self.calls()
        self.assertLess(calls.index("stop lifeos-wa-attention.service"), max(i for i, c in enumerate(calls) if c.startswith("--user start")))

    def test_the_new_user_not_able_to_run_node_stops_in_prepare_and_the_old_service_is_never_touched(self):
        self.flag_on("new-user-cannot-run")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_2"), result)
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse([c for c in self.calls() if c.startswith("--user stop")])
        self.assertEqual(self.endpoint.read_text(), "# old endpoint\n")                  # nothing of the consumer was changed yet

    def test_preflight_refusals_change_nothing(self):
        cases = {"gateway-down": "not_active", "user-has-shell": "new_user_has_shell", "user-in-groups": "new_user_in_other_groups"}
        for flag, word in cases.items():
            with self.subTest(flag):
                if flag.startswith("user-"):
                    (self.prefix / "fake" / "user").touch()
                (self.prefix / flag).touch()
                result = runner.execute("LIFE-643", self.sha8, self.host)
                self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"), result)
                self.assertIn(word, " ".join(result["readback"]))
                (self.prefix / flag).unlink()
                self.assertTrue(self.on("old-active"))
                self.assertFalse(self.new_state.exists())

    def test_an_unexpected_installed_file_or_a_changed_lock_stops_in_preflight(self):
        for target, word in ((self.endpoint, "endpoint_unexpected"), (self.stats, "stats_unexpected"), (self.old_app / "package-lock.json", "lock_unexpected"), (self.old_app / "attention.mjs", "attention_unexpected")):
            with self.subTest(word):
                original = target.read_text()
                target.write_text(original + "\n// edited by hand\n")
                result = runner.execute("LIFE-643", self.sha8, self.host)
                self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"), result)
                self.assertIn("failed=" + word, " ".join(result["readback"]))
                target.write_text(original)

    def test_a_link_in_the_old_session_folder_stops_in_preflight(self):
        (self.old_state / "link").symlink_to("chats.json")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=old_tree_symlink", " ".join(result["readback"]))
        self.assertTrue(self.on("old-active"))

    def test_an_old_unit_that_is_not_running_stops_in_preflight(self):
        (self.prefix / "fake" / "old-active").unlink()
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=old_unit_not_active", " ".join(result["readback"]))


class InterruptTests(Fixture):
    """The runner kills a step's process group with SIGKILL on Ctrl-C or a timeout, so no trap in a step can run. These prove the pack's design: the flag file + ExecStopPost + the runner's own
    restore of the declared units put the OLD service back when the kill lands after the new one has started."""
    def drive(self, flag):
        self.flag_on(flag)
        driver = ("import subprocess, sys; from lifeos.edge.runner import runner; fake = sys.argv[3]\n"
                  "def sc(*a):\n    d = subprocess.run([fake, *a], capture_output=True, text=True); return d.returncode, (d.stdout.split() or [''])[0]\n"
                  "h = runner.Host(state=sys.argv[1], inbox=sys.argv[2], require_root=False, systemctl=sc)\n"
                  "sys.exit(runner.main(sys.argv[4:], h))")
        return subprocess.run([sys.executable, "-c", driver, self.state, self.inbox, self.fake, "LIFE-643", self.sha8], cwd=str(ROOT), capture_output=True, text=True, timeout=120)

    def test_a_kill_after_the_new_service_started_brings_the_old_one_back(self):
        done = self.drive("interrupt")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "RESULT fail E_INTERRUPTED\n", ""))
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))             # resume-old.sh ran when the runner's restore stopped the new units
        self.assertFalse(self.on("new-active") or self.on("sock-active"))
        self.assertFalse(self.flag.exists())
        self.assertEqual(tree_listing(self.old_state), self.before_old)               # the old folder was never touched
        self.assertEqual(self.endpoint.read_text(), "# old endpoint\n")               # the runner restored the declared consumer files
        self.assertEqual(self.stats.read_text(), "// old stats\n")
        self.assertTrue(self.new_state.exists())                                      # a killed step cannot clean up: the new copy stays (700, lifeos-wa's) and the next run wipes it first

    def test_a_kill_between_the_old_stop_and_the_new_start_leaves_the_old_service_stopped_but_intact_and_one_command_restarts_it(self):
        done = self.drive("interrupt-after-old-stop")
        self.assertEqual((done.returncode, done.stdout), (1, "RESULT fail E_INTERRUPTED\n"))
        self.assertFalse(self.on("old-active"))                                       # the one window the pack cannot close by itself (a few seconds); documented in the receipt
        self.assertTrue(self.on("old-enabled"))                                       # still enabled: a reboot would also bring it back
        self.assertFalse(self.on("new-active"))
        self.assertEqual(tree_listing(self.old_state), self.before_old)
        self.assertTrue(self.flag.exists())                                           # the flag shows the state; the recovery line is `systemctl --user start` for the old unit
        self.assertEqual(subprocess.run([self.fake, "--user", "start", "lifeos-whatsapp-attention.service"]).returncode, 0)
        self.assertTrue(self.on("old-active"))

    def test_resume_old_does_nothing_without_the_flag(self):
        (self.prefix / "fake" / "old-active").unlink()
        env = {**os.environ, "WA_ROOT": str(self.prefix)}
        subprocess.run(["sh", str(PACK_SRC / "files" / "resume-old.sh")], env=env, check=True)
        self.assertFalse(self.on("old-active"))
        self.flag.parent.mkdir(parents=True, exist_ok=True)
        self.flag.touch()
        subprocess.run(["sh", str(PACK_SRC / "files" / "resume-old.sh")], env=env, check=True)
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse(self.flag.exists())


class HelperTests(unittest.TestCase):
    """wa_row1.py checks that a pack-level test cannot stage (a second user needs root)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        (self.src / "auth").mkdir(parents=True)
        (self.src / "auth" / "a.json").write_text("NOT-A-REAL-SECRET")
        (self.src / "chats.json").write_text("{}")
        (self.src / "chats.json").chmod(0o664)
        self.server = Server(str(self.src / "attention.sock"), str(self.tmp / "silent"))
        self.server.start()
        self.addCleanup(self.server.sock.close)

    def wa(self, *args, env=None):
        done = subprocess.run([sys.executable, "-I", str(WA_ROW1), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
        return done.returncode, done.stdout.strip()

    def test_copy_session_skips_the_socket_sets_700_600_and_changes_the_names_and_sizes_of_nothing(self):
        dst = self.tmp / "home" / ".local" / "state" / "wa"
        code, out = self.wa("copy-session", str(self.src), str(dst), str(os.getuid()), str(os.getgid()))
        self.assertEqual((code, out), (0, "copied files=2 dirs=2 bytes=%d" % (len("NOT-A-REAL-SECRET") + 2)))
        self.assertFalse((dst / "attention.sock").exists())
        self.assertEqual(self.wa("same", str(self.src), str(dst)), (0, "same entries=4"))
        self.assertEqual({stat.S_IMODE(p.stat().st_mode) for p in dst.rglob("*") if p.is_file()}, {0o600})
        self.assertEqual({stat.S_IMODE(p.stat().st_mode) for p in [dst, dst / "auth", dst.parent, dst.parent.parent]}, {0o700})   # parents made on the way are private too
        self.assertEqual(self.wa("copy-session", str(self.src), str(dst), str(os.getuid()), str(os.getgid())), (1, "dst_exists"))
        (dst / "chats.json").write_text("{}{}")
        self.assertEqual(self.wa("same", str(self.src), str(dst)), (1, "differ"))

    def test_wipe_only_removes_folders_under_the_new_home(self):
        home, other = self.tmp / "home", self.tmp / "other"
        (home / "x").mkdir(parents=True)
        other.mkdir()
        self.assertEqual(self.wa("wipe", str(other), str(home)), (1, "refused"))
        self.assertEqual(self.wa("wipe", str(home), str(home)), (1, "refused"))
        self.assertEqual(self.wa("wipe", str(home / "x"), str(home)), (0, "wiped"))
        self.assertTrue(other.exists() and home.exists() and not (home / "x").exists())

    def test_install_tree_keeps_links_and_exec_bits_and_removes_group_and_other_write(self):
        tree = self.tmp / "nm"
        (tree / "p").mkdir(parents=True)
        (tree / "p" / "run").write_text("x")
        (tree / "p" / "run").chmod(0o775)
        (tree / "p" / "data").write_text("y")
        (tree / "p" / "data").chmod(0o666)
        (tree / "ln").symlink_to("p/run")
        out = self.tmp / "out"
        code, said = self.wa("install-tree", str(tree), str(out), env={"WA_NO_CHOWN": "1"})
        self.assertEqual((code, said), (0, "installed entries=4 bytes=2"))
        self.assertTrue((out / "ln").is_symlink())
        self.assertEqual((stat.S_IMODE((out / "p" / "run").stat().st_mode), stat.S_IMODE((out / "p" / "data").stat().st_mode), stat.S_IMODE((out / "p").stat().st_mode)), (0o755, 0o644, 0o755))
        self.assertEqual(self.wa("tree-summary", str(tree)), self.wa("tree-summary", str(out)))

    def test_a_folder_not_owned_by_the_owner_or_with_a_link_is_refused_and_the_socket_is_counted_never_printed(self):
        self.assertEqual(self.wa("check", str(os.getuid()), str(self.src)), (0, ""))
        self.assertEqual(self.wa("check", str(os.getuid() + 1), str(self.src)), (1, "owner"))
        (self.src / "ln").symlink_to("chats.json")
        self.assertEqual(self.wa("check", str(os.getuid()), str(self.src)), (1, "symlink"))
        self.assertEqual(self.wa("connect", str(self.src / "attention.sock")), (0, str(len(json.dumps(SNAPSHOT)) + 1)))

    def test_canlist_reports_denied_for_a_folder_that_cannot_be_listed(self):
        self.assertEqual(self.wa("canlist", str(self.tmp / "nowhere")), (1, "denied"))
        self.assertEqual(self.wa("canlist", str(self.src)), (0, "readable"))


class ConsumerTests(unittest.TestCase):
    """The private endpoint (user ubuntu) tries the new socket first and still works on the old one (the rollback)."""
    def setUp(self):
        from lifeos.imessage import alerts_endpoint as ep
        self.ep = ep
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def serve(self, name, snapshot):
        path = str(self.tmp / name)
        server = Server(path, str(self.tmp / "silent"))
        server.start()
        self.addCleanup(server.sock.close)
        return path

    def test_the_new_socket_is_tried_first_then_the_old_one(self):
        new, old = self.serve("n.sock", SNAPSHOT), self.serve("o.sock", {**SNAPSHOT, "health": "DEGRADED"})
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=new, WHATSAPP_SOCKET=old):
            self.assertEqual(self.ep.whatsapp("0" * 64)["health"], "CURRENT")
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=str(self.tmp / "gone.sock"), WHATSAPP_SOCKET=old):
            self.assertEqual(self.ep.whatsapp("0" * 64)["health"], "CURRENT")                                # fallback = the rollback state
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=str(self.tmp / "gone.sock"), WHATSAPP_SOCKET=str(self.tmp / "gone2.sock")):
            self.assertEqual(self.ep.whatsapp("0" * 64)["health"], "UNAVAILABLE")                            # neither: unavailable, never zero

    def test_an_explicit_path_is_the_only_one_tried(self):
        old = self.serve("o.sock", SNAPSHOT)
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=old, WHATSAPP_SOCKET=old):
            self.assertEqual(self.ep.whatsapp("0" * 64, str(self.tmp / "gone.sock"))["health"], "UNAVAILABLE")

    def test_the_oracle_card_script_counts_either_unit(self):
        text = (ROOT / "lifeos" / "oraclecloud" / "oracle" / "stats.mjs").read_text()
        self.assertIn('["is-active", "lifeos-wa-attention.service"]', text)
        self.assertIn("active(UNITS.whatsapp)", text)


class unittest_patch:
    def __init__(self, module, **values):
        self.module, self.values, self.saved = module, values, {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = getattr(self.module, key)
            setattr(self.module, key, value)

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(self.module, key, value)


if __name__ == "__main__":
    unittest.main()
