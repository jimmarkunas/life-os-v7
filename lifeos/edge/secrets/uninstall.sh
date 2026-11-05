#!/bin/bash
# LIFE-306 route C: remove the secrets broker (rollback for install.sh). Removes the program and the registry; KEEPS the audit log (it is evidence) unless you pass --purge-log.
set -eu
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
rm -f /usr/local/sbin/lifeos-secret-use
rm -f /etc/lifeos-secrets/purposes.d/openrouter_key_info.json /etc/lifeos-secrets/purposes.d/check_openrouter_credential.json
rmdir /etc/lifeos-secrets/purposes.d /etc/lifeos-secrets 2>/dev/null || echo "NOTE: /etc/lifeos-secrets still holds other files; left in place"
if [ "${1:-}" = "--purge-log" ]; then chattr -a /var/log/lifeos-secret-use.jsonl 2>/dev/null || true; rm -f /var/log/lifeos-secret-use.jsonl; echo "audit log removed"; else echo "audit log kept: /var/log/lifeos-secret-use.jsonl"; fi
echo "program present: $([ -e /usr/local/sbin/lifeos-secret-use ] && echo YES || echo no)   registry present: $([ -d /etc/lifeos-secrets/purposes.d ] && echo YES || echo no)"
