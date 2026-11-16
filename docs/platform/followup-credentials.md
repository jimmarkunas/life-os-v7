# Followup credentials: reuse first, only what is truly new is your step (LIFE-757, Nov 16 2026)

Who: Jim only for every step that touches a secret. Authority: Jim, chat, Nov 16 2026: "I need a complete build", then "reuse an existing credential wherever one exists; create new only where none exists", for the six Followup sources (Gmail/Outlook, Calendar, Jira, Todoist, Notion references, Interview OS). Asana is not read. TGP is excluded and never connected: TGP mail forwarded into your mailboxes is dropped by the readers. Personal and Bytalos stay separate wherever a separate account exists.

## Reused (nothing to do) vs New (your steps)

| Source | Personal | Bytalos |
|---|---|---|
| Jira | **Reused:** `JIRA_EMAIL` and `JIRA_API_TOKEN` (the hourly jira job already loads them). Nothing to do. | **Reused:** the same token. Nothing to do. |
| Notion references and Interview OS | **Reused:** `NOTION_API_TOKEN` (the main integration). **Your step 1:** share the top-level `LIFE OS` page with it. | Same workspace, same token. Nothing more. |
| Calendar | **Reused:** `GCAL_SERVICE_ACCOUNT_JSON` and the calendar the agenda job already reads. Nothing to do. | **Reused:** the same service account. **Your step 2:** share the Bytalos calendar with it. |
| Gmail | **Reused:** the existing `GMAIL_OAUTH_*` items. Nothing to do. | **New, your step 3:** `FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH`. |
| Todoist | **Reused:** the existing OpenClaw OAuth app (`openclaw/todoist/clientId`, `clientSecret`). **New, your step 4:** a `data:read` token, `FOLLOWUP_TODOIST_PERSONAL_TOKEN`. | None (Personal only). |
| Outlook | **Reused:** `OUTLOOK_CLIENT_ID`. **Your step 5, last:** three sign-ins; the tokens go to the private database. | None. |

That is five steps, in this order. Everything not in the New column is the agent's work.

**Say it plainly about the reused credentials.** The Jira token acts as your whole Atlassian account, and the Calendar service account can change events. Neither is read-only by itself. Read-only is enforced in the Followup code: the Jira fetcher can only make GET requests, the Calendar fetcher asks Google for the read-only scope, and tests enforce both. Personal (JFM, LIFE, LOS) and Bytalos (BAD, BPP) share the Jira token with separate project allowlists in the code.

**What the agent does** (you do none of this): adds the loader lines and expiry rows for each item you report, supplies the account labels (`FOLLOWUP_ACCOUNT_<SOURCE>_<WORKSPACE>[_N]`, short text such as `personal inbox`, never an address), and verifies each source with a counts-only dry run.

**How to use this file.** Do only the step an agent sends you. Never paste a token, key or password into chat, Jira, GitHub or a note. After each step you report only: the **NAME**, the **Secret ID** (a UUID; an identifier, not a secret) if a Bitwarden item was created, the **expiry date** and the account.

**Accounts (for reference, descriptions only, never addresses):** Jira is one Atlassian account split by project key. Calendar and Gmail have a Personal and a Bytalos (Google Workspace) account. Outlook has three personal mailboxes (an outlook.com one, a school one and a greatestpmever one) and no Bytalos mailbox. Todoist is Personal. TGP is shown in the Inbox as "Tracked, not connected".

## 1. Notion and Interview OS: share one page (no new integration, no new item)

The Followup job reuses `NOTION_API_TOKEN` (the main integration) for Notion references and Interview OS. A Notion integration can only read pages it is connected to, so the one thing to do is connect it to your top-level page. Child pages inherit the access.

1. In Notion, open the top-level page `LIFE OS`.
2. Click the **...** menu at the top right, then **Connections**.
3. Add the integration that owns `NOTION_API_TOKEN`. The list shows integrations by name. If you are unsure which one it is, pick the main one (the Job Ledger integration); the agent checks it afterwards with a counts-only dry run and tells you if it was the wrong one.

**This includes the Lifehub pages** (Medical, Therapy & Recovery, Dating & Style), as you chose all pages.

**Report:** the name of the integration you added. Nothing else: no new credential exists.
**Revoke:** Notion, `LIFE OS` page, **...**, **Connections**, remove the integration from the page.

