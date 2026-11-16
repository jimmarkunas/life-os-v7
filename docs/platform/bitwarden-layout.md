# Bitwarden layout (LIFE-733, D257)

The Free plan allows **3 projects** and **3 machine accounts**. Access is per project, not per item. Nothing here is a value; names and counts only.

| Project | Holds | Read by | Written by |
|---|---|---|---|
| LIFE OS GitHub Actions | the workflow credentials and identifiers (`lifeos/github-actions/*`, D256) | GitHub Actions Runtime (`bws-load`, `bws-plain`) | Jim, in the Bitwarden app |
| LIFE OS EDGE/OPENCLAW | everything OpenClaw reads, plus the rotating Outlook (personal, work) and Todoist tokens and the mailbox fingerprints | LIFE-OS-ORACLE | LIFE-OS-ORACLE (the wrappers, `bws secret edit`) |
| LIFE OS ALERTS | `ALERTS_ENABLED`, `ALERTS_MUTED_UNTIL`, `ALERT_SETTINGS` | GitHub Actions Runtime (`bws-plain`), LIFE-OS-ORACLE (ticker and Alert settings app through `alert_store`) | LIFE-OS-ORACLE (the Alert settings app, `bwsalerts` credential) |

Rules.
1. Do not create a fourth project or a fourth machine account. Put a new item in the project that has the right readers and writers, or ask Jim.
2. A moved item keeps its id; code that names ids (`bws-plain`, `alert_store`) is checked by `tests/platform/test_bitwarden_layout.py`.
3. The OpenClaw wrappers on the Oracle host find their project by the name `LIFE OS EDGE/OPENCLAW`. Rename that project only together with both wrappers (`openclaw-outlook.mjs`, `openclaw-todoist.mjs`).
4. Never move a Todoist token while a refresh is running: Todoist revokes a refresh token that is reused after 60 seconds.
