"""MegIBOW warnings (D134): the Notion contract's v1 rules, exactly. Pure, no model.

At most 3, ordered: evidence quality, no meetings scheduled / forward pipeline, pipeline imbalance, outreach drop, conversation drop. One suggestion each.
Trend rules need four trustworthy completed weeks. A failed source yields only the source-unavailable warning; two or more unresolved review items yield the Review-queue warning (never a healthy line)."""
from statistics import median

from lifeos.megibow import classify as C
from lifeos.megibow.windows import chicago, elapsed_business_days

MAX = 3
HEALTHY = "Megibow: pipeline healthy — no intervention suggested."
EVIDENCE = "⚠ Megibow evidence is incomplete — use the MegIBOW Review queue to resolve the unclassified Gmail/Calendar items before using this week's numbers to make a pipeline decision."
SOURCE_UNAVAILABLE = "⚠ MegIBOW source evidence is temporarily unavailable — last accepted counts retained; check system health before using this week's numbers."
NO_MEETINGS = "⚠ No new meetings scheduled this week — send 5 targeted outreach messages today and convert at least 1 into a calendar meeting before the week ends."
FORWARD_EMPTY = "⚠ Forward pipeline is empty — book 2 conversations into the next 14 days before doing lower-priority job-search work."
FORWARD_THIN = "⚠ Forward pipeline is thin — add at least 1 more conversation to the next 14 days."
CONSUMING = "⚠ You're consuming pipeline faster than you're replenishing it — turn active conversations into explicit next meetings and book at least 2 future conversations."
NOT_CONVERTING = "⚠ Outreach is not converting into meetings — tighten the ask, prioritize warmer contacts, and follow up on the 5 highest-value open threads with a specific meeting request."
OUTREACH_DROP = "⚠ Outreach is running below your recent cadence — send 5 targeted messages to the warmest recruiters, target-company contacts, or dormant network leads today."
CONVERSATION_DROP = "⚠ Completed conversations are below your recent cadence — prioritize the already-booked calls, then schedule 2 additional conversations for next week."


def calls(counts):
    return sum(counts[a] for a in C.CALLS)


def evaluate(current, baseline, now, forward_14, degraded=False, unresolved=0):
    """current: this week's counts; baseline: counts of the trustworthy completed weeks among the prior 7; forward_14: int or None when unknown. -> list of warning strings (maybe empty)."""
    if degraded:                                            # a failed source says so; it never claims that something waits in the Review queue
        return [SOURCE_UNAVAILABLE]
    weekday = chicago(now).weekday()  # Monday 0
    elapsed = elapsed_business_days(now)
    trend = len(baseline) >= 4
    out = []
    if unresolved >= 2:
        out.append(EVIDENCE)
    if weekday >= 3 and current[C.SCHEDULED] == 0:
        out.append(NO_MEETINGS)
    if forward_14 is not None and (forward_14 == 0 or (forward_14 < 2 and weekday >= 3)):
        out.append(FORWARD_EMPTY if forward_14 == 0 else FORWARD_THIN)
    med_calls = median([calls(w) for w in baseline]) if baseline else 0
    med_out = median([w[C.OUTREACH] for w in baseline]) if baseline else 0
    if weekday >= 3 and calls(current) >= max(2, med_calls) and current[C.SCHEDULED] <= 1 and trend:
        out.append(CONSUMING)
    elif current[C.OUTREACH] >= max(5, med_out) and current[C.SCHEDULED] == 0 and elapsed >= 3:
        out.append(NOT_CONVERTING)
    if trend and med_out >= 3 and weekday >= 2:
        expected = med_out * elapsed / 5
        if current[C.OUTREACH] < 0.6 * expected:
            out.append(OUTREACH_DROP)
    if trend and med_calls >= 2:
        if weekday >= 5 and calls(current) < 0.5 * med_calls:
            out.append(CONVERSATION_DROP)
        elif weekday == 4 and calls(current) == 0 and med_calls >= 3:
            out.append(CONVERSATION_DROP)
    seen, unique = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:MAX]
