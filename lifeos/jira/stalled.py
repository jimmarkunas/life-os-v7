"""Name LIFE tickets idle in In Progress (30 minutes) or Agent Go (2 hours) with no `[waiting on]` last comment.

Read only, no phone push; `needs-jim` tickets are not stalled.
"""

import os
from datetime import datetime, timedelta, timezone

from lifeos.jira.search import search
from lifeos.platform.jira import Jira

IN_PROGRESS_MINUTES, AGENT_GO_MINUTES = 30, 120
WAITING = "[waiting on]"


def last_comment_text(jira, key):
    data = jira.get(f"/rest/api/3/issue/{key}/comment", {"orderBy": "-created", "maxResults": 1})
    items = (data or {}).get("comments") or []
    return json_text(items[0].get("body")) if items else ""


def json_text(node):
    if isinstance(node, dict):
        return node.get("text", "") + " ".join(json_text(c) for c in node.get("content") or [])
    return str(node or "")


def stalled(jira, now):
    found = search(
        jira, 'project = LIFE AND status in ("In Progress", "Agent Go") AND issuetype != Epic', "status,updated,labels"
    )
    out = []
    for issue in found:
        fields = issue.get("fields") or {}
        if "needs-jim" in (fields.get("labels") or []):
            continue
        limit = IN_PROGRESS_MINUTES if (fields.get("status") or {}).get("name") == "In Progress" else AGENT_GO_MINUTES
        try:
            updated = datetime.strptime(fields.get("updated") or "", "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            updated = None
        if updated is None or now - updated < timedelta(minutes=limit):
            continue
        if WAITING in last_comment_text(jira, issue["key"]).lower():
            continue
        out.append(issue["key"])
    return out


def run(limit, live, environ=None, jira=None, now=None):
    jira = jira or Jira.from_env(os.environ if environ is None else environ)
    keys = stalled(jira, now or datetime.now(timezone.utc))
    return {"stalled": len(keys), "keys": sorted(keys)}
