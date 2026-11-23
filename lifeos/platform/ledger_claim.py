"""LIFE-903: the one cross-process claim every Job Ledger creator takes before it creates a row.

Notion has no unique constraint, so lookup-then-create can race; the store does. One conditional UPDATE on the UNIQUE `v7_jobs.dedupe_key` is a lock-protected test-and-set, held in two nullable columns.
claim() -> WON (read the Ledger by key, adopt or create, read back, release), HELD (another creator has the key: adopt a page that exists, create nothing) or CREATED (page id recorded: adopt it).
A create that timed out is never released: the claim stays for LEASE and the next winner reads by key first, so a page Notion accepted is adopted, never repeated. Nothing is deleted or rewritten.
Design and rollback: docs/platform/ledger-claim.md. memory() is an in-process table for a synthetic proof with no store."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from contextlib import contextmanager
import os
import uuid

from lifeos.platform import db
from lifeos.platform.names import norm

LEASE = timedelta(minutes=15)                     # Notion's index can lag a create by a minute or two; well past that, an unreleased claim is an abandoned one
WON, HELD, CREATED = "won", "held", "created"
_memory = None                                    # a dict while memory() is active (a synthetic proof), else the store is used


@contextmanager
def memory():
    """A claim table in this process only, for a synthetic proof against a sandbox Ledger (never production)."""
    global _memory
    _memory = {}
    try:
        yield
    finally:
        _memory = None


def who(role):
    """-> an identity for this claimant: a fixed role word, the workflow run (or process) and a random suffix, so two processes of one run are two claimants."""
    return f"{role}:{os.environ.get('GITHUB_RUN_ID') or os.getpid()}:{uuid.uuid4().hex[:8]}"[:64]


def role_key(company, role):
    """-> the claim key for a row that has no Stable Job Key (an Interview opportunity found by company and role): a hash, never the names themselves."""
    return sha256(f"ledger-role|{norm(company)}|{norm(role)}".encode()).hexdigest()


def claim(key, claimant, connect=None, now=None):
    """-> (WON, None) | (HELD, None) | (CREATED, page id). One short connection, committed before any Notion call so other processes see it."""
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if _memory is not None:
        held = _memory.get(key)
        if held and held[0] and held[1] > moment - LEASE:
            return HELD, None
        _memory[key] = (claimant, moment)
        return WON, None
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
    if _memory is not None:
        if (_memory.get(key) or (None,))[0] == claimant:
            del _memory[key]
        return
    with (connect or db.connect)() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE v7_jobs SET ledger_claim_by=NULL, ledger_claim_at=NULL WHERE dedupe_key=%s AND ledger_claim_by=%s", (key, claimant))
