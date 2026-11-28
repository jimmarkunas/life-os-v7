"""The daily "Decisions for Jim <date>" Agent Inbox item: every open ticket that waits on Jim, in one short table.

`python -m lifeos.run jira-decisions [--live]`. It lists the LIFE tickets labelled `needs-jim`, in Blocked, or in Human UAT:
a callout (how many are left and how long they take), a table (ticket, what it is for, your step, where, status), an
"Already decided" list, and the exact reply phrase in bold. One item per Central date (key `decisions-<date>`): the first
live run after 6 AM CT creates it, later runs the same day update it, and Jim's Status and column order are never touched.
Read only on Jira; the Agent Inbox write reuses the existing delivery. A dry run writes nothing.
"""

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.jira import release
from lifeos.jira.search import search
from lifeos.platform import agent_inbox, inbox_send
from lifeos.platform.jira import Jira
from lifeos.platform.notion_client import Client

CT = ZoneInfo("America/Chicago")
SENDER = "jira-decisions"
START_HOUR = 6
JQL = (
    'project = LIFE AND issuetype != Epic AND statusCategory != Done AND (labels = "needs-jim" OR status in '
    '("Blocked", "Human UAT")) ORDER BY updated DESC'
)
FIELDS = "summary,status,labels,description"
ROWS_MAX, MINUTES_EACH = 25, 2
DETAILS_MAX = 12000
WHAT_MAX, STEP_MAX = 90, 150
OPEN_BOX, DONE_BOX = "☐", "✅"
CHECKBOX = re.compile(r"^\s*(?:[-*•]\s*)?(?:\[[ xX]\]\s*)?")
WHERE = ("Bitwarden", "GitHub", "Notion", "Todoist", "Jira", "Buzz", "Claude chat", "ChatGPT")


def clean(text, limit):
    """One line, no table breaks, nothing the inbox would refuse (secret-like or personal strings are removed)."""
    one = " ".join(str(text or "").replace("|", "/").split())
    one = agent_inbox._SECRET.sub("[removed]", agent_inbox._PERSONAL.sub("[removed]", one))
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def kinds(fields):
    out = []
    if "needs-jim" in (fields.get("labels") or []):
        out.append("needs-jim")
    name = (fields.get("status") or {}).get("name")
    if name in ("Blocked", "Human UAT"):
        out.append(name)
    return out


def first_open_step(fields):
    """-> (present, the first open `Jim must do first` item as plain words or None)."""
    present, open_items = release.jim_first(fields.get("description"))
    if not present or not open_items:
        return present, None
    return present, clean(CHECKBOX.sub("", open_items[0]), STEP_MAX)


def where_of(step, kind):
    for place in WHERE:
        if place.lower() in (step or "").lower():
            return place
    return "Claude chat" if kind == "needs-jim" else "Jira"


def row_for(issue):
    """-> {key, what, step, where, decided} for one ticket."""
    fields = issue.get("fields") or {}
    key, kind = issue["key"], kinds(fields)[0]
    present, step = first_open_step(fields)
    decided = kind == "needs-jim" and present and step is None
    if step is None:
        step = {
            "needs-jim": f"reply **release {key}** (approve it)",
            "Human UAT": f"check it, then reply **accept {key}** or **reject {key}**",
            "Blocked": f"read the blocker on the ticket, then reply **go {key}**",
        }[kind]
    elif kind == "needs-jim":
        step = f"{step}, then reply **release {key}**"
    return {
        "key": key,
        "what": clean(fields.get("summary"), WHAT_MAX),
        "step": step,
        "where": where_of(step, kind),
        "decided": decided,
    }


def render(issues, now):
    """-> (subject, key, details text) for the item; the first line becomes the callout."""
    rows = [row_for(i) for i in issues if kinds((i.get("fields") or {}))]
    open_rows = [r for r in rows if not r["decided"]]
    decided = [r for r in rows if r["decided"]]
    today = now.astimezone(CT)
    lines = [
        f"{len(open_rows)} decision{'s' if len(open_rows) != 1 else ''} left, about {MINUTES_EACH * len(open_rows)} minutes in all. Reply with the bold phrase in your step."
        if open_rows
        else "Nothing is waiting on you today."
    ]
    if open_rows:
        lines.append("| Ticket | What it is for | Your step | Where | Status |")
        for r in open_rows[:ROWS_MAX]:
            lines.append(f"| **{r['key']}** | {r['what']} | {r['step']} | {r['where']} | {OPEN_BOX} |")
        if len(open_rows) > ROWS_MAX:
            lines.append(f"{len(open_rows) - ROWS_MAX} more are open in Jira (label needs-jim, Blocked, Human UAT).")
    if decided:
        lines.append("# Already decided (no reply needed)")
        lines += [f"- {DONE_BOX} {r['key']}: {r['what']}" for r in decided[:ROWS_MAX]]
    subject = f"Decisions for Jim {today.strftime('%a %b')} {today.day}"
    return subject, "decisions-" + today.strftime("%Y-%m-%d"), "\n".join(lines)


def inbox_client(env):
    """The Jira job's own Notion integration (the one that writes the Daily Report card); the Agent Inbox sits under that page."""
    token = (env.get("NOTION_JIRA_TOKEN") or "").strip()
    return Client(
        {"NOTION_JIRA_TOKEN": token, "NOTION_INBOX_VIEW_SOURCE": "0" * 32},
        token_name="NOTION_JIRA_TOKEN",
        source_name="NOTION_INBOX_VIEW_SOURCE",
    )


def run(limit, live, environ=None, jira=None, client=None, now=None):
    env = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    jira = jira or Jira.from_env(env)
    issues = search(jira, JQL, FIELDS)
    subject, key, details = render(issues, now)
    shown = [i for i in issues if kinds(i.get("fields") or {})]
    out = {"tickets": len(shown), "decided": sum(1 for i in shown if row_for(i)["decided"]), "key": key}
    if now.astimezone(CT).hour < START_HOUR:
        return {**out, "written": "before_6am"}
    if not live:
        return {**out, "written": "dry_run", "lines": details.count("\n") + 1}
    reply = inbox_send.deliver(
        {
            "from": SENDER,
            "subject": subject,
            "urgency": "Today",
            "workspace": "Personal",
            "key": key,
            "details": details,
        },
        client or inbox_client(env),
        now,
        DETAILS_MAX,
    )
    if not reply.get("ok"):
        raise RuntimeError("DECISIONS_" + str(reply.get("reason") or "failed").upper()[:40])
    return {**out, "written": "created" if reply.get("created") else "updated"}
