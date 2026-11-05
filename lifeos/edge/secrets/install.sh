#!/bin/bash
# LIFE-306 route C: install the secrets broker on lifeos-edge-01. Run as root (sudo bash /tmp/lifeos-secrets-pack/install.sh) from the folder that holds this file,
# lifeos_secret_use.py and purposes.d/. Protected step: Jim runs it after a warning. Nothing restarts; no service, timer or listener is added.
# It writes: /usr/local/sbin/lifeos-secret-use, /etc/lifeos-secrets/purposes.d/*.json, /var/log/lifeos-secret-use.jsonl (append-only when the filesystem allows).
# It prints names, counts and fixed result codes only. The check calls decrypt ONE credential inside the program; the value is never shown.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ -f "$HERE/lifeos_secret_use.py" ] && [ -d "$HERE/purposes.d" ] || { echo "pack incomplete in $HERE"; exit 1; }
[ -f /etc/hermes-eval/creds/openrouter_api_key.cred ] || { echo "credential file openrouter_api_key.cred not found; nothing installed"; exit 1; }
[ -e /usr/local/sbin/lifeos-secret-use ] && { echo "already installed (/usr/local/sbin/lifeos-secret-use exists); run uninstall.sh first"; exit 1; }
python3 -I -m py_compile "$HERE/lifeos_secret_use.py" && echo "program compiles"
install -o root -g root -m 0755 "$HERE/lifeos_secret_use.py" /usr/local/sbin/lifeos-secret-use
install -d -o root -g root -m 0755 /etc/lifeos-secrets /etc/lifeos-secrets/purposes.d
for f in "$HERE"/purposes.d/*.json; do install -o root -g root -m 0644 "$f" /etc/lifeos-secrets/purposes.d/; done
install -o root -g adm -m 0640 /dev/null /var/log/lifeos-secret-use.jsonl
chattr +a /var/log/lifeos-secret-use.jsonl 2>/dev/null && echo "log is append-only (chattr +a)" || echo "NOTE: chattr +a not available here; the log is root-owned and the program only appends"
echo "== installed =="
ls -l /usr/local/sbin/lifeos-secret-use /etc/lifeos-secrets/purposes.d/ /var/log/lifeos-secret-use.jsonl | sed 's/^/  /'
echo "== read-back through the program (each call is audited) =="
SUDO_USER=${SUDO_USER:-opc}
echo "-- check_openrouter_credential (name, length, 8-hex prefix only):"; /usr/local/sbin/lifeos-secret-use check_openrouter_credential || true
echo "-- openrouter_key_info (limit, limit_remaining, usage only):";        /usr/local/sbin/lifeos-secret-use openrouter_key_info || true
echo "-- negative: unregistered purpose:";                                   /usr/local/sbin/lifeos-secret-use print_everything || true
echo "-- negative: extra argument (a secret name):";                         /usr/local/sbin/lifeos-secret-use openrouter_key_info groq_api_key || true
echo "== audit log: $(wc -l < /var/log/lifeos-secret-use.jsonl) lines; results seen: $(python3 -c "import json,collections;print(dict(collections.Counter(json.loads(l)['result'] for l in open('/var/log/lifeos-secret-use.jsonl'))))")"
grep -c "sk-" /var/log/lifeos-secret-use.jsonl | sed 's/^/  lines containing "sk-" (must be 0): /' || true
echo "UNINSTALL: sudo bash $HERE/uninstall.sh"
