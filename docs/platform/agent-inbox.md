# Agent Inbox (LIFE-791)

Jim, Nov 16 2026: "tasks that need my attention from Agents and people that are not big enough to be a jira ticket, or too small to be a Todoist task ... a centralized place for the various agents to write to me
(similar to what we built with the Concord inbox in Orca)." It is **not** a view of Jira, Todoist or Calendar: "Jira stays the only task list". No row ever mirrors another system's record.

Where: the inline database "Agent Inbox list" on Jim's Agent Inbox page (the full-width page inside the home-page callout). Tabs: Inbox, Personal, Bytalos, Snoozed, Done. The five tiles above it count New items
per urgency (Now, Today, This week, FYI) and Snoozed items.

## Writing an item

Two ways, both ending as one row with Status = New.

1. **`send_to_jim`** (needs `NOTION_API_TOKEN`; the V7 Notion integration, which the database is shared with):
   `python -m lifeos.inbox.send_to_jim --from claude-code --subject "Approve the Buzz key rotation" --urgency Today --workspace Personal [--due 2026-11-20] [--link https://...] [--details "..."] [--key ID]`
   or `--mcp` for a stdio MCP server with one tool, `send_to_jim`, and the same arguments. It prints one JSON line of counts or a fixed reason word.
2. **A direct write** (an agent with the Notion connector, or a person adding a row by hand), with these property names:

| Property | Type | Value |
|---|---|---|
| Subject (title) | title | what it is, one line, 160 characters at most |
| From | text | an agent id or a person's name, 60 characters at most, never an address |
| Workspace | select | `Personal` or `Bytalos` |
| Urgency | select | `Now`, `Today`, `This week`, `FYI` |
| Status | select | `New` (always New on a new row) |
| Due | date | optional, a date or a date-time |
| Link | url | optional https link to the context (ticket, PR, page, thread) |
| Details | text | optional, 1,500 characters at most |
| Key | text | optional idempotency key: the same key never adds a second row |

Never write a secret, a token, a private key or personal data (an address, a phone or card number) into any property. The tool refuses them; a direct writer must not. Reply, Replied, Snooze until and Promoted to are Jim's.

## Rules the tool enforces

One database only (found by title; none or several is a refusal); Status is always New; subject, details and link are checked for secrets, personal data and synthetic hosts before anything is sent; one sender adds at most 20
rows an hour; the same key never adds a second row; output is counts and fixed words (never the item, the sender, a link or the token).

## What Jim does with an item

Mark it Done or Dismissed (Status), write a Reply, set Snoozed (with Snooze until) or Promoted (with Promoted to). Replies, snooze wake-up and promotion into Todoist or Jira are separate, later steps; promotion writes to
Todoist or Jira and needs Jim's explicit approval first.

## Proof and tiles

`diag.yml`, stage `inbox-proof`, confirm `INBOX`: sends three test items through `send_to_jim`, writes one straight into the database, reads the counts back, trashes every test row and reads back again. `inbox-recount`
rewrites the five tile numbers. The hourly followup run recounts them too while the switch `ACTION_INBOX_VIEW` is on.
