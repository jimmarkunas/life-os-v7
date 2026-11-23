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

## Least privilege per job, and the residual risk (LIFE-861)

Each workflow job loads only the Bitwarden names the code it runs reads. `tools/secret_usage.py` works it out from the repository: for each job it reaches the stage modules a step runs (`lifeos.run <stage>`, or `python -m lifeos.<module>`), follows their `lifeos.*` imports, and lists every loaded name that none of that code reads or writes out (a name given only to an action or a shell line counts as used). `tests/contracts/test_secret_least_privilege.py` fails when a job loads such a name; the only exceptions are the (at most five) entries in `tests/contracts/secret_allowlist.json`, each with a one-line reason. Run `python tools/secret_usage.py --all` to see every job's names with the reason each is used.

Trimmed on Nov 22, 2026: the hourly `prep` job no longer loads the Fit profile (2), the Jobright login (2), TinyFish or the open-jobs contact (it only gates and sweeps), and the manual resolver lanes `jobright`, `linkedin` and `lensa` no longer load the three Gmail values or the open-jobs contact.

**Residual risk (written down, not fixed here).** One machine account token (`LIFEOS_BWS_RUNTIME_TOKEN`) can read every item in the project "LIFE OS GitHub Actions", whatever a job's list says: least privilege per job limits what a job is handed, not what a stolen or abused token can read. Options, none taken yet: more projects and machine accounts on a paid Bitwarden plan (one token per project, so a job's token reaches only its project); or Doppler per-config service tokens, which the LIFE-863 trial measures (free plan: one stored token per config, read-only).
