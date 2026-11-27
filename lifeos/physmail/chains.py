"""Per-mail-number lifecycle fold. Pure and order-independent: events are sorted by time, then folded.

SHRED and RECYCLE are DONE and absorbing; FORWARD is WAITING_FOR_TRACKING; SCAN is OPEN. Contradictory evidence
(Shred plus Recycle, Forward with Shred or Recycle, any action after DONE) is REVIEW."""
OPEN, WAITING, DONE, REVIEW = "OPEN", "WAITING_FOR_TRACKING", "DONE", "REVIEW"
TERMINAL = {"SHRED", "RECYCLE"}


def fold(events):
    """events: [(iso time, verb)] -> chain status."""
    status, verbs = OPEN, set()
    for _, verb in sorted(events):
        if verb in TERMINAL and verbs & (TERMINAL - {verb}):
            return REVIEW
        if verb in TERMINAL and "FORWARD" in verbs:
            return REVIEW
        if verb == "FORWARD" and verbs & TERMINAL:
            return REVIEW
        if verb == "SCAN" and verbs & TERMINAL:
            return REVIEW
        verbs.add(verb)
    if verbs & TERMINAL:
        return DONE
    return WAITING if "FORWARD" in verbs else OPEN
