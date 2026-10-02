# One-time setup (you do this; values cannot be copied between repos)

GitHub never reveals secret values, so they must be pasted again. Variables *can* be copied.

## Secrets (repo Settings -> Secrets and variables -> Actions -> Secrets)
Step 1 needs only the first three. The rest are needed by later steps.

| Secret | Needed by |
|---|---|
| `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN` | step 1 (sweep) |
| `NOTION_API_TOKEN`, `NOTION_JOB_LEDGER_DATA_SOURCE_ID` | step 6 |
| `LIFEOS_ACQ_SSH_PRIVATE_KEY`, `LIFEOS_ACQ_DB_PASSWORD` | step 2 |
| `JOBRIGHT_EMAIL`, `JOBRIGHT_PASSWORD` | step 7 only |

The Gmail refresh token must include the `gmail.modify` scope (V2's did).

## Hostinger settings (also SECRETS, not variables)
GitHub variables are **not masked in logs**, and this repo is public, so every Hostinger setting is a secret:
`LIFEOS_ACQ_SSH_HOST`, `LIFEOS_ACQ_SSH_PORT`, `LIFEOS_ACQ_SSH_USER`, `LIFEOS_ACQ_SSH_KNOWN_HOSTS`,
`LIFEOS_ACQ_DB_NAME`, `LIFEOS_ACQ_DB_USER` (plus `LIFEOS_ACQ_SSH_PRIVATE_KEY` and `LIFEOS_ACQ_DB_PASSWORD` above).
The DB host/port are constants in code (loopback through the tunnel).

Add each one at a time in the repo's Settings -> Secrets and variables -> Actions -> Secrets, or with
`gh secret set NAME --repo OWNER/life-os-v7` (paste the value at the prompt; never commit it).

## Turn it on
1. Actions -> hourly -> Run workflow (live = **false**) -> read the dry-run counts.
2. Run again with live = **true**. Re-run: it must report 0 moved.
3. Set repo variable `V7_LIVE` = `true` so the hourly schedule is live.

## Outside timer (D31) - because GitHub drops scheduled runs
1. GitHub -> Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens -> Generate. Resource owner: you. Repository access: **Only select repositories -> life-os-v7**.
   Permissions: **Actions: Read and write** (nothing else). Expiration: 1 year (put a reminder in your calendar).
2. At a free cron service (cron-job.org works): new job, URL
   `https://api.github.com/repos/jimmarkunas/life-os-v7/actions/workflows/hourly.yml/dispatches`, method POST, schedule **every hour at minute 17**,
   headers `Authorization: Bearer <the token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, body `{"ref":"main","inputs":{"tick":"true"}}`.
3. Run it once from the service. A tick that arrives within 50 minutes of another run cancels itself (that is correct).
The token lives only at the timer service. Never put it in the repo.


## Phone alerts (D53)
1. Install the free **ntfy** app (iPhone or Android) and subscribe to your private topic name (Subscribe to topic; leave the server as ntfy.sh).
2. In GitHub: Settings > Secrets and variables > Actions > New repository secret: name `NTFY_TOPIC`, value = the same topic name. The name is the password: use a long random one and never commit it.
3. Test: Actions > watchdog > Run workflow > tick **Send a test push**. Your phone should buzz within seconds.

## Jira (D54)
Add these four as Actions **secrets** (never variables: this repo is public): `JIRA_BASE_URL` (your Atlassian site address, https://...), `JIRA_EMAIL`, `JIRA_API_TOKEN` (a Jira API token for that account), and `JIRA_BOARDS` (comma-separated `PROJECTKEY:boardid`; add `:all` after a board id to triage every non-Epic item, e.g. `AAA:5,BBB:38:all`; add `:readonly` for a team board you want in the report but not rolled over automatically, e.g. `CCC:60:readonly`). Boards may share a filter: V7 picks each project's own sprint by its name starting with the project key, and carries every unfinished item in a closing sprint forward whichever project it belongs to. Then run the workflow by hand with `jira_snapshot` or `jira_rollover` ticked and **Actually move mail** unticked: both are read-only dry runs and print counts only.

### Jira card in the Daily Report (Phase B)

V7 owns exactly one block on the Daily Report page: the **JIRA Execution** callout. Create a separate Notion integration for the card (name it e.g. "LIFE OS Jira Card") with Read, Update and Insert content capabilities, share only the Daily Report page with it, and add its token as secret `NOTION_JIRA_TOKEN` (the Interview integration stays insert-only and is not used). Add Actions secrets `JIRA_CARD_BLOCK_ID` (that callout's block id), optional `JIRA_CARD_PROJECTS` (comma-separated project keys to show; default the first board) and `JIRA_SITE_URL` (your Atlassian site address, for ticket links). The card is rendered only from the saved snapshot, so run `jira: report` (snapshot then card) with **Actually move mail** ticked to write it; without the tick it reads and checks only. The callout must open with a "JIRA Execution" heading; V7 refuses to touch anything else. Retire any other automation that writes the same callout.

GTV action: add secret `JIRA_GTV_EPIC` (the GTV epic's ticket key, which must belong to the first card project) and optionally `JIRA_GTV_CONTEXT_URL` (the GTV Notion page, shown as a link). The card then shows one unfinished, unblocked action from work under that epic: it stays until done or blocked, otherwise Jira priority, then earliest due date, then Jira rank. With no valid ticket it shows "Create Jira \u2014 define the next GTV action \u00b7 Needs Jira".
