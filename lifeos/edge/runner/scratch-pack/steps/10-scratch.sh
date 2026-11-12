#!/bin/bash
# LIFE-310 first real run: the harmless pack. Writes one scratch line to /tmp/lifeos-runner-scratch.txt (declared in restore) and reads it back. Touches nothing else.
set -euo pipefail
printf 'scratch %s\n' "$LIFE_KEY" > /tmp/lifeos-runner-scratch.txt
echo "READBACK scratch_written=$(wc -l < /tmp/lifeos-runner-scratch.txt | tr -d ' ')"
echo "READBACK runner_uid=$(id -u)"
