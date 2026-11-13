# LIFE-643 row 1: shared by the pack steps (sourced, never run alone). Names, modes, ids and counts only; no session file is ever opened, printed or hashed.
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)     # empty on the host; a temp folder in the tests
NOCHOWN=""; [ -z "$R" ] || { NOCHOWN=--no-chown; export WA_ROOT="$R"; }        # tests only: no root, so no chown to a second user

# the old side: the ubuntu user unit, its session folder, key, program folder
UH="$R/home/ubuntu"
OLD_STATE="$UH/.local/state/lifeos-whatsapp"
OLD_KEY="$UH/.config/lifeos-whatsapp/hash-key"
OLD_APP="$UH/.local/lib/lifeos-whatsapp"
OLD_SOCK="$OLD_STATE/attention.sock"
OLD_NODE_LINK="$UH/.openclaw/tools/node/bin/node"
OLD_SVC=lifeos-whatsapp-attention.service
GW=openclaw-gateway.service
ENDPOINT="$UH/.local/lib/lifeos-imessage/alerts_endpoint.py"
STATS="$UH/.local/lib/lifeos-oracle/stats.mjs"

# the new side
NEW_USER=lifeos-wa
NEW_HOME="$R/var/lib/lifeos-wa"
NEW_STATE="$NEW_HOME/.local/state/lifeos-whatsapp"
NEW_KEY="$NEW_HOME/.config/lifeos-whatsapp/hash-key"
APP="$R/opt/lifeos-wa"
UNITDIR="$R/etc/systemd/system"
NEW_SVC=lifeos-wa-attention.service
NEW_SOCKET_UNIT=lifeos-wa-attention.socket
NEW_SOCK="$R/run/lifeos-wa/attention.sock"
FLAG="$R/var/lib/lifeos-runner/whatsapp-row1.switching"

# pinned: what the host holds today (byte-identical to main before this change) and the one node unit this pack must not meet already running
ENDPOINT_OLD_SHA=e9686bdd23021e86562619c4c2303bdc259d51481f45ae6126d16d79f435e1af
STATS_OLD_SHA=6e343b8f093a860fc70c06938ef19d34659074daa2e3178865018fa7ce948e7d
ATTENTION_OLD_SHA=62808b91dd0387b836c510fe2dda93ed02a2928719fdaea1ea3ddcfbeb83e716
STATE_OLD_SHA=9706bcf4be86cf4bdb1cecc1c8e189b5bb219cc6377056f4b61f2fa3ed46ef78

[ -z "$R" ] || [ ! -f "$R/pins.sh" ] || . "$R/pins.sh"   # tests only: the fixture files cannot have the host's real hashes

SLEEP="$R/usr/bin/sleep"
PY=python3; [ -z "$R" ] || PY="$R/usr/bin/python3"

# run an installed command by name: the real one on the host, the stand-in under $R/usr/bin in the tests
cmd() { local c=$1; shift; if [ -z "$R" ]; then command "$c" "$@"; else "$R/usr/bin/$c" "$@"; fi; }
wa() { "$PY" -I "$PACK_DIR/files/wa_row1.py" $NOCHOWN "$@"; }
sysc() { cmd systemctl "$@"; }                                    # the system manager (root)
as_user() { local u=$1; shift; cmd runuser -u "$u" -- "$@"; }       # run as another user

OWNER_UID=$(cmd id -u ubuntu 2>/dev/null || echo 0)
OWNER_GID=$(cmd id -g ubuntu 2>/dev/null || echo 0)

# systemctl --user for the ubuntu user, run from root: that user's runtime folder and bus
usys() {
  if [ -z "$R" ]; then
    runuser -u ubuntu -- env "XDG_RUNTIME_DIR=/run/user/$OWNER_UID" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$OWNER_UID/bus" /usr/bin/systemctl --user "$@"
  else
    "$R/usr/bin/systemctl" --user "$@"
  fi
}

conn_count() { "$R/usr/bin/ss" -tnpH state established 2>/dev/null | grep -c "pid=$1," || true; }

# what the private endpoint (user ubuntu) does: connect and count; the health word is the only thing read back
CONNECT_PY='import socket,sys
s=socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(sys.argv[1]); n=0
while True:
    c=s.recv(65536)
    if not c: break
    n+=len(c)
print(n); sys.exit(0 if n else 1)'
LISTDIR_PY='import os,sys
try: os.listdir(sys.argv[1]); print("readable")
except OSError: print("denied")'
ENDPOINT_PY='import importlib.util as u,sys
sp=u.spec_from_file_location("ep",sys.argv[1]); ep=u.module_from_spec(sp); sp.loader.exec_module(ep)
b=ep.whatsapp("0"*64, sys.argv[2] if len(sys.argv)>2 else None)
print(b.get("health"))'

cd / || exit 1      # the runner starts steps inside its root-only work folder; the users the steps run commands as must not start in a folder they cannot enter
