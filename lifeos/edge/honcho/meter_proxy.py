#!/usr/bin/python3 -I
"""Spend guard in front of every Anthropic call Honcho makes, plus a free path to local embeddings.

POST /v1/messages is metered, then forwarded to api.anthropic.com with the real key; /v1/messages/count_tokens is
forwarded unmetered; /v1/embeddings goes to the local Ollama; GET /health reports spend; anything else is 404.

Before forwarding, every metered request passes one strict admission (monthly and scope alike) and reserves its
vendor-documented MAXIMUM billable cost, never an estimate: the only priced model (MODEL), an explicit max_tokens up to
the documented output limit, allowlisted fields only, no beta header, no server tool. The input side is the documented
context window (the API rejects a longer prompt with a 400, so no admitted prompt bills more) at the dearest class and
tier (long-prompt tier, 1-hour cache write); the output side is max_tokens at the long-prompt output price. If spent +
reservations + this one would pass CAP_USD (default 25) the request is refused with HTTP 400 before it is forwarded.
Sources (Anthropic docs, read Nov 26 2026): models overview and context windows (claude-haiku-5-5: 1M-token context,
128K max output; "if the input alone already exceeds the model's context window, the API returns a 400"), pricing
(Haiku 5.5 over 100,000 prompt tokens: input 0.50, 1-hour cache write 1.00, cache read 0.05, output 2.50 per million
tokens; a request's prompt length counts cache reads and writes), token counting ("The token count is an estimate...
might differ by a small amount": it can refuse, never lower a reservation). When the reply ends the reservation
becomes the actual usage (a broken-off reply keeps the full reservation). The cap is per window (the 5th at 6:00 PM CT to the next 5th): the first request after the boundary
archives the old window to windows.jsonl and starts at zero. A STOP file next to the ledger refuses everything;
METER_STOP_AFTER (optional, UTC) is a hard stop; the first refusal in a window leaves an EXHAUSTED file (one alert
per window). Fail closed: an unreadable or unwritable ledger, or a missing one while the audit file shows spend,
refuses every Anthropic request. The real key arrives as a systemd credential; keys and bodies are never logged.
Stdlib only.

Scope budget (off unless an operator writes scope.json next to the ledger): one hard cap, in whole nano-dollars,
across all metered calls, reserved together with the monthly cap; STOP still comes first. While a scope stands a
request must also fit the scope's cap with the same documented-maximum reservation; at that reservation a $0.50
scope admits nothing (see MIN_RESERVATION_NANO), which is reported, not worked around. Every
reservation is saved before the request is forwarded; after a restart each open one is charged in full, because the
reply may have been billed. A reply with no usage, one that broke off, or a connection that failed after it opened
keeps its full reservation; a reply that cost more than its reservation marks the scope breached and refuses
everything. The scope's spend never resets. Removing, editing or letting scope.json expire while the scope is open
refuses every metered request. A SCOPE-CLOSE file naming the scope id closes it, only when nothing is in flight;
closing archives one line in scopes.jsonl and an id is never reused. Embeddings stay free and unscoped."""

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
LONG_PRICES = {"input": 0.50, "cache_write": 1.00, "cache_read": 0.05, "output": 2.50}
# input tokens (new + cache write + cache read) above which the long tier applies
LONG_AT = 100_000
MAX_BODY = 4_000_000
# Vendor-documented limits of the one priced model (sources in the module docstring).
MODEL = "claude-haiku-5-5"
CONTEXT_TOKENS = 1_000_000
MAX_OUTPUT_TOKENS = 128_000
SCOPE_FILE, SCOPE_CLOSE = "scope.json", "SCOPE-CLOSE"
ADMIT_KEYS = frozenset(
    {
        "model",
        "max_tokens",
        "messages",
        "system",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "stream",
        "metadata",
        "tools",
        "tool_choice",
        "thinking",
    }
)
SCOPE_CONFIG = ("id", "cap_usd", "expires", "models", "max_tokens")
NANO = 1_000_000_000
# Dearest input-side class at the dearest tier: the 1-hour cache write over 100,000 prompt tokens.
INPUT_NANO_PER_TOKEN = max(round(LONG_PRICES[k] * 1000) for k in ("input", "cache_write", "cache_read"))


def _epoch(text):
    import calendar

    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


