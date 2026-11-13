#!/bin/bash
# LIFE-643 cleanup, one step. Runs as root through lifeos-run; the work is in files/cleanup.sh (pass --dry-run to it by hand to see what it would do).
exec bash "$PACK_DIR/files/cleanup.sh"
