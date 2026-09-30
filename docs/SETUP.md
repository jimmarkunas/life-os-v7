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