STOP_AFTER = _epoch(os.environ["METER_STOP_AFTER"]) if os.environ.get("METER_STOP_AFTER") else None
CENTRAL = ZoneInfo("America/Chicago")


def window(epoch):
    """-> (start, end) epochs of the window holding `epoch`: the 5th at 6:00 PM CT (America/Chicago) to the next."""
    now = datetime.fromtimestamp(epoch, CENTRAL)
    year, month = now.year, now.month
    if now < datetime(year, month, 5, 18, tzinfo=CENTRAL):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    nyear, nmonth = (year + 1, 1) if month == 12 else (year, month + 1)
    return int(datetime(year, month, 5, 18, tzinfo=CENTRAL).timestamp()), int(
        datetime(nyear, nmonth, 5, 18, tzinfo=CENTRAL).timestamp()
    )


def table(input_tokens):
    return LONG_PRICES if input_tokens > LONG_AT else PRICES


def usd(tokens, kind, prices=PRICES):
    return tokens * prices[kind] / 1_000_000


def usage_cost(usage):
    u = usage or {}
    new, written, read, out = (
        int(u.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    )
    prices = table(new + written + read)
    return (
        usd(new, "input", prices)
        + usd(written, "cache_write", prices)
        + usd(read, "cache_read", prices)
        + usd(out, "output", prices)
    )


def nano_rate(kind, prices=PRICES):
    """-> whole nano-dollars per token (a USD-per-million price times 1000)."""
    return round(prices[kind] * 1000)


def usage_nano(usage):
    u = usage or {}
    new, written, read, out = (
        int(u.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    )
    prices = table(new + written + read)
    return (
        new * nano_rate("input", prices)
        + written * nano_rate("cache_write", prices)
        + read * nano_rate("cache_read", prices)
        + out * nano_rate("output", prices)
    )


def reservation_nano(max_tokens):
    """-> the documented maximum billable nano-dollars of one admitted request: the whole context window at the dearest
    input class and tier, plus max_tokens of output at the long-prompt price."""
    return CONTEXT_TOKENS * INPUT_NANO_PER_TOKEN + max_tokens * nano_rate("output", LONG_PRICES)


MIN_RESERVATION_NANO = reservation_nano(1)  # the least any admitted request can reserve (max_tokens of 1)


def admission(body, beta=False, counter=None):
    """-> (reservation in nano-dollars, "") for a /v1/messages body, or (0, why): nothing unprovable is forwarded.
    `counter` (optional, a provider token count) may only refuse: a count that is missing, invalid, raises, times out
    or exceeds the context window is refused; a valid count never lowers the reservation, because the count is only
    an estimate.
    """
    if beta:
        return 0, "beta features are not priced"
    try:
        doc = json.loads(body)
    except ValueError:
        return 0, "body is not JSON"
    if not isinstance(doc, dict) or not ADMIT_KEYS.issuperset(doc):
        return 0, "unsupported request field"
    if doc.get("model") != MODEL:
        return 0, "model is not priced"
    max_tokens = doc.get("max_tokens")
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
        return 0, "max_tokens missing or over the documented output limit"
    tools = doc.get("tools", [])
    if not isinstance(tools, list) or any(
        not isinstance(t, dict) or t.get("type") not in (None, "custom") for t in tools
    ):
        return 0, "server tools are not priced"
    if counter is not None:
        try:
            counted = counter(body)
        except Exception:  # noqa: BLE001  an error, a timeout or no answer all refuse
            return 0, "token count unavailable"
        if type(counted) is not int or counted < 1:
            return 0, "token count invalid"
        if counted > CONTEXT_TOKENS:
            return 0, "prompt is over the context window"
    return reservation_nano(max_tokens), ""


class Ledger:
    def __init__(self, state_dir=STATE_DIR, cap=CAP_USD, clock=time.time):
        self.dir, self.cap, self.clock = state_dir, cap, clock
        self.lock = threading.RLock()
        self.path, self.audit_path, self.stop_path, self.windows_path = (
            os.path.join(state_dir, n) for n in ("ledger.json", "audit.jsonl", "STOP", "windows.jsonl")
        )
        os.makedirs(state_dir, mode=0o750, exist_ok=True)
        self.spent, self.reserved, self.calls = 0.0, 0.0, 0
        self.inflight, self.scope, self.closed, self.rid = {}, None, [], 0  # reservations; open scope; closed ids
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
            self.inflight = {
                int(k): {"worst": float(v["worst"]), "nano": int(v["nano"])}
                for k, v in (saved.get("inflight") or {}).items()
            }
            self.scope, self.closed, self.rid = (
                saved.get("scope"),
                list(saved.get("closed_scopes") or []),
                int(saved.get("rid") or 0),
            )
            if self.scope is not None and not (
                isinstance(self.scope, dict) and {"config", "spent_nano", "state"} <= set(self.scope)
            ):
                raise ValueError("scope")
        except FileNotFoundError:
            if os.path.exists(self.audit_path) and os.path.getsize(self.audit_path) > 0:
                self.unverified = "ledger missing while the audit file shows past spend"
        except (OSError, ValueError, KeyError, TypeError):
            self.unverified = "ledger unreadable"
        self.roll()
        self.recover()

    def recover(self):
        """After a restart every open reservation may have been billed: charge each in full to the month and its
        scope.
        """
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
                        handle.write(
                            json.dumps(
                                {
                                    "epoch": int(self.clock()),
                                    "route": "/v1/messages",
                                    "status": 0,
                                    "recovered": True,
                                    "cost_usd": round(held["worst"], 6),
                                    "spent_usd": round(self.spent, 6),
                                },
                                sort_keys=True,
                            )
                            + "\n"
                        )
                except OSError:
                    self.unverified = self.unverified or "audit unwritable"
            self.inflight, self.reserved = {}, 0.0
            try:
                self.save()
            except OSError:
                self.unverified = "ledger unwritable"

    def roll(self):
        with self.lock:
            if self.unverified or self.clock() < self.end:
                return
            try:
                with open(self.windows_path, "a") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "start": self.start,
                                "end": self.end,
                                "spent_usd": round(self.spent, 6),
                                "calls": self.calls,
                                "cap_usd": self.cap,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
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
        """-> (config dict, "") from scope.json, (None, "") when there is none, (None, why) when unreadable or
        invalid.
        """
        try:
            with open(os.path.join(self.dir, SCOPE_FILE)) as handle:
                doc = json.load(handle)
            config = {
                "id": str(doc["id"]),
                "cap_usd": float(doc["cap_usd"]),
                "expires": int(doc["expires"]),
                "models": sorted(str(m) for m in doc["models"]),
                "max_tokens": int(doc["max_tokens"]),
            }
            if (
                not config["id"]
                or config["cap_usd"] <= 0
                or not 1 <= config["max_tokens"] <= MAX_OUTPUT_TOKENS
                or config["models"] != [MODEL]
                or set(doc) - set(SCOPE_CONFIG)
            ):
                raise ValueError("scope")
            config["cap_nano"] = round(config["cap_usd"] * NANO)
            return config, ""
        except FileNotFoundError:
            return None, ""
        except (OSError, ValueError, KeyError, TypeError):
            return None, "scope file invalid"

    def scope_block(self):
        """-> "" when no scope stands or the open one may spend, else why nothing is forwarded; a changed, removed,
        expired or breached scope never reopens spend.
        """
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
        """Close the open scope when SCOPE-CLOSE names its id and nothing is in flight; otherwise keep refusing. -> ""
        once closed, else the reason.
        """
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
        row = {
            "id": held["id"],
            "cap_usd": held["cap_usd"],
            "spent_nano": self.scope["spent_nano"],
            "state": self.scope["state"],
            "closed": int(self.clock()),
        }
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
            json.dump(
                {
                    "spent_usd": round(self.spent, 6),
                    "calls": self.calls,
                    "cap_usd": self.cap,
                    "window_start": self.start,
                    "window_end": self.end,
                    "saved": int(self.clock()),
                    "inflight": {str(k): v for k, v in self.inflight.items()},
                    "scope": self.scope,
                    "closed_scopes": self.closed,
                    "rid": self.rid,
                },
                handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def stopped(self):
        return os.path.lexists(self.stop_path)

    def reserve(self, worst, nano=0):
        """-> a reservation number (truthy) holding `worst` USD, or False when the cap, STOP file or a scope forbids
        it. Saved before returning.
        """
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

    def admit(self, body, beta=False, counter=None):
        """-> (reservation, worst USD, "") for a /v1/messages request, or (False, 0.0, why): one strict admission for the
        monthly cap and any scope alike, reserving the documented maximum.
        """
        with self.lock:
            blocked = self.why_blocked()
            if blocked:
                return False, 0.0, blocked
            nano, why = admission(body, beta, counter)
            if why:
                return False, 0.0, why
            if self.scope is not None and nano > reservation_nano(self.scope["config"]["max_tokens"]):
                return False, 0.0, "max_tokens is over the scope limit"
            rid = self.reserve(nano / NANO, nano)
            return rid, nano / NANO, "cap reached or stopped"

    def settle(self, worst, actual, route, status, usage=None, broke=False, rid=None):
        """Replace the reservation with the actual cost (all of it when the reply broke off) and append one audit
        line; a cost above the reservation breaches the scope.
        """
        with self.lock:
            self.roll()
            held = self.inflight.pop(rid, None) if rid else None
            nano = held["nano"] if held else 0
            if held:
                worst = held["worst"]
            self.reserved = max(0.0, self.reserved - worst)
            scoped = self.scope is not None and nano > 0
            if held and not broke and status < 400 and "output_tokens" not in (usage or {}):
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
            row = {
                "epoch": int(self.clock()),
                "route": route,
                "status": status,
                "cost_usd": round(cost, 6),
                "broke": bool(broke),
                "spent_usd": round(self.spent, 6),
            }
            row.update(
                {
                    k: int((usage or {}).get(k) or 0)
                    for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
                }
            )
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
                    handle.write(
                        json.dumps(
                            {
                                "epoch": int(self.clock()),
                                "route": route,
                                "status": 400,
                                "refused": why,
                                "spent_usd": round(self.spent, 6),
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
            except OSError:
                pass


class Handler(http.server.BaseHTTPRequestHandler):
    ledger = None
    counter = None  # optional provider token count (a refuse-only gate); none is wired: no provider call is made for it
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
        self.reply(
            400, {"type": "error", "error": {"type": "invalid_request_error", "message": "lifeos meter: " + why}}
        )

    def do_GET(self):
        if self.path == "/health":
            blocked = self.ledger.why_blocked() or None
            self.reply(
                200,
                {
                    **({"scope": scope_view(self.ledger.scope)} if self.ledger.scope is not None else {}),
                    "ok": True,
                    "spent_usd": round(self.ledger.spent, 6),
                    "cap_usd": self.ledger.cap,
                    "stopped": self.ledger.stopped(),
                    "blocked": blocked,
                    "window": {
                        "start": datetime.fromtimestamp(self.ledger.start, CENTRAL).isoformat(),
                        "end": datetime.fromtimestamp(self.ledger.end, CENTRAL).isoformat(),
                    },
                },
            )
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
            rid, worst, why = self.ledger.admit(body, bool(self.headers.get("anthropic-beta")), self.counter)
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
        headers = {
            "x-api-key": self.key,
            "anthropic-version": self.headers.get("anthropic-version") or "2023-06-01",
            "content-type": "application/json",
        }
        if self.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = self.headers["anthropic-beta"]
        usage, broke, status, sent, answered = {}, True, 502, False, False
        try:
            conn = http.client.HTTPSConnection(UPSTREAM, timeout=600)
            conn.connect()
            sent = True  # from here the request may reach Anthropic: a failure is ambiguous, not free
            conn.request("POST", path, body, headers)
            answer = conn.getresponse()
            answered = True
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
                # an error reply has no usage and no charge; a broken-off reply keeps its full reservation
                ambiguous = (
                    sent and not answered
                )  # failed after it opened and before any reply: it may have been billed
                self.ledger.settle(
                    worst,
                    usage_cost(usage) if status < 400 else 0.0,
                    path,
                    status,
                    usage,
                    broke=(broke and status < 400) or ambiguous,
                    rid=rid,
                )


def scope_view(scope):
    """-> the scope facts /health may show: id, cap, spend and state, or None."""
    if scope is None:
        return None
    return {
        "id": scope["config"]["id"],
        "cap_usd": scope["config"]["cap_usd"],
        "spent_usd": scope["spent_nano"] / NANO,
        "state": scope["state"],
    }


def complete(raw, streaming):
    if streaming:
        return b'"message_stop"' in raw
    try:
        json.loads(raw)
        return True
    except ValueError:
        return False


def parse_usage(raw, streaming):
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
