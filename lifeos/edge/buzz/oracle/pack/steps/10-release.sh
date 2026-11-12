#!/bin/bash
# LIFE-590 step 1 of 3: lay down the new immutable release folder (nothing running is touched). Names and counts only.
set -euo pipefail
R=$(cat "$PACK_DIR/files/ROOT" 2>/dev/null || true)
REL=$(cat "$PACK_DIR/files/RELEASE")
BASE="$R/opt/lifeos-buzz-mcp"
[ -d "$BASE/releases/c423a4434c4c" ] || { echo "READBACK previous_release=missing"; exit 1; }
[ ! -e "$BASE/releases/$REL" ] || { echo "READBACK release=already_exists"; exit 1; }
OWN="-o root -g root"; [ -z "$R" ] || OWN=""
install -d $OWN -m 755 "$BASE/releases/$REL" "$BASE/releases/$REL/lifeos" "$BASE/releases/$REL/lifeos/buzz"
n=0
while IFS= read -r f; do
  install $OWN -m 644 "$PACK_DIR/files/tree/$f" "$BASE/releases/$REL/$f"
  n=$((n + 1))
done < <(cd "$PACK_DIR/files/tree" && find . -type f | sed 's#^\./##' | sort)
echo "READBACK release=$REL"
echo "READBACK release_files=$n"
