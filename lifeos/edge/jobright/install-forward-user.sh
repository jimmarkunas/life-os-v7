#!/usr/bin/env bash
# LIFE-576 option (a): the forward-only SSH user the hourly runner uses to reach the browser on this VM. Run as root (sudo) once.
#   install-forward-user.sh install '<public key line>'   create the user (no shell) and allow that key to forward 127.0.0.1:9223 and nothing else
#   install-forward-user.sh remove                         delete the user and its key (rollback)
# Same pattern as v7-store-fwd: restrict, port-forwarding only to the one loopback port, command=nologin. The private key lives only in the secret store.
set -euo pipefail
USER_NAME=v7-browser-fwd
case "${1:-}" in
  install)
    pub="${2:?public key line required}"
    case "$pub" in ssh-*|ecdsa-*) ;; *) echo "not a public key line" >&2; exit 2 ;; esac
    id "$USER_NAME" >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin "$USER_NAME"
    install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
    printf 'restrict,port-forwarding,permitopen="127.0.0.1:9223",command="/usr/sbin/nologin" %s\n' "$pub" > "/home/$USER_NAME/.ssh/authorized_keys"
    chown "$USER_NAME:$USER_NAME" "/home/$USER_NAME/.ssh/authorized_keys"
    chmod 0600 "/home/$USER_NAME/.ssh/authorized_keys"
    echo "installed $USER_NAME"
    ;;
  remove)
    userdel -r "$USER_NAME" || true
    ;;
  *) echo "usage: $0 install '<public key>' | remove" >&2; exit 2 ;;
esac
