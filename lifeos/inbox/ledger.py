"""Durable, atomic Action Inbox state in the V7 MySQL store: events, confirmations, actions and access receipts.

`Ledger(connect)`: `connect` is a context manager yielding an autocommit connection. Atomicity comes from unique
keys with `INSERT IGNORE` (a claim) and compare-and-set `UPDATE ... WHERE state = ...` (a transition); no lock is
held. `ensure_schema` creates the tables and is not run automatically. Stores ids, states, fixed reason words,
hashes and UTC times, never a title, credential or exception text. Action states: claimed, in_progress, uncertain,
completed, not_applied, expired.
"""

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
    """CREATE TABLE IF NOT EXISTS v7_inbox_access (
        request_id CHAR(64) NOT NULL, principal_id VARCHAR(80) NOT NULL, source VARCHAR(20) NOT NULL, workspace VARCHAR(20) NOT NULL, account VARCHAR(80) NOT NULL,
        decision VARCHAR(8) NOT NULL, reason VARCHAR(40) NOT NULL, outcome VARCHAR(24) NOT NULL, at VARCHAR(24) NOT NULL, PRIMARY KEY (request_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
ACCESS_COLUMNS = ("request_id", "principal_id", "source", "workspace", "account", "decision", "reason", "outcome", "at")
INTENT_COLUMNS = (
    "intent_id",
    "event_id",
    "principal_id",
    "source",
    "workspace",
    "account",
    "record_id",
    "row_id",
    "action",
    "revision",
    "event_at",
    "nonce",
    "code_mac",
    "expires_at",
    "attempts",
    "state",
)
ACTION_COLUMNS = (
    "request_id",
    "principal_id",
    "source",
    "workspace",
    "account",
    "record_id",
    "action",
    "revision",
    "status_before",
    "nonce",
    "slot",
    "state",
    "reason",
    "needs_human",
    "claimed_at",
    "updated_at",
)
# the slot value while an action holds its (record, action, revision); NULL once nothing was applied
LIVE = "live"


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(text):
    return hashlib.sha256(str(text).encode()).hexdigest()


class LedgerError(RuntimeError):
    """`str(error)` is the fixed code INBOX_LEDGER_UNAVAILABLE."""


class Ledger:
    def __init__(self, connect):
        self._connect = connect

    def _run(self, sql, args=(), fetch=None):
        with (
            fixed(Exception, LedgerError, "INBOX_LEDGER_UNAVAILABLE"),
            self._connect() as connection,
            connection.cursor() as cursor,
        ):
            count = cursor.execute(sql, tuple(args))
            if fetch == "one":
                return cursor.fetchone()
            if fetch == "all":
                return cursor.fetchall()
            return count

    def ensure_schema(self):
        for statement in SCHEMA:
            self._run(statement)

    def claim_event(self, event_id, kind, now):
        return (
            self._run(
                "INSERT IGNORE INTO v7_inbox_events (event_id, kind, seen_at) VALUES (%s,%s,%s)",
                (event_id, kind, stamp(now)),
            )
            == 1
        )

    def open_intent(self, fields):
        self._run(
            "UPDATE v7_inbox_intents SET state='superseded' WHERE row_id=%s AND state='pending'", (fields["row_id"],)
        )
        columns = [c for c in INTENT_COLUMNS if c not in ("attempts", "state")]
        return (
            self._run(
                f"INSERT IGNORE INTO v7_inbox_intents ({', '.join(columns)}, attempts, state) VALUES ({', '.join(['%s'] * len(columns))}, 0, 'pending')",
                [fields[c] for c in columns],
            )
            == 1
        )

    def pending_intent(self, row_id):
        row = self._run(
            f"SELECT {', '.join(INTENT_COLUMNS)} FROM v7_inbox_intents WHERE row_id=%s AND state='pending' ORDER BY event_at DESC LIMIT 1",
            (row_id,),
            "one",
        )
        return dict(zip(INTENT_COLUMNS, row, strict=True)) if row else None

    def intent(self, intent_id):
        row = self._run(
            f"SELECT {', '.join(INTENT_COLUMNS)} FROM v7_inbox_intents WHERE intent_id=%s", (intent_id,), "one"
        )
        return dict(zip(INTENT_COLUMNS, row, strict=True)) if row else None

    def spend_attempt(self, intent_id, limit):
        if (
            self._run(
                "UPDATE v7_inbox_intents SET attempts=attempts+1 WHERE intent_id=%s AND state='pending' AND attempts<%s",
                (intent_id, limit),
            )
            == 1
        ):
            return True
        self.close_intent(intent_id, "locked")
        return False

    def close_intent(self, intent_id, state):
        return (
            self._run("UPDATE v7_inbox_intents SET state=%s WHERE intent_id=%s AND state='pending'", (state, intent_id))
            == 1
        )

    def action(self, request_id):
        row = self._run(
            f"SELECT {', '.join(ACTION_COLUMNS)} FROM v7_inbox_actions WHERE request_id=%s", (request_id,), "one"
        )
        return dict(zip(ACTION_COLUMNS, row, strict=True)) if row else None

    def claim_action(self, fields, now):
        """Returns ("claimed", None) | ("replay", row) | ("refused", reason). Atomic: a reused request id, spent nonce
        or held revision fails the insert.
        """
        columns = (
            "request_id",
            "principal_id",
            "source",
            "workspace",
            "account",
            "record_id",
            "action",
            "revision",
            "status_before",
            "nonce",
        )
        stamped = stamp(now)
        if (
            self._run(
                f"INSERT IGNORE INTO v7_inbox_actions ({', '.join(columns)}, slot, state, reason, needs_human, claimed_at, updated_at) VALUES ({', '.join(['%s'] * len(columns))}, "
                "'live', 'claimed', 'claimed', 0, %s, %s)",
                [fields[c] for c in columns] + [stamped, stamped],
            )
            == 1
        ):
            return "claimed", None
        prior = self.action(fields["request_id"])
        if prior:
            same = (prior["principal_id"], prior["record_id"], prior["action"]) == (
                fields["principal_id"],
                fields["record_id"],
                fields["action"],
            )
            return ("replay", prior) if same else ("refused", "idempotency_key_reused")
        if self._run("SELECT 1 FROM v7_inbox_actions WHERE nonce=%s", (fields["nonce"],), "one"):
            return "refused", "authorization_used"
        holder = self._run(
            "SELECT state FROM v7_inbox_actions WHERE record_id=%s AND action=%s AND revision=%s AND slot='live'",
            (fields["record_id"], fields["action"], fields["revision"]),
            "one",
        )
        return "refused", "outcome_uncertain" if holder and holder[0] == "uncertain" else "already_applied"

    def move(self, request_id, before, after, reason, now, release=False):
        slot = "NULL" if release else "slot"
        return (
            self._run(
                f"UPDATE v7_inbox_actions SET state=%s, reason=%s, slot={slot}, updated_at=%s WHERE request_id=%s AND state=%s",
                (after, reason, stamp(now), request_id, before),
            )
            == 1
        )

    def needs_human(self, request_id, now):
        return (
            self._run(
                "UPDATE v7_inbox_actions SET needs_human=1, updated_at=%s WHERE request_id=%s AND state='uncertain'",
                (stamp(now), request_id),
            )
            == 1
        )

    def stale(self, state, older_than):
        rows = self._run(
            "SELECT request_id FROM v7_inbox_actions WHERE state=%s AND updated_at<%s",
            (state, stamp(older_than)),
            "all",
        )
        return [row[0] for row in rows]

    def record_access(self, fields, now):
        columns = [c for c in ACCESS_COLUMNS if c not in ("outcome", "at")]
        return (
            self._run(
                f"INSERT IGNORE INTO v7_inbox_access ({', '.join(columns)}, outcome, at) VALUES ({', '.join(['%s'] * len(columns))}, '', %s)",
                [fields[c] for c in columns] + [stamp(now)],
            )
            == 1
        )

    def settle_access(self, request_id, outcome):
        return (
            self._run("UPDATE v7_inbox_access SET outcome=%s WHERE request_id=%s AND outcome=''", (outcome, request_id))
            == 1
        )

    def access_receipts(self, principal_id):
        rows = self._run(
            f"SELECT {', '.join(ACCESS_COLUMNS)} FROM v7_inbox_access WHERE principal_id=%s ORDER BY at, request_id",
            (principal_id,),
            "all",
        )
        return [dict(zip(ACCESS_COLUMNS, row, strict=True)) for row in rows]
