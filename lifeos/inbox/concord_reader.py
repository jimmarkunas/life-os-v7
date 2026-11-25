"""LIFE-977: the one live read the Action Inbox allows, Concord's health, over the private endpoint's `concord1` request (Jim, Nov 25 2026 10:58 AM CT, "Concord YES").

It reads nothing but a count and a fixed code: the helper on Oracle (lifeos/edge/concord/concord_helper.py) runs the Concord CLI's read-only listing as the `concord` user and answers
{"ok", "code", "messages", "at"}. No message text, id, name or path reaches this process, so none can be stored or logged. It refuses unless "concord" is on `sources.LIVE_READ_SOURCES`; removing it
from that set is the rollback (the System table's Concord row then returns to `not connected`). No send, reply or any other write is reachable from this file; a test scans it."""
import json

from lifeos.inbox import sources
from lifeos.inbox.model import InboxError

CODES = ("CONCORD_BAD_FRAME", "CONCORD_CLI_MISSING", "CONCORD_READ_FAILED", "CONCORD_TIMEOUT", "CONCORD_BAD_OUTPUT")
UNREACHABLE = "CONCORD_UNREACHABLE"                      # the endpoint itself could not be asked (this run's problem, not Concord's)
REQUEST = b"concord1\n"


def parse(raw):
    """-> {"ok": bool, "code": fixed word or "", "messages": int, "at": epoch} from the helper's one JSON line, or the UNREACHABLE shape when it is anything else."""
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        doc = None
    if (not isinstance(doc, dict) or set(doc) != {"ok", "code", "messages", "at"} or not isinstance(doc["ok"], bool) or isinstance(doc["messages"], bool)
            or not isinstance(doc["messages"], int) or isinstance(doc["at"], bool) or not isinstance(doc["at"], int) or doc["code"] not in ("",) + CODES or bool(doc["code"]) == doc["ok"]):
        return {"ok": False, "code": UNREACHABLE, "messages": 0, "at": 0}
    return doc


def health(host, fetch, allowed=None):
    """-> the parsed reading. `fetch(host, request)` returns the endpoint's raw reply bytes and may raise OSError (then UNREACHABLE). Raises InboxError("live_reads_not_authorized") when Concord is not on the allow-list."""
    if not (sources.live_allowed("concord") if allowed is None else "concord" in allowed):
        raise InboxError("live_reads_not_authorized")
    try:
        return parse(fetch(host, REQUEST))
    except OSError:
        return {"ok": False, "code": UNREACHABLE, "messages": 0, "at": 0}
