#!/usr/bin/python3 -I
"""Spend guard in front of every Anthropic call Honcho makes, plus a free path to local embeddings. POST /v1/messages
is metered then forwarded to api.anthropic.com with the real key; /v1/messages/count_tokens is forwarded unmetered;
/v1/embeddings goes to the local Ollama; GET /health reports spend; anything else is 404.

Before forwarding, the worst-case cost (1 input token per 3 body bytes plus max_tokens, at the PRICES tier for its
size) is reserved; if spent + reservations + this one would pass CAP_USD (default 25) the request is refused with HTTP
400. When the reply ends the reservation becomes the actual usage (a reply that breaks off keeps the full
reservation). A STOP file next to the ledger refuses everything. The cap is per window (the 5th at 6:00 PM CT to the
next 5th): the first request after the boundary archives the old window to windows.jsonl and starts at zero.

Fail closed: a ledger that cannot be read or written, or is missing while the audit file shows spend, means every
Anthropic request is refused (embeddings stay free) and no new window resets it. METER_STOP_AFTER (optional, UTC) is a
hard stop. The first refusal in a window leaves an EXHAUSTED file for one alert per window. The real key arrives as a
systemd credential; keys and bodies are never logged. Stdlib only.
"""
import http.client
import http.server
import json
import os
import socketserver
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CAP_USD = float(os.environ.get("METER_CAP_USD", "25"))
STATE_DIR = os.environ.get("METER_STATE_DIR", "/var/lib/lifeos-honcho-meter")
BIND = os.environ.get("METER_BIND", "172.28.0.1")
PORT = int(os.environ.get("METER_PORT", "8081"))
OLLAMA = ("127.0.0.1", 11434)
UPSTREAM = "api.anthropic.com"
KEY_NAME = "honcho_proof_anthropic_api_key"
PRICES = {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50}
# the over-100,000-token tier
LONG_PRICES = {"input": 0.50, "cache_write": 1.00, "cache_read": 0.05, "output": 2.50}
# input tokens (new + cache write + cache read) above which the long tier applies
LONG_AT = 100_000
BYTES_PER_TOKEN = 3
MAX_BODY = 4_000_000
DEFAULT_MAX_TOKENS = 4096


def _epoch(text):
    import calendar
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


STOP_AFTER = _epoch(os.environ["METER_STOP_AFTER"]) if os.environ.get("METER_STOP_AFTER") else None
CENTRAL = ZoneInfo("America/Chicago")


def window(epoch):
    """-> (start, end) epochs of the window holding `epoch`: the 5th at 6:00 PM CT (America/Chicago) to the next. A
    test pins that this, credit_window.py and the report reader agree.
    """
    now = datetime.fromtimestamp(epoch, CENTRAL)
    year, month = now.year, now.month
    if now < datetime(year, month, 5, 18, tzinfo=CENTRAL):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    nyear, nmonth = (year + 1, 1) if month == 12 else (year, month + 1)
    return int(datetime(year, month, 5, 18, tzinfo=CENTRAL).timestamp()), int(datetime(nyear, nmonth, 5, 18, tzinfo=CENTRAL).timestamp())


def table(input_tokens):
    return LONG_PRICES if input_tokens > LONG_AT else PRICES


def usd(tokens, kind, prices=PRICES):
    return tokens * prices[kind] / 1_000_000


def estimate(body):
    try:
        doc = json.loads(body)
        max_tokens = int(doc.get("max_tokens") or DEFAULT_MAX_TOKENS)
    except (ValueError, TypeError, AttributeError):
        max_tokens = DEFAULT_MAX_TOKENS
    tokens_in = len(body) // BYTES_PER_TOKEN + 1
    prices = table(tokens_in)
    return usd(tokens_in, "input", prices) + usd(max_tokens, "output", prices), tokens_in, max_tokens


