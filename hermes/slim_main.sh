#!/bin/bash
# LIFE-340 (Jim, Nov 6 2026: "How do we fix you to be smarter, faster, and consume less resources?"): two measured causes in the main (default) profile's config.yaml.
#   1) `terminal.cwd: '.'` is relative, so the agent works in the service's folder, `/`; a file search walks /proc, /sys and the swapfile (one ran 1:29+). Fix: an absolute, small folder.
#   2) Every enabled MCP server puts its tool schemas into EVERY prompt (about 42 KB of schemas before any history). On the main profile: todoist, robinhood (57 tools), plaid, figma are enabled.
#      Disabling a server removes its tools from the prompt (and from reach). You choose which with --disable; nothing is disabled unless you name it.
#   sudo bash slim_main.sh                                   DRY RUN (default): lists the MCP servers (enabled, tool counts) and shows the planned edits. Writes nothing.
#   sudo bash slim_main.sh --disable robinhood,plaid,figma   dry run that also plans to set `enabled: false` for those servers.
#   add --apply: backs up config.yaml into /root/hermes-config-<time CT>/, creates the work folder (owner hermes), makes the edits (text edits: every comment stays), reads back
#                (YAML parse: only those settings differ), prints the rollback line.
# Options: --work DIR (default /var/lib/hermeseval/work), --no-cwd (skip edit 1), and for tests --home DIR (the .hermes folder), --backup DIR, --owner USER (default hermes).
# It restarts NOTHING (Hermes reads config at startup: a restart is a separate step) and never prints a key, token, secret or password line.
set -eu
HOME_DIR=/var/lib/hermeseval/home/.hermes; WORK=/var/lib/hermeseval/work; DISABLE=""; APPLY=no; BACKUP=""; OWNER=hermes; DOCWD=yes
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=yes ;;
    --disable) DISABLE=${2:?}; shift ;;
    --work) WORK=${2:?}; shift ;;
    --no-cwd) DOCWD=no ;;
    --home) HOME_DIR=${2:?}; shift ;;
    --backup) BACKUP=${2:?}; shift ;;
    --owner) OWNER=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
