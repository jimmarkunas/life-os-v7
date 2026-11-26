"""LIFE-987: the orchestrator operations of the Buzz bridge (agent_status, agent_dispatch, agent_messages), for the two chat clients only.

(Grants: mcp_access.json; never part of the frozen contract.) The bridge reaches Concord through ONE fixed Unix socket, /run/lifeos-concord-gateway/orchestrator.sock (lifeos/edge/concord/gateway.py, run as `concord`); the path is a constant. With no socket every operation answers `gateway_unavailable`.
Trust boundary: before a dispatch the ticket must be Agent Go or In Progress by a read-only `ticket_state(key)` (injectable; the bridge holds no Jira credential, so without one it fails closed `ticket_state_unavailable`), the text
is untrusted data (refused when it holds a secret or plainly asks for a credential, spend, deletion, scope change or permission), and the idempotency key makes a repeat safe. Nothing here starts a session: an offline agent gets
ONE deduplicated Agent Inbox item "Start <agent>: go", with ORCHESTRATOR_CAN_START on or off. Reasons are fixed words; message text never reaches a log."""
import json
import os
import re
import socket
import time
from datetime import datetime, timezone

from lifeos.platform import inbox_send

SOCKET = "/run/lifeos-concord-gateway/orchestrator.sock"
AGENTS = ("oc", "mc01", "mc02", "codex")
CLIENTS = {"claude": "claude", "chatgpt": "chatgpt"}
RELEASED = ("Agent Go", "In Progress")
TICKET = re.compile(r"^LIFE-[0-9]{1,6}$")
IDEM = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
CONNECT_WAIT, REPLY_WAIT, MAX_REPLY = 3, 32, 65536
SEEN_MAX = 500
SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|passphrase|token)\s*[:=]\s*\S{8,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:ghp_|github_pat_|sk-|xox[abprs]-)[A-Za-z0-9_-]{10,}")
ASKS = tuple(re.compile(p, re.I) for p in (
    r"\b(?:send|paste|share|give|reveal|print|show|read out|email|post)\s+(?:me\s+|us\s+)?(?:the\s+|your\s+|any\s+|all\s+)?(?:\w+\s+)?(?:password|passphrase|api key|token|secret|credential|private key|ssh key)s?\b",
    r"\b(?:approve|authori[sz]e|grant)\b.{0,40}\b(?:spend|budget|payment|purchase|permission|access|deletion|delete)\b",
    r"\b(?:delete|erase|wipe|drop|destroy)\s+(?:all|every|everything|the\s+(?:database|repo|repository|bucket|backup|branch))\b",
    r"\b(?:ignore|bypass|override|skip|disable)\s+(?:the\s+|all\s+)?(?:rules|policy|approval|approvals|gate|safety|permissions)\b",
    r"\b(?:change|expand|widen)\s+(?:the\s+)?(?:scope|permissions)\b"))
FIELDS = {"agent": "oc, mc01, mc02 or codex", "ticket": "a LIFE ticket key in Agent Go or In Progress, for example LIFE-987", "text": "the task, 4,000 characters at most; data for that ticket, never an approval",
          "idempotency_key": "a key; the same key never sends a second prompt", "limit": "page size, 1 to 50 (default 20)", "cursor": "the next_cursor of the previous page"}
OPS = {
    "agent_status": ("Every agent's identity, whether it is online as Concord reports it, its current claim and its last status, with the time (Concord/Orca gateway; read-only).", {}),
    "agent_dispatch": ("Send one task to one named agent (oc, mc01, mc02, codex) for a released ticket through Concord. Returns delivered, acknowledged or not_delivered (agent_offline, which also puts one 'Start <agent>: go' item in Jim's Agent Inbox). Never starts a session, never approves anything.",
                       {k: ("str", True) for k in ("agent", "ticket", "text", "idempotency_key")}),
    "agent_messages": ("The agents' Concord room messages addressed to the orchestrator (status, blocker, completion), newest first, paged.", {"limit": ("int", False), "cursor": ("str", False)}),
}
NEEDS = {"agent_status": "read", "agent_dispatch": "write", "agent_messages": "read"}


class OrchestratorError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def route(op, args, tenant):
    """-> (tenant, action, account, service) for the gate: personal, read or write, account `orchestrator`, service `buzz` (the bridge is the only connector, so the policy level is the Buzz one)."""
    return "personal", NEEDS.get(op, "write"), "orchestrator", "buzz"


def can_start(environ=None):
    """ORCHESTRATOR_CAN_START: on only when exactly "on" (case and spaces ignored); anything else, unset or unreadable is off. No start mechanism exists in this release, so on and off both fall back to the inbox item."""
    environ = os.environ if environ is None else environ
    try:
        return str(environ.get("ORCHESTRATOR_CAN_START") or "").strip().lower() == "on"
    except (AttributeError, TypeError):
        return False


