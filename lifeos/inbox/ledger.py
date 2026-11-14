"""Durable, atomic state for the Action Inbox: seen events, pending confirmations and claimed actions, in the existing V7 MySQL store (LIFE-697).

`Ledger(connect)`: `connect` is a context manager yielding a connection, `lifeos.platform.db.connect` in production (autocommit: every statement below is one atomic statement) or a test shim.
Atomicity comes from unique keys with `INSERT IGNORE` (a claim) and compare-and-set `UPDATE ... WHERE state = ...` (a transition); no lock is held, so any number of workers and restarts agree.
Tables v7_inbox_events, v7_inbox_intents, v7_inbox_actions are created by `ensure_schema`, which is NOT run by anything yet (a live schema change needs Jim's approval).
Stored: ids, states, fixed reason words, hashes and UTC times. Never a title, description, credential, challenge code or exception text. Action states: claimed, in_progress, uncertain,
completed, not_applied, expired; intent states: pending, confirmed, expired, invalidated, locked, superseded."""
import hashlib
from datetime import timezone

from lifeos.platform.fixed_code import fixed

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_inbox_events (
        event_id VARCHAR(120) NOT NULL, kind VARCHAR(16) NOT NULL, seen_at VARCHAR(24) NOT NULL,
        PRIMARY KEY (event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_inbox_intents (
        intent_id CHAR(64) NOT NULL, event_id VARCHAR(120) NOT NULL, principal_id VARCHAR(80) NOT NULL, source VARCHAR(20) NOT NULL, workspace VARCHAR(20) NOT NULL,
        account VARCHAR(80) NOT NULL, record_id VARCHAR(400) NOT NULL, row_id VARCHAR(80) NOT NULL, action VARCHAR(40) NOT NULL, revision VARCHAR(80) NOT NULL,
        event_at VARCHAR(24) NOT NULL, nonce VARCHAR(64) NOT NULL, code_mac CHAR(64) NOT NULL, expires_at VARCHAR(24) NOT NULL, attempts INT NOT NULL, state VARCHAR(12) NOT NULL,
        PRIMARY KEY (intent_id), UNIQUE KEY uq_inbox_intent_event (event_id), UNIQUE KEY uq_inbox_intent_nonce (nonce), KEY ix_inbox_intent_row (row_id, state)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_inbox_actions (
        request_id CHAR(64) NOT NULL, principal_id VARCHAR(80) NOT NULL, source VARCHAR(20) NOT NULL, workspace VARCHAR(20) NOT NULL, account VARCHAR(80) NOT NULL,
        record_id VARCHAR(400) NOT NULL, action VARCHAR(40) NOT NULL, revision VARCHAR(80) NOT NULL, status_before VARCHAR(80) NOT NULL, nonce VARCHAR(64) NOT NULL,
        slot CHAR(4) NULL, state VARCHAR(12) NOT NULL, reason VARCHAR(60) NOT NULL, needs_human TINYINT NOT NULL, claimed_at VARCHAR(24) NOT NULL, updated_at VARCHAR(24) NOT NULL,
        PRIMARY KEY (request_id), UNIQUE KEY uq_inbox_action_nonce (nonce), UNIQUE KEY uq_inbox_action_slot (record_id, action, revision, slot)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
INTENT_COLUMNS = ("intent_id", "event_id", "principal_id", "source", "workspace", "account", "record_id", "row_id", "action", "revision", "event_at", "nonce", "code_mac", "expires_at", "attempts",
                  "state")
ACTION_COLUMNS = ("request_id", "principal_id", "source", "workspace", "account", "record_id", "action", "revision", "status_before", "nonce", "slot", "state", "reason", "needs_human",
                  "claimed_at", "updated_at")
LIVE = "live"                                  # the slot value while an action holds its (record, action, revision); NULL once the source confirmed nothing was applied


def stamp(moment):
    """A UTC time as a sortable text: the only form stored, so a text comparison is a time comparison."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(text):
    return hashlib.sha256(str(text).encode()).hexdigest()


class LedgerError(RuntimeError):
    """`str(error)` is the fixed code INBOX_LEDGER_UNAVAILABLE; the driver's message never leaves."""


class Ledger:
    def __init__(self, connect):
        self._connect = connect

    def _run(self, sql, args=(), fetch=None):
        with fixed(Exception, LedgerError, "INBOX_LEDGER_UNAVAILABLE"), self._connect() as connection, connection.cursor() as cursor:
            count = cursor.execute(sql, tuple(args))
            if fetch == "one":
                return cursor.fetchone()
            if fetch == "all":
                return cursor.fetchall()
            return count

    def ensure_schema(self):
        for statement in SCHEMA:
            self._run(statement)

    # ---- events: a delivery id is handled once, ever -------------------------------------------------------------------------------------------------------------------------------------

    def claim_event(self, event_id, kind, now):
        """True the first time an event id is seen (atomic across workers and restarts), False for every repeat."""
        return self._run("INSERT IGNORE INTO v7_inbox_events (event_id, kind, seen_at) VALUES (%s,%s,%s)", (event_id, kind, stamp(now))) == 1

    # ---- confirmations --------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def open_intent(self, fields):
        """Record one pending confirmation (a dict with the INTENT_COLUMNS except attempts and state); True when it was new. Older pending ones for the same row are superseded first."""
        self._run("UPDATE v7_inbox_intents SET state='superseded' WHERE row_id=%s AND state='pending'", (fields["row_id"],))
        columns = [c for c in INTENT_COLUMNS if c not in ("attempts", "state")]
        return self._run(f"INSERT IGNORE INTO v7_inbox_intents ({', '.join(columns)}, attempts, state) VALUES ({', '.join(['%s'] * len(columns))}, 0, 'pending')",
                         [fields[c] for c in columns]) == 1

    def pending_intent(self, row_id):
        row = self._run(f"SELECT {', '.join(INTENT_COLUMNS)} FROM v7_inbox_intents WHERE row_id=%s AND state='pending' ORDER BY event_at DESC LIMIT 1", (row_id,), "one")
        return dict(zip(INTENT_COLUMNS, row, strict=True)) if row else None

    def intent(self, intent_id):
        row = self._run(f"SELECT {', '.join(INTENT_COLUMNS)} FROM v7_inbox_intents WHERE intent_id=%s", (intent_id,), "one")
        return dict(zip(INTENT_COLUMNS, row, strict=True)) if row else None

    def spend_attempt(self, intent_id, limit):
        """Count one try against a pending confirmation: True while it is still pending and under `limit`; once it is not, the confirmation is locked."""
        if self._run("UPDATE v7_inbox_intents SET attempts=attempts+1 WHERE intent_id=%s AND state='pending' AND attempts<%s", (intent_id, limit)) == 1:
            return True
        self.close_intent(intent_id, "locked")
        return False

    def close_intent(self, intent_id, state):
        """pending -> `state` (confirmed | expired | invalidated | locked), once: True only for the caller that made the change."""
        return self._run("UPDATE v7_inbox_intents SET state=%s WHERE intent_id=%s AND state='pending'", (state, intent_id)) == 1

    # ---- actions: one claim per request, nonce and (record, action, revision) --------------------------------------------------------------------------------------------------------------

    def action(self, request_id):
        row = self._run(f"SELECT {', '.join(ACTION_COLUMNS)} FROM v7_inbox_actions WHERE request_id=%s", (request_id,), "one")
        return dict(zip(ACTION_COLUMNS, row, strict=True)) if row else None

    def claim_action(self, fields, now):
        """-> ("claimed", None) | ("replay", row) | ("refused", reason). Atomic: the insert fails on a reused request id, a spent nonce or a revision that is already held."""
        columns = ("request_id", "principal_id", "source", "workspace", "account", "record_id", "action", "revision", "status_before", "nonce")
        stamped = stamp(now)
        if self._run(f"INSERT IGNORE INTO v7_inbox_actions ({', '.join(columns)}, slot, state, reason, needs_human, claimed_at, updated_at) VALUES ({', '.join(['%s'] * len(columns))}, "
                     "'live', 'claimed', 'claimed', 0, %s, %s)", [fields[c] for c in columns] + [stamped, stamped]) == 1:
            return "claimed", None
        prior = self.action(fields["request_id"])
        if prior:
            same = (prior["principal_id"], prior["record_id"], prior["action"]) == (fields["principal_id"], fields["record_id"], fields["action"])
            return ("replay", prior) if same else ("refused", "idempotency_key_reused")
        if self._run("SELECT 1 FROM v7_inbox_actions WHERE nonce=%s", (fields["nonce"],), "one"):
            return "refused", "authorization_used"
        holder = self._run("SELECT state FROM v7_inbox_actions WHERE record_id=%s AND action=%s AND revision=%s AND slot='live'",
                           (fields["record_id"], fields["action"], fields["revision"]), "one")
        return "refused", "outcome_uncertain" if holder and holder[0] == "uncertain" else "already_applied"

    def move(self, request_id, before, after, reason, now, release=False):
        """Compare-and-set one claimed action from state `before` to `after`: True only for the caller that made the change. `release` frees the (record, action, revision) for a fresh authorization."""
        slot = "NULL" if release else "slot"
        return self._run(f"UPDATE v7_inbox_actions SET state=%s, reason=%s, slot={slot}, updated_at=%s WHERE request_id=%s AND state=%s", (after, reason, stamp(now), request_id, before)) == 1

    def needs_human(self, request_id, now):
        """Flag an uncertain action the source could not settle: it stays uncertain, never retried, until a person resolves it."""
        return self._run("UPDATE v7_inbox_actions SET needs_human=1, updated_at=%s WHERE request_id=%s AND state='uncertain'", (stamp(now), request_id)) == 1

    def stale(self, state, older_than):
        """Request ids still in `state` since before `older_than` (a crashed worker leaves these behind)."""
        rows = self._run("SELECT request_id FROM v7_inbox_actions WHERE state=%s AND updated_at<%s", (state, stamp(older_than)), "all")
        return [row[0] for row in rows]
