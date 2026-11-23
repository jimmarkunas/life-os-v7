"""LIFE-903: the one cross-process claim every Job Ledger creator takes before it creates a row (publish, the Interview lead row, the Interview opportunity row; manual jobs reach the Ledger through publish).
Lives in the platform layer so the Jobs and Interview layers can both use it; `connect` is the store connection (default lifeos.platform.db.connect; Jobs passes its own facade so a test can pin it).

Notion has no unique constraint, so lookup-then-create can race. The existing store does: `v7_jobs` has a UNIQUE `dedupe_key`, and one conditional UPDATE on it is a lock-protected test-and-set, so of any
number of processes asking at once exactly one sees one row changed. Two nullable columns (`ledger_claim_by`, `ledger_claim_at`, added through store.COLUMNS) hold the claim.

Contract for a creator: claim(key) -> WON: read the Ledger by Stable Job Key (authoritative, now that nobody else may create), adopt the page when one is there, else create, read back, then release (or record
the page id, as publish does). HELD: another creator holds the key (or an earlier create is unresolved): create nothing, count a duplicate skip, find it by key next time. CREATED: the page id is already
recorded: adopt it. A create that timed out or whose answer was lost is never released: the claim stays until LEASE has passed, and only then can another creator win it, and it too reads the Ledger
by key first, so a page Notion did accept is adopted, never repeated. Nothing is deleted or rewritten. A key with no job row gets a `LEDGER` placeholder row (status LEDGER: no stage picks it). Counts and fixed codes only."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import uuid

from lifeos.platform import db
from lifeos.platform.names import norm

LEASE = timedelta(minutes=15)                     # Notion's index can lag a create by a minute or two; well past that, an unreleased claim is an abandoned one
WON, HELD, CREATED = "won", "held", "created"


def who(role):
    """-> an identity for this claimant: a fixed role word, the workflow run (or process) and a random suffix, so two processes of one run are two claimants."""
    return f"{role}:{os.environ.get('GITHUB_RUN_ID') or os.getpid()}:{uuid.uuid4().hex[:8]}"[:64]


def role_key(company, role):
    """-> the claim key for a row that has no Stable Job Key (an Interview opportunity found by company and role): a hash, never the names themselves."""
    return sha256(f"ledger-role|{norm(company)}|{norm(role)}".encode()).hexdigest()


def claim(key, claimant, connect=None, now=None):
    """-> (WON, None) | (HELD, None) | (CREATED, page id). One short connection, committed before any Notion call so other processes see it."""
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    with (connect or db.connect)() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT IGNORE INTO v7_jobs (dedupe_key, status, first_seen, last_seen, updated_at) VALUES (%s,'LEDGER',%s,%s,%s)", (key, moment, moment, moment))
        cursor.execute("UPDATE v7_jobs SET ledger_claim_by=%s, ledger_claim_at=%s WHERE dedupe_key=%s AND notion_page_id IS NULL AND (ledger_claim_at IS NULL OR ledger_claim_at < %s)",
                       (claimant, moment, key, moment - LEASE))
        if cursor.rowcount == 1:
            return WON, None
        cursor.execute("SELECT notion_page_id FROM v7_jobs WHERE dedupe_key=%s", (key,))
        row = cursor.fetchone()
    return (CREATED, row[0]) if row and row[0] else (HELD, None)


def release(key, claimant, connect=None):
    """The create is verified by read-back (or there was nothing to create): give the claim up. Only the holder's own claim is released."""
    with (connect or db.connect)() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE v7_jobs SET ledger_claim_by=NULL, ledger_claim_at=NULL WHERE dedupe_key=%s AND ledger_claim_by=%s", (key, claimant))
