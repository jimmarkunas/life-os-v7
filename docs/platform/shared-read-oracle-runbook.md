# Shared read on Oracle: the Jira read through the secrets broker (LIFE-754, for the LIFE-721 live proof, Nov 16 2026)

Jim runs every command below on `lifeos-edge-01`; the agents run nothing there. Authority: Jim, chat, Nov 16 2026: "Jira, extend the broker" and "Yes, draft codex-svc". Merging the PR that holds this file changes nothing on Oracle: the host changes only when Jim installs the updated program (step 2).

**What it proves.** Two clients, Claude Code and Codex, each get their own broker purpose, their own read-only Atlassian token, their own Unix user and their own audit lines, and both read the same Jira issue (LIFE-696, chosen because its summary is under the broker's 100-character field limit). **Honest limit:** the calls run on Oracle (`sudo -u <client user> sudo -n ...`). That proves the broker, the grants, the denials, the log and the revoke. It does NOT prove the Mac-side client route (each Mac client reaching Oracle with its own SSH key); that needs a forced-command key per client, which is a new credential and its own approval. Until then the Mac route stays synthetic only.

**Jim only (secrets):** step 1 (Atlassian tokens) and step 4 (storing them). Everything else prints names, codes and counts.

## Order

0. Check (read-only). 1. Create two scoped tokens (Jim only). 2. Install the updated broker. 3. Users and sudo rules. 4. Store the two credentials (Jim only). 5. Register the two purposes. 6. First call per client. 7. Denial checks. 8. Read the log. 9. Revoke one client, then restore it. 10. Send the outputs back. Rollback is the last section.

## 0. Check first (read-only)

```bash
sudo ls -ld /etc/hermes-eval/creds /etc/lifeos-secrets /etc/lifeos-secrets/purposes.d && sudo ls /etc/hermes-eval/creds
sudo grep -rn lifeos-secret-use /etc/sudoers /etc/sudoers.d; sudo -l -U claude-svc
id claude-svc; getent passwd codex-svc || echo "codex-svc does not exist yet"
sha256sum /usr/local/sbin/lifeos-secret-use
```

Expected: `openrouter_api_key.cred` is listed; the sudoers search shows how `opc` may run the broker (no output means `opc` has full sudo, as the program header says); `sudo -l -U claude-svc` shows what `claude-svc` may already run (note it, the new rule only adds one line); `codex-svc` does not exist.

## 1. Atlassian side (Jim only)

In Chrome: https://id.atlassian.com/manage-profile/security/api-tokens, then **Create API token with scopes**. Make two tokens, one at a time, right before step 4 for each:

| Token name | Expiry | App and scope |
|---|---|---|
| `lifeos-shared-read-claudecode-ro` | 1 year (the Atlassian maximum) | Jira, `read:jira-work` only |
| `lifeos-shared-read-codex-ro` | 1 year (the Atlassian maximum) | Jira, `read:jira-work` only |

**Expiry and rotation (Jim, Nov 16 2026: 1 year).** The broker purpose has no expiry of its own: it works until the token expires, the credential is removed or the purpose file is removed. So the controls are (1) this runbook's revoke step (step 9, and the rollback section) and (2) a calendar reminder at day 330 to create a new token, re-run step 4 for that client and revoke the old token. Put the reminder in your calendar when you create each token.

Do not copy a token into chat, Jira, a file or a note. Keep the page open until you have stored it in step 4, then close it. Both tokens belong to your Atlassian account and differ only by token and purpose. Scoped tokens call `api.atlassian.com` with the cloud id, which is why the purpose URL below is not the `.atlassian.net` address.

## 2. Install the updated broker

On the Mac, from the repository after the PR has merged and you have pulled main:

```bash
shasum -a 256 lifeos/edge/secrets/lifeos_secret_use.py
scp lifeos/edge/secrets/lifeos_secret_use.py opc@lifeos-edge-01:/tmp/lifeos_secret_use.py
```

On Oracle:

```bash
sha256sum /tmp/lifeos_secret_use.py          # must equal the Mac value above
python3 -I -m py_compile /tmp/lifeos_secret_use.py && echo compiles
sudo cp -p /usr/local/sbin/lifeos-secret-use /usr/local/sbin/lifeos-secret-use.pre-life754
sha256sum /usr/local/sbin/lifeos-secret-use.pre-life754     # the "before" value, keep it
sudo install -o root -g root -m 0755 /tmp/lifeos_secret_use.py /usr/local/sbin/lifeos-secret-use
sha256sum /usr/local/sbin/lifeos-secret-use                  # the "after" value: equals the Mac value
sudo -n /usr/local/sbin/lifeos-secret-use openrouter_key_info
```

The last line must still answer `{"ok": true, ...}` with the three OpenRouter fields: the default Bearer path is unchanged. If not, roll back (below).

## 3. Users and sudo rules

`codex-svc` is new (no login shell, no password, no sudo except one broker call). `claude-svc` already exists.

```bash
sudo useradd --system --create-home --home-dir /var/lib/codex-svc --shell /usr/sbin/nologin codex-svc
getent passwd codex-svc
```

One rule per client, each limited to that client's own purpose. Validate before installing:

```bash
printf '%s\n' 'claude-svc ALL=(root) NOPASSWD: /usr/local/sbin/lifeos-secret-use jira_issue_read_claudecode' > /tmp/sudoers-claude-svc
printf '%s\n' 'codex-svc ALL=(root) NOPASSWD: /usr/local/sbin/lifeos-secret-use jira_issue_read_codex' > /tmp/sudoers-codex-svc
sudo visudo -cf /tmp/sudoers-claude-svc && sudo visudo -cf /tmp/sudoers-codex-svc
sudo install -o root -g root -m 0440 /tmp/sudoers-claude-svc /etc/sudoers.d/lifeos-secret-use-claude-svc
sudo install -o root -g root -m 0440 /tmp/sudoers-codex-svc /etc/sudoers.d/lifeos-secret-use-codex-svc
sudo -l -U codex-svc
```

The broker names the caller from `SUDO_USER`, so each call is logged as `claude-svc` or `codex-svc`.

## 4. Store the two credentials (Jim only)

Run this once per client. It asks for your Atlassian email and the token without echoing either, joins them as `email:token` in memory, and encrypts them straight into the broker's credential folder. Nothing goes to the screen, the shell history or a file. Use `bash` first because `read -s` is bash only.

```bash
bash
IFS= read -r -p 'Atlassian email: ' E; IFS= read -r -s -p 'Claude Code token: ' T; echo
printf '%s:%s' "$E" "$T" | sudo systemd-creds encrypt --name=jira_ro_claudecode - /etc/hermes-eval/creds/jira_ro_claudecode.cred; unset E T
```

```bash
IFS= read -r -p 'Atlassian email: ' E; IFS= read -r -s -p 'Codex token: ' T; echo
printf '%s:%s' "$E" "$T" | sudo systemd-creds encrypt --name=jira_ro_codex - /etc/hermes-eval/creds/jira_ro_codex.cred; unset E T
```

```bash
sudo chown root:root /etc/hermes-eval/creds/jira_ro_claudecode.cred /etc/hermes-eval/creds/jira_ro_codex.cred
sudo chmod 0600 /etc/hermes-eval/creds/jira_ro_claudecode.cred /etc/hermes-eval/creds/jira_ro_codex.cred
sudo ls -l /etc/hermes-eval/creds
exit
```

The credential names (`jira_ro_claudecode`, `jira_ro_codex`) must match exactly: the broker decrypts `--name=<name>` from `<name>.cred`. Then close the Atlassian token pages.

## 5. Register the two purposes

Each file fixes one credential, one caller and one fixed Jira read (the cloud id is an identifier, not a secret).

```bash
sudo tee /etc/lifeos-secrets/purposes.d/jira_issue_read_claudecode.json >/dev/null <<'EOF'
{
  "purpose": "jira_issue_read_claudecode",
  "secret": "jira_ro_claudecode",
  "callers": ["claude-svc"],
  "action": "http_get_fields",
  "auth": "basic",
  "url": "https://api.atlassian.com/ex/jira/1efae544-c7e8-44fb-9201-ad978e31076c/rest/api/3/issue/LIFE-696?fields=summary,status,updated",
  "fields": ["key", "fields.summary", "fields.status.name", "fields.updated"]
}
EOF
```

```bash
sudo tee /etc/lifeos-secrets/purposes.d/jira_issue_read_codex.json >/dev/null <<'EOF'
{
  "purpose": "jira_issue_read_codex",
  "secret": "jira_ro_codex",
  "callers": ["codex-svc"],
  "action": "http_get_fields",
  "auth": "basic",
  "url": "https://api.atlassian.com/ex/jira/1efae544-c7e8-44fb-9201-ad978e31076c/rest/api/3/issue/LIFE-696?fields=summary,status,updated",
  "fields": ["key", "fields.summary", "fields.status.name", "fields.updated"]
}
EOF
sudo chown root:root /etc/lifeos-secrets/purposes.d/jira_issue_read_*.json
sudo chmod 0644 /etc/lifeos-secrets/purposes.d/jira_issue_read_*.json
sudo ls -l /etc/lifeos-secrets/purposes.d
```

## 6. First call per client

```bash
sudo -u claude-svc sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_claudecode
sudo -u codex-svc sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_codex
```

Expected, one line each: `{"ok": true, "purpose": "jira_issue_read_<client>", "result": {"fields.status.name": "...", "fields.summary": "...", "fields.updated": "...", "key": "LIFE-696"}}`. Only those four fields, and no token. A `SECRET_FETCH_FAILED` usually means the token needs another minute after creation (Atlassian says up to a minute) or the scope is wrong.

## 7. Denial checks (each must refuse)

```bash
sudo -n /usr/local/sbin/lifeos-secret-use print_everything                              # SECRET_UNKNOWN_PURPOSE
sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_claudecode                    # SECRET_CALLER_NOT_ALLOWED (opc is not a caller)
sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_claudecode jira_ro_codex      # SECRET_BAD_ARGS (a credential name is never accepted)
sudo -u claude-svc sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_codex      # refused by sudo itself: claude-svc may only run its own purpose
```

## 8. Read the log

```bash
sudo tail -n 20 /var/log/lifeos-secret-use.jsonl
sudo grep -c 'ATATT' /var/log/lifeos-secret-use.jsonl
```

Expected: a START line and an outcome line per call, with `caller` `claude-svc` or `codex-svc`, the purpose, the credential NAME and `result` `OK`; the refusals from step 7 with their codes (the last one never reaches the broker); and a count of `0` for `ATATT` (an Atlassian token prefix).

## 9. Revoke one client, then restore it

```bash
sudo mv /etc/lifeos-secrets/purposes.d/jira_issue_read_codex.json /root/jira_issue_read_codex.json.revoked
sudo -u codex-svc sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_codex        # SECRET_UNKNOWN_PURPOSE
sudo -u claude-svc sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_claudecode  # still ok
sudo ls -l /etc/lifeos-secrets/purposes.d                                                # no other purpose changed
```

Restore (Jim's default: restore, so both clients keep their grant until the tokens expire after 1 year):

```bash
sudo install -o root -g root -m 0644 /root/jira_issue_read_codex.json.revoked /etc/lifeos-secrets/purposes.d/jira_issue_read_codex.json
sudo -u codex-svc sudo -n /usr/local/sbin/lifeos-secret-use jira_issue_read_codex
```

## 10. Send back

Paste (no secrets) the "before" and "after" hashes from step 2, the output of steps 6, 7, 9, and the last 20 log lines from step 8 into the chat. Claude Code writes the LIFE-721 receipt from them: endpoints, permitted action, request counts, the Mac-route gap, and what stays synthetic only.

## Rollback (any time, in reverse)

```bash
sudo rm -f /etc/lifeos-secrets/purposes.d/jira_issue_read_claudecode.json /etc/lifeos-secrets/purposes.d/jira_issue_read_codex.json
sudo rm -f /etc/hermes-eval/creds/jira_ro_claudecode.cred /etc/hermes-eval/creds/jira_ro_codex.cred
sudo rm -f /etc/sudoers.d/lifeos-secret-use-claude-svc /etc/sudoers.d/lifeos-secret-use-codex-svc
sudo userdel -r codex-svc
sudo install -o root -g root -m 0755 /usr/local/sbin/lifeos-secret-use.pre-life754 /usr/local/sbin/lifeos-secret-use
sudo -n /usr/local/sbin/lifeos-secret-use openrouter_key_info
```

Then revoke the two Atlassian tokens at https://id.atlassian.com/manage-profile/security/api-tokens (Jim only). The audit log is kept: it is evidence.