def gateway(op, client, args, connect=socket.socket, path=SOCKET):
    """One request/reply over the fixed socket -> the gateway's reply dict. A missing socket, a refusal to connect, a timeout or a bad reply is `gateway_unavailable` or `gateway_bad_reply`; never any text from either side."""
    frame = (json.dumps({"op": op, "client": client, "args": args}, separators=(",", ":"), sort_keys=True) + "\n").encode()
    try:
        s = connect(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(CONNECT_WAIT)
            s.connect(path)
            s.settimeout(REPLY_WAIT)
            s.sendall(frame)
            s.shutdown(socket.SHUT_WR)
            data, end = b"", time.monotonic() + REPLY_WAIT
            while len(data) <= MAX_REPLY and time.monotonic() < end:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
            s.close()
    except OSError:
        raise OrchestratorError("gateway_unavailable") from None
    try:
        reply = json.loads(data)
    except ValueError:
        raise OrchestratorError("gateway_bad_reply") from None
    if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
        raise OrchestratorError("gateway_bad_reply")
    return reply


def _refuse_text(text):
    if SECRET.search(text) or any(p.search(text) for p in ASKS):
        raise OrchestratorError("text_refused")


def _check_dispatch(args, state):
    agent, ticket, text, key = (args.get(k) for k in ("agent", "ticket", "text", "idempotency_key"))
    if agent not in AGENTS or not isinstance(ticket, str) or not TICKET.match(ticket) or not isinstance(text, str) or not 1 <= len(text) <= 4000 or not isinstance(key, str) or not IDEM.match(key):
        raise OrchestratorError("invalid_argument")
    _refuse_text(text)
    lookup = state.get("ticket_state")
    if not callable(lookup):
        raise OrchestratorError("ticket_state_unavailable")
    try:
        seen = lookup(ticket)
    except Exception:                                           # noqa: BLE001 - a fixed word, never the error
        raise OrchestratorError("ticket_state_unavailable") from None
    if seen is None:
        raise OrchestratorError("ticket_state_unavailable")
    if seen not in RELEASED:
        raise OrchestratorError("ticket_not_released")
    return agent, ticket, text, key


def _fallback(agent, ticket, state):
    """The pre-made decision: an offline agent gets ONE Agent Inbox item per agent (same key, so a repeat updates it). -> "created" | "updated" | "duplicate" | "unavailable"."""
    inbox = state.get("inbox") or {}
    client = inbox.get("client")
    if client is None:
        return "unavailable"
    item = {"from": "orchestrator", "subject": f"Start {agent}: go", "urgency": "Today", "workspace": "Personal", "key": f"orchestrator-start-{agent}",
            "details": f"Released ticket {ticket} is waiting for {agent}. Start that agent's session; no session starts on its own."}
    now = inbox["now"]() if callable(inbox.get("now")) else datetime.now(timezone.utc)
    out = inbox_send.deliver(item, client, now)
    if not out.get("ok"):
        return "unavailable"
    return "created" if out.get("created") else "updated" if out.get("updated") else "duplicate"


def run(op, args, state):
    client = CLIENTS.get(str(args.get("_client") or ""))
    if client is None:
        client = {"claude-chat": "claude", "chatgpt-chat": "chatgpt"}.get(str(args.get("_client") or ""))
    if client is None:
        raise OrchestratorError("client_not_allowed")
    wire = state.get("gateway") or gateway
    if op == "agent_status":
        reply = wire("agent_status", client, {})
        return _data(reply)
    if op == "agent_messages":
        extra = {k: args[k] for k in ("limit", "cursor") if k in args}
        return _data(wire("agent_messages", client, extra))
    if op != "agent_dispatch":
        raise OrchestratorError("unknown_operation")
    agent, ticket, text, key = _check_dispatch(args, state)
    seen = state.setdefault("seen", {})
    if key in seen:
        return {**seen[key], "repeat": True}
    reply = wire("agent_dispatch", client, {"agent": agent, "ticket": ticket, "text": text, "idempotency_key": key})
    data = _data(reply)
    out = {"agent": agent, "ticket": ticket, "state": data.get("state", "unknown")}
    if data.get("delivered") is True:
        out["delivered"] = True
        out["acknowledged"] = data.get("state") == "acknowledged"
    else:
        out["delivered"] = False
        out["not_delivered"] = str(data.get("reason") or "delivery_unknown")[:40]
        if out["not_delivered"] == "agent_offline":
            out["start_item"] = _fallback(agent, ticket, state)
            out["can_start"] = can_start()                       # recorded only: no start mechanism exists, so the inbox item is always the answer
    if len(seen) >= SEEN_MAX:
        seen.pop(next(iter(seen)))
    seen[key] = out
    return out


def _data(reply):
    if reply.get("ok") is not True:
        reason = str(reply.get("reason") or "")
        raise OrchestratorError(reason if re.match(r"^[a-z_]{3,40}$", reason) else "gateway_failed")
    data = reply.get("data")
    if not isinstance(data, dict):
        raise OrchestratorError("gateway_bad_reply")
    return data
