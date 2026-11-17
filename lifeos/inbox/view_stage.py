"""LIFE-711: the `inbox-view` stage. QA proof of the Notion view writer on synthetic rows, and the hourly path behind a switch that is off."""
# python -m lifeos.run inbox-view [--live]. Jim, Nov 16 2026: first real Notion write on the QA area (LIFE OS QA Home), the existing NOTION_API_TOKEN integration, the writer owns only its own rows, a read-back
# receipt every run, rollback = archive what it created. Modes (INBOX_VIEW_MODE, set by diag.yml, with INBOX_VIEW_CONFIRM = QA and --live; anything less is a dry run that touches no network):
#   qa-proof     create the view under the QA parent if missing, write scenario A, read back, write scenario B (update, archive, keep), read back. Leaves the rows in place for Jim to look at.
#   qa-rollback  archive every row the writer owns and read back that none remain.
#   (none)       the hourly path: the switch ACTION_INBOX_VIEW (switches.json) is OFF, so this is a counts-only no-op; with it on there is still no live row source (the Followup feed and the other readers are
#                released separately), so it returns `unwired` and writes nothing.
# Output is counts and fixed codes only: no title, link, id or token ever reaches the log.
import os
from datetime import datetime, timezone

from lifeos.inbox import view_plan, view_synthetic, view_writer
from lifeos.inbox.model import InboxError
from lifeos.platform import switches
from lifeos.platform.notion_client import Client, NotionError

SWITCH = "ACTION_INBOX_VIEW"
MODES = ("qa-proof", "qa-rollback")
TOKEN = "NOTION_API_TOKEN"


def _client(env):
    token = (env.get(TOKEN) or "").strip()
    if not token:
        raise InboxError("view_config_missing")
    return Client({TOKEN: token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32}, source_name="NOTION_INBOX_VIEW_SOURCE")        # the data source is found by search, not configured


def _flat(prefix, receipt):
    out = {}
    for key, value in receipt.items():
        if isinstance(value, dict):
            for k, v in value.items():
                out[f"{prefix}_{key}_{k}"] = int(v)
        else:
            out[f"{prefix}_{key}"] = int(value)
    return out


def _dry(now):
    rows = view_synthetic.rows(now, "a")
    ops = view_plan.plan([], rows)
    return {"dry_run": 1, "would_create": sum(1 for o in ops if o[0] == view_plan.CREATE), "wanted": len(rows)}


def _qa(mode, live, env, client, now):
    if not live:
        return _dry(now)
    client = client or _client(env)
    try:
        if mode == "qa-rollback":
            _, source, _ = view_writer.ensure_view(client, create=False)
            return _flat("rollback", view_writer.rollback(view_writer.ViewWriter(client, source)))
        _, source, created = view_writer.ensure_view(client, create=True)
        writer = view_writer.ViewWriter(client, source)
        first = view_writer.reconcile(writer, view_synthetic.rows(now, "a"))
        second = view_writer.reconcile(writer, view_synthetic.rows(now, "b"))
    except NotionError as error:
        raise InboxError(f"view_notion_{str(error).lower()[:40]}") from None
    return {"view_created": int(created), **_flat("a", first), **_flat("b", second)}


def run(limit, live, environ=None, client=None, clock=None):
    """-> counts only. See the module note for the modes; an unrecognised mode is a no-op."""
    env = os.environ if environ is None else environ
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    mode = (env.get("INBOX_VIEW_MODE") or "").strip()
    if mode in MODES:
        return _qa(mode, bool(live) and (env.get("INBOX_VIEW_CONFIRM") or "").strip() == "QA", env, client, now)
    if not switches.state(SWITCH, env):
        return {"off": 1}
    return {"unwired": 1}


__all__ = ["MODES", "SWITCH", "run"]
