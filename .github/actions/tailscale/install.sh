#!/usr/bin/env bash
# LIFE-637: download the official Tailscale tarball and install it only if its SHA-256 equals the one recorded in this repo.
# Test hooks (env): TS_URL (default: the official address), TS_SHA256_FILE, TS_BIN_DIR (default /usr/local/bin), TS_SUDO (default sudo; empty in tests), TS_TMP.
set -euo pipefail
version="1.94.2"
url="${TS_URL:-https://pkgs.tailscale.com/stable/tailscale_${version}_amd64.tgz}"
bin_dir="${TS_BIN_DIR:-/usr/local/bin}"
sudo_cmd="${TS_SUDO-sudo}"
work="${TS_TMP:-$(mktemp -d)}"
expected="$(tr -d '[:space:]' < "${TS_SHA256_FILE:?TS_SHA256_FILE is required}" | tr 'A-F' 'a-f')"
case "$expected" in *[!0-9a-f]* | "") echo "::error::tailscale install: the recorded fingerprint is not 64 hex characters"; exit 1 ;; esac
[ "${#expected}" -eq 64 ] || { echo "::error::tailscale install: the recorded fingerprint is not 64 hex characters"; exit 1; }

tarball="$work/tailscale.tgz"
curl -fsSL --max-time 120 --retry 3 --retry-delay 2 -o "$tarball" "$url"
actual="$(sha256sum "$tarball" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$tarball" | cut -d' ' -f1)"
if [ "$actual" != "$expected" ]; then
  rm -f "$tarball"
  echo "::error::tailscale install: the download does not match the recorded SHA-256; discarded, nothing installed"
  exit 1
fi
tar -xzf "$tarball" -C "$work"
src="$work/tailscale_${version}_amd64"
$sudo_cmd install -m 755 "$src/tailscale" "$src/tailscaled" "$bin_dir"
echo "tailscale install: ${version} installed after the SHA-256 matched the recorded value"
