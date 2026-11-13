#!/bin/bash
# LIFE-652, one step. Runs as root through lifeos-run; the work is in files/install.sh (pass --dry-run to it by hand to see what it would do).
exec bash "$PACK_DIR/files/install.sh"
