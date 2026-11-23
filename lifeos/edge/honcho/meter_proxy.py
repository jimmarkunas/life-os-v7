#!/usr/bin/python3 -I
"""LIFE-889: the spend guard in front of EVERY Anthropic call Honcho makes (API, deriver, summaries, dreams), plus a free path to the local embeddings model.

Honcho is pointed here (its per-feature base URL override), so no model call can reach Anthropic without passing this program:

    POST /v1/messages              metered, then forwarded to https://api.anthropic.com/v1/messages with the real key (Honcho holds only a dummy key)
    POST /v1/messages/count_tokens forwarded unmetered (it is free; refused too once the cap or the STOP file stands)
    POST /v1/embeddings            forwarded to the local Ollama (http://127.0.0.1:11434/v1/embeddings): free, never reaches Anthropic
    GET  /health                   {"ok": true, "spent_usd": ..., "cap_usd": ...}
    anything else                  404

The cap: before a request is forwarded, its WORST-CASE cost (input tokens estimated at one token per 3 bytes of body, plus max_tokens of output, both at the
highest published price: the over-100,000-token Haiku 5.5 rates) is reserved against the ledger; if spent + in-flight reservations + this one would pass CAP_USD
(default 0.90) the request is refused with HTTP 400 and no call is made. When the reply ends, the reservation is replaced by the actual usage (input, cache write,
cache read, output tokens from the reply, at the same highest prices); a reply that breaks off keeps its full reservation. The ledger is saved after every request, so
a restart never forgets spend. A file named STOP next to the ledger refuses everything. Prices are a USD-per-million-token table: PRICES, read from
https://platform.claude.com/docs/en/about-claude/pricing on Nov 21 2026 (Haiku 5.5, over 100,000 tokens: input 0.50, 5m cache write 0.625, 1h cache write 1.00,
cache read 0.05, output 2.50); every token class is charged at the dearest of them (1.00 in, 2.50 out) so an unexpected class can only overcount.

LIFE-867, fail closed: a ledger that exists but cannot be read, a ledger that is missing while the audit file shows past spend, or a ledger that cannot be written, means the budget cannot be
verified, so every Anthropic request is refused (embeddings stay free); so is every request after STOP_AFTER (the promotional credits end Dec 5 2026, 6:00 PM CT = Dec 6 00:00 UTC; after that spend would be
paid, so only Jim's explicit change moves it). Memory is never touched. The first refusal for any of these reasons leaves an EXHAUSTED file next to the ledger (reason and time, nothing else): the hook for the Agent Inbox alert.

The real key arrives as a systemd credential (CREDENTIALS_DIRECTORY/honcho_proof_anthropic_api_key); it is never logged, never put in a reply and never written. No request or reply
body is ever logged: the audit file (JSON lines next to the ledger) holds time, route, status, token counts and cost only. Stdlib only; no shell; no other host is ever contacted."""
import http.client
import http.server
import json
import os
import socketserver
import threading
import time

CAP_USD = float(os.environ.get("METER_CAP_USD", "0.90"))
STATE_DIR = os.environ.get("METER_STATE_DIR", "/var/lib/lifeos-honcho-meter")
BIND = os.environ.get("METER_BIND", "172.28.0.1")
PORT = int(os.environ.get("METER_PORT", "8081"))
OLLAMA = ("127.0.0.1", 11434)
UPSTREAM = "api.anthropic.com"
KEY_NAME = "honcho_proof_anthropic_api_key"
PRICES = {"input": 1.00, "cache_write": 1.00, "cache_read": 1.00, "output": 2.50}          # USD per million tokens: the dearest published class for input-like tokens, the output price
BYTES_PER_TOKEN = 3
MAX_BODY = 4_000_000
DEFAULT_MAX_TOKENS = 4096


def _epoch(text):
    import calendar
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


STOP_AFTER = _epoch(os.environ.get("METER_STOP_AFTER", "2026-12-06T00:00:00Z"))      # the promotional credits' end; paid spend is a decision for Jim, so the proxy stops here unless the unit says otherwise


def usd(tokens, kind):
    return tokens * PRICES[kind] / 1_000_000


