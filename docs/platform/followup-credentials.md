# Followup credentials: one step at a time (LIFE-757, Nov 16 2026)

Who: Jim only for every step that touches a secret. Authority: Jim, chat, Nov 16 2026: "I need a complete build. If we need to share or make new credentials, then so be it", for the six Followup sources (Gmail/Outlook, Calendar, Jira, Todoist, Notion references, Interview OS). Asana is not read. TGP is excluded. Personal and Bytalos credentials are separate, never shared, except Jira, which creates no credential (see step 2).

**Final names (read from the code in LIFE-757 PR-B and PR-A, Nov 16 2026).** Every secret is a Bitwarden item named `lifeos/github-actions/<NAME>` in the project **LIFE OS GitHub Actions**. If a name below ever changes, the agent edits this file and tells you before you create anything.

| Source | Item name (the `<NAME>`) | What the value is |
|---|---|---|
| Jira, Personal and Bytalos | none to create | reuses the existing `JIRA_EMAIL` and `JIRA_API_TOKEN` items (step 2) |
| Notion references, Personal | `FOLLOWUP_NOTION_PERSONAL_READONLY_TOKEN` | the one read-only Notion token |
| Notion references, Bytalos | `FOLLOWUP_NOTION_BYTALOS_READONLY_TOKEN` | the same token again |
| Interview OS, Personal | `FOLLOWUP_INTERVIEW_PERSONAL_READONLY_TOKEN` | the same token again |
| Interview OS, Bytalos | `FOLLOWUP_INTERVIEW_BYTALOS_READONLY_TOKEN` | the same token again |
| Calendar, Personal | `FOLLOWUP_GCAL_PERSONAL_READONLY_SA_JSON` | whole service-account JSON file |
| Calendar, Bytalos | `FOLLOWUP_GCAL_BYTALOS_READONLY_SA_JSON` | the same, a separate service account |
| Gmail, Personal | `FOLLOWUP_GMAIL_PERSONAL_READONLY_REFRESH` | the bare refresh token (not JSON) |
| Gmail, Bytalos | `FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH` | the same, a separate sign-in |
| Outlook, Personal (three mailboxes) | none to create | tokens live in the private database under the labels `followup-personal-1` to `-3` (step 11) |
| Todoist, Personal | `FOLLOWUP_TODOIST_PERSONAL_TOKEN` | a `data:read` token |

No Bytalos Outlook and no Bytalos Todoist exist, so nothing is created for them. No TGP credential exists anywhere.

**Identifiers and labels (not secrets, but the job reads them by these exact names):**

| Name | What it holds | Shape (example only) |
|---|---|---|
| `FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID`, `FOLLOWUP_GCAL_BYTALOS_CALENDAR_ID` | the Calendar ID to read | an address-like id |
| `FOLLOWUP_ACCOUNT_<SOURCE>_<WORKSPACE>` (the agent supplies the Jira ones itself) and, for Outlook, `FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_1`, `_2`, `_3` | a short text label for each account (80 characters at most), never an address | `personal inbox` |
| `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`, `OUTLOOK_CLIENT_ID` | the existing app ids the Gmail and Outlook readers reuse | already in Bitwarden, nothing for you to create |

The Jira project split (Personal JFM, LIFE, LOS; Bytalos BAD, BPP) is fixed in code, so there is no item for it.

**How to use this file.** Do only the step an agent sends you. Never paste a token, key or password into chat, Jira, GitHub or a note. After each step you report only: the **NAME**, the **Secret ID** (a UUID; it is an identifier, not a secret), the **expiry date** and the **account label**. The agent then adds the loader line and the expiry row for that item in code (you never edit YAML), so after each step you only report and wait.

## 0. The fill-in table (Jim fills this once, before step 3)

Blank means "not decided yet". A row with no account is skipped until you name one: nothing is built against a blank.

| Source | Personal | Bytalos | Notes |
|---|---|---|---|
| Jira | The one Atlassian account; projects JFM, LIFE, LOS | The same account; projects BAD, BPP | Reuses the existing Jira token (Jim, Nov 16 2026: "we have WAY too many jira tokens"). No new credential. Read-only is enforced in the Followup code and each workspace reads only its own projects. |
| Notion references | One read-only integration; ALL pages under LIFE OS (Jim, Nov 16 2026: "all") | Bytalos pages inside the same workspace | Jim was told this includes the Lifehub pages (Medical, Therapy & Recovery, Dating & Style) and chose all. |
| Interview OS | Read through the same Notion integration | None | Stored under its own two item names (same token), see step 3. |
| Calendar | Personal Google calendar account | Bytalos Google Workspace calendar account | Primary calendar of each unless Jim names others. |
| Gmail | Personal Google account | Bytalos Google Workspace account | Whole mailbox. Messages from or forwarded from a TGP address are dropped (TGP mail is forwarded into these mailboxes). |
| Outlook | Three personal Outlook mailboxes (an outlook.com one, a school one, a greatestpmever one) | None | One scope per mailbox. Forwarded or direct TGP mail is dropped. |
| Todoist | The personal Todoist account | None | All projects. |
| TGP | Not connected, by design | | Shown in the Action Inbox as "Tracked, not connected". No credential, scope or read of any TGP source. (Jim, Nov 16 2026: "I want TGP tracked, but not necessarily connected".) |

