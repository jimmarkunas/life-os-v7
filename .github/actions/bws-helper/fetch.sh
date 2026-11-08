#!/usr/bin/env bash
# LIFE-406 (extends D209). Puts the Bitwarden helper program where bitwarden/sm-action looks for it, so the action never
# downloads it itself. Order: (1) a saved copy, used only if its SHA-256 equals the fingerprint COMMITTED in helper.sha256;
# a copy that does not match is deleted, never executed. (2) no saved copy: download it, retrying with growing pauses until
# the wait budget is used; the file is used only if it matches the committed fingerprint (never one read from the download).
# No credential is read, passed or written here; Bitwarden is not contacted. Fails the job when nothing verified is available.
set -u
dest=${BWS_DEST:?}
want=$(tr -d ' \n\r' < "${BWS_SHA256_FILE:?}")
url=${BWS_URL:?}
budget=${BWS_WAIT_SECONDS:-300}
pause=${BWS_FIRST_PAUSE:-10}
sleeper=${BWS_SLEEP_CMD:-sleep}
out=${GITHUB_OUTPUT:-/dev/null}

case "$want" in
  *[!0-9a-f]*|"") echo "::error::bws helper: helper.sha256 is not a lowercase hex SHA-256"; exit 1 ;;
esac
[ "${#want}" -eq 64 ] || { echo "::error::bws helper: helper.sha256 is not 64 hex characters"; exit 1; }

valid() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$want" ]; }

mkdir -p "$(dirname "$dest")"
if [ -e "$dest" ]; then
  if valid "$dest"; then
    chmod 755 "$dest"
    echo "bws helper: saved copy verified against the committed fingerprint; no download"
    echo "source=saved" >> "$out"
    exit 0
  fi
  echo "::warning::bws helper: the saved copy does not match the committed fingerprint; discarded, not used"
  rm -f "$dest"
fi

tmp="$dest.part"
waited=0
attempt=0
while :; do
  attempt=$((attempt + 1))
  rm -f "$tmp"
  if curl -fsSL --max-time 60 -o "$tmp" "$url"; then
    if valid "$tmp"; then
      mv "$tmp" "$dest"
      chmod 755 "$dest"
      echo "bws helper: downloaded on attempt $attempt and verified against the committed fingerprint"
      echo "source=downloaded" >> "$out"
      exit 0
    fi
    rm -f "$tmp"
    echo "::error::bws helper: the download does not match the committed fingerprint; discarded, not used"
    exit 1
  fi
  if [ $((waited + pause)) -gt "$budget" ]; then
    break
  fi
  echo "bws helper: download failed (attempt $attempt); retrying in ${pause}s"
  "$sleeper" "$pause"
  waited=$((waited + pause))
  pause=$((pause * 2))
  [ "$pause" -gt 120 ] && pause=120
done
rm -f "$tmp"
echo "::error::bws helper: the GitHub release download failed for about ${waited}s over $attempt attempts and no saved copy exists. Bitwarden was not contacted and no credential was read or guessed from anywhere else."
exit 1
