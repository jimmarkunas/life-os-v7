"""V7 client of Oracle's synthetic-only `fit1` verb and its `limits` reading.

V7 holds no Gateway credential and chooses nothing but the request: it sends one framed request, checks the echoed
hash, nonce and receipt (pinned model, no fallback) and hands the raw judgment to judge.accept. A doubt is a fixed
code.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import socket
import time
from types import SimpleNamespace

from lifeos.jobs.fit import judge
from lifeos.platform import socket_exchange
from lifeos.platform.fixed_code import fixed

PORT, MAX_REQUEST, MAX_REPLY, WAIT = 47341, 16384, 4096, 90
MANIFEST = Path(__file__).with_name("edge_fixtures.json")
STATUSES = {"REFUSED", "BUSY", "TIMEOUT", "TIMEOUT_ORPHAN", "ERROR", "MODEL_DRIFT", "OVERSIZE"}
REASONS = {"NOT_ALLOWLISTED", "NONCE_REPLAYED", "COUNTER_UNAVAILABLE", "DAILY_LIMIT",
           "LIMIT_UNCONFIGURED",
           "REAL_DISABLED", "BRIEF_MISSING", "BRIEF_UNSAFE", "BRIEF_HASH_MISMATCH", "BRIEF_INVALID", "PROFILE_MISMATCH", "PURGE_FAILED",
           "USAGE_UNAVAILABLE",
           "QUOTA_RESERVE", "QUOTA_UNCONFIGURED", "METER_UNAVAILABLE",
           "AUTH_UNAVAILABLE"}
SIGNIN_STATES = {"OK", "EXPIRED", "COOLDOWN", "UNREADABLE"}
MODELS = {"gpt-6-luna", "openai/gpt-6-luna"}
AGENTS = {"fit", "fit2", "fit3"}


class EdgeError(RuntimeError):
    """Fixed codes only. `detail` names the first receipt field not as contracted; `receipt` is set for
    FIT_EDGE_PURGE_FAILED.
    """
    quota = None

    def __init__(self, code, detail="", receipt=None):
        super().__init__(code)
        self.detail, self.receipt = detail, receipt


def digest(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def frame(request, nonce, **extra):
    body = json.dumps({"request": request, "nonce": nonce, **extra}, separators=(",", ":")).encode()
    if len(body) > MAX_REQUEST:
        raise EdgeError("FIT_EDGE_REQUEST_TOO_LARGE")
    return b"fit1 %d\n" % len(body) + body


CONNECT_ATTEMPTS, CONNECT_PAUSE = 2, 3


def exchange(host, data, port=PORT, wait=WAIT, sleep=time.sleep):
    """One request, read to the server's close; b"" = closed with no output, a hung server is FIT_EDGE_TIMEOUT.

    A connection that cannot be opened is retried (CONNECT_ATTEMPTS), then FIT_EDGE_CONNECT_FAILED (nothing was sent).
    """
    return socket_exchange.exchange(host, data, port, wait, MAX_REPLY, lambda: EdgeError("FIT_EDGE_CONNECT_FAILED"), lambda: EdgeError("FIT_EDGE_TIMEOUT"),
                                    sleep=sleep, attempts=CONNECT_ATTEMPTS, pause=CONNECT_PAUSE)


def bad_receipt_field(receipt):
    if not isinstance(receipt, dict):
        return "receipt"
    ids, text = receipt.get("run_ids"), lambda key: isinstance(receipt.get(key), str) and 0 < len(receipt[key]) <= 64
    rules = {"provider": receipt.get("provider") == "openai", "model": isinstance(receipt.get("model"), str) and receipt["model"] in MODELS, "transport": receipt.get("transport") == "gateway",
             "agent": receipt.get("agent") in AGENTS, "fallback": receipt.get("fallback") is False, "run_ids": isinstance(ids, list) and 0 < len(ids) <= 8 and all(isinstance(i, str) and 0 < len(i) <= 64 for i in ids),
             "run_finished": receipt.get("run_finished") is True, "started": text("started"), "ended": text("ended"),
             "purge": receipt.get("purge", "absent") in ("absent", "ok", "failed")}
    return next((name for name, ok in rules.items() if not ok), None)


def quota_reading(raw):
    """Oracle's meter reading -> {"stop", "windows"}, or None if not as contracted."""
    try:
        stop, windows = raw["stop"], raw["windows"]
        assert type(stop) is int and 1 <= stop <= 100 and isinstance(windows, list) and 0 < len(windows) <= 4
        out = [{"label": w["label"], "used": w["used"], "reset_at": w["reset_at"]} for w in windows]
        assert all(isinstance(w["label"], str) and 0 < len(w["label"]) <= 16 and type(w["used"]) in (int, float) and 0 <= w["used"] <= 100
                   and isinstance(w["reset_at"], str) and len(w["reset_at"]) <= 40 for w in out)
        return {"stop": stop, "windows": out}
    except (KeyError, TypeError, AssertionError):
        return None


LIMIT_WAIT = 45
NAME = re.compile(r"[a-z0-9_-]{1,32}\Z")


