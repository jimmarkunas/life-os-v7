"""`send_to_jim`: the one small tool any agent calls to put an item in the Agent Inbox (CLI and stdio MCP server).

  python -m lifeos.inbox.send_to_jim --from claude-code --subject "..." --urgency Today --workspace Personal
      [--due 2026-11-20] [--link https://...] [--details "..."] [--key ID]
  python -m lifeos.inbox.send_to_jim --mcp   stdio MCP server (JSON-RPC lines), one tool `send_to_jim`, same arguments

Needs NOTION_API_TOKEN, else it prints `config_missing` and writes nothing. Output is one JSON line of counts or a
fixed reason word, never the item.
"""

import argparse
import json
import os
import sys

from lifeos.inbox import agent_inbox
from lifeos.inbox.model import InboxError
from lifeos.platform import inbox_send, mcp_access
from lifeos.platform.notion_client import Client, NotionError

FIELDS = inbox_send.FIELDS
TOOL = {
    "name": "send_to_jim",
    "description": "Put one item in Jim's Agent Inbox: something that needs his attention and is too small for a Jira ticket or a Todoist task. Never include secrets or personal data.",
    "inputSchema": {
        "type": "object",
        "properties": {k: {"type": "string", "description": v} for k, v in FIELDS.items() if k != "from"},
        "required": ["subject"],
        "additionalProperties": False,
    },
}


def _client(env):
    token = (env.get("NOTION_API_TOKEN") or "").strip()
    if not token:
        raise InboxError("config_missing")
    return Client(
        {"NOTION_API_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32}, source_name="NOTION_INBOX_VIEW_SOURCE"
    )


def deliver(item, environ=None, client=None, now=None):
    """Returns {"ok": true, "created"|"duplicate": 1, "tiles_changed": n} or {"ok": false, "reason": word}. Never
    raises or carries content.
    """
    env = os.environ if environ is None else environ
    try:
        client = client or _client(env)
    except InboxError as error:
        return {"ok": False, "reason": error.reason}
    return inbox_send.deliver(item, client, now)


def _reply(request_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": request_id}
    out.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


TENANT = {"Personal": "personal", "Bytalos": "bytalos"}


def governed(arguments, environ, client, access, facts, audit, now):
    """send_to_jim through authorization_gate.execute: the client is the OS user of this process (never an argument),
    the tenant is the item's workspace. Returns deliver's dict, or {"ok": false, "reason": fixed word}.
    """
    try:
        access = access or mcp_access.load()
        who = mcp_access.identify(access, "stdio", facts or mcp_access.stdio_facts())
        tenant = TENANT.get(arguments.get("workspace") or "Personal")
        if tenant is None:
            raise mcp_access.AccessError("unknown_tenant")
        # From is not identity: it is derived from the one authenticated client row; a different caller-supplied From
        # is refused.
        sender = who["id"]
        if arguments.get("from") not in (None, sender):
            from lifeos.platform.authorization_gate import fingerprint

            audit(
                {
                    "tool": "send_to_jim",
                    "principal_fp": fingerprint(sender),
                    "outcome": "deny",
                    "reason": "from_spoofed",
                }
            )
            return {"ok": False, "reason": "from_spoofed"}
        item = {**arguments, "from": sender}
        out = mcp_access.call(
            access,
            who,
            tenant,
            "send_to_jim",
            "write",
            "agent-inbox",
            "notion",
            lambda: deliver(item, environ=environ, client=client, now=now),
            audit,
            now,
        )
    except mcp_access.AccessError as error:
        return {"ok": False, "reason": str(error)}
    except Exception:  # noqa: BLE001  an unwritable receipt or any failure: no effect, a fixed word
        return {"ok": False, "reason": "governance_unavailable"}
    return (
        out.result
        if out.executed and isinstance(out.result, dict)
        else {"ok": False, "reason": "denied_" + out.reason if not out.executed else out.reason}
    )


def _receipt(record):
    sys.stderr.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def serve(lines=None, environ=None, client=None, access=None, facts=None, audit=_receipt, now=None):
    for line in lines if lines is not None else sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse_error"})
            continue
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            _reply(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "send_to_jim", "version": "1.0"},
                },
            )
        elif method == "tools/list":
            _reply(request_id, {"tools": [TOOL]})
        elif method == "tools/call" and (message.get("params") or {}).get("name") == "send_to_jim":
            result = governed((message["params"].get("arguments") or {}), environ, client, access, facts, audit, now)
            _reply(request_id, {"content": [{"type": "text", "text": json.dumps(result)}], "isError": not result["ok"]})
        else:
            _reply(request_id, error={"code": -32601, "message": "method_not_found"})


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(prog="send_to_jim", description=TOOL["description"])
    parser.add_argument("--mcp", action="store_true", help="serve one MCP tool, send_to_jim, on stdio")
    for name, help_text in FIELDS.items():
        parser.add_argument(f"--{name}", help=help_text)
    args = parser.parse_args(argv)
    if args.mcp:
        serve(environ=environ)
        return 0
    result = deliver({name: getattr(args, name) for name in FIELDS}, environ=environ)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