def usage_cost(usage):
    u = usage or {}
    new, written, read, out = (int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
    prices = table(new + written + read)
    return usd(new, "input", prices) + usd(written, "cache_write", prices) + usd(read, "cache_read", prices) + usd(out, "output", prices)


class Ledger:

    def __init__(self, state_dir=STATE_DIR, cap=CAP_USD, clock=time.time):
        self.dir, self.cap, self.clock = state_dir, cap, clock
        self.lock = threading.RLock()
        self.path, self.audit_path, self.stop_path, self.windows_path = (os.path.join(state_dir, n) for n in ("ledger.json", "audit.jsonl", "STOP", "windows.jsonl"))
        os.makedirs(state_dir, mode=0o750, exist_ok=True)
        self.spent, self.reserved, self.calls = 0.0, 0.0, 0
        self.start, self.end = window(self.clock())
        # why the budget cannot be verified ("" when it can): metered requests are refused while set
        self.unverified = ""
        try:
            with open(self.path) as handle:
                saved = json.load(handle)
            self.spent, self.calls = float(saved["spent_usd"]), int(saved["calls"])
            if saved.get("window_start") is not None:
                self.start = int(saved["window_start"])
                self.end = window(self.start)[1]
        except FileNotFoundError:
            if os.path.exists(self.audit_path) and os.path.getsize(self.audit_path) > 0:
                self.unverified = "ledger missing while the audit file shows past spend"
        except (OSError, ValueError, KeyError, TypeError):
            self.unverified = "ledger unreadable"
        self.roll()

    def roll(self):
        """Start the current window when the saved one has ended (archive it, zero spend). Never on an unverifiable
        ledger.
        """
        with self.lock:
            if self.unverified or self.clock() < self.end:
                return
            try:
                with open(self.windows_path, "a") as handle:
                    handle.write(json.dumps({"start": self.start, "end": self.end, "spent_usd": round(self.spent, 6), "calls": self.calls, "cap_usd": self.cap}, sort_keys=True) + "\n")
            except OSError:
                pass
            self.start, self.end = window(self.clock())
            self.spent, self.calls = 0.0, 0
            try:
                self.save()
            except OSError:
                self.unverified = "ledger unwritable"

    def why_blocked(self):
        """-> "" when a metered request may be considered, else the fixed refusal reason (STOP file, unverifiable
        budget, hard stop).
        """
        self.roll()
        if self.stopped():
            return "cap reached or stopped"
        if self.unverified:
            return "budget cannot be verified: " + self.unverified
        if STOP_AFTER is not None and self.clock() >= STOP_AFTER:
            return "promotional credits ended: spend would be paid"
        return ""

    def mark_exhausted(self, why):
        """Leave one EXHAUSTED file the first time a request is refused for budget reasons in a window (one alert per
        window).
        """
        path = os.path.join(self.dir, "EXHAUSTED")
        try:
            with open(path) as handle:
                if json.load(handle).get("window") == self.start:
                    return
        except (OSError, ValueError, AttributeError):
            pass
        try:
            with open(path, "w") as handle:
                json.dump({"reason": why, "window": self.start, "epoch": int(self.clock())}, handle)
        except OSError:
            pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump({"spent_usd": round(self.spent, 6), "calls": self.calls, "cap_usd": self.cap, "window_start": self.start, "window_end": self.end, "saved": int(self.clock())}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def stopped(self):
        return os.path.lexists(self.stop_path)

    def reserve(self, worst):
        with self.lock:
            if self.why_blocked():
                return False
            if self.spent + self.reserved + worst > self.cap:
                return False
            self.reserved += worst
            return True

    def settle(self, worst, actual, route, status, usage=None, broke=False):
        with self.lock:
            self.roll()
            self.reserved = max(0.0, self.reserved - worst)
            cost = worst if broke else actual
            self.spent += cost
            self.calls += 1
            try:
                self.save()
            except OSError:
                self.unverified = "ledger unwritable"
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

    def log_message(self, *args):
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
            blocked = self.ledger.why_blocked() or None
            self.reply(200, {"ok": True, "spent_usd": round(self.ledger.spent, 6), "cap_usd": self.ledger.cap, "stopped": self.ledger.stopped(), "blocked": blocked,
                             "window": {"start": datetime.fromtimestamp(self.ledger.start, CENTRAL).isoformat(), "end": datetime.fromtimestamp(self.ledger.end, CENTRAL).isoformat()}})
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
            broke = not complete(raw, streaming)
        except (OSError, http.client.HTTPException):
            if status == 502:
                try:
                    self.reply(502, {"error": "upstream_unreachable"})
                except OSError:
                    pass
        finally:
            if metered:
                # an error reply carries no usage and no charge; a reply that broke off keeps its full reservation
                self.ledger.settle(worst, usage_cost(usage) if status < 400 else 0.0, path, status, usage, broke=broke and status < 400)


def complete(raw, streaming):
    if streaming:
        return b'"message_stop"' in raw
    try:
        json.loads(raw)
        return True
    except ValueError:
        return False


def parse_usage(raw, streaming):
    """-> the reply's usage: the JSON body's `usage`, or for a stream message_start usage with the final
    output_tokens.
    """
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
