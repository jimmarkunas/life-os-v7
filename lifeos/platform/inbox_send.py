"""The governed send_to_jim operation of the Oracle MCP and the one Agent Inbox delivery.

A thin layer over lifeos.platform.agent_inbox: the gate route, From taken ONLY from the server (`_client`) and fixed
reasons. Credential: NOTION_READONLY_TOKEN, whose integration despite its name can write the Agent Inbox and nothing
else."""

from datetime import datetime, timezone

from lifeos.platform import agent_inbox
from lifeos.platform.inbox_error import InboxError
from lifeos.platform.notion_client import Client, NotionError

TENANT = {"Personal": "personal", "Bytalos": "bytalos"}
FIELDS = {
    "from": "who is writing (an agent id or a person's name)",
    "subject": "what it is, one line (160 characters at most)",
    "urgency": "Now, Today, This week or FYI (default Today)",
    "workspace": "Personal or Bytalos (default Personal)",
    "due": "optional date or date-time, ISO 8601",
    "link": "optional https link to the context (ticket, PR, page, thread)",
    "details": "optional short note (1,500 characters at most); never a secret or personal data",
    "key": "optional idempotency key: the same key never adds a second row",
}
OPS = {
    "send_to_jim": (
        "Put one item in Jim's Agent Inbox: something that needs his attention and is too small for a Jira ticket or a Todoist task. The sender is always your verified client, never a field you send; the same key (or the same subject, due date and link) updates the existing item instead of adding one. Never include secrets or personal data.",
        {k: ("str", k == "subject") for k in FIELDS if k != "from"},
    )
}


class InboxSendError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def deliver(item, client, now=None):
    """-> {"ok": true, "created"|"updated"|"duplicate": 1, "tiles_changed": n} or {"ok": false, "reason": word}."""
    try:
        source = agent_inbox.locate(client)
        result = agent_inbox.send(client, item, now=now, source=source)
        try:
            result.update(agent_inbox.recount_page(client, source=source))
        except (InboxError, NotionError):
            result["tiles_changed"] = -1
        return {"ok": True, **result}
    except InboxError as error:
        return {"ok": False, "reason": error.reason}
    except NotionError as error:
        return {"ok": False, "reason": f"notion_{str(error).lower()[:40]}"}


def route(op, args, tenant):
    return TENANT.get(args.get("workspace") or "Personal", "unknown"), "write", "agent-inbox", "notion"


def run(op, args, state):
    client = state.get("client")
    if client is None:
        raise InboxSendError("inbox_not_configured")
    item = {k: v for k, v in args.items() if k in FIELDS and k != "from"}
    item["from"] = str(args.get("_client") or "")
    out = deliver(item, client, state["now"]() if state.get("now") else datetime.now(timezone.utc))
    if not out.get("ok"):
        raise InboxSendError(str(out.get("reason") or "inbox_failed")[:60])
    return {k: v for k, v in out.items() if k != "ok"} | {
        "sender": item["from"],
        "workspace": item.get("workspace") or "Personal",
    }


def wire(environ, state):
    """The Notion client for the inbox from the environment; None (inbox_not_configured) until
    NOTION_READONLY_TOKEN is set."""
    token = (environ.get("NOTION_READONLY_TOKEN") or "").strip()
    state["client"] = (
        Client(
            {"NOTION_READONLY_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32},
            token_name="NOTION_READONLY_TOKEN",
            source_name="NOTION_INBOX_VIEW_SOURCE",
        )
        if token
        else None
    )
