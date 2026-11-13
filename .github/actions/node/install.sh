#!/usr/bin/env bash
# LIFE-639: download the official Node tarball and unpack it only if its SHA-256 equals the one recorded in this repo; then put it first on the PATH.
# Test hooks (env): NODE_URL (default: the official address), NODE_SHA256_FILE, NODE_DIR (default $RUNNER_TEMP/node), GITHUB_PATH (file that receives the PATH line), NODE_TMP.
set -euo pipefail
version="v24.22.3"
name="node-${version}-linux-x64"
url="${NODE_URL:-https://nodejs.org/dist/${version}/${name}.tar.xz}"
dest="${NODE_DIR:-${RUNNER_TEMP:?RUNNER_TEMP or NODE_DIR is required}/node}"
work="${NODE_TMP:-$(mktemp -d)}"
expected="$(tr -d '[:space:]' < "${NODE_SHA256_FILE:?NODE_SHA256_FILE is required}" | tr 'A-F' 'a-f')"
case "$expected" in *[!0-9a-f]* | "") echo "::error::node install: the recorded fingerprint is not 64 hex characters"; exit 1 ;; esac
[ "${#expected}" -eq 64 ] || { echo "::error::node install: the recorded fingerprint is not 64 hex characters"; exit 1; }

tarball="$work/node.tar.xz"
curl -fsSL --max-time 180 --retry 3 --retry-delay 2 -o "$tarball" "$url"
actual="$(sha256sum "$tarball" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$tarball" | cut -d' ' -f1)"
if [ "$actual" != "$expected" ]; then
  rm -f "$tarball"
  echo "::error::node install: the download does not match the recorded SHA-256; discarded, nothing installed"
  exit 1
fi
mkdir -p "$dest"
tar -xJf "$tarball" -C "$dest" --strip-components=1
echo "$dest/bin" >> "${GITHUB_PATH:?GITHUB_PATH is required}"
echo "node install: ${version} unpacked after the SHA-256 matched the recorded value"
