"""Phase 1 configuration: which senders are job newsletters and where they go."""
import re

NEWSLETTER_LABEL = "J Newsletters"
PROCESSED_LABEL = "J Newsletters/Processed"
DONE_LABEL = "J Newsletters/Done"                      # every job in the mail has an outcome (finalize)
FINALIZE_QUERY = "label:j-newsletters-processed -label:j-newsletters-done newer_than:45d"
# Gmail search syntax flattens nested label names; pending = in the folder, not yet Processed.
RECONCILE_QUERY = "label:j-newsletters-processed from:lensa.com newer_than:5d"
PENDING_QUERY = "label:j-newsletters -label:j-newsletters-processed"

# (name, regex matched against the bare lowercase sender address). Order is irrelevant.
SENDER_RULES = (
    ("lensa", re.compile(r"@([a-z0-9-]+\.)*lensa\.com$")),
    ("jobright", re.compile(r"@([a-z0-9-]+\.)*jobright\.ai$")),
    ("linkedin-alerts", re.compile(r"^(jobalerts-noreply|jobs-noreply)@linkedin\.com$")),
    # Dice is deliberately absent: *.user.dice.com is Dice Private Email relaying
    # individual staffing recruiters (human outreach), not a job newsletter.
)

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
