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

Scheduled rollover: every scheduled or tick run checks each non-`:readonly` board; once its sprint has ended and it is Monday midnight (America/Chicago) or later it carries unfinished work forward, closes the old sprint, starts the next and reads each step back. It writes only when the run is live. To stop V7 rolling a board over, add `:readonly` to that board's entry in `JIRA_BOARDS`.

### Outlook (Phase C)

Register one Azure app that signs in both mailboxes (steps are given one at a time in the PR conversation), then add the secret `OUTLOOK_CLIENT_ID` (the app's Application (client) ID). Sign each mailbox in once with the **outlook** workflow: action `auth`, account `personal` or `work`, **live** ticked. The run prints a short code and a Microsoft web address; open the address, enter the code, sign in. The refresh token is saved to the private database, not to a secret. Check with action `probe` (read-only; counts of recent and unread inbox messages per account, by position).

### Outlook calendar to Google (Phase D)

1. In Google Cloud: enable the Google Calendar API, create a service account, create a JSON key.
2. In Google Calendar open your calendar's settings, **Share with specific people**, add the service account's email with **Make changes to events**, and copy the **Calendar ID** (Integrate calendar; for your main calendar it is your own address).
3. Add Actions secrets `GCAL_SERVICE_ACCOUNT_JSON` (the whole key file) and `GCAL_CALENDAR_ID`.
4. Run the **calendar** workflow with **live** unticked first (counts only), then ticked. `CALENDAR_ACCOUNTS` (optional, comma-separated labels, default `personal`) chooses which signed-in mailboxes to add.

### Bills snapshot (read-only Notion)

1. Create a separate Notion integration with read-only access and share only the Bill Tracker data source with it.
2. Add the integration token as the Actions secret `NOTION_BILLS_TOKEN`.
3. Add the Bill Tracker data source ID as the Actions secret `NOTION_BILLS_DATA_SOURCE_ID`.
4. Run the **bills** workflow with **Save snapshot** unticked first; it reads every page and prints counts only. Tick it to save the complete snapshot to the private database.

### Calendar callout

1. Create a callout headed exactly **Calendar** on the Daily Report page.
2. Copy its block ID into the Actions secret `CALENDAR_CARD_BLOCK_ID`; the card reuses the existing `NOTION_JIRA_TOKEN` Daily Report integration.
3. Stop the ChatGPT task from writing the **Calendar** callout; V7 is its sole owner, following the JIRA Execution ownership rule.
4. Add the existing JIRA Execution callout block ID as the Actions secret `JIRA_CARD_BLOCK_ID`; V7 checks it remains unchanged when refreshing Calendar.

### Bills card (the "Bills: This Week" callout)

1. The callout is the existing **Bills: This Week** callout on the Daily LIFE OS Report: its first child must be the heading "Bills: This Week". Copy the callout's block link (the callout itself, not the heading) and put its block ID into the Actions secret `BILLS_CARD_BLOCK_ID`. The card reuses `NOTION_JIRA_TOKEN`, `CALENDAR_CARD_BLOCK_ID` and `JIRA_CARD_BLOCK_ID`; no new integration.
2. Stop any other writer from editing that callout; V7 is the sole owner of its text. The callout keeps its linked Bills table (that is where Paid is ticked): V7 writes one summary line under the heading and never removes or edits the table. Filter the table by relative dates (for example Due Date on or before today plus 7 days), never by fixed dates.
3. Until `BILLS_CARD_BLOCK_ID` exists the stage reports `not_configured` and changes nothing.

### Amazon Orders

1. Create a separate Notion integration with Read, Update and Insert content access, and share only the existing Amazon Orders data source with it.
2. Add its token as the Actions secret `NOTION_AMAZON_TOKEN`.
3. Add the existing Amazon Orders data source ID as the Actions secret `NOTION_AMAZON_DATA_SOURCE_ID`.
4. The Amazon Orders stage uses the existing Gmail OAuth secrets; the documented `gmail.modify` scope permits adding the existing Amazon label and removing INBOX.
