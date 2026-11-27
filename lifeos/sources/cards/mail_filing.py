"""File the Physical Mail vendor's notices: mark read and move them from the Inbox to the "Physical Mail" Gmail label.

Only mail whose From address is exactly the vendor's no-reply address is touched; nothing is deleted; every move is
read back. Dry run unless live. Runs after the read-only lifeos.physmail package, which reads by sender and is
unaffected by labels. Counts and fixed codes only.
"""

import os
from email.utils import getaddresses

from lifeos.platform import isolate
from lifeos.platform.gmail import Gmail, GmailError

_AT = chr(64)  # built apart so no address literal enters the repository text
SENDER = "no-reply" + _AT + "anytimemailbox" + ".com"
LABEL = "Physical Mail"
DEFAULT_LIMIT = 200


def _exact(header):
    parsed = getaddresses([header]) if isinstance(header, str) else []
    return len(parsed) == 1 and parsed[0][1].strip().lower() == SENDER


class _Readback(Exception):
    """The filed message did not read back as filed."""


def run(limit, live, environ=os.environ, gmail=None):
    counts = {"listed": 0, "filed": 0, "skipped_sender": 0, "failed": 0, "why": {}}

    def fail(code):
        counts["failed"] += 1
        counts["why"][code] = counts["why"].get(code, 0) + 1

    try:
        gmail = gmail or Gmail.from_env()
        ids = gmail.list_ids(f"from:{SENDER} (in:inbox OR is:unread)", limit if limit and limit > 0 else DEFAULT_LIMIT)
        counts["listed"] = len(ids)
        if not ids or not live:
            return counts
        label_id = gmail.label_id(LABEL, create=True)

        def file_one(message_id):
            if not _exact(gmail.sender(message_id)):
                counts["skipped_sender"] += 1
                return
            gmail.file_read(message_id, label_id)
            labels = gmail.message_labels(message_id)
            if label_id not in labels or "INBOX" in labels or "UNREAD" in labels:
                raise _Readback
            counts["filed"] += 1

        # one message that cannot be filed is that message's failure, recorded by its fixed code; the others are still
        # filed,
        # and the stage fails only if every message failed
        done = isolate.run_items(
            ids,
            file_one,
            code_of=lambda error: (
                "PHYSMAIL_FILING_READBACK_MISMATCH" if isinstance(error, _Readback) else "PHYSMAIL_FILING_MOVE_FAILED"
            ),
        )
        for _, code in done.failures:
            fail(code)
        isolate.record(counts, done)
    except GmailError:
        fail("PHYSMAIL_FILING_GMAIL_FAILED")
        counts["stage_failed"] = 1  # the vendor's mail could not be listed or the label made: the stage itself failed
    return counts
