"""Read-only Concord source for the Action Inbox (LIFE-710).

DURABLE BOUNDARY. Concord's own store (`.concord` in one repository, read through the CLI `concord -C <repo> messages --json`, the same read-only call the Orca Concord Room worker makes) is the only
durable record: message id, sender, recipient, content, created time, status, reply-to id, receipt. Concord has NO native approval or resume state. This source therefore adds no store, bus or
orchestrator and derives the Inbox view from messages alone, using an explicit envelope in the message content (a JSON object with `"lifeos_inbox": 1`). Free text is never mined: a message
without the envelope is ignored.

  approval_request  {"lifeos_inbox":1,"kind":"approval_request","workspace","title","source_url":"https://...","evidence":[https urls],"resume_after_approval":bool,"correlation_id"}
  approval_decision {"lifeos_inbox":1,"kind":"approval_decision","decision":"approve|reject|request_changes"}  -- a REPLY to the request, from a `human:` sender only

Status comes from the reply chain: no decision -> pending_approval; the newest human decision -> approved | rejected | changes_requested. An agent-sent decision never counts. Resume eligibility is
separate from approval: approved AND the request asked for resume AND the requesting agent is live in `concord who` right now. Missing `who` means not eligible, never guessed.

`open_reader(credential_ref)` returns `() -> (messages, live_agent_ids)`: the CLI's message records in its own field names and the ids from `concord who` (or None when it could not be read).
Nothing is fetched live while LIVE_READS_ENABLED is False (sources.py): the default reader refuses. No send, reply, prompt, resume or any other write is reachable from this file; a test scans it."""
import json

from lifeos.inbox.model import InboxError
from lifeos.inbox.sources import _Source, refuse_live

DECISIONS = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}
HUMAN_PREFIX = "human:"
MAX_MESSAGES = 80                                   # the same cap the Room worker enforces


def _envelope(message):
    content = message.get("content")
    if not isinstance(content, str) or not content.startswith("{"):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("lifeos_inbox") == 1 else None


def _valid_message(m):
    return isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ("messageId", "senderAgentId", "recipientAgentId", "content", "createdAt", "status"))


class ConcordSource(_Source):
    """One repository's Concord store for ONE workspace and account. Envelopes naming another workspace (or TGP) are dropped here, not shown."""
    source = "concord"

    def _read(self, read):
        messages, live = read()
        if not isinstance(messages, list) or len(messages) > MAX_MESSAGES or not all(_valid_message(m) for m in messages):
            raise InboxError("listing_incomplete")                                          # a partial or malformed answer is an error, never a smaller list
        self._live = frozenset(live) if isinstance(live, (list, tuple, set, frozenset)) else None
        return self._requests(messages)

    def _requests(self, messages):
        by_id = {m["messageId"]: m for m in messages}
        decisions = {}
        for m in sorted(messages, key=lambda m: (m["createdAt"], m["messageId"])):
            env = _envelope(m)
            if env and env.get("kind") == "approval_decision" and env.get("decision") in DECISIONS and m["senderAgentId"].startswith(HUMAN_PREFIX):
                parent = m.get("replyToMessageId")
                if parent in by_id and by_id[parent]["senderAgentId"] != m["senderAgentId"]:
                    decisions[parent] = (m, env["decision"])                                # the newest human decision wins
        out = []
        for m in messages:
            env = _envelope(m)
            if env and env.get("kind") == "approval_request" and env.get("workspace") == self.workspace:
                out.append((m, env, decisions.get(m["messageId"])))
        return out

    def _native(self, item):
        message, env, decision = item
        status = DECISIONS[decision[1]] if decision else "pending_approval"
        approved = status == "approved"
        eligible = approved and env.get("resume_after_approval") is True and self._live is not None and message["senderAgentId"] in self._live
        updated = (decision[0] if decision else message)["createdAt"]
        return {"id": message["messageId"], "url": env["source_url"], "title": env.get("title"), "body": "", "agent": message["senderAgentId"], "status": status, "updated": updated,
                "tz": "UTC", "evidence": list(env.get("evidence") or []) if status == "pending_approval" else [], "correlation_id": env.get("correlation_id"), "resume_eligible": eligible}


def concord_source(workspace, account, identity, open_reader=refuse_live):
    return ConcordSource(workspace, account, identity, open_reader=open_reader)
