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

## Variables (copyable from life-os-v2)
`LIFEOS_ACQ_SSH_HOST`, `_SSH_PORT`, `_SSH_USER`, `_SSH_KNOWN_HOSTS`, `LIFEOS_ACQ_DB_HOST`, `_DB_PORT`, `_DB_NAME`, `_DB_USER`

```bash
export SRC=jimmarkunas/life-os-v2 DST=jimmarkunas/life-os-v7
gh api --paginate "repos/$SRC/actions/variables" --jq '.variables[] | [.name,.value] | @tsv' |
while IFS=$'\t' read -r name value; do
  gh variable set "$name" --repo "$DST" --body "$value"
done
# secrets: one at a time, value typed/pasted at the prompt (never committed):
gh secret set GMAIL_OAUTH_CLIENT_ID --repo "$DST"
```

## Turn it on
1. Actions -> hourly -> Run workflow (live = **false**) -> read the dry-run counts.
2. Run again with live = **true**. Re-run: it must report 0 moved.
3. Set repo variable `V7_LIVE` = `true` so the hourly schedule is live.
