"""LIFE-60 (Jim's approval, Jira 10361): the dormant V7 side of Oracle's synthetic-only `fit1` verb. The hourly pipeline never imports this; only the manual fit-edge-test workflow runs it, on the existing
ephemeral tag:v7-runner node, over the existing private endpoint (TCP 47341). V7 holds no Gateway credential and chooses no model, agent, tool or instruction: it sends one framed synthetic request whose
sha256 is in the committed manifest, checks the echoed request hash, nonce and receipt (pinned model, no fallback), and hands the raw judgment to the unchanged judge.accept. A doubt is a fixed code, never a retry."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
from types import SimpleNamespace

from lifeos.jobs.fit import judge

PORT, MAX_REQUEST, MAX_REPLY, WAIT = 47341, 16384, 4096, 90                      # Oracle answers one JSON line under 4 KiB
MANIFEST = Path(__file__).with_name("edge_fixtures.json")        # the Oracle operator's export, synthetic only: {"fixtures": [{"id", "request", "request_sha256"}]}
STATUSES = {"REFUSED", "BUSY", "TIMEOUT", "TIMEOUT_ORPHAN", "ERROR", "MODEL_DRIFT", "OVERSIZE"}
REASONS = {"NOT_ALLOWLISTED", "NONCE_REPLAYED", "COUNTER_UNAVAILABLE", "DAILY_LIMIT",
           "LIMIT_UNCONFIGURED",                                                 # LIFE-41 G3: Oracle's fail-closed daily cap has no limit.json
           "REAL_DISABLED", "BRIEF_MISSING", "BRIEF_UNSAFE", "BRIEF_HASH_MISMATCH", "BRIEF_INVALID", "PROFILE_MISMATCH", "PURGE_FAILED"}   # LIFE-41 G5 real gate (10489/10491)
MODELS = {"gpt-6-luna", "openai/gpt-6-luna"}


class EdgeError(RuntimeError):
    """Fixed codes only. `detail` (when set) names the first receipt field that was not as contracted: a fixed field name, never content."""
    detail = ""


def digest(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def frame(request, nonce, **extra):
    body = json.dumps({"request": request, "nonce": nonce, **extra}, separators=(",", ":")).encode()
    if len(body) > MAX_REQUEST:
        raise EdgeError("FIT_EDGE_REQUEST_TOO_LARGE")
    return b"fit1 %d\n" % len(body) + body


def exchange(host, data, port=PORT, wait=WAIT):
    """One request, read to the server's close (no half-close: Tailscale Serve drops the connection on a FIN). b"" = closed with no output; a hung server is FIT_EDGE_TIMEOUT."""
    try:
        with socket.create_connection((host, port), timeout=15) as s:
            s.settimeout(wait)
            s.sendall(data)
            out = b""
            while len(out) <= MAX_REPLY:
                chunk = s.recv(MAX_REPLY + 1 - len(out))
                if not chunk:
                    break
                out += chunk
            return out
    except TimeoutError:
        raise EdgeError("FIT_EDGE_TIMEOUT") from None
    except OSError:
        return b""


def bad_receipt_field(receipt):
    """The Oracle-owned completed-run receipt (Edge contract, Jira 10374): the name of the first field that is not as contracted, or None. Checked before any judgment is accepted."""
    if not isinstance(receipt, dict):
        return "receipt"
    ids, text = receipt.get("run_ids"), lambda key: isinstance(receipt.get(key), str) and 0 < len(receipt[key]) <= 64
    rules = {"provider": receipt.get("provider") == "openai", "model": isinstance(receipt.get("model"), str) and receipt["model"] in MODELS, "transport": receipt.get("transport") == "gateway",
             "agent": receipt.get("agent") == "fit", "fallback": receipt.get("fallback") is False, "run_ids": isinstance(ids, list) and 0 < len(ids) <= 8 and all(isinstance(i, str) and 0 < len(i) <= 64 for i in ids),
             "run_finished": receipt.get("run_finished") is True, "started": text("started"), "ended": text("ended")}
    return next((name for name, ok in rules.items() if not ok), None)


