#!/usr/bin/env bash
# Run `concord setup` in the current repository, then show what it did to your user-level agent configs.
#
# Why: Concord 0.20.0's `setup` rewrites the Concord hooks block in ~/.codex/config.toml (it dropped the
# opening marker and two comment lines on Nov 8 2026). It can touch other user-level files too, so back
# them up first and diff after. Usage, from the repository you are enrolling:
#
#   tools/orca-concord-room/scripts/concord-setup-safe.sh --no-adapters
#
# Everything you pass goes to `concord setup`. Exit code: 0 = setup ran and the user-level files are
# unchanged; 1 = setup ran and changed, created or removed one (the diff or the exact restore/undo command is
# printed; nothing is restored for you); anything else = `concord setup` itself failed.
# CONCORD_SETUP_BACKUP_DIR overrides where the backups go. The default is ~/.local/state/concord-room/setup-backups,
# deliberately OUTSIDE the Concord install directory: rolling Concord back deletes that directory, and the backups
# are what you restore your agent configs from first.
set -euo pipefail

stamp=$(date +%Y%m%d-%H%M%S)
backup="${CONCORD_SETUP_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/concord-room/setup-backups}/$stamp"
files=("$HOME/.codex/config.toml" "$HOME/.claude/settings.json")

mkdir -p "$backup"
for f in "${files[@]}"; do
  if [ -f "$f" ]; then cp -p "$f" "$backup/$(basename "$f")"; else : > "$backup/$(basename "$f").absent"; fi     # note a file that did not exist yet
done

concord setup "$@"

changed=0
for f in "${files[@]}"; do
  b="$backup/$(basename "$f")"
  if [ -f "$b.absent" ]; then                              # it did not exist before setup
    if [ -f "$f" ]; then
      changed=1
      echo
      echo "CREATED by concord setup: $f (it did not exist before, so there is no backup to restore)"
      echo "To undo: rm '$f'"
    fi
    continue
  fi
  [ -f "$b" ] || continue
  if ! diff -u "$b" "$f" > "$b.diff"; then
    changed=1
    echo
    echo "CHANGED by concord setup: $f"
    cat "$b.diff"
    echo "To restore the version from before setup: cp -p '$b' '$f'"
  fi
done

if [ "$changed" = 0 ]; then echo "Your user-level agent configs are unchanged (backups in $backup)."; fi
exit "$changed"
