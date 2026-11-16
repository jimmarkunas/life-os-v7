# Followup credentials: one step at a time (LIFE-757, Nov 16 2026)

Who: Jim only for every step that touches a secret. Authority: Jim, chat, Nov 16 2026: "I need a complete build. If we need to share or make new credentials, then so be it", for the six Followup sources (Gmail/Outlook, Calendar, Jira, Todoist, Notion references, Interview OS). Asana is not read. TGP is excluded. Personal and Bytalos credentials are separate, never shared.

**Provisional names.** The names below are what the fetcher code (LIFE-757 PR-B) will read. They are provisional: if a fetcher needs a different shape, the agent changes this file and tells you before you create anything.

**How to use this file.** Do only the step an agent sends you. Never paste a token, key or password into chat, Jira, GitHub or a note. After each step you report only: the **NAME**, the **Secret ID** (a UUID; it is an identifier, not a secret), the **expiry date** and the **account label**. The agent then edits the loader and the credential inventory in code (you never edit YAML).

## 0. The fill-in table (Jim fills this once, before step 2)

Blank means "not decided yet". A row with no account is skipped until you name one: nothing is built against a blank.

| Source | Personal account | Bytalos account | Notes |
|---|---|---|---|
| Jira | Account: ______ | Account: ______ | Which Jira site and project(s) Followup may read: ______ |
| Notion references | Account: ______ | Account: ______ | Pages or databases Followup may read (names only): ______ |
| Interview OS | Account: ______ | Account: ______ | Which Interview OS pages or databases (names only): ______ |
| Calendar | Account: ______ | Account: ______ | Which calendar(s): ______ |
| Gmail | Account: ______ | Account: ______ | Which labels or the whole mailbox: ______ |
| Outlook | Account: ______ | Account: ______ | Which mailbox label: ______ |
| Todoist | Account: ______ | Account: ______ | Which projects: ______ |

The repository names no Bytalos account for any source, so every Bytalos cell is yours to fill or leave blank.

## 1. One-time: how a Bitwarden item is added (read once, nothing to do)

All hourly-job credentials live in the Bitwarden project **LIFE OS GitHub Actions** (D256, D257, `docs/platform/bitwarden-layout.md`). The workflows read them with `bws-load` as `secret-id > NAME` lines. Do not create a fourth project or machine account.

To add an item (Jim only): Bitwarden web app, **Secrets Manager**, **Secrets**, **New secret**. Name `lifeos/github-actions/<NAME>`, paste the value, project **LIFE OS GitHub Actions**, **Save**. To get its Secret ID: open the secret, then the row menu or detail panel, **Copy secret ID** (if you cannot find it, tell the agent; do not guess). After the value is saved, clear your clipboard: `pbcopy < /dev/null`.

How to check a value without showing it: copy the value from Bitwarden, then run the one check printed in that step. It prints only a length, a prefix and field names, never the secret itself. Clear the clipboard after.

The real proof of every credential is the **dry run** of the `followup` job (step 14): it prints, per source, only `ok` or `degraded` and counts.

## 2. Jira, Personal (Atlassian scoped token)

1. Open https://id.atlassian.com/manage-profile/security/api-tokens (Atlassian asks you to verify with an emailed code).
2. Click **Create API token with scopes**.
3. Name `lifeos-followup-jira-personal-ro`. Expiry **1 year** (the maximum). App **Jira**. Scope **read:jira-work** only. Create.
4. Copy the token once. Atlassian never shows it again.
5. In Bitwarden create `lifeos/github-actions/FOLLOWUP_JIRA_PERSONAL_TOKEN` with the value typed as `your-atlassian-email:the-token` (one line, a colon between).
6. Check (Jim only), with the value on your clipboard:

```bash
pbpaste | awk -F: '{print "email:", $1; print "token length:", length($2); print "token starts:", substr($2,1,5)}'
```

   Expect your email, a length well above 100 and `ATATT`. Then `pbcopy < /dev/null`.
