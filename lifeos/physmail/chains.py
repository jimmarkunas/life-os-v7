"""Per-mail-number lifecycle fold (MAIL-1.2). Pure and order-independent: the events of one mail number are sorted by time, then folded.

SHRED and RECYCLE are DONE and absorbing; FORWARD is WAITING_FOR_TRACKING (tracking is MAIL-2.1, not here); SCAN is OPEN and non-terminal. Contradictory
evidence (Shred plus Recycle, Forward with Shred or Recycle in either order, any action after DONE) is REVIEW and never guessed away."""
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
