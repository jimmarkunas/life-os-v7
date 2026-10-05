"""MegIBOW weekly projection (D134): outcomes -> the 8-week table with Cumulative. Pure.

The open week is always recomputed from outcomes. A closed week comes from the frozen store when it is there; a week before the cut-over Monday is None (shown as a dash).
Cumulative = the carried-over legacy totals + every closed week + the open week."""
from lifeos.megibow import classify as C
from lifeos.megibow.windows import monday, visible_weeks


def dedupe(outcomes):
    """One outcome per key: the first seen, except that a settled COUNTED beats REVIEW and EXCLUDED for the same key."""
    rank = {C.COUNTED: 0, C.REVIEW: 1, C.EXCLUDED: 2}
    best = {}
    for o in outcomes:
        key = (o["key"], o["activity"] if o["status"] == C.COUNTED else None)
        current = best.get(o["key"])
        if current is None or rank[o["status"]] < rank[current["status"]]:
            best[o["key"]] = o
    return list(best.values())


def zero():
    return {a: 0 for a in C.ACTIVITIES}


def total(counts):
    return sum(counts.values())


def project(outcomes, today, frozen=None, legacy=None, cutover=None):
    """-> {weeks, counts{monday: {activity: n} | None}, cumulative, review[], current}. frozen: {monday: counts}; legacy: counts or None."""
    frozen = frozen or {}
    weeks = visible_weeks(today)
    current = monday(today)
    live = {w: zero() for w in weeks}
    review = []
    for o in dedupe(outcomes):
        if o["status"] == C.COUNTED and o["week"] in live and o["activity"] in live[o["week"]]:
            live[o["week"]][o["activity"]] += 1
        elif o["status"] == C.REVIEW and o["week"] >= weeks[0]:
            review.append(o)
    counts = {}
    for w in weeks:
        if w == current:
            counts[w] = live[w]
        elif w in frozen:
            counts[w] = dict(frozen[w])
        elif cutover is not None and w < cutover:
            counts[w] = None
        else:
            counts[w] = live[w]
    cumulative = dict(legacy) if legacy else zero()
    for w, c in frozen.items():
        if w not in weeks:
            for a in C.ACTIVITIES:
                cumulative[a] += c.get(a, 0)
    for w in weeks:
        if counts[w]:
            for a in C.ACTIVITIES:
                cumulative[a] += counts[w][a]
    return {"weeks": weeks, "counts": counts, "cumulative": cumulative, "review": review, "current": current}
