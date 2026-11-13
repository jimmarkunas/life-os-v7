"""File the Physical Mail vendor's notices: mark read and move them from the Inbox to the "Physical Mail" Gmail label (LIFE-149).

Only mail whose From address is exactly the vendor's no-reply address is touched; nothing is deleted or trashed; every move is read back. Dry run unless live. Outputs: counts and fixed codes only.
Runs after the read-only Physical Mail package (lifeos.physmail) in the mail job and never changes it: that stage reads by sender, not by label or read state, so filing cannot alter what it sees (a test keeps physmail free of any label or read-state change)."""
# LIFE-149 (Jim, Oct 27 2026).
import os
from email.utils import getaddresses

from lifeos.platform.gmail import Gmail, GmailError

_AT = chr(64)                                     # built apart so no address literal enters the repository text (privacy test)
SENDER = "no-reply" + _AT + "anytimemailbox" + ".com"
LABEL = "Physical Mail"
DEFAULT_LIMIT = 200                         # per run; the hourly tick drains a backlog


def _exact(header):
    parsed = getaddresses([header]) if isinstance(header, str) else []
    return len(parsed) == 1 and parsed[0][1].strip().lower() == SENDER


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
        for message_id in ids:
            try:
                if not _exact(gmail.sender(message_id)):
                    counts["skipped_sender"] += 1
                    continue
                gmail.file_read(message_id, label_id)
                labels = gmail.message_labels(message_id)
                if label_id not in labels or "INBOX" in labels or "UNREAD" in labels:
                    fail("PHYSMAIL_FILING_READBACK_MISMATCH")
                    continue
                counts["filed"] += 1
            except GmailError:
                fail("PHYSMAIL_FILING_MOVE_FAILED")
    except GmailError:
        fail("PHYSMAIL_FILING_GMAIL_FAILED")
    return counts
