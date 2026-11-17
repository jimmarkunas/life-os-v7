"""LIFE-791: the `inbox-view` stage: the Agent Inbox proof and tile recount (stage name kept for the registries)."""
# python -m lifeos.run inbox-view [--live]. Modes (INBOX_VIEW_MODE, set by diag.yml, with INBOX_VIEW_CONFIRM = INBOX and --live; anything less is a dry run that touches no network):
#   proof     send three TEST items through `send_to_jim` and write one more straight into the database the way a person or an agent with the Notion connector would, read the counts back, then move
#             every test row to the trash and read the counts back again. Leaves no row behind.
#   recount   write the five tile numbers from the database (what the hourly followup run does after a live read).
#   (none)    the switch ACTION_INBOX_VIEW (switches.json): on, the same recount; off, a counts-only no-op.
# Output is counts and fixed reason words only: no subject, sender, link, id or token ever reaches the log.
import os
from datetime import datetime, timezone

from lifeos.inbox import agent_inbox, send_to_jim
from lifeos.inbox.model import InboxError
from lifeos.platform import switches
from lifeos.platform.notion_client import NotionError

MODES = ("proof", "recount")
TEST_FROM = "claude-code"
TESTS = (("proof-1", "Now", "Personal"), ("proof-2", "Today", "Bytalos"), ("proof-3", "FYI", "Personal"))


def _items(now):
    stamp = now.strftime("%Y%m%d%H%M%S")
    return [{"from": TEST_FROM, "subject": f"TEST only: inbox proof {n} (safe to ignore)", "urgency": urgency, "workspace": workspace, "key": f"{n}-{stamp}",
             "details": "Test item written by the inbox proof; it is removed at the end of the run."} for n, urgency, workspace in TESTS], f"direct-{stamp}"


def _flat(prefix, counts):
    return {f"{prefix}_{label.lower().replace(' ', '_')}": number for label, number in counts.items()}


def _proof(env, client, now):
    client = client or send_to_jim._client(env)
    source = agent_inbox.locate(client)
    items, direct_key = _items(now)
    keys, out = [], {}
    try:
        for item in items:
            result = send_to_jim.deliver(item, environ=env, client=client, now=now)
            if not result["ok"]:
                raise InboxError(f"proof_send_{result['reason']}"[:60])
            keys.append(item["key"])
            out["sent"] = out.get("sent", 0) + 1
        direct = agent_inbox.normalize({"from": TEST_FROM, "subject": "TEST only: direct Notion write (safe to ignore)", "urgency": "Today", "workspace": "Bytalos", "key": direct_key})
        client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source}, "properties": agent_inbox.properties(direct)})
        keys.append(direct_key)
        out["direct"] = 1
        out.update(_flat("before", agent_inbox.counts(client, source)))
        out.update(agent_inbox.recount_page(client, source=source))
    finally:
        out["archived"] = agent_inbox.archive_keys(client, keys, source) if keys else 0
    out.update(_flat("after", agent_inbox.counts(client, source)))
    out.update({f"final_{k}": v for k, v in agent_inbox.recount_page(client, source=source).items()})
    return out


def run(limit, live, environ=None, client=None, clock=None):
    """-> counts only. A refusal raises InboxError with a fixed reason word."""
    env = os.environ if environ is None else environ
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    mode = (env.get("INBOX_VIEW_MODE") or "").strip()
    if mode in MODES:
        if not (live and (env.get("INBOX_VIEW_CONFIRM") or "").strip() == "INBOX"):
            return {"dry_run": 1}
        try:
            return _proof(env, client, now) if mode == "proof" else agent_inbox.recount_page(client or send_to_jim._client(env))
        except NotionError as error:
            raise InboxError(f"inbox_notion_{str(error).lower()[:40]}") from None
    if not switches.state("ACTION_INBOX_VIEW", env):
        return {"off": 1}
    return refresh(env, client)


def refresh(env, client=None):
    """The tile recount behind the switch, for the followup stage and the hourly path. Never raises: -> counts, or {"failed": 1} after one fixed-word line."""
    try:
        return agent_inbox.recount_page(client or send_to_jim._client(env))
    except (InboxError, NotionError) as error:
        print(f"inbox-tiles failed {getattr(error, 'reason', str(error).lower())}")
        return {"failed": 1}


__all__ = ["refresh", "run"]
