"""LIFE-747: the minimal state store for Followup threads (D258), in the existing V7 MySQL store; synthetic only, nothing here creates a table or runs on a schedule."""
# `FollowupStore(connect)`: `connect` is a context manager yielding a connection (`lifeos.platform.db.connect` in production, a sqlite shim in tests).
# Five tables, all created only by `ensure_schema` (NOT run by anything: a production schema change needs Jim's go at release, D258):
#   v7_followup_threads      one row per open-loop thread: identity hash, class, owner, derived state, checkpoint + basis, confidence, source links, evidence fingerprint, resolution
#   v7_followup_checkpoints  one cursor per (workspace, account, source): advanced only after an accepted write
#   v7_followup_overrides    a Jim checkpoint override per thread; it never edits a source due date
#   v7_followup_suppressions fingerprints of dismissed or closed evidence, so the same evidence never reopens a thread
#   v7_followup_payload      ONE component-state payload per (workspace, account) scope, replaced each live run and read back (Jim, complete build, Nov 16 2026); the Action Inbox reads it
# One write path, `accept(batch)`: validate, write every thread and the cursor in ONE transaction, read every thread back and compare, commit; any failure rolls everything back and keeps the last known-good state.
# Stored: hashes, fixed words, UTC times, bounded labels and source links. Never a mail body, a copied source payload, a credential or an exception text. TGP and unknown workspaces are refused first.
import json