## 2. Calendar, Bytalos: share the calendar with the existing service account

Calendar reuses `GCAL_SERVICE_ACCOUNT_JSON` (the existing service account, already used by the agenda job). Personal needs nothing. Bytalos needs one share and one identifier.

1. In Google Calendar, signed in as the Bytalos account, open the Bytalos calendar: **Settings and sharing**, **Share with specific people or groups**, **Add people**, paste the service account's email address (the agent gives it to you in chat; it ends in `.iam.gserviceaccount.com`), permission **See all event details** only. Send.
2. On the same settings page, copy the **Calendar ID**.
3. In Bitwarden, Secrets Manager, **New secret**, name `lifeos/github-actions/FOLLOWUP_GCAL_BYTALOS_CALENDAR_ID`, value the Calendar ID, project **LIFE OS GitHub Actions**. This is an identifier, not a secret. Then copy its **Secret ID**.

**Report:** the NAME, the Secret ID and the calendar's display name.
**Revoke:** remove the share in Google Calendar; delete the Bitwarden item.

## 3. Gmail, Bytalos: the one new Gmail credential

Personal Gmail reuses the existing `GMAIL_OAUTH_*` items. Only Bytalos is new: `FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH`. The repo has the read-only sign-in helper `lifeos/platform/google_consent_readonly.py` (LIFE-423): scope `gmail.readonly` only, an allowlist, and the credential goes to a pipe, never the screen. It reuses the existing LIFE OS Gmail OAuth client, so no new Google client is created.

1. **Check the consent screen first.** Google Cloud console, **APIs and Services**, **OAuth consent screen**. If **Publishing status** says **Testing**, a refresh token expires after 7 days and Gmail would go DEGRADED every week: tell the agent, and we set it to **In production** first (or accept weekly renewals). If the Bytalos account is in a different Google Workspace than the one that owns the OAuth client, Google may block the consent: tell the agent.
2. In the V7 checkout on your Mac make a file `/tmp/followup-gmail-allowlist.json` with exactly one entry (replace the placeholder; the label and size are free text and a number):

```json
[{"account": "you@example.com", "tenant": "bytalos", "service": "gmail", "label": "bytalos inbox", "size_cap": 500}]
```

3. Run (the output goes only to the clipboard, never to the screen):

```bash
python3 -m lifeos.platform.google_consent_readonly --account you@example.com --service gmail --allowlist /tmp/followup-gmail-allowlist.json | pbcopy
```

4. It asks for the client ID and secret of the LIFE OS Gmail web client (Bitwarden, project **LIFE OS EDGE/OPENCLAW**, items `openclaw/gmail/clientId` and `openclaw/gmail/clientSecret`; the secret is typed hidden). It prints a link: open it yourself in the browser on this Mac, signed in as the Bytalos account, and click **Allow**. The screen shows a receipt with `scopes_granted` listing only `gmail.readonly` and `"stored": "pipe"`. Anything else means stop and tell the agent.
5. Keep only the refresh token on the clipboard (it replaces the one-line JSON with the bare token and prints nothing):

```bash
pbpaste | python3 -c "import json,sys; sys.stdout.write(json.load(sys.stdin)['refresh_token'])" | pbcopy
```

6. In Bitwarden create `lifeos/github-actions/FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH` in the project **LIFE OS GitHub Actions** and paste the clipboard.
7. Check (Jim only). It prints only a length:

```bash
pbpaste | awk '{print "refresh length:", length($0)}'
```

   Expect a length over 50. Then clear the clipboard and delete the file:

```bash
pbcopy < /dev/null; rm /tmp/followup-gmail-allowlist.json
```

**Report:** the NAME, the Secret ID, expiry "none until revoked", and that it is the Bytalos mailbox.
**Revoke:** https://myaccount.google.com/permissions , remove the LIFE OS app grant for that Bytalos account; delete the Bitwarden item.

## 4. Todoist, Personal: a new token from the existing OpenClaw app

The Todoist OAuth app already exists, in Bitwarden project **LIFE OS EDGE/OPENCLAW** as `openclaw/todoist/clientId` and `openclaw/todoist/clientSecret`. **Do not create a new app.** Only the token is new: `FOLLOWUP_TODOIST_PERSONAL_TOKEN`, authorized with scope `data:read` only.

