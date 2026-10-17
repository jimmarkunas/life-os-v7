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

PORT, MAX_REQUEST, MAX_REPLY, WAIT = 47341, 16384, 16384, 90
MANIFEST = Path(__file__).with_name("edge_fixtures.json")        # synthetic fixtures only: {"fixtures": {label: {"title", "request", "sha256", "capped", "exclusion"}}}


class EdgeError(RuntimeError):
    """Fixed codes only."""


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


def invoke(host, request, base, title, nonce=None, send=exchange):
    """One model invocation, never retried: -> {judgment, receipt, request_hash}, or an EdgeError code at the first thing that does not match."""
    if not host:
        raise EdgeError("FIT_EDGE_NO_HOST")
    nonce, want = nonce or secrets.token_hex(16), digest(request)
    raw = send(host, frame(request, nonce))
    if not raw:
        raise EdgeError("FIT_EDGE_NO_REPLY")
    try:
        reply = json.loads(raw)
        receipt = reply["receipt"]
        assert reply["schema"] == "lifeos.fit.v1" and isinstance(receipt, dict) and isinstance(reply["judgment_raw"], str)
    except (ValueError, KeyError, AssertionError, TypeError):
        raise EdgeError("FIT_EDGE_OFF_CONTRACT") from None
    if reply.get("request_hash") != want:
        raise EdgeError("FIT_EDGE_HASH_MISMATCH")
    if reply.get("nonce") != nonce:
        raise EdgeError("FIT_EDGE_NONCE_MISMATCH")
    if (receipt.get("provider"), receipt.get("model"), receipt.get("fallback"), receipt.get("status")) != ("openai", "gpt-6-luna", False, "ok"):
        raise EdgeError("FIT_EDGE_RECEIPT_REJECTED")
    judgment = judge.accept(reply["judgment_raw"], base, title, request)
    if judgment is None:
        raise EdgeError("FIT_EDGE_JUDGMENT_REJECTED")
    return {"judgment": judgment, "receipt": receipt, "request_hash": want}


def probes():
    """Requests that must be refused with no model call: malformed framing, an oversize length, a body that is not JSON, an unknown key, and a synthetic request that is not on Oracle's allowlist."""
    bad, nonce, ghost = b"not-json-body", "0" * 32, {"title": "SYNTHETIC PROBE", "description": "SYNTHETIC PROBE", "profile": {"years": 1}}
    return {"bad_length": b"fit1 abc\n", "oversize": b"fit1 99999999\n", "not_json": b"fit1 %d\n" % len(bad) + bad, "unknown_key": frame(ghost, nonce, extra=1), "not_allowlisted": frame(ghost, nonce)}


def run(limit, live, environ=os.environ, send=exchange, manifest=MANIFEST):
    out = _run(environ, send, manifest)
    return {**out, "failed": int(out["status"] != "OK")}                  # run.py turns failed > 0 into a red job: a refused or unproven test is never a green one


def _run(environ, send, manifest):
    """FIT_EDGE_MODE=negative: the refusal probes (no model call expected). Otherwise one fixture label from the manifest: one real model invocation. Writes nothing; `live` is ignored."""
    host, mode = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip(), (environ.get("FIT_EDGE_MODE") or "").strip()
    if not host or not mode:
        return {"status": "FIT_EDGE_NOT_CONFIGURED"}
    if mode == "negative":
        out = {"refused": 0, "hung": 0, "unexpected_reply": 0}
        for data in probes().values():
            try:
                out["unexpected_reply" if send(host, data, wait=10) else "refused"] += 1
            except EdgeError:
                out["hung"] += 1
        return {"status": "OK" if out["refused"] == len(probes()) else "FIT_EDGE_PROBE_FAILED", "probes": len(probes()), **out, "model_calls_expected": 0}
    try:
        fixture = json.loads(Path(manifest).read_text())["fixtures"][mode]
    except (OSError, ValueError, KeyError):
        return {"status": "FIT_EDGE_FIXTURE_MISSING"}
    if digest(fixture["request"]) != fixture["sha256"]:
        return {"status": "FIT_EDGE_FIXTURE_HASH"}                             # a modified fixture never leaves V7
    try:
        got = invoke(host, fixture["request"], SimpleNamespace(capped=bool(fixture.get("capped")), exclusion=fixture.get("exclusion")), fixture["title"], send=send)
    except EdgeError as error:
        return {"status": str(error)}
    j, r = got["judgment"], got["receipt"]
    return {"status": "OK", "fixture": mode, "request_hash": got["request_hash"][:8], "nonce_correlated": True, "provider": r["provider"], "model": r["model"], "fallback": r["fallback"], "run_id": str(r.get("run_id"))[:12],
            "usage": r.get("usage"), "score": j.score, "decision": j.decision, "confidence": j.confidence, "rationale": j.rationale}
