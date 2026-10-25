"""The outcome-code registry (LIFE-72): one checked-in definition of every fixed code the pipeline can emit.
A code is a fixed UPPER_SNAKE word that names a result: a refusal, a failure, a skip, a normal stop or a state. For each one the registry says which stage owns it,
what kind of result it is (CLASSES), whether it counts as an attempt against the job's retry budget, what it means in plain words, what to do next, and whether it
may ever alert (SEVERITIES: none, info, page). tests/contracts/test_codes.py fails when the code emits a code that is neither here nor in the shrinking
docs/codes_unregistered.txt list, so a new code cannot appear without a definition. No code pages yet: turning any severity up is Jim's call (LIFE-71, alerts are suspended).
Config and environment names that look like codes are listed in NOT_CODES."""
import ast
import re
from typing import NamedTuple

CLASSES = ("ok", "state", "terminal", "retry", "blocked", "degraded", "bug")
SEVERITIES = ("none", "info", "page")
CANDIDATE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


class Code(NamedTuple):
    stage: str
    klass: str          # ok: a normal success | state: a status word, not a result | terminal: final for this job | retry: tried again later | blocked: a person or a limit must act | degraded: ran, but part failed | bug: off contract, a code defect
    attempt: bool       # counts as an attempt against the job's retry budget
    meaning: str
    action: str
    severity: str = "none"


def _r(stage, klass, attempt, meaning, action, severity="none"):
    return Code(stage, klass, attempt, meaning, action, severity)


