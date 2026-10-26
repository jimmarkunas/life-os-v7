"""LIFE-37 Step 2: the V7 side of Oracle's `gran1` request (contract posted in LIFE-37 comment 11235).

Same private endpoint and framing as the Cloud Fit `fit1` verb (TCP 47341 over the runner's tailnet node): no new credential, port or service. One request per
connection: `gran1 <len>\\n` + a JSON body with `op` and a fresh `nonce`; the reply is `gran1 <len>\\n` + JSON with `ok`, `code` and the echoed `nonce`. Ops: sync, list, show,
purge. Nothing here logs a reply: callers get fixed codes, and the transcript JSON only ever goes to the contract check and the private Notion page."""
import json
import secrets
import socket
import time

PORT = 47341
MAX_REQUEST = 4096
MAX_REPLY = 26_000_000              # the contract's own transcript limit is 25,000,000 bytes, plus the envelope
WAIT = 180                          # sync can take a while; Oracle answers or closes
OPS = ("sync", "list", "show", "purge")
CODES = {"GRAN_BAD_FRAME", "GRAN_BAD_OP", "GRAN_NOT_FOUND", "GRAN_SYNC_FAILED", "GRAN_AUTH_EXPIRED", "GRAN_TIMEOUT", "GRAN_OVERSIZE", "GRAN_HASH_MISMATCH"}


class OracleError(RuntimeError):
    """A fixed code only: GRAN_* from Oracle, or one of GRAN_NO_HOST, GRAN_CONNECT_FAILED, GRAN_NO_REPLY, GRAN_REPLY_INVALID, GRAN_NONCE_MISMATCH."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def frame(op, nonce, **fields):
    if op not in OPS:
        raise OracleError("GRAN_BAD_OP")
    body = json.dumps({"op": op, "nonce": nonce, **fields}, separators=(",", ":")).encode()
    if len(body) > MAX_REQUEST:
        raise OracleError("GRAN_BAD_FRAME")
    return b"gran1 %d\n" % len(body) + body


def exchange(host, data, port=PORT, wait=WAIT, sleep=time.sleep):
    """One request, read to the server's close. b"" = closed with no output. A connection that cannot be opened has sent nothing: tried twice."""
    s = None
    for attempt in range(2):
        try:
            s = socket.create_connection((host, port), timeout=15)
            break
        except OSError:
            if attempt == 0:
                sleep(3)
    if s is None:
        raise OracleError("GRAN_CONNECT_FAILED")
    try:
        with s:
            s.settimeout(wait)
            s.sendall(data)
            out = b""
            while len(out) <= MAX_REPLY:
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                out += chunk
            return out
    except TimeoutError:
        raise OracleError("GRAN_TIMEOUT") from None
    except OSError:
        return b""


def call(host, op, send=exchange, **fields):
    """-> the reply's dict on `ok`. Raises OracleError with a fixed code on anything else: no host, no reply, a reply that is not as contracted, a nonce that was
    not echoed, or Oracle's own refusal (its code is passed through only when it is on the contract's list)."""
    if not host:
        raise OracleError("GRAN_NO_HOST")
    nonce = secrets.token_hex(16)
    raw = send(host, frame(op, nonce, **fields))
    if not raw:
        raise OracleError("GRAN_NO_REPLY")
    if len(raw) > MAX_REPLY:
        raise OracleError("GRAN_OVERSIZE")
    head, _, body = raw.partition(b"\n")
    try:
        verb, size = head.decode("ascii").split(" ")
        assert verb == "gran1" and int(size) == len(body)
        reply = json.loads(body)
        assert isinstance(reply, dict) and isinstance(reply.get("ok"), bool)
    except (ValueError, AssertionError, UnicodeDecodeError):
        raise OracleError("GRAN_REPLY_INVALID") from None
    if reply.get("nonce") != nonce:
        raise OracleError("GRAN_NONCE_MISMATCH")
    if not reply["ok"]:
        raise OracleError(reply.get("code") if reply.get("code") in CODES else "GRAN_REPLY_INVALID")
    return reply
