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

Scope budget (off unless an operator writes scope.json next to the ledger): one hard dollar cap, in whole nano-dollars, across all metered calls, reserved together with the monthly cap; STOP still comes first. While a scope
stands a request is forwarded only when its worst case is proven from the request itself: a priced model, an explicit max_tokens inside the scope limit (the API never returns more output), allowlisted fields, text-only
content (SCOPE_KEYS, SCOPE_BLOCKS), no beta header, and an input bound of one token per body byte plus SCOPE_OVERHEAD_TOKENS (assumption A1: no token covers less than one byte and the API's own framing adds less than that),
priced at the dearest input class and the tier the bound falls in. Anything else is refused before forwarding.
Every reservation is saved to the ledger before the request is forwarded; after a restart each open reservation is charged in full to the month and the scope, because the reply may have been billed. A reply with no usage,
a reply that broke off, or a connection that failed after it opened keeps its full reservation; a reply that cost more than its reservation marks the scope breached and refuses everything.
The scope's spend never resets (it survives restarts and monthly windows). Removing or editing scope.json, or letting it expire, while the scope is open refuses every metered request. A file SCOPE-CLOSE naming the scope
id closes it, and only when this proxy has nothing in flight (the operator first confirms Honcho has nothing queued; the proxy cannot see that). Closing archives one line in scopes.jsonl; a closed id is never reused.
Embeddings stay free and unscoped.
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
SCOPE_FILE, SCOPE_CLOSE = "scope.json", "SCOPE-CLOSE"
SCOPE_OVERHEAD_TOKENS = 4096                                                                 # assumption A1: the API's own framing (role markers, the tool-use preamble) adds fewer tokens than this
SCOPE_KEYS = frozenset({"model", "max_tokens", "messages", "system", "temperature", "top_p", "top_k", "stop_sequences", "stream", "metadata", "tools", "tool_choice", "thinking"})
SCOPE_BLOCKS = frozenset({"text", "tool_use", "tool_result"})                                # content kinds whose tokens cannot outnumber their bytes (no images, documents or server tools)
SCOPE_CONFIG = ("id", "cap_usd", "expires", "models", "max_tokens")
NANO = 1_000_000_000


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


def nano_rate(kind, prices=PRICES):
    """-> whole nano-dollars per token (a USD-per-million price times 1000: every published price is a whole number of them)."""
    return round(prices[kind] * 1000)


def usage_nano(usage):
    """-> the integer nano-dollar twin of usage_cost."""
    u = usage or {}
    new, written, read, out = (int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
    prices = table(new + written + read)
    return new * nano_rate("input", prices) + written * nano_rate("cache_write", prices) + read * nano_rate("cache_read", prices) + out * nano_rate("output", prices)


def _text_only(content):
    """True when message or system content is a string or a list of text, tool_use and tool_result blocks (tool_result content checked the same way)."""
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in SCOPE_BLOCKS:
            return False
        if block["type"] == "tool_result" and "content" in block and not _text_only(block["content"]):
            return False
    return True


def scope_bound(body, config, beta=False):
    """-> (worst-case nano-dollars, "") for a /v1/messages body under a scope, or (0, why) when the worst case cannot be proven from the request: nothing is forwarded then.
    Output: max_tokens (the API never returns more). Input: one token per body byte plus SCOPE_OVERHEAD_TOKENS (assumption A1), at the dearest input-side class and the tier that bound falls in."""
    if beta:
        return 0, "scope: beta features are not priced"
    try:
        doc = json.loads(body)
    except ValueError:
        return 0, "scope: body is not JSON"
    if not isinstance(doc, dict) or not SCOPE_KEYS.issuperset(doc):
        return 0, "scope: unsupported request field"
    if doc.get("model") not in config["models"]:
        return 0, "scope: model is not priced"
    max_tokens = doc.get("max_tokens")
    if type(max_tokens) is not int or not 1 <= max_tokens <= config["max_tokens"]:
        return 0, "scope: max_tokens missing or over the scope limit"
    messages = doc.get("messages")
    if not isinstance(messages, list) or not all(isinstance(m, dict) and _text_only(m.get("content")) for m in messages) or not _text_only(doc.get("system", "")):
        return 0, "scope: content is not text"
    tools = doc.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(t, dict) or t.get("type") not in (None, "custom") for t in tools):
        return 0, "scope: server tools are not priced"
    tokens = len(body) + SCOPE_OVERHEAD_TOKENS
    prices = table(tokens)
    return tokens * max(nano_rate("input", prices), nano_rate("cache_write", prices)) + max_tokens * nano_rate("output", prices), ""


class Ledger:

    def __init__(self, state_dir=STATE_DIR, cap=CAP_USD, clock=time.time):
        self.dir, self.cap, self.clock = state_dir, cap, clock
        self.lock = threading.RLock()
        self.path, self.audit_path, self.stop_path, self.windows_path = (os.path.join(state_dir, n) for n in ("ledger.json", "audit.jsonl", "STOP", "windows.jsonl"))
        os.makedirs(state_dir, mode=0o750, exist_ok=True)
        self.spent, self.reserved, self.calls = 0.0, 0.0, 0
        self.inflight, self.scope, self.closed, self.rid = {}, None, [], 0          # reservations saved before forwarding; the open scope (frozen config, spend, state); closed scope ids
        self.scopes_path = os.path.join(state_dir, "scopes.jsonl")
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
            self.inflight = {int(k): {"worst": float(v["worst"]), "nano": int(v["nano"])} for k, v in (saved.get("inflight") or {}).items()}
            self.scope, self.closed, self.rid = saved.get("scope"), list(saved.get("closed_scopes") or []), int(saved.get("rid") or 0)
            if self.scope is not None and not (isinstance(self.scope, dict) and {"config", "spent_nano", "state"} <= set(self.scope)):
                raise ValueError("scope")
        except FileNotFoundError:
            if os.path.exists(self.audit_path) and os.path.getsize(self.audit_path) > 0:
                self.unverified = "ledger missing while the audit file shows past spend"
        except (OSError, ValueError, KeyError, TypeError):
            self.unverified = "ledger unreadable"
        self.roll()
        self.recover()

    def recover(self):
        """After a restart every reservation still open may have been billed: charge each in full to the month and to its scope, save, and leave one audit line per reservation. Nothing is released."""
        with self.lock:
            if self.unverified or not self.inflight:
                return
            for rid, held in sorted(self.inflight.items()):
                self.spent += held["worst"]
                self.calls += 1
                if self.scope is not None and held["nano"]:
                    self.scope["spent_nano"] += held["nano"]
                try:
                    with open(self.audit_path, "a") as handle:
                        handle.write(json.dumps({"epoch": int(self.clock()), "route": "/v1/messages", "status": 0, "recovered": True, "cost_usd": round(held["worst"], 6), "spent_usd": round(self.spent, 6)}, sort_keys=True) + "\n")
                except OSError:
                    self.unverified = self.unverified or "audit unwritable"
            self.inflight, self.reserved = {}, 0.0
            try:
                self.save()
            except OSError:
                self.unverified = "ledger unwritable"

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
        return self.scope_block()

    def scope_file(self):
        """-> (config dict, "") from scope.json, (None, "") when there is none, (None, why) when it is unreadable or invalid."""
        try:
            with open(os.path.join(self.dir, SCOPE_FILE)) as handle:
                doc = json.load(handle)
            config = {"id": str(doc["id"]), "cap_usd": float(doc["cap_usd"]), "expires": int(doc["expires"]), "models": sorted(str(m) for m in doc["models"]), "max_tokens": int(doc["max_tokens"])}
            if not config["id"] or config["cap_usd"] <= 0 or config["max_tokens"] < 1 or not config["models"] or set(doc) - set(SCOPE_CONFIG):
                raise ValueError("scope")
            config["cap_nano"] = round(config["cap_usd"] * NANO)
            return config, ""
        except FileNotFoundError:
            return None, ""
        except (OSError, ValueError, KeyError, TypeError):
            return None, "scope file invalid"

    def scope_block(self):
        """-> "" when no scope stands or the open one may spend, else why nothing is forwarded: a changed, removed, expired, breached or unreadable scope never reopens spend. Also opens a scope and closes a drained one."""
        with self.lock:
            config, bad = self.scope_file()
            if self.scope is None:
                if bad:
                    return bad
                if config is None or config["id"] in self.closed:
                    return ""
                self.scope = {"config": config, "spent_nano": 0, "state": "open"}
                try:
                    self.save()
                except OSError:
                    self.scope = None
                    self.unverified = "ledger unwritable"
                    return "budget cannot be verified: ledger unwritable"
            held = self.scope["config"]
            if os.path.lexists(os.path.join(self.dir, SCOPE_CLOSE)):
                return self.try_close()
            if bad or config is None:
                return "scope removed or unreadable before it was closed"
            if config != held:
                return "scope file changed before it was closed"
            if self.scope["state"] != "open":
                return "scope " + self.scope["state"]
            if self.clock() >= held["expires"]:
                return "scope expired and not closed"
            return ""

    def try_close(self):
        """Close the open scope when SCOPE-CLOSE names its id and nothing is in flight; otherwise keep refusing. -> "" once closed, else the reason."""
        try:
            with open(os.path.join(self.dir, SCOPE_CLOSE)) as handle:
                named = str(json.load(handle).get("scope"))
        except (OSError, ValueError, AttributeError):
            return "scope close file unreadable"
        held = self.scope["config"]
        if named != held["id"]:
            return "scope close names another scope"
        if self.inflight:
            return "scope close waits for requests in flight"
        row = {"id": held["id"], "cap_usd": held["cap_usd"], "spent_nano": self.scope["spent_nano"], "state": self.scope["state"], "closed": int(self.clock())}
        try:
            with open(self.scopes_path, "a") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
            self.closed.append(held["id"])
            self.scope = None
            self.save()
            os.remove(os.path.join(self.dir, SCOPE_CLOSE))
        except OSError:
            self.unverified = "ledger unwritable"
            return "budget cannot be verified: ledger unwritable"
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
            json.dump({"spent_usd": round(self.spent, 6), "calls": self.calls, "cap_usd": self.cap, "window_start": self.start, "window_end": self.end, "saved": int(self.clock()),
                       "inflight": {str(k): v for k, v in self.inflight.items()}, "scope": self.scope, "closed_scopes": self.closed, "rid": self.rid}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def stopped(self):
        return os.path.lexists(self.stop_path)

    def reserve(self, worst, nano=0):
        """-> a reservation number (truthy) and hold `worst` USD, or False (nothing held) when the cap, the STOP file or a scope forbids this request. The reservation is saved before this returns: a crash after it is still counted.
        A request with no scope proof (nano=0) is refused while a scope stands."""
        with self.lock:
            if self.why_blocked():
                return False
            if self.scope is not None and not nano:
                return False
            if self.spent + self.reserved + worst > self.cap:
                return False
            if self.scope is not None:
                reserved_nano = sum(h["nano"] for h in self.inflight.values())
                if self.scope["spent_nano"] + reserved_nano + nano > self.scope["config"]["cap_nano"]:
                    return False
            self.rid += 1
            self.inflight[self.rid] = {"worst": worst, "nano": nano}
            self.reserved += worst
            try:
                self.save()
            except OSError:
                del self.inflight[self.rid]
                self.reserved = max(0.0, self.reserved - worst)
                self.unverified = "ledger unwritable"
                return False
            return self.rid

    def admit(self, body, beta=False):
        """-> (reservation, worst USD, "") for a /v1/messages request, or (False, 0.0, why). Without a scope the old estimate; with one, the proven bound of scope_bound or a refusal."""
        with self.lock:
            blocked = self.why_blocked()
            if blocked:
                return False, 0.0, blocked
            if self.scope is None:
                worst = estimate(body)[0]
                return self.reserve(worst), worst, "cap reached or stopped"
            nano, why = scope_bound(body, self.scope["config"], beta)
            if why:
                return False, 0.0, why
            rid = self.reserve(nano / NANO, nano)
            return rid, nano / NANO, "cap reached or stopped"

    def settle(self, worst, actual, route, status, usage=None, broke=False, rid=None):
        """Replace the reservation with the actual cost (or keep the whole reservation when the reply broke off) and append one audit line. Under a scope a reply with no output count counts as broken, and a cost above the reservation marks the scope breached."""
        with self.lock:
            self.roll()
            held = self.inflight.pop(rid, None) if rid else None
            nano = held["nano"] if held else 0
            if held:
                worst = held["worst"]
            self.reserved = max(0.0, self.reserved - worst)
            scoped = self.scope is not None and nano > 0
            if scoped and not broke and status < 400 and "output_tokens" not in (usage or {}):
                broke = True
            cost = worst if broke else actual
            self.spent += cost
            self.calls += 1
            if scoped:
                cost_nano = nano if broke else usage_nano(usage) if status < 400 else 0
                self.scope["spent_nano"] += cost_nano
                if cost_nano > nano:
                    self.scope["state"] = "breached"
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
            self.reply(200, {**({"scope": scope_view(self.ledger.scope)} if self.ledger.scope is not None else {}), "ok": True, "spent_usd": round(self.ledger.spent, 6), "cap_usd": self.ledger.cap, "stopped": self.ledger.stopped(), "blocked": blocked,
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
            rid, worst, why = self.ledger.admit(body, bool(self.headers.get("anthropic-beta")))
            if not rid:
                return self.refuse(path, why)
            return self.forward_anthropic(path, body, worst, metered=True, rid=rid)
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

    def forward_anthropic(self, path, body, worst, metered, rid=None):
        headers = {"x-api-key": self.key, "anthropic-version": self.headers.get("anthropic-version") or "2023-06-01", "content-type": "application/json"}
        if self.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = self.headers["anthropic-beta"]
        usage, broke, status, sent = {}, True, 502, False
        try:
            conn = http.client.HTTPSConnection(UPSTREAM, timeout=600)
            conn.connect()
            sent = True                                                   # from here the request may reach Anthropic: a failure is ambiguous, not free
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
                # an error reply (status >= 400) carries no usage and no charge; a reply that broke off keeps its full reservation
                ambiguous = sent and status == 502 and self.ledger.scope is not None                 # under a scope a failure after connecting keeps the whole reservation
                self.ledger.settle(worst, usage_cost(usage) if status < 400 else 0.0, path, status, usage, broke=(broke and status < 400) or ambiguous, rid=rid)


def scope_view(scope):
    """-> the scope facts /health may show: id, cap, spend and state (dollars and words only), or None."""
    if scope is None:
        return None
    return {"id": scope["config"]["id"], "cap_usd": scope["config"]["cap_usd"], "spent_usd": scope["spent_nano"] / NANO, "state": scope["state"]}


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
