"""The Weekly Review's link to the governed Oracle Drive reader: one `drive1 <days>` line over the tailnet, one JSON
line back.

The runner holds no Google credential. Anything but a clean answer is a fixed code raised as OSError, which the Drive
lane reports as its Degraded reason."""
import json
import socket

PORT, WAIT, MAX_REPLY = 47341, 70, 4096
KEYS = {"new", "changed", "unfiled", "duplicates", "missing_metadata"}
CODES = {"DRIVE_BAD_FRAME", "DRIVE_NOT_CONFIGURED", "DRIVE_WRONG_ACCOUNT", "DRIVE_CONSENT_REVOKED", "DRIVE_UNAVAILABLE"}


def fetch(host, days, port=PORT, connect=socket.create_connection):
    data = b""
    with connect((host, port), timeout=WAIT) as s:
        s.sendall(f"drive1 {int(days)}\n".encode())
        while len(data) <= MAX_REPLY:
            chunk = s.recv(MAX_REPLY + 1 - len(data))
            if not chunk:
                break
            data += chunk
    return data


def reader(host, days=7, fetcher=fetch):
    """-> a callable for weekly_review returning the Drive counts, or raising OSError with a fixed code."""
    def read():
        if not (host or "").strip():
            raise OSError("DRIVE_HOST_NOT_SET")
        try:
            data = fetcher(host.strip(), days)
        except (OSError, ValueError):
            raise OSError("DRIVE_ORACLE_UNREACHABLE") from None
        if not data:
            raise OSError("DRIVE_ORACLE_REFUSED")
        try:
            doc = json.loads(data)
        except ValueError:
            raise OSError("DRIVE_REPLY_OFF_CONTRACT") from None
        if not isinstance(doc, dict):
            raise OSError("DRIVE_REPLY_OFF_CONTRACT")
        if doc.get("ok") is not True:
            code = doc.get("code")
            raise OSError(code if code in CODES else "DRIVE_REPLY_OFF_CONTRACT")
        if not all(type(doc.get(k)) is int and doc[k] >= 0 for k in KEYS):
            raise OSError("DRIVE_REPLY_OFF_CONTRACT")
        return {k: doc[k] for k in KEYS} | {"complete": doc.get("complete") is True}
    return read
