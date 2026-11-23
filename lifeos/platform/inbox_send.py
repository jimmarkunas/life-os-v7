"""LIFE-876: the governed `send_to_jim` operation of the Oracle MCP: any granted client puts one item in Jim's Agent Inbox, the sender is always the verified client, and a repeat updates the same row.

It is a thin layer over lifeos.inbox.send_to_jim.deliver (the existing capability: the one Notion write, the idempotency key derived from sender, subject, due and link when absent, the update of an existing row without
resetting Jim's Status, the refusal of another sender's key). Added here: the gate route (tenant = the item's workspace, action write, account agent-inbox, service notion), From taken ONLY from the server (`_client`, the verified
credential or OAuth client; a caller cannot send it, the schema has no such field), and fixed reasons. Credential: NOTION_READONLY_TOKEN. Since Jim's decision of Nov 22 2026 (LIFE-876 comment 13823) that integration, "LIFE OS Oracle Read",
has Read, Insert and Update content and is connected to the Agent Inbox: so despite its name this token now WRITES the Agent Inbox (and nothing else it is not connected to); the name was kept so nothing is placed twice."""
from datetime import datetime, timezone

from lifeos.inbox import send_to_jim
from lifeos.platform.notion_client import Client

TENANT = {"Personal": "personal", "Bytalos": "bytalos"}                          # the gate decides which is granted; TGP and any other name are an unknown tenant, refused before anything runs
FIELDS = send_to_jim.FIELDS
OPS = {"send_to_jim": ("Put one item in Jim's Agent Inbox: something that needs his attention and is too small for a Jira ticket or a Todoist task. The sender is always your verified client, never a field you send; the same key (or the same subject, due date and link) updates the existing item instead of adding one. Never include secrets or personal data.",
                       {k: ("str", k == "subject") for k in FIELDS if k != "from"})}


class InboxSendError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def route(op, args, tenant):
    """-> (tenant, action, account, service) for the gate."""
    return TENANT.get(args.get("workspace") or "Personal", "unknown"), "write", "agent-inbox", "notion"


def run(op, args, state):
    client = state.get("client")
    if client is None:
        raise InboxSendError("inbox_not_configured")
    item = {k: v for k, v in args.items() if k in FIELDS and k != "from"}
    item["from"] = str(args.get("_client") or "")
    out = send_to_jim.deliver(item, client=client, now=state["now"]() if state.get("now") else datetime.now(timezone.utc))
    if not out.get("ok"):
        raise InboxSendError(str(out.get("reason") or "inbox_failed")[:60])
    return {k: v for k, v in out.items() if k != "ok"} | {"sender": item["from"], "workspace": item.get("workspace") or "Personal"}


def wire(environ, state):
    """The Notion client for the inbox, from the service's own environment; None (inbox_not_configured) until NOTION_READONLY_TOKEN is placed."""
    token = (environ.get("NOTION_READONLY_TOKEN") or "").strip()
    state["client"] = Client({"NOTION_READONLY_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32}, token_name="NOTION_READONLY_TOKEN", source_name="NOTION_INBOX_VIEW_SOURCE") if token else None
