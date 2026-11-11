# Monitoring finish (LIFE-528)

Status as of Nov 10 2026. Nothing here changes when an alert fires or who gets it.

## Step 0: deployed-version drift check

`lifeos/platform/drift.py`, run inside the existing Oracle watchdog (`ticker.py watch`, every 15 minutes; no new scheduler). For each repo-owned surface it records the blob on `main`, the installed file's hash, the systemd unit that activates it and that unit's state.

| State | Meaning |
|---|---|
| DEPLOYED | installed hash equals main **and** the unit is active |
| DRIFT | hash differs, file is gone, or the unit is not running |
| UNKNOWN | main, the file or systemd could not be read. **Never DEPLOYED.** |

Surfaces: the ticker, the six platform modules the ticker installs (`gate`, `alerts`, `alert_registry`, `alert_settings`, `redact`, `drift`), and the off-site backup script. A test (`tests/platform/test_drift.py`) holds that list equal to the installer's `MODULES`.
Any DRIFT opens one quiet GitHub issue (`deploy-drift`) and one INFO push (`deploy_drift`, phone only); all DEPLOYED closes it (`deploy_drift_recovered`). UNKNOWN changes nothing.
**Not deployed yet.** The VM install is a protected host action, held for Jim's yes. Until then the VM has none of this and the stale ticker and registry files remain.

## Outcome 1: the gateway is the only alert sender

`tests/contracts/test_alert_senders.py` scans `lifeos/` and `.github/` for every notification pattern (ntfy, Telegram Bot API, Graph, email, Slack/Twilio, `openclaw message send`, macOS notification). Inventory:

| File | What it does | Decision |
|---|---|---|
| `lifeos/platform/alerts.py` | the one POST to the phone push service | **The gateway.** |
| `lifeos/whatsapp/oracle/forward_alerts.py` (+ its `.service`) | reads the gateway's topic, copies each alert to Telegram and WhatsApp | The relay: creates no alert; allowed. |
| `lifeos/edge/oracle/openclaw-jira.mjs` | Telegram **approval prompt with buttons** before a Jira write | Not an alert; allowed. Needs Jim's decision whether to route it through the gateway (the gateway has no buttons). |
| `lifeos/telegram/attention.py` | reads Telegram through TDLib; sends nothing | Read-only; no sender pattern; left as the ticket says. |
| `lifeos/imessage/*`, `lifeos/sources/alerts_app.py`, `lifeos/oraclecloud/*` | call `alerts.push` / `alerts.deliver`, or only read | Already on the gateway. |
| Mac launchd (`lifeos/imessage/mac/*.plist`) | start the iMessage attention reader and relay | No sender pattern found. |

Result: no code moved, because no direct sender outside the gateway exists apart from the approval prompt above.

## Outcome 2: credential expiry warnings: PARTIAL

Exists: the credential list (`lifeos/platform/credential_list.py`, dated entries only; LIFE-604 removed the older `lifeos/jobs/expiries.json`) feeds `lifeos/jobs/alerts.py`, which raises `expiry_*` (PAGE) within 7 days through the gateway. **Not done:** a list of provider credentials with expiry from provider evidence, and the dashboard display. Needs from Jim: which dashboard is "the existing dashboard", and the provider list to check (no provider was queried here).

## Outcome 3: old outcomes

| Outcome | Status | Implementation and proof |
|---|---|---|
| Run coverage (LIFETMP-62) | Done (counts) | `gate.coverage` and the daily check-in line "Coverage: N of 24 expected, M missed" (`tests/platform/test_gate.py`). A per-run health record store is not built. |
| Alert routes and fault injection (LIFETMP-63) | Partial | Unlisted key refused, wrong severity caught, direct send caught: `tests/edge/test_alert_registry.py` (`test_the_checks_turn_red_for_an_unlisted_alert_a_wrong_severity_and_a_direct_send`, `test_an_unlisted_key_is_refused_and_nothing_is_sent`). Missing: a live fault that shows one alert arriving once; Jim's own test push does that. |
| Failure isolation (LIFETMP-67) | Covered | Isolated jobs blank workflow secrets and `domains.yml` declares none at the top: `tests/contracts/test_workflow.py` (`IsolatedJobTests`, `DomainsWorkflowTests`). |
| Troubleshooting page | Not done | The cause-to-action text exists as the `meaning` and `action` fields of every code in `lifeos/platform/codes.py`; the private Notion page is not written. |
| Failure-mode register | Decision | The code registry (`codes.py`, LIFE-72) is the register; no second list. |
| Alert-noise root cause | Decision | Not evidenced in the repo. Next step is to read reminder counts after the drift check ships; no cause is claimed. |
