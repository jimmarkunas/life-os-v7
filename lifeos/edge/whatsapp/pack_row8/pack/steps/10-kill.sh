#!/bin/bash
# LIFE-643 row 8: the kill switch, one step. Runs as root through lifeos-run; the work is in files/kill_switch.sh (pass --dry-run to it by hand to see what it would do).
exec bash "$PACK_DIR/files/kill_switch.sh"