def estimate(body):
    """-> (worst-case USD, input-token estimate, max_tokens) for a /v1/messages request body (bytes)."""
    try:
        doc = json.loads(body)
        max_tokens = int(doc.get("max_tokens") or DEFAULT_MAX_TOKENS)
    except (ValueError, TypeError, AttributeError):
        max_tokens = DEFAULT_MAX_TOKENS
    tokens_in = len(body) // BYTES_PER_TOKEN + 1
    return usd(tokens_in, "input") + usd(max_tokens, "output"), tokens_in, max_tokens


def usage_cost(usage):
    """-> USD for a usage object from a reply (any missing class counts as 0)."""
    u = usage or {}
    return (usd(int(u.get("input_tokens") or 0), "input") + usd(int(u.get("cache_creation_input_tokens") or 0), "cache_write")
            + usd(int(u.get("cache_read_input_tokens") or 0), "cache_read") + usd(int(u.get("output_tokens") or 0), "output"))


class Ledger:
    """Spend so far, in-flight reservations, saved atomically after every change."""

    def __init__(self, state_dir=STATE_DIR, cap=CAP_USD, clock=time.time):
        self.dir, self.cap, self.clock = state_dir, cap, clock
        self.lock = threading.Lock()
        self.path, self.audit_path, self.stop_path = (os.path.join(state_dir, n) for n in ("ledger.json", "audit.jsonl", "STOP"))
        os.makedirs(state_dir, mode=0o750, exist_ok=True)
        self.spent, self.reserved, self.calls = 0.0, 0.0, 0
        self.unverified = ""                                                  # why the budget cannot be verified ("" when it can): every metered request is refused while this is set
        try:
            with open(self.path) as handle:
                saved = json.load(handle)
            self.spent, self.calls = float(saved["spent_usd"]), int(saved["calls"])
        except FileNotFoundError:
            if os.path.exists(self.audit_path) and os.path.getsize(self.audit_path) > 0:
                self.unverified = "ledger missing while the audit file shows past spend"
        except (OSError, ValueError, KeyError, TypeError):
            self.unverified = "ledger unreadable"

    def why_blocked(self):
        """-> "" when a metered request may be considered, else the fixed reason it is refused (STOP file, unverifiable budget, credits ended)."""
        if self.stopped():
            return "cap reached or stopped"
        if self.unverified:
            return "budget cannot be verified: " + self.unverified
        if self.clock() >= STOP_AFTER:
            return "promotional credits ended: spend would be paid"
        return ""

    def mark_exhausted(self, why):
        """Leave one EXHAUSTED file (reason and time only) the first time a request is refused for budget reasons; never overwritten, never read by the proxy."""
        path = os.path.join(self.dir, "EXHAUSTED")
        if not os.path.exists(path):
            try:
                with open(path, "w") as handle:
                    json.dump({"reason": why, "epoch": int(self.clock())}, handle)
            except OSError:
                pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump({"spent_usd": round(self.spent, 6), "calls": self.calls, "cap_usd": self.cap, "saved": int(self.clock())}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def stopped(self):
        return os.path.lexists(self.stop_path)

    def reserve(self, worst):
        """-> True and hold `worst` USD, or False (nothing held) when the cap or the STOP file forbids this request."""
        with self.lock:
            if self.why_blocked():
                return False
            if self.spent + self.reserved + worst > self.cap:
                return False
            self.reserved += worst
            return True

    def settle(self, worst, actual, route, status, usage=None, broke=False):
        """Replace the reservation with the actual cost (or keep the whole reservation when the reply broke off) and append one audit line."""
        with self.lock:
            self.reserved = max(0.0, self.reserved - worst)
            cost = worst if broke else actual
            self.spent += cost
            self.calls += 1
            try:
                self.save()
            except OSError:
                self.unverified = "ledger unwritable"                         # spend can no longer be recorded: refuse everything after this reply
            row = {"epoch": int(self.clock()), "route": route, "status": status, "cost_usd": round(cost, 6), "broke": bool(broke), "spent_usd": round(self.spent, 6)}
            row.update({k: int((usage or {}).get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")})
            try:
                with open(self.audit_path, "a") as handle:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
            except OSError:
                self.unverified = self.unverified or "audit unwritable"

    def refuse(self, route, why):
        with self.lock:
            if why != "bad body size":
                self.mark_exhausted(why)
            try:
                with open(self.audit_path, "a") as handle:
                    handle.write(json.dumps({"epoch": int(self.clock()), "route": route, "status": 400, "refused": why, "spent_usd": round(self.spent, 6)}, sort_keys=True) + "\n")
            except OSError:
                pass


class Handler(http.server.BaseHTTPRequestHandler):
    ledger = None
    key = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):                                     # nothing is logged to stderr: bodies and keys must never appear in a journal
        return

    def reply(self, status, doc):
        data = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def refuse(self, route, why):
        self.ledger.refuse(route, why)
        self.reply(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "lifeos meter: " + why}})

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ok": True, "spent_usd": round(self.ledger.spent, 6), "cap_usd": self.ledger.cap, "stopped": self.ledger.stopped(), "blocked": self.ledger.why_blocked() or None})
        else:
            self.reply(404, {"error": "not_found"})

    def body(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            return None
        return self.rfile.read(size)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self.body()
        if body is None:
            return self.refuse(path, "bad body size")
        if path == "/v1/embeddings":
            return self.forward_local(body)
        if path == "/v1/messages/count_tokens":
            if self.ledger.why_blocked() or self.ledger.spent >= self.ledger.cap:
                return self.refuse(path, self.ledger.why_blocked() or "cap reached")
            return self.forward_anthropic(path, body, 0.0, metered=False)
        if path == "/v1/messages":
            worst, _, _ = estimate(body)
            if not self.ledger.reserve(worst):
                return self.refuse(path, self.ledger.why_blocked() or "cap reached or stopped")
            return self.forward_anthropic(path, body, worst, metered=True)
        self.reply(404, {"error": "not_found"})

    def forward_local(self, body):
        try:
            conn = http.client.HTTPConnection(*OLLAMA, timeout=120)
            conn.request("POST", "/v1/embeddings", body, {"Content-Type": "application/json"})
            answer = conn.getresponse()
            data = answer.read()
        except (OSError, http.client.HTTPException):
            return self.reply(502, {"error": "local_embeddings_unreachable"})
        self.send_response(answer.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def forward_anthropic(self, path, body, worst, metered):
        headers = {"x-api-key": self.key, "anthropic-version": self.headers.get("anthropic-version") or "2023-06-01", "content-type": "application/json"}
        if self.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = self.headers["anthropic-beta"]
        usage, broke, status = {}, True, 502
        try:
            conn = http.client.HTTPSConnection(UPSTREAM, timeout=600)
            conn.request("POST", path, body, headers)
            answer = conn.getresponse()
            status = answer.status
            self.send_response(status)
            ctype = answer.getheader("Content-Type") or "application/json"
            self.send_header("Content-Type", ctype)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            streaming = "text/event-stream" in ctype
            raw = b""
            while True:
                chunk = answer.read(4096) if streaming else answer.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                raw += chunk
                if len(raw) > 64_000_000:
                    break
            usage = parse_usage(raw, streaming)
            broke = not complete(raw, streaming)                          # a stream that never reached message_stop, or a body that is not whole JSON, broke off
        except (OSError, http.client.HTTPException):
            if status == 502:
                try:
                    self.reply(502, {"error": "upstream_unreachable"})
                except OSError:
                    pass
        finally:
            if metered:
                # an error reply (status >= 400) carries no usage and no charge; a reply that broke off keeps its full reservation
                self.ledger.settle(worst, usage_cost(usage) if status < 400 else 0.0, path, status, usage, broke=broke and status < 400)


def complete(raw, streaming):
    """True when a reply arrived whole: a JSON body that parses, or a stream that reached its message_stop event."""
    if streaming:
        return b'"message_stop"' in raw
    try:
        json.loads(raw)
        return True
    except ValueError:
        return False


def parse_usage(raw, streaming):
    """-> the usage object of a reply: the JSON body's `usage`, or for a stream the message_start usage with the final message_delta output_tokens."""
    if not streaming:
        try:
            return json.loads(raw).get("usage") or {}
        except (ValueError, AttributeError):
            return {}
    usage = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if event.get("type") == "message_start":
            usage.update((event.get("message") or {}).get("usage") or {})
        elif event.get("type") == "message_delta":
            usage.update({k: v for k, v in (event.get("usage") or {}).items() if v is not None})
    return usage


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def read_key():
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    with open(os.path.join(directory, KEY_NAME)) as handle:
        return handle.read().strip()


def main():
    Handler.key = read_key()
    Handler.ledger = Ledger()
    Server((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
