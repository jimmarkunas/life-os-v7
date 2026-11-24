#!/bin/sh
# LIFE-872: refresh the read-only V7 checkout that the Oracle Concord server (account `concord`) uses as its repo root.
# Oracle has no GitHub credential, so main travels as a git bundle over the existing ssh. Run from any V7 checkout on the Mac. Never edit the Oracle checkout by hand.
set -eu
HOST=${CONCORD_ORACLE_HOST:-opc@lifeos-edge-01}
DIR=/home/concord/life-os-v7
git fetch -q origin main
BUNDLE=$(mktemp -t v7bundle.XXXXXX)
trap 'rm -f "$BUNDLE"' EXIT
git bundle create "$BUNDLE" origin/main >/dev/null 2>&1
scp -q "$BUNDLE" "$HOST:/tmp/v7.bundle"
ssh -o BatchMode=yes "$HOST" "sudo chmod 644 /tmp/v7.bundle && sudo -u concord env HOME=/home/concord sh -c 'cd $DIR && git fetch -q /tmp/v7.bundle +refs/remotes/origin/main:refs/heads/fetched && git checkout -q -f fetched && git checkout -q -B main && git log --oneline -1'; sudo rm -f /tmp/v7.bundle"
