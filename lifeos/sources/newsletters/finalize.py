"""Stage 4 - finalize: close out a newsletter email only when every job in it has an outcome.

Finalize adds the Done label once no job from the message is in flight and every published one has been read back from
the Ledger. A mail with no saved jobs has nothing in flight and is closed too. Idempotent; counts only.
Usage: python -m lifeos.sources.newsletters.finalize [--live] [--limit N]

"""

import argparse
import sys

from lifeos.jobs import store
from lifeos.platform import stage_timing
from lifeos.platform.gmail import Gmail, GmailError, error_code, is_rate_limit
from lifeos.sources.newsletters import config

IN_FLIGHT = ("NEW", "RESOLVED", "READY")
TERMINAL = (
    "PUBLISHED",
    "CLOSED",
    "DUPLICATE",
    "PURGED",
)  # plus EXCLUDED_*; anything else (HOLD included) keeps the mail open


def survivor_verified(cursor, job_id, hops=3):
    """True when the job a DUPLICATE points at (repost_of) is published and read back from the Ledger, following a
    survivor that is itself a DUPLICATE for at most `hops` steps."""
    for _ in range(hops):
        cursor.execute("SELECT status, verified_at IS NULL, repost_of FROM v7_jobs WHERE id=%s", (job_id,))
        row = cursor.fetchone()
        if not row:
            return False
        status, unverified, twin = row
        if status == "PUBLISHED":
            return not unverified
        if status != "DUPLICATE" or not twin:
            return False
        job_id = twin
    return False


def blockers(connection, message_ids):
    """{message id: why it cannot close yet} for the given messages; a message absent from the result is complete."""
    if not message_ids:
        return {}
    marks = ",".join(["%s"] * len(message_ids))
    out = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT s.gmail_message_id, j.status, j.verified_at IS NULL, j.repost_of FROM v7_job_sources s JOIN v7_jobs j ON j.id = s.job_id"
            f" WHERE s.gmail_message_id IN ({marks})",
            tuple(message_ids),
        )
        for message_id, status, unverified, twin in cursor.fetchall():
            if status == "DUPLICATE" and twin and not survivor_verified(cursor, twin):
                out.setdefault(
                    message_id, "twin_unverified"
                )  # a mail is finalized as DUPLICATE only once the job it duplicates has a verified Ledger row
                continue
            if status in IN_FLIGHT:
                out.setdefault(message_id, "in_flight")
            elif status == "HOLD":
                out.setdefault(message_id, "hold")  # unreadable after every attempt: not an outcome, stays visible
            elif status == "PUBLISHED" and unverified:
                out.setdefault(message_id, "unverified")
            elif status not in TERMINAL and not status.startswith("EXCLUDED_"):
                out.setdefault(message_id, "other")  # an allowlist, never "whatever is not in flight"
    return out


def finalize(gmail, live, limit, connection_factory=store.connect):
    try:
        done_label = gmail.label_id(config.DONE_LABEL, create=live)
    except GmailError:
        if live:
            raise
        done_label = None  # a dry run before the label exists: report what would close, create nothing
    ids = gmail.list_ids(config.FINALIZE_QUERY, limit=limit)
    counts = {
        "candidates": len(ids),
        "closed": 0,
        "in_flight": 0,
        "unverified": 0,
        "twin_unverified": 0,
        "hold": 0,
        "other": 0,
    }
    if not ids:
        return counts
    with connection_factory() as connection:
        held = blockers(connection, ids)
    ready = [i for i in ids if i not in held]
    for why in held.values():
        counts[why] += 1
    if live and ready:
        gmail.relabel(ready, add=[done_label])
    counts["closed"] = len(ready) if live else 0
    counts["would_close"] = len(ready)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        counts = finalize(Gmail.from_env(), args.live, args.limit)
    except (GmailError, store.StoreError) as error:
        if isinstance(error, GmailError) and is_rate_limit(error):
            # Gmail's per-user limit, still on after every retry, while the early domains run uses the same mailbox.
            # Closing a mail is idempotent, so nothing is lost: the stage reports degraded and the next tick closes it.
            print(
                f"FINALIZE DEGRADED: {error_code(error)}; nothing was closed this run, the next tick retries",
                file=sys.stderr,
            )
            print("::warning title=finalize::Gmail rate limit; mail closing deferred to the next tick", flush=True)
            stage_timing.note_counts({"degraded": True})
            return 0
        print(f"FINALIZE FAILED: {error}", file=sys.stderr)
        return 1
    print(f"finalize {'LIVE' if args.live else 'DRY-RUN'}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