def invoke(host, request, base, title, nonce=None, send=exchange):
    """One model invocation, never retried: -> {judgment, receipt, request_hash}, or an EdgeError code at the first thing that does not match."""
    if not host:
        raise EdgeError("FIT_EDGE_NO_HOST")
    nonce, want = nonce or secrets.token_hex(16), digest(request)
    raw = send(host, frame(request, nonce))
    if not raw:
        raise EdgeError("FIT_EDGE_NO_REPLY")
    if len(raw) > MAX_REPLY:
        raise EdgeError("FIT_EDGE_REPLY_TOO_LARGE")                          # before any parsing
    try:
        reply = json.loads(raw)
        assert isinstance(reply, dict) and reply["schema"] == "lifeos.fit.v1" and isinstance(reply["status"], str)
        receipt, status = reply.get("receipt"), reply["status"]
    except (ValueError, KeyError, AssertionError, TypeError):
        raise EdgeError("FIT_EDGE_OFF_CONTRACT") from None
    if reply.get("request_hash") != want:
        raise EdgeError("FIT_EDGE_HASH_MISMATCH")
    if reply.get("nonce") != nonce:
        raise EdgeError("FIT_EDGE_NONCE_MISMATCH")
    if status != "OK":                                                    # a refused, busy, timed-out or drifted request is a named code, never a Fit
        raise EdgeError("FIT_EDGE_" + (status if status in STATUSES else "OFF_CONTRACT") + ("_" + reply["reason"] if status == "REFUSED" and isinstance(reply.get("reason"), str) and reply["reason"] in REASONS else ""))
    field = bad_receipt_field(receipt) or (None if isinstance(reply.get("judgment_raw"), str) else "judgment_raw")
    if field:
        error = EdgeError("FIT_EDGE_RECEIPT_REJECTED")
        error.detail = field
        raise error
    judgment = judge.accept(reply["judgment_raw"], base, title, request)
    if judgment is None:
        raise EdgeError("FIT_EDGE_JUDGMENT_REJECTED")
    return {"judgment": judgment, "receipt": receipt, "request_hash": want, "runs_today": reply.get("runs_today"), "limit": reply.get("limit"), "fixture": reply.get("fixture")}


def probes(fixture=None):
    """Requests that must be refused with no model call: malformed framing, an oversize length, a body that is not JSON, an unknown key, and a request that is not on Oracle's allowlist (the real fixture with its
    title changed when there is one)."""
    bad, nonce = b"not-json-body", "0" * 32
    ghost = {**fixture, "title": fixture["title"] + " (modified)"} if fixture else {"title": "SYNTHETIC PROBE", "description": "SYNTHETIC PROBE", "profile": {"years": 1}}
    return {"bad_length": b"fit1 abc\n", "oversize": b"fit1 99999999\n", "not_json": b"fit1 %d\n" % len(bad) + bad, "unknown_key": frame(ghost, nonce, extra=1), "modified_or_unlisted": frame(ghost, nonce)}


def refused(raw):
    """No model ran: the server closed with no output, or answered a refusal status."""
    if len(raw) > MAX_REPLY:
        return False
    try:
        return not raw or (lambda reply: isinstance(reply, dict) and reply.get("status") in ("REFUSED", "OVERSIZE"))(json.loads(raw))
    except ValueError:
        return False


def run(limit, live, environ=os.environ, send=exchange, manifest=MANIFEST):
    out = _run(environ, send, manifest)
    return {**out, "failed": int(out["status"] != "OK")}                  # run.py turns failed > 0 into a red job: a refused or unproven test is never a green one


def _run(environ, send, manifest):
    """FIT_EDGE_MODE=negative: the refusal probes (no model call expected). Otherwise one fixture label from the manifest: one real model invocation. Writes nothing; `live` is ignored."""
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
        return {"status": "FIT_EDGE_FIXTURE_HASH"}                             # a modified fixture never leaves V7
    nonce = secrets.token_hex(16)
    try:
        got = invoke(host, request, SimpleNamespace(capped=False, exclusion=None), request["title"], nonce=nonce, send=send)
        replay = send(host, frame(request, nonce), wait=30)                      # the same nonce again: Oracle must refuse it and run no model
    except EdgeError as error:
        return {"status": str(error), **({"detail": error.detail} if error.detail else {})}
    if not refused(replay):
        return {"status": "FIT_EDGE_REPLAY_ACCEPTED"}
    j, r = got["judgment"], got["receipt"]
    return {"status": "OK", "fixture": mode, "request_hash": got["request_hash"][:8], "nonce_correlated": True, "replay_refused": True, "provider": r["provider"], "model": r["model"], "fallback": r["fallback"], "agent": r.get("agent"), "run_ids": [str(x)[:12] for x in (r.get("run_ids") or [])][:3],
            "run_finished": r.get("run_finished"), "usage": r.get("usage"), "cost": r.get("cost"), "runs_today": got["runs_today"], "limit": got["limit"], "score": j.score, "decision": j.decision, "confidence": j.confidence, "rationale": j.rationale}