7. Close the Atlassian token page.

**Report:** NAME, Secret ID, expiry date (the token page shows it; scoped tokens usually expire a day short of a year), account.
**Revoke:** the same Atlassian page, **Revoke** on that token; delete the Bitwarden item.

## 3. Jira, Bytalos

Only if a Bytalos Jira account exists (table in step 0). Repeat step 2 signed in as that account, with the name `lifeos-followup-jira-bytalos-ro` and the item `FOLLOWUP_JIRA_BYTALOS_TOKEN`. If Bytalos uses the same Atlassian login as Personal, still create a **separate** token: the rule is one credential per workspace.

## 4. Notion references and Interview OS, Personal (read-only integration)

One new integration covers both sources: Interview OS is read through Notion, so it needs no extra credential, only the right pages shared.

1. Open https://www.notion.so/profile/integrations and click **New integration**.
2. Name `LIFE OS Followup Read (Personal)`. Type **Internal**. Workspace: your Personal workspace.
3. Under **Capabilities** tick **Read content** only. Leave **Update content**, **Insert content** and **Read user information** off.
4. Save, then copy the **Internal Integration Secret** (starts `ntn_` or `secret_`).
5. In Bitwarden create `lifeos/github-actions/FOLLOWUP_NOTION_READONLY_TOKEN_PERSONAL`.
6. Check (Jim only), value on the clipboard:

```bash
pbpaste | awk '{print "length:", length($0); print "starts:", substr($0,1,4)}'
```

   Expect a length around 50 and `ntn_` (or `secr`). Then `pbcopy < /dev/null`.
7. Share only the pages or databases you named in step 0: open each in Notion, **...** menu, **Connections**, add **LIFE OS Followup Read (Personal)**. Share nothing else. Do not share the Daily Report page or any page this integration should never see.

**Report:** NAME, Secret ID, "no expiry" (Notion tokens do not expire), account, the list of page and database names you shared (names only, no links).
**Revoke:** Notion integrations page, delete the integration; delete the Bitwarden item.

## 5. Notion references and Interview OS, Bytalos

Only if Bytalos has its own Notion workspace. Repeat step 4 in that workspace with the name `LIFE OS Followup Read (Bytalos)` and the item `FOLLOWUP_NOTION_READONLY_TOKEN_BYTALOS`.

## 6. Calendar, Personal (Google service account, read-only share)

The existing calendar service account can change events. Followup gets its own, with a read-only share.

1. Open https://console.cloud.google.com/iam-admin/serviceaccounts in the same Google Cloud project that holds the other LIFE OS Google credentials.
2. **Create service account**. Name `lifeos-followup-calendar-ro`. Skip the optional role and user-access screens. Done.
3. Open the new account, **Keys**, **Add key**, **Create new key**, **JSON**, **Create**. A file downloads. If the console refuses (an organization policy blocks key creation), stop and tell the agent.
4. In Google Calendar, open the calendar you named in step 0: **Settings and sharing**, **Share with specific people or groups**, **Add people**, paste the service account's email address (it ends in `.iam.gserviceaccount.com`), permission **See all event details** only. Send.
5. In the same settings page, copy the **Calendar ID**.
6. In Bitwarden create two items: `lifeos/github-actions/FOLLOWUP_GCAL_READONLY_SA_JSON_PERSONAL` (the whole contents of the downloaded file) and `lifeos/github-actions/FOLLOWUP_GCAL_CALENDAR_ID_PERSONAL` (the Calendar ID; an identifier).
7. Check (Jim only), JSON on the clipboard:

```bash
pbpaste | python3 -c "import json,sys; d=json.load(sys.stdin); print('type:', d.get('type')); print('has private_key:', bool(d.get('private_key')), 'length', len(d.get('private_key','')))"
```

   Expect `type: service_account` and a private key length over 1,000. Then `pbcopy < /dev/null`.
