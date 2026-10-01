"""Suppression tombstones (machine state only): after a canonical job is retired, keep the minimum identity for 90 days so the same stale or
reposted vacancy is not immediately recreated. A vacancy that is provably newer than the retirement (a later posting date) may re-enter."""
from datetime import timedelta

TTL_DAYS = 90


def blocked(cursor, key, fuzzy, posted, now):
    """True when an active tombstone matches this posting and nothing proves it is a newer vacancy."""
    cursor.execute("SELECT retired_at FROM v7_tombstones WHERE expires_at > %s AND (dedupe_key=%s OR fuzzy_key=%s) LIMIT 1", (now, key, fuzzy))
    row = cursor.fetchone()
    if not row:
        return False
    retired = row[0]
    if posted and retired and posted > (retired.date() if hasattr(retired, "date") else retired):
        cursor.execute("DELETE FROM v7_tombstones WHERE dedupe_key=%s OR fuzzy_key=%s", (key, fuzzy))      # genuinely relisted: re-enter
        return False
    cursor.execute("UPDATE v7_tombstones SET hits=hits+1 WHERE dedupe_key=%s OR fuzzy_key=%s", (key, fuzzy))
    return True


def write(cursor, key, fuzzy, now):
    cursor.execute("REPLACE INTO v7_tombstones (dedupe_key, fuzzy_key, retired_at, expires_at, hits) VALUES (%s,%s,%s,%s,0)",
                   (key, fuzzy, now, now + timedelta(days=TTL_DAYS)))


def expire(cursor, now):
    cursor.execute("DELETE FROM v7_tombstones WHERE expires_at <= %s", (now,))
    return cursor.rowcount
