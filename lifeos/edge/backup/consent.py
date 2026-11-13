"""LIFE-129: the one-time Google sign-in for the BACKUP credential. Unlike lifeos/platform/google_consent.py (the full `drive` scope the résumé feature needs), this asks for
`drive.file` ONLY: the backup job can see and delete just the files it made itself, never the rest of Jim's Drive, and a reply that grants anything wider is refused. The sign-in itself
(PKCE link, loopback catcher, a credential written only to a pipe, never to a terminal) is google_consent's, run with this scope. It runs on Jim's Mac, where the browser is, once:
  python3 -m lifeos.edge.backup.consent --port 8765 | ssh opc@lifeos-edge-01 "sudo bash /opt/lifeos-offsite/install-offsite.sh credential"
"""
import functools
import sys

from lifeos.platform import google_consent as base

SCOPE = "https://www.googleapis.com/auth/drive.file"          # the same value as offsite_backup.SCOPE (a test holds them equal)
auth_url = functools.partial(base.auth_url, scope=SCOPE)
exchange = functools.partial(base.exchange, scope=SCOPE, exact=True)
main = functools.partial(base.main, scope=SCOPE, exact=True, sink="Edge installer")

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