from lifeos.inbox.ledger import digest, stamp
from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES
from lifeos.platform.fixed_code import fixed

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_followup_threads (
        thread_id CHAR(64) NOT NULL, workspace VARCHAR(20) NOT NULL, account VARCHAR(80) NOT NULL, outcome VARCHAR(120) NOT NULL, klass VARCHAR(16) NOT NULL,
        owner VARCHAR(10) NOT NULL, state VARCHAR(14) NOT NULL, checkpoint_at VARCHAR(24) NOT NULL, checkpoint_basis VARCHAR(10) NOT NULL, confidence VARCHAR(6) NOT NULL,
        source_refs VARCHAR(1200) NOT NULL, evidence_fp CHAR(64) NOT NULL, resolution VARCHAR(10) NOT NULL, version INT NOT NULL, first_seen VARCHAR(24) NOT NULL,
        last_accepted VARCHAR(24) NOT NULL,
        PRIMARY KEY (thread_id), KEY ix_followup_thread_scope (workspace, account, resolution)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_followup_checkpoints (
        workspace VARCHAR(20) NOT NULL, account VARCHAR(80) NOT NULL, source VARCHAR(16) NOT NULL, cursor_value VARCHAR(200) NOT NULL, accepted_at VARCHAR(24) NOT NULL,
        PRIMARY KEY (workspace, account, source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_followup_overrides (
        thread_id CHAR(64) NOT NULL, checkpoint_at VARCHAR(24) NOT NULL, set_by VARCHAR(80) NOT NULL, set_at VARCHAR(24) NOT NULL, PRIMARY KEY (thread_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_followup_suppressions (
        thread_id CHAR(64) NOT NULL, fingerprint CHAR(64) NOT NULL, reason VARCHAR(10) NOT NULL, at VARCHAR(24) NOT NULL, PRIMARY KEY (thread_id, fingerprint)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_followup_payload (
        scope_key VARCHAR(190) NOT NULL, payload MEDIUMTEXT NOT NULL, generated_at VARCHAR(32) NOT NULL, state VARCHAR(16) NOT NULL,
        PRIMARY KEY (scope_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
TABLES = ("v7_followup_threads", "v7_followup_checkpoints", "v7_followup_overrides", "v7_followup_suppressions", "v7_followup_payload")
SOURCES = ("gmail", "outlook", "calendar", "jira", "todoist", "notion", "interview_os")          # D258 first slice; Asana is excluded
CLASSES = ("waiting", "owed", "value_add", "outcome_thread")
OWNERS = ("jim", "external", "ambiguous")
STATES = ("needs_action", "waiting", "upcoming", "review")
BASES = ("source", "suggested", "")
CONFIDENCE = ("low", "medium", "high")
RESOLUTIONS = ("open", "closed", "dismissed")
PAYLOAD_STATES = ("PASS", "DEGRADED")
PAYLOAD_MAX = 1_000_000                                     # characters of JSON per payload; the column is MEDIUMTEXT (16 MB)
THREAD_COLUMNS = ("thread_id", "workspace", "account", "outcome", "klass", "owner", "state", "checkpoint_at", "checkpoint_basis", "confidence", "source_refs", "evidence_fp", "resolution", "version",
                  "first_seen", "last_accepted")
CONTENT = ("outcome", "klass", "owner", "state", "checkpoint_at", "checkpoint_basis", "confidence", "source_refs", "evidence_fp")      # what a replay compares; time and version follow a change


class FollowupRejected(ValueError):
    """The input was refused before anything was written; `str(error)` is a fixed reason word."""


class FollowupStoreError(RuntimeError):
    """`str(error)` is a fixed code (FOLLOWUP_STORE_UNAVAILABLE or FOLLOWUP_READBACK_MISMATCH); a driver message never leaves."""


def thread_id(workspace, account, identity_key):
    """The stable identity: the same outcome in the same workspace and account is one thread, and a Personal id can never equal a Bytalos id."""
    return digest(f"{workspace}|{account}|{identity_key}")


def _need(condition, reason):
    if not condition:
        raise FollowupRejected(reason)


def _scope(workspace, account):
    _need(workspace not in UNRELEASED_WORKSPACES, "workspace_not_released")
    _need(workspace in WORKSPACES, "workspace_unknown")
    _need(isinstance(account, str) and 0 < len(account) <= 80, "account_invalid")


def normalize(workspace, account, item):
    """-> the row a thread becomes, or FollowupRejected(reason). Free text is bounded and source links must be https or concord links; nothing else is kept."""
    _need(isinstance(item, dict), "thread_invalid")
    key = item.get("identity_key")
    _need(isinstance(key, str) and 0 < len(key) <= 200, "identity_invalid")
    _need(item.get("klass") in CLASSES, "class_unknown")
    _need(item.get("owner") in OWNERS, "owner_unknown")
    _need(item.get("state") in STATES, "state_unknown")
    basis, checkpoint = item.get("checkpoint_basis", ""), item.get("checkpoint_at", "")
    _need(basis in BASES, "basis_unknown")
    _need((checkpoint == "") == (basis == ""), "checkpoint_basis_mismatch")                # a checkpoint is always source-backed or visibly Suggested
    _need(checkpoint == "" or (len(checkpoint) == 20 and checkpoint.endswith("Z")), "checkpoint_not_utc")
    _need(item.get("confidence") in CONFIDENCE, "confidence_unknown")
    outcome = item.get("outcome", "")
    _need(isinstance(outcome, str) and 0 < len(outcome) <= 120, "outcome_invalid")
    refs = item.get("source_refs") or []
    _need(isinstance(refs, (list, tuple)) and 0 < len(refs) <= 5, "source_refs_invalid")
    _need(all(isinstance(r, str) and len(r) <= 200 and r.startswith(("https://", "concord://")) for r in refs), "source_ref_invalid")
    fingerprint = item.get("evidence_fp")
    _need(isinstance(fingerprint, str) and len(fingerprint) == 64, "evidence_fp_invalid")
    return {"thread_id": thread_id(workspace, account, key), "workspace": workspace, "account": account, "outcome": outcome, "klass": item["klass"], "owner": item["owner"], "state": item["state"],
            "checkpoint_at": checkpoint, "checkpoint_basis": basis, "confidence": item["confidence"], "source_refs": json.dumps(sorted(set(refs)), separators=(",", ":")), "evidence_fp": fingerprint}


def scope_key(workspace, account):
    """The payload row key: one row per (workspace, account); a workspace name never contains the separator, so two scopes never share a key."""
    return f"{workspace}|{account}"


class FollowupStore:
    def __init__(self, connect):
        self._connect = connect

    def ensure_schema(self):
        """NOT run by anything yet: creating these tables in production needs Jim's go (D258)."""
        with fixed(Exception, FollowupStoreError, "FOLLOWUP_STORE_UNAVAILABLE"), self._connect() as connection, connection.cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)

    def _read(self, sql, args=(), fetch="all"):
        with fixed(Exception, FollowupStoreError, "FOLLOWUP_STORE_UNAVAILABLE"), self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, tuple(args))
            return cursor.fetchone() if fetch == "one" else cursor.fetchall()

    # ---- the one write path -------------------------------------------------------------------------------------------------------------------------------------------------------------

    def accept(self, workspace, account, source, items, cursor_value, now, complete=True):
        """Validate, write every thread and (when `complete`) the source cursor in one transaction, read every thread back and compare, then commit.
        -> {"written", "unchanged", "suppressed", "cursor_advanced"}. `complete=False` (a failed, denied or truncated source read) stores what was accepted but never advances the cursor."""
        _scope(workspace, account)
        _need(source in SOURCES, "source_not_approved")
        _need(isinstance(cursor_value, str) and len(cursor_value) <= 200, "cursor_invalid")
        rows = [normalize(workspace, account, item) for item in items]                       # every item is validated before the first write
        _need(len({r["thread_id"] for r in rows}) == len(rows), "duplicate_thread")
        stamped = stamp(now)
        out = {"written": 0, "unchanged": 0, "suppressed": 0, "cursor_advanced": False}
        expected = []
        with fixed(Exception, FollowupStoreError, "FOLLOWUP_STORE_UNAVAILABLE", keep=FollowupStoreError), self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    for row in rows:
                        wanted = self._upsert(cursor, row, stamped, out)
                        if wanted:
                            expected.append(wanted)
                    for wanted in expected:                                                    # read back and compare before anything is committed
                        cursor.execute(f"SELECT {', '.join(THREAD_COLUMNS)} FROM v7_followup_threads WHERE thread_id=%s AND workspace=%s AND account=%s",
                                       (wanted["thread_id"], workspace, account))
                        found = cursor.fetchone()
                        if not found or any(dict(zip(THREAD_COLUMNS, found, strict=True))[k] != wanted[k] for k in CONTENT + ("thread_id", "resolution")):
                            raise FollowupStoreError("FOLLOWUP_READBACK_MISMATCH")
                    if complete:
                        self._advance(cursor, workspace, account, source, cursor_value, stamped)
                        out["cursor_advanced"] = True
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return out

    def _upsert(self, cursor, row, stamped, out):
        """-> the row the thread must read back as, or None when evidence is suppressed. Replay of the same content changes nothing."""
        cursor.execute(f"SELECT {', '.join(THREAD_COLUMNS)} FROM v7_followup_threads WHERE thread_id=%s", (row["thread_id"],))
        found = cursor.fetchone()
        if not found:
            full = {**row, "resolution": "open", "version": 1, "first_seen": stamped, "last_accepted": stamped}
            cursor.execute(f"INSERT INTO v7_followup_threads ({', '.join(THREAD_COLUMNS)}) VALUES ({', '.join(['%s'] * len(THREAD_COLUMNS))})", [full[c] for c in THREAD_COLUMNS])
            out["written"] += 1
            return full
        current = dict(zip(THREAD_COLUMNS, found, strict=True))
        reopen = False
        if current["resolution"] != "open":
            cursor.execute("SELECT 1 FROM v7_followup_suppressions WHERE thread_id=%s AND fingerprint=%s", (row["thread_id"], row["evidence_fp"]))
            if cursor.fetchone():                                                              # the same evidence that closed or dismissed it never brings it back
                out["suppressed"] += 1
                return None
            reopen = True                                                                      # new authoritative evidence reopens it
        if not reopen and all(current[k] == row[k] for k in CONTENT):
            out["unchanged"] += 1
            return {**current}
        changes = {**{k: row[k] for k in CONTENT}, "resolution": "open", "last_accepted": stamped, "version": current["version"] + 1}
        cursor.execute("UPDATE v7_followup_threads SET outcome=%s, klass=%s, owner=%s, state=%s, checkpoint_at=%s, checkpoint_basis=%s, confidence=%s, source_refs=%s, evidence_fp=%s, resolution=%s, "
                       "last_accepted=%s, version=%s WHERE thread_id=%s AND version=%s",
                       [changes[k] for k in ("outcome", "klass", "owner", "state", "checkpoint_at", "checkpoint_basis", "confidence", "source_refs", "evidence_fp", "resolution", "last_accepted",
                                             "version")] + [row["thread_id"], current["version"]])
        if cursor.rowcount != 1:
            raise FollowupStoreError("FOLLOWUP_STORE_UNAVAILABLE")                             # a concurrent writer won: this batch rolls back whole
        out["written"] += 1
        return {**current, **changes}

    @staticmethod
    def _advance(cursor, workspace, account, source, cursor_value, stamped):
        cursor.execute("SELECT 1 FROM v7_followup_checkpoints WHERE workspace=%s AND account=%s AND source=%s", (workspace, account, source))
        if cursor.fetchone():
            cursor.execute("UPDATE v7_followup_checkpoints SET cursor_value=%s, accepted_at=%s WHERE workspace=%s AND account=%s AND source=%s", (cursor_value, stamped, workspace, account, source))
        else:
            cursor.execute("INSERT INTO v7_followup_checkpoints (workspace, account, source, cursor_value, accepted_at) VALUES (%s,%s,%s,%s,%s)", (workspace, account, source, cursor_value, stamped))

    # ---- resolution, override and reads -----------------------------------------------------------------------------------------------------------------------------------------------------

    def resolve(self, workspace, account, identity_key, resolution, evidence_fp, now):
        """Record that a thread is closed or dismissed on strong proof or Jim's explicit word, and remember the evidence fingerprint so it cannot reopen the thread. True when the row changed."""
        _scope(workspace, account)
        _need(resolution in ("closed", "dismissed"), "resolution_unknown")
        _need(isinstance(evidence_fp, str) and len(evidence_fp) == 64, "evidence_fp_invalid")
        tid = thread_id(workspace, account, identity_key)
        with fixed(Exception, FollowupStoreError, "FOLLOWUP_STORE_UNAVAILABLE"), self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT IGNORE INTO v7_followup_suppressions (thread_id, fingerprint, reason, at) VALUES (%s,%s,%s,%s)", (tid, evidence_fp, resolution, stamp(now)))
                    cursor.execute("UPDATE v7_followup_threads SET resolution=%s, version=version+1, last_accepted=%s WHERE thread_id=%s AND workspace=%s AND account=%s AND resolution='open'",
                                   (resolution, stamp(now), tid, workspace, account))
                    changed = cursor.rowcount == 1
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return changed

    def set_override(self, workspace, account, identity_key, checkpoint_at, set_by, now):
        """A Jim checkpoint override for one thread. It lives only here: no source due date is ever written."""
        _scope(workspace, account)
        _need(isinstance(checkpoint_at, str) and len(checkpoint_at) == 20 and checkpoint_at.endswith("Z"), "checkpoint_not_utc")
        tid = thread_id(workspace, account, identity_key)
        with fixed(Exception, FollowupStoreError, "FOLLOWUP_STORE_UNAVAILABLE"), self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM v7_followup_threads WHERE thread_id=%s AND workspace=%s AND account=%s", (tid, workspace, account))
            if not cursor.fetchone():
                return False
            cursor.execute("SELECT 1 FROM v7_followup_overrides WHERE thread_id=%s", (tid,))
            if cursor.fetchone():
                cursor.execute("UPDATE v7_followup_overrides SET checkpoint_at=%s, set_by=%s, set_at=%s WHERE thread_id=%s", (checkpoint_at, set_by, stamp(now), tid))
            else:
                cursor.execute("INSERT INTO v7_followup_overrides (thread_id, checkpoint_at, set_by, set_at) VALUES (%s,%s,%s,%s)", (tid, checkpoint_at, set_by, stamp(now)))
        return True

    def threads(self, workspace, account):
        """The open and resolved threads of ONE workspace and account, each with its effective checkpoint (a Jim override wins). Another workspace is never visible here."""
        _scope(workspace, account)
        rows = self._read(f"SELECT {', '.join('t.' + c for c in THREAD_COLUMNS)}, o.checkpoint_at FROM v7_followup_threads t LEFT JOIN v7_followup_overrides o ON o.thread_id = t.thread_id "
                          "WHERE t.workspace=%s AND t.account=%s ORDER BY t.thread_id", (workspace, account))
        out = []
        for row in rows:
            thread = dict(zip(THREAD_COLUMNS, row[:-1], strict=True))
            override = row[-1]
            thread["effective_checkpoint"] = override or thread["checkpoint_at"]
            thread["checkpoint_basis_effective"] = "override" if override else thread["checkpoint_basis"]
            thread["source_refs"] = json.loads(thread["source_refs"])
            out.append(thread)
        return out

    def cursor_for(self, workspace, account, source):
        _scope(workspace, account)
        row = self._read("SELECT cursor_value, accepted_at FROM v7_followup_checkpoints WHERE workspace=%s AND account=%s AND source=%s", (workspace, account, source), "one")
        return {"cursor": row[0], "accepted_at": row[1]} if row else None

    # ---- the component-state payload (one row per scope) --------------------------------------------------------------------------------------------------------------------------------

    def save_payload(self, workspace, account, payload):
        """Validate, write ONE row for this scope in a single statement (insert or replace its values) and read it back. -> True when the stored payload equals what was given.
        Refused first, nothing written: TGP or an unknown workspace, a payload that is not a dict, names another scope, has a state other than PASS or DEGRADED, has no generated_at of at most 32
        characters, is not JSON, or is over one million characters. A failed write is FOLLOWUP_STORE_UNAVAILABLE; a row that does not read back equal is FOLLOWUP_READBACK_MISMATCH."""
        _scope(workspace, account)
        _need(isinstance(payload, dict), "payload_invalid")
        _need(payload.get("workspace") == workspace and payload.get("account") == account, "payload_scope_mismatch")
        _need(payload.get("state") in PAYLOAD_STATES, "payload_state_invalid")
        generated = payload.get("generated_at")
        _need(isinstance(generated, str) and 0 < len(generated) <= 32, "payload_time_invalid")
        try:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raise FollowupRejected("payload_invalid") from None
        _need(len(text) <= PAYLOAD_MAX, "payload_too_large")
        with fixed(Exception, FollowupStoreError, "FOLLOWUP_STORE_UNAVAILABLE"), self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO v7_followup_payload (scope_key, payload, generated_at, state) VALUES (%s,%s,%s,%s) "
                           "ON DUPLICATE KEY UPDATE payload=VALUES(payload), generated_at=VALUES(generated_at), state=VALUES(state)",
                           (scope_key(workspace, account), text, generated, payload["state"]))
        if self.load_payload(workspace, account) != json.loads(text):
            raise FollowupStoreError("FOLLOWUP_READBACK_MISMATCH")
        return True

    def load_payload(self, workspace, account):
        """The stored payload of ONE scope, or None when none was saved. A row whose columns disagree with its own JSON is FOLLOWUP_READBACK_MISMATCH, never a guess."""
        _scope(workspace, account)
        row = self._read("SELECT payload, generated_at, state FROM v7_followup_payload WHERE scope_key=%s", (scope_key(workspace, account),), "one")
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except ValueError:
            raise FollowupStoreError("FOLLOWUP_READBACK_MISMATCH") from None
        if not isinstance(payload, dict) or payload.get("generated_at") != row[1] or payload.get("state") != row[2]:
            raise FollowupStoreError("FOLLOWUP_READBACK_MISMATCH")
        return payload


__all__ = ["FollowupStore", "FollowupRejected", "FollowupStoreError", "SCHEMA", "TABLES", "SOURCES", "thread_id", "scope_key", "normalize"]
