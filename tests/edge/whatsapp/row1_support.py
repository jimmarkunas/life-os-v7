"""shared fixture of the three test modules of the WhatsApp row 1 pack (test_whatsapp_row1_pack, test_whatsapp_row1_rollback,
test_whatsapp_row1_rollback_more): the pack is built from the repo and run through the REAL runner on a temp tree with fakes."""

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
SNAPSHOT = {
    "channel": "whatsapp",
    "health": "CURRENT",
    "observed_at": "2026-11-13T15:00:00Z",
    "chats_known": 80,
    "unread_messages": 12,
    "unread_messages_unmuted": 9,
    "unread_chats": 5,
    "unread_chats_unmuted": 4,
    "latest_state": "NONE",
    "latest_name": None,
}

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
if [ "$2" = ubuntu ]; then exec /usr/bin/id "$1"; fi
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
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
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
        for folder in (
            self.old_state / "auth",
            self.old_key.parent,
            self.old_app / "node_modules" / "baileys",
            self.old_app / "node_modules" / ".bin",
            self.endpoint.parent,
            self.stats.parent,
            self.old_unit.parent,
            home / ".openclaw" / "tools" / "node-v24" / "bin",
            p / "etc" / "systemd" / "system",
            p / "opt",
            p / "var" / "lib",
            p / "run" / "lifeos-wa",
            p / "proc" / "4242",
            p / "usr" / "bin",
        ):
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
        for name in ("package.json", "package-lock.json"):
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
        (p / "pins.sh").write_text(
            "ENDPOINT_OLD_SHA=%s\nSTATS_OLD_SHA=%s\nATTENTION_OLD_SHA=%s\nSTATE_OLD_SHA=%s\n"
            % (
                sha(self.endpoint),
                sha(self.stats),
                sha(self.old_app / "attention.mjs"),
                sha(self.old_app / "state.mjs"),
            )
        )
        bindir = p / "usr" / "bin"
        for name, body in (
            ("systemctl", FAKE_SYSTEMCTL),
            ("ss", FAKE_SS),
            ("getent", FAKE_GETENT),
            ("id", FAKE_ID),
            ("useradd", FAKE_USERADD),
            ("runuser", FAKE_RUNUSER),
            ("sleep", "#!/bin/bash\nexit 0\n"),
        ):
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
        subprocess.run(
            [sys.executable, str(BUILDER), str(out), "--root-prefix", str(self.prefix)],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )

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
        """The old service is running and enabled, the new one is down, the new session copy is gone, the old folder is
        exactly as it was.
        """
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse(
            self.on("new-active") or self.on("sock-active") or self.on("enabled-lifeos-wa-attention.service")
        )
        self.assertFalse(self.new_state.exists())
        self.assertFalse((self.new_home / ".config" / "lifeos-whatsapp").exists())
        self.assertFalse(self.flag.exists())
        self.assertEqual(tree_listing(self.old_state), self.before_old)
        self.assertEqual(self.endpoint.read_text(), "# old endpoint\n")  # the runner put the consumer files back
        self.assertEqual(self.stats.read_text(), "// old stats\n")
        self.assertEqual(self.old_unit.read_text(), OLD_UNIT)


class RollbackBase(Fixture):
    def failure(self, flag, code="E_STEP_3"):
        self.flag_on(flag)
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", code), result)
        self.assert_old_is_back()
        return result


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
