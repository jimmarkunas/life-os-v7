"""The one live read the Action Inbox allows: Concord's health, over the private endpoint's `concord1` request.

It reads only a count and a fixed code: the Oracle helper (lifeos/edge/concord/concord_helper.py) runs the Concord
CLI's read-only listing and answers {"ok", "code", "messages", "at"}; no message text reaches this process. It refuses
unless "concord" is in `sources.LIVE_READ_SOURCES` (removing it is the rollback). No write is reachable; a test scans
the file.
"""
import json

from lifeos.inbox import sources
from lifeos.inbox.model import InboxError

CODES = ("CONCORD_BAD_FRAME", "CONCORD_CLI_MISSING", "CONCORD_READ_FAILED", "CONCORD_TIMEOUT", "CONCORD_BAD_OUTPUT")
UNREACHABLE = "CONCORD_UNREACHABLE"
REQUEST = b"concord1\n"


def parse(raw):
    """Returns the helper's one JSON line as {"ok", "code", "messages", "at"}, or the UNREACHABLE shape for anything
    else.
    """
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        doc = None
    if (not isinstance(doc, dict) or set(doc) != {"ok", "code", "messages", "at"} or not isinstance(doc["ok"], bool) or isinstance(doc["messages"], bool)
            or not isinstance(doc["messages"], int) or isinstance(doc["at"], bool) or not isinstance(doc["at"], int) or doc["code"] not in ("",) + CODES or bool(doc["code"]) == doc["ok"]):
        return {"ok": False, "code": UNREACHABLE, "messages": 0, "at": 0}
    return doc


def health(host, fetch, allowed=None):
    """Returns the parsed reading. `fetch(host, request)` returns raw reply bytes and may raise OSError (then
    UNREACHABLE). Raises InboxError("live_reads_not_authorized") when Concord is not on the allow-list.
    """
    if not (sources.live_allowed("concord") if allowed is None else "concord" in allowed):
        raise InboxError("live_reads_not_authorized")
    try:
        return parse(fetch(host, REQUEST))
    except OSError:
        return {"ok": False, "code": UNREACHABLE, "messages": 0, "at": 0}