ESTIMATE_KEYS = {"label", "basis_calls", "per_call_percent", "per_call_below", "proposed_percent", "proposed_percent_at_most", "calls_left", "calls_left_at_least"}


def _estimate(raw):
    windows = [{k: v for k, v in w.items() if k in ESTIMATE_KEYS} for w in raw["windows"]]
    assert type(raw["proposed"]) is int and raw["proposed"] >= 0 and isinstance(raw["reported_at"], str) and len(raw["reported_at"]) <= 40 and len(windows) <= 6
    assert all(isinstance(w.get("label"), str) and len(w["label"]) <= 32 and type(w.get("basis_calls")) is int
               and all(type(v) in (int, float) and v >= 0 for k, v in w.items() if k not in ("label",)) for w in windows)
    return {"proposed": raw["proposed"], "reported_at": raw["reported_at"], "windows": windows}


def _answer(host, data, send, wait, status_ok):
    raw = send(host, data, wait=wait)
    if not raw:
        raise EdgeError("FIT_EDGE_NO_REPLY")
    if len(raw) > MAX_REPLY:
        raise EdgeError("FIT_EDGE_REPLY_TOO_LARGE")
    with fixed((ValueError, KeyError, AssertionError, TypeError), EdgeError, "FIT_EDGE_OFF_CONTRACT"):
        reply = json.loads(raw)
        assert isinstance(reply, dict) and reply["schema"] == "lifeos.fit.v1" and status_ok(reply["status"])
    return reply


def limits(host, send=None, wait=LIMIT_WAIT, backlog=None):
    """Oracle's `limits` request (no model run) -> {"read_at", "pools", "unavailable", "safety_cap"} or an EdgeError.

    `backlog` (the Cloud Fit backlog count) is sent to Oracle, which returns its per-window estimate.
    """
    if not host:
        raise EdgeError("FIT_EDGE_NO_HOST")
    nonce = secrets.token_hex(16)
    body = json.dumps({"limits": True, "nonce": nonce, **({"backlog": backlog} if backlog is not None else {})}, separators=(",", ":")).encode()
    reply = _answer(host, b"fit1 %d\n" % len(body) + body, send or exchange, wait, lambda status: status == "LIMITS")
    if reply.get("nonce") != nonce:
        raise EdgeError("FIT_EDGE_NONCE_MISMATCH")
    with fixed((KeyError, TypeError, AssertionError), EdgeError, "FIT_EDGE_LIMITS_OFF_CONTRACT"):
        pools = []
        for p in reply["pools"]:
            assert isinstance(p["provider"], str) and NAME.match(p["provider"]) and isinstance(p["windows"], list) and len(p["windows"]) <= 6
            windows = [{"label": w["label"], "used": w["used"], "reset_at": w["reset_at"]} for w in p["windows"]]
            assert all(isinstance(w["label"], str) and 0 < len(w["label"]) <= 32 and type(w["used"]) in (int, float) and 0 <= w["used"] <= 100
                       and (w["reset_at"] is None or (isinstance(w["reset_at"], str) and len(w["reset_at"]) <= 40)) for w in windows)
            stop = p.get("stop")
            assert stop is None or (type(stop) is int and 1 <= stop <= 100)
            pools.append({"provider": p["provider"], "windows": windows, **({"stop": stop} if stop else {}), **({"estimate": _estimate(p["estimate"])} if p.get("estimate") else {})})
        unavailable = reply["unavailable"]
        assert isinstance(unavailable, list) and all(isinstance(u, str) and NAME.match(u) for u in unavailable)
        cap = reply.get("safety_cap")
        assert cap is None or (isinstance(cap, dict) and type(cap.get("runs_today")) is int and type(cap.get("limit")) is int)
        read_at = reply["read_at"]
        assert isinstance(read_at, str) and len(read_at) <= 40
        signins = reply.get("signins") or {}
        assert isinstance(signins, dict) and set(signins) <= {"fit", "meter"}
        signins = {k: {"state": v["state"], "expires": v.get("expires")} for k, v in signins.items()}
        assert all(v["state"] in SIGNIN_STATES and (v["expires"] is None or (isinstance(v["expires"], str) and len(v["expires"]) <= 40)) for v in signins.values())
    return {"read_at": read_at, "pools": pools, "unavailable": unavailable, "safety_cap": cap and {"runs_today": cap["runs_today"], "limit": cap["limit"]},
            **({"signins": signins} if signins else {})}


