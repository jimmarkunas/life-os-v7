"""The one cross-process claim every Job Ledger creator takes before it creates a row (docs/platform/ledger-claim.md).

Notion has no unique constraint, so one conditional UPDATE on the UNIQUE `v7_jobs.dedupe_key` is the lock-protected
test-and-set. claim() -> WON, HELD (adopt the existing page, create nothing) or CREATED (page id recorded: adopt it).
A create that timed out is never released: the claim stays for LEASE and the next winner reads by key first."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from contextlib import contextmanager
import os
import uuid

from lifeos.platform import db
from lifeos.platform.names import norm

LEASE = timedelta(minutes=15)
WON, HELD, CREATED = "won", "held", "created"
_memory = None


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
    """-> an identity for this claimant: role word, workflow run (or process) and a random suffix."""
    return f"{role}:{os.environ.get('GITHUB_RUN_ID') or os.getpid()}:{uuid.uuid4().hex[:8]}"[:64]


def role_key(company, role):
    """-> the claim key for a row with no Stable Job Key (an Interview opportunity found by company and role): a hash,
    never the names."""
    return sha256(f"ledger-role|{norm(company)}|{norm(role)}".encode()).hexdigest()


def claim(key, claimant, connect=None, now=None):
    """-> (WON, None) | (HELD, None) | (CREATED, page id)."""
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
    """The create is verified by read-back (or there was nothing to create): give the claim up."""
    if _memory is not None:
        if (_memory.get(key) or (None,))[0] == claimant:
            del _memory[key]
        return
    with (connect or db.connect)() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE v7_jobs SET ledger_claim_by=NULL, ledger_claim_at=NULL WHERE dedupe_key=%s AND ledger_claim_by=%s", (key, claimant))
