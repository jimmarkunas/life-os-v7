"""Match a job's employer to the stored network, from stored state only. Pure: no database, no network, no names.

A lead is a person with a stored position at the employer's normalized company key: tier CURRENT, PREVIOUS
(superseded position) or POSSIBLE (one key is a whole-word prefix of the other; shown last). Within a tier:
title-word overlap, freshest observation, stable id. At most MAX_LEADS are returned. Nothing about closeness or
willingness to refer is inferred; freshness is only the observation's age."""

from lifeos.platform import names

CURRENT, PREVIOUS, POSSIBLE = "CURRENT", "PREVIOUS", "POSSIBLE"
TIERS = (CURRENT, PREVIOUS, POSSIBLE)
FRESH_DAYS, AGING_DAYS, MAX_LEADS = 45, 120, 5
ROLE_STOP = {
    "and",
    "the",
    "for",
    "of",
    "senior",
    "sr",
    "junior",
    "jr",
    "lead",
    "head",
    "principal",
    "staff",
    "associate",
}


def freshness(age_days):
    return "FRESH" if age_days <= FRESH_DAYS else "AGING" if age_days <= AGING_DAYS else "STALE"


def _words(title):
    return {t for t in names.norm(title).split() if len(t) > 2 and t not in ROLE_STOP}


def build_index(rows):
    """rows of (person_id, company_key, title, position_state, last_verified[, position_id]) -> {company_key: [lead
    tuples]}. A blank company key is dropped."""
    index = {}
    for person_id, company_key, title, state, verified, *rest in rows:
        if not company_key:
            continue
        tier = CURRENT if state == "CURRENT" else PREVIOUS
        index.setdefault(company_key, []).append((person_id, tier, _words(title), verified, rest[0] if rest else None))
    return index


def _prefix(a, b):
    x, y = a.split(), b.split()
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return a != b and bool(short) and long_[: len(short)] == short


def leads_for(company_key, job_title, index, today, aliases=None, limit=MAX_LEADS):
    """-> up to `limit` leads, best first, one per person. `aliases` ({company key: company key}) equate keys for
    matching only."""
    if not company_key:
        return []
    aliases = aliases or {}
    wanted = _words(job_title)
    best = {}
    for key, entries in index.items():
        if key == company_key or aliases.get(key) == company_key or aliases.get(company_key) == key:
            kind = None
        elif _prefix(key, company_key):
            kind = POSSIBLE
        else:
            continue
        for person_id, tier, words, verified, position_id in entries:
            tier = kind or tier
            rank = TIERS.index(tier)
            lead = {
                "person_id": person_id,
                "tier": tier,
                "age_days": max((today - verified).days, 0),
                "overlap": len(wanted & words),
                "company_key": key,
                "_verified": verified,
            }
            if position_id is not None:
                lead["position_id"] = position_id
            if (
                person_id not in best
                or rank < TIERS.index(best[person_id]["tier"])
                or (rank == TIERS.index(best[person_id]["tier"]) and verified > best[person_id]["_verified"])
            ):
                best[person_id] = lead
    ordered = sorted(
        best.values(), key=lambda l: (TIERS.index(l["tier"]), -l["overlap"], l["age_days"], l["person_id"])
    )
    for lead in ordered:
        lead["freshness"] = freshness(lead["age_days"])
        del lead["_verified"]
    return ordered[:limit]