def invoke(host, request, base, title, nonce=None, send=exchange, wait=WAIT):
    """One model invocation, never retried: -> {judgment, receipt, request_hash, ...} or an EdgeError at the first
    mismatch.
    """
    if not host:
        raise EdgeError("FIT_EDGE_NO_HOST")
    nonce, want = nonce or secrets.token_hex(16), digest(request)
    reply = _answer(host, frame(request, nonce), send, wait, lambda status: isinstance(status, str))
    receipt, status = reply.get("receipt"), reply["status"]
    if status == "BUSY":
        raise EdgeError("FIT_EDGE_BUSY")
    if reply.get("request_hash") != want:
        raise EdgeError("FIT_EDGE_HASH_MISMATCH")
    if reply.get("nonce") != nonce:
        raise EdgeError("FIT_EDGE_NONCE_MISMATCH")
    if status != "OK":
        error = EdgeError("FIT_EDGE_" + (status if status in STATUSES else "OFF_CONTRACT") + ("_" + reply["reason"] if status == "REFUSED" and isinstance(reply.get("reason"), str) and reply["reason"] in REASONS else ""))
        error.quota = quota_reading(reply.get("quota"))
        raise error
    field = bad_receipt_field(receipt) or (None if isinstance(reply.get("judgment_raw"), str) else "judgment_raw")
    if field:
        raise EdgeError("FIT_EDGE_RECEIPT_REJECTED", detail=field)
    if reply.get("fixture") == "REAL" and receipt.get("purge") not in ("ok", "failed"):
        raise EdgeError("FIT_EDGE_RECEIPT_REJECTED", detail="purge")
    if receipt.get("purge") == "failed":
        raise EdgeError("FIT_EDGE_PURGE_FAILED", receipt=receipt)
    judgment = judge.accept(reply["judgment_raw"], base, title, request)
    if judgment is None:
        raise EdgeError("FIT_EDGE_JUDGMENT_REJECTED")
    return {"judgment": judgment, "receipt": receipt, "request_hash": want, "runs_today": reply.get("runs_today"), "limit": reply.get("limit"), "fixture": reply.get("fixture"),
            "quota": quota_reading(reply.get("quota"))}


def probes(fixture=None):
    bad, nonce = b"not-json-body", "0" * 32
    ghost = {**fixture, "title": fixture["title"] + " (modified)"} if fixture else {"title": "SYNTHETIC PROBE", "description": "SYNTHETIC PROBE", "profile": {"years": 1}}
    return {"bad_length": b"fit1 abc\n", "oversize": b"fit1 99999999\n", "not_json": b"fit1 %d\n" % len(bad) + bad, "unknown_key": frame(ghost, nonce, extra=1), "modified_or_unlisted": frame(ghost, nonce)}


def refused(raw):
    if len(raw) > MAX_REPLY:
        return False
    try:
        return not raw or (lambda reply: isinstance(reply, dict) and reply.get("status") in ("REFUSED", "OVERSIZE"))(json.loads(raw))
    except ValueError:
        return False


def run(limit, live, environ=os.environ, send=exchange, manifest=MANIFEST):
    out = _run(environ, send, manifest)
    return {**out, "failed": int(out["status"] != "OK")}


def _run(environ, send, manifest):
    """FIT_EDGE_MODE=negative runs the refusal probes; otherwise one manifest fixture is one real call."""
    host, mode = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip(), (environ.get("FIT_EDGE_MODE") or "").strip()
    if not host or not mode:
        return {"status": "FIT_EDGE_NOT_CONFIGURED"}
    try:
        fixtures = {f["id"]: f for f in json.loads(Path(manifest).read_text())["fixtures"]}
    except (OSError, ValueError, KeyError, TypeError):
        fixtures = {}
    if mode == "negative":
        data, out = probes(next(iter(fixtures.values()), {}).get("request")), {"refused": 0, "hung": 0, "unexpected_reply": 0}
        for frame_bytes in data.values():
            try:
                out["refused" if refused(send(host, frame_bytes, wait=10)) else "unexpected_reply"] += 1
            except EdgeError:
                out["hung"] += 1
        return {"status": "OK" if out["refused"] == len(data) else "FIT_EDGE_PROBE_FAILED", "probes": len(data), **out, "model_calls_expected": 0}
    fixture = fixtures.get(mode)
    if not fixture or not isinstance(fixture.get("request"), dict) or not fixture.get("request_sha256"):
        return {"status": "FIT_EDGE_FIXTURE_MISSING"}
    request = fixture["request"]
    if digest(request) != fixture["request_sha256"]:
        return {"status": "FIT_EDGE_FIXTURE_HASH"}
    nonce = secrets.token_hex(16)
    try:
        got = invoke(host, request, SimpleNamespace(capped=False, exclusion=None), request["title"], nonce=nonce, send=send)
        replay = send(host, frame(request, nonce), wait=30)
    except EdgeError as error:
        return {"status": str(error), **({"detail": error.detail} if error.detail else {})}
    if not refused(replay):
        return {"status": "FIT_EDGE_REPLAY_ACCEPTED"}
    j, r = got["judgment"], got["receipt"]
    return {"status": "OK", "fixture": mode, "request_hash": got["request_hash"][:8], "nonce_correlated": True, "replay_refused": True, "provider": r["provider"], "model": r["model"], "fallback": r["fallback"], "agent": r.get("agent"), "run_ids": [str(x)[:12] for x in (r.get("run_ids") or [])][:3],
            "run_finished": r.get("run_finished"), "usage": r.get("usage"), "cost": r.get("cost"), "runs_today": got["runs_today"], "limit": got["limit"], "score": j.score, "decision": j.decision, "confidence": j.confidence, "rationale": j.rationale}