8. Delete the downloaded file: move it to the Trash and empty the Trash (the key now lives only in Bitwarden).

**Report:** NAMEs and Secret IDs, expiry "none (key is valid until revoked)", account, the calendar name.
**Revoke:** Cloud console, the service account's **Keys**, delete the key; delete the Bitwarden items; remove the share in Calendar.

## 7. Calendar, Bytalos

Only if a Bytalos calendar exists. Repeat step 6 with the account `lifeos-followup-calendar-bytalos-ro`, in the Google Cloud project and calendar that belong to Bytalos, with the items `FOLLOWUP_GCAL_READONLY_SA_JSON_BYTALOS` and `FOLLOWUP_GCAL_CALENDAR_ID_BYTALOS`.

## 8. Gmail, Personal (read-only sign-in)

The repo already has the read-only sign-in helper `lifeos/platform/google_consent_readonly.py` (LIFE-423): scope `gmail.readonly` only, an allowlist, and the credential goes to a pipe, never the screen. It reuses the existing LIFE OS Gmail OAuth client, so no new Google client is created.

1. **Check the consent screen first.** Google Cloud console, **APIs and Services**, **OAuth consent screen**. If **Publishing status** says **Testing**, a refresh token expires after 7 days and Followup would go DEGRADED every week: tell the agent, and we set it to **In production** first (or accept weekly renewals).
2. In the V7 checkout on your Mac make a file `/tmp/followup-gmail-allowlist.json` with exactly one entry per mailbox (replace the placeholders; the label and size are free text and a number):

```json
[{"account": "you@example.com", "tenant": "personal", "service": "gmail", "label": "personal inbox", "size_cap": 500}]
```

3. Run (the output goes only to the clipboard, never to the screen):

```bash
python3 -m lifeos.platform.google_consent_readonly --account you@example.com --service gmail --allowlist /tmp/followup-gmail-allowlist.json | pbcopy
```

4. It asks for the client ID and secret of the LIFE OS Gmail web client (Bitwarden, project **LIFE OS EDGE/OPENCLAW**, items `openclaw/gmail/clientId` and `openclaw/gmail/clientSecret`; the secret is typed hidden). It prints a link: open it yourself in the browser on this Mac and click **Allow**. The screen shows a receipt with `scopes_granted` listing only `gmail.readonly` and `"stored": "pipe"`. Anything else means stop and tell the agent.
5. In Bitwarden create `lifeos/github-actions/FOLLOWUP_GMAIL_READONLY_REFRESH_PERSONAL` and paste the clipboard (a one-line JSON with the client id, secret and refresh token).
6. Check (Jim only):

```bash
pbpaste | python3 -c "import json,sys; d=json.load(sys.stdin); print('keys:', sorted(d)); print('refresh length:', len(d.get('refresh_token','')))"
```

   Expect keys `client_id`, `client_secret`, `refresh_token` and a refresh length over 50. Then `pbcopy < /dev/null` and `rm /tmp/followup-gmail-allowlist.json`.

**Report:** NAME, Secret ID, expiry "none until revoked", account.
**Revoke:** https://myaccount.google.com/permissions , remove the LIFE OS app grant for that account (this also kills the other Gmail refresh tokens signed in with the same client: do it only with the agent); delete the Bitwarden item.

## 9. Gmail, Bytalos

Only if a Bytalos Gmail account exists. Repeat step 8 with that account, `"tenant": "bytalos"` in the allowlist and the item `FOLLOWUP_GMAIL_READONLY_REFRESH_BYTALOS`.

## 10. Outlook, Personal (read-only device sign-in)

This credential is **not** a Bitwarden item. The refresh token is saved to the private database (`v7_outlook_token`) under an account label, as the existing Outlook sign-in does. Only `OUTLOOK_CLIENT_ID` is in Bitwarden, and it already exists.