Account identifiers are never committed: each is supplied by environment name when the job runs. Add to this table only descriptions, never addresses.

## 1. One-time: how a Bitwarden item is added (read once, nothing to do)

All hourly-job credentials live in the Bitwarden project **LIFE OS GitHub Actions** (D256, D257, `docs/platform/bitwarden-layout.md`). The workflows read them with `bws-load` as `secret-id > NAME` lines. Do not create a fourth project or machine account.

To add an item (Jim only): Bitwarden web app, **Secrets Manager**, **Secrets**, **New secret**. Name `lifeos/github-actions/<NAME>`, paste the value, project **LIFE OS GitHub Actions**, **Save**. To get its Secret ID: open the secret, then the row menu or detail panel, **Copy secret ID** (if you cannot find it, tell the agent; do not guess). After the value is saved, clear your clipboard: `pbcopy < /dev/null`.

How to check a value without showing it: copy the value from Bitwarden, then run the one check printed in that step. It prints only a length, a prefix and field names, never the secret itself. Clear the clipboard after.

The real proof of every credential is the **dry run** of the `followup` job (step 13): it prints, per source, only `ok` or `degraded` and counts.

## 2. Jira: nothing to do (reuses a token you already have)

No new token, no new Bitwarden item, nothing for you. Jim decided (Nov 16 2026: "we have WAY too many jira tokens. Let's reuse one we already have"). The Followup job reuses the existing Bitwarden items `JIRA_EMAIL` and `JIRA_API_TOKEN`, which the hourly jira job already loads (most likely the Atlassian token named `life-os-v7`; the agent confirms from the loader, by name only).

**Say it plainly:** that token acts as your whole Atlassian account, so it **can write to Jira**. Read-only is enforced in the Followup code, not by the token: the Jira fetcher can only make GET requests, and a test enforces that it exposes no write call. Personal (JFM, LIFE, LOS) and Bytalos (BAD, BPP) share this one token, each with its own project allowlist in the code, so a workspace's read covers only its own projects.

The agent adds the loader lines for these two existing items to the `followup` job and supplies the Jira account labels itself. You do nothing until the next step.

## 3. Notion: one read-only integration over all pages (Notion references and Interview OS, both workspaces)

You have one Notion workspace and chose all pages (Jim, Nov 16 2026), so one integration covers every source and no database ids are needed. The code reads the same token under four item names so each grant names its own workspace and source.

1. Open https://www.notion.so/profile/integrations and click **New integration**.
2. Name `LIFE OS Followup Read`. Type **Internal**. Workspace: your workspace.
3. Under **Capabilities** tick **Read content** only. Leave **Update content**, **Insert content** and **Read user information** off.
4. Save, then copy the **Internal Integration Secret** (starts `ntn_` or `secret_`).
5. In Bitwarden create the first item, `lifeos/github-actions/FOLLOWUP_NOTION_PERSONAL_READONLY_TOKEN`, and paste the secret.
6. Check (Jim only), value on the clipboard:

```bash
pbpaste | awk '{print "length:", length($0); print "starts:", substr($0,1,4)}'
```

   Expect a length around 50 and `ntn_` (or `secr`). Do not clear the clipboard yet.
7. Create three more items with the same pasted value, one at a time: `lifeos/github-actions/FOLLOWUP_NOTION_BYTALOS_READONLY_TOKEN`, `lifeos/github-actions/FOLLOWUP_INTERVIEW_PERSONAL_READONLY_TOKEN`, `lifeos/github-actions/FOLLOWUP_INTERVIEW_BYTALOS_READONLY_TOKEN`. Then `pbcopy < /dev/null`.
8. Share the integration with your top-level **LIFE OS** page: open the page `LIFE OS` in Notion, click the **...** menu, **Connections**, and add **LIFE OS Followup Read**. Every page beneath it inherits the access, and you do not name databases or pages. This includes the Lifehub pages (Medical, Therapy & Recovery, Dating & Style), as you chose all. Share nothing outside LIFE OS.

**Report:** the four NAMEs and Secret IDs and "no expiry" (Notion tokens do not expire).
After you report, the agent adds the loader lines and the expiry rows.
**Revoke:** Notion integrations page, delete the integration; delete the four Bitwarden items.

## 4. Notion, Bytalos

Nothing to do. Bytalos reads the same workspace through its item names created in step 3.

