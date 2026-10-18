"""Phase 1 configuration: which senders are job newsletters and where they go."""
import re

NEWSLETTER_LABEL = "J Newsletters"
PROCESSED_LABEL = "J Newsletters/Processed"
DONE_LABEL = "J Newsletters/Done"                      # every job in the mail has an outcome (finalize)
FINALIZE_QUERY = "label:j-newsletters-processed -label:j-newsletters-done newer_than:45d"
# Gmail search syntax flattens nested label names; pending = in the folder, not yet Processed.
RECONCILE_QUERY = "label:j-newsletters-processed {from:jobright.ai from:jobalerts-noreply@linkedin.com from:jobs-noreply@linkedin.com} newer_than:5d"   # every live Gmail rule in SENDER_RULES (not the retired ones)
REOPEN_QUERY = "label:j-newsletters-done newer_than:5d"
RETIRED_LABEL = "J Newsletters/Retired"                # D201: a retired sender's mail: closed unread, never Processed (Processed means every card was saved)
PENDING_QUERY = "label:j-newsletters -label:j-newsletters-processed -label:j-newsletters-retired"

# (name, regex matched against the bare lowercase sender address). Order is irrelevant.
SENDER_RULES = (
    ("lensa", re.compile(r"@([a-z0-9-]+\.)*lensa\.com$")),
    ("jobright", re.compile(r"@([a-z0-9-]+\.)*jobright\.ai$")),
    ("linkedin-alerts", re.compile(r"^(jobalerts-noreply|jobs-noreply)@linkedin\.com$")),
    # Outlook-only Dice/Reed rules live with the Outlook source. Dice Private Email
    # is deliberately absent: recruiter mail is human outreach.
)

# D201: senders whose mail is still swept out of the inbox but no longer read for jobs. Their mail gets RETIRED_LABEL at once with this count; no
# card is parsed, and it is never marked Processed, so finalize, reconcile and repair never see it.
RETIRED_SENDERS = {"lensa": "retired_sender_lensa"}

# Gmail does the sender matching server-side: one search per rule, no per-message fetches.
GMAIL_QUERIES = {
    "lensa": "in:inbox from:lensa.com",
    "jobright": "in:inbox from:jobright.ai",
    "linkedin-alerts": "in:inbox {from:jobalerts-noreply@linkedin.com from:jobs-noreply@linkedin.com}",
}


def classify(sender: str):
    """Return the matching rule name for a bare sender address, else None."""
    address = sender.strip().lower()
    match = re.search(r"<([^>]+)>", address)
    if match:
        address = match.group(1)
    for name, pattern in SENDER_RULES:
        if pattern.search(address):
            return name
    return None
