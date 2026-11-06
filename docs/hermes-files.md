# Where every Hermes file lives (canon)

Approved by Jim, Nov 5, 2026 (LIFE-315). Inventory with notes: Google Sheet "Hermes file inventory" in Jim's Drive folder "Hermes". Wiki rules: Notion, LIFE OS / Hermes. Background: LIFE-288 (one shared librarian brain), LIFE-308 (the wiki lives in Notion), LIFE-301 and LIFE-309 (permanent Hermes).

There is ONE Hermes, on the Oracle host `lifeos-edge-01`. The Hermes Desktop app on Jim's Mac is a remote client only; it is not a second Hermes.

| What | Master (the copy people edit) | Live copy (the copy Hermes uses) | Notes |
|---|---|---|---|
| Role files: `SOUL.md` for the main profile and the claude-hermes, developer, librarian, research and writer roles | This repo, `hermes/roles/<name>/SOUL.md` | Oracle, `~/.hermes/SOUL.md` and `~/.hermes/profiles/<name>/SOUL.md` | Deployed by `hermes/deploy_roles.sh` (dry run by default; Jim runs `--apply`). Edit here, never on the host. |
| The wiki | Notion: LIFE OS / Hermes / Hermes Wiki | none | The librarian role keeps it: it finds the Notion tools with tool_search and can search, read and create pages (checked by a read-only test on Nov 5; a real write is not yet tested). The main Hermes reads and writes it too and hands upkeep to the librarian. Jim is asked before any new entry. Claude Chat reads it through its own Notion connector. |
| About-Jim memory (`USER.md`) | Oracle (Hermes writes it) | Oracle | Never in this repo. A readable copy may be kept in Notion. |
| Memory folder `memories/` | Oracle | Oracle | Empty at the time of writing. Limits are in `config.yaml` (about 800 tokens of notes, about 500 about Jim). |
| Conversation history (`state.db`, per-profile databases), `sessions/`, `logs/` | Oracle | Oracle | Live databases: never in Notion, Drive or GitHub. Backed up nightly, encrypted, by the off-site backup (LIFE-129, extended for Hermes by LIFE-318); the copy in Drive is ciphertext only. |
| `config.yaml`, per-profile `config.yaml`, `.env`, `auth.json`, `mcp-tokens/`, encrypted credentials | Oracle | Oracle | Secrets. Never in this repo, Notion or Drive. |
| Skills in use | Oracle | Oracle | Most ship with the program. Skills installed from the ClawHub hub are listed by name in `hermes/skills.manifest`. A skill we write ourselves lives in `hermes/skills/<name>/`. |
| Program (`installs/`, `tools/`, `cache/`, `plugins/`) | Nous Research | Oracle | Not knowledge. |
| Host wiring: launch and guard scripts, systemd units, firewall rules, `hold.py`, `last_seen` | Oracle (some copies under `lifeos/edge/`) | Oracle | `hold.py` keeps the firewall rules loaded and `last_seen` is the clock check before each start: do not delete either. |
| Connector allowlist plugin (LIFE-320): limits one profile to the connectors Jim names | This repo, `hermes/plugins/connector-allowlist/` (code only) | Oracle, `~/.hermes/profiles/<name>/plugins/connector-allowlist/`; the allowlist is the file `allowlist.txt` there | Deployed by `hermes/deploy_plugin.sh --profile NAME --allow notion` (dry run by default; Jim runs `--apply`). It does not enable itself: the profile's `plugins.enabled` list is a separate, approved step. The allowlist belongs to the host profile and is never mastered here. |
| The V7 stage that asks Hermes to work | This repo: `lifeos/hermes_requests.py`, `.github/workflows/hermes-stage.yml` | GitHub Actions | Started by hand only. |
| Plans, decisions, receipts | Jira (LIFE) and Notion | none | Jira is the status record. |

Not used for Hermes files: Google Drive (Hermes has no Drive connector and a second copy would need syncing).

Rules:

1. Change a role file by pull request, then deploy it with `hermes/deploy_roles.sh`. The script refuses a file over 4096 bytes or one that looks like it contains a secret, backs up what it replaces, reads each file back, and restarts nothing.
2. Nothing private about Jim goes in this public repo: no `USER.md` text, no wiki text, no conversation text.
3. A profile gets the `connections` toolset only while the connector allowlist plugin is installed, enabled and tested in that profile: the toolset alone exposes every connector, including Robinhood, Plaid and PayPal, and Hermes has no per-profile connector limit of its own (the Nous account policy applies to every bot). As of Nov 5, 2026 no profile has the toolset except the main one.