CODES = {
    # --- Cloud Fit: the Edge request and the backlog batch (lifeos/jobs/fit/cloud.py, edge.py, backlog.py)
    "FIT_EDGE_JUDGMENT_REJECTED": _r("fit", "terminal", True, "The model answered, but the strict judge refused the answer (not valid JSON, score or rationale).", "Counted per batch; the batch fails only when more than 3 and more than 10% of its calls are refused."),
    "FIT_EDGE_REQUEST_TOO_LARGE": _r("fit", "terminal", False, "This job's request is over the frame limit, so it was never sent.", "None; it is a fact about the job. A shortened description would clear it."),
    "FIT_EDGE_OVERSIZE": _r("fit", "terminal", False, "Oracle refused the request as too large.", "None for one job; three in a row with nothing judged stop the batch (a limit fault)."),
    "FIT_EDGE_ALL_TOO_LARGE": _r("fit", "bug", False, "Every request in the batch was too large: the limit, not the jobs, is wrong.", "Check the frame limit against Oracle's limits.", "info"),
    "FIT_EDGE_BUSY": _r("fit", "retry", False, "Oracle's model lanes are all busy.", "None; the next tick asks again."),
    "FIT_EDGE_NO_HOST": _r("fit", "blocked", False, "No Oracle host is configured for Cloud Fit.", "Set the Edge host in the Actions secrets."),
    "FIT_EDGE_NOT_CONFIGURED": _r("fit", "blocked", False, "Cloud Fit is not configured for this run.", "Set the Cloud Fit variables."),
    "FIT_EDGE_PROBE_FAILED": _r("fit", "retry", False, "The pre-call probe of Oracle failed.", "None; the next tick probes again."),
    "FIT_EDGE_CONNECT_FAILED": _r("fit", "retry", False, "The connection to Oracle failed.", "None; the next tick retries. Persistent: check Tailscale and the Edge service."),
    "FIT_EDGE_TIMEOUT": _r("fit", "retry", True, "Oracle did not answer within the wait.", "None; the row is asked again later."),
    "FIT_EDGE_NO_REPLY": _r("fit", "retry", False, "Oracle closed the connection without a reply.", "None; retried next tick."),
    "FIT_EDGE_OFF_CONTRACT": _r("fit", "bug", False, "Oracle's reply did not match the fit1 protocol.", "Compare Oracle's service version with the repository's.", "info"),
    "FIT_EDGE_NONCE_MISMATCH": _r("fit", "bug", False, "The reply echoed a different nonce than the request carried.", "Treat the reply as untrusted; check for a stale or crossed connection.", "info"),
    "FIT_EDGE_HASH_MISMATCH": _r("fit", "bug", False, "The reply's request hash did not match what was sent.", "Treat the reply as untrusted; check the Edge service.", "info"),
    "FIT_EDGE_LIMITS_OFF_CONTRACT": _r("fit", "bug", False, "Oracle's reported limits are missing or not numbers.", "Compare Oracle's limits answer with the protocol."),
    "FIT_EDGE_REPLY_TOO_LARGE": _r("fit", "bug", False, "Oracle's reply was over the 4 KiB reply limit.", "Check the Edge service."),
    "FIT_EDGE_RECEIPT_REJECTED": _r("fit", "degraded", True, "The receipt (usage, model, provenance) was missing a field or invalid.", "The row keeps its provenance; the field name is logged."),
    "FIT_EDGE_PURGE_FAILED": _r("fit", "degraded", True, "A model ran but Oracle could not purge its session, so the batch stops after recording the row (no score).", "Check Oracle's session purge before the next batch."),
    "FIT_EDGE_REFUSED": _r("fit", "blocked", False, "Oracle refused the request without a known reason.", "Read Oracle's refusal log."),
    "FIT_EDGE_REFUSED_QUOTA_RESERVE": _r("fit", "blocked", False, "The account's allowance reached Jim's reserve (stop at 50% used).", "A normal stop, not a failure. It clears when the allowance resets."),
    "FIT_EDGE_REFUSED_USAGE_UNAVAILABLE": _r("fit", "degraded", False, "Oracle could not read or check usage, so it made no model call.", "Check Oracle's usage meter."),
    "FIT_EDGE_FIXTURE_HASH": _r("fit", "bug", False, "A synthetic test fixture was modified, so it is not sent.", "Restore the fixture or re-export the manifest."),
    "FIT_EDGE_FIXTURE_MISSING": _r("fit", "bug", False, "The synthetic fixture named in the test is not in the manifest.", "Re-export the Edge fixtures."),
    "FIT_EDGE_REPLAY_ACCEPTED": _r("fit", "bug", False, "Oracle accepted a replayed nonce: its replay guard is not working.", "Stop sending; fix Oracle's nonce check.", "page"),
    "FIT_EDGE_ERROR": _r("fit", "retry", True, "Oracle reported an internal error.", "None; retried later. Persistent: read Oracle's service log."),
    "FIT_EDGE_MODEL_DRIFT": _r("fit", "blocked", False, "Oracle used a model other than the pinned one.", "Pin the model again before any more calls."),
    "FIT_EDGE_TIMEOUT_ORPHAN": _r("fit", "degraded", True, "The call timed out and its model session may still be running on Oracle.", "Check Oracle for an orphaned session."),
    "MODE_NOT_AUTHORIZED": _r("fit", "blocked", False, "The requested Cloud Fit mode is not authorized in this run.", "Authorize the mode through its switch, or run the allowed mode."),
    "CANARY_WITHOUT_PROMOTION": _r("fit", "blocked", False, "A canary call was requested outside the Fit stage that owns admission.", "Run the canary inside the Fit stage."),
    "CANARY_WRITE_CONFLICT": _r("fit", "retry", False, "The guarded canary write stored nothing: the row was no longer unasked.", "None; an unpersisted answer is never a success."),
    "CANARY_PROMOTION_FAILED": _r("fit", "degraded", False, "A canary answer is stored as this until promotion marks it CANARY.", "Re-run the promotion; never leave a stuck row."),
    "BACKLOG_TARGET_NOT_ELIGIBLE": _r("fit", "terminal", False, "The one named job is gone, already asked, unpublishable or excluded.", "None; nothing else is asked in its place."),
    "BACKLOG_WITHOUT_LEDGER_CHECK": _r("fit", "blocked", False, "A published job is asked only after its Notion page is read for human state.", "Enable the ledger check for the batch."),
    "PER_RUN_ZERO": _r("fit", "blocked", False, "The per-run call limit is zero.", "Raise the per-run limit if calls are wanted."),
    "PROFILE_MISSING": _r("fit", "blocked", False, "The Fit profile brief could not be read.", "Run the profile sync; check the brief page."),
    "PROFILE_REF_UNCONFIGURED": _r("fit", "blocked", False, "No profile reference is configured for Cloud Fit.", "Set the profile reference variable."),
    "STAGE_BUDGET": _r("fit", "degraded", False, "No time was left for even one call in this tick.", "A real shortfall; check the tick budget."),
    "LEDGER_SYNC_INCOMPLETE": _r("fit", "degraded", False, "Scores are kept but the Notion ledger write-back did not finish.", "None; the next sync retries."),
    "USAGE_UNAVAILABLE": _r("fit", "degraded", False, "Usage was missing, invalid or inconsistent in Oracle's refusal or the receipt.", "Check Oracle's usage meter."),
    "BELOW_RESOLUTION": _r("fit", "state", False, "An estimate is smaller than one call's resolution, so only a bound is given.", "None."),
    "NOT_IN_STORE": _r("fit", "state", False, "The job is not in the store.", "None."),
    "NOT_SCORED": _r("fit", "state", False, "The job has no admission or score yet.", "None."),
    "NO_DESCRIPTION": _r("fit", "state", False, "The job has no description to judge.", "None; Enrich fills it first."),
    "NOT_PUBLIC_POSTING": _r("fit", "terminal", False, "The job is not a public posting, so it is never sent to a model.", "None."),
    "NO_SUBSTANTIVE_TEXT": _r("fit", "terminal", False, "The description has no real text to judge.", "None; a better description would reopen it."),
    "TITLE_LOCATION": _r("fit", "terminal", False, "A place name left in the title is never sent (fail closed).", "None."),
    # --- the Oracle refusal reasons Fit reports as FIT_EDGE_REFUSED_<reason> (lifeos/jobs/fit/edge.py REASONS)
    "NOT_ALLOWLISTED": _r("fit", "blocked", False, "Oracle's allowlist does not contain this caller.", "Add the caller on Oracle."),
    "NONCE_REPLAYED": _r("fit", "bug", False, "Oracle saw this nonce before.", "Check for a crossed or retried connection."),
    "COUNTER_UNAVAILABLE": _r("fit", "blocked", False, "Oracle's daily counter cannot be read, so it fails closed.", "Check Oracle's counter file."),
    "DAILY_LIMIT": _r("fit", "blocked", False, "Oracle's own daily call cap is reached.", "A normal stop; it clears at the next daily reset."),
    "LIMIT_UNCONFIGURED": _r("fit", "blocked", False, "Oracle's fail-closed daily cap has no limit.json.", "Create the limit file on Oracle."),
    "REAL_DISABLED": _r("fit", "blocked", False, "Real (non-synthetic) calls are switched off on Oracle.", "Switch real calls on only when approved."),
    "BRIEF_MISSING": _r("fit", "blocked", False, "Oracle has no profile brief.", "Run the profile sync."),
    "BRIEF_UNSAFE": _r("fit", "blocked", False, "The brief failed Oracle's safety check.", "Review the brief page."),
    "BRIEF_HASH_MISMATCH": _r("fit", "blocked", False, "The brief on Oracle is not the one the request names.", "Run the profile sync."),
    "BRIEF_INVALID": _r("fit", "blocked", False, "The brief on Oracle is not valid.", "Run the profile sync; review the brief."),
    "PROFILE_MISMATCH": _r("fit", "blocked", False, "The profile on Oracle is not the one the request names.", "Run the profile sync."),
    "PURGE_FAILED": _r("fit", "degraded", False, "Oracle's session purge failed.", "Check Oracle before more calls."),
    "QUOTA_RESERVE": _r("fit", "blocked", False, "The account's allowance is at Jim's reserve.", "A normal stop; it clears when the allowance resets."),
    "QUOTA_UNCONFIGURED": _r("fit", "blocked", False, "The account's allowance has no policy.", "Set the quota policy on Oracle."),
    "METER_UNAVAILABLE": _r("fit", "degraded", False, "The account's allowance cannot be read.", "Check Oracle's meter."),
    "AUTH_UNAVAILABLE": _r("fit", "blocked", False, "The lane's OpenAI sign-in is expired or cooling down; no model answered.", "Sign in again on Oracle.", "info"),
    "TIMEOUT_ORPHAN": _r("fit", "degraded", True, "Oracle status: a timed-out call may still be running.", "Check Oracle for an orphaned session."),
    "MODEL_DRIFT": _r("fit", "blocked", False, "Oracle status: the model differs from the pinned one.", "Pin the model again."),
    # --- the Fit profile sync (lifeos/jobs/fit/profile_sync.py)
    "SYNC_WRITTEN": _r("profile-sync", "ok", False, "The profile block was written to Oracle.", "None."),
    "SYNC_UNCHANGED": _r("profile-sync", "ok", False, "The profile block is already current.", "None."),
    "SYNC_DRY_RUN": _r("profile-sync", "ok", False, "Dry run: nothing was written.", "None."),
    "SYNC_WOULD_WRITE": _r("profile-sync", "ok", False, "Dry run: a write would have happened.", "Approve the sync to write."),
    "SYNC_NOT_APPROVED": _r("profile-sync", "blocked", False, "A write was ready but not approved.", "Approve the sync."),
    "SYNC_PAGE_UNCONFIGURED": _r("profile-sync", "blocked", False, "The profile page is not configured.", "Set the profile page id."),
    "SYNC_PAGE_UNREADABLE": _r("profile-sync", "retry", False, "The profile page could not be read from Notion.", "None; retried next run."),
    "SYNC_PAGE_TOO_LARGE": _r("profile-sync", "blocked", False, "The profile page is over the size limit.", "Shorten the page."),
    "SYNC_NO_BLOCK": _r("profile-sync", "blocked", False, "The profile page has no sync block.", "Add the block."),
    "SYNC_BLOCK_NOT_JSON": _r("profile-sync", "blocked", False, "The sync block is not JSON.", "Fix the block."),
    "SYNC_BLOCK_KEYS": _r("profile-sync", "blocked", False, "The sync block has missing or extra keys.", "Fix the block."),
    "SYNC_BLOCK_SIZE": _r("profile-sync", "blocked", False, "The sync block is the wrong size.", "Fix the block."),
    "SYNC_BLOCK_TOO_LARGE": _r("profile-sync", "blocked", False, "The sync block is over the size limit.", "Shorten the block."),
    "SYNC_BLOCK_INVALID": _r("profile-sync", "blocked", False, "The sync block failed validation.", "Fix the block."),
    "SYNC_CAPABILITY_ID": _r("profile-sync", "blocked", False, "The block's capability id is not the expected one.", "Fix the block."),
    "SYNC_STORE_UNAVAILABLE": _r("profile-sync", "retry", False, "The Oracle store could not be reached.", "None; retried next run."),
    "SYNC_READBACK_MISMATCH": _r("profile-sync", "bug", False, "What was written did not read back the same.", "Check the Oracle store.", "info"),
    # --- Capacity card (lifeos/jobs/fit/capacity_card.py)
    "ANCHOR_NOT_FOUND": _r("capacity-card", "blocked", False, "The card's anchor block was not found on the page.", "Restore the anchor block."),
    "BLOCK_NOT_FOUND": _r("capacity-card", "blocked", False, "A block the card owns is missing.", "Let the card recreate it."),
    "BLOCK_SHAPE": _r("capacity-card", "bug", False, "A card block is not the shape the card expects.", "Check the page; the card never edits what it does not own."),
    "CARD_AMBIGUOUS": _r("capacity-card", "blocked", False, "More than one card of this kind was found.", "Remove the extra card."),
    "INSTALL_FAILED": _r("capacity-card", "degraded", False, "The card could not be installed.", "Retried next run."),
    "PLACE_NOT_FOUND": _r("capacity-card", "blocked", False, "No full-width divider to sit under was found, so nothing is inserted anywhere else.", "Restore the divider."),
    "RENDER_SHAPE": _r("capacity-card", "bug", False, "The rendered card is not the expected shape.", "A code defect."),
    "RETIRE_FAILED": _r("capacity-card", "degraded", False, "An old card could not be retired.", "Retried next run."),
    "RETIRE_NOT_OWNED": _r("capacity-card", "blocked", False, "The block to retire is not owned by the card.", "None; the card never retires what it does not own."),
    "VERIFY_FAILED": _r("capacity-card", "degraded", False, "The card read-back did not match what was written.", "Retried next run."),
    # --- Oracle Cloud card (lifeos/oraclecloud/card.py)
    "ORACLE_NOT_CONFIGURED": _r("oraclecloud", "blocked", False, "The Oracle Cloud card has no configuration.", "Set the card variables."),
    "ORACLE_OFF_CONTRACT": _r("oraclecloud", "bug", False, "Oracle's stats did not match the contract.", "Compare the Edge service version."),
    "ORACLE_READBACK_MISMATCH": _r("oraclecloud", "bug", False, "The card did not read back as written.", "Check the page."),
    "ORACLE_STALE": _r("oraclecloud", "degraded", False, "Oracle's stats are older than the freshness limit.", "Check the Edge stats service."),
    "ORACLE_UNREACHABLE": _r("oraclecloud", "retry", False, "The Oracle stats endpoint could not be reached.", "None; retried next tick."),
    "ORACLE_WRITE_FAILED": _r("oraclecloud", "degraded", False, "The card could not be written to Notion.", "Retried next tick."),
    "ORACLE_STATS_OFF_CONTRACT": _r("imessage", "bug", False, "The Oracle stats payload failed validation.", "Compare the Edge service version."),
    "AT_RISK": _r("oraclecloud", "state", False, "The Oracle instance reclaim state is AT_RISK.", "None."),
    # --- iMessage comms card and alerts endpoint (lifeos/imessage/*)
    "COMMS_NOT_CONFIGURED": _r("imessage", "blocked", False, "The comms card has no configuration.", "Set the card variables."),
    "COMMS_OFF_CONTRACT": _r("imessage", "bug", False, "The comms payload did not match the contract.", "Compare the Mac service version."),
    "COMMS_READBACK_MISMATCH": _r("imessage", "bug", False, "The card did not read back as written.", "Check the page."),
    "COMMS_SCRAMBLE_INVALID": _r("imessage", "bug", False, "The scrambled counts failed validation.", "Check the Mac service."),
    "COMMS_STALE": _r("imessage", "degraded", False, "The comms data is older than the freshness limit.", "Check the Mac service."),
    "COMMS_UNREACHABLE": _r("imessage", "retry", False, "The comms endpoint could not be reached.", "None; retried next tick."),
    "COMMS_WRITE_FAILED": _r("imessage", "degraded", False, "The card could not be written to Notion.", "Retried next tick."),
    "ACTION_REQUIRED": _r("imessage", "state", False, "Attention level: action is required.", "None."),
    "TIME_CRITICAL": _r("imessage", "state", False, "Attention level: time critical.", "None."),
    "NEEDS_ATTENTION": _r("imessage", "state", False, "Attention state: a message needs a look.", "None."),
    "NOT_NEEDED": _r("imessage", "state", False, "Attention state: nothing needed.", "None."),
    "ADAPTER_UNAVAILABLE": _r("imessage", "state", False, "The WhatsApp adapter is unavailable.", "Check the adapter."),
    "NOT_LINKED": _r("imessage", "state", False, "WhatsApp health: not linked.", "Link the device."),
    "NO_ENDPOINT": _r("imessage", "state", False, "No alerts endpoint is configured.", "Set the endpoint."),
    "IMSG_Q": _r("imessage", "state", False, "Alert queue marker: queued.", "None."),
    "IMSG_P": _r("imessage", "state", False, "Alert queue marker: pending.", "None."),
    "IMSG_S": _r("imessage", "state", False, "Alert queue marker: sent.", "None."),
    "IMSG_U": _r("imessage", "state", False, "Alert queue marker: unknown.", "None."),
    "IMSG_X": _r("imessage", "state", False, "Alert queue marker: expired.", "None."),
    "IMSG_M": _r("imessage", "state", False, "Alert queue marker line.", "None."),
    # --- Attention card (lifeos/attention/*)
    "ATTENTION_READBACK_MISMATCH": _r("attention", "bug", False, "The card did not read back as written.", "Check the page."),
    "ATTENTION_READ_FAILED": _r("attention", "retry", False, "The attention source could not be read.", "None; retried next tick."),
    "ATTENTION_READ_INCOMPLETE": _r("attention", "degraded", False, "The attention source read did not finish.", "None; retried next tick."),
    "ATTENTION_ROW_INVALID": _r("attention", "bug", False, "A source row did not match the schema.", "Check the source database."),
    "ATTENTION_SCHEMA_MISMATCH": _r("attention", "blocked", False, "The source database no longer has the properties the card reads.", "Restore the properties."),
    "SOURCE_UNREADABLE": _r("attention", "retry", False, "The card's source could not be read.", "None; retried next tick."),
    # --- Telegram attention (lifeos/telegram/attention.py)
    "CHAT_LIST_INCOMPLETE": _r("telegram", "degraded", False, "The chat list did not finish loading.", "None; retried."),
    "CREDENTIALS_UNAVAILABLE": _r("telegram", "blocked", False, "The Telegram credentials are not available.", "Install the credentials."),
    "DATABASE_UNAVAILABLE": _r("telegram", "blocked", False, "The Telegram database is locked or the key is wrong.", "Close the running login or fix the key."),
    "NOT_CONNECTED": _r("telegram", "retry", False, "Telegram is not connected yet.", "None; retried."),
    "NOT_SIGNED_IN": _r("telegram", "blocked", False, "Telegram is not signed in.", "Sign in on the Mac."),
    "REQUEST_NOT_ALLOWED": _r("telegram", "blocked", False, "The request is not on the allowed list.", "None; the reader is read-only by design."),
    "TDLIB_NOT_STARTED": _r("telegram", "retry", False, "The Telegram library has not started.", "None; retried."),
    # --- Close release on the Edge (lifeos/edge/oracle/close_release.py) and the gate (lifeos/edge/close_gate.py)
    "CLOSURE_INCOMPLETE": _r("close-release", "bug", False, "The release closure is missing files.", "Rebuild the release closure."),
    "ENTRY_POINT_MISSING": _r("close-release", "bug", False, "The release has no entry point.", "Rebuild the release."),
    "NOTION_ID_INVALID": _r("close-release", "blocked", False, "A Notion id in the release is not valid.", "Fix the id."),
    "NOT_A_COMMIT": _r("close-release", "blocked", False, "The release ref is not a commit.", "Name a full commit hash."),
    "NOT_GREEN": _r("close-release", "retry", False, "No hourly run has tested this commit yet.", "None; the auto-release waits for a green run."),
    "NOT_ON_MAIN": _r("close-release", "blocked", False, "The commit is not on main.", "Release a commit on main."),
    "NO_CLOSURE": _r("close-release", "bug", False, "The release has no closure list.", "Rebuild the release."),
    "NO_NODES": _r("close-release", "blocked", False, "No Node runtime was found on the Edge.", "Install Node."),
    "TREE_DIFFERS": _r("close-release", "bug", False, "The installed tree is not the commit's tree.", "Reinstall the release."),
    "UNSAFE_CLOSURE": _r("close-release", "bug", False, "The closure contains an unsafe path.", "Rebuild the release."),
    "READ_ONLY": _r("close-release", "blocked", False, "The Close gate is read-only here.", "None; a write was refused by design."),
    # --- WhatsApp relay (lifeos/whatsapp/oracle/forward_alerts.py)
    "NOT_DELIVERED": _r("whatsapp", "state", False, "The alert was not delivered.", "None."),
    "GAVE_UP": _r("whatsapp", "state", False, "Delivery was given up after retries.", "None."),
    # --- the alert-state diagnostic (lifeos/jobs/alertstate.py)
    "ALERT_STATE_MESSAGE_REF_INVALID": _r("alertstate", "blocked", False, "The message reference is not a 64-character hash.", "Pass the SHA-256 of the message id."),
    "ALERT_STATE_NOT_READ_ONLY": _r("alertstate", "bug", False, "The diagnostic did not get a read-only connection.", "A code defect; it never writes."),
    "ALERT_STATE_QUERY_FAILED": _r("alertstate", "degraded", False, "A diagnostic query failed.", "Retry."),
    "EXCLUDED_FIT": _r("alertstate", "state", False, "Job status: excluded by Fit.", "None."),
    "EXCLUDED_STALE": _r("alertstate", "state", False, "Job status: excluded as stale.", "None."),
    "EXCLUDED_UNRESOLVED": _r("alertstate", "state", False, "Job status: excluded as unresolved.", "None."),
}