## 5. Calendar, Personal (Google service account, read-only share)

The existing calendar service account can change events. Followup gets its own, with a read-only share.

1. Open https://console.cloud.google.com/iam-admin/serviceaccounts in the same Google Cloud project that holds the other LIFE OS Google credentials.
2. **Create service account**. Name `lifeos-followup-calendar-personal-ro`. Skip the optional role and user-access screens. Done.
3. Open the new account, **Keys**, **Add key**, **Create new key**, **JSON**, **Create**. A file downloads. If the console refuses (an organization policy blocks key creation), stop and tell the agent.
4. In Google Calendar for your personal account, open the calendar you named in step 0: **Settings and sharing**, **Share with specific people or groups**, **Add people**, paste the service account's email address (it ends in `.iam.gserviceaccount.com`), permission **See all event details** only. Send.
5. In the same settings page, copy the **Calendar ID**.
6. In Bitwarden create `lifeos/github-actions/FOLLOWUP_GCAL_PERSONAL_READONLY_SA_JSON` (the whole contents of the downloaded file).
7. Check (Jim only), JSON on the clipboard:

```bash
pbpaste | python3 -c "import json,sys; d=json.load(sys.stdin); print('type:', d.get('type')); print('has private_key:', bool(d.get('private_key')), 'length', len(d.get('private_key','')))"
```

   Expect `type: service_account` and a private key length over 1,000. Then `pbcopy < /dev/null`.
8. Delete the downloaded file: move it to the Trash and empty the Trash (the key now lives only in Bitwarden).
9. In Bitwarden create two plain items: `lifeos/github-actions/FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID` (the Calendar ID from step 5) and `lifeos/github-actions/FOLLOWUP_ACCOUNT_CALENDAR_PERSONAL` (the text `personal calendar`).

**Report:** the three NAMEs and Secret IDs, expiry "none (key is valid until revoked)", the calendar name.
After you report, the agent adds the loader lines and the expiry row.
**Revoke:** Cloud console, the service account's **Keys**, delete the key; delete the Bitwarden items; remove the share in Calendar.

## 6. Calendar, Bytalos

Repeat step 5 with the service account `lifeos-followup-calendar-bytalos-ro`, in the Google Cloud project and the Bytalos calendar account, with the items `FOLLOWUP_GCAL_BYTALOS_READONLY_SA_JSON`, `FOLLOWUP_GCAL_BYTALOS_CALENDAR_ID` and `FOLLOWUP_ACCOUNT_CALENDAR_BYTALOS` (the text `bytalos calendar`).

## 7. Gmail, Personal (read-only sign-in)

The repo already has the read-only sign-in helper `lifeos/platform/google_consent_readonly.py` (LIFE-423): scope `gmail.readonly` only, an allowlist, and the credential goes to a pipe, never the screen. It reuses the existing LIFE OS Gmail OAuth client, so no new Google client is created. The Followup reader takes the client id and secret from the existing `GMAIL_OAUTH_CLIENT_ID` and `GMAIL_OAUTH_CLIENT_SECRET` items, so the item you create here holds only the refresh token.

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
5. Keep only the refresh token on the clipboard (the clipboard now holds a one-line JSON; this replaces it with the bare token and prints nothing):

```bash
pbpaste | python3 -c "import json,sys; sys.stdout.write(json.load(sys.stdin)['refresh_token'])" | pbcopy
```

6. In Bitwarden create `lifeos/github-actions/FOLLOWUP_GMAIL_PERSONAL_READONLY_REFRESH` and paste the clipboard.
7. Check (Jim only):

```bash
pbpaste | awk '{print "refresh length:", length($0)}'
```

   Expect a length over 50. Then `pbcopy < /dev/null` and `rm /tmp/followup-gmail-allowlist.json`.
8. In Bitwarden create the plain item `lifeos/github-actions/FOLLOWUP_ACCOUNT_GMAIL_PERSONAL` with the text `personal inbox`.

**Report:** NAMEs and Secret IDs, expiry "none until revoked", which mailbox.
After you report, the agent adds the loader lines (including the two existing client items) and the expiry row.
**Revoke:** https://myaccount.google.com/permissions , remove the LIFE OS app grant for that account (this also kills the other Gmail refresh tokens signed in with the same client: do it only with the agent); delete the Bitwarden items.

## 8. Gmail, Bytalos

Repeat step 7 with the Bytalos mailbox, `"tenant": "bytalos"` in the allowlist, the item `FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH` and the label item `FOLLOWUP_ACCOUNT_GMAIL_BYTALOS` (the text `bytalos inbox`).

## 9. Todoist, Personal (OAuth app with read-only scope)

