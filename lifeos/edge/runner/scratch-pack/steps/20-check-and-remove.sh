#!/bin/bash
# Reads the scratch line back and removes the file, so a successful run leaves nothing behind.
set -euo pipefail
grep -q '^scratch ' /tmp/lifeos-runner-scratch.txt
rm -f /tmp/lifeos-runner-scratch.txt
[ ! -e /tmp/lifeos-runner-scratch.txt ]
echo "READBACK scratch_removed=yes"
echo "READBACK systemctl_present=$([ -x /usr/bin/systemctl ] && echo yes || echo no)"
