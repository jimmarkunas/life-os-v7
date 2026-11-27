"""Google sign-in for the backup credential. Unlike lifeos/platform/google_consent.py (full `drive` scope) this asks only
for `drive.file`, so the backup job sees and deletes only files it made, and a wider grant is refused. The sign-in
(PKCE link, loopback catcher, credential written only to a pipe) is google_consent's. Run once on the Mac:
  python3 -m lifeos.edge.backup.consent --port 8765 | ssh opc@lifeos-edge-01 "sudo bash $INSTALL credential"
  (INSTALL=/opt/lifeos-offsite/install-offsite.sh)
"""

import functools
import sys

from lifeos.platform import google_consent as base

SCOPE = "https://www.googleapis.com/auth/drive.file"  # the same value as offsite_backup.SCOPE (a test holds them equal)
auth_url = functools.partial(base.auth_url, scope=SCOPE)
exchange = functools.partial(base.exchange, scope=SCOPE, exact=True)
main = functools.partial(base.main, scope=SCOPE, exact=True, sink="Edge installer")

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