V7 has no Todoist client or credential today; the existing Todoist token belongs to OpenClaw on Oracle and rotates (Bitwarden project **LIFE OS EDGE/OPENCLAW**, layout rule 4). Do not reuse it. Todoist's personal **API token is not scope-limited** (it can change and delete tasks), so do not use it either.

1. Open https://developer.todoist.com/appconsole.html and **Create a new app**. Name `LIFE OS Followup Read`.
2. Set the **OAuth redirect URL** to `http://127.0.0.1:8765/callback` (the agent's helper catches the reply on that loopback address). Save.
3. Copy the app's **Client ID** and **Client Secret**. Keep them for the helper below.
4. **Agent prerequisite (not yours):** the agent builds a small sign-in helper like the Gmail one (scope `data:read` only, the credential to a pipe) and tells you the exact command. If Todoist returns only a short-lived access token with a refresh token, the credential rotates, and it must live in the private database like the Outlook token, not in a read-only Bitwarden item, and the reader changes to take it from there. The agent confirms this from Todoist's current documentation before you run anything.
5. Run the helper the agent gives you, open the link, click **Allow**, and store the result where the agent says: Bitwarden item `FOLLOWUP_TODOIST_PERSONAL_TOKEN` for a non-expiring token, or the database for a rotating one. Then create the plain item `FOLLOWUP_ACCOUNT_TODOIST_PERSONAL` with the text `personal todoist`.

**Report:** NAME or "database row", Secret ID if any, expiry.
After you report, the agent adds the loader lines and the expiry row.
**Revoke:** Todoist, Settings, Integrations, **Connected apps**, remove **LIFE OS Followup Read**; delete the Bitwarden items or database row.

## 10. Todoist, Bytalos

Nothing to do. Todoist is Personal only.

## 11. Outlook, Personal, three mailboxes (your last step)

This is your **last** credential step. The tokens are **not** Bitwarden items and there are no `FOLLOWUP_OUTLOOK_READONLY_PERSONAL_N` names. Microsoft rotates a refresh token every time it is used, so each token is saved in the private database (`v7_outlook_token`) under a label, as the existing Outlook sign-in does: `followup-personal-1`, `followup-personal-2`, `followup-personal-3`, one per mailbox (your outlook.com one, your school one and your greatestpmever one). Only `OUTLOOK_CLIENT_ID`, which already exists, is used. There is no Bytalos Outlook mailbox, and TGP is never connected (TGP mail forwarded into these mailboxes is dropped by the reader).

**Agent first (not yours):** the `outlook` workflow only offers the labels `personal` and `work`, and a label may already hold a sign-in that can move mail. The agent adds `followup-personal-1`, `followup-personal-2` and `followup-personal-3` to the `outlook.yml` options in a small PR and tells you. Do not re-sign `personal` or `work`: that would replace a token another job uses.

Then you sign in once per mailbox:
1. When the agent says the labels are in, open GitHub, **Actions**, workflow **outlook**, **Run workflow**.
2. Action `auth` (never `auth-write`), account `followup-personal-1`, tick **live** (it needs the Save box), **Run workflow**.
3. Open the run. It prints a short code and a Microsoft address. Open that address in Chrome, enter the code, sign in with the first mailbox, and approve **Read your mail** and **Read your calendars** only.
4. Run the workflow again with action `probe`: it prints counts for the mailbox, by position, never a subject or address.
5. Repeat steps 2 to 4 for `followup-personal-2` and `followup-personal-3`, one mailbox at a time.
6. In Bitwarden create three plain label items, `FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_1`, `_2` and `_3`, with the texts `outlook.com mailbox`, `school mailbox` and `greatestpmever mailbox`.

**Report:** the three labels, "refresh token in the private database", and which mailbox each is.
After you report, the agent adds the loader lines for the label items.
**Revoke:** https://account.microsoft.com/privacy/app-access , remove the app for that mailbox; the agent deletes the label's row.

## 12. Outlook, Bytalos

Nothing to do. There is no Bytalos Outlook mailbox.

## 13. What happens after your reports (the verification)

1. The agent adds each Secret ID to the `bws-load` list of the new `followup` job, adds an entry per credential (name, provider, purpose, expiry, no value) to `lifeos/platform/credential_list.py` so the Alert settings screen shows days left, and puts a rotation reminder in your calendar 35 days before each expiry.
2. The `followup` job runs as a **dry run** (no writes). Its log shows, per source and workspace, only `ok` or `degraded` and counts. A source that reports `degraded` with a fixed code means that credential or share is wrong; the agent tells you which step to repeat.
3. Only after every source you named is `ok`, and the tables exist (LIFE-756), does the agent flip the job to live. That flip is a one-line change.

## Rollback (any time, in any order)

Revoke at the provider (each step above lists where), delete the Bitwarden item, and tell the agent so it removes the line from the loader and the inventory. A revoked credential makes only that source DEGRADED; the other sources keep running and nothing is closed or removed because of a failed read.