**Agent prerequisite (not yours):** the `outlook` workflow only offers the labels `personal` and `work`, and a label may already hold a sign-in that can move mail. The agent adds the new labels `followup-personal` and `followup-bytalos` to the `outlook.yml` options in a small PR before you do anything. Do not re-sign `personal` or `work`: that would replace a token another job uses.

1. When the agent says the labels are in, open GitHub, **Actions**, workflow **outlook**, **Run workflow**.
2. Action `auth` (never `auth-write`), account `followup-personal`, tick **live** (it needs the Save box), **Run workflow**.
3. Open the run. It prints a short code and a Microsoft address. Open that address in Chrome, enter the code, sign in with the mailbox named in step 0, and approve **Read your mail** and **Read your calendars** only.
4. Run the workflow again with action `probe`: it prints counts for the mailbox, by position, never a subject or address.

**Report:** the account label, "refresh token in the private database", which mailbox it is.
**Revoke:** https://account.microsoft.com/privacy/app-access , remove the app for that mailbox; the agent deletes the label's row.

## 11. Outlook, Bytalos

Only if a Bytalos mailbox exists. Repeat step 10 with the label `followup-bytalos`.

## 12. Todoist, Personal (OAuth app with read-only scope)

V7 has no Todoist client or credential today; the existing Todoist token belongs to OpenClaw on Oracle and rotates (Bitwarden project **LIFE OS EDGE/OPENCLAW**, layout rule 4). Do not reuse it. Todoist's personal **API token is not scope-limited** (it can change and delete tasks), so do not use it either.

1. Open https://developer.todoist.com/appconsole.html and **Create a new app**. Name `LIFE OS Followup Read`.
2. Set the **OAuth redirect URL** to `http://127.0.0.1:8765/callback` (the agent's helper catches the reply on that loopback address). Save.
3. Copy the app's **Client ID** and **Client Secret**. Keep them for the helper below.
4. **Agent prerequisite (not yours):** the agent builds a small sign-in helper like the Gmail one (scope `data:read` only, the credential to a pipe) and tells you the exact command. If Todoist returns only a short-lived access token with a refresh token, the credential rotates, and it must live in the private database like the Outlook token, not in a read-only Bitwarden item. The agent confirms this from Todoist's current documentation before you run anything.
5. Run the helper the agent gives you, open the link, click **Allow**, and store the result where the agent says (Bitwarden item `FOLLOWUP_TODOIST_TOKEN_PERSONAL` for a non-expiring token, or the database for a rotating one).

**Report:** NAME or "database row", Secret ID if any, expiry, account.
**Revoke:** Todoist, Settings, Integrations, **Connected apps**, remove **LIFE OS Followup Read**; delete the Bitwarden item or database row.

## 13. Todoist, Bytalos

Only if a Bytalos Todoist account exists. Repeat step 12 signed in as that account, with its own OAuth app (or the same app, separate sign-in) and the item `FOLLOWUP_TODOIST_TOKEN_BYTALOS`.

## 14. What happens after your reports (the verification)

1. The agent adds each Secret ID to the `bws-load` list of the new `followup` job, adds an entry per credential (name, provider, purpose, expiry, no value) to `lifeos/platform/credential_list.py` so the Alert settings screen shows days left, and puts a rotation reminder in your calendar 35 days before each expiry.
2. The `followup` job runs as a **dry run** (no writes). Its log shows, per source and workspace, only `ok` or `degraded` and counts. A source that reports `degraded` with a fixed code means that credential or share is wrong; the agent tells you which step to repeat.
3. Only after every source you named is `ok`, and the tables exist (LIFE-756), does the agent flip the job to live. That flip is a one-line change.

## Rollback (any time, in any order)

Revoke at the provider (each step above lists where), delete the Bitwarden item, and tell the agent so it removes the line from the loader and the inventory. A revoked credential makes only that source DEGRADED; the other sources keep running and nothing is closed or removed because of a failed read.
