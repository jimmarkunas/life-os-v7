"""First-class memory tools on the Oracle MCP: `memory_recall` and the other memory operations as their own MCP tools.

A call to one of them is rewritten, at the HTTP entry, into the existing `buzz_read` alias (operation plus nested
arguments, no community), so the identity check, the gate, the receipts and every memory safeguard run exactly as they
already do. `tools/list` gains only the memory tools the caller holds a live grant for, and a first-class call's reply
is the operation's own data (or its fixed error), without the Buzz envelope. Descriptions and parameters come from
`memory_honcho.OPS`, so the answer rule travels with the tool. Fixed codes only."""

import json

from lifeos.platform import memory_honcho, mcp_access

TYPES = {"str": "string", "int": "integer"}
READ_ONLY = ("memory_recall", "memory_ask")


def _schema(params):
    properties = {}
    for name, (kind, _required) in params.items():
        properties[name] = {"type": "string", "enum": ["Personal"]} if name == "workspace" else {"type": TYPES[kind]}
    required = [name for name, (_kind, needed) in params.items() if needed]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def tool_entries():
    """-> {name: MCP tool entry} for every memory operation, in a fixed order."""
    return {
        name: {
            "name": name,
            "description": description,
            "inputSchema": _schema(params),
            "annotations": {
                "readOnlyHint": name in READ_ONLY,
                "destructiveHint": name == "memory_revoke",
                "openWorldHint": False,
            },
        }
        for name, (description, params) in memory_honcho.OPS.items()
    }


def as_buzz_read(msg, memory_ops):
    """A `tools/call` of a first-class memory tool -> the same call through the `buzz_read` alias, marked so its reply
    can be unwrapped. Any other message is returned unchanged."""
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    name = params.get("name")
    if msg.get("method") != "tools/call" or name not in memory_ops:
        return msg
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    inner = {k: v for k, v in arguments.items() if k != "community"}
    return {
        **msg,
        "params": {**params, "name": "buzz_read", "arguments": {"operation": name, "arguments": inner}},
        "_memory_tool": name,
    }


def listed(reply, msg, who, access, gate_tool, memory_ops):
    """`tools/list`: a credential client sees only the tools it holds a live grant for (moved here unchanged from
    lifeos/buzz/mcp.py, `_only_granted`), then every caller gains its granted first-class memory tools."""
    if msg.get("method") != "tools/list" or not isinstance(reply.get("result"), dict):
        return reply
    if (who.get("facts") or {}).get("credential"):
        try:
            a = access or mcp_access.load()
            allowed = mcp_access.granted_tools(a, mcp_access.identify(a, "http", who["facts"]))
            tools = [
                t
                for t in reply["result"]["tools"]
                if gate_tool.get(t["name"], t["name"]) in allowed
                or (t["name"] == "buzz_read" and set(memory_ops) & set(allowed))
            ]
        except Exception:  # noqa: BLE001  grants that cannot be read show no tools
            tools = []
        reply = {**reply, "result": {**reply["result"], "tools": tools}}
    return add_tools(reply, msg, who, access)


def add_tools(reply, msg, who, access):
    """`tools/list` -> the same list plus the memory tools this caller is granted (none when its grants cannot be read)."""
    if msg.get("method") != "tools/list" or not isinstance(reply.get("result"), dict):
        return reply
    try:
        a = access or mcp_access.load()
        client = mcp_access.identify(a, "http", who.get("facts") or {"oauth_client": who.get("client")})
        granted = mcp_access.granted_tools(a, client)
    except Exception:  # noqa: BLE001  no readable grants: no memory tools are offered
        granted = set()
    entries = tool_entries()
    have = {t.get("name") for t in reply["result"].get("tools", [])}
    extra = [entries[name] for name in entries if name in granted and name not in have]
    return {**reply, "result": {**reply["result"], "tools": [*reply["result"].get("tools", []), *extra]}}


def unwrap(reply, msg):
    """A first-class memory call's reply -> {"ok": true, ...its data}, or {"ok": false, "error", "reason"} for a refusal;
    other replies unchanged. `ok` is always present so no client has to infer success from a missing field."""
    if not msg.get("_memory_tool") or not isinstance(reply.get("result"), dict):
        return reply
    content = reply["result"].get("content") or []
    try:
        payload = json.loads(content[0]["text"])
    except (IndexError, KeyError, TypeError, ValueError):
        return reply
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        data = payload["data"] if isinstance(payload["data"], dict) else {"data": payload["data"]}
        text = json.dumps({"ok": True, **data})
    elif isinstance(payload, dict):
        text = json.dumps(
            {"ok": False, **{k: v for k, v in payload.items() if k not in ("contract", "operation", "ok")}}
        )
    else:
        return reply
    return {**reply, "result": {**reply["result"], "content": [{"type": "text", "text": text}]}}
