#!/usr/bin/python3 -I
"""Read-only Drive weekly-count helper on Oracle for the Weekly Review; adds no credential (its unit loads the Drive
credentials Oracle already holds). One process per connection as `buzz-mcp` (socket activation, stdin/stdout); the
only caller is lifeos/imessage/alerts_endpoint.py. Request `drive1 <days 1..14>\\n`; one JSON reply line of counts
only ({"ok": true, "new", "changed", "unfiled", "duplicates", "missing_metadata", "complete"}) or {"ok": false,
"code": <fixed word>}. It lists file metadata through google_read.Google (drive.readonly, GET only, wrong-account
check) and never reads a file's content, name, id or link. Codes: DRIVE_BAD_FRAME, DRIVE_NOT_CONFIGURED,
DRIVE_WRONG_ACCOUNT, DRIVE_CONSENT_REVOKED, DRIVE_UNAVAILABLE. Stdlib only.
"""

import json
import os
import re
import sys

try:
    from lifeos.platform import drive_week, google_read
except ImportError:  # on Oracle both are installed beside this file (lifeos/platform/drive_week.py and google_read.py)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import drive_week
    import google_read

ACCOUNT = "personal"
REQUEST = re.compile(rb"^drive1 ([1-9]|1[0-4])\n$")
CODES = {
    "wrong_account": "DRIVE_WRONG_ACCOUNT",
    "consent_revoked": "DRIVE_CONSENT_REVOKED",
    "not_configured": "DRIVE_NOT_CONFIGURED",
    "scope_missing": "DRIVE_CONSENT_REVOKED",
}


def creds_from(directory):
    def read(name):
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    client_id, client_secret, refresh = (
        read("google_read_client_id"),
        read("google_read_client_secret"),
        read("google_read_personal_drive_refresh"),
    )
    if not (client_id and client_secret and refresh):
        return None
    return lambda account, service: (
        {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh}
        if (account, service) == (ACCOUNT, "drive")
        else None
    )


def answer(raw, creds, opener=None):
    match = REQUEST.match(raw)
    if not match:
        return {"ok": False, "code": "DRIVE_BAD_FRAME"}
    if creds is None:
        return {"ok": False, "code": "DRIVE_NOT_CONFIGURED"}
    try:
        google = google_read.Google(creds, **({"opener": opener} if opener else {}))
        return {"ok": True, **drive_week.summary(google, ACCOUNT, int(match.group(1)))}
    except google_read.GoogleError as error:
        return {"ok": False, "code": CODES.get(getattr(error, "reason", ""), "DRIVE_UNAVAILABLE")}
    except Exception:  # noqa: BLE001
        return {"ok": False, "code": "DRIVE_UNAVAILABLE"}


def main(stdin=None, stdout=None, environ=os.environ, opener=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    raw = stdin.readline(32)
    directory = environ.get("CREDENTIALS_DIRECTORY") or ""
    reply = answer(raw, creds_from(directory) if directory else None, opener)
    stdout.write(json.dumps(reply, separators=(",", ":"), sort_keys=True).encode() + b"\n")
    stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
