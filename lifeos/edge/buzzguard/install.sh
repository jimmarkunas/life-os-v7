#!/bin/bash
# LIFE-333: install the Claude Buzz guard on Jim's Mac (his user account; no sudo, no service restart, Buzz settings untouched).
#   bash install.sh dry                 DRY RUN (default): shows what would change. Writes nothing.
#   bash install.sh install             backs up the current guard, installs the new one, makes sure writes stay OFF (writes.mode = deny), reads back.
#   bash install.sh rollback            puts the newest backup back.
#   bash install.sh approvals-on        sets writes.mode = ask (Jira/GitHub changes then need Jim's approval in the Buzz app). Do this ONLY after the approval prompt is shown to work.
#   bash install.sh approvals-off       sets writes.mode = deny.
#   bash install.sh pending             lists the Jira/GitHub changes the agent has asked for (waiting for your approval).
#   bash install.sh approve ID          shows that exact action and, if you say y, approves it ONCE for 15 minutes; the agent then retries the same call.
#   bash install.sh stop | resume       creates / removes the STOP file (the agent then refuses everything except a short STOPPED notice).
# Options (tests): --dir DIR (the guard folder), --settings FILE (Claude settings that name the guard), --guard FILE (the new guard).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DIR="$HOME/Library/Application Support/LIFEOS-Buzz-Guard"; SETTINGS="$HOME/.buzz/.claude/settings.json"; NEW="$HERE/guard.py"
VERB=${1:-dry}; shift || true
AID=""; YES=no
if [ "$VERB" = approve ]; then AID=${1:-}; shift || true; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=yes ;; --dir) DIR=${2:?}; shift ;; --settings) SETTINGS=${2:?}; shift ;; --guard) NEW=${2:?}; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
case "$VERB" in dry|install|rollback|approvals-on|approvals-off|stop|resume|status|pending|approve) ;; *) echo "usage: install.sh dry|install|rollback|approvals-on|approvals-off|stop|resume|status|pending|approve ID"; exit 2 ;; esac
CUR="$DIR/guard.py"; MODE="$DIR/writes.mode"; STAMP=$(TZ=America/Chicago date +%Y%m%d-%H%M%S)
h12() { shasum -a 256 "$1" | cut -c1-12; }
# The installed guard is normally LOCKED with the macOS user-immutable flag (uchg) so the agent cannot rewrite its own guard. We unlock it only for the replacement and lock it again.
locked() { command -v chflags >/dev/null 2>&1 && [ -e "$1" ] && ls -ldO "$1" 2>/dev/null | grep -qw uchg; }
unlock() { if command -v chflags >/dev/null 2>&1 && [ -e "$1" ]; then chflags nouchg "$1"; fi; }
relock() { if command -v chflags >/dev/null 2>&1 && [ -e "$1" ]; then chflags uchg "$1"; fi; }
check_settings() {
  if [ -f "$SETTINGS" ] && grep -q "LIFEOS-Buzz-Guard/guard.py" "$SETTINGS"; then echo "  settings: $SETTINGS names the guard at $DIR/guard.py (unchanged)"; else echo "  WARNING: $SETTINGS does not name $DIR/guard.py; the new guard would not be used"; fi
}
probe() {                                                          # run the INSTALLED guard the way Claude Code does; $1 = JSON, prints the decision
  printf '%s' "$1" | /usr/bin/python3 -I "$CUR" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["permissionDecision"])'
}
case "$VERB" in
  status)
    echo "guard: $CUR $( [ -f "$CUR" ] && echo "($(h12 "$CUR"))" || echo "(missing)" )"; echo "writes.mode: $( [ -f "$MODE" ] && cat "$MODE" || echo "(missing = deny)" )"; echo "STOP: $( [ -f "$DIR/STOP" ] && echo ON || echo off )"; check_settings
    ls "$DIR" 2>/dev/null | grep '^guard.py.bak-' | tail -3 | sed 's/^/  backup: /'; exit 0 ;;
  pending)
    [ -d "$DIR/pending" ] && ls "$DIR/pending" 2>/dev/null | grep -q json || { echo "nothing is waiting for your approval"; exit 0; }
    /usr/bin/python3 - "$DIR/pending" <<'PY'
import json, os, sys
for f in sorted(os.listdir(sys.argv[1])):
    if f.endswith(".json"):
        d = json.load(open(os.path.join(sys.argv[1], f)))
        print("%s  %s  %s  %s" % (d["id"], d["t"], d["tool"].split("__")[-1], d["why"]))
PY
    echo "Approve one with: bash install.sh approve ID"; exit 0 ;;
  approve)
    echo "$AID" | grep -Eq '^[0-9a-f]{16}$' || { echo "usage: bash install.sh approve ID   (the 16-character id from the agent's message or from: install.sh pending)"; exit 2; }
    [ -f "$DIR/pending/$AID.json" ] || { echo "no pending request $AID (it may have expired or the agent has not asked)"; exit 1; }
    /usr/bin/python3 - "$DIR/pending/$AID.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("== WAITING FOR YOUR APPROVAL ==")