for _reason in ("NOT_ALLOWLISTED", "NONCE_REPLAYED", "COUNTER_UNAVAILABLE", "DAILY_LIMIT", "LIMIT_UNCONFIGURED", "REAL_DISABLED", "BRIEF_MISSING", "BRIEF_UNSAFE",
                "BRIEF_HASH_MISMATCH", "BRIEF_INVALID", "PROFILE_MISMATCH", "PURGE_FAILED", "USAGE_UNAVAILABLE", "QUOTA_RESERVE", "QUOTA_UNCONFIGURED",
                "METER_UNAVAILABLE", "AUTH_UNAVAILABLE"):
    if "FIT_EDGE_REFUSED_" + _reason not in CODES:                  # the f-string forms Fit builds from Oracle's refusal reasons; same meaning as the bare reason
        _base = CODES[_reason]
        CODES["FIT_EDGE_REFUSED_" + _reason] = _r(_base.stage, _base.klass, _base.attempt, "Oracle refused: " + _base.meaning[0].lower() + _base.meaning[1:], _base.action, _base.severity)

NOT_CODES = frozenset((            # upper-case names that are configuration, not results: environment variables, secret names and setting keys
    "CREDENTIALS_DIRECTORY", "GITHUB_RUN_ID", "NOTION_ATTENTION_DATA_SOURCE_ID", "V7_CLOUD_FIT_PER_RUN",
))


def candidates(source):
    """The fixed code-shaped strings in one module's source (docstrings excluded): {code: first line number}."""
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CANDIDATE.match(node.value) and not isinstance(parent.get(node), ast.Expr):
            found.setdefault(node.value, node.lineno)
    return found


def lookup(code):
    """-> the Code for a registered code, else None."""
    return CODES.get(code)