**Never reuse `openclaw/todoist/token`.** It rotates and OpenClaw depends on it. Todoist's personal API token is not scope-limited either, so do not use that.

**Agent first (not yours):** the agent builds a small sign-in helper under `lifeos/platform/` like the Gmail one (scope `data:read` only, the client id and secret read from Bitwarden on your Mac, the token to a pipe) and tells you the exact command. Before you run anything the agent also checks Todoist's current documentation that (a) the app's redirect and authorization settings accept `data:read` for a second token, and (b) issuing it does not invalidate OpenClaw's token. If the agent cannot confirm either, it says so and you do not proceed. If Todoist returns a short-lived token with a refresh token, the credential rotates and lives in the private database like Outlook, and the agent changes the reader to match.

1. When the agent sends the command, run it. Open the link it prints, signed in to your Todoist, and click **Allow** (it must list only reading tasks).
2. In Bitwarden create `lifeos/github-actions/FOLLOWUP_TODOIST_PERSONAL_TOKEN` in the project **LIFE OS GitHub Actions** and paste the clipboard. The GitHub runner cannot read the EDGE/OPENCLAW project, so the item must live in the GitHub Actions project.

**Report:** the NAME (or "database row"), the Secret ID, and the expiry.
**Revoke:** delete the Bitwarden item or database row. In Todoist, Settings, Integrations, **Connected apps**, revoke only if the agent says this grant is separately listed; otherwise leave it, since the same app serves OpenClaw.

## 5. Outlook, Personal, three mailboxes (your last step)

This is your **last** step. There are no Bitwarden items for Outlook. Microsoft rotates a refresh token every time it is used, so each token is saved in the private database (`v7_outlook_token`) under a label, as the existing Outlook sign-in does: `followup-personal-1`, `followup-personal-2`, `followup-personal-3`, one per mailbox. Only `OUTLOOK_CLIENT_ID`, which already exists, is used. There is no Bytalos Outlook mailbox, and TGP is never connected.

**Agent first (not yours):** the `outlook` workflow only offers the labels `personal` and `work`, and a label may already hold a sign-in that can move mail. The agent adds `followup-personal-1`, `followup-personal-2` and `followup-personal-3` to the sign-in workflow's options in a small PR, and tells you. Do not re-sign `personal` or `work`: that would replace a token another job uses.

Then you sign in once per mailbox (your outlook.com one, your school one, your greatestpmever one):

1. When the agent says the labels are in, open GitHub, **Actions**, workflow **outlook**, **Run workflow**.
2. Action `auth` (never `auth-write`), account `followup-personal-1`, tick **live** (it needs the Save box), **Run workflow**.
3. Open the run. It prints a short code and a Microsoft address. Open that address in Chrome, enter the code, sign in with that mailbox, and approve **Read your mail** and **Read your calendars** only.
4. Run the workflow again with action `probe`: it prints counts for the mailbox, by position, never a subject or address.
5. Repeat steps 2 to 4 for `followup-personal-2` and `followup-personal-3`, one mailbox at a time.

**Report:** the three labels, "refresh token in the private database", and which mailbox each is.
**Revoke:** https://account.microsoft.com/privacy/app-access , remove the app for that mailbox; the agent deletes the label's row.

## 6. What happens after your reports (the verification)

1. The agent adds each Secret ID to the `bws-load` list of the new `followup` job, adds an entry per credential (name, provider, purpose, expiry, no value) to `lifeos/platform/credential_list.py` so the Alert settings screen shows days left, and puts a rotation reminder in your calendar 35 days before each expiry.
2. The `followup` job runs as a **dry run** (no writes). Its log shows, per source and workspace, only `ok` or `degraded` and counts. A source that reports `degraded` with a fixed code means that credential or share is wrong; the agent tells you which step to repeat.
3. Only after every source is `ok`, and the tables exist (LIFE-756), does the agent flip the job to live. That flip is a one-line change.

## Rollback (any time, in any order)

Revoke at the provider (each step above lists where), delete the Bitwarden item, and tell the agent so it removes the line from the loader. A revoked credential makes only that source DEGRADED; the other sources keep running and nothing is closed or removed because of a failed read. The reused credentials are not revoked by this document: they belong to the jobs that already use them.