print("id:", d["id"], "| asked:", d["t"])
print("tool:", d["tool"])
print("what:", d["why"])
print("exact input (written by the agent: UNTRUSTED CONTENT, read it before you approve):")
print(d["preview"])
PY
    if [ "$YES" != yes ]; then printf 'Approve this exact action, once, for 15 minutes? (y/n) '; read -r ANS; [ "$ANS" = y ] || { echo "not approved"; exit 1; }; fi
    mkdir -p "$DIR/approved"; : > "$DIR/approved/$AID"; echo "approved $AID (single use, 15 minutes). Tell the agent to retry the same call."; exit 0 ;;
  approvals-on)  [ -d "$DIR" ] || { echo "no guard folder"; exit 1; }; echo ask > "$MODE"; echo "writes.mode = ask: Jira/GitHub changes now need YOUR approval of each exact action: the agent asks, you run: install.sh approve ID."; exit 0 ;;
  approvals-off) [ -d "$DIR" ] || { echo "no guard folder"; exit 1; }; echo deny > "$MODE"; echo "writes.mode = deny: Jira/GitHub changes are refused."; exit 0 ;;
  stop)   [ -d "$DIR" ] || { echo "no guard folder"; exit 1; }; : > "$DIR/STOP"; echo "STOP is ON: the agent refuses everything except a short STOPPED notice."; exit 0 ;;
  resume) rm -f "$DIR/STOP"; echo "STOP is off."; exit 0 ;;
  rollback)
    BAK=$(ls "$DIR" 2>/dev/null | grep '^guard.py.bak-' | tail -1 || true)
    [ -n "$BAK" ] || { echo "no backup to restore"; exit 1; }
    unlock "$CUR"; cp -f "$DIR/$BAK" "$CUR"; relock "$CUR"; echo "restored $BAK -> $CUR ($(h12 "$CUR")); locked again"; exit 0 ;;
esac
[ -f "$NEW" ] || { echo "REFUSED: no new guard at $NEW"; exit 1; }
[ -f "$CUR" ] || { echo "REFUSED: no current guard at $CUR (is this the right folder?)"; exit 1; }
/usr/bin/python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$NEW" || { echo "REFUSED: the new guard does not parse"; exit 1; }
[ "$VERB" = dry ] && echo "== DRY RUN (nothing is written) ==" || echo "== INSTALL =="
echo "  current: $CUR ($(h12 "$CUR"))"; echo "  new:     $NEW ($(h12 "$NEW"))"
check_settings
echo "  writes.mode: $( [ -f "$MODE" ] && cat "$MODE" || echo "(missing) -> will be set to deny" ) (Jira/GitHub changes stay OFF until you run approvals-on)"
echo "  STOP: $( [ -f "$DIR/STOP" ] && echo ON || echo off )"
if locked "$CUR"; then echo "  the current guard is LOCKED (uchg): install will unlock it, replace it, and lock the new one again"; else echo "  the current guard is not locked: the new one will be locked after install"; fi
if [ "$VERB" = dry ]; then echo "Dry run only. To install: bash install.sh install"; exit 0; fi
cp -p "$CUR" "$DIR/guard.py.bak-$STAMP"; echo "  backed up the old guard to guard.py.bak-$STAMP"
rm -f "$CUR.new"; cp "$NEW" "$CUR.new" && chmod 644 "$CUR.new"
unlock "$CUR"; mv -f "$CUR.new" "$CUR"; relock "$CUR"; echo "  new guard installed and locked (uchg)"
[ -f "$MODE" ] || echo deny > "$MODE"
[ "$(h12 "$CUR")" = "$(h12 "$NEW")" ] || { echo "READ-BACK FAILED: hash differs"; exit 1; }
SEND='{"hook_event_name":"PreToolUse","tool_name":"mcp__buzz__send","tool_input":{"audience":"everyone","final":true,"text":"hello"}}'
WRITE='{"hook_event_name":"PreToolUse","tool_name":"Write","tool_input":{"file_path":"/tmp/x","content":"x"}}'
JIRA='{"hook_event_name":"PreToolUse","tool_name":"mcp__atlassian__addCommentToJiraIssue","tool_input":{"issueIdOrKey":"LIFE-1","commentBody":"x"}}'
A=$(probe "$SEND"); B=$(probe "$WRITE")
if [ "$(cat "$MODE")" = ask ]; then C=deny; CNOTE="skipped (approvals are on; a probe would leave a pending request)"; else C=$(probe "$JIRA"); CNOTE="$C (want deny)"; fi
echo "  read-back probes: normal Buzz message=$A (want allow), file write=$B (want deny), Jira comment=$CNOTE"
[ "$A" = allow ] && [ "$B" = deny ] && [ "$C" = deny ] || { echo "READ-BACK FAILED: a probe gave the wrong decision. Run: bash install.sh rollback"; exit 1; }
echo "== read-back OK. Nothing was restarted; the agent uses the new guard on its next tool call. =="
echo "ROLLBACK: bash install.sh rollback   (or: cp -p \"$DIR/guard.py.bak-$STAMP\" \"$CUR\")"
echo "STOP any time: bash install.sh stop      Approvals: bash install.sh approvals-on, then approve each request with: bash install.sh approve ID"