echo "$WORK" | grep -Eq '^/[A-Za-z0-9._/-]{2,100}$' && ! echo "$WORK" | grep -q '\.\.' || { echo "REFUSED: --work must be an absolute path with plain characters"; exit 1; }
[ "$WORK" != "/" ] || { echo "REFUSED: --work must not be /"; exit 1; }
echo "$DISABLE" | grep -Eq '^([A-Za-z0-9_-]+(,[A-Za-z0-9_-]+)*)?$' || { echo "REFUSED: --disable takes server names separated by commas"; exit 1; }
CONF="$HOME_DIR/config.yaml"
[ -f "$CONF" ] || { echo "REFUSED: no $CONF"; exit 1; }
PYCODE=$(cat <<'PY'
import re, shutil, os, sys
mode, conf, dst, work, disable, docwd = sys.argv[1:7]
want = [x for x in disable.split(",") if x]
lines = open(conf).read().split("\n")
notes, errors, changed = [], [], 0

def block(start_rx, indent, src):
    for i, l in enumerate(src):
        if re.match(start_rx, l):
            j = i + 1
            while j < len(src):
                t = src[j]
                if t.strip() and not t.lstrip().startswith("#") and (len(t) - len(t.lstrip())) <= indent:
                    break
                j += 1
            return i, j
    return None

new = list(lines)
mc = block(r"^mcp_servers:\s*$", 0, new)
servers = {}
if mc:
    i = mc[0] + 1
    while i < mc[1]:
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", new[i])
        if m:
            end = i + 1
            while end < mc[1] and not re.match(r"^  [A-Za-z0-9_-]+:\s*$", new[end]):
                end += 1
            servers[m.group(1)] = (i, end)
            i = end
        else:
            i += 1
print("-- MCP servers on this profile (each enabled server adds its tool schemas to EVERY prompt):")
for name, (a, b) in servers.items():
    body = new[a:b]
    en = next((re.match(r"^    enabled:\s*(\w+)", l).group(1) for l in body if re.match(r"^    enabled:\s*\w+", l)), "unset(true)")
    items = sum(1 for l in body if re.match(r"^\s+- ", l))
    kind = "include" if any(re.match(r"^      include:", l) for l in body) else ("exclude" if any(re.match(r"^      exclude:", l) for l in body) else "")
    tools = ("%d listed to include" % items) if kind == "include" else (("all but %d excluded" % items) if kind == "exclude" else "all its tools")
    print("   %-12s enabled=%-11s %s" % (name, en, tools))
for name in want:
    if name not in servers:
        errors.append("no MCP server named '%s' in this config" % name)
        continue
    a, b = servers[name]
    k = next((x for x in range(a, b) if re.match(r"^    enabled:\s*true\b", new[x])), None)
    if k is None:
        if any(re.match(r"^    enabled:\s*false\b", new[x]) for x in range(a, b)):
            notes.append("%s is already disabled" % name)
        else:
            errors.append("%s has no 'enabled: true' line to change; edit it by hand" % name)
        continue
    new[k] = "    enabled: false"
    notes.append("would set enabled: false for %s" % name)
    changed += 1
if docwd == "yes":
    tb = block(r"^terminal:\s*$", 0, new)
    done = False
    if tb:
        for x in range(tb[0] + 1, tb[1]):
            if re.match(r"^  cwd:\s*(['\"]?)\.\1\s*(#.*)?$", new[x]):
                new[x] = "  cwd: " + work
                notes.append("would set terminal.cwd to %s (today it is the relative '.', which resolves to / for the service)" % work)
                changed += 1
                done = True
                break
            m = re.match(r"^  cwd:\s*(.*)$", new[x])
            if m:
                notes.append("terminal.cwd is already set (%s): not changed" % m.group(1)[:60])
                done = True
                break
    if not done:
        errors.append("no 'terminal:' section with a 'cwd:' line was found; edit by hand")
for n in notes:
    print("-- " + n)
for e in errors:
    print("REFUSED: " + e)
if errors:
    sys.exit(1)
if not changed:
    print("Nothing to do.")
    sys.exit(3)
if mode == "apply":
    open(dst, "w").write("\n".join(new))
    shutil.copymode(conf, dst)
    st = os.stat(conf)
    try:
        os.chown(dst, st.st_uid, st.st_gid)
    except PermissionError:
        pass
PY
)
echo "== $([ "$APPLY" = yes ] && echo APPLY || echo 'DRY RUN (nothing is written)'): $CONF =="
PLAN=$(python3 -c "$PYCODE" plan "$CONF" "" "$WORK" "$DISABLE" "$DOCWD") && RC=0 || RC=$?
echo "$PLAN"
case "$RC" in 0) ;; 3) exit 0 ;; *) echo "(nothing was changed)"; exit 1 ;; esac
if [ "$APPLY" != yes ]; then echo "Dry run only. To apply, add --apply (and --disable NAME,NAME for the servers you choose). Nothing is restarted either way."; exit 0; fi
STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
[ -n "$BACKUP" ] || BACKUP="/root/hermes-config-$STAMP"
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"; cp -p "$CONF" "$BACKUP/config.yaml"
TMP="$CONF.new.$$"
python3 -c "$PYCODE" apply "$CONF" "$TMP" "$WORK" "$DISABLE" "$DOCWD" >/dev/null || { rm -f "$TMP"; echo "REFUSED while writing; nothing was changed"; exit 1; }
if [ "$DOCWD" = yes ]; then mkdir -p "$WORK"; chmod 750 "$WORK"; chown "$OWNER":"$OWNER" "$WORK" 2>/dev/null || true; fi
mv -f "$TMP" "$CONF"
echo "  backed up to $BACKUP/config.yaml and made the edit(s)$([ "$DOCWD" = yes ] && echo "; work folder $WORK ready")"
RESULT=$(python3 - "$CONF" "$BACKUP/config.yaml" "$WORK" "$DISABLE" "$DOCWD" <<'PY'
import sys
conf, old, work, disable, docwd = sys.argv[1:6]
want = [x for x in disable.split(",") if x]
try:
    import yaml
except Exception:
    yaml = None
if not yaml:
    print("text-ok")
    sys.exit()
new, was = yaml.safe_load(open(conf)), yaml.safe_load(open(old))
ok = True
for n in want:
    if (was.get("mcp_servers") or {}).get(n, {}).get("enabled", True) is True:
        new["mcp_servers"][n]["enabled"] = True        # compare as if untouched; checked below
        ok = ok and yaml.safe_load(open(conf))["mcp_servers"][n]["enabled"] is False
if docwd == "yes" and (was.get("terminal") or {}).get("cwd") == ".":
    ok = ok and yaml.safe_load(open(conf))["terminal"]["cwd"] == work
    new["terminal"]["cwd"] = "."
print("yaml-ok" if ok and new == was else "yaml-MISMATCH")
PY
)
case "$RESULT" in
  yaml-ok) echo "== read-back OK: the file parses as YAML and ONLY the planned settings differ from the backup ==" ;;
  text-ok) echo "== read-back: no YAML parser for python3 on this host; the edits were made line by line ==" ;;
  *) echo "READ-BACK FAILED ($RESULT). ROLLBACK NOW: cp -p $BACKUP/config.yaml $CONF"; exit 1 ;;
esac
echo "Nothing was restarted. Hermes reads config at startup; next, as its own step: sudo systemctl restart hermes-eval-backend (Desktop reconnects; history is kept), then measure again: ask Hermes to run hermes prompt-size --json."
echo "ROLLBACK: cp -p $BACKUP/config.yaml $CONF   (then restart the same way)"
